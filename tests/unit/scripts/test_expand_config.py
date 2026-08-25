"""Contract for scripts/expand-config.py, the CI config expander.

It exists so a config file can live in the repo while its secrets live in the
environment. The three rules below are all reactions to how the expansion already inside
fm behaves (`transport.py` runs `os.path.expandvars` over the `[registry]` credentials),
so they are the point of the script rather than incidental:

- only `FM_ACTION_*` names are substituted, because `expandvars` rewrites anything and a `$HOME`
  or `$PATH` inside a config value is a legitimate thing to write,
- an unset reference is an error, because `expandvars` leaves it as literal text and a
  typo then travels onward as a password of `${FM_ACTION_TOKENN}`,
- values never reach the log, only names.

The expansion stays in the action layer. Doing it inside fm at load time would write the
plaintext back out: `export_to_toml` builds from the model, and normal operation rewrites
`bench_config.toml` from dozens of call sites.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent.parent / "scripts" / "expand-config.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("expand_config_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expand(mod, text, **env):
    return mod.expand(text, "FM_ACTION_", env)


class TestSubstitution:
    def test_a_braced_reference_is_replaced(self, mod):
        out, used = _expand(mod, 'password = "${FM_ACTION_TOKEN}"', FM_ACTION_TOKEN="s3cr3t")

        assert out == 'password = "s3cr3t"'
        assert used == ["FM_ACTION_TOKEN"]

    def test_a_bare_reference_is_replaced(self, mod):
        out, _ = _expand(mod, "tag = $FM_ACTION_TAG", FM_ACTION_TAG="v16")

        assert out == "tag = v16"

    def test_a_reference_inside_a_larger_string_is_replaced(self, mod):
        out, _ = _expand(mod, 'base = "ghcr.io/acme/frappe:${FM_ACTION_TAG}-slim"', FM_ACTION_TAG="v16")

        assert out == 'base = "ghcr.io/acme/frappe:v16-slim"'

    def test_the_same_name_twice_is_reported_once(self, mod):
        _, used = _expand(mod, "$FM_ACTION_A $FM_ACTION_A", FM_ACTION_A="x")

        assert used == ["FM_ACTION_A"]

    def test_names_are_reported_in_first_seen_order(self, mod):
        _, used = _expand(mod, "$FM_ACTION_B $FM_ACTION_A", FM_ACTION_A="1", FM_ACTION_B="2")

        assert used == ["FM_ACTION_B", "FM_ACTION_A"]

    def test_an_empty_value_is_still_a_substitution(self, mod):
        """Set-but-empty is a deliberate choice by the caller, not a missing variable."""
        out, used = _expand(mod, 'x = "${FM_ACTION_EMPTY}"', FM_ACTION_EMPTY="")

        assert out == 'x = ""'
        assert used == ["FM_ACTION_EMPTY"]


class TestOnlyThePrefix:
    def test_home_is_left_alone(self, mod):
        """The whole reason for a prefix: a config path may legitimately mention $HOME."""
        out, used = _expand(mod, 'include = ["$HOME/certs"]', HOME="/root")

        assert out == 'include = ["$HOME/certs"]'
        assert used == []

    def test_a_braced_non_prefixed_name_is_left_alone(self, mod):
        out, _ = _expand(mod, "x = ${PATH}", PATH="/usr/bin")

        assert out == "x = ${PATH}"

    def test_an_unset_non_prefixed_name_is_not_an_error(self, mod):
        """Only FM_ACTION_* is ours to guarantee; anything else passes through untouched."""
        out, _ = _expand(mod, "x = $SOMETHING_ELSE")

        assert out == "x = $SOMETHING_ELSE"

    def test_a_custom_prefix_is_honoured(self, mod):
        out, used = mod.expand("$ACME_A $FM_ACTION_B", "ACME_", {"ACME_A": "1", "FM_ACTION_B": "2"})

        assert out == "1 $FM_ACTION_B"
        assert used == ["ACME_A"]

    def test_the_default_prefix_is_the_action_namespace(self, mod):
        """`FM_ACTION_`, not `FM_`. The bare `FM_` namespace was already taken."""
        import inspect

        signature = inspect.signature(mod.main)
        source = inspect.getsource(mod.main)

        assert 'default="FM_ACTION_"' in source, signature

    @pytest.mark.parametrize(
        "name",
        [
            # scripts/install.sh
            "FM_PASSWORD",
            "FM_SUDO_PASSWORD",
            "FM_USERNAME",
            "FM_BRANCH",
            "FM_NON_INTERACTIVE",
            # fm itself
            "FM_THEME",
            "FM_STRICT_OUTPUT",
            "FM_LETSENCRYPT_STAGING",
        ],
    )
    def test_existing_fm_variables_are_not_swept_into_config(self, mod, name):
        """These already mean something to install.sh or to fm. A blanket `FM_` prefix
        would have expanded the installer's sudo password into a config file."""
        out, used = _expand(mod, f"x = ${{{name}}}", **{name: "leaked"})

        assert out == f"x = ${{{name}}}"
        assert used == []


class TestFailures:
    def test_an_unset_reference_is_an_error(self, mod):
        with pytest.raises(mod.ExpandError, match="FM_ACTION_MISSING"):
            _expand(mod, 'password = "${FM_ACTION_MISSING}"')

    def test_every_missing_name_is_listed_at_once(self, mod):
        """One run, one fix. Reporting the first only makes this a guessing game."""
        with pytest.raises(mod.ExpandError) as excinfo:
            _expand(mod, "$FM_ACTION_ONE $FM_ACTION_TWO", FM_ACTION_THREE="x")

        assert "FM_ACTION_ONE" in str(excinfo.value)
        assert "FM_ACTION_TWO" in str(excinfo.value)

    def test_shell_default_syntax_is_refused_not_ignored(self, mod):
        """`${FM_ACTION_T:-x}` looks like it works. Left alone it would reach fm verbatim."""
        with pytest.raises(mod.ExpandError, match="unsupported reference syntax"):
            _expand(mod, 'x = "${FM_ACTION_T:-fallback}"', FM_ACTION_T="set")

    def test_a_non_prefixed_modifier_is_not_our_business(self, mod):
        out, _ = _expand(mod, "x = ${OTHER:-y}")

        assert out == "x = ${OTHER:-y}"


class TestCli:
    def test_the_output_file_is_not_readable_by_anyone_else(self, mod, tmp_path, monkeypatch):
        source = tmp_path / "in.toml"
        source.write_text('password = "${FM_ACTION_TOKEN}"')
        dest = tmp_path / "out.toml"
        monkeypatch.setenv("FM_ACTION_TOKEN", "s3cr3t")

        assert mod.main(["--in", str(source), "--out", str(dest)]) == 0
        assert dest.read_text() == 'password = "s3cr3t"'
        assert dest.stat().st_mode & 0o077 == 0, "an expanded config holds secrets"

    def test_a_missing_input_file_is_reported(self, mod, tmp_path):
        assert mod.main(["--in", str(tmp_path / "nope.toml"), "--out", "-"]) == 1

    def test_an_unset_variable_fails_the_run(self, mod, tmp_path, monkeypatch):
        source = tmp_path / "in.toml"
        source.write_text('x = "${FM_ACTION_NOPE}"')
        monkeypatch.delenv("FM_ACTION_NOPE", raising=False)

        assert mod.main(["--in", str(source), "--out", "-"]) == 1

    def test_the_secret_never_reaches_the_log(self, mod, tmp_path, monkeypatch, capsys):
        source = tmp_path / "in.toml"
        source.write_text('password = "${FM_ACTION_TOKEN}"')
        monkeypatch.setenv("FM_ACTION_TOKEN", "s3cr3t-do-not-log")

        mod.main(["--in", str(source), "--out", str(tmp_path / "out.toml")])
        captured = capsys.readouterr()

        assert "FM_ACTION_TOKEN" in captured.err
        assert "s3cr3t-do-not-log" not in captured.err
        assert "s3cr3t-do-not-log" not in captured.out
