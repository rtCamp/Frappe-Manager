"""`get_service_volumes` preserves the file's order while still deduping.

Both properties are load-bearing and they used to be in conflict. The dedupe is required: a
deploy re-pin reads the volumes, filters them, appends the managed binds and writes them back, and
without dedupe the data binds pile up (`test_image_mounts_idempotent.py` pins that). It was
implemented with a `set`, which deduped and discarded ORDER as a side effect.

Because every writer round-trips through this getter, each regeneration rewrote the volume list in
a fresh arbitrary order. That changed docker's service config hash, so the next plain
`compose up` recreated a container nobody had asked to touch -- and the restart was attributed to
whichever command happened to run next. Measured on a live bench: three consecutive no-op
regenerations produced three different orders and three different config hashes.

There was also no fixed point to converge on. Each run's set was built from the previous run's
output order, so even with PYTHONHASHSEED pinned the result alternated between two orders forever
rather than settling. That is the property the round-trip test below defends, and it is why
asserting "the order is stable" is not the same as asserting "the order equals the input".
"""

import pytest

from frappe_manager.docker import ComposeFile

pytestmark = pytest.mark.timeout(15)

COMPOSE = """\
services:
  nginx:
    image: nginx:1
    volumes:
      - ./configs/nginx/conf:/etc/nginx
      - ./configs/nginx/logs:/var/log/nginx
      - ./configs/nginx/cache:/var/cache/nginx
      - ./configs/nginx/html:/usr/share/nginx/html
      - fm-sockets:/fm-sockets
volumes:
  fm-sockets:
"""


@pytest.fixture
def cfm(tmp_path):
    path = tmp_path / "docker-compose.yml"
    path.write_text(COMPOSE)
    return ComposeFile(path)


def test_the_order_is_the_order_the_file_lists(cfm):
    assert [str(v) for v in cfm.get_service_volumes("nginx")] == [
        "./configs/nginx/conf:/etc/nginx",
        "./configs/nginx/logs:/var/log/nginx",
        "./configs/nginx/cache:/var/cache/nginx",
        "./configs/nginx/html:/usr/share/nginx/html",
        "fm-sockets:/fm-sockets",
    ]


def test_duplicates_are_still_collapsed(cfm):
    """The reason the set existed. A re-pin appends managed binds to what it read, so a getter
    that returns duplicates turns every deploy into a bind pile-up."""
    cfm.yml["services"]["nginx"]["volumes"].append("./configs/nginx/conf:/etc/nginx")

    volumes = [str(v) for v in cfm.get_service_volumes("nginx")]

    assert volumes.count("./configs/nginx/conf:/etc/nginx") == 1
    assert len(volumes) == 5


def test_a_duplicate_keeps_its_first_position(cfm):
    """First-seen wins, so a stray duplicate cannot reorder the entries around it."""
    cfm.yml["services"]["nginx"]["volumes"].insert(1, "fm-sockets:/fm-sockets")

    assert [str(v) for v in cfm.get_service_volumes("nginx")][:2] == [
        "./configs/nginx/conf:/etc/nginx",
        "fm-sockets:/fm-sockets",
    ]


def test_a_read_write_round_trip_is_a_fixed_point(cfm):
    """THE regression. Repeated identical regenerations must converge, not cycle: docker hashes
    the rendered service, so a list that reorders on every write marks the container dirty for
    the next `compose up` even when the bench asked for no change at all."""
    renders = []
    for _ in range(5):
        cfm.set_service_volumes("nginx", cfm.get_service_volumes("nginx"))
        renders.append(list(cfm.yml["services"]["nginx"]["volumes"]))

    assert all(render == renders[0] for render in renders)
    # And the fixed point is the FILE's order, not some arbitrary order it happens to settle on.
    assert renders[0] == [
        "./configs/nginx/conf:/etc/nginx",
        "./configs/nginx/logs:/var/log/nginx",
        "./configs/nginx/cache:/var/cache/nginx",
        "./configs/nginx/html:/usr/share/nginx/html",
        "fm-sockets:/fm-sockets",
    ]


def test_the_round_trip_does_not_drop_or_invent_a_mount(cfm):
    before = [str(v) for v in cfm.get_service_volumes("nginx")]

    cfm.set_service_volumes("nginx", cfm.get_service_volumes("nginx"))

    assert [str(v) for v in cfm.get_service_volumes("nginx")] == before
