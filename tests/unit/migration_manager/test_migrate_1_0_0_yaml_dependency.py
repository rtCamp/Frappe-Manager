"""v1.0.0 services migration: the module must load on a clean install, without PyYAML.

`migrate_1_0_0.py` used to `import yaml` (PyYAML) at module scope even though fm declares only
`ruamel-yaml>=0.19.0,<0.20.0` in pyproject.toml. That worked in the dev venv, where PyYAML ships
transitively through some other dependency, but on a clean `uv tool install` Python has no `yaml`
module at all: the import raised at module load, the migration loader caught the exception and
silently skipped the ENTIRE v1.0.0 migration, and `fm services migrate` still printed success and
stamped the version as migrated.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock

import yaml

import frappe_manager
from frappe_manager.migration_manager.migrations.migrate_1_0_0 import MigrationV100

PACKAGE_ROOT = Path(frappe_manager.__file__).parent


def _is_pyyaml(module: str) -> bool:
    return module == "yaml" or module.startswith("yaml.")


def _pyyaml_import_lines(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_pyyaml(alias.name):
                    yield node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and _is_pyyaml(node.module):
            yield node.lineno


def test_no_frappe_manager_module_imports_pyyaml():
    """fm depends on ruamel-yaml, not PyYAML, so an `import yaml` anywhere under
    `frappe_manager/` is unloadable on a clean install and silently costs the operator an
    entire migration; parsing the AST (instead of importing) catches it even though PyYAML
    happens to be importable in this dev venv."""
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        for lineno in _pyyaml_import_lines(path)
    ]
    assert offenders == [], f"PyYAML import(s) found: {', '.join(offenders)}"


def test_the_three_converted_methods_round_trip_a_services_compose_without_losing_keys(tmp_path, monkeypatch):
    """The PyYAML -> ruamel conversion must not drop a key the operator already had on disk."""
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    compose = services_dir / "docker-compose.yml"
    original = {
        "version": "3.9",
        "x-fm-managed": True,
        "services": {
            "global-db": {
                "image": "mariadb:11.4",
                "restart": "always",
                "environment": {"MARIADB_AUTO_UPGRADE": 1},
            },
            "global-nginx-proxy": {
                "image": "jwilder/nginx-proxy:1.11",
                "environment": {"LOG_FORMAT": "old-format", "LOG_FORMAT_ESCAPE": "json"},
                "volumes": ["./nginx-proxy/certs:/etc/nginx/certs"],
            },
        },
        "networks": {
            "frontend-network": {"name": "fm-frontend-network"},
            "backend-network": {"name": "fm-backend-network"},
        },
        "secrets": {"db_password": {"file": "/secrets/db_password.txt"}},
    }
    compose.write_text(yaml.safe_dump(original, sort_keys=False))

    monkeypatch.setattr("frappe_manager.CLI_SERVICES_DIRECTORY", services_dir)
    migration = MigrationV100.__new__(MigrationV100)
    migration.output = MagicMock()

    migration._refresh_proxy_log_format()
    migration._add_proxy_fmd_mount()
    migration._add_postgres_service()

    doc = yaml.safe_load(compose.read_text())

    # every key present before the three methods ran is still present after them
    assert doc["version"] == "3.9"
    assert doc["x-fm-managed"] is True
    assert doc["services"]["global-db"]["environment"]["MARIADB_AUTO_UPGRADE"] == 1
    assert doc["networks"]["frontend-network"]["name"] == "fm-frontend-network"
    assert doc["networks"]["backend-network"]["name"] == "fm-backend-network"
    assert doc["secrets"]["db_password"]["file"] == "/secrets/db_password.txt"

    # and each method's own write landed too
    proxy = doc["services"]["global-nginx-proxy"]
    assert proxy["environment"]["LOG_FORMAT"] != "old-format"
    assert "./nginx-proxy/fmd:/etc/nginx/fm.d" in proxy["volumes"]
    assert "./nginx-proxy/certs:/etc/nginx/certs" in proxy["volumes"]
    assert "postgres" in doc["services"]
    assert doc["secrets"]["postgres_root_password"]["file"]
