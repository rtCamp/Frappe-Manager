"""External database preflight probe (design: `external-db-design.md`, "Preflight probe").

This module owns query construction and output parsing. It does **not** own execution: the
caller injects a ``Runner`` that runs one shell command inside the bench container and returns
its combined output. That is why nothing here imports docker, and why every check is unit
testable with a dict of canned outputs.

The probe is two stage, and not by preference. `python` inside the bench container resolves to
`.uv/python-default/bin/python` and `pymysql` lives in `env/`, both created by phase 2, so
neither exists at the point where a preflight is worth running. What *is* in the image at that
moment is `/usr/bin/mariadb`:

- :func:`probe_stage_one` runs early (between create phase 1 and phase 2) with the `mariadb`
  client: reachability, credentials, server identity and settings, schema state, and the CLI
  half of TLS. The CLI is a real half of the job, because it is the stack that needs the
  `[client]` option file and that Frappe shells out to for the initial SQL import.
- :func:`probe_stage_two` runs in phase 4 with `pymysql` from the venv or the app image: the
  exact driver the site will use, reading the exact `db_ssl_*` shapes out of the site's own
  `site_config.json`, plus the emptiness re-check that stands between `--force` and someone's
  data. It connects as the site login, so it applies to the flows where that login already
  exists (an existing schema, and attach). On the provisioning path the site login does not
  exist yet: there, re-run :func:`probe_stage_one` with the admin credentials for the staleness
  re-check, and Frappe's own `setup_database` connection is the driver level check.

A probe that exercises only one of the two stacks can pass while the create fails, which is the
whole reason both exist.

Secrets: passwords are passed to the `mariadb` client through a ``MYSQL_PWD`` environment
prefix, never as `-p<pass>`, so they do not show up in a process listing of the container.
The prefix value does live in the shell string handed to the ``Runner`` (the seam is a single
string), so callers MUST run :func:`redact` over anything they log.
"""

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

# A runner executes one shell command inside the bench container and returns its combined
# stdout/stderr. Raising on a non-zero exit is fine: the probe treats the exception text as
# output, which is where the `mariadb` client's `ERROR <code> (…)` line lives.
Runner = Callable[[str], str]

MYSQL_CLIENT = "/usr/bin/mariadb"
DEFAULT_CONNECT_TIMEOUT = 10
MIN_SERVER_VERSION = (10, 6)

WANTED_CHARACTER_SET = "utf8mb4"
WANTED_COLLATION = "utf8mb4_unicode_ci"

DOCTYPE_TABLE = "tabDocType"
SINGLES_TABLE = "tabSingles"
INSTALLED_APPLICATION_TABLE = "tabInstalled Application"
FRAPPE_CORE_TABLES = (DOCTYPE_TABLE, SINGLES_TABLE)

# Container path of the bench sites directory. The stage two script reads the site file through
# an absolute path so it does not depend on the working directory of the exec.
SITES_CONTAINER_ROOT = "/workspace/frappe-bench/sites"
STAGE_TWO_MARKER = "FM_PROBE2"

# Server error codes the probe reasons about.
ER_ACCESS_DENIED = 1045
ER_NO_SUCH_GRANT = 1141
ER_SECURE_TRANSPORT_REQUIRED = 3159
ER_TLS_CLIENT = 2026  # client side "TLS/SSL error: …" from the mariadb client

# Server variables read in one round trip. Fetched with `SHOW VARIABLES WHERE Variable_name IN
# (…)` rather than `SELECT @@x`, because a variable that does not exist on this server yields
# zero rows instead of erroring the whole batch. That is what makes
# "innodb_read_only_compressed where the variable exists" expressible.
PROBED_VARIABLES = (
    "version",
    "version_comment",
    "character_set_server",
    "collation_server",
    "socket",
    "innodb_read_only_compressed",
    "require_secure_transport",
)

# Global privileges Frappe's own provisioning needs: CREATE DATABASE, CREATE USER, the GRANT it
# hands to the site login, and the unconditional FLUSH PRIVILEGES at the end of setup_database.
ADMIN_PRIVILEGES = frozenset({"CREATE", "CREATE USER", "RELOAD", "GRANT OPTION"})

CHECK_CONNECT = "connect"
CHECK_SERVER_IS_MARIADB = "server_is_mariadb"
CHECK_SERVER_VERSION = "server_version"
CHECK_INNODB_READ_ONLY_COMPRESSED = "innodb_read_only_compressed"
CHECK_CHARACTER_SET = "character_set"
CHECK_DB_SOCKET = "db_socket"
CHECK_SCHEMA_STATE = "schema_state"
CHECK_FRAPPE_SCHEMA = "frappe_schema"
CHECK_DB_USER_EXISTS = "db_user_exists"
CHECK_SITE_CREDENTIALS = "site_credentials"
CHECK_SERVER_ENFORCES_TLS = "server_enforces_tls"
CHECK_TLS_IN_FORCE = "tls_in_force"
CHECK_CA_VERIFICATION = "ca_verification"
CHECK_ADMIN_GRANTS = "admin_grants"
CHECK_APP_PARITY = "app_parity"
CHECK_SITE_FILES = "site_files"

_ERROR_RE = re.compile(r"ERROR\s+(\d+)\s*\(")
_PY_ERROR_RE = re.compile(r"\((\d{4}),")
_GRANT_RE = re.compile(r"^GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<scope>\S+)\s+TO\s", re.IGNORECASE)
_ROLE_GRANT_RE = re.compile(r"^GRANT\s+(?!.*\sON\s).+\sTO\s", re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_TLS_ERROR_HINTS = ("certificate", "tls/ssl error", "ssl connection error", "self-signed", "verify")


class CheckStatus(StrEnum):
    ok = "ok"
    warn = "warn"
    fail = "fail"


@dataclass(frozen=True)
class ProbeCheck:
    """One reported check. `detail` is operator facing and must never carry a secret."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class SchemaState:
    exists: bool
    table_count: int
    is_frappe: bool
    installed_apps: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    checks: tuple[ProbeCheck, ...]
    schema: SchemaState
    server_enforces_tls: bool
    tls_in_force: bool
    user_exists: bool

    @property
    def failures(self) -> tuple[ProbeCheck, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.fail)

    @property
    def warnings(self) -> tuple[ProbeCheck, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.warn)

    @property
    def ok(self) -> bool:
        return not self.failures

    def check(self, name: str) -> ProbeCheck | None:
        return next((c for c in self.checks if c.name == name), None)


class Flow(StrEnum):
    """What `fm create` should do, per the design's Decision table."""

    provision = "provision"
    adopt_empty = "adopt_empty"
    attach = "attach"
    refuse = "refuse"


@dataclass(frozen=True)
class FlowDecision:
    flow: Flow
    message: str = ""

    @property
    def refused(self) -> bool:
        return self.flow is Flow.refuse


@dataclass(frozen=True)
class CredentialInputs:
    """Which credential flags `fm create` was given. Booleans only: no secret enters this type."""

    site_password_given: bool
    admin_given: bool
    db_name: str = ""
    db_user: str | None = None  # --db-user; None means "equal to db_name"
    supports_db_user: bool = True  # False on a v15 bench, which has no db_user config key


# --------------------------------------------------------------------------- text plumbing


def redact(text: str, *secrets: str | None) -> str:
    """Replace every supplied secret with `***`. Use before logging a command or its output."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return re.sub(r"MYSQL_PWD=\S+", "MYSQL_PWD=***", text)


def _sql_literal(value: str) -> str:
    """Escape a string for use inside single quotes in SQL."""
    return value.replace("\\", "\\\\").replace("'", "''")


def _sql_identifier(value: str) -> str:
    """Backtick quote an identifier."""
    return "`" + value.replace("`", "``") + "`"


def _require_safe_name(value: str, kind: str) -> str:
    """Reject names that would have to be escaped through the shell/python/SQL sandwich."""
    if not _SAFE_NAME_RE.match(value):
        raise ValueError(f"unsafe {kind} for a database probe: {value!r}")
    return value


def _summary(text: str, *secrets: str | None, limit: int = 400) -> str:
    """First meaningful line of a command's output, redacted, for a check detail."""
    for line in redact(text, *secrets).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return "no output"


def _summary_tail(text: str, *secrets: str | None, limit: int = 400) -> str:
    """Last meaningful line, which is where a python traceback puts the exception."""
    for line in reversed(redact(text, *secrets).splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return "no output"


@dataclass(frozen=True)
class _Reply:
    text: str
    ok: bool

    @property
    def code(self) -> int | None:
        match = _ERROR_RE.search(self.text)
        return int(match.group(1)) if match else None

    @property
    def rows(self) -> list[list[str]]:
        """Tab separated rows, ignoring blank lines and any noise the runner mixed in."""
        return [line.split("\t") for line in self.text.splitlines() if line.strip()]

    def pairs(self) -> dict[str, str]:
        """`SHOW VARIABLES` / `SHOW STATUS` output as a name to value map."""
        return {row[0].strip(): row[1].strip() for row in self.rows if len(row) == 2}

    def column(self) -> list[str]:
        return [row[0].strip() for row in self.rows if row and row[0].strip()]

    @property
    def tls_error(self) -> bool:
        lowered = self.text.lower()
        return self.code == ER_TLS_CLIENT or any(hint in lowered for hint in _TLS_ERROR_HINTS)

    @property
    def secure_transport_required(self) -> bool:
        return self.code == ER_SECURE_TRANSPORT_REQUIRED or "insecure transport" in self.text.lower()


def _run(runner: Runner, command: str) -> _Reply:
    try:
        text = runner(command)
    except Exception as exc:
        # The runner owns execution; its failure is data here, not an error to propagate.
        return _Reply(str(exc), False)
    return _Reply(text, _ERROR_RE.search(text) is None)


# --------------------------------------------------------------------------- command building


def build_mysql_command(
    sql: str,
    *,
    host: str,
    port: int,
    user: str,
    password: str | None = None,
    mysql_home: str | None = None,
    database: str | None = None,
    plaintext: bool = False,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
) -> str:
    """One `mariadb` invocation, batch mode, password via `MYSQL_PWD` and never via `-p`.

    `mysql_home` sets `MYSQL_HOME=<dir>`, which makes the client read `<dir>/my.cnf`, whose
    `[client]` section is the only way to give the CLI a CA and hostname verification.
    """
    prefix: list[str] = []
    if mysql_home:
        prefix.append(f"MYSQL_HOME={shlex.quote(mysql_home)}")
    if password is not None:
        prefix.append(f"MYSQL_PWD={shlex.quote(password)}")

    args = [
        MYSQL_CLIENT,
        "-h",
        shlex.quote(host),
        "-P",
        str(int(port)),
        "-u",
        shlex.quote(user),
        f"--connect-timeout={int(connect_timeout)}",
        "--batch",
        "--skip-column-names",
    ]
    if plaintext:
        # Explicitly refuse TLS, so a refusal proves the server enforces it.
        args.append("--skip-ssl")
    if database:
        args.append(shlex.quote(database))
    args += ["-e", shlex.quote(sql)]
    return " ".join(prefix + args)


def variables_sql() -> str:
    names = ", ".join(f"'{_sql_literal(name)}'" for name in PROBED_VARIABLES)
    return f"SHOW VARIABLES WHERE Variable_name IN ({names}); SHOW STATUS LIKE 'Ssl_cipher'"


def schema_state_sql(schema: str) -> str:
    literal = _sql_literal(schema)
    return (  # noqa: S608 - schema is a config value, escaped as a SQL literal above
        "SELECT"
        f" (SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{literal}'),"
        f" (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='{literal}')"
    )


def frappe_tables_sql(schema: str) -> str:
    wanted = ", ".join(f"'{_sql_literal(name)}'" for name in (*FRAPPE_CORE_TABLES, INSTALLED_APPLICATION_TABLE))
    return (  # noqa: S608 - schema and table names are escaped SQL literals
        "SELECT TABLE_NAME FROM information_schema.TABLES"
        f" WHERE TABLE_SCHEMA='{_sql_literal(schema)}' AND TABLE_NAME IN ({wanted})"
    )


def installed_apps_sql(schema: str) -> str:
    table = f"{_sql_identifier(schema)}.{_sql_identifier(INSTALLED_APPLICATION_TABLE)}"
    return f"SELECT app_name FROM {table}"  # noqa: S608 - identifiers are backtick quoted


def show_grants_sql(user: str, scope: str = "%") -> str:
    return f"SHOW GRANTS FOR '{_sql_literal(user)}'@'{_sql_literal(scope)}'"


# --------------------------------------------------------------------------- advisory lock


LOCK_NAME_PREFIX = "fm:create:"
LOCK_TIMEOUT_SECONDS = 0


def lock_name(schema: str) -> str:
    return f"{LOCK_NAME_PREFIX}{schema}"


def get_lock_sql(schema: str) -> str:
    """Advisory lock held for the duration of the provisioning connection, timeout 0.

    It needs no privileges, it is scoped to the schema name, and it is released when the
    connection closes. It closes the window that the emptiness re-check only narrows: two
    operators can otherwise both read "absent" and both proceed.
    """
    return f"SELECT GET_LOCK('{_sql_literal(lock_name(schema))}', {LOCK_TIMEOUT_SECONDS})"


def release_lock_sql(schema: str) -> str:
    return f"SELECT RELEASE_LOCK('{_sql_literal(lock_name(schema))}')"


def lock_taken(output: str) -> bool:
    """`GET_LOCK` returns 1 when the lock was taken, 0 on timeout and NULL on error."""
    return _Reply(output, True).column()[:1] == ["1"]


def lock_refusal(schema: str) -> str:
    return (
        f"another fm create appears to be provisioning this schema ({schema}): the advisory lock"
        f" {lock_name(schema)!r} is already held. Wait for that run to finish, or check with"
        " whoever is running it, and retry."
    )


# --------------------------------------------------------------------------- stage one


def probe_stage_one(
    runner: Runner,
    *,
    host: str,
    port: int = 3306,
    admin_user: str | None = None,
    admin_password: str | None = None,
    site_user: str | None = None,
    site_password: str | None = None,
    schema: str,
    mysql_home: str | None = None,
    bench_apps: tuple[str, ...] = (),
    attach: bool = False,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
) -> ProbeResult:
    """Early probe with the `mariadb` client, from inside the container that will run the site."""
    checks: list[ProbeCheck] = []
    secrets = (admin_password, site_password)
    login_site_user = site_user or schema

    use_admin = bool(admin_user) and admin_password is not None
    login_user: str = admin_user if use_admin and admin_user else login_site_user
    login_password = admin_password if use_admin else site_password

    def invoke(sql: str, *, user: str, password: str | None, tls: bool = True, plaintext: bool = False) -> _Reply:
        return _run(
            runner,
            build_mysql_command(
                sql,
                host=host,
                port=port,
                user=user,
                password=password,
                mysql_home=mysql_home if tls else None,
                plaintext=plaintext,
                connect_timeout=connect_timeout,
            ),
        )

    if login_password is None:
        checks.append(
            ProbeCheck(
                CHECK_CONNECT,
                CheckStatus.fail,
                "no database credentials were supplied, so nothing could be probed. Pass"
                " --db-password for a schema that already exists, or --db-admin-user with"
                " --db-admin-password so fm can have Frappe create the schema and the user.",
            )
        )
        return ProbeResult(tuple(checks), SchemaState(False, 0, False, ()), False, False, False)

    settings = invoke(variables_sql(), user=login_user, password=login_password)

    if not settings.ok:
        checks.append(_connect_failure(settings, host=host, port=port, user=login_user, secrets=secrets))
        if mysql_home and settings.tls_error:
            checks.append(
                ProbeCheck(
                    CHECK_CA_VERIFICATION,
                    CheckStatus.fail,
                    "the supplied CA did not verify this server, or the certificate cannot name"
                    f" {host}: {_summary(settings.text, *secrets)}. Fix the bundle or pass"
                    " --db-no-verify-hostname only if the certificate genuinely cannot name the"
                    " endpoint.",
                )
            )
        enforcement, enforced = _tls_enforcement(
            invoke,
            variable=None,
            host=host,
            user=login_user,
            password=login_password,
            mysql_home=mysql_home,
            secrets=secrets,
            connect_error=settings,
        )
        checks.append(enforcement)
        return ProbeResult(tuple(checks), SchemaState(False, 0, False, ()), enforced, False, False)

    variables = settings.pairs()
    checks.append(
        ProbeCheck(
            CHECK_CONNECT,
            CheckStatus.ok,
            f"connected to {host}:{port} as {login_user!r} from the bench container",
        )
    )
    checks.append(_flavour_check(variables))
    checks.append(_version_check(variables))
    checks.append(_read_only_compressed_check(variables))
    checks.append(_character_set_check(variables))
    checks.append(_socket_check(variables, host=host, port=port))

    state = _schema_state(invoke, schema=schema, user=login_user, password=login_password)
    checks.append(
        ProbeCheck(
            CHECK_SCHEMA_STATE,
            CheckStatus.ok,
            f"schema {schema!r} does not exist on {host}"
            if not state.exists
            else f"schema {schema!r} exists on {host} and holds {state.table_count} tables",
        )
    )
    checks.append(_frappe_schema_check(state, schema=schema, attach=attach))

    grants = invoke(show_grants_sql(login_site_user), user=login_user, password=login_password)
    user_exists = grants.ok
    checks.append(
        _db_user_check(
            grants,
            user=login_site_user,
            use_admin=use_admin,
            site_password_given=site_password is not None,
            attach=attach,
            secrets=secrets,
        )
    )

    if site_password is not None:
        auth = invoke("SELECT 1", user=login_site_user, password=site_password)
        checks.append(_site_credentials_check(auth, user=login_site_user, secrets=secrets))
        if auth.ok:
            user_exists = True

    enforcement, enforced = _tls_enforcement(
        invoke,
        variable=variables.get("require_secure_transport"),
        host=host,
        user=login_user,
        password=login_password,
        mysql_home=mysql_home,
        secrets=secrets,
        connect_error=None,
    )
    checks.append(enforcement)

    cipher = variables.get("Ssl_cipher", "")
    checks.append(_tls_in_force_check(cipher, mysql_home=mysql_home))
    checks.append(_ca_verification_check(cipher, mysql_home=mysql_home, host=host))

    if use_admin:
        checks.append(
            _admin_grants_check(
                invoke("SHOW GRANTS FOR CURRENT_USER()", user=login_user, password=login_password),
                user=login_user,
                secrets=secrets,
            )
        )
    else:
        checks.append(
            ProbeCheck(
                CHECK_ADMIN_GRANTS,
                CheckStatus.ok,
                "no admin credentials were supplied, so fm will not provision and no privileged"
                " connection is opened at all",
            )
        )

    if attach:
        checks.append(_app_parity_check(state, bench_apps=bench_apps))
        checks.append(
            ProbeCheck(
                CHECK_SITE_FILES,
                CheckStatus.warn,
                "public/files and private/files live on disk rather than in the database and must"
                " be copied into workspace/frappe-bench/sites/<site>/ separately, or every"
                " attachment 404s",
            )
        )

    return ProbeResult(tuple(checks), state, enforced, bool(cipher), user_exists)


def _connect_failure(reply: _Reply, *, host: str, port: int, user: str, secrets: tuple[str | None, ...]) -> ProbeCheck:
    detail = _summary(reply.text, *secrets)
    if reply.code == ER_ACCESS_DENIED:
        reason = (
            f"the server refused the credentials for {user!r} (1045). Note that MySQL returns the"
            " same 1045 for a wrong password and a missing account"
        )
    elif reply.secure_transport_required:
        reason = (
            "the server refused the connection because it requires TLS (3159 connections using"
            " insecure transport are prohibited). Pass --db-ca with the server's CA bundle: there"
            " is no opportunistic TLS, so without a CA no later connection can succeed either"
        )
    elif reply.tls_error:
        reason = "the TLS handshake failed"
    else:
        reason = f"{host}:{port} is not reachable from the bench container"
    return ProbeCheck(CHECK_CONNECT, CheckStatus.fail, f"{reason}: {detail}")


def _flavour_check(variables: dict[str, str]) -> ProbeCheck:
    banner = " ".join(v for v in (variables.get("version", ""), variables.get("version_comment", "")) if v)
    if "mariadb" in banner.lower():
        return ProbeCheck(CHECK_SERVER_IS_MARIADB, CheckStatus.ok, f"server is MariaDB ({banner})")
    return ProbeCheck(
        CHECK_SERVER_IS_MARIADB,
        CheckStatus.fail,
        f"server reports {banner!r}, which is MySQL and not MariaDB. MySQL is not a supported"
        " Frappe db_type, and normal operation emits MariaDB only SQL (CREATE SEQUENCE, nextval,"
        " set session max_statement_time), so this breaks later rather than now. If this is an"
        " Azure instance: Azure retired Database for MariaDB, so an Azure managed instance today"
        " is almost certainly Database for MySQL, which Frappe does not support. Any MariaDB is a"
        " valid target, managed or self hosted.",
    )


def _server_version(variables: dict[str, str]) -> tuple[int, int] | None:
    match = re.match(r"(\d+)\.(\d+)", variables.get("version", ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _version_check(variables: dict[str, str]) -> ProbeCheck:
    raw = variables.get("version", "")
    version = _server_version(variables)
    wanted = ".".join(str(part) for part in MIN_SERVER_VERSION)
    if version is None:
        return ProbeCheck(
            CHECK_SERVER_VERSION,
            CheckStatus.warn,
            f"could not read a version number from {raw!r}, so the {wanted} minimum is unverified",
        )
    if version < MIN_SERVER_VERSION:
        return ProbeCheck(
            CHECK_SERVER_VERSION,
            CheckStatus.fail,
            f"server is {raw}, below MariaDB {wanted}, which Frappe warns is unsupported",
        )
    return ProbeCheck(CHECK_SERVER_VERSION, CheckStatus.ok, f"server is {raw} (>= {wanted})")


def _read_only_compressed_check(variables: dict[str, str]) -> ProbeCheck:
    if "innodb_read_only_compressed" not in variables:
        return ProbeCheck(
            CHECK_INNODB_READ_ONLY_COMPRESSED,
            CheckStatus.ok,
            "innodb_read_only_compressed does not exist on this server, so there is nothing to set",
        )
    value = variables["innodb_read_only_compressed"].upper()
    if value in {"OFF", "0"}:
        return ProbeCheck(CHECK_INNODB_READ_ONLY_COMPRESSED, CheckStatus.ok, "innodb_read_only_compressed is OFF")
    return ProbeCheck(
        CHECK_INNODB_READ_ONLY_COMPRESSED,
        CheckStatus.fail,
        f"innodb_read_only_compressed is {value}. Core doctypes declare ROW_FORMAT=Compressed and"
        " Frappe rewrites that only for restores, never for freshly created tables, so a create"
        " fails outright; on attach the existing compressed tables become read only and the site"
        " breaks on its first write to View Log or Deleted Document. Set"
        " innodb_read_only_compressed=0 on the server (parameter group on a managed provider,"
        " command flag or my.cnf otherwise) and retry. It defaulted to ON in MariaDB 10.6.1"
        " through 10.6.5.",
    )


def _character_set_check(variables: dict[str, str]) -> ProbeCheck:
    charset = variables.get("character_set_server", "")
    collation = variables.get("collation_server", "")
    if charset == WANTED_CHARACTER_SET and collation == WANTED_COLLATION:
        return ProbeCheck(
            CHECK_CHARACTER_SET,
            CheckStatus.ok,
            f"character_set_server is {charset} and collation_server is {collation}",
        )
    return ProbeCheck(
        CHECK_CHARACTER_SET,
        CheckStatus.warn,
        f"character_set_server is {charset or 'unknown'} and collation_server is"
        f" {collation or 'unknown'}, not {WANTED_CHARACTER_SET} / {WANTED_COLLATION}. Warning"
        " only, since Frappe forces both per connection and per table, but fm sets them on its"
        " own container and a managed provider takes them from a parameter group.",
    )


def _socket_check(variables: dict[str, str], *, host: str, port: int) -> ProbeCheck:
    server_socket = variables.get("socket", "")
    if host.strip().lower() in _LOCAL_HOSTS:
        return ProbeCheck(
            CHECK_DB_SOCKET,
            CheckStatus.warn,
            f"the endpoint is {host!r}, which the client resolves through the local socket"
            f" ({server_socket or 'unknown path'}) rather than TCP. A set db_socket silently"
            " overrides host and port, so an external endpoint must be a real hostname reachable"
            " from the bench container.",
        )
    return ProbeCheck(
        CHECK_DB_SOCKET,
        CheckStatus.ok,
        f"connection is TCP to {host}:{port}; no db_socket is written into site_config.json, so"
        f" it cannot override the endpoint (the server's own socket is {server_socket or 'unset'})",
    )


def _schema_state(
    invoke: Callable[..., _Reply],
    *,
    schema: str,
    user: str,
    password: str | None,
) -> SchemaState:
    counts = invoke(schema_state_sql(schema), user=user, password=password)
    rows = counts.rows
    if not counts.ok or not rows or len(rows[0]) < 2:
        return SchemaState(False, 0, False, ())
    exists = rows[0][0].strip() not in {"0", ""}
    table_count = int(rows[0][1].strip() or 0)
    if not exists or table_count == 0:
        return SchemaState(exists, table_count, False, ())

    tables = invoke(frappe_tables_sql(schema), user=user, password=password)
    present = set(tables.column())
    apps: tuple[str, ...] = ()
    if INSTALLED_APPLICATION_TABLE in present:
        listed = invoke(installed_apps_sql(schema), user=user, password=password)
        if listed.ok:
            apps = tuple(listed.column())
    is_frappe = all(table in present for table in FRAPPE_CORE_TABLES) and "frappe" in apps
    return SchemaState(exists, table_count, is_frappe, apps)


def _frappe_schema_check(state: SchemaState, *, schema: str, attach: bool) -> ProbeCheck:
    if not state.exists or state.table_count == 0:
        return ProbeCheck(
            CHECK_FRAPPE_SCHEMA,
            CheckStatus.ok,
            f"schema {schema!r} holds no tables, so there is no existing site to identify",
        )
    if state.is_frappe:
        return ProbeCheck(
            CHECK_FRAPPE_SCHEMA,
            CheckStatus.ok,
            f"schema {schema!r} is a Frappe schema: {DOCTYPE_TABLE} and {SINGLES_TABLE} are"
            f" present and {INSTALLED_APPLICATION_TABLE} carries a frappe row"
            f" ({', '.join(state.installed_apps) or 'no apps listed'})",
        )
    reason = (
        f"no {DOCTYPE_TABLE} or no {SINGLES_TABLE}"
        if not state.installed_apps
        else f"no frappe row in {INSTALLED_APPLICATION_TABLE} (found {', '.join(state.installed_apps)})"
    )
    detail = (
        f"schema {schema!r} has {state.table_count} tables but is not a Frappe schema: {reason}."
        " Attaching would build a site directory around someone else's data and report success."
    )
    return ProbeCheck(CHECK_FRAPPE_SCHEMA, CheckStatus.fail if attach else CheckStatus.warn, detail)


def _db_user_check(
    reply: _Reply,
    *,
    user: str,
    use_admin: bool,
    site_password_given: bool,
    attach: bool,
    secrets: tuple[str | None, ...],
) -> ProbeCheck:
    if not reply.ok and reply.code == ER_NO_SUCH_GRANT:
        return ProbeCheck(
            CHECK_DB_USER_EXISTS,
            CheckStatus.ok,
            f"no login {user!r}@'%' exists yet (1141), so Frappe's CREATE USER will create it",
        )
    if not reply.ok:
        return ProbeCheck(
            CHECK_DB_USER_EXISTS,
            CheckStatus.warn,
            f"could not determine whether the login {user!r}@'%' exists: {_summary(reply.text, *secrets)}",
        )
    if use_admin and not site_password_given and not attach:
        return ProbeCheck(
            CHECK_DB_USER_EXISTS,
            CheckStatus.fail,
            f"the login {user!r}@'%' already exists and fm does not know its password. Frappe's"
            " create_user is CREATE USER IF NOT EXISTS, so the account would keep the password it"
            " has and the site would be unconnectable, while Frappe still logs the misleading"
            " 'Created or updated user'. Pass --db-password with that login's existing password,"
            " or point --db-user at a login that does not exist yet.",
        )
    return ProbeCheck(CHECK_DB_USER_EXISTS, CheckStatus.ok, f"login {user!r}@'%' already exists on the server")


def _site_credentials_check(reply: _Reply, *, user: str, secrets: tuple[str | None, ...]) -> ProbeCheck:
    if reply.ok:
        return ProbeCheck(
            CHECK_SITE_CREDENTIALS,
            CheckStatus.ok,
            f"the supplied --db-password authenticates as {user!r}",
        )
    if reply.code == ER_ACCESS_DENIED:
        return ProbeCheck(
            CHECK_SITE_CREDENTIALS,
            CheckStatus.fail,
            f"the supplied --db-password was rejected for {user!r} (1045). MySQL returns the same"
            " 1045 for a wrong password and a missing account, which is why existence and"
            " authentication are two separate checks: see the db_user_exists check for which one"
            " this is.",
        )
    return ProbeCheck(
        CHECK_SITE_CREDENTIALS,
        CheckStatus.fail,
        f"could not authenticate as {user!r} with the supplied --db-password: {_summary(reply.text, *secrets)}",
    )


def _tls_enforcement(
    invoke: Callable[..., _Reply],
    *,
    variable: str | None,
    host: str,
    user: str,
    password: str | None,
    mysql_home: str | None,
    secrets: tuple[str | None, ...],
    connect_error: _Reply | None,
) -> tuple[ProbeCheck, bool]:
    """`@@require_secure_transport` ON, or a plaintext connection refused with 3159."""
    evidence = ""
    enforced: bool | None = None

    if variable and variable.upper() in {"ON", "1"}:
        enforced, evidence = True, "@@require_secure_transport is ON"
    elif connect_error is not None and connect_error.secure_transport_required:
        enforced, evidence = True, "the probe connection was refused with 3159 (insecure transport)"
    else:
        plaintext = invoke("SELECT 1", user=user, password=password, tls=False, plaintext=True)
        if plaintext.secure_transport_required:
            enforced, evidence = True, "a plaintext connection was refused with 3159"
        elif plaintext.ok:
            enforced, evidence = False, "a plaintext connection succeeded"
        else:
            evidence = f"a plaintext connection failed for another reason: {_summary(plaintext.text, *secrets)}"

    if enforced is None:
        return (
            ProbeCheck(
                CHECK_SERVER_ENFORCES_TLS,
                CheckStatus.warn,
                f"could not determine whether {host} enforces TLS: {evidence}",
            ),
            False,
        )
    if enforced and not mysql_home:
        return (
            ProbeCheck(
                CHECK_SERVER_ENFORCES_TLS,
                CheckStatus.fail,
                f"{host} enforces TLS ({evidence}) and no --db-ca was given. There is no"
                " opportunistic TLS in Frappe's driver or in the client fm shells out to, so"
                " every later connection, including the site bootstrap, would fail. Pass --db-ca"
                " with the server's CA bundle.",
            ),
            True,
        )
    if enforced:
        return (
            ProbeCheck(
                CHECK_SERVER_ENFORCES_TLS,
                CheckStatus.ok,
                f"{host} enforces TLS ({evidence}) and a CA was supplied",
            ),
            True,
        )
    return (
        ProbeCheck(
            CHECK_SERVER_ENFORCES_TLS,
            CheckStatus.ok,
            f"{host} does not enforce TLS ({evidence})",
        ),
        False,
    )


def _tls_in_force_check(cipher: str, *, mysql_home: str | None) -> ProbeCheck:
    if cipher:
        return ProbeCheck(
            CHECK_TLS_IN_FORCE,
            CheckStatus.ok,
            f"the probe connection is encrypted: Ssl_cipher is {cipher}",
        )
    if mysql_home:
        return ProbeCheck(
            CHECK_TLS_IN_FORCE,
            CheckStatus.fail,
            f"a CA was supplied through {mysql_home}/my.cnf but Ssl_cipher is empty, so the"
            " connection carries no TLS at all. The server most likely does not offer TLS, and"
            " Frappe's driver would then fail once db_ssl_ca is set in site_config.json.",
        )
    return ProbeCheck(
        CHECK_TLS_IN_FORCE,
        CheckStatus.warn,
        "Ssl_cipher is empty, so this connection is unencrypted. That is expected without"
        " --db-ca: there is no opportunistic TLS, so without a CA there is no TLS at all.",
    )


def _ca_verification_check(cipher: str, *, mysql_home: str | None, host: str) -> ProbeCheck:
    if not mysql_home:
        return ProbeCheck(
            CHECK_CA_VERIFICATION,
            CheckStatus.warn,
            "no --db-ca was given, so there is no chain and no hostname to verify",
        )
    if not cipher:
        return ProbeCheck(
            CHECK_CA_VERIFICATION,
            CheckStatus.fail,
            f"the CA in {mysql_home}/my.cnf was never exercised, because the connection is not encrypted",
        )
    return ProbeCheck(
        CHECK_CA_VERIFICATION,
        CheckStatus.ok,
        f"the CA in {mysql_home}/my.cnf verified this server's chain and the certificate names"
        f" {host} (ssl-verify-server-cert in the [client] option file)",
    )


def _parse_grants(lines: list[str]) -> tuple[set[str], bool, bool]:
    """Global privileges, whether roles are involved, whether a wildcard scope was seen."""
    granted: set[str] = set()
    roles = False
    wildcard = False
    for line in lines:
        if _ROLE_GRANT_RE.match(line):
            roles = True
            continue
        match = _GRANT_RE.match(line)
        if not match:
            continue
        scope = match.group("scope")
        privileges = {p.strip().upper() for p in match.group("privs").split(",") if p.strip()}
        if scope != "*.*":
            if "%" in scope or "_" in scope:
                wildcard = True
            continue
        if any(p.startswith("ALL PRIVILEGES") or p == "ALL" for p in privileges):
            granted |= set(ADMIN_PRIVILEGES)
        granted |= privileges
        if "WITH GRANT OPTION" in line.upper():
            granted.add("GRANT OPTION")
    return granted, roles, wildcard


def _admin_grants_check(reply: _Reply, *, user: str, secrets: tuple[str | None, ...]) -> ProbeCheck:
    """Check the admin account's ability without exercising it, before minutes of build time."""
    if not reply.ok:
        return ProbeCheck(
            CHECK_ADMIN_GRANTS,
            CheckStatus.warn,
            f"could not read the grants of {user!r}: {_summary(reply.text, *secrets)}. Provisioning"
            " needs CREATE, CREATE USER, RELOAD and GRANT OPTION at global scope.",
        )
    granted, roles, wildcard = _parse_grants([" ".join(row) for row in reply.rows])
    missing = sorted(ADMIN_PRIVILEGES - granted)
    if not missing:
        return ProbeCheck(
            CHECK_ADMIN_GRANTS,
            CheckStatus.ok,
            f"{user!r} holds the privileges provisioning needs (CREATE, CREATE USER, RELOAD, GRANT OPTION)",
        )
    detail = (
        f"{user!r} appears to be missing {', '.join(missing)} at global scope. Frappe's"
        " setup_database creates the schema and the user, grants on the schema, and ends in an"
        " unconditional FLUSH PRIVILEGES, which needs RELOAD."
    )
    if roles or wildcard:
        return ProbeCheck(
            CHECK_ADMIN_GRANTS,
            CheckStatus.warn,
            f"{detail} Not blocking: this account's grants come through"
            f" {'a role' if roles else 'a wildcard scope'}, so they cannot be read reliably here.",
        )
    return ProbeCheck(CHECK_ADMIN_GRANTS, CheckStatus.fail, detail)


def _app_parity_check(state: SchemaState, *, bench_apps: tuple[str, ...]) -> ProbeCheck:
    if not state.installed_apps:
        return ProbeCheck(
            CHECK_APP_PARITY,
            CheckStatus.warn,
            f"could not read {INSTALLED_APPLICATION_TABLE}, so the bench's apps were not compared against the site's",
        )
    if not bench_apps:
        return ProbeCheck(
            CHECK_APP_PARITY,
            CheckStatus.ok,
            f"the site reports {', '.join(state.installed_apps)}; no bench app list was supplied to compare against",
        )
    missing = [app for app in state.installed_apps if app not in bench_apps]
    if not missing:
        return ProbeCheck(
            CHECK_APP_PARITY,
            CheckStatus.ok,
            f"every app the site has installed is present in this bench ({', '.join(state.installed_apps)})",
        )
    return ProbeCheck(
        CHECK_APP_PARITY,
        CheckStatus.warn,
        f"the site has {', '.join(missing)} installed but this bench does not carry"
        f" {'them' if len(missing) > 1 else 'it'}. The site will fail at runtime on doctypes from"
        " an absent app. Not blocking, since attach writes nothing to the database: add them"
        " afterwards with fm update <bench> --apps <app>.",
    )


# --------------------------------------------------------------------------- stage two


def stage_two_script(site: str, schema: str) -> str:
    """The pymysql probe, as python source, run with the venv or app image interpreter.

    It reads the site's own `site_config.json`, so it exercises the exact `db_ssl_*` shapes
    Frappe's `get_connection_settings` builds, rather than a shape fm believes in. `database` is
    deliberately not selected, so the emptiness re-check works whether or not the schema exists.
    Every SQL literal goes through a placeholder, which keeps single quotes out of the source and
    the shell quoting of the whole one liner trivial.
    """
    _require_safe_name(site, "site name")
    _require_safe_name(schema, "schema name")
    config_path = f"{SITES_CONTAINER_ROOT}/{site}/site_config.json"
    installed = installed_apps_sql(schema)
    statements = [
        "import json,pymysql",
        f'c=json.load(open("{config_path}"))',
        'k={"user":c.get("db_user") or c["db_name"],"password":c.get("db_password"),'
        '"charset":"utf8mb4","collation":"utf8mb4_unicode_ci","use_unicode":True,"local_infile":False}',
        'k.update({"unix_socket":c["db_socket"]} if c.get("db_socket")'
        ' else {"host":c["db_host"],"port":int(c.get("db_port") or 3306)})',
        'k.update({"ssl":{"ca":c["db_ssl_ca"],"check_hostname":c.get("db_ssl_check_hostname")}}'
        ' if c.get("db_ssl_ca") else {})',
        "cn=pymysql.connect(**k)",
        "cu=cn.cursor()",
        f'cu.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",("{schema}",))',
        "sx=cu.fetchone()[0]",
        f'cu.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",("{schema}",))',
        "tc=cu.fetchone()[0]",
        'cu.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s'
        f' AND TABLE_NAME IN (%s,%s,%s)",("{schema}","{DOCTYPE_TABLE}","{SINGLES_TABLE}",'
        f'"{INSTALLED_APPLICATION_TABLE}"))',
        "tn=[r[0] for r in cu.fetchall()]",
        f'cu.execute("{installed}" if "{INSTALLED_APPLICATION_TABLE}" in tn else "SELECT 1 FROM DUAL WHERE 0")',
        "ap=[r[0] for r in cu.fetchall()]",
        'cu.execute("SHOW STATUS LIKE %s",("Ssl_cipher",))',
        "sc=cu.fetchone()",
        'cu.execute("SHOW VARIABLES LIKE %s",("require_secure_transport",))',
        "rst=cu.fetchone()",
        f'print("{STAGE_TWO_MARKER} "+json.dumps({{"schema_exists":bool(sx),"tables":tc,'
        '"tables_present":tn,"apps":ap,"ssl_cipher":(sc[1] if sc else ""),'
        '"require_secure_transport":(rst[1] if rst else ""),"db_socket":bool(c.get("db_socket")),'
        '"ssl_ca":c.get("db_ssl_ca") or "","check_hostname":c.get("db_ssl_check_hostname"),'
        '"server":cn.get_server_info()}))',
    ]
    return ";".join(statements)


#: The only interpreter in the bench container that can import pymysql. A bare ``python``
#: resolves to /workspace/frappe-bench/.uv/python-default/bin/python (exec-entrypoint.sh),
#: which cannot: the driver lives in the bench venv. Naming it here keeps stage two correct
#: for every caller instead of making each one prepend the venv to PATH.
BENCH_PYTHON = "/workspace/frappe-bench/env/bin/python"


def stage_two_command(site: str, schema: str) -> str:
    """`<bench venv python> -c '<script>'` for the container. Carries no secret: the site file has them."""
    return f"{BENCH_PYTHON} -c {shlex.quote(stage_two_script(site, schema))}"


def probe_stage_two(runner: Runner, *, site: str, schema: str) -> ProbeResult:
    """Phase 4 probe with the driver the site will actually use, plus the staleness re-check."""
    reply = _run(runner, stage_two_command(site, schema))
    payload = _stage_two_payload(reply.text)

    if payload is None:
        code = _PY_ERROR_RE.search(reply.text)
        detail = _summary_tail(reply.text)
        hint = ""
        if code and int(code.group(1)) == ER_ACCESS_DENIED:
            hint = (
                " The site login in site_config.json was rejected (1045), which is the same error"
                " for a wrong password and a missing account."
            )
        elif code and int(code.group(1)) == ER_SECURE_TRANSPORT_REQUIRED:
            hint = " The server requires TLS (3159) and db_ssl_ca is missing or not being read from the site file."
        return ProbeResult(
            (
                ProbeCheck(
                    CHECK_CONNECT,
                    CheckStatus.fail,
                    f"pymysql could not connect for site {site!r} using its own site_config.json: {detail}.{hint}",
                ),
            ),
            SchemaState(False, 0, False, ()),
            False,
            False,
            False,
        )

    apps = tuple(str(app) for app in payload.get("apps") or ())
    present = {str(name) for name in payload.get("tables_present") or ()}
    table_count = int(payload.get("tables") or 0)
    exists = bool(payload.get("schema_exists"))
    is_frappe = all(table in present for table in FRAPPE_CORE_TABLES) and "frappe" in apps
    state = SchemaState(exists, table_count, is_frappe, apps)

    cipher = str(payload.get("ssl_cipher") or "")
    ca = str(payload.get("ssl_ca") or "")
    check_hostname = payload.get("check_hostname")
    enforced = str(payload.get("require_secure_transport") or "").upper() in {"ON", "1"}

    checks = [
        ProbeCheck(
            CHECK_CONNECT,
            CheckStatus.ok,
            f"pymysql connected as the site login from {site}/site_config.json to"
            f" {payload.get('server', 'the server')}, which is the exact driver and config the"
            " site will use",
        ),
        ProbeCheck(
            CHECK_SCHEMA_STATE,
            CheckStatus.ok,
            f"re-checked immediately before provisioning: schema {schema!r}"
            + (f" holds {table_count} tables" if exists else " does not exist"),
        ),
        _tls_in_force_check(cipher, mysql_home=ca or None),
    ]

    if not ca:
        checks.append(
            ProbeCheck(
                CHECK_CA_VERIFICATION,
                CheckStatus.warn,
                "db_ssl_ca is not set in the site file, so the driver sends no TLS and verifies nothing",
            )
        )
    elif check_hostname is True:
        checks.append(
            ProbeCheck(
                CHECK_CA_VERIFICATION,
                CheckStatus.ok,
                f"the driver verified the chain against {ca} and db_ssl_check_hostname is true, so"
                " the certificate has to name this endpoint",
            )
        )
    else:
        checks.append(
            ProbeCheck(
                CHECK_CA_VERIFICATION,
                CheckStatus.warn,
                f"db_ssl_ca is {ca} but db_ssl_check_hostname is {check_hostname!r}, so pymysql"
                " verifies the chain and not the hostname. On a managed provider one regional CA"
                " signs every tenant's instance, so chain verification alone accepts any instance"
                " in that region.",
            )
        )

    checks.append(
        ProbeCheck(
            CHECK_DB_SOCKET,
            CheckStatus.warn if payload.get("db_socket") else CheckStatus.ok,
            "db_socket is set in the site file and silently overrides db_host and db_port"
            if payload.get("db_socket")
            else "db_socket is unset in the site file, so db_host and db_port are what the driver uses",
        )
    )
    checks.append(
        ProbeCheck(
            CHECK_SERVER_ENFORCES_TLS,
            CheckStatus.ok,
            f"@@require_secure_transport is {payload.get('require_secure_transport') or 'unset'}",
        )
    )
    checks.append(_frappe_schema_check(state, schema=schema, attach=False))

    return ProbeResult(tuple(checks), state, enforced, bool(cipher), True)


def _stage_two_payload(text: str) -> dict | None:
    for line in text.splitlines():
        marker = line.find(STAGE_TWO_MARKER)
        if marker < 0:
            continue
        try:
            parsed = json.loads(line[marker + len(STAGE_TWO_MARKER) :].strip())
        except ValueError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


# --------------------------------------------------------------------------- decision table


def credential_refusal(credentials: CredentialInputs, *, attach: bool, schema_exists: bool) -> str | None:
    """The design's credential error table. Returns the operator facing refusal, or None."""
    if attach and credentials.admin_given:
        return (
            "--db-admin-user/--db-admin-password cannot be used with --attach-existing-site:"
            " attach performs zero writes to the database and never provisions, so admin"
            " credentials are meaningless there and their presence signals a misunderstanding."
            " Pass --db-password with the existing site's database password instead."
        )
    if not credentials.site_password_given and not credentials.admin_given:
        return (
            "no database credentials given. Either pass --db-password, for a schema that already"
            " exists with a login fm can use, or pass --db-admin-user with --db-admin-password so"
            " Frappe can create the schema, the login and the grant."
        )
    if schema_exists and credentials.admin_given:
        target = credentials.db_name or "the target schema"
        if credentials.site_password_given:
            return (
                f"schema {target!r} already exists, so fm cannot create its login with"
                " --db-password: it would have to ALTER USER, which means fm owning SQL against a"
                " database it does not own. Admin credentials are only for the provisioning path."
                " Drop --db-admin-user/--db-admin-password and pass --db-password alone."
            )
        return (
            f"schema {target!r} already exists, so there is nothing to provision, and fm would"
            " have to ALTER USER to set a password it knows, which means owning SQL again. Pass"
            " --db-password with the existing login's password and drop the admin credentials."
        )
    if not credentials.supports_db_user and credentials.db_user and credentials.db_user != credentials.db_name:
        return (
            f"--db-user {credentials.db_user!r} differs from --db-name"
            f" {credentials.db_name!r}, and this bench runs Frappe v15, which has no db_user"
            " config key: the login must equal the schema name there. Drop --db-user, or create"
            " the bench on v16."
        )
    return None


def decide_flow(  # noqa: PLR0911 - one return per row of the design's decision table
    result: ProbeResult,
    *,
    attach: bool,
    credentials: CredentialInputs | None = None,
    schema: str | None = None,
    host: str | None = None,
) -> FlowDecision:
    """The design's Decision table. `credentials` adds the credential error rows when supplied."""
    target = f"schema {schema!r}" if schema else "the target schema"
    where = f" on {host}" if host else ""
    state = result.schema

    if credentials is not None:
        refusal = credential_refusal(credentials, attach=attach, schema_exists=state.exists)
        if refusal:
            return FlowDecision(Flow.refuse, refusal)

    if not result.ok:
        listed = "; ".join(f"{check.name}: {check.detail}" for check in result.failures)
        return FlowDecision(Flow.refuse, f"the database preflight refused this create. {listed}")

    if attach:
        if not state.exists:
            return FlowDecision(
                Flow.refuse,
                f"--attach-existing-site was given but {target} does not exist{where}: there is"
                " nothing to attach. Drop the flag and fm will create the site.",
            )
        if state.table_count == 0:
            return FlowDecision(
                Flow.refuse,
                f"--attach-existing-site was given but {target}{where} holds no tables: there is"
                " nothing to attach. Drop the flag and fm will create the site.",
            )
        if not state.is_frappe:
            reason = result.check(CHECK_FRAPPE_SCHEMA)
            return FlowDecision(
                Flow.refuse,
                f"{target}{where} has {state.table_count} tables but is not a Frappe schema, so"
                " fm will not attach to it: attaching would build a site directory around someone"
                f" else's data and report success. {reason.detail if reason else ''}".strip(),
            )
        return FlowDecision(
            Flow.attach,
            f"attaching to the existing site in {target}{where} ({state.table_count} tables,"
            f" apps: {', '.join(state.installed_apps) or 'none listed'}). new-site is not called"
            " in any form and nothing is written to the database.",
        )

    if not state.exists:
        return FlowDecision(
            Flow.provision,
            f"{target} does not exist{where}: Frappe will create the schema, the login and the"
            " grant through a direct setup_database call, then new-site --no-setup-db.",
        )
    if state.table_count == 0:
        return FlowDecision(
            Flow.adopt_empty,
            f"{target}{where} exists and holds no tables: fm will use it as is with new-site"
            " --no-setup-db, and no admin connection is opened at all.",
        )
    return FlowDecision(
        Flow.refuse,
        f"{target}{where} already has {state.table_count} tables. fm will not create a site in a"
        " schema that is not empty, because new-site bootstraps the database unconditionally and"
        " that drops the core tables. Pass --attach-existing-site to attach to the site it"
        " already holds, or point --db-name at an empty or absent schema.",
    )
