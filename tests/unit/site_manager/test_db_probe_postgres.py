"""What the Postgres preflight establishes before a site is created.

The MariaDB probe and this one share a result vocabulary and nothing else: that one reasons about
numeric `ERROR <code>` lines from `/usr/bin/mariadb`, this one about the prose `psql` prints,
because psql does not surface SQLSTATE on stderr. Every canned output below was taken from a real
PostgreSQL 17 server rather than written from the documentation, which is the only thing that
makes prose matching defensible.

What these defend is the direction of each answer. A probe that says "fine" about a server that
is not fine produces a create that fails half way through, with a site directory already on disk
and a role already altered.
"""

import pytest

from frappe_manager.site_manager.modules.db_probe import CheckStatus
from frappe_manager.site_manager.modules.db_probe_postgres import (
    CHECK_ADMIN_ROLE,
    CHECK_CONNECT,
    CHECK_ENCODING,
    CHECK_SERVER_IS_POSTGRES,
    CHECK_SERVER_VERSION,
    build_psql_command,
    probe_stage_one,
)

BANNER = "PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc, 64-bit"
SETTINGS = f"{BANNER}\n170011\nUTF8\n"
SUPERUSER = "t|t|t\n"

# Captured from psql, not paraphrased.
AUTH_FAILURE = (
    'psql: error: connection to server at "pg.example" (10.2.0.9), port 5432 failed: '
    'FATAL:  password authentication failed for user "postgres"'
)
UNREACHABLE = (
    'psql: error: connection to server at "10.255.255.1", port 5432 failed: Connection timed out\n'
    "\tIs the server running on that host and accepting TCP/IP connections?"
)


class FakeRunner:
    """Answers each query by the SQL it recognises, so a test states only what it changes."""

    def __init__(self, **overrides):
        self.replies = {
            "version()": SETTINGS,
            "rolsuper": SUPERUSER,
            "pg_stat_ssl": "f||\n",
            "pg_database": "",
            "pg_roles where rolname =": "",
            "count(*)": "0\n",
            "table_name in": "",
            "tabInstalled Application": "",
        }
        self.replies.update(overrides)
        self.commands: list[str] = []

    def __call__(self, command: str) -> str:
        self.commands.append(command)
        for marker, reply in self.replies.items():
            if marker in command:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return ""


def _probe(runner, **kwargs):
    base = {"host": "pg.example", "port": 5432, "user": "postgres", "password": "secret", "dbname": "shop_app"}
    return probe_stage_one(runner, **{**base, **kwargs})


class TestTheCommand:
    def test_the_password_never_appears_in_the_argument_list(self):
        """It goes in the environment, so it stays out of the container's process listing. The
        connection URI form would put it in argv for anyone running `ps`."""
        command = build_psql_command(
            host="h", port=5432, user="u", password="s3cret", dbname="d", sql="select 1"
        )

        assert "PGPASSWORD=" in command
        assert command.index("PGPASSWORD") < command.index("/usr/bin/psql")
        assert "postgresql://" not in command

    def test_a_failed_statement_is_a_failed_command(self):
        """Without ON_ERROR_STOP psql reports the error and exits 0, so every check would read as
        passing against a server that refused the query."""
        command = build_psql_command(host="h", port=5432, user="u", password=None, dbname="d", sql="select 1")

        assert "ON_ERROR_STOP=1" in command

    def test_the_output_is_machine_readable(self):
        """Headers and alignment padding would have to be parsed back out of every answer."""
        command = build_psql_command(host="h", port=5432, user="u", password=None, dbname="d", sql="select 1")

        assert "-tAF'|'" in command


class TestTheServerItself:
    def test_a_healthy_server_passes_every_check(self):
        result = _probe(FakeRunner())

        assert result.ok
        assert result.check(CHECK_CONNECT).status is CheckStatus.ok
        assert result.check(CHECK_SERVER_VERSION).detail.startswith("server is 17.11")

    def test_a_wire_compatible_server_that_is_not_postgres_is_refused(self):
        """CockroachDB and YugabyteDB answer version() with their own name and then diverge on the
        DDL Frappe emits, which breaks after the create rather than during it."""
        runner = FakeRunner(**{"version()": "CockroachDB CCL v23.1\n230000\nUTF8\n"})

        result = _probe(runner)

        assert result.check(CHECK_SERVER_IS_POSTGRES).status is CheckStatus.fail
        assert not result.ok

    def test_a_server_older_than_frappe_supports_is_refused(self):
        runner = FakeRunner(**{"version()": "PostgreSQL 11.2\n110002\nUTF8\n"})

        result = _probe(runner)

        assert result.check(CHECK_SERVER_VERSION).status is CheckStatus.fail

    def test_the_version_is_read_as_a_number_not_a_tuple(self):
        """`server_version_num` is already major*10000+minor, so there is no text to parse and no
        way to compare tuples of unequal length -- the trap the MariaDB probe guards by hand."""
        runner = FakeRunner(**{"version()": "PostgreSQL 12.0\n120000\nUTF8\n"})

        result = _probe(runner)

        assert result.check(CHECK_SERVER_VERSION).status is CheckStatus.ok

    def test_a_non_utf8_server_is_refused(self):
        """Frappe stores arbitrary unicode; another encoding rejects it on write, long after the
        create reported success."""
        runner = FakeRunner(**{"version()": f"{BANNER}\n170011\nLATIN1\n"})

        result = _probe(runner)

        assert result.check(CHECK_ENCODING).status is CheckStatus.fail


class TestTheLoginFmWasGiven:
    def test_a_superuser_is_enough(self):
        result = _probe(FakeRunner())

        assert result.check(CHECK_ADMIN_ROLE).detail.endswith("is a superuser")

    def test_a_managed_provider_login_with_both_attributes_passes(self):
        """No managed Postgres hands out a true superuser, so the attributes are the real test."""
        runner = FakeRunner(rolsuper="f|t|t\n")

        result = _probe(runner)

        assert result.check(CHECK_ADMIN_ROLE).status is CheckStatus.ok

    @pytest.mark.parametrize(
        ("row", "missing"),
        [("f|f|t\n", "CREATEDB"), ("f|t|f\n", "CREATEROLE"), ("f|f|f\n", "CREATEDB, CREATEROLE")],
    )
    def test_a_login_that_cannot_do_what_setup_database_needs_is_refused(self, row, missing):
        """Frappe's setup_database issues CREATE DATABASE and creates the site's own role. Finding
        that out part way through leaves a site directory on disk and no database."""
        runner = FakeRunner(rolsuper=row)

        result = _probe(runner)

        check = result.check(CHECK_ADMIN_ROLE)
        assert check.status is CheckStatus.fail
        assert missing in check.detail


class TestWhatWentWrong:
    def test_a_rejected_password_is_named_as_such(self):
        runner = FakeRunner(**{"version()": RuntimeError(AUTH_FAILURE)})

        result = _probe(runner)

        assert "was refused by" in result.check(CHECK_CONNECT).detail
        assert not result.ok

    def test_an_unreachable_endpoint_is_not_reported_as_an_auth_problem(self):
        """The two need different remedies, and psql's own text is the only evidence there is."""
        runner = FakeRunner(**{"version()": RuntimeError(UNREACHABLE)})

        result = _probe(runner)

        detail = result.check(CHECK_CONNECT).detail
        assert "not reachable" in detail
        assert "refused" not in detail

    def test_a_failed_connection_stops_the_probe(self):
        """Every later check would dial the same dead endpoint and report its own failure, burying
        the one line that matters under six copies of it."""
        runner = FakeRunner(**{"version()": RuntimeError(UNREACHABLE)})

        result = _probe(runner)

        assert len(result.checks) == 1

    def test_the_password_is_not_echoed_in_the_failure(self):
        runner = FakeRunner(**{"version()": RuntimeError(AUTH_FAILURE)})

        result = _probe(runner, password="hunter2")

        assert "hunter2" not in result.check(CHECK_CONNECT).detail


class TestWhatIsAlreadyThere:
    def test_an_absent_database_is_reported_as_absent(self):
        result = _probe(FakeRunner())

        assert result.schema.exists is False
        assert result.schema.table_count == 0

    def test_an_empty_database_is_told_apart_from_an_absent_one(self):
        """`fm create` treats them differently: one is provisioned, the other adopted."""
        runner = FakeRunner(pg_database="1\n")

        result = _probe(runner)

        assert (result.schema.exists, result.schema.table_count) == (True, 0)

    def test_a_frappe_site_is_recognised_and_its_apps_read(self):
        runner = FakeRunner(
            pg_database="1\n",
            **{
                "count(*)": "312\n",
                "table_name in": "tabDocType\ntabSingles\n",
                "tabInstalled Application": "frappe\nerpnext\n",
            },
        )

        result = _probe(runner)

        assert result.schema.is_frappe is True
        assert result.schema.installed_apps == ("frappe", "erpnext")

    def test_a_database_holding_someone_elses_tables_is_not_a_frappe_site(self):
        """The case that must never be adopted silently: real data fm did not put there."""
        runner = FakeRunner(pg_database="1\n", **{"count(*)": "2\n", "table_name in": ""})

        result = _probe(runner)

        assert result.schema.exists is True
        assert result.schema.is_frappe is False
        assert result.schema.table_count == 2

    def test_an_existing_site_role_is_reported(self):
        """Postgres ALTERs an existing role's password during setup_database, so whether the role
        is already there decides whether a create is about to change someone's credentials."""
        runner = FakeRunner(**{"pg_roles where rolname =": "1\n"})

        result = _probe(runner, site_login="shop_role")

        assert result.user_exists is True

    def test_the_role_is_not_probed_when_no_site_login_is_given(self):
        result = _probe(FakeRunner())

        assert result.user_exists is False


class TestTLS:
    def test_an_encrypted_connection_is_reported(self):
        runner = FakeRunner(pg_stat_ssl="t|TLSv1.3|TLS_AES_256_GCM_SHA384\n")

        result = _probe(runner)

        assert result.tls_in_force is True

    def test_a_plaintext_connection_is_reported_as_plaintext(self):
        result = _probe(FakeRunner())

        assert result.tls_in_force is False

    def test_enforcement_is_never_claimed(self):
        """Postgres decides TLS per connection in pg_hba.conf, which a client cannot read. The
        MariaDB probe reads a server variable; there is no equivalent here, so claiming one would
        be inventing an answer."""
        runner = FakeRunner(pg_stat_ssl="t|TLSv1.3|TLS_AES_256_GCM_SHA384\n")

        result = _probe(runner)

        assert result.server_enforces_tls is False
