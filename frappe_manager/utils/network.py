"""
Docker network utility functions for CIDR selection and IP allocation.

Provides helpers to automatically find an available subnet and proxy IP
for the global-frontend-network, avoiding conflicts with existing Docker networks.
"""

import ipaddress
import sys
from typing import Tuple

from frappe_manager.docker.docker_client import DockerClient

PREFERRED_SUBNET = ipaddress.IPv4Network("10.1.0.0/16")
DEFAULT_PROXY_NAME = "fm_global-nginx-proxy"
DEFAULT_NETWORK_NAME = "fm-global-frontend-network"


def get_docker_network_subnets(docker: DockerClient | None = None) -> list[ipaddress.IPv4Network]:
    """Scan all Docker networks and return their IPv4 subnets."""
    subnets: list[ipaddress.IPv4Network] = []
    if docker is None:
        docker = DockerClient()

    names = docker.network_ls()
    for name in names:
        configs = docker.network_inspect(name)
        if not configs or not isinstance(configs, list):
            continue
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            subnet_str = cfg.get("Subnet")
            if subnet_str:
                try:
                    subnets.append(ipaddress.IPv4Network(subnet_str, strict=False))
                except ValueError:
                    continue

    return subnets


def find_available_subnet(
    used_subnets: list[ipaddress.IPv4Network] | None = None,
    docker: DockerClient | None = None,
) -> ipaddress.IPv4Network:
    """
    Find a free /16 in 10.0.0.0/8 that doesn't overlap with any existing Docker networks.

    Tries 10.1.0.0/16 first (most common), then scans 10.2.0.0/16 through 10.255.0.0/16.
    """
    if used_subnets is None:
        used_subnets = get_docker_network_subnets(docker=docker)

    if not any(PREFERRED_SUBNET.overlaps(u) for u in used_subnets):
        return PREFERRED_SUBNET

    for i in range(2, 256):
        candidate = ipaddress.IPv4Network(f"10.{i}.0.0/16")
        if not any(candidate.overlaps(u) for u in used_subnets):
            return candidate

    raise RuntimeError("No free /16 subnet found in 10.0.0.0/8")


def get_ips_in_use_on_network(
    network_name: str = DEFAULT_NETWORK_NAME,
    docker: DockerClient | None = None,
) -> set[str]:
    """Return the set of IPv4 addresses currently assigned on a Docker network."""
    used: set[str] = set()
    if docker is None:
        docker = DockerClient()

    containers = docker.network_inspect(network_name, format="{{json .Containers}}")
    if not containers:
        return used

    for c in containers.values():
        ip = c.get("IPv4Address", "")
        if ip:
            used.add(ip.split("/")[0])

    return used


def pick_proxy_ip(
    subnet_cidr: str,
    network_name: str = DEFAULT_NETWORK_NAME,
    docker: DockerClient | None = None,
) -> str:
    """
    Pick the first free IP address after the gateway (.1) on the given network.
    """
    net = ipaddress.IPv4Network(subnet_cidr, strict=False)
    used = get_ips_in_use_on_network(network_name, docker=docker)

    for offset in range(2, 255):
        candidate = str(net.network_address + offset)
        if candidate not in used:
            return candidate

    raise RuntimeError(f"No free IP address in {subnet_cidr} for proxy")


def detect_running_network(
    network_name: str = DEFAULT_NETWORK_NAME,
    proxy_container: str = DEFAULT_PROXY_NAME,
    docker: DockerClient | None = None,
) -> dict | None:
    """
    Detect the actual subnet and proxy IP from a running Docker network.

    Returns dict with 'subnet_cidr' and 'proxy_ip' if the network exists,
    or None if the network doesn't exist.
    """
    if docker is None:
        docker = DockerClient()

    configs = docker.network_inspect(network_name)
    if not configs or not isinstance(configs, list):
        return None

    subnet_cidr = configs[0].get("Subnet") if isinstance(configs[0], dict) else None
    if not subnet_cidr:
        return None

    # Get proxy container's IP on this network
    proxy_ip = ""
    net_info = docker.container_inspect(proxy_container, format="{{json .NetworkSettings.Networks}}")
    net_info = net_info or {}
    for net_name, cfg in net_info.items():
        if net_name == network_name:
            proxy_ip = cfg.get("IPAddress", "")
            break

    return {"subnet_cidr": subnet_cidr, "proxy_ip": proxy_ip}


def compute_network_config(
    subnet_cidr: str,
    network_name: str = DEFAULT_NETWORK_NAME,
    docker: DockerClient | None = None,
) -> dict:
    """Compute the full network config dict (subnet + IP) given a CIDR."""
    return {
        "subnet_cidr": subnet_cidr,
        "proxy_ip": pick_proxy_ip(subnet_cidr, network_name, docker=docker),
    }


def get_platform() -> str:
    """Return whether we're on macOS ('osx') or Linux ('linux')."""
    if sys.platform == "darwin":
        return "osx"
    return "linux"
