"""One server block per site, so a site can carry nginx config its neighbours do not.

The bench nginx used to render ONE server block whose `server_name` listed every domain of every
site, with `include /etc/nginx/custom/*.conf` inside it. Anything an operator dropped in there
applied to the whole bench, and there was no place to put config for one site: `include` cannot be
made conditional on a variable, so per-site config genuinely needs per-site blocks.

These render the REAL template with `jinja2`, the same library the nginx image's entrypoint uses
(`Docker/nginx/entrypoint.sh` runs `jinja2 -f json /config/template.conf`), so a change to the
template is checked against the shape nginx will actually be handed. Syntax is verified separately
by `nginx -t`, which a unit test cannot do; what these pin is the structure.
"""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "Docker" / "nginx"

TWO_SITES = {
    "shop.localhost": "shop.localhost",
    "www.shop.example.com": "shop.localhost",
    "b.example.com": "b.example.com",
    "www.b.example.com": "b.example.com",
}


@pytest.fixture(scope="module")
def render():
    # autoescape stays off deliberately: the output is an nginx config, not HTML, and the
    # entrypoint's `jinja2 -f json` renders it the same way. Escaping would corrupt it.
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), keep_trailing_newline=True)  # noqa: S701
    template = env.get_template("template.conf")
    return lambda site_map: template.render(site_map=site_map)


def _blocks(conf: str) -> list[str]:
    """The rendered server blocks, split on the top-level `server {` lines."""
    return list(conf.split("\nserver {")[1:])


def test_each_site_gets_its_own_server_block(render):
    conf = render(TWO_SITES)

    assert len(_blocks(conf)) == 2


def test_a_block_carries_its_own_site_and_that_sites_aliases_only(render):
    """An alias is a hostname OF a site, so it belongs in that site's block. Putting every domain
    in every block would defeat the whole point: nginx picks a block by Host."""
    first, second = _blocks(render(TWO_SITES))

    assert "server_name shop.localhost www.shop.example.com ;" in first
    assert "b.example.com" not in first.split("server_name")[1].split(";")[0]
    assert "server_name b.example.com www.b.example.com ;" in second


def test_the_primary_block_is_first_so_it_stays_the_default_server(render):
    """nginx serves a Host matching no `server_name` from the FIRST block. The `map` default names
    the same site, so an unknown Host behaves as it did when one block answered for everything."""
    conf = render(TWO_SITES)

    assert conf.index("server_name shop.localhost") < conf.index("server_name b.example.com")
    assert "default shop.localhost;" in conf


def test_every_block_keeps_the_bench_wide_drop_ins(render):
    """Flat `custom/*.conf` files predate this and must keep applying everywhere. A `*.conf` glob
    does not match directories, so the existing files are untouched by the per-site layout and no
    migration is needed."""
    for block in _blocks(render(TWO_SITES)):
        assert "include /etc/nginx/custom/*.conf;" in block


def test_each_block_includes_only_its_own_sites_drop_ins(render):
    first, second = _blocks(render(TWO_SITES))

    assert "include /etc/nginx/custom/shop.localhost/*.conf;" in first
    assert "include /etc/nginx/custom/b.example.com/*.conf;" not in first
    assert "include /etc/nginx/custom/b.example.com/*.conf;" in second


def test_a_single_site_bench_renders_one_block_as_before(render):
    conf = render({"shop.localhost": "shop.localhost"})

    assert len(_blocks(conf)) == 1
    assert "server_name shop.localhost ;" in conf


def test_a_bench_with_no_sites_renders_no_server_block_and_no_map(render):
    """A `--bench-only` bench has an empty site map. The old template rendered `server_name ;` and
    `default ;` for it, neither of which nginx parses, so its nginx could not start at all. Nothing
    to serve means nothing to declare.
    """
    conf = render({})

    assert _blocks(conf) == []
    assert "map $host $frappe_site_name" not in conf
    assert "server_name" not in conf


def test_the_site_map_still_covers_every_domain(render):
    """`$frappe_site_name` is read by `try_files` and the upstream site header in every block, and
    it is still resolved by Host rather than set per block, so the map has to list them all."""
    conf = render(TWO_SITES)

    for domain, site in TWO_SITES.items():
        assert f"\t{domain} {site};" in conf
