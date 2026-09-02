"""Characterization of the three "what does fm actually report / ship / refuse" seams.

Pinned here so a later refactor (the BenchInfo/BenchDatabase constructor twins, the
transport helpers, the BenchService facade) cannot silently change behaviour:

- ``BenchInfo``: which attribute each constructor argument lands on, which file each
  config read touches and what it raises when absent, the log-path set per environment,
  and every DECISION ``display_info`` makes while assembling the detail card -- protocol
  from the certificate, admin-password precedence, the image-vs-mount runtime facts, the
  deploy-history "current" marker, and the live container-state gathering (which swallows
  a DockerException for workers and any Exception for admin tools).
- ``transport``: which missing tags are pulled and
  which pull failure is survivable (nginx) versus fatal, and why a ``save_load``
  distribution refuses to pull instead of guessing.
- ``BenchService``: the guards -- delete without ``--yes`` delegates instead of removing,
  a missing config falls back to the cleanup bench, discovery ignores anything without a
  compose file, and a broken bench is *listed* rather than raised.

Version/apps parsing lives in test_bench_info_versions.py and is not repeated here.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, call, patch

import pytest
import tomlkit

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.output_manager import railcard
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import (
    AuthConfig,
    BenchConfig,
    BenchRuntime,
    FMBenchEnvType,
    WebAuthConfig,
    resolve_primary_site,
)
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.exceptions import BenchException
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.transport import (
    TransportError,
    fetch_image,
    image_present,
    push_images,
)
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate

BENCH = "bench.localhost"
# Deliberately NOT the bench name. Reading a site's files under the bench name is the bug these
# stand-ins have to be able to catch, and a fixture that gives one string to both roles cannot.
SITE = "web.example.com"
# A bench holds N sites and the alias rows are per-site, so a second site is needed to tell one
# row's aliases from another's.
SECOND_SITE = "b.example.com"
ADMIN_PW = "admin-pass"  # a literal here would trip S106
AUTH_PW = "s3cr3t"
ROOT_PW = "rootpass"


def _docker_exc(cmd="docker pull x"):
    return DockerException(cmd.split(), SubprocessOutput(stdout=[], stderr=["boom"], combined=["boom"], exit_code=1))


# =========================================================================== BenchInfo: wiring


def _config(*, sites=None, aliases=None, **over):
    """Minimal duck-typed BenchConfig: display_info only ever reads these attributes.

    `sites` maps each recorded site name to its external database config, or to None for a site on
    the global-db container fm owns; it defaults to the single site every other test wants, and
    `sites={}` is a bench with no site in it. It drives `name`, `sites`, `site_names`,
    `primary_site` and `get_database_config` TOGETHER, because a stand-in where those disagree
    describes no bench that can exist.

    `aliases` maps a site name to ITS alternate hostnames. That is where the list lives now, so the
    recorded entries carry it: one flat bench-level list could not say which site an alias reached,
    and the card prints one row per site precisely because it can now say.

    Which site is the bench's own comes from the real `resolve_primary_site`, the one
    implementation of that rule, rather than from a second copy of it here: display_info calls it
    on this stand-in's `name` and `sites`, so a fixture answering differently would test nothing.

    The primary is deliberately DIFFERENT from the bench name, because reading the site config
    under the bench name is the bug this stand-in has to be able to catch.
    """
    recorded = {SITE: None} if sites is None else sites
    alias_lists = aliases or {}
    resolved = resolve_primary_site(BENCH, recorded)
    base = {
        "runtime": BenchRuntime.mount,
        "environment_type": FMBenchEnvType.prod,
        "restart_policy": SimpleNamespace(value="unless-stopped"),
        "admin_pass": ADMIN_PW,
        "deploy_state": None,
        "base_image": None,
        "seed_image": None,
        "admin_tools": False,
        "auth": None,
        # The real model stores None rather than an empty table, and `site_names` falls back to the
        # BENCH name there: enumeration has to read `sites` to tell those two apart. Each entry is
        # a site record, which is what carries that site's own aliases.
        "sites": {
            site: SimpleNamespace(
                database=database,
                alias_domains=list(alias_lists.get(site, [])),
                # Both per-site overrides the card reads. Absent means "follow the bench", which is
                # what every bench that never touched them has.
                auth=None,
                serve_admin_tools=None,
            )
            for site, database in recorded.items()
        }
        or None,
        # No such directory: `read_default_site` is deliberately silent about a
        # common_site_config.json it cannot read, so the card falls through to the name-shaped
        # rules, which is what every assertion below is written against.
        "root_path": "/nonexistent-bench-root",
        "name": BENCH,
        "site_names": list(recorded) or [BENCH],
        "primary_site": resolved,
        "get_database_config": lambda site=None: recorded.get(site or resolved),
    }
    base.update(over)
    config = SimpleNamespace(**base)
    # The REAL resolver, bound to the stand-in rather than reimplemented here: whether a site routes
    # the tools is a two-level rule (bench floor, then the site's own value) and a second copy of it
    # in the fixture would drift from the one the card actually calls.
    config.serves_admin_tools = lambda site: BenchConfig.serves_admin_tools(config, site)
    return config


def _kwargs(tmp_path, **over) -> dict:
    """Every argument BenchInfo requires, so a test can also drop one and pin the refusal."""
    kwargs = {
        "bench_name": BENCH,
        "bench_path": tmp_path,
        "bench_config": _config(),
        "services": MagicMock(),
        "workers": MagicMock(),
        "admin_tools": MagicMock(),
        "certificate_manager": MagicMock(),
        "get_db_connection_info_fn": MagicMock(return_value={"name": "db", "password": "dbpass"}),
        "has_certificate_fn": MagicMock(return_value=False),
        "is_running_fn": MagicMock(return_value=True),
        "get_services_running_status_fn": MagicMock(return_value={}),
        "unmanaged_site_dirs_fn": MagicMock(return_value=[]),
        "docker_client": None,
        "output_handler": MagicMock(),
    }
    kwargs.update(over)
    return kwargs


def _info(tmp_path, **over):
    """BenchInfo through its REAL __init__ (it has no side effects) so the wiring is pinned."""
    return BenchInfo(**_kwargs(tmp_path, **over))


def test_constructor_stores_the_injected_callables_under_their_short_names(tmp_path):
    """The five ``*_fn`` arguments are stored WITHOUT the suffix; display_info calls them by
    the short name, so renaming either half breaks the card."""
    db_fn, cert_fn, running_fn, status_fn, drift_fn = (MagicMock() for _ in range(5))
    info = _info(
        tmp_path,
        get_db_connection_info_fn=db_fn,
        has_certificate_fn=cert_fn,
        is_running_fn=running_fn,
        get_services_running_status_fn=status_fn,
        unmanaged_site_dirs_fn=drift_fn,
    )
    assert (info.get_db_connection_info, info.has_certificate) == (db_fn, cert_fn)
    assert (info.is_running, info.get_services_running_status) == (running_fn, status_fn)
    assert info.unmanaged_site_dirs is drift_fn


def test_the_drift_callable_has_no_default_so_no_construction_can_skip_the_report(tmp_path):
    """A defaulted `unmanaged_site_dirs_fn` would let a caller that forgets it silently never
    report a site directory fm does not manage, and a drift report that quietly does not happen is
    worse than none: an operator would read the card as "fm knows about everything here"."""
    kwargs = {k: v for k, v in _kwargs(tmp_path).items() if k != "unmanaged_site_dirs_fn"}
    with pytest.raises(TypeError, match="unmanaged_site_dirs_fn"):
        BenchInfo(**kwargs)


def test_constructor_shares_bench_database_prefix_and_output_default(tmp_path):
    """The twin of BenchDatabase.__init__: same first four positional facts, same
    ``output_handler or RichOutputHandler()`` default.

    Differences (BenchInfo only): workers, admin_tools, certificate_manager, five injected
    callables and an optional docker_client; BenchDatabase instead takes a single
    ``set_common_bench_config_fn``.
    """
    info = _info(tmp_path, output_handler=None)
    assert (info.bench_name, info.bench_path) == (BENCH, tmp_path)
    assert isinstance(info.output, RichOutputHandler)
    assert info.docker_client is None


# =========================================================================== BenchInfo: config reads


def _sites_dir(tmp_path):
    d = tmp_path / "workspace" / "frappe-bench" / "sites"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_get_common_config_reads_the_bench_wide_file(tmp_path):
    (_sites_dir(tmp_path) / "common_site_config.json").write_text(json.dumps({"db_host": "global-db"}))
    assert _info(tmp_path).get_common_config() == {"db_host": "global-db"}


def test_get_common_config_missing_raises_bench_exception(tmp_path):
    with pytest.raises(BenchException) as err:
        _info(tmp_path).get_common_config()
    assert "common_site_config.json not found." in str(err.value)


def test_get_site_config_reads_the_per_site_file(tmp_path):
    """Under the SITE's name, not the bench's: on a bench whose site is differently named, reading
    `sites/<bench>/` raised "site_config.json not found" at the end of a successful create."""
    site_dir = _sites_dir(tmp_path) / SITE
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "_abc"}))
    assert _info(tmp_path).get_site_config() == {"db_name": "_abc"}
    # And the bench-named directory is NOT where it looked.
    assert not (_sites_dir(tmp_path) / BENCH).exists()


def test_get_site_config_missing_raises_bench_exception(tmp_path):
    """A common_site_config.json alone is not enough: the per-site file is a separate read."""
    (_sites_dir(tmp_path) / "common_site_config.json").write_text("{}")
    with pytest.raises(BenchException) as err:
        _info(tmp_path).get_site_config()
    assert f"site_config.json not found for site '{SITE}'." in str(err.value)


# =========================================================================== BenchInfo: log paths


def _logs_dir(tmp_path, *names):
    d = tmp_path / "workspace" / "frappe-bench" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).touch()
    return d


def test_log_paths_dev_is_the_single_dev_server_log(tmp_path):
    # The files are created because only EXISTING paths are returned (see
    # test_log_paths_omit_files_that_were_never_created); this test is about *which*
    # file the dev environment maps to.
    logs = _logs_dir(tmp_path, "web.dev.log")
    info = _info(tmp_path, bench_config=_config(environment_type=FMBenchEnvType.dev))
    assert info.get_log_file_paths() == [logs / "web.dev.log"]


def test_log_paths_prod_returns_error_log_before_stdout_log(tmp_path):
    """Order is stderr-then-stdout (not the variable order above it): callers tail [0] first."""
    logs = _logs_dir(tmp_path, "web.log", "web.error.log")
    info = _info(tmp_path, bench_config=_config(environment_type=FMBenchEnvType.prod))
    assert info.get_log_file_paths() == [logs / "web.error.log", logs / "web.log"]


def test_log_paths_omit_files_that_were_never_created(tmp_path):
    """The caller open()s every path it gets, so a path that is not there must not be
    returned: an unstarted web program (or a dev->prod switch before the first prod
    start) leaves the expected log absent, and `fm logs` died with FileNotFoundError."""
    logs = _logs_dir(tmp_path, "web.log")
    info = _info(tmp_path, bench_config=_config(environment_type=FMBenchEnvType.prod))
    assert info.get_log_file_paths() == [logs / "web.log"]

    _logs_dir(tmp_path)
    dev_info = _info(tmp_path, bench_config=_config(environment_type=FMBenchEnvType.dev))
    assert dev_info.get_log_file_paths() == []


# =========================================================================== BenchInfo: apps guards


def test_get_bench_apps_image_runtime_without_a_tag_never_touches_docker(tmp_path):
    """No deploy yet (or no docker client): the apps list is empty rather than a label read."""
    docker = MagicMock()
    info = _info(tmp_path, bench_config=_config(runtime=BenchRuntime.image), docker_client=docker)
    assert info.get_bench_apps() == []
    docker.image_labels.assert_not_called()

    info = _info(
        tmp_path,
        bench_config=_config(runtime=BenchRuntime.image, deploy_state=SimpleNamespace(current_tag="r:t")),
        docker_client=None,
    )
    assert info.get_bench_apps() == []


def test_get_bench_apps_image_runtime_survives_a_malformed_label(tmp_path):
    docker = MagicMock()
    docker.image_labels.return_value = {"fm.apps": "{not json"}
    info = _info(
        tmp_path,
        bench_config=_config(runtime=BenchRuntime.image, deploy_state=SimpleNamespace(current_tag="r:t")),
        docker_client=docker,
    )
    assert info.get_bench_apps() == []


# =========================================================================== BenchInfo: formatters


def test_short_ts_formats_iso_and_passes_junk_through():
    assert BenchInfo._short_ts("2026-01-02T03:04:05") == "2026-01-02 03:04"
    assert BenchInfo._short_ts("not-a-date") == "not-a-date"
    assert BenchInfo._short_ts(None) == "None"


def test_compact_list_truncates_only_past_the_limit():
    cl = BenchInfo._compact_list
    assert cl("allow", []) == ""
    assert cl("allow", ["a", "b"]) == "allow a, b"
    assert cl("allow", ["a", "b", "c"]) == "allow a, b, c"  # exactly at the limit: no "+0"
    assert cl("allow", ["a", "b", "c", "d", "e"]) == "allow a, b, c +2"
    assert cl("open", ["a", "b", "c"], limit=1) == "open a +2"


def test_auth_fact_none_config_reports_the_model_defaults():
    """A config written before ``[auth]`` existed: tools prompt, web does not, no password yet."""
    fact = BenchInfo._auth_fact(None)
    assert "tools" in fact
    assert "web" not in fact
    assert "password minted on next start" in fact
    assert "admin" in fact


def test_auth_fact_off_when_neither_surface_prompts():
    assert BenchInfo._auth_fact(AuthConfig(web=False, tools=False)) == "[fm.muted]off[/fm.muted]"


def test_auth_fact_lists_both_surfaces_password_and_allow_lists():
    auth = AuthConfig(
        user="ops",
        password=AUTH_PW,
        web=True,
        tools=True,
        allow_ips=["10.0.0.1", "10.0.0.2"],
        allow_paths=["/api/method/ping"],
    )
    fact = BenchInfo._auth_fact(auth)
    assert "web + tools" in fact
    assert f"[fm.secret]{AUTH_PW}[/fm.secret]" in fact
    assert "allow 10.0.0.1, 10.0.0.2" in fact
    assert "open /api/method/ping" in fact


def test_auth_fact_password_set_suppresses_the_minted_notice():
    fact = BenchInfo._auth_fact(AuthConfig(password=AUTH_PW, web=True))
    assert "minted" not in fact


# =========================================================================== BenchInfo: display_info


class _CardSpy:
    """Stand-in for railcard.Card that records the facts/sections display_info decided on."""

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

    @property
    def sections(self) -> list[str]:
        return [label for kind, label, _ in self.rows if kind == "section"]

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
    return _CardSpy


def _displayable(tmp_path, *, site_config=None, apps=None, **over):
    site_dir = _sites_dir(tmp_path) / SITE
    site_dir.mkdir(exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(site_config or {}))
    info = _info(tmp_path, **over)
    info.services.database_manager.database_server_info = SimpleNamespace(
        user="root", password=ROOT_PW, host="global-db"
    )
    info.workers.compose_file_manager.get_container_names.return_value = {}
    info.workers.docker_client.compose.get_all_services_status.return_value = []
    info.admin_tools.compose_file_manager.exists.return_value = False
    info.get_bench_apps = MagicMock(return_value=apps or [])
    return info


def test_display_info_without_certificate_uses_http_and_says_not_enabled(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.display_info()

    (card,) = card_spy.made
    assert card.link == f"http://{SITE}"
    assert card.facts["url"] == f"http://{SITE}"
    assert card.facts["https"] == "[fm.muted]not enabled[/fm.muted]"
    info.certificate_manager.get_certificate_expiry.assert_not_called()
    assert card.sections[:2] == ["site", "runtime"]
    info.output.change_head.assert_called_once_with("Getting bench info")
    info.output.print_data.assert_called_once_with(f"<rendered {BENCH}>")


def test_display_info_with_certificate_switches_every_url_to_https(tmp_path, card_spy):
    info = _displayable(tmp_path, has_certificate_fn=MagicMock(return_value=True))
    info.bench_config.admin_tools = True
    info.bench_config.get_primary_certificate = MagicMock(
        return_value=SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.dev)
    )
    with patch(
        "frappe_manager.site_manager.modules.bench_info.format_ssl_certificate_time_remaining",
        return_value="42 days",
    ):
        info.display_info()

    (card,) = card_spy.made
    # Every URL is the DOMAIN the site is served on, never the bench name: `http://shop` resolves
    # nowhere, and printing it sent operators to a dead address.
    assert card.facts["url"] == f"https://{SITE}"
    assert card.link == f"https://{SITE}"
    assert card.facts["https"] == "DEV [fm.muted]·[/fm.muted] 42 days"
    assert card.facts["tools"] == (f"https://{SITE}/mailpit [fm.muted]·[/fm.muted] https://{SITE}/adminer")
    # The card is still TITLED with the bench, which is what commands take.
    assert card.name == BENCH


def test_display_info_letsencrypt_prefixes_the_challenge_type(tmp_path, card_spy):
    """A non-LE type (or a non-Letsencrypt object) must NOT grow the ``[http01]`` prefix."""
    info = _displayable(tmp_path, has_certificate_fn=MagicMock(return_value=True))
    info.bench_config.get_primary_certificate = MagicMock(
        return_value=LetsencryptSSLCertificate(domain=BENCH, ssl_type=SUPPORTED_SSL_TYPES.le)
    )
    with patch(
        "frappe_manager.site_manager.modules.bench_info.format_ssl_certificate_time_remaining",
        return_value="9 days",
    ):
        info.display_info()

    (card,) = card_spy.made
    assert card.facts["https"].startswith("[HTTP01] LETSENCRYPT")


def test_display_info_le_ssl_type_on_a_foreign_object_keeps_the_plain_type(tmp_path, card_spy):
    """isinstance guard: an ``le`` ssl_type that is not a LetsencryptSSLCertificate has no
    challenge_type to read, so the type is printed bare rather than crashing."""
    info = _displayable(tmp_path, has_certificate_fn=MagicMock(return_value=True))
    info.bench_config.get_primary_certificate = MagicMock(return_value=SimpleNamespace(ssl_type=SUPPORTED_SSL_TYPES.le))
    with patch(
        "frappe_manager.site_manager.modules.bench_info.format_ssl_certificate_time_remaining",
        return_value="9 days",
    ):
        info.display_info()

    (card,) = card_spy.made
    assert card.facts["https"] == "LETSENCRYPT [fm.muted]·[/fm.muted] 9 days"


def test_display_info_site_config_admin_password_wins_over_the_config_default(tmp_path, card_spy):
    info = _displayable(tmp_path, site_config={"admin_password": "from-site-config"})
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["frappe"] == "administrator [fm.muted]/[/fm.muted] from-site-config"


def test_display_info_marks_the_bench_config_password_as_default(tmp_path, card_spy):
    info = _displayable(tmp_path, site_config={})
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["frappe"].endswith("admin-pass (default)")


def test_display_info_db_facts_fall_back_to_na_when_the_connection_info_is_empty(tmp_path, card_spy):
    info = _displayable(tmp_path, get_db_connection_info_fn=MagicMock(return_value={}))
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["db"] == "N/A [fm.muted]/[/fm.muted] [fm.secret]N/A[/fm.secret]"
    assert card.facts["root db"] == (
        "root [fm.muted]/[/fm.muted] [fm.secret]rootpass[/fm.secret] [fm.muted]@[/fm.muted] global-db"
    )


def test_display_info_apps_label_only_on_the_first_row_and_em_dash_for_a_missing_ref(tmp_path, card_spy):
    info = _displayable(
        tmp_path,
        apps=[
            {"name": "frappe", "ref": "version-15", "commit": "abc1234"},
            {"name": "hrms", "commit": "def5678"},
            {},
        ],
    )
    info.display_info()

    (card,) = card_spy.made
    rows = card.labelled("apps")
    assert len(rows) == 3
    assert rows[0] == "frappe  [fm.muted]version-15  abc1234[/fm.muted]"
    assert rows[1] == "hrms  [fm.muted]—  def5678[/fm.muted]"
    assert rows[2] == "?  [fm.muted]—  [/fm.muted]"


def test_display_info_mount_runtime_shows_base_and_seed_images_not_a_tag(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.bench_config.base_image = "ghcr.io/acme/base:1"
    info.bench_config.seed_image = "ghcr.io/acme/seed:1"
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["base"] == "ghcr.io/acme/base:1"
    assert card.facts["seeded"] == "ghcr.io/acme/seed:1"
    assert "tag" not in card.facts
    assert "deploys" not in card.sections


def test_display_info_image_runtime_without_a_deploy_reports_not_yet_deployed(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.bench_config.runtime = BenchRuntime.image
    info.bench_config.base_image = "ghcr.io/acme/base:1"  # image runtime never shows it
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["tag"] == "[fm.muted]N/A (not yet deployed)[/fm.muted]"
    assert "base" not in card.facts
    assert "previous" not in card.facts


def test_display_info_image_runtime_shows_current_and_previous_tag(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.bench_config.runtime = BenchRuntime.image
    info.bench_config.deploy_state = SimpleNamespace(current_tag="r:new", previous_tag="r:old", history=[])
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["tag"] == "r:new"
    assert card.facts["previous"] == "r:old"
    assert "deploys" not in card.sections  # empty history: no section at all


def _deploy_entry(tag, status, backups=None):
    return SimpleNamespace(tag=tag, deployed_at="2026-01-02T03:04:05", migrate_status=status, backups=backups or {})


def test_display_info_deploy_history_is_newest_first_and_marks_current_once(tmp_path, card_spy):
    """The same tag can appear twice (redeploy); only the newest occurrence is '● current'."""
    info = _displayable(tmp_path)
    info.bench_config.runtime = BenchRuntime.image
    info.bench_config.deploy_state = SimpleNamespace(
        current_tag="r:2",
        previous_tag=None,
        history=[
            _deploy_entry("r:1", "migrated"),
            _deploy_entry("r:2", "failed", {"a.localhost": "/dump.sql"}),
            _deploy_entry("r:2", "skipped"),
        ],
    )
    info.display_info()

    (card,) = card_spy.made
    assert "deploys" in card.sections
    rows = card.labelled("history")
    assert [r.split("  ")[0] for r in rows] == ["r:2", "r:2", "r:1"]
    assert "● current" in rows[0]
    assert "● current" not in rows[1]
    assert "[fm.error]failed[/fm.error]" in rows[1]
    assert "1 db-dump" in rows[1]
    assert "2026-01-02 03:04" in rows[0]
    assert "db-dump" not in rows[2]


def test_display_info_deploy_history_counts_the_dumps_it_recorded(tmp_path, card_spy):
    """A multi-site bench dumps every schema, so the row states HOW MANY it took: a bare
    'db-dump' marker would not say whether the site you need to roll back was covered."""
    info = _displayable(tmp_path)
    info.bench_config.runtime = BenchRuntime.image
    info.bench_config.deploy_state = SimpleNamespace(
        current_tag="r:2",
        previous_tag="r:1",
        history=[
            _deploy_entry("r:1", "migrated", {"a.localhost": "/one.sql"}),
            _deploy_entry("r:2", "migrated", {"a.localhost": "/a.sql", "shop.a.localhost": "/shop.sql"}),
        ],
    )
    info.display_info()

    (card,) = card_spy.made
    rows = card.labelled("history")
    assert "2 db-dumps" in rows[0]
    assert "1 db-dump" in rows[1]
    assert "1 db-dumps" not in rows[1]


def test_display_info_admin_tools_disabled_says_not_enabled(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["tools"] == "[fm.muted]not enabled[/fm.muted]"


def _alias_rows(card) -> list[str]:
    """The alias block: the row labelled `aliases` plus the unlabelled continuations under it.

    Read off `rows` rather than `facts`, because `facts` drops empty labels by design and every
    continuation row carries one. The `sites` block above is built the same way, so a per-site row
    here is the card's existing convention and not a new shape. The site sits in the VALUE because
    the label column is 14 characters wide and `aliases of <site>` overruns it.
    """
    collected, inside = [], False
    for kind, label, value in card.rows:
        if kind != "fact":
            continue
        if label == "aliases":
            inside, collected = True, [value]
        elif inside and label == "":
            collected.append(value)
        elif inside:
            break
    return collected


def test_display_info_alias_rows_name_their_site_and_are_sorted(tmp_path, card_spy):
    """The flat `domains` fact is gone: an alias is an alternate FOR a site, so the row names that
    site. A bench whose sites have no aliases prints no such row at all."""
    info = _displayable(tmp_path, bench_config=_config(aliases={SITE: ["z.example", "a.example"]}))
    info.display_info()

    (card,) = card_spy.made
    assert _alias_rows(card) == [f"[fm.muted]{SITE}[/fm.muted]  a.example, z.example"]

    _CardSpy.made = []
    plain = _displayable(tmp_path)
    plain.display_info()
    assert _alias_rows(_CardSpy.made[0]) == []


def test_display_info_gives_every_site_with_aliases_its_own_row(tmp_path, card_spy):
    """Why one joined fact could not survive N sites: two sites each holding an alternate produced
    one list in which no hostname said which schema it reached."""
    info = _displayable(
        tmp_path,
        bench_config=_config(
            sites={SITE: None, SECOND_SITE: None},
            aliases={SITE: ["www.web.example.com"], SECOND_SITE: ["www.b.example.com"]},
        ),
    )
    info.display_info()

    (card,) = card_spy.made
    assert _alias_rows(card) == [
        f"[fm.muted]{SITE}[/fm.muted]  www.web.example.com",
        f"[fm.muted]{SECOND_SITE}[/fm.muted]  www.b.example.com",
    ]


def test_display_info_alias_block_carries_one_label_and_aligns_the_rest(tmp_path, card_spy):
    """The label column is 14 characters. Putting the site in the label instead knocked `sites`,
    `aliases` and `dir` out of alignment on a real bench, which is how this was caught."""
    info = _displayable(
        tmp_path,
        bench_config=_config(
            sites={SITE: None, SECOND_SITE: None},
            aliases={SITE: ["www.web.example.com"], SECOND_SITE: ["www.b.example.com"]},
        ),
    )
    info.display_info()

    (card,) = card_spy.made
    labels = [label for kind, label, _ in card.rows if kind == "fact" and label.startswith("aliases")]
    assert labels == ["aliases"]
    assert all(len(label) <= 14 for kind, label, _ in card.rows if kind == "fact")


def test_display_info_services_section_reports_bench_workers_and_tools_sorted(tmp_path, card_spy):
    info = _displayable(tmp_path, get_services_running_status_fn=MagicMock(return_value={"nginx": "running"}))
    info.workers.compose_file_manager.get_container_names.return_value = {"long": "fm-long"}
    info.workers.docker_client.compose.get_all_services_status.return_value = [
        {"Service": "long-worker", "State": "running", "Name": "fm-long"},
        {"Service": "other-bench-worker", "State": "running", "Name": "fm-someone-else"},
    ]
    info.admin_tools.compose_file_manager.exists.return_value = True
    info.admin_tools.compose_file_manager.get_container_names.return_value = {"mailpit": "fm-mailpit"}
    info.admin_tools.docker_client.compose.get_all_services_status.return_value = [
        {"Service": "mailpit", "State": "exited", "Name": "fm-mailpit"},
    ]
    info.display_info()

    (card,) = card_spy.made
    assert "services" in card.sections
    assert "nginx" in card.facts["bench"]
    # Only containers belonging to THIS bench's compose project are reported.
    assert "long-worker" in card.facts["workers"]
    assert "other-bench-worker" not in card.facts["workers"]
    assert "mailpit" in card.facts["tools"]
    assert "exited" in card.facts["tools"]


def test_display_info_workers_docker_exception_yields_no_workers_fact(tmp_path, card_spy):
    """A DockerException while listing workers must not abort the whole card."""
    info = _displayable(tmp_path)
    info.workers.docker_client.compose.get_all_services_status.side_effect = _docker_exc("docker compose ps")
    info.display_info()

    (card,) = card_spy.made
    assert "services" not in card.sections
    assert "workers" not in card.facts
    info.output.print_data.assert_called_once()


def test_display_info_admin_tools_status_swallows_any_exception(tmp_path, card_spy):
    """The admin-tools probe catches bare Exception (wider than the workers probe)."""
    info = _displayable(tmp_path, get_services_running_status_fn=MagicMock(return_value={"nginx": "running"}))
    info.admin_tools.compose_file_manager.exists.return_value = True
    info.admin_tools.docker_client.compose.get_all_services_status.side_effect = RuntimeError("nope")
    info.display_info()

    (card,) = card_spy.made
    assert "services" in card.sections
    assert "tools" in card.facts  # the access-section 'tools' fact, not a status one
    assert card.facts["tools"] == "[fm.muted]not enabled[/fm.muted]"


def test_display_info_skips_the_admin_tools_probe_when_no_compose_file(tmp_path, card_spy):
    info = _displayable(tmp_path)
    info.admin_tools.compose_file_manager.exists.return_value = False
    info.display_info()

    info.admin_tools.docker_client.compose.get_all_services_status.assert_not_called()
    assert len(card_spy.made) == 1


def test_display_info_headline_active_flag_comes_from_is_running(tmp_path, card_spy):
    info = _displayable(tmp_path, is_running_fn=MagicMock(return_value=False))
    info.display_info()

    (card,) = card_spy.made
    assert card.active is False
    assert "unless-stopped" in card.meta
    assert "mount" in card.meta


# =========================================================================== transport: presence


def test_image_present_matches_repository_and_tag_separately():
    docker = MagicMock()
    docker.images.return_value = [{"Repository": "ghcr.io/acme/erp", "Tag": "jun01"}]
    assert image_present(docker, "ghcr.io/acme/erp:jun01") is True
    assert image_present(docker, "ghcr.io/acme/erp:jun02") is False
    assert image_present(docker, "ghcr.io/acme/other:jun01") is False


def test_image_present_is_false_for_an_untagged_name():
    """SUSPICION (pinned, not fixed): rpartition(':') on a bare name yields repo='', so a
    tagless reference never matches even when the daemon has it as :latest."""
    docker = MagicMock()
    docker.images.return_value = [{"Repository": "erp", "Tag": "latest"}]
    assert image_present(docker, "erp") is False


def test_image_present_treats_a_daemon_error_as_absent():
    docker = MagicMock()
    docker.images.side_effect = RuntimeError("daemon down")
    assert image_present(docker, "r:t") is False


# =========================================================================== transport: fetch


def test_fetch_image_does_nothing_when_both_tags_are_present():
    """This is also how the airgap case is served now that `[registry].distribution` is gone.

    That flag's `save_load` value existed to declare "the image is already on this daemon,
    do not reach for a registry", and made an absent tag a hard error. It was config
    restating something the daemon can be asked directly, so it went: ship the image
    yourself (`docker save | ssh host docker load`), or bake it here, and the presence check
    below finds it. Nothing is pulled and no registry has to be configured or reachable.
    """
    docker = MagicMock()
    docker.images.return_value = [
        {"Repository": "ghcr.io/acme/erp", "Tag": "jun01"},
        {"Repository": "ghcr.io/acme/erp-nginx", "Tag": "jun01"},
    ]
    fetch_image(docker, "ghcr.io/acme/erp:jun01")
    docker.pull.assert_not_called()
    docker.login.assert_not_called()


def test_fetch_image_pulls_only_the_missing_tags():
    docker = MagicMock()
    docker.images.return_value = [{"Repository": "ghcr.io/acme/erp", "Tag": "jun01"}]

    fetch_image(docker, "ghcr.io/acme/erp:jun01", output=MagicMock())

    docker.pull.assert_called_once_with("ghcr.io/acme/erp-nginx:jun01", stream=False)


def test_fetch_image_pulls_both_tags_when_neither_is_present():
    docker = MagicMock()
    docker.images.return_value = []
    fetch_image(docker, "r:t")
    assert [c.args[0] for c in docker.pull.call_args_list] == ["r:t", "r-nginx:t"]


def test_fetch_image_tolerates_a_missing_nginx_image():
    """The assets image is optional, so only its pull failure is downgraded to a warning."""
    docker = MagicMock()
    docker.images.return_value = [{"Repository": "r", "Tag": "t"}]
    docker.pull.side_effect = _docker_exc()
    output = MagicMock()

    fetch_image(docker, "r:t", output=output)

    assert output.warning.call_count == 1
    assert "r-nginx:t" in output.warning.call_args.args[0]


def test_fetch_image_raises_when_the_app_image_pull_fails():
    docker = MagicMock()
    docker.images.return_value = []
    docker.pull.side_effect = _docker_exc()

    with pytest.raises(TransportError) as err:
        fetch_image(docker, "r:t")

    # The wording and its login diagnosis live in test_pull_diagnosis.py; here it is only
    # that the app image's failure is fatal, names the tag, and keeps the cause attached.
    assert "r:t" in str(err.value)
    assert isinstance(err.value.__cause__, DockerException)
    docker.pull.assert_called_once()  # stops at the first fatal tag


# =========================================================================== transport: push


def test_push_images_does_nothing_for_an_empty_tag_list():
    docker = MagicMock()
    push_images(docker, ["", None])
    docker.push.assert_not_called()


def test_push_images_pushes_every_tag_in_order():
    docker = MagicMock()
    output = MagicMock()
    push_images(docker, ["r:t", "", "r-nginx:t"], output)
    assert docker.push.call_args_list == [call("r:t", stream=False), call("r-nginx:t", stream=False)]
    assert [c.args[0] for c in output.print.call_args_list] == ["Pushed r:t", "Pushed r-nginx:t"]


# =========================================================================== BenchService: facade


def _service(tmp_path, **over):
    kwargs = {
        "benches_directory": tmp_path,
        "services": MagicMock(),
        "verbose": True,
        "output_handler": MagicMock(),
    }
    kwargs.update(over)
    return BenchService(**kwargs)


def test_get_bench_forwards_the_service_configuration_to_bench_get_object(tmp_path):
    service = _service(tmp_path)
    with patch("frappe_manager.site_manager.bench_service.Bench") as bench_cls:
        got = service.get_bench("a.localhost", workers_check=False)

    bench_cls.get_object.assert_called_once_with(
        bench_name="a.localhost",
        services=service.services,
        workers_check=False,
        admin_tools_check=True,
        verbose=True,
        output_handler=service.output,
    )
    assert got is bench_cls.get_object.return_value


def test_create_bench_wires_the_compose_path_then_runs_creation(tmp_path):
    service = _service(tmp_path)
    config = MagicMock()
    with (
        patch("frappe_manager.site_manager.bench_service.Bench") as bench_cls,
        patch("frappe_manager.site_manager.bench_service.ComposeFile") as compose_cls,
        patch("frappe_manager.site_manager.bench_service.DockerClient") as docker_cls,
        patch("frappe_manager.site_manager.bench_service.set_context") as set_ctx,
    ):
        got = service.create_bench("a.localhost", config, bench_only=True)

    compose_path = tmp_path / "a.localhost" / "docker-compose.yml"
    compose_cls.assert_called_once_with(compose_path)
    docker_cls.assert_called_once_with(compose_file_path=compose_path, output=service.output)
    set_ctx.assert_called_once_with(bench="a.localhost", operation="create")
    assert bench_cls.call_args.kwargs["path"] == tmp_path / "a.localhost"
    assert bench_cls.call_args.kwargs["bench_config"] is config
    got.create.assert_called_once_with(bench_only=True)
    assert got is bench_cls.return_value


# --------------------------------------------------------------------------- delete guards


def test_delete_bench_delegates_the_whole_sequence(tmp_path):
    """`BenchService` owns resolving the bench and NOTHING else about removal.

    It used to carry a second copy of the cert/db/containers sequence for the `--yes` path, which
    differed from `Bench.remove_bench` only in skipping the confirmation, and the two had already
    drifted in the wording of the database question. The sequence itself, its order, and its
    partial-failure rules are pinned on `Bench.remove_bench` in `test_site_contract.py`.
    """
    service = _service(tmp_path)
    bench = MagicMock()
    with patch.object(BenchService, "get_bench", return_value=bench):
        service.delete_bench("a.localhost", delete_db_from_global_db=True)

    bench.remove_bench.assert_called_once_with(delete_db_from_global_db=True, prompt=True)
    bench.remove_containers_and_dirs.assert_not_called()
    bench.remove_certificate.assert_not_called()


def test_the_yes_flag_becomes_prompt_false(tmp_path):
    """The only thing `--yes` changes, and the reason the second copy existed."""
    service = _service(tmp_path)
    bench = MagicMock()
    with patch.object(BenchService, "get_bench", return_value=bench):
        service.delete_bench("a.localhost", yes=True, delete_db_from_global_db=False)

    bench.remove_bench.assert_called_once_with(delete_db_from_global_db=False, prompt=False)


def test_delete_bench_returns_what_the_removal_returned(tmp_path):
    """A declined confirmation is a False, not an exception, and it has to reach the exit code."""
    service = _service(tmp_path)
    bench = MagicMock()
    bench.remove_bench.return_value = False
    with patch.object(BenchService, "get_bench", return_value=bench):
        assert service.delete_bench("a.localhost") is False
    bench.remove_certificate.assert_not_called()


def test_delete_bench_falls_back_to_the_cleanup_bench_when_the_config_is_missing(tmp_path):
    """A bench whose `bench_config.toml` is gone is exactly the bench most in need of deleting, so
    resolution falls back to a stand-in rather than refusing."""
    service = _service(tmp_path)
    stub = MagicMock()
    with (
        patch.object(BenchService, "get_bench", side_effect=FileNotFoundError("bench_config.toml")),
        patch.object(BenchService, "_create_cleanup_bench", return_value=stub) as cleanup,
    ):
        service.delete_bench("a.localhost", yes=True)

    cleanup.assert_called_once_with("a.localhost")
    stub.remove_bench.assert_called_once_with(delete_db_from_global_db=None, prompt=False)


def test_create_cleanup_bench_builds_an_unchecked_bench_with_a_placeholder_config(tmp_path):
    service = _service(tmp_path)
    with (
        patch("frappe_manager.site_manager.bench_service.Bench") as bench_cls,
        patch("frappe_manager.site_manager.bench_service.ComposeFile"),
        patch("frappe_manager.site_manager.bench_service.DockerClient"),
        patch("frappe_manager.site_manager.bench_service.set_context") as set_ctx,
    ):
        service._create_cleanup_bench("a.localhost")

    kwargs = bench_cls.call_args.kwargs
    assert (kwargs["workers_check"], kwargs["admin_tools_check"]) == (False, False)
    fake = kwargs["bench_config"]
    assert fake.name == "a.localhost"
    assert fake.apps_list == []
    assert (fake.developer_mode, fake.admin_tools) == (False, False)
    assert fake.environment_type is FMBenchEnvType.dev
    assert fake.root_path == tmp_path / "a.localhost" / "bench_config.toml"
    set_ctx.assert_called_once_with(bench="a.localhost", operation="cleanup")

# --------------------------------------------------------------------------- discovery


def _bench_dir(root: Path, name: str, compose: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if compose:
        (d / "docker-compose.yml").write_text("services: {}\n")
    return d


def test_discover_benches_returns_empty_when_the_root_is_absent(tmp_path):
    assert _service(tmp_path / "nope").discover_benches() == {}


def test_discover_benches_requires_a_compose_file_and_ignores_plain_files(tmp_path):
    _bench_dir(tmp_path, "a.localhost")
    _bench_dir(tmp_path, "half-created.localhost", compose=False)
    (tmp_path / "stray.txt").write_text("x")

    found = _service(tmp_path).discover_benches()
    assert found == {"a.localhost": tmp_path / "a.localhost" / "docker-compose.yml"}
    assert _service(tmp_path).get_bench_names() == ["a.localhost"]


# --------------------------------------------------------------------------- list data


def _listable_bench(path: Path, name: str, *, sites=(SITE,), aliases=(), **over):
    """A bench `list_benches_data` can read. `sites` is the recorded `[sites]` table's keys, and it
    is deliberately NOT the bench name: a row that read the sites out of the bench's own name would
    pass a fixture that gave one string to both roles. `sites=()` is a bench with no site in it.

    `aliases` are the alternate hostnames of the FIRST recorded site, because that is where they
    live now; the row flattens them back out across the bench, and it does that by subtracting
    `site_names` from `domains`, so those three have to agree here."""
    site_names = list(sites) or [name]
    recorded = {site: SimpleNamespace(database=None, alias_domains=[]) for site in sites}
    if aliases:
        recorded[site_names[0]].alias_domains = list(aliases)
    # Each site's own name followed by that site's aliases, exactly as the real property publishes
    # them into VIRTUAL_HOST. A siteless bench falls back to its own name and has no entry.
    domains: list[str] = []
    for site in site_names:
        domains.append(site)
        if site in recorded:
            domains.extend(recorded[site].alias_domains)
    config = SimpleNamespace(
        runtime=BenchRuntime.mount,
        environment_type=FMBenchEnvType.prod,
        apps_list=[SimpleNamespace(name="frappe")],
        deploy_state=None,
        base_image=None,
        seed_image=None,
        developer_mode=False,
        admin_tools=True,
        restart_policy=SimpleNamespace(value="always"),
        sites=recorded or None,
        # The real property's mid-create fallback, which is why the row reads `sites` to decide.
        site_names=site_names,
        domains=domains,
    )
    for key, value in over.items():
        setattr(config, key, value)
    bench = MagicMock()
    bench.name = name
    bench.path = path
    bench.running = True
    bench.bench_config = config
    return bench


def test_list_benches_data_prefers_apps_txt_over_the_configured_app_list(tmp_path):
    path = _bench_dir(tmp_path, "a.localhost")
    sites = path / "workspace" / "frappe-bench" / "sites"
    sites.mkdir(parents=True)
    (sites / "apps.txt").write_text("frappe\n\n  hrms  \n")
    service = _service(tmp_path)

    with patch.object(BenchService, "get_bench", return_value=_listable_bench(path, "a.localhost")):
        (row,) = service.list_benches_data()

    assert row["apps"] == ["frappe", "hrms"]
    assert row["status"] == "active"
    assert row["error"] is None
    assert row["path"] == str(path)


def test_list_benches_data_falls_back_to_the_config_app_names(tmp_path):
    path = _bench_dir(tmp_path, "a.localhost")
    service = _service(tmp_path)
    bench = _listable_bench(
        path, "a.localhost", apps_list=[SimpleNamespace(name="frappe"), SimpleNamespace(name="erp")]
    )
    bench.running = False

    with patch.object(BenchService, "get_bench", return_value=bench):
        (row,) = service.list_benches_data()

    assert row["apps"] == ["frappe", "erp"]
    assert row["status"] == "inactive"
    assert (row["deployed_tag"], row["previous_tag"]) == (None, None)
    assert row["alias_domains"] == []


def test_list_benches_data_reports_the_deployed_tags_for_an_image_bench(tmp_path):
    path = _bench_dir(tmp_path, "a.localhost")
    bench = _listable_bench(
        path,
        "a.localhost",
        runtime=BenchRuntime.image,
        deploy_state=SimpleNamespace(current_tag="r:new", previous_tag="r:old"),
        aliases=["alias.localhost"],
    )
    with patch.object(BenchService, "get_bench", return_value=bench):
        (row,) = _service(tmp_path).list_benches_data()

    assert (row["runtime"], row["deployed_tag"], row["previous_tag"]) == ("image", "r:new", "r:old")
    assert row["alias_domains"] == ["alias.localhost"]


def test_list_benches_data_lists_a_broken_bench_instead_of_raising(tmp_path):
    """`fm list` must survive one unreadable bench and still show the healthy one."""
    _bench_dir(tmp_path, "broken.localhost")
    good_path = _bench_dir(tmp_path, "good.localhost")
    missing = FileNotFoundError(2, "No such file", str(tmp_path / "broken.localhost" / "bench_config.toml"))

    def get_bench(name, **_):
        if name == "broken.localhost":
            raise missing
        return _listable_bench(good_path, name)

    with patch.object(BenchService, "get_bench", side_effect=get_bench):
        rows = {r["name"]: r for r in _service(tmp_path).list_benches_data()}

    broken = rows["broken.localhost"]
    assert broken["status"] == "unknown"
    assert broken["error"] == f"bench config not found at {missing.filename}"
    assert rows["good.localhost"]["error"] is None


def test_list_benches_data_lists_a_bench_whose_config_is_not_valid_toml(tmp_path):
    """A malformed bench_config.toml raises tomlkit's ParseError -- a ValueError, NOT a
    FileNotFoundError -- so it used to escape the per-bench handler and abort `fm list`
    entirely, hiding every healthy bench. Any per-bench config failure belongs in that
    bench's own row.
    """
    _bench_dir(tmp_path, "broken.localhost")
    good_path = _bench_dir(tmp_path, "good.localhost")

    # The real exception fm gets from BenchConfig.import_from_toml on this content.
    with pytest.raises(tomlkit.exceptions.ParseError) as err:
        tomlkit.parse('name = "bad"\nthis is not toml\n')
    parse_error = err.value

    def get_bench(name, **_):
        if name == "broken.localhost":
            raise parse_error
        return _listable_bench(good_path, name)

    with patch.object(BenchService, "get_bench", side_effect=get_bench):
        rows = {r["name"]: r for r in _service(tmp_path).list_benches_data()}

    assert rows["good.localhost"]["error"] is None
    broken = rows["broken.localhost"]
    assert broken["status"] == "unknown"
    assert broken["path"] == str(tmp_path / "broken.localhost")
    assert str(parse_error) in broken["error"]


def test_list_benches_data_never_asks_for_worker_or_admin_tool_checks(tmp_path):
    """Listing is a cheap read: the expensive per-bench checks stay off."""
    path = _bench_dir(tmp_path, "a.localhost")
    with patch.object(BenchService, "get_bench", return_value=_listable_bench(path, "a.localhost")) as get_bench:
        _service(tmp_path).list_benches_data()

    assert get_bench.call_args.kwargs == {"workers_check": False, "admin_tools_check": False}


# --------------------------------------------------------------------------- list view


def test_list_benches_view_returns_none_and_hints_at_create_when_empty(tmp_path):
    service = _service(tmp_path)
    assert service.list_benches_view() is None
    assert "fm create <benchname>" in service.output.print.call_args.args[0]
    service.output.stop.assert_called_once()


def test_list_benches_view_warns_about_a_broken_bench_and_draws_no_card(tmp_path, monkeypatch, card_spy):
    monkeypatch.setattr(railcard, "cards", lambda items: items)
    service = _service(tmp_path)
    rows = [
        {"name": "broken.localhost", "error": "bench config not found at /x/bench_config.toml"},
        {
            "name": "a.localhost",
            "error": None,
            "sites": [SITE],
            "status": "active",
            "runtime": "mount",
            "environment": "prod",
            "restart_policy": "always",
            "apps": [],
            "deployed_tag": None,
            "base_image": None,
            "seed_image": None,
            "alias_domains": [],
            "path": "/benches/a.localhost",
        },
    ]
    with patch.object(BenchService, "list_benches_data", return_value=rows):
        view = service.list_benches_view()

    assert [c.name for c in card_spy.made] == ["a.localhost"]
    assert "broken.localhost" in service.output.warning.call_args.args[0]
    card = view[0]
    assert card.link == "http://a.localhost"
    assert card.facts["apps"] == "-"  # empty app list renders as a dash, never blank
    assert card.facts["dir"] == "[fm.muted]/benches/a.localhost[/fm.muted]"
    assert "tag" not in card.facts
    assert "base" not in card.facts
    assert "domains" not in card.facts


def test_list_benches_view_adds_image_and_alias_facts_only_when_set(tmp_path, monkeypatch, card_spy):
    monkeypatch.setattr(railcard, "cards", lambda items: items)
    service = _service(tmp_path)
    row = {
        "name": "a.localhost",
        "error": None,
        "sites": [SITE],
        "status": "inactive",
        "runtime": "image",
        "environment": "prod",
        "restart_policy": "no",
        "apps": ["frappe", "hrms"],
        "deployed_tag": "r:new",
        "base_image": "ghcr.io/acme/base:1",
        "seed_image": "ghcr.io/acme/seed:1",
        "alias_domains": ["alias.localhost", "b.localhost"],
        "path": "/benches/a.localhost",
    }
    with patch.object(BenchService, "list_benches_data", return_value=[row]):
        (card,) = service.list_benches_view()

    assert card is card_spy.made[0]
    assert card.active is False
    assert card.facts["apps"] == "frappe, hrms"
    assert card.facts["tag"] == "r:new"
    assert card.facts["base"] == "ghcr.io/acme/base:1"
    assert card.facts["seeded"] == "ghcr.io/acme/seed:1"
    assert card.facts["domains"] == "alias.localhost, b.localhost"


def test_display_info_reports_a_recorded_site_that_is_absent_on_disk(tmp_path, card_spy):
    """The mirror of the `unmanaged` row, and it matters for the same reason in reverse.

    `unmanaged` names a directory `[sites]` does not record. This names a `[sites]` entry with no
    directory: `bench --site <name>` answers "does not exist" for it, so it gets enumerated,
    published to nginx and offered as a target while no command can act on it. It is also no
    longer eligible to be the primary, so this row is how an operator finds out why.
    """
    info = _displayable(
        tmp_path,
        bench_config=_config(sites={SITE: None, "ghost.localhost": None}, root_path=str(tmp_path / "bench_config.toml")),
    )
    info.display_info()

    (card,) = card_spy.made
    assert "sites/ghost.localhost/" in card.facts["missing"]
    # The site that IS on disk is not accused of being absent.
    assert SITE not in card.facts["missing"]


def test_display_info_has_no_missing_row_when_every_recorded_site_exists(tmp_path, card_spy):
    info = _displayable(tmp_path, bench_config=_config(root_path=str(tmp_path / "bench_config.toml")))
    info.display_info()

    (card,) = card_spy.made
    assert "missing" not in card.facts


# ---------------- credentials belong to a site, and a multi-site bench has more than one


def test_a_single_site_bench_prints_the_credential_rows_it_always_did(tmp_path, card_spy):
    """The same call the `sites` rows make: with one site there is nothing to disambiguate, `url`
    has already named it, and the common bench's card should not change shape."""
    info = _displayable(tmp_path, site_config={"admin_password": "from-site-config"})
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["frappe"] == "administrator [fm.muted]/[/fm.muted] from-site-config"
    assert SITE not in card.facts["frappe"]


def test_every_site_gets_its_own_administrator_row(tmp_path, card_spy):
    """One `frappe` row read as bench-wide while carrying the primary's password, and the other
    sites' were not reachable from any fm command."""
    # The primary's file goes through the harness, which writes it itself; the sibling's is ours.
    second = _sites_dir(tmp_path) / SECOND_SITE
    second.mkdir(exist_ok=True)
    (second / "site_config.json").write_text(json.dumps({"admin_password": f"pw-{SECOND_SITE}"}))
    info = _displayable(
        tmp_path,
        site_config={"admin_password": f"pw-{SITE}"},
        bench_config=_config(sites={SITE: None, SECOND_SITE: None}),
    )
    info.display_info()

    (card,) = card_spy.made
    assert card.labelled("frappe") == [
        f"[fm.muted]{SITE}[/fm.muted]  administrator [fm.muted]/[/fm.muted] pw-{SITE}",
        f"[fm.muted]{SECOND_SITE}[/fm.muted]  administrator [fm.muted]/[/fm.muted] pw-{SECOND_SITE}",
    ]


def test_every_site_gets_its_own_database_row_read_from_that_site(tmp_path, card_spy):
    """Each row is that site's schema and password, not the primary's repeated."""
    per_site = {
        SITE: {"name": "schema_one", "password": "pw-one"},
        SECOND_SITE: {"name": "schema_two", "password": "pw-two"},
    }
    info = _displayable(
        tmp_path,
        bench_config=_config(sites={SITE: None, SECOND_SITE: None}),
        get_db_connection_info_fn=MagicMock(side_effect=lambda site=None: per_site.get(site, {})),
    )
    info.display_info()

    (card,) = card_spy.made
    assert card.labelled("db") == [
        f"[fm.muted]{SITE}[/fm.muted]  schema_one [fm.muted]/[/fm.muted] [fm.secret]pw-one[/fm.secret]",
        f"[fm.muted]{SECOND_SITE}[/fm.muted]  schema_two [fm.muted]/[/fm.muted] [fm.secret]pw-two[/fm.secret]",
    ]


def test_the_credential_rows_keep_one_label_and_the_fourteen_char_column(tmp_path, card_spy):
    """Same constraint the alias rows are under: a site name in the label overruns 14 characters and
    knocks the whole card out of alignment, which is why the site goes in the value."""
    info = _displayable(tmp_path, bench_config=_config(sites={SITE: None, SECOND_SITE: None}))
    info.display_info()

    (card,) = card_spy.made
    labels = [label for kind, label, _ in card.rows if kind == "fact"]
    assert labels.count("frappe") == 1
    assert labels.count("db") == 1
    assert all(len(label) <= 14 for label in labels)


def test_a_site_with_no_recorded_password_reads_as_the_bench_default(tmp_path, card_spy):
    """`fm create BENCH/SITE` records no `admin_password`, so the added site legitimately shows the
    bench's value. Labelling it "(default)" is honest: it is what the site was created with, not a
    password fm watched work."""
    d = _sites_dir(tmp_path) / SECOND_SITE
    d.mkdir(exist_ok=True)
    (d / "site_config.json").write_text(json.dumps({}))
    info = _displayable(tmp_path, bench_config=_config(sites={SITE: None, SECOND_SITE: None}))
    info.display_info()

    (card,) = card_spy.made
    assert card.labelled("frappe")[1].endswith("admin-pass (default)")


# ------------------------- per-site tool routing and auth on the card


def test_the_tools_row_is_unchanged_when_every_site_routes_them(tmp_path, card_spy):
    """The common bench never touched per-site routing, so its card must read exactly as before."""
    info = _displayable(
        tmp_path,
        bench_config=_config(sites={SITE: None, SECOND_SITE: None}, admin_tools=True),
    )
    info.display_info()

    (card,) = card_spy.made
    assert card.labelled("tools") == [
        f"http://{SITE}/mailpit [fm.muted]·[/fm.muted] http://{SITE}/adminer"
    ]


def test_the_tools_row_names_only_the_hostnames_that_serve_them(tmp_path, card_spy):
    """One URL against the primary would name a hostname that answers 404 for half the bench, and
    would say nothing at all about the site that stopped serving them."""
    config = _config(sites={SITE: None, SECOND_SITE: None}, admin_tools=True)
    config.sites[SECOND_SITE].serve_admin_tools = False
    info = _displayable(tmp_path, bench_config=config)
    info.display_info()

    (card,) = card_spy.made
    rows = card.labelled("tools")
    assert rows[0] == f"[fm.muted]{SITE}[/fm.muted]  http://{SITE}/mailpit [fm.muted]·[/fm.muted] http://{SITE}/adminer"
    assert rows[1] == f"[fm.muted]not served on {SECOND_SITE}[/fm.muted]"


def test_the_tools_row_says_so_when_no_site_routes_them(tmp_path, card_spy):
    """Containers running and unreachable is a real state, reachable with `BENCH/all --admin-tools
    disable`. Printing a URL there would be a lie, and printing 'not enabled' a different one."""
    config = _config(sites={SITE: None}, admin_tools=True)
    config.sites[SITE].serve_admin_tools = False
    info = _displayable(tmp_path, bench_config=config)
    info.display_info()

    (card,) = card_spy.made
    assert card.facts["tools"] == "[fm.muted]running, but no site routes them[/fm.muted]"


def test_the_auth_row_is_unchanged_when_no_site_has_its_own(tmp_path, card_spy):
    info = _displayable(
        tmp_path,
        bench_config=_config(sites={SITE: None, SECOND_SITE: None}, auth=AuthConfig(web=True, password="bp")),
    )
    info.display_info()

    (card,) = card_spy.made
    assert card.labelled("auth") == ["[fm.ok]web + tools[/fm.ok]  [fm.muted]·[/fm.muted] admin [fm.muted]/[/fm.muted] [fm.secret]bp[/fm.secret]"]


def test_a_site_with_its_own_auth_gets_its_own_row(tmp_path, card_spy):
    """A single row would report one site's password as if it opened the others, which is the exact
    thing per-site credentials exist to prevent."""
    config = _config(sites={SITE: None, SECOND_SITE: None}, auth=AuthConfig(web=True, password="bench-pw"))
    config.sites[SECOND_SITE].auth = WebAuthConfig(web=True, user="customer", password="site-pw")
    info = _displayable(tmp_path, bench_config=config)
    info.display_info()

    (card,) = card_spy.made
    rows = card.labelled("auth")
    assert rows[0].startswith("[fm.muted]bench[/fm.muted]  ")
    assert "bench-pw" in rows[0]
    assert rows[1].startswith(f"[fm.muted]{SECOND_SITE}[/fm.muted]  ")
    assert "site-pw" in rows[1]
    # `tools` is bench-wide, so it is reported on the bench's row and NOT on a site's.
    assert "tools" in rows[0]
    assert "tools" not in rows[1]
