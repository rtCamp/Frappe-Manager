"""``parse_docker_volume`` only recognises a compose volume entry that has a mapping.

``ComposeFile.get_service_volumes`` feeds every string from a service's ``volumes:``
list through this parser and hands the results to ``set_service_volumes``, which
stringifies them back into the compose file. An entry with no ``:`` is not a mapping
(an anonymous volume such as ``- /var/lib/mysql``), and the parser refuses it by
returning None rather than inventing ``host == container``: inventing one would rewrite
the user's compose entry into a bind mount of the container path onto itself.

Also pinned: which side of the colon becomes host vs container, and that a name matching
a top-level ``volumes:`` key is classified as a named volume instead of a bind mount --
that classification is what decides whether fm treats the path as a host directory.
"""

from pathlib import Path

import pytest

from frappe_manager.docker import DockerVolumeType
from frappe_manager.utils.site import parse_docker_volume

COMPOSE_PATH = Path("/benches/mybench/docker-compose.yml")


def test_entry_without_a_mapping_is_not_a_volume_mount():
    assert parse_docker_volume("mydata", {}, COMPOSE_PATH) is None
    assert parse_docker_volume("/var/lib/mysql", {}, COMPOSE_PATH) is None


def test_empty_entry_is_not_a_volume_mount():
    assert parse_docker_volume("", {}, COMPOSE_PATH) is None


def test_host_and_container_sides_are_read_in_compose_order():
    volume = parse_docker_volume("/host/side:/container/side", {}, COMPOSE_PATH)

    assert volume is not None
    assert volume.host == Path("/host/side")
    assert volume.container == Path("/container/side")
    assert volume.type == DockerVolumeType.bind


def test_a_name_declared_at_the_compose_root_is_a_named_volume():
    volume = parse_docker_volume("mydata:/var/lib/mysql", {"mydata": None}, COMPOSE_PATH)

    assert volume is not None
    assert volume.type == DockerVolumeType.volume
    assert volume.host == "mydata"


@pytest.mark.parametrize(("mode", "expected_read_only"), [("rw", False), ("ro", True)])
def test_a_trailing_access_mode_does_not_shift_the_mapping(mode, expected_read_only):
    volume = parse_docker_volume(f"/host/side:/container/side:{mode}", {}, COMPOSE_PATH)

    assert volume is not None
    assert volume.host == Path("/host/side")
    assert volume.container == Path("/container/side")
    assert volume.read_only is expected_read_only


def test_a_ro_mode_round_trips_back_through_str():
    """A CA bundle (or any bind meant to be read-only) must survive being read off disk and
    written back out on every compose regeneration -- see TestCaTrustIdempotency in
    tests/unit/site_manager/test_bench_docker_supervisor_contract.py for the end-to-end case."""
    volume = parse_docker_volume("/host/side:/container/side:ro", {}, COMPOSE_PATH)

    assert volume is not None
    assert str(volume) == "/host/side:/container/side:ro"


def test_a_bare_mapping_defaults_to_read_write():
    volume = parse_docker_volume("/host/side:/container/side", {}, COMPOSE_PATH)

    assert volume is not None
    assert volume.read_only is False
    assert str(volume) == "/host/side:/container/side"
