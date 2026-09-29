"""v1.0.0 services migration: the proxy's access-log format.

`generate_compose` applies onto the file already on disk and never re-renders from the template,
which is what preserves a pinned subnet and any profile fm set earlier. The consequence is that
the template decides what a NEW install gets and nothing else, so a format change reaches an
existing host only here.

Why this tier and not a reconcile on some command's start path: the content is fm's own constant,
so it changes only with fm's version, and docker reads an environment variable once, at container
creation. A write anywhere an operator has not already accepted a recreate leaves the compose
disagreeing with the running proxy indefinitely.
"""

from unittest.mock import MagicMock

import yaml

from frappe_manager.migration_manager.migrations.migrate_1_0_0 import MigrationV100
from frappe_manager.site_manager.modules.nginx_logging import FM_JSON_LOG_FORMAT

_LEGACY = {
    "services": {
        "global-nginx-proxy": {
            "image": "jwilder/nginx-proxy:1.11",
            "environment": {"LOG_FORMAT": '{"scheme":"$$scheme"}', "LOG_FORMAT_ESCAPE": "json"},
        }
    }
}


def _run(tmp_path, monkeypatch, doc) -> dict:
    services_dir = tmp_path / "services"
    services_dir.mkdir(parents=True, exist_ok=True)
    compose = services_dir / "docker-compose.yml"
    if doc is not None:
        compose.write_text(yaml.safe_dump(doc, sort_keys=False))

    monkeypatch.setattr("frappe_manager.CLI_SERVICES_DIRECTORY", services_dir)

    migration = MigrationV100.__new__(MigrationV100)
    migration.output = MagicMock()
    migration._refresh_proxy_log_format()

    return yaml.safe_load(compose.read_text()) if compose.exists() else {}


def test_an_existing_install_gains_the_current_format(tmp_path, monkeypatch):
    """The bug this closes: a template-only change reached new installs and nobody else."""
    doc = _run(tmp_path, monkeypatch, _LEGACY)

    written = doc["services"]["global-nginx-proxy"]["environment"]["LOG_FORMAT"]
    assert written == FM_JSON_LOG_FORMAT.replace("$", "$$")
    assert "$$fm_client_scheme" in written


def test_the_nginx_variables_stay_escaped_for_compose(tmp_path, monkeypatch):
    """Unescaped, compose expands them itself and hands nginx a line of empty strings."""
    doc = _run(tmp_path, monkeypatch, _LEGACY)

    written = doc["services"]["global-nginx-proxy"]["environment"]["LOG_FORMAT"]
    assert "$$time_iso8601" in written
    assert "$time_iso8601" not in written.replace("$$time_iso8601", "")


def test_the_post_rename_service_name_is_handled_too(tmp_path, monkeypatch):
    """Dev builds re-run migrations, so this has to work against a compose the rename already
    cut over -- the same idempotence every other step in this migration carries."""
    doc = _run(
        tmp_path,
        monkeypatch,
        {"services": {"nginx-proxy": {"environment": {"LOG_FORMAT": "old"}}}},
    )

    assert "$$fm_client_scheme" in doc["services"]["nginx-proxy"]["environment"]["LOG_FORMAT"]


def test_a_current_format_is_left_untouched(tmp_path, monkeypatch):
    current = {
        "services": {
            "nginx-proxy": {"environment": {"LOG_FORMAT": FM_JSON_LOG_FORMAT.replace("$", "$$")}}
        }
    }
    services_dir = tmp_path / "services"
    services_dir.mkdir(parents=True)
    compose = services_dir / "docker-compose.yml"
    compose.write_text(yaml.safe_dump(current, sort_keys=False))
    before = compose.read_text()

    _run(tmp_path, monkeypatch, None)

    assert compose.read_text() == before


def test_a_host_with_no_services_compose_is_a_no_op(tmp_path, monkeypatch):
    """`fm services migrate` runs against hosts that never created the stack."""
    assert _run(tmp_path, monkeypatch, None) == {}


def test_a_proxy_with_no_environment_block_is_left_alone(tmp_path, monkeypatch):
    """Hand-edited compose files exist; fm must not invent an environment block in one."""
    doc = _run(tmp_path, monkeypatch, {"services": {"nginx-proxy": {"image": "x"}}})

    assert "environment" not in doc["services"]["nginx-proxy"]
