"""Docker subnets are parsed non-strictly, so a gateway-style CIDR still counts as used.

`docker network inspect` reports the IPAM `Subnet` verbatim, and a network created with
`--subnet 10.1.0.1/16` (host bits set) is reported that way. Parsing those strictly would
raise ValueError, the `except ValueError: continue` would swallow it, and the network
would vanish from the "used" list -- so `find_available_subnet` would happily hand out a
/16 that already exists and bench creation would fail on an overlap.

These tests pin that host-bits CIDRs are kept (masked to their network address) while
genuinely unparseable values are still dropped.
"""

import ipaddress
from unittest.mock import MagicMock

import pytest

from frappe_manager.utils import network


def _docker(inspects: dict) -> MagicMock:
    docker = MagicMock()
    docker.network_ls.return_value = list(inspects)
    docker.network_inspect.side_effect = lambda name, **_kwargs: inspects.get(name, [])
    return docker


@pytest.mark.unit
class TestSubnetsWithHostBitsSet:
    def test_cidr_with_host_bits_is_kept_as_its_network(self):
        docker = _docker({"bridge": [{"Subnet": "10.1.0.1/16"}]})

        assert network.get_docker_network_subnets(docker=docker) == [ipaddress.IPv4Network("10.1.0.0/16")]

    def test_such_a_network_still_blocks_the_preferred_subnet(self):
        """The end-to-end consequence: 10.1.0.0/16 must not be offered as free."""
        docker = _docker({"bridge": [{"Subnet": "10.1.0.1/16"}]})

        assert network.find_available_subnet(docker=docker) != ipaddress.IPv4Network("10.1.0.0/16")

    def test_unparseable_subnet_is_still_dropped(self):
        docker = _docker({"bridge": [{"Subnet": "10.1.0.0/33"}], "other": [{"Subnet": "10.2.0.0/16"}]})

        assert network.get_docker_network_subnets(docker=docker) == [ipaddress.IPv4Network("10.2.0.0/16")]
