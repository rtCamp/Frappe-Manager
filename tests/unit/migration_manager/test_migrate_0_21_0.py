"""v0.21.0 rename cutover: the compose rewrite and the db_host rewrite.

The decisions defended here are the ones whose failure mode is data loss or a dark host:

* Subnets must travel UNCHANGED through the network rename: bench `real-ip.conf` trusts
  the frontend subnet, and a drifted subnet silently mislabels every visitor.
* The macOS volume rename must keep pointing compose at the COPIED data via an explicit
  `name:`; a bare key rename makes docker create a fresh empty volume.
* Only the exact `db_host: global-db` is rewritten: an external endpoint (RDS, IP) is
  someone else's server and must never be touched.
* The rewrite is idempotent, because dev builds re-run migrations.
"""

import json
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from frappe_manager.migration_manager.migrations.migrate_0_21_0 import (
    MigrationV0210,
    rewrite_compose_for_rename,
)

SERVICES_COMPOSE = """\
services:
  global-db:
    container_name: fm_global-db
    image: mariadb:11.8
    volumes:
      - fm-global-db-data:/var/lib/mysql
      - ./mariadb/conf:/etc/mysql
    networks:
        - global-backend-network
  global-nginx-proxy:
    container_name: fm_global-nginx-proxy
    image: jwilder/nginx-proxy:1.11
    networks:
      - global-frontend-network

networks:
  global-frontend-network:
    name: fm-global-frontend-network
    ipam:
      config:
      - subnet: '10.7.0.0/16'
  global-backend-network:
    name: fm-global-backend-network
    ipam:
      config:
      - subnet: '10.8.0.0/16'

volumes:
  fm-global-db-data:
"""

BENCH_COMPOSE = """\
services:
  frappe:
    networks:
      site-network:
        aliases:
          - frappe-site
      global-backend-network:
      global-frontend-network:
  nginx:
    networks:
      site-network:
        aliases:
          - nginx-site
      global-frontend-network:

networks:
  site-network:
    name: fm__shop__site-network
  global-frontend-network:
    name: fm-global-frontend-network
    external: true
  global-backend-network:
    name: fm-global-backend-network
    external: true
"""


def _load(text: str):
    return YAML().load(text)


class TestServicesComposeRewrite:
    def test_services_containers_networks_and_volume_are_renamed(self):
        data = _load(SERVICES_COMPOSE)

        assert rewrite_compose_for_rename(data) is True

        services = data["services"]
        assert set(services) == {"mariadb", "nginx-proxy"}
        assert services["mariadb"]["container_name"] == "fm_mariadb"
        assert services["nginx-proxy"]["container_name"] == "fm_nginx-proxy"
        assert services["mariadb"]["networks"] == ["backend-network"]
        assert services["nginx-proxy"]["networks"] == ["frontend-network"]
        assert data["networks"]["frontend-network"]["name"] == "fm-frontend-network"
        assert data["networks"]["backend-network"]["name"] == "fm-backend-network"

    def test_subnets_travel_unchanged(self):
        """The frontend subnet is what every bench's real-ip.conf trusts."""
        data = _load(SERVICES_COMPOSE)

        rewrite_compose_for_rename(data)

        assert data["networks"]["frontend-network"]["ipam"]["config"][0]["subnet"] == "10.7.0.0/16"
        assert data["networks"]["backend-network"]["ipam"]["config"][0]["subnet"] == "10.8.0.0/16"

    def test_the_data_volume_is_renamed_and_pinned_to_the_copied_name(self):
        """A bare key rename would make docker create a fresh EMPTY volume; the explicit
        `name:` is what points compose at the data the migration copied."""
        data = _load(SERVICES_COMPOSE)

        rewrite_compose_for_rename(data)

        assert data["services"]["mariadb"]["volumes"][0] == "mariadb-data:/var/lib/mysql"
        assert data["services"]["mariadb"]["volumes"][1] == "./mariadb/conf:/etc/mysql"
        assert data["volumes"] == {"mariadb-data": {"name": "fm-mariadb-data"}}

    def test_rewrite_is_idempotent(self):
        data = _load(SERVICES_COMPOSE)
        rewrite_compose_for_rename(data)
        snapshot = json.dumps(data, default=str)

        assert rewrite_compose_for_rename(data) is False
        assert json.dumps(data, default=str) == snapshot


class TestBenchComposeRewrite:
    def test_mapping_form_networks_and_external_declarations_are_renamed(self):
        data = _load(BENCH_COMPOSE)

        assert rewrite_compose_for_rename(data) is True

        frappe = data["services"]["frappe"]["networks"]
        assert list(frappe) == ["site-network", "backend-network", "frontend-network"]
        # the site network and its aliases are not fm's rename to make
        assert frappe["site-network"]["aliases"] == ["frappe-site"]
        nets = data["networks"]
        assert nets["frontend-network"]["name"] == "fm-frontend-network"
        assert nets["frontend-network"]["external"] is True
        assert nets["backend-network"]["name"] == "fm-backend-network"
        assert nets["site-network"]["name"] == "fm__shop__site-network"


LEGACY_EXTERNAL_SERVICES_COMPOSE = """\
services:
  global-nginx-proxy:
    container_name: fm_global-nginx-proxy
    networks:
      global-frontend-network:
        ipv4_address: 10.0.1.2

networks:
  global-frontend-network:
    name: fm-global-frontend-network
    external: true
    ipam:
      config:
      - subnet: '10.1.0.0/16'
  global-backend-network:
    name: fm-global-backend-network
    external: true
"""


class TestServicesNetworkOwnershipNormalization:
    """Legacy installs marked the shared networks `external` in the SERVICES compose too,
    leaving them with no owner: nothing recreated them after the rename deleted them, and
    compose ignores ipam on an external network, so that block was free to rot (this very
    fixture: a proxy pinned at 10.0.1.2 beside a claimed 10.1.0.0/16)."""

    def _migration(self):
        m = MigrationV0210(output_handler=MagicMock())
        # daemon truth captured before deletion; deliberately different from the rotten
        # compose ipam, because the daemon is the side that must win
        m._old_subnets = {"frontend-network": "10.0.0.0/16", "backend-network": None}
        return m

    def test_external_is_dropped_and_the_daemon_subnet_wins(self):
        data = _load(LEGACY_EXTERNAL_SERVICES_COMPOSE)
        m = self._migration()

        rewrite_compose_for_rename(data)
        m._normalize_services_networks(data)

        front = data["networks"]["frontend-network"]
        assert "external" not in front
        assert front["name"] == "fm-frontend-network"
        assert front["ipam"]["config"][0]["subnet"] == "10.0.0.0/16"
        # the proxy's pinned address is untouched; it lives inside the daemon subnet
        assert data["services"]["nginx-proxy"]["networks"]["frontend-network"]["ipv4_address"] == "10.0.1.2"

    def test_no_daemon_capture_keeps_the_compose_ipam_but_still_takes_ownership(self):
        data = _load(LEGACY_EXTERNAL_SERVICES_COMPOSE)
        m = self._migration()

        rewrite_compose_for_rename(data)
        m._normalize_services_networks(data)

        back = data["networks"]["backend-network"]
        assert "external" not in back
        assert back["name"] == "fm-backend-network"
        assert "ipam" not in back


def _migration(tmp_path):
    m = MigrationV0210(output_handler=MagicMock())
    m.backup_manager = MagicMock()
    return m


def _bench(tmp_path, name="shop"):
    bench = MagicMock()
    bench.name = name
    bench.path = tmp_path / name
    (bench.path / "workspace" / "frappe-bench" / "sites").mkdir(parents=True)
    return bench


def _write_site_config(bench, site: str, config: dict):
    site_dir = bench.path / "workspace" / "frappe-bench" / "sites" / site
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))
    return site_dir / "site_config.json"


class TestDbHostRewrite:
    def test_the_shared_host_is_renamed_and_backed_up_first(self, tmp_path):
        m = _migration(tmp_path)
        bench = _bench(tmp_path)
        config_path = _write_site_config(bench, "shop.localhost", {"db_host": "global-db", "db_name": "x"})

        assert m._rewrite_db_hosts(bench) is True

        rewritten = json.loads(config_path.read_text())
        assert rewritten["db_host"] == "mariadb"
        assert rewritten["db_name"] == "x"
        m.backup_manager.backup.assert_called_once_with(config_path, bench_name="shop")

    def test_an_external_endpoint_is_never_touched(self, tmp_path):
        """`db_host` naming anything but the shared service is someone else's server."""
        m = _migration(tmp_path)
        bench = _bench(tmp_path)
        config_path = _write_site_config(bench, "ext.example.com", {"db_host": "rds.internal", "db_name": "x"})

        assert m._rewrite_db_hosts(bench) is False

        assert json.loads(config_path.read_text())["db_host"] == "rds.internal"
        m.backup_manager.backup.assert_not_called()

    def test_the_legacy_common_fallback_is_renamed_too(self, tmp_path):
        m = _migration(tmp_path)
        bench = _bench(tmp_path)
        common = bench.path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"
        common.write_text(json.dumps({"db_host": "global-db", "db_port": 3306}))

        assert m._rewrite_db_hosts(bench) is True

        assert json.loads(common.read_text())["db_host"] == "mariadb"

    def test_invalid_json_is_reported_and_left_untouched(self, tmp_path):
        """A migration must not turn a broken config into a stack trace or an empty file."""
        m = _migration(tmp_path)
        bench = _bench(tmp_path)
        config_path = _write_site_config(bench, "shop.localhost", {})
        config_path.write_text("{not json")

        assert m._rewrite_db_hosts(bench) is False

        assert config_path.read_text() == "{not json"
        assert m.output.warning.called
