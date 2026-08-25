"""Contract for scripts/docslint.py, the docs CI gate.

A lint that cannot fail is worse than no lint, because it is trusted. These tests exist
because the realistic way this script breaks is not a crash: it is someone simplifying a
check until it silently passes everything. Each test below injects the defect the check is
supposed to catch, so neutering a check turns a test red instead of turning CI green.

The dash check earned its test the hard way. It originally fired on
`fm shell mybench -- bench build`, a POSIX argument separator inside a shell example, and
the fix (skip fenced code) is one `continue` away from ignoring the whole file.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent.parent / "scripts" / "docslint.py"


@pytest.fixture
def dl():
    """The script, loaded as a module. It is a script, not a package, so no import path."""
    spec = importlib.util.spec_from_file_location("docslint_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(tmp_path: Path, body: str) -> list[Path]:
    f = tmp_path / "page.md"
    f.write_text(body)
    return [f]


class TestDashStyle:
    def test_an_em_dash_in_prose_is_caught(self, dl, tmp_path):
        assert dl.check_dashes(_doc(tmp_path, "A sentence \u2014 like this.\n"))

    def test_an_en_dash_in_prose_is_caught(self, dl, tmp_path):
        assert dl.check_dashes(_doc(tmp_path, "Ports 80\u201390 are used.\n"))

    def test_a_prose_double_dash_connector_is_caught(self, dl, tmp_path):
        assert dl.check_dashes(_doc(tmp_path, "The bench restarts -- and then resumes.\n"))

    def test_a_dash_inside_a_fenced_block_is_left_alone(self, dl, tmp_path):
        """`--` in a shell example is an argument separator, not prose style."""
        body = "Prose is fine.\n\n```bash\nfm shell mybench -- bench build\n```\n"

        assert dl.check_dashes(_doc(tmp_path, body)) == []

    def test_an_em_dash_inside_a_fence_is_also_left_alone(self, dl, tmp_path):
        assert dl.check_dashes(_doc(tmp_path, "```\necho a \u2014 b\n```\n")) == []

    def test_prose_after_a_closed_fence_is_checked_again(self, dl, tmp_path):
        """The fence flag has to toggle off, or one code block disables the rest of the file."""
        body = "```bash\nfm restart -- x\n```\n\nThen a real \u2014 dash in prose.\n"

        assert dl.check_dashes(_doc(tmp_path, body))

    def test_a_real_flag_is_not_mistaken_for_a_prose_dash(self, dl, tmp_path):
        assert dl.check_dashes(_doc(tmp_path, "Pass --no-drain to interrupt the job.\n")) == []


class TestFlagsExist:
    def test_a_flag_the_cli_does_not_have_is_reported(self, dl, tmp_path):
        unknown = dl.check_flags(_doc(tmp_path, "Run `fm restart --no-such-flag` now.\n"), {"--drain"})

        assert "--no-such-flag" in unknown

    def test_a_real_flag_passes(self, dl, tmp_path):
        assert dl.check_flags(_doc(tmp_path, "Run `fm restart --drain`.\n"), {"--drain"}) == {}

    def test_the_reported_file_is_named_so_the_failure_is_actionable(self, dl, tmp_path):
        unknown = dl.check_flags(_doc(tmp_path, "Use --ghost here.\n"), set())

        assert unknown["--ghost"] == {str(tmp_path / "page.md")}


class TestLiveFlagInventory:
    """The point of reading the CLI instead of a checked-in list: it cannot go stale."""

    def test_the_inventory_comes_from_the_real_cli(self, dl):
        flags = dl.fm_flags()

        assert {"--drain", "--no-drain", "--rolling"} <= flags

    def test_negatable_flags_contribute_both_halves(self, dl):
        flags = dl.fm_flags()

        assert "--push" in flags
        assert "--no-push" in flags

    def test_fmx_flags_are_parsed_from_source_since_it_cannot_be_imported(self, dl):
        """fmx imports supervisor, so a host cannot load it; the flags still have to be known."""
        with pytest.raises(ModuleNotFoundError):
            __import__("fmx.main")

        assert len(dl.fmx_flags()) > 5


class TestVendoredTreesAreIgnored:
    """`just test-fmx` builds Docker/frappe/fmx/.venv, which lands inside the Docker/ glob.

    Both halves of this script broke on it, in opposite directions. The docs glob picked up a
    vendored typer SKILL.md and failed on a flag typer documents about itself: noisy, but it
    announces itself. The flag scan was worse and silent, because every flag it finds becomes
    an ALLOWED name: the inventory went from 176 to 638, so a doc typo could pass by matching
    some flag in an unrelated dependency.
    """

    @pytest.mark.parametrize(
        "part",
        [".venv", "venv", "site-packages", "node_modules", "dist", "build", "__pycache__", ".git"],
    )
    def test_a_vendored_path_component_is_not_ours(self, dl, part):
        assert dl._ours(Path(f"Docker/frappe/fmx/{part}/lib/typer/SKILL.md")) is False

    def test_our_own_paths_are_still_ours(self, dl):
        assert dl._ours(Path("docs/deploy/transports.md")) is True
        assert dl._ours(Path("Docker/frappe/fmx/fmx/commands/restart.py")) is True

    def test_the_docs_glob_excludes_vendored_markdown(self, dl, tmp_path, monkeypatch):
        """Both globs filter, so both are planted: `hand_written` walks docs/ AND Docker/."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "real.md").write_text("# ours\n")
        in_docs = tmp_path / "docs" / "node_modules" / "pkg"
        in_docs.mkdir(parents=True)
        (in_docs / "README.md").write_text("# theirs\n")

        (tmp_path / "Docker" / "pkg").mkdir(parents=True)
        (tmp_path / "Docker" / "pkg" / "ours.md").write_text("# ours too\n")
        in_docker = tmp_path / "Docker" / "pkg" / ".venv" / "site-packages"
        in_docker.mkdir(parents=True)
        (in_docker / "SKILL.md").write_text("# theirs\n")
        monkeypatch.chdir(tmp_path)

        found = {p.name for p in dl.hand_written()}

        assert found == {"real.md", "ours.md"}

    def test_the_link_scan_ignores_vendored_markdown(self, dl, tmp_path, monkeypatch):
        """A vendored page's broken links are not ours to report, or to fail CI on."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "real.md").write_text("# ours\n")
        vendored = tmp_path / "docs" / ".venv" / "site-packages"
        vendored.mkdir(parents=True)
        (vendored / "SKILL.md").write_text("See [gone](./no-such-file.md) and [abs](/x.md).\n")
        monkeypatch.chdir(tmp_path)

        absolute, broken = dl.check_links()

        assert (absolute, broken) == ([], [])

    def test_the_flag_inventory_excludes_vendored_python(self, dl, tmp_path, monkeypatch):
        """The dangerous direction: a vendored flag would become an allowed name."""
        src = tmp_path / "fmx"
        (src / "fmx").mkdir(parents=True)
        (src / "fmx" / "cli.py").write_text('opt = "--real-fmx-flag"\n')
        vendored = src / ".venv" / "lib" / "site-packages" / "typer"
        vendored.mkdir(parents=True)
        (vendored / "main.py").write_text('opt = "--someone-elses-flag"\n')
        monkeypatch.setattr(dl, "FMX_SRC", src)

        flags = dl.fmx_flags()

        assert "--real-fmx-flag" in flags
        assert "--someone-elses-flag" not in flags


class TestAnchors:
    def test_an_explicit_id_is_an_anchor(self, dl):
        assert "workers" in dl.anchors_of("## Worker care {#workers}\n")

    def test_a_heading_also_yields_its_auto_slug(self, dl):
        assert "safe-worker-restarts" in dl.anchors_of("## Safe worker restarts\n")

    def test_punctuation_and_code_ticks_are_dropped_from_the_slug(self, dl):
        assert "fm-restart-flags" in dl.anchors_of("### `fm restart`: flags!\n")


class TestLinks:
    @pytest.fixture(autouse=True)
    def _docs_tree(self, tmp_path, monkeypatch):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "index.md").write_text("# Home\n\n## Real section\n")
        monkeypatch.chdir(tmp_path)

    def _page(self, body: str) -> None:
        Path("docs/page.md").write_text(body)

    def test_a_link_to_a_missing_file_is_broken(self, dl):
        self._page("See [gone](./removed.md).\n")

        _, broken = dl.check_links()
        assert any("missing file" in b for b in broken)

    def test_a_link_to_a_missing_anchor_in_another_page_is_broken(self, dl):
        self._page("See [x](./index.md#not-a-heading).\n")

        _, broken = dl.check_links()
        assert any("missing anchor" in b for b in broken)

    def test_a_same_page_anchor_is_checked_too(self, dl):
        self._page("# Page\n\nSee [x](#not-here).\n")

        _, broken = dl.check_links()
        assert any("same page" in b for b in broken)

    def test_a_good_link_and_anchor_pass(self, dl):
        self._page("See [home](./index.md#real-section).\n")

        absolute, broken = dl.check_links()
        assert (absolute, broken) == ([], [])

    def test_an_absolute_link_is_reported_separately(self, dl):
        self._page("See [x](/docs/index.md).\n")

        absolute, broken = dl.check_links()
        assert absolute
        assert broken == []

    def test_external_links_are_not_fetched_or_judged(self, dl):
        self._page("See [x](https://example.invalid/nope) and [y](mailto:a@b.c).\n")

        assert dl.check_links() == ([], [])

    def test_a_directory_link_resolves_to_its_index_page(self, dl):
        """mkdocs serves `foo/` as `foo/index.md`; a bare directory link is not broken."""
        self._page("See [home](./).\n")

        assert dl.check_links() == ([], [])


class TestCannotSilentlyPass:
    """A clean report from a broken checkout is the one failure nobody would notice."""

    def test_finding_no_docs_is_an_error_not_a_clean_run(self, dl, monkeypatch):
        monkeypatch.setattr(dl, "hand_written", list)

        assert dl.main() == 2

    def test_an_empty_flag_inventory_is_an_error_not_a_clean_run(self, dl, monkeypatch):
        monkeypatch.setattr(dl, "fm_flags", set)

        assert dl.main() == 2
