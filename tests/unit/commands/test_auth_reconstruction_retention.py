"""`fm auth` must not delete a stray key while rewriting the auth settings it DOES understand.

`WebAuthConfig` (and `AuthConfig` by inheritance) is extra="allow": an unknown key inside `[auth]`
or `[sites."<name>".auth]` is retained on load, per the retention ruling (fm never deletes a key it
does not understand). The command used to rebuild the entry from `WebAuthConfig(user=..., password=
..., web=..., allow_ips=..., allow_paths=...)` / `AuthConfig(..., tools=...)`, five or six named
kwargs with no way to see anything else the loaded instance carried -- so a stray survived every
read but was erased by the one write path meant to update just the named fields. `fm auth` is the
operator deliberately rewriting THEIR OWN named fields, not asking to erase an unrelated key in the
same table, so the fix mutates the loaded instance in place and the stray survives across both the
bench scope and the site scope.

Proven against the file text tomlkit round-trips out, not merely the in-memory model: a two-cycle
export/reimport fixed point would be trivially identical if a key were dropped on the first cycle,
so every retention assertion here re-parses the SAVED bench_config.toml from disk.
"""

from importlib import import_module
from unittest.mock import MagicMock, patch

import tomlkit
import typer

from frappe_manager.commands.auth import AuthSurface, auth
from frappe_manager.site_manager.bench_config import BenchConfig

auth_mod = import_module("frappe_manager.commands.auth")

BENCH_TOML = """\
name = "mybench"
developer_mode = false
admin_tools = true
environment_type = "dev"

[auth]
user = "admin"
password = "oldpw"
web = false
tools = true
stray_bench_auth = "operator_typo_value"

[sites."b.localhost"]
[sites."b.localhost".auth]
user = "siteuser"
password = "sitepw"
web = false
stray_site_auth = "site_typo_value"
"""


def _bench_with_real_config(tmp_path, toml_text: str = BENCH_TOML):
    """A bench whose `bench_config` is a REAL `BenchConfig.import_from_toml` result, not a
    MagicMock attribute, because the whole point of these tests is proving what actually reaches
    disk. Only the `Bench` wrapper itself is mocked: `save_bench_config`'s real body
    (`site.py:485-491`) is one line, `self.bench_config.export_to_toml(self.bench_config.root_path)`,
    reproduced here as the mock's side effect so the save is real too.
    """
    root = tmp_path / "mybench"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "bench_config.toml"
    config_path.write_text(toml_text)
    config = BenchConfig.import_from_toml(config_path)

    bench = MagicMock()
    bench.name = "mybench"
    bench.path = root
    bench.bench_config = config
    bench.save_bench_config.side_effect = lambda print_message=False: config.export_to_toml(config.root_path)
    bench.nginx_conf_serves_per_site.return_value = True
    return bench, config_path


def _run_auth(bench, **kwargs):
    """Call the real `auth` command body against `bench`, with only the bench lookup and the
    migration gate mocked. `--insecure` defaults on and no nginx conf is written so neither safety
    gate needs its own fixture; they are exercised by the sibling contract test file, not this one.
    """
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "site": kwargs.pop("site", None)}
    params = {
        "address": "mybench",
        "protect": [],
        "off": False,
        "status": False,
        "user": None,
        "password": None,
        "rotate": False,
        "allow_ip": [],
        "allow_path": [],
        "clear_exemptions": False,
        "insecure": True,
    }
    params.update(kwargs)
    with (
        patch.object(auth_mod, "check_bench_migration_required"),
        patch.object(auth_mod, "Bench") as bench_cls,
    ):
        bench_cls.get_object.return_value = bench
        try:
            auth(ctx, **params)
            raised = None
        except typer.Exit as exc:
            raised = exc
    return raised


def _reload(config_path) -> tomlkit.TOMLDocument:
    """The saved file, re-parsed independently of the model that wrote it -- the proof standard is
    the original file TEXT, not whatever the in-memory `BenchConfig` still happens to hold."""
    return tomlkit.parse(config_path.read_text())


class TestBenchScopeStrayRetention:
    def test_a_stray_inside_auth_survives_a_bench_wide_rewrite(self, tmp_path):
        bench, config_path = _bench_with_real_config(tmp_path)
        raised = _run_auth(bench, protect=[AuthSurface.web], user="newuser", password="newpw")
        assert raised is None
        bench.save_bench_config.assert_called_once()

        doc = _reload(config_path)
        assert doc["auth"]["stray_bench_auth"] == "operator_typo_value"

    def test_the_named_bench_auth_fields_are_still_correctly_updated(self, tmp_path):
        """The regression risk of preserving too much: the operator's new values must actually
        land, including a surface being cleared back to False by --protect's declarative semantics
        (tools was on in the fixture; --protect web alone must turn it back off)."""
        bench, config_path = _bench_with_real_config(tmp_path)
        _run_auth(bench, protect=[AuthSurface.web], user="newuser", password="newpw")

        doc = _reload(config_path)
        assert doc["auth"]["user"] == "newuser"
        assert doc["auth"]["password"] == "newpw"
        assert doc["auth"]["web"] is True
        assert doc["auth"]["tools"] is False


class TestSiteScopeStrayRetention:
    def test_a_stray_inside_a_sites_auth_survives_a_per_site_rewrite(self, tmp_path):
        bench, config_path = _bench_with_real_config(tmp_path)
        raised = _run_auth(bench, site="b.localhost", protect=[AuthSurface.web], user="newsiteuser", password="newsitepw")
        assert raised is None
        bench.save_bench_config.assert_called_once()

        doc = _reload(config_path)
        assert doc["sites"]["b.localhost"]["auth"]["stray_site_auth"] == "site_typo_value"

    def test_the_named_site_auth_fields_are_still_correctly_updated(self, tmp_path):
        bench, config_path = _bench_with_real_config(tmp_path)
        _run_auth(bench, site="b.localhost", protect=[AuthSurface.web], user="newsiteuser", password="newsitepw")

        doc = _reload(config_path)
        site_auth = doc["sites"]["b.localhost"]["auth"]
        assert site_auth["user"] == "newsiteuser"
        assert site_auth["password"] == "newsitepw"
        assert site_auth["web"] is True

    def test_a_site_taking_auth_for_the_first_time_still_creates_a_plain_entry(self, tmp_path):
        """No prior `[sites."<name>".auth]` table to preserve: the construct-fresh branch, not the
        mutate branch, and there is no stray to lose because none was ever loaded."""
        toml_text = BENCH_TOML.replace(
            '[sites."b.localhost".auth]\n'
            'user = "siteuser"\n'
            'password = "sitepw"\n'
            "web = false\n"
            'stray_site_auth = "site_typo_value"\n',
            "",
        )
        bench, config_path = _bench_with_real_config(tmp_path, toml_text)
        raised = _run_auth(bench, site="b.localhost", protect=[AuthSurface.web], password="freshpw")
        assert raised is None

        doc = _reload(config_path)
        site_auth = doc["sites"]["b.localhost"]["auth"]
        assert site_auth["password"] == "freshpw"
        assert site_auth["web"] is True
