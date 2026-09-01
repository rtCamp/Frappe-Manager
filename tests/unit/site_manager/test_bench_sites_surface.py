"""What `fm list` and `fm info` say about the SITES a bench serves.

A bench is a directory and a compose project; the sites it serves are recorded in `[sites]` and
each lives in `sites/<site>/`. Both surfaces used to talk as though there were exactly one, and
both had to learn four things at once:

- `[sites]` is the RECORD. An empty table is a bench with no site in it (`--bench-only`, or the
  last site deleted), not a bench serving one site named after itself, which is what
  `site_names` falls back to mid-create.
- enumeration must never depend on selection. `primary_site` RAISES when several sites are
  recorded and none is the bench's own, so a listing that reached for it would drop the row (or
  the whole card) of a bench whose only fault is being multi-site. `fm info` on exactly that
  bench is how an operator finds out WHY a bench-scoped command refuses.
- absence of `[sites."<site>".database]` is the only switch between "on the global-db container
  fm owns" and "on a server fm does not own", whose host the card has to name.
- a site directory on disk that `[sites]` does not record is REPORTED and never acted on.

The configs here are REAL `BenchConfig` objects, so `site_names`, `_primary_site_or_none` and
`get_database_config` answer the way the model does rather than the way a stand-in was told to.
The bench name and the site name are never the same string: a fixture that gives one string to
both roles cannot catch code that reaches for a site under the bench's name.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.output_manager import railcard
from frappe_manager.site_manager.bench_config import (
    BenchConfig,
    DatabaseConfig,
    FMBenchEnvType,
    RestartPolicyEnum,
    SiteConfig,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.modules.bench_info import BenchInfo

# What `fm create shop` makes: a bench called `shop`, which is not a hostname, serving a site
# called `shop.localhost`, which is one. Never the same string.
BENCH = "shop"
SITE = "shop.localhost"
OTHER = "admin.example.com"
# Two sites, neither named after the bench: this is the bench whose primary is AMBIGUOUS.
FOREIGN_A = "a.example.com"
FOREIGN_B = "b.example.com"
UNMANAGED = "test.localhost"
ROOT_PW = "rootpass"  # a literal at the call site would trip S106


def _external(host: str = "rds.internal", **over) -> DatabaseConfig:
    return DatabaseConfig(host=host, name="app_prod", **over)


def _config(tmp_path: Path, *, sites, name: str = BENCH, **over) -> BenchConfig:
    """A real bench config. `sites` maps a recorded site to its external database config, or to
    None for a site on the global-db container fm owns; `sites=None` is a bench with no site."""
    recorded = None if sites is None else {site: SiteConfig(database=db) for site, db in sites.items()}
    return BenchConfig(
        name=name,
        developer_mode=False,
        admin_tools=False,
        environment_type=FMBenchEnvType.prod,
        restart_policy=RestartPolicyEnum.unless_stopped,
        root_path=tmp_path / name / "bench_config.toml",
        sites=recorded,
        **over,
    )


class _CardSpy:
    """Stand-in for railcard.Card recording the facts and sections a view decided on."""

    made: ClassVar[list["_CardSpy"]] = []

    def __init__(self, name, meta, active, link=None):
        self.name, self.meta, self.active, self.link = name, meta, active, link
        self.rows: list[tuple[str, str, str]] = []
        _CardSpy.made.append(self)

    def fact(self, label, value):
        self.rows.append(("fact", label, value))
        return self

    def section(self, title):
        self.rows.append(("section", title, ""))
        return self

    def render(self):
        return f"<rendered {self.name}>"

    @property
    def facts(self) -> dict[str, str]:
        return {label: value for kind, label, value in self.rows if kind == "fact" and label}

    def labelled(self, label) -> list[str]:
        """All fact values under ``label``, including the continuation rows (label '')."""
        out, taking = [], False
        for kind, lab, value in self.rows:
            if kind != "fact":
                taking = False
                continue
            if lab == label:
                taking = True
                out.append(value)
            elif lab == "":
                if taking:
                    out.append(value)
            else:
                taking = False
        return out


@pytest.fixture
def card_spy(monkeypatch):
    _CardSpy.made = []
    monkeypatch.setattr(railcard, "Card", _CardSpy)
    monkeypatch.setattr(railcard, "cards", lambda items: items)
    return _CardSpy


# =============================================================================== fm list


def _bench_dir(root: Path, name: str = BENCH) -> Path:
    """A bench directory as `discover_benches` recognises one: it holds a compose file."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "docker-compose.yml").write_text("services: {}\n")
    return path


def _listable(path: Path, config: BenchConfig, *, running: bool = True):
    bench = MagicMock()
    bench.name = config.name
    bench.path = path
    bench.running = running
    bench.bench_config = config
    return bench


def _rows(tmp_path: Path, config: BenchConfig) -> dict[str, dict]:
    path = _bench_dir(tmp_path, config.name)
    service = BenchService(tmp_path, MagicMock(), output_handler=MagicMock())
    with patch.object(BenchService, "get_bench", return_value=_listable(path, config)):
        return {row["name"]: row for row in service.list_benches_data()}


def _list_card(tmp_path: Path, config: BenchConfig) -> _CardSpy:
    service = BenchService(tmp_path, MagicMock(), output_handler=MagicMock())
    rows = list(_rows(tmp_path, config).values())
    with patch.object(BenchService, "list_benches_data", return_value=rows):
        (card,) = service.list_benches_view()
    return card


def test_a_one_site_bench_names_its_site_in_the_row_and_on_the_card(tmp_path, card_spy):
    """The common case. The site's name is its domain and the second half of every `BENCH/SITE`
    address, so naming it is what lets an operator go from `fm list` to `fm shell shop/...`."""
    config = _config(tmp_path, sites={SITE: None})

    assert _rows(tmp_path, config)[BENCH]["sites"] == [SITE]
    assert _list_card(tmp_path, config).facts["sites"] == SITE


def test_a_two_site_bench_shows_both_sites_primary_first(tmp_path, card_spy):
    config = _config(tmp_path, sites={OTHER: None, SITE: None})

    # Primary first, not recorded order: `shop.localhost` is the site `fm create shop` made, and
    # it is the one every bench-scoped command means.
    assert _rows(tmp_path, config)[BENCH]["sites"] == [SITE, OTHER]
    assert _list_card(tmp_path, config).facts["sites"] == f"{SITE}, {OTHER}"


def test_a_bench_with_no_site_is_still_listed_and_says_it_has_none(tmp_path, card_spy):
    """`--bench-only`, or the last site deleted. An empty `[sites]` is a real state, and
    `site_names` would answer with the bench's own name here, listing a site that does not
    exist."""
    config = _config(tmp_path, sites=None)

    assert _rows(tmp_path, config)[BENCH]["sites"] == []
    assert _list_card(tmp_path, config).facts["sites"] == "[fm.muted]none[/fm.muted]"


def test_a_bench_whose_primary_is_ambiguous_is_still_listed_with_every_site(tmp_path, card_spy):
    """Two recorded sites, neither named after the bench: `primary_site` raises, and a row built
    on it would take the whole bench out of `fm list`, which is where an operator would look to
    see what is wrong with it."""
    config = _config(tmp_path, sites={FOREIGN_A: None, FOREIGN_B: None})

    assert config._primary_site_or_none() is None  # the state under test really is ambiguous
    assert _rows(tmp_path, config)[BENCH]["sites"] == [FOREIGN_A, FOREIGN_B]
    assert _list_card(tmp_path, config).facts["sites"] == f"{FOREIGN_A}, {FOREIGN_B}"


def test_the_card_names_three_sites_and_counts_the_rest(tmp_path, card_spy):
    """`fm list` is the overview: it stays scannable, and `fm info` and `--json` carry them all."""
    sites = {SITE: None, OTHER: None, "c.example.com": None, "d.example.com": None, "e.example.com": None}
    config = _config(tmp_path, sites=sites)

    assert len(_rows(tmp_path, config)[BENCH]["sites"]) == 5
    assert _list_card(tmp_path, config).facts["sites"] == f"{SITE}, {OTHER}, c.example.com [fm.muted]+2[/fm.muted]"


def test_a_bench_whose_config_is_unreadable_is_listed_without_claiming_a_site_count(tmp_path):
    """The bench most in need of being listed. Its sites are UNKNOWN, not zero: the row carries
    the error and no `sites` key at all, so no consumer can read a claim fm cannot make."""
    _bench_dir(tmp_path, BENCH)
    service = BenchService(tmp_path, MagicMock(), output_handler=MagicMock())
    with patch.object(BenchService, "get_bench", side_effect=ValueError("invalid toml")):
        (row,) = service.list_benches_data()

    assert row["name"] == BENCH
    assert "sites" not in row
    assert "invalid toml" in row["error"]


# =============================================================================== fm info


def _info(tmp_path: Path, config: BenchConfig, *, unmanaged=(), site_config=None, certificate=False) -> BenchInfo:
    """A BenchInfo over a real bench directory, with the live-container probes turned off.

    `site_config` writes `sites/<site>/site_config.json` for the sites named in it, which is where
    the admin password comes from when Frappe has written one.
    """
    path = _bench_dir(tmp_path, config.name)
    for site, content in (site_config or {}).items():
        site_dir = path / "workspace" / "frappe-bench" / "sites" / site
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "site_config.json").write_text(json.dumps(content))

    services = MagicMock()
    services.database_manager.database_server_info = SimpleNamespace(user="root", password=ROOT_PW, host="global-db")
    workers = MagicMock()
    workers.compose_file_manager.get_container_names.return_value = {}
    workers.docker_client.compose.get_all_services_status.return_value = []
    admin_tools = MagicMock()
    admin_tools.compose_file_manager.exists.return_value = False

    info = BenchInfo(
        bench_name=config.name,
        bench_path=path,
        bench_config=config,
        services=services,
        workers=workers,
        admin_tools=admin_tools,
        certificate_manager=MagicMock(),
        get_db_connection_info_fn=MagicMock(return_value={"name": "db", "password": "dbpass"}),
        has_certificate_fn=MagicMock(return_value=certificate),
        is_running_fn=MagicMock(return_value=True),
        get_services_running_status_fn=MagicMock(return_value={}),
        unmanaged_site_dirs_fn=MagicMock(return_value=list(unmanaged)),
        docker_client=None,
        output_handler=MagicMock(),
    )
    info.get_bench_apps = MagicMock(return_value=[])
    return info


def _info_card(tmp_path: Path, config: BenchConfig, **over) -> tuple[_CardSpy, BenchInfo]:
    info = _info(tmp_path, config, **over)
    info.display_info()
    (card,) = _CardSpy.made
    return card, info


def test_a_one_site_bench_prints_the_url_it_always_has_and_no_per_site_rows(tmp_path, card_spy):
    """The common case, and what every screenshot and doc shows: one site on fm's own global-db
    needs no per-site rows, because `url` already names it and its schema is in `access`."""
    config = _config(tmp_path, sites={SITE: None})
    card, info = _info_card(tmp_path, config, site_config={SITE: {}})

    assert card.facts["url"] == f"http://{SITE}"
    assert card.link == f"http://{SITE}"
    assert "sites" not in card.facts
    assert "unmanaged" not in card.facts
    info.output.print_data.assert_called_once_with(f"<rendered {BENCH}>")


def test_a_two_site_bench_lists_every_site_and_marks_the_primary(tmp_path, card_spy):
    config = _config(tmp_path, sites={SITE: None, OTHER: None})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}})

    assert card.labelled("sites") == [
        f"http://{SITE}  [fm.muted]global-db[/fm.muted]  [fm.ok]● primary[/fm.ok]",
        f"http://{OTHER}  [fm.muted]global-db[/fm.muted]",
    ]
    # The primary's own line is unchanged: the rows are an addition, not a replacement.
    assert card.facts["url"] == f"http://{SITE}"


def test_a_site_on_someone_elses_server_names_that_server(tmp_path, card_spy):
    """One site on the global-db container fm owns, one on an external server. The absence of a
    `[sites."<site>".database]` entry is the only switch, and the external host is the fact an
    operator cannot get anywhere else on this card."""
    config = _config(tmp_path, sites={SITE: None, OTHER: _external(port=3307)})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}})

    assert card.labelled("sites") == [
        f"http://{SITE}  [fm.muted]global-db[/fm.muted]  [fm.ok]● primary[/fm.ok]",
        f"http://{OTHER}  [fm.muted]external · rds.internal:3307[/fm.muted]",
    ]


def test_a_single_external_site_still_gets_a_row_because_url_cannot_say_where_it_lives(tmp_path, card_spy):
    """The one case where a single-site bench grows a row: `url` says the site is served, and
    nothing else on the card says its schema is on a server fm does not own."""
    config = _config(tmp_path, sites={SITE: _external()})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}})

    assert card.labelled("sites") == [
        f"http://{SITE}  [fm.muted]external · rds.internal:3306[/fm.muted]  [fm.ok]● primary[/fm.ok]",
    ]


def test_a_bench_with_no_site_prints_a_card_that_says_so(tmp_path, card_spy):
    """It used to raise: `get_site_config` looked for a file no siteless bench has. And the url
    slot cannot hold `http://shop`, an address that resolves nowhere and serves nothing."""
    config = _config(tmp_path, sites=None)
    card, info = _info_card(tmp_path, config)

    assert card.facts["url"] == "[fm.muted]no site recorded in bench_config.toml[/fm.muted]"
    assert "sites" not in card.facts
    # The rest of the card is intact: this bench still has a runtime, a database server, a dir.
    assert card.facts["frappe"].endswith("(default)")
    info.output.print_data.assert_called_once_with(f"<rendered {BENCH}>")


def test_an_ambiguous_primary_prints_why_instead_of_raising(tmp_path, card_spy):
    """`primary_site` raises here, and this card is exactly where an operator comes to find out
    why `fm shell shop` refuses. So the url slot explains, and the rows carry the two addresses
    that do work."""
    config = _config(tmp_path, sites={FOREIGN_A: None, FOREIGN_B: None})
    card, info = _info_card(tmp_path, config)

    assert card.facts["url"] == "[fm.muted]2 sites recorded, none named after the bench[/fm.muted]"
    assert card.labelled("sites") == [
        f"http://{FOREIGN_A}  [fm.muted]global-db[/fm.muted]",
        f"http://{FOREIGN_B}  [fm.muted]global-db[/fm.muted]",
    ]
    # Nothing is marked primary, because nothing is: guessing one would point every bench-scoped
    # command at another site's schema.
    assert "primary" not in " ".join(card.labelled("sites"))
    info.output.print_data.assert_called_once()


def test_a_recorded_site_with_no_directory_yet_does_not_stop_the_card(tmp_path, card_spy):
    """`sites/<site>/site_config.json` is Frappe's file, absent until `new-site` has run. The
    admin password then falls back to the bench config's, labelled as the default."""
    config = _config(tmp_path, sites={SITE: None})
    card, _ = _info_card(tmp_path, config)

    assert card.facts["url"] == f"http://{SITE}"
    assert card.facts["frappe"].endswith("(default)")


def test_frappes_own_admin_password_still_wins_when_the_site_config_is_there(tmp_path, card_spy):
    """The guard around that read must not swallow the file when it exists."""
    config = _config(tmp_path, sites={SITE: None})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {"admin_password": "from-site-config"}})

    assert card.facts["frappe"] == "administrator [fm.muted]/[/fm.muted] from-site-config"


def test_an_unmanaged_site_directory_is_reported_and_nothing_else_changes(tmp_path, card_spy):
    """Someone ran `bench new-site` by hand inside `fm shell`. fm reports the directory and will
    not touch its schema, because fm only ever destroys what it wrote down."""
    config = _config(tmp_path, sites={SITE: None})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}}, unmanaged=[UNMANAGED])

    assert card.labelled("unmanaged") == [
        f"sites/{UNMANAGED}/",
        "[fm.muted]not in bench_config.toml; fm will not touch their schemas[/fm.muted]",
    ]
    # A directory fm does not manage is not a site: it changes nothing else about the card.
    assert card.facts["url"] == f"http://{SITE}"
    assert "sites" not in card.facts


def test_many_unmanaged_directories_still_cost_two_rows(tmp_path, card_spy):
    """The card is a summary and every fact on it is written to fit 80 columns, which one sentence
    per directory does not. The long form belongs to `fm delete`, which prints it per directory at
    the moment that schema is about to be destroyed."""
    config = _config(tmp_path, sites={SITE: None})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}}, unmanaged=[UNMANAGED, "old.localhost"])

    assert card.labelled("unmanaged") == [
        f"sites/{UNMANAGED}/ [fm.muted]·[/fm.muted] sites/old.localhost/",
        "[fm.muted]not in bench_config.toml; fm will not touch their schemas[/fm.muted]",
    ]


def test_no_unmanaged_directory_means_no_unmanaged_row(tmp_path, card_spy):
    """The report is drift, so its absence has to be silence rather than a row saying none."""
    config = _config(tmp_path, sites={SITE: None})
    card, _ = _info_card(tmp_path, config, site_config={SITE: {}})

    assert "unmanaged" not in card.facts
