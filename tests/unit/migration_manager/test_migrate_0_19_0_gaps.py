"""
Mutation-gap tests for the v0.19.0 bench migration (``MigrationV0190``).

``fm migrate`` rewrites *real* user benches in place. ``migrate_0_19_0.py`` sits at
100% statement and branch coverage, yet two executed decisions were not pinned by
any assertion -- flipping them left the whole suite green:

Gap 1 -- ``_resolve_upload_limit``, the ``max_file_size % (1024 * 1024) == 0`` test
    That comparison decides *which arithmetic* converts the operator's byte count
    back into a human limit: exact floor division for whole mebibytes, rounding to
    the nearest MB otherwise. Every previously pinned value was a whole mebibyte,
    where floor and round agree, so the branch could be inverted for free. A byte
    count that is **not** a whole mebibyte (e.g. 25 000 000 -- 25 MB written in
    decimal, which is 23.84 MiB) separates them: the migration must round *up* to
    ``24M`` and never truncate to ``23M``, or the bench silently starts rejecting
    uploads the operator had already allowed.

Gap 2 -- ``_regenerate_supervisor_config``, ``allow_no_value=True`` on the *inner*
    (per-section) parser
    The rendered supervisor config is read by one parser and split into one file
    per program by a second one. Both must accept the same input. If the writer
    stops accepting value-less options, ``ConfigParser.set`` raises ``TypeError``
    part-way through the loop and the migration abandons the bench with a
    half-written ``config/`` directory -- some program files present, the rest and
    ``fm-web-server.sh`` missing, i.e. a bench that cannot boot its web server.

Every assertion looks at what is on disk afterwards; no docker, no network, no
real bench.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.migration_manager.migrations.migrate_0_19_0 import MigrationV0190
from frappe_manager.utils import helpers as helpers_module

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeBench:
    """Stand-in for ``MigrationBench`` exposing only what the migration reaches for."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.compose = MagicMock()
        self.workers_docker = MagicMock()
        self.running = False
        self.workers_running = False


@pytest.fixture
def migration():
    """A migration whose every collaborator is a mock and which is NOT a dev build."""
    m = MigrationV0190(output_handler=MagicMock())
    m.logger = MagicMock()
    m.backup_manager = MagicMock()
    m.backup_manager.backups = []
    m.services_manager = MagicMock()
    executor = Mock()
    executor.skip_backup = False
    executor.skip_backup_for = []
    m.migration_executor = executor
    m.is_dev_environment = False
    m.effective_image_tag = "vX.dev"
    m._images_updated = False
    return m


@pytest.fixture
def bench(tmp_path):
    """A fake bench tree at ``<tmp>/sites/test-bench``."""
    path = tmp_path / "sites" / "test-bench"
    (path / "workspace" / "frappe-bench" / "sites").mkdir(parents=True)
    (path / "configs" / "nginx" / "conf" / "conf.d").mkdir(parents=True)
    return FakeBench("test-bench", path)


COMPOSE_BEFORE = (
    "x-version: 0.18.0\n"
    "services:\n"
    "  frappe:\n"
    "    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0\n"
    "  nginx:\n"
    "    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0\n"
    "    environment:\n"
    "      SITENAME: test-bench\n"
)


def compose_after(client_max_body_size: str) -> str:
    """The exact bytes ``_migrate_docker_compose_yml`` must leave behind."""
    return (
        "x-version: 0.19.0\n"
        "services:\n"
        "  frappe:\n"
        "    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0\n"
        '    restart: "unless-stopped"\n'
        "  nginx:\n"
        "    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.19.0\n"
        "    environment:\n"
        '      SITE_MAPPINGS: \'{"test-bench": "test-bench"}\'\n'
        "      HTTPS_METHOD: noredirect\n"
        f"      CLIENT_MAX_BODY_SIZE: {client_max_body_size}\n"
        '    restart: "unless-stopped"\n'
    )


def site_config_path(bench: FakeBench) -> Path:
    return bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"


def printed(migration) -> list[str]:
    return [c.args[0] for c in migration.output.print.call_args_list if c.args]


# ===========================================================================
# Gap 1: bytes -> human limit, on a bench whose max_file_size is not a whole MiB
# ===========================================================================


class TestNonWholeMebibyteUploadLimit:
    """``max_file_size % (1024 * 1024) == 0`` picks floor division vs rounding."""

    def _prepare(self, bench, max_file_size: int):
        (bench.path / "docker-compose.yml").write_text(COMPOSE_BEFORE)
        site_config_path(bench).write_text(json.dumps({"db_host": "mariadb", "max_file_size": max_file_size}))
        vhostd = bench.path.parent.parent / "services" / "nginx-proxy" / "vhostd"
        vhostd.mkdir(parents=True)
        return vhostd

    def test_decimal_25mb_is_rounded_up_in_every_written_file(self, migration, bench):
        """25 000 000 bytes is 23.84 MiB: it must be reported as 24M, not truncated to 23M."""
        vhostd = self._prepare(bench, 25_000_000)
        migration._rebuild_runtime_environment = Mock()
        bench.compose.pull.return_value = SubprocessOutput(stdout=[], stderr=[], combined=[], exit_code=0)

        migration.migrate_bench(bench)

        assert (bench.path / "docker-compose.yml").read_text() == compose_after("24m")
        assert (vhostd / "test-bench").read_text() == "\nclient_max_body_size 24m;\n"
        assert (
            bench.path / "configs" / "nginx" / "conf" / "custom" / "upload-limit.conf"
        ).read_text() == "client_max_body_size 24m;\n"
        assert json.loads(site_config_path(bench).read_text()) == {
            "db_host": "mariadb",
            "max_file_size": 25_000_000,
        }, "the operator's byte count is the source of truth and is left untouched"

    def test_compose_env_matches_the_whole_mebibyte_neighbour_exactly(self, migration, bench):
        """The sibling case: a whole mebibyte count is converted by exact division."""
        self._prepare(bench, 25 * 1024**2)

        migration._migrate_docker_compose_yml(bench, bench.path / "docker-compose.yml")

        assert (bench.path / "docker-compose.yml").read_text() == compose_after("25m")

    @pytest.mark.parametrize(
        ("max_file_size", "expected"),
        [
            (25_000_000, "24M"),  # 23.84 MiB -> nearest MB, rounding up
            (25 * 1024**2, "25M"),  # whole MiB -> exact division
            (1024**2 + 1024**2 // 2, "2M"),  # 1.5 MiB -> rounds away from zero
            (1_717_986_918, "1638M"),  # 1.6 GiB: not a whole GiB, so reported in MB
            (2 * 1024**3, "2G"),  # whole GiB -> exact division
        ],
    )
    def test_reported_limit_for_each_arithmetic_branch(self, migration, bench, max_file_size, expected):
        site_config_path(bench).write_text(json.dumps({"max_file_size": max_file_size}))

        assert migration._resolve_upload_limit(bench) == expected
        assert f"Using existing site_config.json max_file_size: {expected} ({max_file_size} bytes)" in printed(
            migration
        )


# ===========================================================================
# Gap 2: splitting the rendered supervisor config must not reject value-less options
# ===========================================================================

MINIMAL_SUPERVISOR_TEMPLATE = """[program:{{ bench_name }}-frappe-web]
command=/bin/bash {{ bench_dir }}/config/fm-web-server.sh
autostart=true
redirect_stderr
user={{ user }}

[program:{{ bench_name }}-node-socketio]
command={{ node }} {{ bench_dir }}/apps/frappe/socketio.js
autostart=true

[group:{{ bench_name }}-web]
programs={{ bench_name }}-frappe-web,{{ bench_name }}-node-socketio
"""


class TestSupervisorSplitKeepsValuelessDirectives:
    def _config_dir(self, bench) -> Path:
        return bench.path / "workspace" / "frappe-bench" / "config"

    def test_valueless_directive_is_carried_into_its_program_file(self, migration, bench, tmp_path, monkeypatch):
        """The per-section writer must accept everything the reader produced.

        A value-less option (``redirect_stderr`` with no ``=``) parses to ``None``.
        If the writer rejects it, the loop dies half-way and the bench is left
        without the remaining program files or ``fm-web-server.sh``.
        """
        template = tmp_path / "supervisor.conf.tmpl"
        template.write_text(MINIMAL_SUPERVISOR_TEMPLATE)
        real_get_template_path = helpers_module.get_template_path
        monkeypatch.setattr(
            helpers_module,
            "get_template_path",
            lambda file_name, *a, **kw: (
                template if file_name == "supervisor.conf.tmpl" else real_get_template_path(file_name, *a, **kw)
            ),
        )
        site_config_path(bench).write_text("{}")

        migration._regenerate_supervisor_config(bench)

        config_dir = self._config_dir(bench)
        assert sorted(p.name for p in config_dir.iterdir()) == [
            "fm-web-server.sh",
            "socketio.fm.supervisor.conf",
            "web.fm.supervisor.conf",
        ], "a rejected option would abort the split before the later files were written"
        assert (config_dir / "web.fm.supervisor.conf").read_text() == (
            "[program:frappe-bench-frappe-web]\n"
            "command = /bin/bash /workspace/frappe-bench/config/fm-web-server.sh\n"
            "autostart = true\n"
            "redirect_stderr\n"
            "user = frappe\n"
            "\n"
        )

    def test_valueless_directive_reaching_the_render_from_common_site_config(self, migration, bench):
        """Proof the value-less case is reachable from real on-disk user data.

        ``workers`` is copied verbatim out of ``common_site_config.json`` into the
        template, so an operator's multi-line worker timeout puts a bare option
        line into the rendered config. That must not cost the bench its supervisor
        configs.
        """
        site_config_path(bench).write_text(
            json.dumps({"workers": {"custom": {"timeout": "360\nredirect_stderr", "background_workers": 1}}})
        )

        migration._regenerate_supervisor_config(bench)

        config_dir = self._config_dir(bench)
        assert sorted(p.name for p in config_dir.iterdir()) == [
            "custom-worker.workers.fm.supervisor.conf",
            "fm-web-server.sh",
            "long-worker.workers.fm.supervisor.conf",
            "schedule.fm.supervisor.conf",
            "short-worker.workers.fm.supervisor.conf",
            "socketio.fm.supervisor.conf",
            "web.fm.supervisor.conf",
        ]
        custom = (config_dir / "custom-worker.workers.fm.supervisor.conf").read_text()
        assert "stopwaitsecs = 360" in custom
        assert "\nredirect_stderr\n" in custom, "the value-less option is written back without a delimiter"
