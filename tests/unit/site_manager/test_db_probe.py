"""External database preflight probe: secret handling, the decision table and the probed checks.

Every test here drives the real `db_probe` entry points through a fake `Runner` that maps a
substring of the issued command to canned `mariadb --batch --skip-column-names` output, so the
module's own command building and output parsing are what is under test. Design reference:
`external-db-design.md`, "## Decision table" and "## Preflight probe".
"""

import json

import pytest

from frappe_manager.site_manager.modules.db_probe import (
    CHECK_ADMIN_GRANTS,
    CHECK_CA_VERIFICATION,
    CHECK_CHARACTER_SET,
    CHECK_CONNECT,
    CHECK_DB_USER_EXISTS,
    CHECK_FRAPPE_SCHEMA,
    CHECK_INNODB_READ_ONLY_COMPRESSED,
    CHECK_SERVER_ENFORCES_TLS,
    CHECK_SERVER_IS_MARIADB,
    CHECK_SERVER_VERSION,
    CHECK_SITE_CREDENTIALS,
    CHECK_TLS_IN_FORCE,
    MYSQL_CLIENT,
    STAGE_TWO_MARKER,
    CheckStatus,
    CredentialInputs,
    Flow,
    ProbeCheck,
    ProbeResult,
    SchemaState,
    decide_flow,
    get_lock_sql,
    lock_refusal,
    lock_taken,
    probe_stage_one,
    probe_stage_two,
    redact,
    stage_two_command,
)

HOST = "mydb.abc.rds.amazonaws.com"
SCHEMA = "app_prod"
SITE = "fm.example.com"
ADMIN_PASSWORD = "adm1n-pa55"
SITE_PASSWORD = "s1te-pa55"
MYSQL_HOME = "/workspace/frappe-bench/config/tls/fm.example.com"

# What the `mariadb` client prints for the errors the probe reasons about.
ERROR_NO_SUCH_GRANT = "ERROR 1141 (42000) at line 1: There is no such grant defined for user 'app_prod' on host '%'"
ERROR_ACCESS_DENIED = "ERROR 1045 (28000): Access denied for user 'app_prod'@'10.0.0.7' (using password: YES)"
ERROR_SECURE_TRANSPORT = (
    "ERROR 3159 (HY000): Connections using insecure transport are prohibited while"
    " --require_secure_transport=ON."
)

ADMIN_GRANT_LINE = "GRANT ALL PRIVILEGES ON *.* TO `dbadmin`@`%` WITH GRANT OPTION"
FRAPPE_TABLES = "tabDocType\ntabSingles\ntabInstalled Application"

DEFAULT_VARIABLES = {
    "version": "11.4.2-MariaDB-log",
    "version_comment": "mariadb.org binary distribution",
    "character_set_server": "utf8mb4",
    "collation_server": "utf8mb4_unicode_ci",
    "socket": "/run/mysqld/mysqld.sock",
    "innodb_read_only_compressed": "OFF",
    "require_secure_transport": "OFF",
    "Ssl_cipher": "",
}


def variables_reply(**overrides) -> str:
    """`SHOW VARIABLES ...; SHOW STATUS LIKE 'Ssl_cipher'` output. A value of None drops the row,
    which is how a server that does not have the variable at all is expressed."""
    values = {**DEFAULT_VARIABLES, **overrides}
    return "\n".join(f"{name}\t{value}" for name, value in values.items() if value is not None)


class FakeServer:
    """A canned server. Routes are tried in order; the first substring found in the command wins."""

    def __init__(
        self,
        *,
        variables: str | None = None,
        schema: str = "0\t0",
        frappe_tables: str = "",
        apps: str = "",
        grants: str = ERROR_NO_SUCH_GRANT,
        current_user_grants: str = ADMIN_GRANT_LINE,
        site_auth: str = "1",
        plaintext: str = "1",
    ) -> None:
        self.commands: list[str] = []
        self.routes: list[tuple[str, str]] = [
            # The plaintext TLS-enforcement probe is the only invocation carrying --skip-ssl, and
            # it runs the same `SELECT 1` as the site credential check, so it must match first.
            ("--skip-ssl", plaintext),
            ("CURRENT_USER", current_user_grants),
            ("SHOW GRANTS FOR", grants),
            ("SHOW VARIABLES", variables_reply() if variables is None else variables),
            ("information_schema.SCHEMATA", schema),
            ("TABLE_NAME IN", frappe_tables),
            ("SELECT app_name FROM", apps),
            ("SELECT 1", site_auth),
        ]

    def __call__(self, command: str) -> str:
        self.commands.append(command)
        for needle, reply in self.routes:
            if needle in command:
                return reply
        return ""

    def argv(self) -> list[str]:
        """The argument half of every issued command, with the environment prefix removed."""
        return [command[command.index(MYSQL_CLIENT) :] for command in self.commands]

    def env_prefixes(self) -> list[str]:
        return [command[: command.index(MYSQL_CLIENT)] for command in self.commands]


def stage_one(server: FakeServer, **overrides) -> ProbeResult:
    params: dict = {
        "host": HOST,
        "port": 3306,
        "schema": SCHEMA,
        "admin_user": "dbadmin",
        "admin_password": ADMIN_PASSWORD,
    }
    params.update(overrides)
    return probe_stage_one(server, **params)


def probe_result(
    *,
    exists: bool = False,
    table_count: int = 0,
    is_frappe: bool = False,
    apps: tuple[str, ...] = (),
    checks: tuple[ProbeCheck, ...] = (),
) -> ProbeResult:
    return ProbeResult(checks, SchemaState(exists, table_count, is_frappe, apps), False, False, False)


# ------------------------------------------------------------------ secrets never on a command line


def test_no_password_reaches_a_command_argument():
    # A -p<pass> argument is visible in the container's process listing; MYSQL_PWD is not.
    server = FakeServer(schema="1\t0", site_auth="1")
    stage_one(server, site_password=SITE_PASSWORD)

    assert server.commands, "the probe issued no commands at all"
    for arguments in server.argv():
        assert ADMIN_PASSWORD not in arguments
        assert SITE_PASSWORD not in arguments
        for token in arguments.split():
            assert not token.startswith("-p"), f"password bearing argument {token!r} in {arguments!r}"
            assert not token.startswith("--password")


def test_password_travels_as_a_mysql_pwd_environment_prefix():
    server = FakeServer(schema="1\t0")
    stage_one(server, site_password=SITE_PASSWORD)

    prefixes = server.env_prefixes()
    assert prefixes, "the probe issued no commands at all"
    for prefix in prefixes:
        assert "MYSQL_PWD=" in prefix
    supplied = {ADMIN_PASSWORD, SITE_PASSWORD}
    assert {prefix.split("MYSQL_PWD=")[1].strip() for prefix in prefixes} <= supplied
    assert f"MYSQL_PWD={SITE_PASSWORD}" in " ".join(prefixes)


def test_mysql_home_is_how_the_ca_reaches_the_client():
    # The CLI has no CA flag the probe can pass; MYSQL_HOME=<dir> makes it read <dir>/my.cnf.
    server = FakeServer(variables=variables_reply(Ssl_cipher="TLS_AES_256_GCM_SHA384"))
    stage_one(server, mysql_home=MYSQL_HOME)

    tls_commands = [command for command in server.commands if "--skip-ssl" not in command]
    assert tls_commands
    for command in tls_commands:
        assert f"MYSQL_HOME={MYSQL_HOME}" in command
    # The plaintext enforcement probe must not carry it, or a refusal would prove nothing.
    for command in server.commands:
        if "--skip-ssl" in command:
            assert "MYSQL_HOME=" not in command


def test_redact_masks_supplied_secrets_and_any_mysql_pwd_value():
    assert redact(f"login failed for {SITE_PASSWORD}", SITE_PASSWORD) == "login failed for ***"
    assert redact(f"MYSQL_PWD={SITE_PASSWORD} {MYSQL_CLIENT} -h db") == f"MYSQL_PWD=*** {MYSQL_CLIENT} -h db"
    assert redact("MYSQL_PWD=unknown-to-the-caller -e x") == "MYSQL_PWD=*** -e x"
    assert redact("nothing secret", None, "") == "nothing secret"


# ------------------------------------------------------------------ decision table, all six rows


def test_absent_schema_without_attach_provisions():
    decision = decide_flow(probe_result(exists=False), attach=False)
    assert decision.flow is Flow.provision
    assert not decision.refused


def test_existing_empty_schema_without_attach_is_adopted():
    decision = decide_flow(probe_result(exists=True, table_count=0), attach=False)
    assert decision.flow is Flow.adopt_empty
    assert not decision.refused


def test_existing_populated_schema_without_attach_refuses_with_count_and_flag():
    decision = decide_flow(probe_result(exists=True, table_count=137, is_frappe=True), attach=False, schema=SCHEMA)
    assert decision.refused
    assert "137" in decision.message
    assert "--attach-existing-site" in decision.message


def test_frappe_schema_with_attach_attaches():
    decision = decide_flow(
        probe_result(exists=True, table_count=137, is_frappe=True, apps=("frappe", "erpnext")),
        attach=True,
        schema=SCHEMA,
    )
    assert decision.flow is Flow.attach
    assert not decision.refused


def test_populated_non_frappe_schema_with_attach_refuses_and_reports_the_count():
    decision = decide_flow(
        probe_result(exists=True, table_count=12, is_frappe=False),
        attach=True,
        schema=SCHEMA,
    )
    assert decision.refused
    assert "12" in decision.message, "the operator cannot see what they pointed at without the table count"


@pytest.mark.parametrize(("exists", "table_count"), [(False, 0), (True, 0)])
def test_attach_with_nothing_to_attach_refuses(exists, table_count):
    decision = decide_flow(probe_result(exists=exists, table_count=table_count), attach=True, schema=SCHEMA)
    assert decision.refused
    assert "nothing to attach" in decision.message


# ------------------------------------------------------------------ credential error rows


def test_no_credentials_at_all_refuses_naming_both_paths():
    decision = decide_flow(
        probe_result(exists=False),
        attach=False,
        credentials=CredentialInputs(site_password_given=False, admin_given=False, db_name=SCHEMA),
    )
    assert decision.refused
    assert "--db-password" in decision.message
    assert "--db-admin-user" in decision.message
    assert "--db-admin-password" in decision.message


def test_site_password_with_admin_credentials_is_legal_when_the_schema_is_absent():
    decision = decide_flow(
        probe_result(exists=False),
        attach=False,
        credentials=CredentialInputs(site_password_given=True, admin_given=True, db_name=SCHEMA),
    )
    assert decision.flow is Flow.provision


def test_site_password_with_admin_credentials_refuses_once_the_schema_exists():
    decision = decide_flow(
        probe_result(exists=True, table_count=0),
        attach=False,
        credentials=CredentialInputs(site_password_given=True, admin_given=True, db_name=SCHEMA),
    )
    assert decision.refused
    assert "ALTER USER" in decision.message


def test_existing_schema_with_only_admin_credentials_refuses():
    decision = decide_flow(
        probe_result(exists=True, table_count=0),
        attach=False,
        credentials=CredentialInputs(site_password_given=False, admin_given=True, db_name=SCHEMA),
    )
    assert decision.refused
    assert "ALTER USER" in decision.message
    assert "--db-password" in decision.message


def test_attach_with_admin_credentials_refuses():
    decision = decide_flow(
        probe_result(exists=True, table_count=137, is_frappe=True, apps=("frappe",)),
        attach=True,
        credentials=CredentialInputs(site_password_given=True, admin_given=True, db_name=SCHEMA),
    )
    assert decision.refused
    assert "--attach-existing-site" in decision.message
    assert "--db-admin-user" in decision.message


def test_distinct_db_user_on_a_v15_bench_refuses():
    credentials = CredentialInputs(
        site_password_given=True,
        admin_given=False,
        db_name=SCHEMA,
        db_user="app_svc",
        supports_db_user=False,
    )
    decision = decide_flow(probe_result(exists=True, table_count=0), attach=False, credentials=credentials)
    assert decision.refused
    assert "app_svc" in decision.message
    assert "v15" in decision.message


def test_distinct_db_user_is_fine_on_a_v16_bench():
    credentials = CredentialInputs(
        site_password_given=True,
        admin_given=False,
        db_name=SCHEMA,
        db_user="app_svc",
        supports_db_user=True,
    )
    decision = decide_flow(probe_result(exists=True, table_count=0), attach=False, credentials=credentials)
    assert decision.flow is Flow.adopt_empty


# ------------------------------------------------------------------ server settings


@pytest.mark.parametrize("value", ["1", "ON"])
def test_innodb_read_only_compressed_on_is_a_failure_not_a_warning(value):
    # Core doctypes declare ROW_FORMAT=Compressed and Frappe rewrites that only for restores, so
    # a create fails outright. Refusing is actionable: it is a server setting.
    result = stage_one(FakeServer(variables=variables_reply(innodb_read_only_compressed=value)))
    check = result.check(CHECK_INNODB_READ_ONLY_COMPRESSED)
    assert check.status is CheckStatus.fail
    assert "innodb_read_only_compressed=0" in check.detail
    assert not result.ok


def test_innodb_read_only_compressed_absent_passes():
    result = stage_one(FakeServer(variables=variables_reply(innodb_read_only_compressed=None)))
    assert result.check(CHECK_INNODB_READ_ONLY_COMPRESSED).status is CheckStatus.ok
    assert result.ok


def test_mysql_is_refused_and_the_refusal_names_azure():
    variables = variables_reply(version="8.0.36", version_comment="MySQL Community Server - GPL")
    result = stage_one(FakeServer(variables=variables))
    check = result.check(CHECK_SERVER_IS_MARIADB)
    assert check.status is CheckStatus.fail
    assert "Azure" in check.detail, "an Azure user told only 'MySQL rejected' is left stuck"
    assert not result.ok


def test_mariadb_passes_the_flavour_check():
    result = stage_one(FakeServer())
    assert result.check(CHECK_SERVER_IS_MARIADB).status is CheckStatus.ok


def test_version_below_the_minimum_fails():
    result = stage_one(FakeServer(variables=variables_reply(version="10.5.23-MariaDB")))
    check = result.check(CHECK_SERVER_VERSION)
    assert check.status is CheckStatus.fail
    assert "10.5.23" in check.detail
    assert not result.ok


def test_version_at_the_minimum_passes():
    result = stage_one(FakeServer(variables=variables_reply(version="10.6.16-MariaDB")))
    assert result.check(CHECK_SERVER_VERSION).status is CheckStatus.ok
    assert result.ok


def test_charset_and_collation_mismatch_only_warns():
    # Frappe forces both per connection and per table, so this must not block a create.
    variables = variables_reply(character_set_server="latin1", collation_server="latin1_swedish_ci")
    result = stage_one(FakeServer(variables=variables))
    check = result.check(CHECK_CHARACTER_SET)
    assert check.status is CheckStatus.warn
    assert "latin1" in check.detail
    assert result.ok


# ------------------------------------------------------------------ TLS


def test_enforced_tls_without_a_ca_fails():
    # Every later connection, including the site bootstrap, would fail: there is no
    # opportunistic TLS, so without a CA there is no TLS at all.
    result = stage_one(FakeServer(variables=variables_reply(require_secure_transport="ON")), mysql_home=None)
    check = result.check(CHECK_SERVER_ENFORCES_TLS)
    assert check.status is CheckStatus.fail
    assert "--db-ca" in check.detail
    assert result.server_enforces_tls is True
    assert not result.ok


def test_enforcement_is_also_detected_from_a_refused_plaintext_connection():
    # The variable does not exist on every server, so a 3159 refusal is the other evidence.
    server = FakeServer(
        variables=variables_reply(require_secure_transport=None),
        plaintext=ERROR_SECURE_TRANSPORT,
    )
    result = stage_one(server, mysql_home=None)
    assert result.check(CHECK_SERVER_ENFORCES_TLS).status is CheckStatus.fail
    assert result.server_enforces_tls is True


def test_enforced_tls_with_a_ca_passes():
    variables = variables_reply(require_secure_transport="ON", Ssl_cipher="TLS_AES_256_GCM_SHA384")
    result = stage_one(FakeServer(variables=variables), mysql_home=MYSQL_HOME)
    assert result.check(CHECK_SERVER_ENFORCES_TLS).status is CheckStatus.ok
    assert result.server_enforces_tls is True
    assert result.ok


def test_tls_in_force_is_driven_by_a_non_empty_ssl_cipher():
    variables = variables_reply(Ssl_cipher="TLS_AES_256_GCM_SHA384")
    result = stage_one(FakeServer(variables=variables), mysql_home=MYSQL_HOME)
    assert result.tls_in_force is True
    assert result.check(CHECK_TLS_IN_FORCE).status is CheckStatus.ok
    assert result.check(CHECK_CA_VERIFICATION).status is CheckStatus.ok


def test_a_ca_that_was_never_exercised_fails():
    # Ssl_cipher empty with a CA configured means the connection carries no TLS at all, so the
    # CA verified nothing and Frappe's driver would fail once db_ssl_ca is in site_config.json.
    result = stage_one(FakeServer(variables=variables_reply(Ssl_cipher="")), mysql_home=MYSQL_HOME)
    assert result.tls_in_force is False
    assert result.check(CHECK_TLS_IN_FORCE).status is CheckStatus.fail
    assert result.check(CHECK_CA_VERIFICATION).status is CheckStatus.fail
    assert not result.ok


def test_no_ca_and_no_enforcement_is_unencrypted_but_not_a_failure():
    result = stage_one(FakeServer(), mysql_home=None)
    assert result.tls_in_force is False
    assert result.check(CHECK_TLS_IN_FORCE).status is CheckStatus.warn
    assert result.check(CHECK_CA_VERIFICATION).status is CheckStatus.warn
    assert result.ok


# ------------------------------------------------------------------ the site login


def test_show_grants_1141_means_the_login_is_absent():
    result = stage_one(FakeServer(grants=ERROR_NO_SUCH_GRANT))
    assert result.user_exists is False
    check = result.check(CHECK_DB_USER_EXISTS)
    assert check.status is CheckStatus.ok
    assert "1141" in check.detail


def test_existing_login_with_a_generated_password_is_a_refusal():
    # create_user is CREATE USER IF NOT EXISTS, so the account keeps a password fm does not know
    # and the site would be unconnectable, while Frappe still logs "Created or updated user".
    result = stage_one(FakeServer(grants="GRANT USAGE ON *.* TO `app_prod`@`%`"), site_password=None)
    assert result.user_exists is True
    check = result.check(CHECK_DB_USER_EXISTS)
    assert check.status is CheckStatus.fail
    assert "--db-password" in check.detail
    assert not result.ok


def test_existing_login_is_fine_when_its_password_was_supplied():
    result = stage_one(
        FakeServer(schema="1\t0", grants="GRANT USAGE ON *.* TO `app_prod`@`%`", site_auth="1"),
        admin_user=None,
        admin_password=None,
        site_password=SITE_PASSWORD,
    )
    assert result.user_exists is True
    assert result.check(CHECK_DB_USER_EXISTS).status is CheckStatus.ok
    assert result.ok


def test_a_rejected_site_password_fails_separately_from_existence():
    result = stage_one(
        FakeServer(schema="1\t0", grants="GRANT USAGE ON *.* TO `app_prod`@`%`", site_auth=ERROR_ACCESS_DENIED),
        admin_user=None,
        admin_password=None,
        site_password=SITE_PASSWORD,
    )
    assert not result.ok
    assert SITE_PASSWORD not in result.check(CHECK_SITE_CREDENTIALS).detail


# ------------------------------------------------------------ the admin account's grants


def test_a_global_grant_short_of_admin_is_read_privilege_by_privilege():
    """Only a genuine blanket grant covers everything provisioning needs.

    The whole point of reading the grants before the build is to refuse an account that cannot
    finish `setup_database`. Treating any global grant as blanket would report a read-only
    reporting login as fully privileged and the refusal would arrive minutes later, from
    Frappe, mid-provision.
    """
    result = stage_one(FakeServer(current_user_grants="GRANT SELECT, INSERT, UPDATE ON *.* TO `dbadmin`@`%`"))

    check = result.check(CHECK_ADMIN_GRANTS)
    assert check.status is CheckStatus.fail
    for privilege in ("CREATE", "CREATE USER", "RELOAD", "GRANT OPTION"):
        assert privilege in check.detail
    assert not result.ok


def test_the_short_all_spelling_of_a_blanket_grant_is_recognised():
    """`GRANT ALL ON *.*` is the same grant as `GRANT ALL PRIVILEGES ON *.*`.

    Both spellings are accepted by the server and either can come back from SHOW GRANTS, so the
    bare one must not be mistaken for a single privilege literally named ALL: that turns a
    fully privileged admin into a refusal naming privileges it already holds.
    """
    result = stage_one(FakeServer(current_user_grants="GRANT ALL ON *.* TO `dbadmin`@`%` WITH GRANT OPTION"))

    assert result.check(CHECK_ADMIN_GRANTS).status is CheckStatus.ok
    assert result.ok


# ------------------------------------------------------------------ advisory lock


def test_lock_sql_is_schema_scoped_with_a_zero_timeout():
    assert get_lock_sql(SCHEMA) == "SELECT GET_LOCK('fm:create:app_prod', 0)"


def test_lock_taken_reads_the_get_lock_result():
    assert lock_taken("1") is True
    assert lock_taken("1\n") is True
    assert lock_taken("0") is False
    assert lock_taken("NULL") is False
    assert lock_taken("") is False


def test_lock_refusal_names_the_schema():
    message = lock_refusal(SCHEMA)
    assert SCHEMA in message
    assert "fm:create:app_prod" in message


# ------------------------------------------------------------------ stage two


def stage_two_payload(**overrides) -> str:
    payload = {
        "schema_exists": True,
        "tables": 41,
        "tables_present": ["tabDocType", "tabSingles", "tabInstalled Application"],
        "apps": ["frappe", "erpnext"],
        "ssl_cipher": "TLS_AES_256_GCM_SHA384",
        "require_secure_transport": "ON",
        "db_socket": False,
        "ssl_ca": f"{MYSQL_HOME}/db-ca.pem",
        "check_hostname": True,
        "server": "11.4.2-MariaDB-log",
    }
    payload.update(overrides)
    return f"{STAGE_TWO_MARKER} {json.dumps(payload)}"


def test_stage_two_runs_the_bench_venv_interpreter_and_not_a_bare_python():
    # A bare `python` in the bench container resolves to the uv default interpreter, which has no
    # pymysql, so stage two would die on the import rather than probe anything.
    issued: list[str] = []

    def runner(command: str) -> str:
        issued.append(command)
        return stage_two_payload()

    result = probe_stage_two(runner, site=SITE, schema=SCHEMA)

    assert issued == [stage_two_command(SITE, SCHEMA)]
    interpreter = issued[0].split(" ", 1)[0]
    assert interpreter.startswith("/"), f"{interpreter!r} is resolved through PATH"
    assert interpreter.endswith("/env/bin/python"), f"{interpreter!r} is not the bench venv interpreter"
    assert "pymysql" in issued[0]
    # Proof the parsed result came from that command's output rather than a default.
    assert result.schema.table_count == 41
    assert result.schema.is_frappe is True
    assert result.tls_in_force is True
    assert result.server_enforces_tls is True
    assert result.ok


def test_stage_two_reports_an_absent_schema_for_the_staleness_recheck():
    result = probe_stage_two(
        lambda _: stage_two_payload(schema_exists=False, tables=0, tables_present=[], apps=[]),
        site=SITE,
        schema=SCHEMA,
    )
    assert result.schema.exists is False
    assert result.schema.table_count == 0
    assert decide_flow(result, attach=False).flow is Flow.provision


def test_stage_two_without_a_marker_line_is_a_connect_failure():
    traceback = "Traceback (most recent call last):\npymysql.err.OperationalError: (1045, 'Access denied')"
    result = probe_stage_two(lambda _: traceback, site=SITE, schema=SCHEMA)
    assert not result.ok
    assert "1045" in result.check(CHECK_CONNECT).detail


# ------------------------------------------------------------------ probe and decision together


def test_absent_schema_with_admin_credentials_probes_clean_and_provisions():
    server = FakeServer(schema="0\t0")
    result = stage_one(server)

    assert result.ok, [check.detail for check in result.failures]
    assert result.schema == SchemaState(False, 0, False, ())
    assert result.check(CHECK_ADMIN_GRANTS).status is CheckStatus.ok
    credentials = CredentialInputs(site_password_given=False, admin_given=True, db_name=SCHEMA)
    assert decide_flow(result, attach=True, credentials=credentials).refused
    assert decide_flow(result, attach=False, credentials=credentials).flow is Flow.provision


def test_frappe_schema_probes_clean_and_attaches():
    server = FakeServer(
        variables=variables_reply(),
        schema="1\t137",
        frappe_tables=FRAPPE_TABLES,
        apps="frappe\nerpnext",
        grants="GRANT USAGE ON *.* TO `app_prod`@`%`",
        site_auth="1",
    )
    result = stage_one(
        server,
        admin_user=None,
        admin_password=None,
        site_password=SITE_PASSWORD,
        attach=True,
        bench_apps=("frappe", "erpnext"),
    )

    assert result.ok, [check.detail for check in result.failures]
    assert result.schema == SchemaState(True, 137, True, ("frappe", "erpnext"))
    assert result.check(CHECK_FRAPPE_SCHEMA).status is CheckStatus.ok
    credentials = CredentialInputs(site_password_given=True, admin_given=False, db_name=SCHEMA)
    assert decide_flow(result, attach=True, credentials=credentials, schema=SCHEMA).flow is Flow.attach


def test_non_frappe_schema_is_a_failed_check_on_the_attach_path():
    server = FakeServer(
        schema="1\t12",
        frappe_tables="",
        grants="GRANT USAGE ON *.* TO `app_prod`@`%`",
        site_auth="1",
    )
    result = stage_one(
        server,
        admin_user=None,
        admin_password=None,
        site_password=SITE_PASSWORD,
        attach=True,
    )
    check = result.check(CHECK_FRAPPE_SCHEMA)
    assert check.status is CheckStatus.fail
    assert "12 tables" in check.detail
    decision = decide_flow(result, attach=True, schema=SCHEMA)
    assert decision.refused
    assert "12" in decision.message
