"""
Docker network utility functions for CIDR selection and IP allocation.

Provides helpers to automatically find an available subnet and proxy IP
for the global-frontend-network, avoiding conflicts with existing Docker networks.
"""

import ipaddress
import json
import subprocess
import sys
from typing import Tuple

PREFERRED_SUBNET = ipaddress.IPv4Network("10.1.0.0/16")


def get_docker_network_subnets() -> list[ipaddress.IPv4Network]:
    """Scan all Docker networks and return their IPv4 subnets."""
    subnets: list[ipaddress.IPv4Network] = []

    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return subnets

    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue
        try:
            inspect = subprocess.run(
                ["docker", "network", "inspect", name, "--format", "{{json .IPAM.Config}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue

        if inspect.returncode != 0 or not inspect.stdout.strip():
            continue
        raw = inspect.stdout.strip()
        if raw == "null":
            continue
        try:
            configs = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not configs:
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
) -> ipaddress.IPv4Network:
    """
    Find a free /16 in 10.0.0.0/8 that doesn't overlap with any existing Docker networks.

    Tries 10.1.0.0/16 first (most common), then scans 10.2.0.0/16 through 10.255.0.0/16.
    """
    if used_subnets is None:
        used_subnets = get_docker_network_subnets()

    # Try preferred subnet first
    if not any(PREFERRED_SUBNET.overlaps(u) for u in used_subnets):
        return PREFERRED_SUBNET

    # Scan remaining /16s in 10.0.0.0/8
    for i in range(2, 256):
        candidate = ipaddress.IPv4Network(f"10.{i}.0.0/16")
        if not any(candidate.overlaps(u) for u in used_subnets):
            return candidate

    raise RuntimeError("No free /16 subnet found in 10.0.0.0/8")


def get_ips_in_use_on_network(network_name: str) -> set[str]:
    """Return the set of IPv4 addresses currently assigned on a Docker network."""
    used: set[str] = set()

    try:
        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                network_name,
                "--format",
                "{{json .Containers}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return used

    if result.returncode != 0 or not result.stdout.strip():
        return used

    try:
        containers = json.loads(result.stdout)
    except json.JSONDecodeError:
        return used

    if not containers:
        return used

    for c in containers.values():
        ip = c.get("IPv4Address", "")
        if ip:
            used.add(ip.split("/")[0])

    return used


def pick_proxy_ip(subnet_cidr: str, network_name: str = "fm-global-frontend-network") -> str:
    """
    Pick the first free IP address after the gateway (.1) on the given network.
    """
    net = ipaddress.IPv4Network(subnet_cidr, strict=False)
    gateway = net.network_address + 1  # Docker reserves .1 as gateway
    used = get_ips_in_use_on_network(network_name)

    for offset in range(2, 255):
        candidate = str(net.network_address + offset)
        if candidate not in used:
            return candidate

    raise RuntimeError(f"No free IP address in {subnet_cidr} for proxy")


def compute_network_config(subnet_cidr: str, network_name: str = "fm-global-frontend-network") -> dict:
    """
    Compute the full network config dict (subnet + IP) given a CIDR.
    """
    return {
        "subnet_cidr": subnet_cidr,
        "proxy_ip": pick_proxy_ip(subnet_cidr, network_name),
    }


def get_platform() -> str:
    """Return whether we're on macOS ('osx') or Linux ('linux')."""
    if sys.platform == "darwin":
        return "osx"
    return "linux"
