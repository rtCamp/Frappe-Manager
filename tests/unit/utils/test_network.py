"""Unit tests for Docker network utility helpers."""

import ipaddress
from unittest.mock import MagicMock

import pytest

from frappe_manager.utils import network


def make_docker(networks=None, inspects=None, containers=None):
    """Build a mock DockerClient.

    networks: names returned by network_ls()
    inspects: name -> IPAM config list (default network_inspect return)
    containers: name -> Containers dict (network_inspect with Containers format)
    """
    docker = MagicMock()
    docker.network_ls.return_value = networks or []

    def network_inspect(name, **kwargs):
        fmt = kwargs.get("format", "")
        if "Containers" in fmt:
            return (containers or {}).get(name, {})
        return (inspects or {}).get(name, [])

    docker.network_inspect.side_effect = network_inspect
    return docker


@pytest.mark.unit
class TestGetDockerNetworkSubnets:
    def test_parses_subnets(self):
        docker = make_docker(
            networks=["a", "b"],
            inspects={
                "a": [{"Subnet": "10.1.0.0/16"}],
                "b": [{"Subnet": "172.18.0.0/16"}],
            },
        )
        subnets = network.get_docker_network_subnets(docker=docker)
        assert ipaddress.IPv4Network("10.1.0.0/16") in subnets
        assert ipaddress.IPv4Network("172.18.0.0/16") in subnets

    def test_skips_networks_without_subnet(self):
        docker = make_docker(networks=["a"], inspects={"a": [{}]})
        assert network.get_docker_network_subnets(docker=docker) == []

    def test_skips_invalid_subnet(self):
        docker = make_docker(networks=["a"], inspects={"a": [{"Subnet": "not-a-subnet"}]})
        assert network.get_docker_network_subnets(docker=docker) == []


@pytest.mark.unit
class TestFindAvailableSubnet:
    def test_prefers_10_1_when_free(self):
        assert network.find_available_subnet(used_subnets=[]) == ipaddress.IPv4Network("10.1.0.0/16")

    def test_skips_conflicting_preferred(self):
        used = [ipaddress.IPv4Network("10.1.0.0/16")]
        assert network.find_available_subnet(used_subnets=used) == ipaddress.IPv4Network("10.2.0.0/16")

    def test_finds_next_free_when_multiple_used(self):
        used = [ipaddress.IPv4Network("10.1.0.0/16"), ipaddress.IPv4Network("10.2.0.0/16")]
        assert network.find_available_subnet(used_subnets=used) == ipaddress.IPv4Network("10.3.0.0/16")

    def test_partial_overlap_counts_as_conflict(self):
        # A /24 inside 10.1 still conflicts with the /16 preferred subnet.
        used = [ipaddress.IPv4Network("10.1.5.0/24")]
        assert network.find_available_subnet(used_subnets=used) != ipaddress.IPv4Network("10.1.0.0/16")


@pytest.mark.unit
class TestGetIpsInUse:
    def test_extracts_ips_strips_prefix(self):
        docker = make_docker(
            containers={
                network.DEFAULT_NETWORK_NAME: {
                    "c1": {"IPv4Address": "10.1.0.2/16"},
                    "c2": {"IPv4Address": "10.1.0.3/16"},
                }
            }
        )
        used = network.get_ips_in_use_on_network(docker=docker)
        assert used == {"10.1.0.2", "10.1.0.3"}

    def test_empty_when_no_containers(self):
        docker = make_docker(containers={})
        assert network.get_ips_in_use_on_network(docker=docker) == set()


@pytest.mark.unit
class TestPickProxyIp:
    def test_picks_dot_two_when_free(self):
        docker = make_docker(containers={network.DEFAULT_NETWORK_NAME: {}})
        assert network.pick_proxy_ip("10.1.0.0/16", docker=docker) == "10.1.0.2"

    def test_skips_used_ips(self):
        docker = make_docker(
            containers={network.DEFAULT_NETWORK_NAME: {"c": {"IPv4Address": "10.1.0.2/16"}}}
        )
        assert network.pick_proxy_ip("10.1.0.0/16", docker=docker) == "10.1.0.3"


@pytest.mark.unit
class TestGetProxyIpOnFrontend:
    def test_returns_ip_on_matching_network(self):
        docker = MagicMock()
        docker.container_inspect.return_value = {
            network.DEFAULT_NETWORK_NAME: {"IPAddress": "10.1.0.2"},
            "bridge": {"IPAddress": "172.17.0.2"},
        }
        assert network.get_proxy_ip_on_frontend(docker=docker) == "10.1.0.2"

    def test_empty_when_not_on_network(self):
        docker = MagicMock()
        docker.container_inspect.return_value = {"bridge": {"IPAddress": "172.17.0.2"}}
        assert network.get_proxy_ip_on_frontend(docker=docker) == ""

    def test_empty_on_inspect_error(self):
        docker = MagicMock()
        docker.container_inspect.side_effect = RuntimeError("boom")
        assert network.get_proxy_ip_on_frontend(docker=docker) == ""


@pytest.mark.unit
class TestDetectRunningNetwork:
    def test_returns_none_when_no_network(self):
        docker = make_docker()
        assert network.detect_running_network(docker=docker) is None

    def test_returns_subnet_and_proxy_ip(self):
        docker = MagicMock()
        docker.network_inspect.return_value = [{"Subnet": "10.1.0.0/16"}]
        docker.container_inspect.return_value = {network.DEFAULT_NETWORK_NAME: {"IPAddress": "10.1.0.2"}}
        assert network.detect_running_network(docker=docker) == {
            "subnet_cidr": "10.1.0.0/16",
            "proxy_ip": "10.1.0.2",
        }

    def test_proxy_ip_empty_when_proxy_absent(self):
        docker = MagicMock()
        docker.network_inspect.return_value = [{"Subnet": "10.1.0.0/16"}]
        docker.container_inspect.return_value = {}
        assert network.detect_running_network(docker=docker) == {
            "subnet_cidr": "10.1.0.0/16",
            "proxy_ip": "",
        }


@pytest.mark.unit
class TestComputeNetworkConfig:
    def test_compute_network_config(self):
        docker = make_docker(containers={network.DEFAULT_NETWORK_NAME: {}})
        assert network.compute_network_config("10.1.0.0/16", docker=docker) == {
            "subnet_cidr": "10.1.0.0/16",
            "proxy_ip": "10.1.0.2",
        }
