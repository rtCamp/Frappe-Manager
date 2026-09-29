"""Where the proxy publishes, and whose forwarded headers it believes.

Two settings that look independent and are not. Both are rendered from one place so nginx and
gunicorn cannot disagree about them, and both have a failure mode that only shows up on a host
that is already serving:

* a published port is fixed when a container is CREATED, so applying one has to recreate the
  proxy -- and the port the proxy listens on INSIDE the container must never move with it, or
  every bench's server-side calls to itself break (notes/proxy-front-design.md V5);
* `$fm_client_scheme` must be defined even when nothing is trusted, because every per-domain
  redirect block references it and nginx refuses to start on an undefined variable.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.metadata_manager import FMProxyConfig
from frappe_manager.services_manager.services import ServicesManager, _port_hint
from frappe_manager.site_manager.modules.realip import (
    PROXY_TRUST_CONF_FILENAME,
    build_proxy_realip_conf,
    build_proxy_trust_conf,
    trusted_ranges,
)


@pytest.fixture
def services(tmp_path, monkeypatch):
    confd = tmp_path / "confd"
    confd.mkdir()
    manager = ServicesManager.__new__(ServicesManager)
    manager.proxy_storage = MagicMock()
    manager.proxy_storage.dirs.confd.host = str(confd)
    manager.compose_file_manager = MagicMock()
    manager.compose_file_manager.yml = {"services": {"nginx-proxy": {"ports": ["80:80", "443:443"]}}}
    manager.confd = confd
    monkeypatch.setenv("FRAPPE_MANAGER_HOME", str(tmp_path))
    return manager


def _with_proxy(monkeypatch, **kwargs):
    config = MagicMock()
    config.proxy = FMProxyConfig(**kwargs)
    monkeypatch.setattr(
        "frappe_manager.services_manager.services.FMConfigManager.import_from_toml",
        lambda *a, **k: config,
    )


class TestPublishedPorts:
    def test_only_the_host_side_moves(self, services, monkeypatch):
        """The container half stays 80/443: benches reach the proxy at those ports over the fm
        network, so publishing elsewhere must not change what it listens on."""
        _with_proxy(monkeypatch, http_port=8080, https_port=8443)

        assert services.apply_proxy_ports() is True
        assert services.compose_file_manager.yml["services"]["nginx-proxy"]["ports"] == ["8080:80", "8443:443"]

    def test_bind_restricts_the_published_address(self, services, monkeypatch):
        _with_proxy(monkeypatch, http_port=8080, https_port=8443, bind="127.0.0.1")

        services.apply_proxy_ports()

        assert services.compose_file_manager.yml["services"]["nginx-proxy"]["ports"] == [
            "127.0.0.1:8080:80",
            "127.0.0.1:8443:443",
        ]

    def test_no_change_reports_no_change(self, services, monkeypatch):
        """`init()` runs on every command: reporting a change that did not happen would rewrite
        the compose file, and on the ports command it would recreate the proxy for nothing."""
        _with_proxy(monkeypatch)

        assert services.apply_proxy_ports() is False

    def test_a_default_port_leaves_no_suffix_in_redirects(self):
        """A host that never touched this feature must render byte-identical config to before it
        existed, so the feature cannot regress anyone who does not use it."""
        assert FMProxyConfig().https_suffix == ""
        assert FMProxyConfig(https_port=8443).https_suffix == ":8443"


class TestForwardedTrust:
    def test_the_scheme_table_exists_even_with_nothing_trusted(self, services, monkeypatch):
        """Every per-domain redirect references `$fm_client_scheme`; an absent definition makes
        nginx refuse to start, taking every bench on the host down."""
        _with_proxy(monkeypatch)

        assert services.set_forwarded_trust_conf() is True

        conf = (services.confd / PROXY_TRUST_CONF_FILENAME).read_text()
        assert "$fm_client_scheme" in conf
        assert "$fm_https_suffix" in conf

    def test_an_untrusted_peer_falls_back_to_the_real_connection(self):
        """The whole point: a forged X-Forwarded-Proto from a stranger must not decide the
        scheme, so the default arm is the connection nginx actually received."""
        conf = build_proxy_trust_conf(["10.0.0.0/8"], "")
        assert "default   $scheme;" in conf
        assert "    10.0.0.0/8 1;" in conf

    def test_a_trusted_front_makes_the_redirect_carry_no_port(self, services, monkeypatch):
        """fm's published port is what sits BEHIND the front, not what the browser reached. Naming
        it in a redirect sends the visitor somewhere only the front can reach -- and with
        `--bind 127.0.0.1`, somewhere nothing outside the machine can."""
        _with_proxy(monkeypatch, http_port=8080, https_port=8443)
        (services.confd / "fm-real-ip.conf").write_text(
            build_proxy_realip_conf(["10.1.0.1/32"], "X-Forwarded-For", recursive=True)
        )

        services.set_forwarded_trust_conf()

        assert 'default "";' in (services.confd / PROXY_TRUST_CONF_FILENAME).read_text()

    def test_with_nothing_trusted_the_redirect_names_fms_own_port(self, services, monkeypatch):
        """Nothing in front means fm IS the public endpoint, so the port it publishes on is the
        one a browser has to be sent to."""
        _with_proxy(monkeypatch, http_port=8080, https_port=8443)

        services.set_forwarded_trust_conf()

        assert 'default ":8443";' in (services.confd / PROXY_TRUST_CONF_FILENAME).read_text()

    def test_ranges_are_matched_with_geo_not_map(self):
        """`map` matches exact strings and regexes, never CIDRs -- a CIDR in a map silently
        matches nothing, which reads as "trust configured" while trusting no one."""
        conf = build_proxy_trust_conf(["203.0.113.0/24"], "")

        assert conf.count("geo $realip_remote_addr") == 1
        assert "map $realip_remote_addr" not in conf

    def test_the_peer_is_read_before_real_ip_rewrites_it(self):
        """`$remote_addr` becomes the CLIENT once set_real_ip_from applies, so keying trust on it
        would ask whether the visitor is trusted instead of whether the front is."""
        assert "$realip_remote_addr" in build_proxy_trust_conf(["10.0.0.0/8"], "")

    def test_gunicorn_trust_follows_the_trusted_set(self, services, monkeypatch):
        """TRUST_DOWNSTREAM_PROXY passes a client-supplied scheme inward. With nothing in front,
        fm knows the scheme from the connection, so believing a stranger only loses information."""
        _with_proxy(monkeypatch)

        assert services.apply_forwarded_trust_env() is True
        assert services.compose_file_manager.yml["services"]["nginx-proxy"]["environment"] == {
            "TRUST_DOWNSTREAM_PROXY": "false"
        }

        (services.confd / "fm-real-ip.conf").write_text(
            build_proxy_realip_conf(["127.0.0.1/32"], "X-Forwarded-For", recursive=True)
        )

        assert services.apply_forwarded_trust_env() is True
        assert services.compose_file_manager.yml["services"]["nginx-proxy"]["environment"] == {
            "TRUST_DOWNSTREAM_PROXY": "true"
        }

    def test_a_foreign_conf_grants_no_trust(self, tmp_path):
        """Same ownership rule every fm-managed nginx file follows: a hand-written file of that
        name is not fm's, and must not be read as an fm trust grant."""
        (tmp_path / "fm-real-ip.conf").write_text("set_real_ip_from 10.0.0.0/8;\n")

        assert trusted_ranges(tmp_path) == []

    def test_no_conf_at_all_is_no_trust(self, tmp_path):
        assert trusted_ranges(Path(tmp_path) / "missing") == []


class TestPortConflictRefusal:
    def test_a_busy_port_names_the_command_that_fixes_it(self):
        """The daemon's own sentence names no fix, and the fix is a command that must run BEFORE
        the install it unblocks -- not something an operator finds by retrying."""
        hint = _port_hint(Exception("Bind for 0.0.0.0:80 failed: port is already allocated"))

        assert "fm services ports" in hint

    def test_an_unrelated_failure_gets_no_port_advice(self):
        assert _port_hint(Exception("Pool overlaps with other one on this address space")) == ""


class TestSharedNetworks:
    """Both shared networks are infrastructure every bench compose declares `external`, so fm has
    to materialise them itself rather than rely on a service happening to attach to one."""

    @pytest.fixture
    def manager(self, services, monkeypatch):
        config = MagicMock()
        config.network.subnet_cidr = "10.1.0.0/16"
        config.network.backend_subnet_cidr = "10.2.0.0/16"
        monkeypatch.setattr(
            "frappe_manager.services_manager.services.FMConfigManager.import_from_toml",
            lambda *a, **k: config,
        )
        services.docker_client = MagicMock()
        services.docker_client.network_create.return_value = True
        return services

    def test_a_missing_backend_network_is_created(self, manager):
        """The bug: with both database services switched off, nothing attached to the backend
        network, so `compose up` never created it and the first `fm create` died on
        "declared as external, but could not be found"."""
        manager.docker_client.network_ls.return_value = ["fm-frontend-network", "bridge"]

        assert manager.ensure_shared_networks() == ["fm-backend-network"]
        manager.docker_client.network_create.assert_called_once_with(
            "fm-backend-network", "10.2.0.0/16", labels={"com.docker.compose.network": "backend-network"}
        )

    def test_a_created_network_carries_the_label_compose_demands(self, manager):
        """Without `com.docker.compose.network=<compose key>` docker compose refuses the network
        it declared itself ("was found but has incorrect label"), so the whole stack fails to
        start -- a worse failure than the one this fixes."""
        manager.docker_client.network_ls.return_value = []

        manager.ensure_shared_networks()

        labels = [call.kwargs["labels"] for call in manager.docker_client.network_create.call_args_list]
        assert labels == [
            {"com.docker.compose.network": "frontend-network"},
            {"com.docker.compose.network": "backend-network"},
        ]

    def test_existing_networks_are_left_alone(self, manager):
        manager.docker_client.network_ls.return_value = ["fm-frontend-network", "fm-backend-network"]

        assert manager.ensure_shared_networks() == []
        manager.docker_client.network_create.assert_not_called()

    def test_no_recorded_subnet_creates_nothing(self, services, monkeypatch):
        """A network created without the subnet the compose file declares would be adopted at the
        wrong range, which is harder to unpick than the missing network."""
        config = MagicMock()
        config.network.subnet_cidr = None
        config.network.backend_subnet_cidr = None
        monkeypatch.setattr(
            "frappe_manager.services_manager.services.FMConfigManager.import_from_toml",
            lambda *a, **k: config,
        )
        services.docker_client = MagicMock()
        services.docker_client.network_ls.return_value = []

        assert services.ensure_shared_networks() == []
        services.docker_client.network_create.assert_not_called()
