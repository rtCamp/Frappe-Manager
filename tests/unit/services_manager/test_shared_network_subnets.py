"""Both shared networks must land on ranges this host does not already use.

Docker refuses a network whose pool overlaps an existing one ("invalid pool request: Pool
overlaps with other one on this address space") and fails the whole `compose up`, so a subnet
chosen for one network and hardcoded for the other breaks the FIRST command on a host that
happens to own that range.
"""

import ipaddress
from unittest import mock

import pytest

from frappe_manager.services_manager.services import ServicesManager


def compose_skeleton() -> dict:
    return {
        "services": {"nginx-proxy": {"networks": {"frontend-network": {}}}},
        "networks": {
            "frontend-network": {"ipam": {"config": [{"subnet": "10.1.0.0/16"}]}},
            "backend-network": {"ipam": {"config": [{"subnet": "10.2.0.0/16"}]}},
        },
    }


def subnets_after_create(tmp_path, host_subnets: list[str], recorded: dict | None = None) -> dict:
    """Run the network sizing against a host owning `host_subnets`; return both chosen subnets."""
    manager = ServicesManager(path=tmp_path / "services", output_handler=mock.MagicMock())
    manager.compose_file_manager = mock.MagicMock()
    manager.compose_file_manager.yml = compose_skeleton()
    manager.docker_client = mock.MagicMock()

    fm_config = mock.MagicMock()
    fm_config.network.subnet_cidr = (recorded or {}).get("subnet_cidr")
    fm_config.network.proxy_ip = (recorded or {}).get("proxy_ip")
    fm_config.network.backend_subnet_cidr = (recorded or {}).get("backend_subnet_cidr")
    fm_config.network.configured = bool(fm_config.network.subnet_cidr and fm_config.network.proxy_ip)

    used = [ipaddress.IPv4Network(s) for s in host_subnets]
    with (
        mock.patch("frappe_manager.services_manager.services.FMConfigManager") as config_cls,
        mock.patch("frappe_manager.services_manager.services.detect_running_network", return_value=None),
        mock.patch("frappe_manager.services_manager.services.get_docker_network_subnets", return_value=list(used)),
        mock.patch("frappe_manager.services_manager.services.compute_network_config") as compute,
    ):
        config_cls.import_from_toml.return_value = fm_config
        compute.side_effect = lambda cidr, *a, **k: {"subnet_cidr": cidr, "proxy_ip": str(ipaddress.IPv4Network(cidr)[2])}
        manager.configure_shared_networks()

    networks = manager.compose_file_manager.yml["networks"]
    return {name: networks[name]["ipam"]["config"][0]["subnet"] for name in ("frontend-network", "backend-network")}


@pytest.mark.unit
class TestBothNetworksAvoidHostRanges:
    def test_backend_moves_off_a_range_the_host_already_owns(self, tmp_path):
        """The reported failure: 10.1 and 10.2 taken, the frontend moved to 10.3 and the backend
        stayed on the template's 10.2, so `compose up` died on the first `fm list`."""
        chosen = subnets_after_create(tmp_path, ["10.1.0.0/16", "10.2.0.0/16"])

        taken = [ipaddress.IPv4Network("10.1.0.0/16"), ipaddress.IPv4Network("10.2.0.0/16")]
        for name, cidr in chosen.items():
            assert not any(ipaddress.IPv4Network(cidr).overlaps(t) for t in taken), f"{name} on {cidr}"

    def test_the_two_networks_never_get_the_same_range(self, tmp_path):
        """Docker reports a self-collision the same way as a foreign one, and the frontend's
        range is not yet created when the backend is chosen, so it cannot be discovered."""
        chosen = subnets_after_create(tmp_path, ["10.1.0.0/16"])

        assert chosen["frontend-network"] != chosen["backend-network"]

    def test_a_recorded_backend_subnet_is_reused_not_reassigned(self, tmp_path):
        """An existing install's networks already exist at recorded ranges; re-creating the
        services directory must not renumber them under the running containers."""
        chosen = subnets_after_create(
            tmp_path,
            ["10.1.0.0/16"],
            recorded={"subnet_cidr": "10.5.0.0/16", "proxy_ip": "10.5.0.2", "backend_subnet_cidr": "10.6.0.0/16"},
        )

        assert chosen == {"frontend-network": "10.5.0.0/16", "backend-network": "10.6.0.0/16"}
