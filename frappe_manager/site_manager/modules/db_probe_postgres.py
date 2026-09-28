"""Postgres preflight probe, the sibling of :mod:`db_probe`.

A separate module rather than a branch inside the MariaDB probe, because almost nothing
transfers. That probe is a client-protocol reader: it shells out to `/usr/bin/mariadb`, passes
the password through `MYSQL_PWD`, and reasons about numeric `ERROR <code>` lines. Postgres
reports through `psql`, takes its password from `PGPASSWORD`, and says what went wrong in
SQLSTATE-backed prose. Sharing one function would mean a branch at every line of it.

What IS shared is the vocabulary: `ProbeCheck`, `CheckStatus`, `SchemaState` and `ProbeResult`
come from :mod:`db_probe`, so a caller reads the result of either probe the same way and
`decide_flow` keeps one implementation.

Same two-stage reasoning as the MariaDB probe, and the same reason for it: at the point a
preflight is worth running, phase 2 has not created the bench venv, so the only client on hand
is the one in the image. This module is stage ONE, the `psql` half. Stage two, the driver half,
belongs with `psycopg2` once that ships in the image.

Secrets: the password is passed through a `PGPASSWORD` environment prefix, never inside the
connection URI, so it does not appear in the container's process listing. The prefix value does
live in the command string handed to the ``Runner``, so callers MUST run
:func:`db_probe.redact` over anything they log.

Every query here was run against a real PostgreSQL 17 server before it was written down.
"""

import shlex

from frappe_manager.site_manager.modules.db_probe import (
    CHECK_CONNECT,
    CHECK_SCHEMA_STATE,
    CHECK_SERVER_VERSION,
    CheckStatus,
    ProbeCheck,
    ProbeResult,
    Runner,
    SchemaState,
    require_safe_name,
    run_query,
    summarize,
)

PSQL_CLIENT = "/usr/bin/psql"
DEFAULT_CONNECT_TIMEOUT = 10

# Frappe's Postgres support lands in 12 and its schema module uses `GENERATED ... AS IDENTITY`
# and `ON CONFLICT`, both of which predate it comfortably. The floor is about what Frappe tests
# against rather than about syntax.
MIN_SERVER_VERSION = (12, 0)

# Postgres reports one encoding per database and Frappe requires UTF8; there is no per-connection
# character-set negotiation to check, which is why this has no collation twin like the MariaDB
# probe's `utf8mb4_unicode_ci`.
WANTED_ENCODING = "UTF8"

CHECK_SERVER_IS_POSTGRES = "server_is_postgres"
CHECK_ENCODING = "encoding"
CHECK_ADMIN_ROLE = "admin_role"
CHECK_SCHEMA_PRIVILEGES = "schema_privileges"

# What psql prints, by cause. Matched on prose because psql does not surface SQLSTATE on stderr;
# each string was taken from a real failure rather than from the documentation.
AUTH_FAILURE_MARKERS = ("password authentication failed", "authentication failed for user")
MISSING_DATABASE_MARKER = "does not exist"
UNREACHABLE_MARKERS = (
    "could not connect to server",
    "connection refused",
    "could not translate host name",
    "timeout expired",
    "connection timed out",
    "no route to host",
)


def build_psql_command(
    *,
    host: str,
    port: int,
    user: str,
    password: str | None,
    dbname: str,
    sql: str,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    sslmode: str | None = None,
    sslrootcert: str | None = None,
) -> str:
    """One `psql` invocation, unaligned and tuple-only, password via the environment.

    `-tAF'|'` is what makes the output parseable: no column headers, no alignment padding, and a
    field separator that cannot appear in the identifiers this probe reads back. `-v
    ON_ERROR_STOP=1` turns a failed statement into a non-zero exit, which the ``Runner`` surfaces
    as text this module can classify; without it psql reports the error and exits 0.
    """
    env = [f"PGCONNECT_TIMEOUT={connect_timeout}"]
    if password is not None:
        env.append(f"PGPASSWORD={shlex.quote(password)}")
    if sslmode:
        env.append(f"PGSSLMODE={shlex.quote(sslmode)}")
    if sslrootcert:
        env.append(f"PGSSLROOTCERT={shlex.quote(sslrootcert)}")

    argv = [
        PSQL_CLIENT,
        "-h",
        shlex.quote(host),
        "-p",
        str(port),
        "-U",
        shlex.quote(user),
        "-d",
        shlex.quote(dbname),
        "-tAF'|'",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        shlex.quote(sql),
    ]
    return " ".join([*env, *argv])


def settings_sql() -> str:
    """Server identity, version and encoding in one round trip, one value per line."""
    return (
        "select version() union all "
        "select current_setting('server_version_num') union all "
        "select current_setting('server_encoding')"
    )


def admin_role_sql() -> str:
    """What the login fm was given can actually do.

    Frappe's `setup_database` issues `CREATE DATABASE`, `CREATE USER`/`ALTER USER` and
    `GRANT`, so the three attributes below are exactly the ones whose absence turns into a
    failure part way through a create.
    """
    return "select rolsuper, rolcreatedb, rolcreaterole from pg_roles where rolname = current_user"


# Every name interpolated below passes `require_safe_name` first, which raises on anything a
# database, role or schema may not be called -- so the S608 findings are the linter seeing an
# f-string, not an unvalidated one. Parameters are not an option: `psql -c` has no bind
# mechanism, and a name cannot be a placeholder in Postgres anyway.
def database_exists_sql(dbname: str) -> str:
    return f"select 1 from pg_database where datname = '{require_safe_name(dbname, 'database')}'"  # noqa: S608


def role_exists_sql(role: str) -> str:
    return f"select 1 from pg_roles where rolname = '{require_safe_name(role, 'role')}'"  # noqa: S608


def table_count_sql(schema: str = "public") -> str:
    """Tables in the site's schema. Postgres has a schema layer MariaDB does not, and Frappe puts
    everything in `public` unless `db_schema` says otherwise, so emptiness is a question about a
    schema inside a database rather than about the database itself."""
    return (
        "select count(*) from information_schema.tables "  # noqa: S608
        f"where table_schema = '{require_safe_name(schema, 'schema')}' and table_type = 'BASE TABLE'"
    )


def frappe_tables_sql(schema: str = "public") -> str:
    return (
        "select table_name from information_schema.tables "  # noqa: S608
        f"where table_schema = '{require_safe_name(schema, 'schema')}' "
        "and table_name in ('tabDocType', 'tabSingles')"
    )


def installed_apps_sql(schema: str = "public") -> str:
    return f'select name from "{require_safe_name(schema, "schema")}"."tabInstalled Application"'  # noqa: S608


def tls_sql() -> str:
    """Whether THIS connection is encrypted, asked of the server rather than inferred.

    `pg_stat_ssl` reports the backend's own view, so an answer of false means the session really
    is in the clear, whatever the client believed it negotiated.
    """
    return "select ssl, coalesce(version, ''), coalesce(cipher, '') from pg_stat_ssl where pid = pg_backend_pid()"


def _connect_failure(text: str, *, host: str, port: int, user: str, secrets: tuple[str | None, ...]) -> ProbeCheck:
    """Name the cause psql described, or say plainly that it could not be reached.

    Classified on the message because psql does not print SQLSTATE: the caller sees prose, so
    the probe reads the same prose rather than pretending to a code it never receives.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in AUTH_FAILURE_MARKERS):
        reason = f"{user} was refused by {host}:{port}"
    elif any(marker in lowered for marker in UNREACHABLE_MARKERS):
        reason = f"{host}:{port} is not reachable from the bench container"
    elif MISSING_DATABASE_MARKER in lowered:
        reason = f"{host}:{port} answered, but the database named does not exist on it"
    else:
        reason = f"{host}:{port} did not answer a query"
    return ProbeCheck(CHECK_CONNECT, CheckStatus.fail, f"{reason}: {summarize(text, *secrets)}")


def _flavour_check(banner: str) -> ProbeCheck:
    """A server that is not PostgreSQL behind a Postgres port.

    Worth a check rather than an assumption: CockroachDB, YugabyteDB and pgbouncer's admin
    console all speak the wire protocol and answer `version()` with their own name, and Frappe's
    `db_type = postgres` means PostgreSQL specifically.
    """
    if "postgresql" in banner.lower():
        return ProbeCheck(CHECK_SERVER_IS_POSTGRES, CheckStatus.ok, f"server is PostgreSQL ({banner})")
    return ProbeCheck(
        CHECK_SERVER_IS_POSTGRES,
        CheckStatus.fail,
        f"server reports {banner!r}, which speaks the PostgreSQL wire protocol but is not PostgreSQL. "
        "Frappe's postgres backend targets PostgreSQL itself; a compatible server accepts the "
        "connection and then diverges on the DDL Frappe emits, which breaks later rather than now.",
    )


def _version_check(version_num: str) -> ProbeCheck:
    """`server_version_num` rather than the banner: it is already an integer
    (major * 10000 + minor), so there is no text to parse and no way to compare tuples of
    different length, which is the trap the MariaDB probe's 2-tuple rule exists for."""
    wanted = MIN_SERVER_VERSION[0] * 10000
    try:
        numeric = int(version_num)
    except ValueError:
        return ProbeCheck(
            CHECK_SERVER_VERSION,
            CheckStatus.warn,
            f"server version could not be read ({version_num!r}); wanted {MIN_SERVER_VERSION[0]} or newer",
        )
    if numeric < wanted:
        return ProbeCheck(
            CHECK_SERVER_VERSION,
            CheckStatus.fail,
            f"server is {numeric // 10000}.{numeric % 10000}, older than the {MIN_SERVER_VERSION[0]} "
            "Frappe's postgres backend is tested against",
        )
    return ProbeCheck(
        CHECK_SERVER_VERSION,
        CheckStatus.ok,
        f"server is {numeric // 10000}.{numeric % 10000} (>= {MIN_SERVER_VERSION[0]})",
    )


def _encoding_check(encoding: str) -> ProbeCheck:
    if encoding.upper() == WANTED_ENCODING:
        return ProbeCheck(CHECK_ENCODING, CheckStatus.ok, f"server encoding is {encoding}")
    return ProbeCheck(
        CHECK_ENCODING,
        CheckStatus.fail,
        f"server encoding is {encoding}, not {WANTED_ENCODING}. Frappe stores arbitrary unicode, and "
        "a database created under another encoding rejects it on write rather than at setup.",
    )


def _admin_role_check(row: str, *, user: str) -> ProbeCheck:
    """`CREATE DATABASE`, role creation and `GRANT` are what Frappe's setup_database issues.

    A superuser has all three implicitly, so the attributes are only consulted when it is not one
    -- which is the managed-provider case, where no login is ever a true superuser.
    """
    parts = row.split("|")
    if len(parts) != 3:
        return ProbeCheck(
            CHECK_ADMIN_ROLE, CheckStatus.warn, f"could not read the privileges of {user}: {row!r}"
        )
    superuser, createdb, createrole = (part.strip() == "t" for part in parts)
    if superuser:
        return ProbeCheck(CHECK_ADMIN_ROLE, CheckStatus.ok, f"{user} is a superuser")
    missing = [
        label
        for label, held in (("CREATEDB", createdb), ("CREATEROLE", createrole))
        if not held
    ]
    if missing:
        return ProbeCheck(
            CHECK_ADMIN_ROLE,
            CheckStatus.fail,
            f"{user} is not a superuser and lacks {', '.join(missing)}. Creating a site needs both: "
            "Frappe's setup_database issues CREATE DATABASE and creates the site's own role.",
        )
    return ProbeCheck(CHECK_ADMIN_ROLE, CheckStatus.ok, f"{user} holds CREATEDB and CREATEROLE")


def _schema_state(
    *,
    database_exists: bool,
    table_count: int,
    frappe_tables: tuple[str, ...],
    installed_apps: tuple[str, ...],
) -> tuple[SchemaState, ProbeCheck]:
    state = SchemaState(
        exists=database_exists,
        table_count=table_count,
        is_frappe=len(frappe_tables) == 2,
        installed_apps=installed_apps,
    )
    if not database_exists:
        detail = "the database does not exist yet; fm will create it"
    elif table_count == 0:
        detail = "the database exists and is empty"
    elif state.is_frappe:
        detail = f"the database holds a Frappe site ({table_count} tables)"
    else:
        detail = f"the database holds {table_count} tables that are not a Frappe site"
    return state, ProbeCheck(CHECK_SCHEMA_STATE, CheckStatus.ok, detail)


def probe_stage_one(
    runner: Runner,
    *,
    host: str,
    port: int,
    user: str,
    password: str | None,
    dbname: str,
    site_login: str | None = None,
    schema: str = "public",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    sslmode: str | None = None,
    sslrootcert: str | None = None,
) -> ProbeResult:
    """Everything the `psql` client can establish before a site is created.

    Connects to `dbname` when it exists and to the server's own `postgres` database otherwise,
    because Postgres has no "connect without selecting a database" mode: the endpoint has to be
    proved reachable before the site's database is known to exist.
    """
    secrets = (password,)
    checks: list[ProbeCheck] = []

    def ask(sql: str, *, database: str) -> tuple[bool, str]:
        reply = run_query(
            runner,
            build_psql_command(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=database,
                sql=sql,
                connect_timeout=connect_timeout,
                sslmode=sslmode,
                sslrootcert=sslrootcert,
            ),
        )
        return reply.ok, reply.text

    ok, text = ask(settings_sql(), database="postgres")
    if not ok:
        checks.append(_connect_failure(text, host=host, port=port, user=user, secrets=secrets))
        return ProbeResult(
            checks=tuple(checks),
            schema=SchemaState(exists=False, table_count=0, is_frappe=False, installed_apps=()),
            server_enforces_tls=False,
            tls_in_force=False,
            user_exists=False,
        )

    settings = [line.strip() for line in text.splitlines() if line.strip()]
    banner, version_num, encoding = (*settings, "", "", "")[:3]
    checks.append(ProbeCheck(CHECK_CONNECT, CheckStatus.ok, f"connected to {host}:{port} as {user}"))
    checks.append(_flavour_check(banner))
    checks.append(_version_check(version_num))
    checks.append(_encoding_check(encoding))

    _, row = ask(admin_role_sql(), database="postgres")
    checks.append(_admin_role_check(row.strip().splitlines()[0] if row.strip() else "", user=user))

    _, tls_row = ask(tls_sql(), database="postgres")
    tls_in_force = tls_row.strip().startswith("t")

    _, exists_row = ask(database_exists_sql(dbname), database="postgres")
    database_exists = exists_row.strip().startswith("1")

    table_count = 0
    frappe_tables: tuple[str, ...] = ()
    installed_apps: tuple[str, ...] = ()
    if database_exists:
        _, count_row = ask(table_count_sql(schema), database=dbname)
        table_count = int(count_row.strip() or 0)
        _, tables = ask(frappe_tables_sql(schema), database=dbname)
        frappe_tables = tuple(line.strip() for line in tables.splitlines() if line.strip())
        if len(frappe_tables) == 2:
            apps_ok, apps = ask(installed_apps_sql(schema), database=dbname)
            if apps_ok:
                installed_apps = tuple(line.strip() for line in apps.splitlines() if line.strip())

    state, state_check = _schema_state(
        database_exists=database_exists,
        table_count=table_count,
        frappe_tables=frappe_tables,
        installed_apps=installed_apps,
    )
    checks.append(state_check)

    user_exists = False
    if site_login:
        _, role_row = ask(role_exists_sql(site_login), database="postgres")
        user_exists = role_row.strip().startswith("1")

    return ProbeResult(
        checks=tuple(checks),
        # Postgres has no server-side "require TLS from every client" setting to read back: it is
        # decided per connection by pg_hba.conf, which a client cannot see. So the probe reports
        # what THIS connection got and claims nothing about enforcement.
        server_enforces_tls=False,
        tls_in_force=tls_in_force,
        user_exists=user_exists,
        schema=state,
    )
