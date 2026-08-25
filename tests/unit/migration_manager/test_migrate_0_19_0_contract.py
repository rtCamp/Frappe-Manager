"""
Characterization tests for the v0.19.0 bench migration (``MigrationV0190``).

Contract defended
-----------------
``fm migrate`` rewrites *real, existing user benches* in place: it rewrites
``bench_config.toml``, both compose files, nginx configs, the site config, the
supervisor configs and the whole Python/Node runtime inside the container. A
behaviour change here silently damages installations that already work, and the
module was almost entirely unprotected.

These tests pin CURRENT behaviour so that a later refactor -- in particular the
planned deduplication of the eight near-identical blocks in this module -- is
provably behaviour preserving. They are a specification, not a wish list: where
the current code looks wrong it is pinned as-is and reported as a suspicion.

The duplication cluster is pinned explicitly in
``TestDuplicatedComposeYamlBlocks`` and ``TestDuplicatedComposeRunBlocks``.
Those two classes assert the *differences* between the sibling blocks, because
the differences are exactly what a merge is at risk of flattening:

  yaml-loader blocks (2)
    ``_migrate_docker_compose_yml``     -- resolves upload limit + rewrites nginx env
    ``_migrate_workers_compose_yml``    -- does NOT touch nginx env at all

  ``bench.compose.run`` blocks (6)
    ``_check_runtime_current``          -- swallows everything, returns (False, False)
    ``_setup_python_with_uv``           -- translates docker-network errors, raises on failure
    ``_setup_node_with_fnm``            -- no try/except at all, raises on failure
    ``_cleanup_old_runtime_dirs``       -- non-zero exit is a WARNING, migration continues
    ``_reinstall_apps_and_rebuild``     -- raises on failure, script built conditionally
    ``_auto_detect_runtime_versions``   -- two raw commands (no ``shlex.quote``), errors swallowed

No test touches docker, the network, or a real bench: every bench lives in
``tmp_path`` and every collaborator is mocked at its boundary.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import tomlkit
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.migration_manager.migration_exceptions import MigrationExceptionInBench
from frappe_manager.migration_manager.migrations.migrate_0_19_0 import MigrationV0190

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeBench:
    """Stand-in for ``MigrationBench``.

    Only the attributes the migration actually reaches for are provided, so a
    test fails loudly if the migration starts depending on something new.
    """

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.compose = MagicMock()
        self.workers_docker = MagicMock()
        self.running = False
        self.workers_running = False


def ok(*lines: str) -> SubprocessOutput:
    return SubprocessOutput(stdout=list(lines), stderr=[], combined=list(lines), exit_code=0)


def fail(code: int = 1, *lines: str) -> SubprocessOutput:
    return SubprocessOutput(stdout=[], stderr=list(lines), combined=list(lines), exit_code=code)


def load_yaml(path: Path):
    yaml = YAML(typ="rt")
    return yaml.load(path.read_text())


def printed(migration) -> list[str]:
    return [c.args[0] for c in migration.output.print.call_args_list if c.args]


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
    # Pin the stable-release branch of _get_image_tag_for_migration so image
    # assertions do not depend on the version of the checkout under test.
    m.is_dev_environment = False
    m.effective_image_tag = "vX.dev"
    m._images_updated = False
    return m


@pytest.fixture
def bench(tmp_path):
    """A fake bench tree at ``<tmp>/sites/test-bench``, deliberately empty."""
    path = tmp_path / "sites" / "test-bench"
    (path / "workspace" / "frappe-bench" / "sites").mkdir(parents=True)
    (path / "configs" / "nginx" / "conf" / "conf.d").mkdir(parents=True)
    return FakeBench("test-bench", path)


# ===========================================================================
# bench_basic_backup -- the extra backups this migration adds on top of parent
# ===========================================================================


class TestBenchBasicBackupExtras:
    def _prepare(self, bench):
        cfg = bench.path / "workspace" / "frappe-bench" / "config"
        cfg.mkdir(parents=True)
        (cfg / "supervisor.conf").write_text("x")
        (cfg / "web.fm.supervisor.conf").write_text("x")
        (cfg / "worker.fm.supervisor.conf").write_text("x")
        (cfg / "unrelated.conf").write_text("x")
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        default_conf.write_text("server {}")
        return cfg, default_conf

    def test_backs_up_supervisor_glob_and_nginx_default_conf(self, migration, bench):
        self._prepare(bench)
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup"):
            migration.bench_basic_backup(bench)

        names = sorted(c.args[0].name for c in migration.backup_manager.backup.call_args_list)
        assert names == [
            "default.conf",
            "supervisor.conf",
            "web.fm.supervisor.conf",
            "worker.fm.supervisor.conf",
        ], "only supervisor.conf, *.fm.supervisor.conf and nginx default.conf are added"
        for call in migration.backup_manager.backup.call_args_list:
            assert call.kwargs["bench_name"] == "test-bench"

    def test_skip_backup_flag_suppresses_only_the_extra_backups(self, migration, bench):
        self._prepare(bench)
        migration.migration_executor.skip_backup = True
        parent = MagicMock()
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup", parent):
            migration.bench_basic_backup(bench)

        parent.assert_called_once()  # parent still runs; only the extras are skipped
        migration.backup_manager.backup.assert_not_called()

    def test_skip_backup_for_this_bench_suppresses_the_extra_backups(self, migration, bench):
        self._prepare(bench)
        migration.migration_executor.skip_backup_for = ["test-bench"]
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup"):
            migration.bench_basic_backup(bench)
        migration.backup_manager.backup.assert_not_called()

    def test_skip_backup_for_another_bench_does_not_suppress(self, migration, bench):
        self._prepare(bench)
        migration.migration_executor.skip_backup_for = ["other-bench"]
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup"):
            migration.bench_basic_backup(bench)
        assert migration.backup_manager.backup.call_count == 4

    def test_missing_config_dir_and_missing_default_conf_are_no_ops(self, migration, bench):
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup"):
            migration.bench_basic_backup(bench)
        migration.backup_manager.backup.assert_not_called()

    def test_supervisor_conf_absent_but_fm_confs_present(self, migration, bench):
        cfg = bench.path / "workspace" / "frappe-bench" / "config"
        cfg.mkdir(parents=True)
        (cfg / "web.fm.supervisor.conf").write_text("x")
        with patch.object(MigrationV0190.__mro__[1], "bench_basic_backup"):
            migration.bench_basic_backup(bench)
        names = [c.args[0].name for c in migration.backup_manager.backup.call_args_list]
        assert names == ["web.fm.supervisor.conf"]


# ===========================================================================
# env/ backup + rollback
# ===========================================================================


class TestEnvBackupAndUndo:
    def test_env_moved_aside_not_copied(self, migration, bench):
        env = bench.path / "workspace" / "frappe-bench" / "env"
        (env / "bin").mkdir(parents=True)
        (env / "bin" / "python").write_text("#!/bin/sh")

        migration._backup_env_for_rollback(bench)

        assert not env.exists(), "env/ is MOVED, so the original must be gone"
        moved = bench.path / "workspace" / "frappe-bench" / "env.backup.migration"
        assert (moved / "bin" / "python").read_text() == "#!/bin/sh"

    @pytest.mark.parametrize("kind", ["missing", "file"])
    def test_env_backup_requires_a_real_directory(self, migration, bench, kind):
        fb = bench.path / "workspace" / "frappe-bench"
        if kind == "file":
            (fb / "env").write_text("not a venv")

        migration._backup_env_for_rollback(bench)

        assert not (fb / "env.backup.migration").exists()
        migration.output.print.assert_not_called()

    def test_stale_backup_from_a_prior_attempt_is_replaced_not_merged(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / "env").mkdir(parents=True)
        (fb / "env" / "CURRENT").write_text("current")
        (fb / "env.backup.migration").mkdir(parents=True)
        (fb / "env.backup.migration" / "STALE").write_text("stale")

        migration._backup_env_for_rollback(bench)

        backup = fb / "env.backup.migration"
        assert (backup / "CURRENT").exists()
        assert not (backup / "STALE").exists(), "a leftover backup must be wiped first"

    def test_undo_removes_new_env_before_restoring_backup(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / "env").mkdir(parents=True)
        (fb / "env" / "NEW").write_text("new")
        (fb / "env.backup.migration").mkdir(parents=True)
        (fb / "env.backup.migration" / "OLD").write_text("old")

        migration.undo_bench_migrate(bench)

        assert (fb / "env" / "OLD").read_text() == "old"
        assert not (fb / "env" / "NEW").exists(), "the rebuilt env/ must be destroyed, not merged"
        assert not (fb / "env.backup.migration").exists()

    def test_undo_without_backup_leaves_env_untouched(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / "env").mkdir(parents=True)
        (fb / "env" / "KEEP").write_text("keep")

        migration.undo_bench_migrate(bench)

        assert (fb / "env" / "KEEP").read_text() == "keep"

    def test_undo_moves_backup_into_place_when_env_is_already_gone(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / "env.backup.migration").mkdir(parents=True)
        (fb / "env.backup.migration" / "OLD").write_text("old")

        migration.undo_bench_migrate(bench)

        assert (fb / "env" / "OLD").read_text() == "old"
        assert "Removing new env/" not in printed(migration), "nothing to remove when env/ is absent"

    def test_undo_restores_first_bashrc_backup_only(self, migration, bench):
        first = Mock(src=Path("/w/.bashrc"))
        second = Mock(src=Path("/other/.bashrc"))
        unrelated = Mock(src=Path("/w/bench_config.toml"))
        migration.backup_manager.backups = [unrelated, first, second]

        migration.undo_bench_migrate(bench)

        migration.backup_manager.restore.assert_called_once_with(first, force=True)

    def test_undo_tolerates_absent_backup_manager(self, migration, bench):
        del migration.backup_manager
        migration.undo_bench_migrate(bench)  # must not raise

    def test_undo_restores_nginx_default_conf_from_migration_bak(self, migration, bench):
        confd = bench.path / "configs" / "nginx" / "conf" / "conf.d"
        (confd / "default.conf").write_text("regenerated")
        (confd / "default.conf.migration.bak").write_text("original")

        migration.undo_bench_migrate(bench)

        assert (confd / "default.conf").read_text() == "original"
        assert not (confd / "default.conf.migration.bak").exists()

    def test_undo_moves_bak_into_place_when_default_conf_absent(self, migration, bench):
        confd = bench.path / "configs" / "nginx" / "conf" / "conf.d"
        (confd / "default.conf.migration.bak").write_text("original")

        migration.undo_bench_migrate(bench)

        assert (confd / "default.conf").read_text() == "original"


# ===========================================================================
# migrate_bench -- ordering and guards
# ===========================================================================


class TestMigrateBenchOrchestration:
    def _instrument(self, migration, order):
        def rec(name, ret=None):
            def f(*a, **k):
                order.append(name)
                return ret

            return f

        for name in (
            "_migrate_bench_config_toml",
            "_migrate_docker_compose_yml",
            "_migrate_workers_compose_yml",
            "_pull_bench_images",
            "_cleanup_admin_tools_nginx_config",
            "_write_upload_limit_vhostd",
            "_write_upload_limit_site_config",
            "_write_upload_limit_nginx_conf",
            "_rebuild_runtime_environment",
        ):
            setattr(migration, name, rec(name))
        migration._resolve_upload_limit = rec("_resolve_upload_limit", "128M")

    def test_full_order_of_side_effects(self, migration, bench):
        (bench.path / "bench_config.toml").write_text("")
        (bench.path / "docker-compose.yml").write_text("")
        (bench.path / "docker-compose.workers.yml").write_text("")
        order: list[str] = []
        self._instrument(migration, order)

        # Only a compose migrator can enable the pull: migrate_bench resets the
        # flag at entry, so the pull is driven by an actual image-tag rewrite.
        def compose_migrator(*a, **k):
            order.append("_migrate_docker_compose_yml")
            migration._images_updated = True

        migration._migrate_docker_compose_yml = compose_migrator

        migration.migrate_bench(bench)

        assert order == [
            "_migrate_bench_config_toml",
            "_migrate_docker_compose_yml",
            "_migrate_workers_compose_yml",
            "_pull_bench_images",
            "_cleanup_admin_tools_nginx_config",
            "_resolve_upload_limit",
            "_write_upload_limit_vhostd",
            "_write_upload_limit_site_config",
            "_write_upload_limit_nginx_conf",
            "_rebuild_runtime_environment",
        ]

    def test_images_updated_flag_is_reset_at_entry_and_gates_the_pull(self, migration, bench):
        order: list[str] = []
        self._instrument(migration, order)
        migration._images_updated = True  # stale value from a previous bench

        migration.migrate_bench(bench)

        assert "_pull_bench_images" not in order, "_images_updated must be reset to False at entry"

    def test_missing_files_skip_their_migration_steps(self, migration, bench):
        (bench.path / "docker-compose.yml").write_text("")
        order: list[str] = []
        self._instrument(migration, order)

        migration.migrate_bench(bench)

        assert "_migrate_bench_config_toml" not in order
        assert "_migrate_docker_compose_yml" in order
        assert "_migrate_workers_compose_yml" not in order

    def test_runtime_rebuild_runs_outside_the_spinner(self, migration, bench):
        order: list[str] = []
        self._instrument(migration, order)
        migration.output.stop.side_effect = lambda *a, **k: order.append("spinner-stop")

        migration.migrate_bench(bench)

        assert order.index("spinner-stop") < order.index("_rebuild_runtime_environment")

    def test_success_message_names_bench_and_version(self, migration, bench):
        self._instrument(migration, [])
        migration.migrate_bench(bench)
        assert "Successfully migrated test-bench to v0.19.0" in printed(migration)

    def test_upload_limit_is_resolved_once_and_shared_by_all_three_writers(self, migration, bench):
        seen = []
        migration._resolve_upload_limit = Mock(return_value="256M")
        for name in (
            "_write_upload_limit_vhostd",
            "_write_upload_limit_site_config",
            "_write_upload_limit_nginx_conf",
        ):
            setattr(migration, name, lambda _b, limit, _n=name: seen.append((_n, limit)))
        migration._cleanup_admin_tools_nginx_config = Mock()
        migration._rebuild_runtime_environment = Mock()

        migration.migrate_bench(bench)

        migration._resolve_upload_limit.assert_called_once_with(bench)
        assert [limit for _, limit in seen] == ["256M", "256M", "256M"]


# ===========================================================================
# bench_config.toml: [ssl] -> [[ssl_certificates]]
# ===========================================================================


class TestTransformSslConfig:
    def test_no_ssl_table_is_a_no_op(self, migration):
        doc = tomlkit.parse('name = "x"\n')
        migration._transform_ssl_config(doc, "site.local")
        assert "ssl_certificates" not in doc

    def test_already_migrated_array_is_left_alone(self, migration):
        doc = tomlkit.parse('[[ssl]]\ndomain = "a"\n')
        migration._transform_ssl_config(doc, "site.local")
        assert "ssl" in doc, "an [[ssl]] array is treated as already-migrated and kept verbatim"
        assert "ssl_certificates" not in doc

    def test_full_transform_shape_and_defaults(self, migration):
        doc = tomlkit.parse("[ssl]\n")
        migration._transform_ssl_config(doc, "site.local")

        assert "ssl" not in doc
        assert doc["ssl_certificates"] == [
            {
                "domain": "site.local",
                "ssl_type": "letsencrypt",
                "acme_client": "acme.sh",
                "hsts": "off",
                "challenge_type": "http01",
            }
        ]

    def test_domain_comes_from_bench_name_not_from_the_old_table(self, migration):
        doc = tomlkit.parse('[ssl]\ndomain = "ignored.example"\n')
        migration._transform_ssl_config(doc, "real.local")
        assert doc["ssl_certificates"][0]["domain"] == "real.local"

    def test_acme_client_is_forced_to_acme_sh(self, migration):
        doc = tomlkit.parse('[ssl]\nacme_client = "certbot"\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["ssl_certificates"][0]["acme_client"] == "acme.sh"

    def test_preferred_challenge_is_renamed_to_challenge_type(self, migration):
        doc = tomlkit.parse('[ssl]\npreferred_challenge = "dns01"\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["ssl_certificates"][0]["challenge_type"] == "dns01"

    def test_preferred_challenge_wins_over_challenge_type(self, migration):
        doc = tomlkit.parse('[ssl]\npreferred_challenge = "dns01"\nchallenge_type = "http01"\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["ssl_certificates"][0]["challenge_type"] == "dns01"

    def test_existing_challenge_type_is_preserved_when_no_preferred_challenge(self, migration):
        doc = tomlkit.parse('[ssl]\nchallenge_type = "dns01"\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["ssl_certificates"][0]["challenge_type"] == "dns01"

    def test_empty_preferred_challenge_falls_through_to_default(self, migration):
        doc = tomlkit.parse('[ssl]\npreferred_challenge = ""\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["ssl_certificates"][0]["challenge_type"] == "http01"

    def test_ssl_type_and_hsts_are_carried_over(self, migration):
        doc = tomlkit.parse('[ssl]\nssl_type = "self_signed"\nhsts = "on"\n')
        migration._transform_ssl_config(doc, "s")
        cert = doc["ssl_certificates"][0]
        assert cert["ssl_type"] == "self_signed"
        assert cert["hsts"] == "on"

    def test_unknown_ssl_keys_are_dropped(self, migration):
        doc = tomlkit.parse('[ssl]\nemail = "a@b.c"\n')
        migration._transform_ssl_config(doc, "s")
        assert "email" not in doc["ssl_certificates"][0], "keys outside the fixed five are discarded"


class TestMoveDnsCredentials:
    def test_no_credentials_means_no_dns_providers_table(self, migration):
        doc = tomlkit.parse("")
        migration._move_dns_credentials(doc, {})
        assert "dns_providers" not in doc
        migration.output.print.assert_not_called()

    def test_api_token_only(self, migration):
        doc = tomlkit.parse("")
        migration._move_dns_credentials(doc, {"api_token": "tok"})
        assert doc["dns_providers"]["cloudflare"] == {"api_token": "tok"}
        assert "Migrated DNS credentials to dns_providers.cloudflare" in printed(migration)

    def test_api_key_only(self, migration):
        doc = tomlkit.parse("")
        migration._move_dns_credentials(doc, {"api_key": "k"})
        assert doc["dns_providers"]["cloudflare"] == {"api_key": "k"}

    def test_both_credentials_keep_token_first(self, migration):
        doc = tomlkit.parse("")
        migration._move_dns_credentials(doc, {"api_token": "tok", "api_key": "k"})
        assert list(doc["dns_providers"]["cloudflare"].keys()) == ["api_token", "api_key"]

    def test_credentials_are_moved_during_ssl_transform_and_removed_with_ssl(self, migration):
        doc = tomlkit.parse('[ssl]\napi_token = "tok"\n')
        migration._transform_ssl_config(doc, "s")
        assert doc["dns_providers"]["cloudflare"]["api_token"] == "tok"
        assert "ssl" not in doc
        assert "api_token" not in doc["ssl_certificates"][0]


class TestAddNewConfigFields:
    def test_all_four_defaults_added(self, migration):
        doc = tomlkit.parse("")
        migration._add_new_config_fields(doc)
        assert doc["alias_domains"] == []
        assert doc["upload_limit"] == "50M"
        assert doc["restart_policy"] == "unless-stopped"
        assert doc["use_uv"] is True

    def test_restart_policy_default_depends_on_environment_type(self, migration):
        prod = tomlkit.parse('environment_type = "prod"\n')
        dev = tomlkit.parse('environment_type = "dev"\n')
        migration._add_new_config_fields(prod)
        migration._add_new_config_fields(dev)
        assert prod["restart_policy"] == "unless-stopped"
        assert dev["restart_policy"] == "no", "non-prod benches must not auto-restart"

    def test_missing_environment_type_is_treated_as_prod(self, migration):
        doc = tomlkit.parse("")
        migration._add_new_config_fields(doc)
        assert doc["restart_policy"] == "unless-stopped"

    def test_existing_values_are_never_overwritten(self, migration):
        doc = tomlkit.parse(
            'alias_domains = ["a.test"]\nupload_limit = "1G"\nrestart_policy = "always"\nuse_uv = false\n'
        )
        migration._add_new_config_fields(doc)
        assert doc["alias_domains"] == ["a.test"]
        assert doc["upload_limit"] == "1G"
        assert doc["restart_policy"] == "always"
        assert doc["use_uv"] is False


class TestMigrateBenchConfigToml:
    def test_writes_transformed_document_and_preserves_comments(self, migration, bench):
        path = bench.path / "bench_config.toml"
        path.write_text('# keep me\nname = "test-bench"\n\n[ssl]\npreferred_challenge = "dns01"\n')

        migration._migrate_bench_config_toml(bench, path)

        text = path.read_text()
        assert "# keep me" in text, "tomlkit round-trip must preserve comments"
        doc = tomlkit.parse(text)
        assert doc["ssl_certificates"][0]["challenge_type"] == "dns01"
        assert doc["use_uv"] is True
        msgs = printed(migration)
        assert msgs[0] == "Migrating bench_config.toml"
        assert msgs[-1] == "Updated SSL configuration format"

    def test_config_without_ssl_still_gains_the_new_fields(self, migration, bench):
        path = bench.path / "bench_config.toml"
        path.write_text('name = "test-bench"\n')

        migration._migrate_bench_config_toml(bench, path)

        doc = tomlkit.parse(path.read_text())
        assert "ssl_certificates" not in doc
        assert doc["upload_limit"] == "50M"


# ===========================================================================
# image tag rewriting
# ===========================================================================


class TestUpdateServiceImages:
    def test_rewrites_only_fm_images_and_flags_the_change(self, migration):
        services = {
            "frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0"},
            "nginx": {"image": "ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0"},
            "mailpit": {"image": "axllent/mailpit:v1.13"},
        }
        migration._update_service_images(services)

        assert services["frappe"]["image"] == "ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0"
        assert services["nginx"]["image"] == "ghcr.io/rtcamp/frappe-manager-nginx:v0.19.0"
        assert services["mailpit"]["image"] == "axllent/mailpit:v1.13", "third-party images are untouched"
        assert migration._images_updated is True

    def test_no_change_leaves_the_pull_flag_false(self, migration):
        services = {"frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0"}}
        migration._update_service_images(services)
        assert migration._images_updated is False, "a needless docker pull must not be triggered"
        migration.output.print.assert_not_called()

    def test_dev_release_tag_is_rewritten_too(self, migration):
        services = {"frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0.dev3"}}
        migration._update_service_images(services)
        assert services["frappe"]["image"] == "ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0"

    def test_dev_environment_uses_effective_image_tag_instead_of_version(self, migration):
        migration.is_dev_environment = True
        migration.effective_image_tag = "v0.19.0.dev9"
        services = {"frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0"}}
        migration._update_service_images(services)
        assert services["frappe"]["image"] == "ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0.dev9"

    def test_service_without_image_key_is_skipped(self, migration):
        services = {"only-build": {"build": {"context": "."}}}
        migration._update_service_images(services)
        assert services == {"only-build": {"build": {"context": "."}}}
        assert migration._images_updated is False

    def test_non_semver_tag_is_not_rewritten(self, migration):
        services = {"frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:latest"}}
        migration._update_service_images(services)
        assert services["frappe"]["image"] == "ghcr.io/rtcamp/frappe-manager-frappe:latest"
        assert migration._images_updated is False

    def test_message_reports_old_and_new_tag(self, migration):
        migration._update_service_images({"frappe": {"image": "ghcr.io/rtcamp/frappe-manager-frappe:v0.18.2"}})
        assert "Updated frappe image: v0.18.2 → v0.19.0" in printed(migration)


# ===========================================================================
# nginx SITENAME -> SITE_MAPPINGS
# ===========================================================================


class TestTransformNginxEnvironment:
    def test_dict_form_converts_sitename_to_json_mapping(self, migration):
        services = {"nginx": {"environment": {"SITENAME": "site.local"}}}
        migration._transform_nginx_environment(services, "50M")

        env = services["nginx"]["environment"]
        assert "SITENAME" not in env
        assert json.loads(env["SITE_MAPPINGS"]) == {"site.local": "site.local"}
        assert env["HTTPS_METHOD"] == "noredirect"
        assert env["CLIENT_MAX_BODY_SIZE"] == "50m", "nginx requires a lowercase size suffix"

    def test_dict_form_without_sitename_still_gains_the_new_keys(self, migration):
        services = {"nginx": {"environment": {"VIRTUAL_HOST": "site.local"}}}
        migration._transform_nginx_environment(services, "1G")
        env = services["nginx"]["environment"]
        assert "SITE_MAPPINGS" not in env
        assert env["HTTPS_METHOD"] == "noredirect"
        assert env["CLIENT_MAX_BODY_SIZE"] == "1g"

    def test_dict_form_never_overwrites_existing_values(self, migration):
        services = {
            "nginx": {"environment": {"HTTPS_METHOD": "redirect", "CLIENT_MAX_BODY_SIZE": "500m"}},
        }
        migration._transform_nginx_environment(services, "50M")
        env = services["nginx"]["environment"]
        assert env["HTTPS_METHOD"] == "redirect"
        assert env["CLIENT_MAX_BODY_SIZE"] == "500m"

    def test_list_form_replaces_the_entry_in_place_and_appends_the_rest(self, migration):
        services = {"nginx": {"environment": ["VIRTUAL_HOST=site.local", "SITENAME=site.local"]}}
        migration._transform_nginx_environment(services, "50M")

        assert services["nginx"]["environment"] == [
            "VIRTUAL_HOST=site.local",
            'SITE_MAPPINGS={"site.local": "site.local"}',
            "HTTPS_METHOD=noredirect",
            "CLIENT_MAX_BODY_SIZE=50m",
        ]

    def test_list_form_respects_existing_keys(self, migration):
        services = {"nginx": {"environment": ["HTTPS_METHOD=redirect", "CLIENT_MAX_BODY_SIZE=9m"]}}
        migration._transform_nginx_environment(services, "50M")
        assert services["nginx"]["environment"] == ["HTTPS_METHOD=redirect", "CLIENT_MAX_BODY_SIZE=9m"]

    def test_list_form_keeps_non_string_entries(self, migration):
        services = {"nginx": {"environment": [None, "SITENAME=s"]}}
        migration._transform_nginx_environment(services, "50M")
        assert services["nginx"]["environment"][0] is None

    def test_missing_nginx_service_is_a_no_op(self, migration):
        services = {"frappe": {"environment": {"SITENAME": "s"}}}
        migration._transform_nginx_environment(services, "50M")
        assert services["frappe"]["environment"] == {"SITENAME": "s"}, "only the nginx service is rewritten"

    def test_nginx_without_environment_is_a_no_op(self, migration):
        services = {"nginx": {"image": "x"}}
        migration._transform_nginx_environment(services, "50M")
        assert services["nginx"] == {"image": "x"}

    def test_unsupported_environment_type_is_ignored(self, migration):
        services = {"nginx": {"environment": "SITENAME=s"}}
        migration._transform_nginx_environment(services, "50M")
        assert services["nginx"]["environment"] == "SITENAME=s", "a scalar env block is left alone"


class TestAddRestartPolicyToServices:
    def test_restart_is_added_double_quoted(self, migration):
        services = {"frappe": {}, "nginx": {}}
        migration._add_restart_policy_to_services(services, "no")

        for svc in services.values():
            assert svc["restart"] == "no"
            assert isinstance(svc["restart"], DoubleQuotedScalarString), (
                "'no' must be quoted or YAML 1.1 turns it into the boolean False"
            )

    def test_existing_restart_is_preserved(self, migration):
        services = {"frappe": {"restart": "always"}}
        migration._add_restart_policy_to_services(services, "unless-stopped")
        assert services["frappe"]["restart"] == "always"

    def test_quoting_survives_a_yaml_round_trip(self, migration, tmp_path):
        yaml = YAML(typ="rt")
        services = {"frappe": {}}
        migration._add_restart_policy_to_services(services, "no")
        path = tmp_path / "c.yml"
        with path.open("w") as f:
            yaml.dump({"services": services}, f)
        assert 'restart: "no"' in path.read_text()
        assert load_yaml(path)["services"]["frappe"]["restart"] == "no"


# ===========================================================================
# The 2 duplicated YAML-loader blocks (lines ~241 and ~507)
# ===========================================================================


class TestDuplicatedComposeYamlBlocks:
    """Pins what the two compose migrators share AND how they differ."""

    MAIN = """\
x-version: 0.18.0
services:
  frappe:
    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0
  nginx:
    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0
    environment:
      SITENAME: test-bench
"""

    WORKERS = """\
x-version: 0.18.0
services:
  frappe-default-worker:
    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0
    environment:
      SITENAME: test-bench
"""

    def test_main_compose_full_effect(self, migration, bench):
        path = bench.path / "docker-compose.yml"
        path.write_text(self.MAIN)
        (bench.path / "bench_config.toml").write_text('restart_policy = "always"\nupload_limit = "200M"\n')

        migration._migrate_docker_compose_yml(bench, path)

        data = load_yaml(path)
        assert data["x-version"] == "0.19.0", "x-version is plain semver, no 'v' prefix"
        assert data["services"]["frappe"]["image"].endswith(":v0.19.0")
        assert data["services"]["frappe"]["restart"] == "always"
        env = data["services"]["nginx"]["environment"]
        assert json.loads(env["SITE_MAPPINGS"]) == {"test-bench": "test-bench"}
        assert env["CLIENT_MAX_BODY_SIZE"] == "200m", "compose env must agree with upload-limit.conf"

    def test_workers_compose_does_not_touch_nginx_environment(self, migration, bench):
        """DIFFERENCE: the workers migrator has no nginx/upload-limit handling."""
        path = bench.path / "docker-compose.workers.yml"
        path.write_text(self.WORKERS)

        migration._migrate_workers_compose_yml(bench, path)

        env = load_yaml(path)["services"]["frappe-default-worker"]["environment"]
        assert env == {"SITENAME": "test-bench"}, "workers keep SITENAME verbatim"

    def test_workers_compose_never_resolves_the_upload_limit(self, migration, bench):
        path = bench.path / "docker-compose.workers.yml"
        path.write_text(self.WORKERS)
        migration._resolve_upload_limit = Mock(return_value="50M")
        migration._transform_nginx_environment = Mock()

        migration._migrate_workers_compose_yml(bench, path)

        migration._resolve_upload_limit.assert_not_called()
        migration._transform_nginx_environment.assert_not_called()

    def test_main_compose_resolves_upload_limit_exactly_once(self, migration, bench):
        path = bench.path / "docker-compose.yml"
        path.write_text(self.MAIN)
        migration._resolve_upload_limit = Mock(return_value="50M")

        migration._migrate_docker_compose_yml(bench, path)

        migration._resolve_upload_limit.assert_called_once_with(bench)

    def test_workers_compose_shares_image_and_restart_and_x_version_behaviour(self, migration, bench):
        path = bench.path / "docker-compose.workers.yml"
        path.write_text(self.WORKERS)
        (bench.path / "bench_config.toml").write_text('restart_policy = "no"\n')

        migration._migrate_workers_compose_yml(bench, path)

        data = load_yaml(path)
        assert data["x-version"] == "0.19.0"
        assert data["services"]["frappe-default-worker"]["image"].endswith(":v0.19.0")
        assert data["services"]["frappe-default-worker"]["restart"] == "no"
        assert 'restart: "no"' in path.read_text()

    @pytest.mark.parametrize("method", ["_migrate_docker_compose_yml", "_migrate_workers_compose_yml"])
    @pytest.mark.parametrize("body", ["", "x-version: 0.18.0\n", "{}\n"])
    def test_both_bail_out_without_writing_when_services_missing(self, migration, bench, method, body):
        """Shared guard: no ``services`` key => file is left byte-for-byte alone."""
        path = bench.path / "c.yml"
        path.write_text(body)

        getattr(migration, method)(bench, path)

        assert path.read_text() == body, "x-version must NOT be bumped when there are no services"

    @pytest.mark.parametrize(
        ("method", "message"),
        [
            ("_migrate_docker_compose_yml", "Migrating docker-compose.yml"),
            ("_migrate_workers_compose_yml", "Migrating docker-compose.workers.yml"),
        ],
    )
    def test_each_announces_its_own_file(self, migration, bench, method, message):
        path = bench.path / "c.yml"
        path.write_text("services: {}\n")
        getattr(migration, method)(bench, path)
        assert printed(migration)[0] == message

    @pytest.mark.parametrize("method", ["_migrate_docker_compose_yml", "_migrate_workers_compose_yml"])
    def test_both_default_restart_policy_to_unless_stopped_without_bench_config(self, migration, bench, method):
        path = bench.path / "c.yml"
        path.write_text("services:\n  frappe:\n    image: x\n")

        getattr(migration, method)(bench, path)

        assert load_yaml(path)["services"]["frappe"]["restart"] == "unless-stopped"

    @pytest.mark.parametrize("method", ["_migrate_docker_compose_yml", "_migrate_workers_compose_yml"])
    def test_both_preserve_quote_style_of_untouched_scalars(self, migration, bench, method):
        path = bench.path / "c.yml"
        path.write_text("services:\n  frappe:\n    container_name: 'quoted-name'\n")

        getattr(migration, method)(bench, path)

        assert "'quoted-name'" in path.read_text(), "preserve_quotes=True must be kept by any refactor"


# ===========================================================================
# upload limit resolution + the three writers
# ===========================================================================


class TestResolveUploadLimit:
    def _site_config(self, bench, payload):
        p = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return p

    def test_default_when_nothing_configured(self, migration, bench):
        assert migration._resolve_upload_limit(bench) == "50M"
        assert "Using default upload_limit: 50M" in printed(migration)

    def test_bench_config_is_used_when_site_config_has_no_max_file_size(self, migration, bench):
        (bench.path / "bench_config.toml").write_text('upload_limit = "300M"\n')
        self._site_config(bench, {"db_host": "mariadb"})
        assert migration._resolve_upload_limit(bench) == "300M"

    def test_site_config_max_file_size_beats_bench_config(self, migration, bench):
        (bench.path / "bench_config.toml").write_text('upload_limit = "300M"\n')
        self._site_config(bench, {"max_file_size": 50 * 1024 * 1024})
        assert migration._resolve_upload_limit(bench) == "50M", "an operator's site_config wins"

    @pytest.mark.parametrize(
        ("size_bytes", "expected"),
        [
            (2 * 1024**3, "2G"),
            (1024**3, "1G"),
            (50 * 1024**2, "50M"),
            (1024**2, "1M"),
            (1536 * 1024**2, "1536M"),  # 1.5G is not a whole G => reported in M
            (1_000_000, "1M"),  # below 1MiB => rounded to nearest MB
            (100, "0M"),  # rounds down to zero
        ],
    )
    def test_byte_to_human_conversion(self, migration, bench, size_bytes, expected):
        self._site_config(bench, {"max_file_size": size_bytes})
        assert migration._resolve_upload_limit(bench) == expected

    def test_zero_max_file_size_is_falsy_and_ignored(self, migration, bench):
        self._site_config(bench, {"max_file_size": 0})
        (bench.path / "bench_config.toml").write_text('upload_limit = "77M"\n')
        assert migration._resolve_upload_limit(bench) == "77M"

    def test_unreadable_site_config_falls_through_silently(self, migration, bench):
        self._site_config(bench, "{not json")
        (bench.path / "bench_config.toml").write_text('upload_limit = "77M"\n')
        assert migration._resolve_upload_limit(bench) == "77M"

    def test_string_max_file_size_falls_through_via_typeerror(self, migration, bench):
        """SUSPICION pinned: a string max_file_size is swallowed by ``except Exception``."""
        self._site_config(bench, {"max_file_size": "50M"})
        assert migration._resolve_upload_limit(bench) == "50M"
        assert "Using default upload_limit: 50M" in printed(migration)

    def test_empty_bench_config_upload_limit_falls_through_to_default(self, migration, bench):
        (bench.path / "bench_config.toml").write_text('upload_limit = ""\n')
        assert migration._resolve_upload_limit(bench) == "50M"


class TestWriteUploadLimitVhostd:
    def _vhostd(self, bench):
        d = bench.path.parent.parent / "services" / "nginx-proxy" / "vhostd"
        d.mkdir(parents=True)
        return d

    def test_missing_vhostd_dir_warns_and_writes_nothing(self, migration, bench):
        migration._write_upload_limit_vhostd(bench, "50M")
        assert any("nginx-proxy vhostd directory not found" in m for m in printed(migration))
        migration.backup_manager.backup.assert_not_called()

    def test_writes_lowercase_directive_for_the_bench_domain(self, migration, bench):
        vhostd = self._vhostd(bench)
        migration._write_upload_limit_vhostd(bench, "200M")
        assert "client_max_body_size 200m;" in (vhostd / "test-bench").read_text()
        assert "Set upload limit (200M) for 1 domain(s)" in printed(migration)

    def test_alias_domains_are_included(self, migration, bench):
        vhostd = self._vhostd(bench)
        (bench.path / "bench_config.toml").write_text('alias_domains = ["a.test", "b.test"]\n')

        migration._write_upload_limit_vhostd(bench, "50M")

        assert (vhostd / "a.test").exists()
        assert (vhostd / "b.test").exists()
        assert "Set upload limit (50M) for 3 domain(s)" in printed(migration)

    def test_existing_vhost_files_are_backed_up_before_modification(self, migration, bench):
        vhostd = self._vhostd(bench)
        (vhostd / "test-bench").write_text("client_max_body_size 10m;\n")
        (bench.path / "bench_config.toml").write_text('alias_domains = ["fresh.test"]\n')

        migration._write_upload_limit_vhostd(bench, "50M")

        backed = [c.args[0] for c in migration.backup_manager.backup.call_args_list]
        assert backed == [vhostd / "test-bench"], "only pre-existing vhost files are backed up"
        assert migration.backup_manager.backup.call_args_list[0].kwargs["bench_name"] == "test-bench"

    def test_existing_directive_is_replaced_not_duplicated(self, migration, bench):
        vhostd = self._vhostd(bench)
        (vhostd / "test-bench").write_text("client_max_body_size 10m;\n")

        migration._write_upload_limit_vhostd(bench, "50M")

        content = (vhostd / "test-bench").read_text()
        assert content.count("client_max_body_size") == 1
        assert "50m" in content

    def test_empty_alias_domains_list_adds_nothing(self, migration, bench):
        self._vhostd(bench)
        (bench.path / "bench_config.toml").write_text("alias_domains = []\n")
        migration._write_upload_limit_vhostd(bench, "50M")
        assert "Set upload limit (50M) for 1 domain(s)" in printed(migration)


class TestWriteUploadLimitSiteConfig:
    def _path(self, bench):
        return bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"

    def test_absent_file_is_a_no_op(self, migration, bench):
        migration._write_upload_limit_site_config(bench, "50M")
        migration.output.print.assert_not_called()

    def test_existing_max_file_size_is_respected(self, migration, bench):
        p = self._path(bench)
        p.write_text(json.dumps({"max_file_size": 123}))

        migration._write_upload_limit_site_config(bench, "50M")

        assert json.loads(p.read_text())["max_file_size"] == 123
        assert any("already set (123), skipping" in m for m in printed(migration))

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [("50M", 50 * 1024**2), ("1G", 1024**3), ("50m", 50 * 1024**2), ("2g", 2 * 1024**3)],
    )
    def test_size_parsing_is_case_insensitive(self, migration, bench, limit, expected):
        p = self._path(bench)
        p.write_text(json.dumps({"db_host": "mariadb"}))

        migration._write_upload_limit_site_config(bench, limit)

        data = json.loads(p.read_text())
        assert data["max_file_size"] == expected
        assert data["db_host"] == "mariadb", "unrelated keys survive"

    @pytest.mark.parametrize("limit", ["50", "1.5G", "50MB", "50K", ""])
    def test_unparseable_limit_writes_nothing(self, migration, bench, limit):
        p = self._path(bench)
        p.write_text(json.dumps({"db_host": "mariadb"}))

        migration._write_upload_limit_site_config(bench, limit)

        assert "max_file_size" not in json.loads(p.read_text())
        migration.output.print.assert_not_called()

    def test_output_is_indented_json(self, migration, bench):
        p = self._path(bench)
        p.write_text('{"db_host": "mariadb"}')
        migration._write_upload_limit_site_config(bench, "50M")
        assert "\n    " in p.read_text()

    def test_corrupt_json_warns_and_leaves_file_untouched(self, migration, bench):
        p = self._path(bench)
        p.write_text("{broken")

        migration._write_upload_limit_site_config(bench, "50M")

        assert p.read_text() == "{broken"
        assert "Warning: Could not update site_config.json max_file_size" in printed(migration)


class TestWriteUploadLimitNginxConf:
    def _conf(self, bench):
        return bench.path / "configs" / "nginx" / "conf" / "custom" / "upload-limit.conf"

    def test_creates_directory_and_file_and_tracks_it_as_new(self, migration, bench):
        migration._write_upload_limit_nginx_conf(bench, "200M")

        assert self._conf(bench).read_text() == "client_max_body_size 200m;\n"
        migration.backup_manager.track_new_file.assert_called_once_with(self._conf(bench))
        migration.backup_manager.backup.assert_not_called()
        assert "Created custom nginx upload-limit.conf" in printed(migration)

    def test_pre_existing_file_is_backed_up_and_not_tracked_as_new(self, migration, bench):
        conf = self._conf(bench)
        conf.parent.mkdir(parents=True)
        conf.write_text("client_max_body_size 10m;\n")

        migration._write_upload_limit_nginx_conf(bench, "50M")

        migration.backup_manager.backup.assert_called_once_with(conf, bench_name="test-bench")
        migration.backup_manager.track_new_file.assert_not_called()
        assert conf.read_text() == "client_max_body_size 50m;\n"

    def test_duplicate_directive_is_stripped_from_default_conf_after_backup(self, migration, bench):
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        default_conf.write_text("server {\n    client_max_body_size 10m;\n    listen 80;\n}\n")

        migration._write_upload_limit_nginx_conf(bench, "50M")

        assert "client_max_body_size" not in default_conf.read_text()
        assert "listen 80;" in default_conf.read_text()
        backed = [c.args[0] for c in migration.backup_manager.backup.call_args_list]
        assert default_conf in backed, "default.conf is backed up before being rewritten"
        assert "Removed duplicate client_max_body_size from default.conf" in printed(migration)

    def test_default_conf_without_the_directive_is_neither_backed_up_nor_rewritten(self, migration, bench):
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        original = "server {\n    listen 80;\n}\n"
        default_conf.write_text(original)

        migration._write_upload_limit_nginx_conf(bench, "50M")

        assert default_conf.read_text() == original
        migration.backup_manager.backup.assert_not_called()

    def test_absent_default_conf_is_tolerated(self, migration, bench):
        (bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf").unlink(missing_ok=True)
        migration._write_upload_limit_nginx_conf(bench, "50M")
        assert self._conf(bench).exists()

    def test_every_occurrence_of_the_directive_is_removed(self, migration, bench):
        default_conf = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        default_conf.write_text("client_max_body_size 1m;\nlisten 80;\nclient_max_body_size 2m;\n")

        migration._write_upload_limit_nginx_conf(bench, "50M")

        assert "client_max_body_size" not in default_conf.read_text()


# ===========================================================================
# admin tools cleanup / bench image pull
# ===========================================================================


class TestCleanupAdminToolsNginxConfig:
    def test_removes_both_stale_files(self, migration, bench):
        custom = bench.path / "configs" / "nginx" / "conf" / "custom"
        custom.mkdir(parents=True)
        conf = custom / "admin-tools.conf"
        conf.write_text("x")
        http_auth = bench.path / "configs" / "nginx" / "conf" / "http_auth"
        http_auth.mkdir(parents=True)
        htpasswd = http_auth / "test-bench-admin-tools.htpasswd"
        htpasswd.write_text("x")

        migration._cleanup_admin_tools_nginx_config(bench)

        assert not conf.exists()
        assert not htpasswd.exists()
        assert "Cleaned up stale admin-tools nginx config" in printed(migration)

    def test_htpasswd_removal_is_silent_and_independent(self, migration, bench):
        http_auth = bench.path / "configs" / "nginx" / "conf" / "http_auth"
        http_auth.mkdir(parents=True)
        (http_auth / "test-bench-admin-tools.htpasswd").write_text("x")

        migration._cleanup_admin_tools_nginx_config(bench)

        assert not (http_auth / "test-bench-admin-tools.htpasswd").exists()
        migration.output.print.assert_not_called()

    def test_htpasswd_name_is_derived_from_bench_name(self, migration, bench):
        http_auth = bench.path / "configs" / "nginx" / "conf" / "http_auth"
        http_auth.mkdir(parents=True)
        other = http_auth / "other-bench-admin-tools.htpasswd"
        other.write_text("x")

        migration._cleanup_admin_tools_nginx_config(bench)

        assert other.exists(), "another bench's htpasswd must never be deleted"

    def test_nothing_present_is_a_no_op(self, migration, bench):
        migration._cleanup_admin_tools_nginx_config(bench)
        migration.output.print.assert_not_called()


class TestPullBenchImages:
    def test_success_path_uses_non_streaming_pull(self, migration, bench):
        bench.compose.pull.return_value = ok()
        migration._pull_bench_images(bench)
        bench.compose.pull.assert_called_once_with(stream=False)
        assert "✓ Images ready" in printed(migration)

    def test_failure_raises_migration_exception_in_bench(self, migration, bench):
        bench.compose.pull.return_value = fail(1)
        with pytest.raises(MigrationExceptionInBench, match="Failed to pull images for test-bench"):
            migration._pull_bench_images(bench)

    def test_announcement_includes_the_resolved_tag(self, migration, bench):
        bench.compose.pull.return_value = ok()
        migration._pull_bench_images(bench)
        assert "Pulling updated images (v0.19.0)..." in printed(migration)


# ===========================================================================
# services-level migration
# ===========================================================================


class TestUpdateGlobalNginxProxyImage:
    def test_missing_services_compose_is_skipped(self, migration):
        cf = migration.services_manager.compose_file_manager
        cf.exists.return_value = False
        migration._update_global_nginx_proxy_image()
        cf.write_to_file.assert_not_called()

    @pytest.mark.parametrize(
        "yml",
        [
            {},
            {"services": None},
            {"services": {}},
            {"services": {"other": {"image": "x"}}},
            {"services": {"global-nginx-proxy": {}}},
        ],
    )
    def test_absent_or_imageless_nginx_service_is_skipped(self, migration, yml):
        cf = migration.services_manager.compose_file_manager
        cf.exists.return_value = True
        cf.yml = yml
        migration._update_global_nginx_proxy_image()
        cf.write_to_file.assert_not_called()

    def test_upgrades_the_pinned_tag_and_persists(self, migration):
        cf = migration.services_manager.compose_file_manager
        cf.exists.return_value = True
        cf.yml = {"services": {"global-nginx-proxy": {"image": "jwilder/nginx-proxy:1.6"}}}

        migration._update_global_nginx_proxy_image()

        assert cf.yml["services"]["global-nginx-proxy"]["image"] == "jwilder/nginx-proxy:1.11"
        cf.write_to_file.assert_called_once()
        assert "Updated global-nginx-proxy image: jwilder/nginx-proxy:1.6 → jwilder/nginx-proxy:1.11" in printed(
            migration
        )

    def test_already_correct_tag_is_not_rewritten(self, migration):
        cf = migration.services_manager.compose_file_manager
        cf.exists.return_value = True
        cf.yml = {"services": {"global-nginx-proxy": {"image": "jwilder/nginx-proxy:1.11"}}}

        migration._update_global_nginx_proxy_image()

        cf.write_to_file.assert_not_called()

    def test_any_other_image_is_forced_to_1_11(self, migration):
        cf = migration.services_manager.compose_file_manager
        cf.exists.return_value = True
        cf.yml = {"services": {"global-nginx-proxy": {"image": "nginxproxy/nginx-proxy:1.4"}}}

        migration._update_global_nginx_proxy_image()

        assert cf.yml["services"]["global-nginx-proxy"]["image"] == "jwilder/nginx-proxy:1.11"


class TestMigrateServices:
    def test_order_image_update_then_pull_then_recreate(self, migration):
        order: list[str] = []
        migration._update_global_nginx_proxy_image = lambda: order.append("update-image")
        migration.services_manager.compose.up.side_effect = lambda **k: order.append("up")

        with patch("frappe_manager.utils.site.pull_docker_images", side_effect=lambda: order.append("pull") or True):
            migration.migrate_services()

        assert order == ["update-image", "pull", "up"]
        migration.services_manager.compose.up.assert_called_once_with(
            services=["global-nginx-proxy"], force_recreate=True, detach=True
        )

    def test_pull_failure_raises_and_prevents_recreate(self, migration):
        migration._update_global_nginx_proxy_image = Mock()
        with (
            patch("frappe_manager.utils.site.pull_docker_images", return_value=False),
            pytest.raises(Exception, match="Failed to pull one or more Docker images"),
        ):
            migration.migrate_services()

        migration.output.display_error.assert_called_once_with("Failed to pull one or more Docker images")
        migration.services_manager.compose.up.assert_not_called()

    def test_undo_services_migrate_is_a_message_only(self, migration):
        migration.undo_services_migrate()
        assert "No services rollback needed for v0.19.0" in printed(migration)
        migration.services_manager.compose.up.assert_not_called()


# ===========================================================================
# runtime version resolution
# ===========================================================================


class TestResolveRuntimeVersions:
    def test_versions_from_config_short_circuit_auto_detection(self, migration, bench):
        path = bench.path / "bench_config.toml"
        path.write_text('python_version = "3.11.9"\nnode_version = "18.17.0"\n')
        migration._auto_detect_runtime_versions = Mock()

        py, node, doc = migration._resolve_runtime_versions(bench)

        assert (py, node) == ("3.11", "18"), "config values are normalised to runtime granularity"
        migration._auto_detect_runtime_versions.assert_not_called()
        assert doc is not None
        assert path.read_text() == 'python_version = "3.11.9"\nnode_version = "18.17.0"\n', "config untouched"

    @pytest.mark.parametrize(
        "body",
        ['python_version = "3.11"\n', 'node_version = "18"\n', ""],
    )
    def test_a_single_missing_version_triggers_auto_detection_for_both(self, migration, bench, body):
        (bench.path / "bench_config.toml").write_text(body)
        migration._auto_detect_runtime_versions = Mock(return_value=("3.12", "20"))

        py, node, _ = migration._resolve_runtime_versions(bench)

        migration._auto_detect_runtime_versions.assert_called_once_with(bench)
        assert (py, node) == ("3.12", "20")

    def test_auto_detected_versions_are_persisted_to_the_config(self, migration, bench):
        path = bench.path / "bench_config.toml"
        path.write_text('name = "test-bench"\n')
        migration._auto_detect_runtime_versions = Mock(return_value=("3.12", "20"))

        migration._resolve_runtime_versions(bench)

        doc = tomlkit.parse(path.read_text())
        assert doc["python_version"] == "3.12"
        assert doc["node_version"] == "20"
        assert doc["name"] == "test-bench"
        assert "Updated bench_config.toml with detected versions" in printed(migration)

    def test_nothing_is_persisted_when_detection_returns_nothing(self, migration, bench):
        path = bench.path / "bench_config.toml"
        path.write_text('name = "test-bench"\n')
        migration._auto_detect_runtime_versions = Mock(return_value=(None, None))

        py, node, _ = migration._resolve_runtime_versions(bench)

        assert (py, node) == (None, None)
        assert path.read_text() == 'name = "test-bench"\n'

    def test_no_config_file_means_no_document_and_no_write(self, migration, bench):
        migration._auto_detect_runtime_versions = Mock(return_value=("3.12", "20"))

        py, node, doc = migration._resolve_runtime_versions(bench)

        assert doc is None
        assert (py, node) == ("3.12", "20")
        assert not (bench.path / "bench_config.toml").exists(), "the migration never creates the config"

    @pytest.mark.parametrize(
        ("detected", "written", "absent"),
        [(("3.12", None), "python_version", "node_version"), ((None, "20"), "node_version", "python_version")],
    )
    def test_only_the_detected_half_is_persisted(self, migration, bench, detected, written, absent):
        path = bench.path / "bench_config.toml"
        path.write_text('name = "test-bench"\n')
        migration._auto_detect_runtime_versions = Mock(return_value=detected)

        migration._resolve_runtime_versions(bench)

        doc = tomlkit.parse(path.read_text())
        assert written in doc
        assert absent not in doc, "a half-detection must not invent the other version"

    def test_empty_config_file_is_falsy_so_detected_versions_are_not_persisted(self, migration, bench):
        """SUSPICION pinned: the persistence guard is ``if config_doc``, and an
        empty TOML document is falsy, so a zero-byte bench_config.toml silently
        loses the auto-detected versions (they are still returned and used)."""
        path = bench.path / "bench_config.toml"
        path.write_text("")
        migration._auto_detect_runtime_versions = Mock(return_value=("3.12", "20"))

        py, node, _ = migration._resolve_runtime_versions(bench)

        assert (py, node) == ("3.12", "20")
        assert path.read_text() == ""
        assert "Updated bench_config.toml with detected versions" not in printed(migration)


class TestChooseBestPythonVersion:
    @pytest.mark.parametrize(
        ("current", "requirement", "expected"),
        [
            # no current version at all
            (None, None, "3.11"),
            (None, ">=3.10,<3.13", "3.10"),
            (None, ">=3.10", "3.10"),
            # current inside a bounded requirement => keep it
            ("3.11", ">=3.10,<3.13", "3.11"),
            ("3.10", ">=3.10,<3.13", "3.10"),
            # current below the floor => upgrade to the floor
            ("3.9", ">=3.10,<3.13", "3.10"),
            ("3.9", ">=3.10", "3.10"),
            # current at/above the ceiling => DOWNGRADED to the floor
            ("3.13", ">=3.10,<3.13", "3.10"),
            ("3.14", ">=3.10,<3.13", "3.10"),
            # unbounded requirement
            ("3.12", ">=3.10", "3.12"),
            # requirement with no >= / < operator: the comparison block is skipped
            ("3.12", "3.11", "3.12"),
            ("3.9", "3.8", "3.8"),
            # no requirement: 3.10+ is kept, anything older falls back to the default
            ("3.12", None, "3.12"),
            ("3.10", None, "3.10"),
            ("3.9", None, "3.11"),
            ("2.7", None, "3.11"),
        ],
    )
    def test_decision_matrix(self, migration, current, requirement, expected):
        assert migration._choose_best_python_version(current, requirement) == expected

    def test_unparseable_requirement_is_ignored_and_current_is_kept(self, migration):
        """A requirement string the parser cannot read falls back to the plain 3.10 floor test."""
        assert migration._choose_best_python_version("3.12", "not-a-version") == "3.12"
        assert migration._choose_best_python_version("3.9", "not-a-version") == "3.11"
        assert migration._choose_best_python_version(None, "not-a-version") == "3.11"


class TestChooseBestNodeVersion:
    @pytest.mark.parametrize(
        ("current", "requirement", "expected"),
        [
            (None, None, "18"),
            (None, ">=18", "18"),
            (None, ">=20", "20"),
            ("20", ">=18", "20"),
            ("18", ">=18", "18"),
            ("16", ">=18", "18"),
            ("20", None, "20"),
            ("18", None, "18"),
            ("16", None, "18"),
        ],
    )
    def test_decision_matrix(self, migration, current, requirement, expected):
        assert migration._choose_best_node_version(current, requirement) == expected

    def test_unparseable_requirement_is_ignored_and_current_is_kept(self, migration):
        assert migration._choose_best_node_version("20", "not-a-version") == "20"
        assert migration._choose_best_node_version("16", "not-a-version") == "18"
        assert migration._choose_best_node_version(None, "not-a-version") == "18"


class TestAutoDetectRuntimeVersions:
    def test_detects_both_from_the_container_and_delegates_the_choice(self, migration, bench):
        bench.compose.run.side_effect = [ok("Python 3.11.9"), ok("v18.17.1")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        assert migration._auto_detect_runtime_versions(bench) == ("3.11", "18")

        migration._choose_best_python_version.assert_called_once_with("3.11", None)
        migration._choose_best_node_version.assert_called_once_with("18", None)
        assert "Detected current Python: 3.11" in printed(migration)
        assert "Detected current Node: 18" in printed(migration)

    def test_docker_failures_are_swallowed_and_yield_no_current_version(self, migration, bench):
        bench.compose.run.side_effect = RuntimeError("no such container")
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        migration._auto_detect_runtime_versions(bench)

        migration._choose_best_python_version.assert_called_once_with(None, None)
        migration._choose_best_node_version.assert_called_once_with(None, None)

    def test_non_zero_exit_is_treated_as_undetected(self, migration, bench):
        bench.compose.run.side_effect = [fail(1, "Python 3.11.9"), fail(1, "v18.0.0")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        migration._auto_detect_runtime_versions(bench)

        migration._choose_best_python_version.assert_called_once_with(None, None)

    def test_unparseable_output_is_treated_as_undetected(self, migration, bench):
        bench.compose.run.side_effect = [ok("command not found"), ok("garbage")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        migration._auto_detect_runtime_versions(bench)

        migration._choose_best_python_version.assert_called_once_with(None, None)
        migration._choose_best_node_version.assert_called_once_with(None, None)

    def test_frappe_requirements_are_read_only_when_the_app_exists(self, migration, bench):
        bench.compose.run.side_effect = [ok("Python 3.11.9"), ok("v18.0.0")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        with (
            patch("frappe_manager.site_manager.bench_config.extract_python_version_requirement") as extract_python,
            patch("frappe_manager.site_manager.bench_config.extract_node_version_requirement") as extract_node,
        ):
            migration._auto_detect_runtime_versions(bench)
            extract_python.assert_not_called()
            extract_node.assert_not_called()

    def test_frappe_requirements_are_forwarded_to_the_choosers(self, migration, bench):
        (bench.path / "workspace" / "frappe-bench" / "apps" / "frappe").mkdir(parents=True)
        bench.compose.run.side_effect = [ok("Python 3.11.9"), ok("v18.0.0")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        with (
            patch(
                "frappe_manager.site_manager.bench_config.extract_python_version_requirement",
                return_value=">=3.10,<3.13",
            ),
            patch("frappe_manager.site_manager.bench_config.extract_node_version_requirement", return_value=">=18"),
        ):
            migration._auto_detect_runtime_versions(bench)

        migration._choose_best_python_version.assert_called_once_with("3.11", ">=3.10,<3.13")
        migration._choose_best_node_version.assert_called_once_with("18", ">=18")
        assert "Frappe requires Python: >=3.10,<3.13" in printed(migration)
        assert "Frappe requires Node: >=18" in printed(migration)

    def test_frappe_app_without_readable_requirements_prints_nothing(self, migration, bench):
        (bench.path / "workspace" / "frappe-bench" / "apps" / "frappe").mkdir(parents=True)
        bench.compose.run.side_effect = [ok("Python 3.11.9"), ok("v18.0.0")]
        migration._choose_best_python_version = Mock(return_value="3.11")
        migration._choose_best_node_version = Mock(return_value="18")

        with (
            patch("frappe_manager.site_manager.bench_config.extract_python_version_requirement", return_value=None),
            patch("frappe_manager.site_manager.bench_config.extract_node_version_requirement", return_value=None),
        ):
            migration._auto_detect_runtime_versions(bench)

        assert not any("Frappe requires" in m for m in printed(migration))
        migration._choose_best_python_version.assert_called_once_with("3.11", None)


# ===========================================================================
# _check_runtime_current
# ===========================================================================


class TestCheckRuntimeCurrent:
    def test_no_targets_means_rebuild_without_touching_docker(self, migration, bench):
        assert migration._check_runtime_current(bench, None, None) == (False, False)
        bench.compose.run.assert_not_called()

    def test_both_ok_markers_are_parsed(self, migration, bench):
        bench.compose.run.return_value = ok("+ echo", "ENV_OK=true", "NODE_OK=true")
        assert migration._check_runtime_current(bench, "3.11", "18") == (True, True)

    def test_markers_are_independent(self, migration, bench):
        bench.compose.run.return_value = ok("ENV_OK=false", "NODE_OK=true")
        assert migration._check_runtime_current(bench, "3.11", "18") == (False, True)

    def test_script_checks_uv_cache_venv_and_fnm_for_the_target_versions(self, migration, bench):
        bench.compose.run.return_value = ok("ENV_OK=true", "NODE_OK=true")

        migration._check_runtime_current(bench, "3.11", "18")

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "cpython-3.11*" in cmd
        assert "Python 3.11" in cmd
        assert 'fnm list 2>/dev/null | grep -q "v18"' in cmd
        assert bench.compose.run.call_args.kwargs["entrypoint"] == "/exec-entrypoint.sh"
        assert bench.compose.run.call_args.kwargs["rm"] is True
        assert bench.compose.run.call_args.kwargs["service"] == "frappe"

    def test_missing_python_target_hardcodes_env_not_ok(self, migration, bench):
        bench.compose.run.return_value = ok("ENV_OK=false", "NODE_OK=true")

        env_current, node_current = migration._check_runtime_current(bench, None, "18")

        assert 'echo "ENV_OK=false"' in bench.compose.run.call_args.kwargs["command"]
        assert "cpython-" not in bench.compose.run.call_args.kwargs["command"]
        assert (env_current, node_current) == (False, True)

    def test_missing_node_target_hardcodes_node_not_ok(self, migration, bench):
        bench.compose.run.return_value = ok("ENV_OK=true", "NODE_OK=false")

        env_current, node_current = migration._check_runtime_current(bench, "3.11", None)

        assert "fnm list" not in bench.compose.run.call_args.kwargs["command"]
        assert (env_current, node_current) == (True, False)

    def test_docker_exception_is_swallowed_as_rebuild_needed(self, migration, bench):
        bench.compose.run.side_effect = RuntimeError("docker down")
        assert migration._check_runtime_current(bench, "3.11", "18") == (False, False)

    def test_non_zero_exit_is_rebuild_needed_even_when_markers_say_true(self, migration, bench):
        bench.compose.run.return_value = fail(2, "ENV_OK=true", "NODE_OK=true")
        assert migration._check_runtime_current(bench, "3.11", "18") == (False, False)

    def test_streaming_output_is_rebuild_needed(self, migration, bench):
        bench.compose.run.return_value = iter([("stdout", b"ENV_OK=true")])
        assert migration._check_runtime_current(bench, "3.11", "18") == (False, False)


# ===========================================================================
# _rebuild_runtime_environment -- the guard-heavy heart of the migration
# ===========================================================================


class TestRebuildRuntimeEnvironment:
    def _stub_steps(self, migration, order):
        def rec(name, ret=None):
            def f(*a, **k):
                order.append(name)
                return ret

            return f

        for name in (
            "_ensure_runtime_dirs",
            "_cleanup_old_runtime_dirs",
            "_backup_env_for_rollback",
            "_setup_python_with_uv",
            "_setup_node_with_fnm",
            "_reinstall_apps_and_rebuild",
            "_regenerate_supervisor_config",
            "_restart_services",
        ):
            setattr(migration, name, rec(name))

    def test_everything_current_short_circuits_the_whole_rebuild(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, True))

        migration._rebuild_runtime_environment(bench)

        assert order == []
        assert "Runtime environment already up to date" in printed(migration)

    def test_current_runtime_still_restarts_when_images_changed_and_bench_runs(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, True))
        migration._images_updated = True
        bench.running = True

        migration._rebuild_runtime_environment(bench)

        assert order == ["_restart_services"]

    def test_current_runtime_does_not_restart_a_stopped_bench(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, True))
        migration._images_updated = True
        bench.running = False
        bench.workers_running = False

        migration._rebuild_runtime_environment(bench)

        assert order == []

    def test_current_runtime_restarts_for_workers_only_too(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, True))
        migration._images_updated = True
        bench.workers_running = True

        migration._rebuild_runtime_environment(bench)

        assert order == ["_restart_services"]

    def test_current_runtime_without_image_change_never_restarts(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, True))
        migration._images_updated = False
        bench.running = True

        migration._rebuild_runtime_environment(bench)

        assert order == []

    def test_full_rebuild_order_and_env_backup_position(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(False, False))
        bench.running = True

        migration._rebuild_runtime_environment(bench)

        assert order == [
            "_ensure_runtime_dirs",
            "_cleanup_old_runtime_dirs",
            "_backup_env_for_rollback",
            "_setup_python_with_uv",
            "_setup_node_with_fnm",
            "_reinstall_apps_and_rebuild",
            "_regenerate_supervisor_config",
            "_restart_services",
        ]
        assert migration._env_was_rebuilt is True
        assert migration._node_was_setup is True

    def test_stopped_bench_is_not_started_by_the_rebuild(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(False, False))

        migration._rebuild_runtime_environment(bench)

        assert "_restart_services" not in order

    def test_prev_versions_are_read_before_resolve_writes_them(self, migration, bench):
        """The ``prev is None`` first-run guard depends on reading the config first."""
        path = bench.path / "bench_config.toml"
        path.write_text("")
        order: list[str] = []
        self._stub_steps(migration, order)

        def resolve(_bench):
            # emulate _resolve_runtime_versions persisting auto-detected versions
            path.write_text('python_version = "3.11"\nnode_version = "18"\n')
            return "3.11", "18", None

        migration._resolve_runtime_versions = resolve
        migration._check_runtime_current = Mock(return_value=(True, True))

        migration._rebuild_runtime_environment(bench)

        assert migration._python_version_changed is False
        assert migration._node_version_changed is False

    def test_config_versions_matching_targets_and_healthy_runtime_skip_setup(self, migration, bench):
        (bench.path / "bench_config.toml").write_text('python_version = "3.11"\nnode_version = "18"\n')
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, False))

        migration._rebuild_runtime_environment(bench)

        assert migration._env_was_rebuilt is False, "healthy env + unchanged version => no venv rebuild"
        assert migration._node_was_setup is True
        assert "_backup_env_for_rollback" not in order
        assert "_setup_python_with_uv" not in order
        assert "_setup_node_with_fnm" in order

    def test_changed_python_version_forces_env_rebuild_even_if_runtime_looks_current(self, migration, bench):
        (bench.path / "bench_config.toml").write_text('python_version = "3.10"\nnode_version = "18"\n')
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", "18", None))
        migration._check_runtime_current = Mock(return_value=(True, False))

        migration._rebuild_runtime_environment(bench)

        assert migration._python_version_changed is True
        assert migration._env_was_rebuilt is True
        assert "_setup_python_with_uv" in order

    def test_env_backup_is_skipped_when_no_python_target_is_known(self, migration, bench):
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=(None, "18", None))
        migration._check_runtime_current = Mock(return_value=(False, False))

        migration._rebuild_runtime_environment(bench)

        assert migration._env_was_rebuilt is True, "flag is set even though the setup is skipped"
        assert "_backup_env_for_rollback" not in order
        assert "_setup_python_with_uv" not in order

    def test_node_setup_flag_stays_true_without_a_node_target(self, migration, bench):
        """SUSPICION pinned: ``_node_was_setup`` is True even when fnm never ran,
        so ``_reinstall_apps_and_rebuild`` still runs the node build steps."""
        order: list[str] = []
        self._stub_steps(migration, order)
        migration._resolve_runtime_versions = Mock(return_value=("3.11", None, None))
        migration._check_runtime_current = Mock(return_value=(False, False))

        migration._rebuild_runtime_environment(bench)

        assert migration._node_was_setup is True
        assert "_setup_node_with_fnm" not in order


# ===========================================================================
# The 6 duplicated ``bench.compose.run`` blocks
# ===========================================================================


class TestDuplicatedComposeRunBlocks:
    """Each of the six sibling blocks reacts DIFFERENTLY to the same failures.

    A dedup that unifies them must reproduce this table exactly.
    """

    def _call(self, migration, bench, name):
        if name == "_check_runtime_current":
            return migration._check_runtime_current(bench, "3.11", "18")
        if name == "_setup_python_with_uv":
            return migration._setup_python_with_uv(bench, "3.11")
        if name == "_setup_node_with_fnm":
            return migration._setup_node_with_fnm(bench, "18")
        if name == "_reinstall_apps_and_rebuild":
            migration._env_was_rebuilt = True
            migration._node_was_setup = True
            return migration._reinstall_apps_and_rebuild(bench)
        return getattr(migration, name)(bench)

    ALL = [
        "_check_runtime_current",
        "_setup_python_with_uv",
        "_setup_node_with_fnm",
        "_cleanup_old_runtime_dirs",
        "_reinstall_apps_and_rebuild",
    ]

    @pytest.mark.parametrize("name", ALL)
    def test_shared_invocation_shape(self, migration, bench, name):
        bench.compose.run.return_value = ok("ENV_OK=true", "NODE_OK=true")

        self._call(migration, bench, name)

        kwargs = bench.compose.run.call_args.kwargs
        assert kwargs["service"] == "frappe"
        assert kwargs["rm"] is True
        assert kwargs["entrypoint"] == "/exec-entrypoint.sh"
        assert kwargs["command"].startswith("bash -c ")

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("_check_runtime_current", None),  # swallowed: returns (False, False)
            ("_setup_python_with_uv", "Python setup failed with exit code 7"),
            ("_setup_node_with_fnm", "Node setup failed with exit code 7"),
            ("_cleanup_old_runtime_dirs", None),  # warning only
            ("_reinstall_apps_and_rebuild", "App reinstallation failed with exit code 7"),
        ],
    )
    def test_non_zero_exit_reaction_differs_per_block(self, migration, bench, name, expected):
        bench.compose.run.return_value = fail(7)

        if expected is None:
            self._call(migration, bench, name)  # must not raise
        else:
            with pytest.raises(Exception, match=expected):
                self._call(migration, bench, name)

    @pytest.mark.parametrize(
        ("name", "raises"),
        [
            ("_check_runtime_current", False),  # returns (False, False)
            ("_setup_python_with_uv", True),
            ("_setup_node_with_fnm", True),
            ("_cleanup_old_runtime_dirs", True),
            ("_reinstall_apps_and_rebuild", True),
        ],
    )
    def test_streaming_output_reaction_differs_per_block(self, migration, bench, name, raises):
        bench.compose.run.return_value = iter([("stdout", b"x")])

        if raises:
            with pytest.raises(Exception, match="Unexpected streaming output received"):
                self._call(migration, bench, name)
        else:
            assert self._call(migration, bench, name) == (False, False)

    @pytest.mark.parametrize(
        ("name", "raises"),
        [
            ("_check_runtime_current", False),  # only this one catches docker exceptions
            ("_setup_python_with_uv", True),
            ("_setup_node_with_fnm", True),
            ("_cleanup_old_runtime_dirs", True),
            ("_reinstall_apps_and_rebuild", True),
        ],
    )
    def test_docker_exception_propagation_differs_per_block(self, migration, bench, name, raises):
        bench.compose.run.side_effect = RuntimeError("docker exploded")

        if raises:
            with pytest.raises(RuntimeError, match="docker exploded"):
                self._call(migration, bench, name)
        else:
            assert self._call(migration, bench, name) == (False, False)

    def test_only_python_setup_translates_docker_network_errors(self, migration, bench):
        bench.compose.run.side_effect = RuntimeError('network "fm" not found')

        with pytest.raises(Exception, match="Docker network not found"):
            migration._setup_python_with_uv(bench, "3.11")

    @pytest.mark.parametrize("message", ['network "fm" could not be found', 'NETWORK "fm" NOT FOUND'])
    def test_network_error_matching_is_case_insensitive(self, migration, bench, message):
        bench.compose.run.side_effect = RuntimeError(message)
        with pytest.raises(Exception, match="Try: fm services start"):
            migration._setup_python_with_uv(bench, "3.11")

    def test_non_network_error_is_re_raised_unchanged_by_python_setup(self, migration, bench):
        bench.compose.run.side_effect = RuntimeError("network unreachable")

        with pytest.raises(RuntimeError, match="network unreachable"):
            migration._setup_python_with_uv(bench, "3.11")

    def test_node_setup_does_not_translate_network_errors(self, migration, bench):
        """DIFFERENCE: the fnm block has no try/except, so raw errors escape."""
        bench.compose.run.side_effect = RuntimeError('network "fm" not found')

        with pytest.raises(RuntimeError, match='network "fm" not found'):
            migration._setup_node_with_fnm(bench, "18")

    def test_cleanup_failure_warns_but_lets_the_migration_continue(self, migration, bench):
        bench.compose.run.return_value = fail(3)

        migration._cleanup_old_runtime_dirs(bench)

        migration.logger.warning.assert_called_once()
        assert "continuing" in migration.logger.warning.call_args.args[0]

    def test_auto_detect_block_uses_raw_commands_without_shlex_quoting(self, migration, bench):
        """DIFFERENCE: the two detection calls pass literal commands, not quoted scripts."""
        bench.compose.run.side_effect = [ok("Python 3.11.9"), ok("v18.0.0")]

        migration._auto_detect_runtime_versions(bench)

        commands = [c.kwargs["command"] for c in bench.compose.run.call_args_list]
        assert commands == [
            "bash -c '/workspace/frappe-bench/env/bin/python --version 2>&1'",
            "bash -c 'node --version'",
        ]


class TestSetupPythonWithUvScript:
    def test_script_moves_old_venv_aside_and_repoints_python_default(self, migration, bench):
        bench.compose.run.return_value = ok()

        migration._setup_python_with_uv(bench, "3.11")

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "mv env env.bak" in cmd
        assert "export UV_PYTHON_INSTALL_DIR=/workspace/frappe-bench/.uv/python" in cmd
        assert "uv python install cpython-3.11" in cmd
        assert "ls -1d /workspace/frappe-bench/.uv/python/cpython-3.11*" in cmd
        assert "rm -f python-default" in cmd
        assert 'uv venv env --clear --python "$PYTHON_BASENAME" --seed --link-mode=copy' in cmd

    def test_version_is_shell_quoted(self, migration, bench):
        bench.compose.run.return_value = ok()
        migration._setup_python_with_uv(bench, "3.11; rm -rf /")
        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "'cpython-3.11; rm -rf /'" in cmd


class TestSetupNodeWithFnmScript:
    def test_script_installs_defaults_and_verifies_yarn(self, migration, bench):
        bench.compose.run.return_value = ok()

        migration._setup_node_with_fnm(bench, "18")

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert 'if fnm list | grep -q "v18"' in cmd
        assert "fnm install 18" in cmd
        assert "fnm default 18" in cmd
        assert "yarn --version" in cmd


class TestCleanupOldRuntimeDirs:
    def test_pyenv_nvm_and_bashrc_are_backed_up_before_removal(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / ".pyenv").mkdir(parents=True)
        (fb / ".nvm").mkdir(parents=True)
        (bench.path / "workspace" / ".bashrc").write_text("export PYENV_ROOT=...")
        bench.compose.run.return_value = ok()

        migration._cleanup_old_runtime_dirs(bench)

        backed = [c.args[0] for c in migration.backup_manager.backup.call_args_list]
        assert backed == [fb / ".pyenv", fb / ".nvm", bench.path / "workspace" / ".bashrc"]

    def test_absent_legacy_dirs_are_not_backed_up(self, migration, bench):
        bench.compose.run.return_value = ok()
        migration._cleanup_old_runtime_dirs(bench)
        migration.backup_manager.backup.assert_not_called()

    def test_script_removes_bashrc_pyenv_and_nvm_inside_the_container(self, migration, bench):
        bench.compose.run.return_value = ok()

        migration._cleanup_old_runtime_dirs(bench)

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "rm -f /workspace/.bashrc" in cmd
        assert "rm -rf /workspace/.pyenv" in cmd
        assert "rm -rf /workspace/.nvm" in cmd


class TestReinstallAppsAndRebuild:
    def test_no_changes_skips_docker_entirely(self, migration, bench):
        migration._env_was_rebuilt = False
        migration._node_was_setup = False

        migration._reinstall_apps_and_rebuild(bench)

        bench.compose.run.assert_not_called()
        assert "No env or Node changes — skipping app reinstall and build" in printed(migration)

    def test_env_only_rebuild_omits_the_node_steps(self, migration, bench):
        migration._env_was_rebuilt = True
        migration._node_was_setup = False
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "uv pip install --python env/bin/python" in cmd
        assert "bench setup requirements --node" not in cmd
        assert "bench build" not in cmd

    def test_node_only_setup_omits_the_pip_install(self, migration, bench):
        migration._env_was_rebuilt = False
        migration._node_was_setup = True
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "uv pip install" not in cmd
        assert "bench setup requirements --node" in cmd
        assert "bench build" in cmd

    def test_script_always_sources_bash_bashrc_for_fnm(self, migration, bench):
        migration._env_was_rebuilt = True
        migration._node_was_setup = True
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        cmd = bench.compose.run.call_args.kwargs["command"]
        assert "source /etc/bash.bashrc" in cmd
        assert "cd /workspace/frappe-bench" in cmd
        assert "set -x" in cmd

    def test_missing_apps_txt_warns_but_still_runs_the_reinstall(self, migration, bench):
        migration._env_was_rebuilt = True
        migration._node_was_setup = False
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        migration.output.warning.assert_called_once_with("No apps.txt found, skipping app reinstallation")
        bench.compose.run.assert_called_once()

    def test_empty_apps_txt_warns_differently(self, migration, bench):
        migration._env_was_rebuilt = True
        migration._node_was_setup = False
        (bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt").write_text("\n  \n")
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        migration.output.warning.assert_called_once_with("No apps found in apps.txt")

    def test_populated_apps_txt_produces_no_warning(self, migration, bench):
        migration._env_was_rebuilt = True
        migration._node_was_setup = False
        (bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt").write_text("frappe\nerpnext\n")
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        migration.output.warning.assert_not_called()

    def test_apps_txt_is_not_consulted_when_only_node_changed(self, migration, bench):
        migration._env_was_rebuilt = False
        migration._node_was_setup = True
        bench.compose.run.return_value = ok()

        migration._reinstall_apps_and_rebuild(bench)

        migration.output.warning.assert_not_called()


class TestEnsureRuntimeDirs:
    def test_creates_uv_and_fnm_dirs_and_fixes_ownership(self, migration, bench):
        with patch("frappe_manager.utils.docker.fix_host_path_ownership") as fix:
            migration._ensure_runtime_dirs(bench)

        fb = bench.path / "workspace" / "frappe-bench"
        assert (fb / ".uv").is_dir()
        assert (fb / ".fnm").is_dir()
        fix.assert_called_once_with(paths=[fb / ".uv", fb / ".fnm"], output=migration.output)

    def test_existing_dirs_are_tolerated(self, migration, bench):
        fb = bench.path / "workspace" / "frappe-bench"
        (fb / ".uv").mkdir(parents=True)
        with patch("frappe_manager.utils.docker.fix_host_path_ownership"):
            migration._ensure_runtime_dirs(bench)
        assert (fb / ".fnm").is_dir()


# ===========================================================================
# supervisor config regeneration
# ===========================================================================


class TestRegenerateSupervisorConfig:
    def _run(self, migration, bench, site_config=None):
        if site_config is not None:
            (bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json").write_text(
                json.dumps(site_config)
            )
        migration._regenerate_supervisor_config(bench)
        return bench.path / "workspace" / "frappe-bench" / "config"

    def test_one_file_per_program_section_and_no_group_files(self, migration, bench):
        config_dir = self._run(migration, bench, {})

        names = sorted(p.name for p in config_dir.iterdir())
        assert names == [
            "fm-web-server.sh",
            "long-worker.workers.fm.supervisor.conf",
            "schedule.fm.supervisor.conf",
            "short-worker.workers.fm.supervisor.conf",
            "socketio.fm.supervisor.conf",
            "web.fm.supervisor.conf",
        ], "[group:...] sections are never written to their own file"

    def test_multi_queue_consumption_is_hardcoded_so_no_default_worker_exists(self, migration, bench):
        config_dir = self._run(migration, bench, {})

        assert not (config_dir / "default-worker.workers.fm.supervisor.conf").exists()
        short = (config_dir / "short-worker.workers.fm.supervisor.conf").read_text()
        long_ = (config_dir / "long-worker.workers.fm.supervisor.conf").read_text()
        assert "--queue short,default" in short
        assert "--queue long,default,short" in long_

    def test_worker_sections_get_the_workers_infix(self, migration, bench):
        config_dir = self._run(migration, bench, {})
        assert (config_dir / "short-worker.workers.fm.supervisor.conf").exists()
        assert not (config_dir / "short-worker.fm.supervisor.conf").exists()
        assert (config_dir / "web.fm.supervisor.conf").exists(), "non-worker sections have no .workers infix"

    def test_node_sections_are_split_on_the_node_delimiter(self, migration, bench):
        config_dir = self._run(migration, bench, {})
        text = (config_dir / "socketio.fm.supervisor.conf").read_text()
        assert "[program:frappe-bench-node-socketio]" in text

    def test_custom_workers_produce_extra_files(self, migration, bench):
        config_dir = self._run(migration, bench, {"workers": {"custom": {"timeout": 5000}}})
        assert (config_dir / "custom-worker.workers.fm.supervisor.conf").exists()

    def test_gunicorn_settings_come_from_common_site_config(self, migration, bench):
        config_dir = self._run(
            migration,
            bench,
            {
                "gunicorn_workers": 3,
                "gunicorn_threads": 2,
                "gunicorn_max_requests": 500,
                "http_timeout": 60,
                "webserver_port": 8080,
            },
        )

        script = (config_dir / "fm-web-server.sh").read_text()
        assert "--bind 0.0.0.0:8080" in script
        assert "--workers 3" in script
        assert "--threads 2" in script
        assert "--max-requests 500" in script
        assert "--max-requests-jitter 50" in script, "jitter is 10% of max-requests"
        assert "-t 60" in script
        assert "--graceful-timeout 30" in script
        assert "frappe.app:application --preload" in script

    def test_gunicorn_defaults_are_derived_from_cpu_count(self, migration, bench):
        with patch("multiprocessing.cpu_count", return_value=4):
            config_dir = self._run(migration, bench, {})

        script = (config_dir / "fm-web-server.sh").read_text()
        assert "--workers 9" in script, "(cpu*2)+1"
        assert "--threads 4" in script, "max(2, min(cpu, 4))"
        assert "--max-requests 1000" in script
        assert "--max-requests-jitter 100" in script
        assert "--bind 0.0.0.0:80" in script
        assert "-t 120" in script

    def test_thread_default_is_floored_at_two(self, migration, bench):
        with patch("multiprocessing.cpu_count", return_value=1):
            config_dir = self._run(migration, bench, {})
        assert "--threads 2" in (config_dir / "fm-web-server.sh").read_text()

    def test_corrupt_common_site_config_falls_back_to_defaults(self, migration, bench):
        (bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json").write_text("{broken")
        with patch("multiprocessing.cpu_count", return_value=2):
            migration._regenerate_supervisor_config(bench)

        script = (bench.path / "workspace" / "frappe-bench" / "config" / "fm-web-server.sh").read_text()
        assert "--workers 5" in script

    def test_missing_common_site_config_is_tolerated(self, migration, bench):
        config_dir = self._run(migration, bench, None)
        assert (config_dir / "web.fm.supervisor.conf").exists()

    def test_generated_config_points_at_the_fnm_node_binary(self, migration, bench):
        config_dir = self._run(migration, bench, {})
        text = (config_dir / "socketio.fm.supervisor.conf").read_text()
        assert "/workspace/frappe-bench/.fnm/aliases/default/bin/node" in text

    def test_background_workers_zero_is_coerced_to_one(self, migration, bench):
        config_dir = self._run(migration, bench, {"background_workers": 0})
        text = (config_dir / "short-worker.workers.fm.supervisor.conf").read_text()
        assert "numprocs = 1" in text, "0 background workers would disable the queue entirely"

    def test_background_workers_value_is_propagated(self, migration, bench):
        config_dir = self._run(migration, bench, {"background_workers": 3})
        text = (config_dir / "short-worker.workers.fm.supervisor.conf").read_text()
        assert "numprocs = 3" in text


class TestGenerateFmWebServerScript:
    CONTEXT = {
        "bench_dir": "/workspace/frappe-bench",
        "bench_name": "frappe-bench",
        "webserver_port": 80,
        "gunicorn_workers": 5,
        "gunicorn_threads": 4,
        "gunicorn_max_requests": 1000,
        "gunicorn_max_requests_jitter": 100,
        "http_timeout": 120,
    }

    def test_new_script_is_executable_and_tracked_for_rollback(self, migration, tmp_path):
        migration._generate_fm_web_server_script(tmp_path, dict(self.CONTEXT))

        path = tmp_path / "fm-web-server.sh"
        assert path.stat().st_mode & 0o777 == 0o755
        migration.backup_manager.track_new_file.assert_called_once_with(path)
        migration.backup_manager.backup.assert_not_called()
        assert "Generated fm-web-server.sh" in printed(migration)

    def test_pre_existing_script_is_backed_up_under_the_template_bench_name(self, migration, tmp_path):
        """SUSPICION pinned: ``bench_name`` here is the literal template value
        ``frappe-bench``, not the real bench, so the backup lands in the wrong tree."""
        path = tmp_path / "fm-web-server.sh"
        path.write_text("old")

        migration._generate_fm_web_server_script(tmp_path, dict(self.CONTEXT))

        migration.backup_manager.backup.assert_called_once_with(path, bench_name="frappe-bench")
        migration.backup_manager.track_new_file.assert_not_called()
        assert path.read_text() != "old"

    def test_missing_gunicorn_threads_defaults_to_one(self, migration, tmp_path):
        context = dict(self.CONTEXT)
        del context["gunicorn_threads"]

        migration._generate_fm_web_server_script(tmp_path, context)

        assert "--threads 1" in (tmp_path / "fm-web-server.sh").read_text()


# ===========================================================================
# service restart
# ===========================================================================


class TestRestartServices:
    def test_stale_default_conf_is_copied_aside_then_deleted(self, migration, bench):
        confd = bench.path / "configs" / "nginx" / "conf" / "conf.d"
        (confd / "default.conf").write_text("old vhost")

        migration._restart_services(bench)

        assert not (confd / "default.conf").exists(), "entrypoint must regenerate it"
        assert (confd / "default.conf.migration.bak").read_text() == "old vhost"
        assert "Backed up and removed stale nginx default.conf for regeneration" in printed(migration)

    def test_forced_recreate_of_the_four_core_services(self, migration, bench):
        migration._restart_services(bench)
        bench.compose.up.assert_called_once_with(
            services=["frappe", "socketio", "schedule", "nginx"], force_recreate=True, detach=True
        )

    def test_workers_are_recreated_only_when_running(self, migration, bench):
        bench.workers_running = True
        migration._restart_services(bench)
        bench.workers_docker.compose.up.assert_called_once_with(force_recreate=True, detach=True)

    def test_stopped_workers_are_left_alone(self, migration, bench):
        bench.workers_running = False
        migration._restart_services(bench)
        bench.workers_docker.compose.up.assert_not_called()

    def test_restart_failure_warns_with_manual_instructions_and_never_raises(self, migration, bench):
        bench.compose.up.side_effect = RuntimeError("boom")

        migration._restart_services(bench)

        warnings = [c.args[0] for c in migration.output.warning.call_args_list]
        assert "Service restart (force-recreate) failed: boom" in warnings
        assert "Please restart services manually: fm restart test-bench" in warnings

    def test_worker_restart_failure_is_swallowed_too(self, migration, bench):
        bench.workers_running = True
        bench.workers_docker.compose.up.side_effect = RuntimeError("workers boom")

        migration._restart_services(bench)

        bench.compose.up.assert_called_once()
        assert any("workers boom" in c.args[0] for c in migration.output.warning.call_args_list)

    def test_absent_default_conf_still_recreates_services(self, migration, bench):
        migration._restart_services(bench)
        bench.compose.up.assert_called_once()
        assert not (bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf.migration.bak").exists()


# ===========================================================================
# end-to-end on a fake bench tree
# ===========================================================================


class TestMigrateBenchEndToEndOnFakeTree:
    @pytest.mark.timeout(15)
    def test_config_and_compose_are_migrated_consistently(self, migration, bench):
        (bench.path / "bench_config.toml").write_text(
            'name = "test-bench"\nenvironment_type = "dev"\n\n[ssl]\npreferred_challenge = "dns01"\n'
        )
        (bench.path / "docker-compose.yml").write_text(
            "x-version: 0.18.0\n"
            "services:\n"
            "  frappe:\n"
            "    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.18.0\n"
            "  nginx:\n"
            "    image: ghcr.io/rtcamp/frappe-manager-nginx:v0.18.0\n"
            "    environment:\n"
            "      SITENAME: test-bench\n"
        )
        (bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json").write_text("{}")
        vhostd = bench.path.parent.parent / "services" / "nginx-proxy" / "vhostd"
        vhostd.mkdir(parents=True)
        migration._rebuild_runtime_environment = Mock()
        bench.compose.pull.return_value = ok()

        migration.migrate_bench(bench)

        config = tomlkit.parse((bench.path / "bench_config.toml").read_text())
        assert config["ssl_certificates"][0]["challenge_type"] == "dns01"
        assert config["restart_policy"] == "no"
        assert config["upload_limit"] == "50M"

        compose = load_yaml(bench.path / "docker-compose.yml")
        assert compose["x-version"] == "0.19.0"
        assert compose["services"]["frappe"]["restart"] == "no", "restart policy follows the migrated config"
        assert compose["services"]["nginx"]["environment"]["CLIENT_MAX_BODY_SIZE"] == "50m"

        assert (
            bench.path / "configs" / "nginx" / "conf" / "custom" / "upload-limit.conf"
        ).read_text() == "client_max_body_size 50m;\n"
        assert "client_max_body_size 50m;" in (vhostd / "test-bench").read_text()
        site_config = json.loads(
            (bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json").read_text()
        )
        assert site_config["max_file_size"] == 50 * 1024**2
        bench.compose.pull.assert_called_once_with(stream=False)

    @pytest.mark.timeout(15)
    def test_no_pull_when_images_are_already_current(self, migration, bench):
        (bench.path / "docker-compose.yml").write_text(
            "services:\n  frappe:\n    image: ghcr.io/rtcamp/frappe-manager-frappe:v0.19.0\n"
        )
        migration._rebuild_runtime_environment = Mock()

        migration.migrate_bench(bench)

        bench.compose.pull.assert_not_called()
