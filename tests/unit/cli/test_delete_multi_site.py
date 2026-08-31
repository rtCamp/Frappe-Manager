"""`fm delete` when a bench holds several sites: the guards, and what they say.

A bench used to be its single site, so `fm delete shop` destroyed exactly what was typed. It can
now serve N sites, and the rule these tests pin is: the operator types the name back only when the
command destroys MORE than the address named.

    fm delete shop         (3 sites)   one word, three sites die   -> --all-sites AND the name typed
    fm delete shop         (1 site)    what was typed              -> the existing yes/no
    fm delete shop/a.x.com             exactly what was typed      -> the existing yes/no

Black-box through the CLI, because the exit code and the message are the contract an operator
meets, and because a guard asserted by calling a helper is a guard that can stop being wired up.

The engine half is mocked: `Bench.site_schemas`, `Bench.remove_site` and
`BenchService.delete_bench` are what this surface drives, and driving them for real would need
docker. `_Schema` stands in for `SiteSchema` so a failure here is always about the CLI.
"""

from dataclasses import dataclass
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.output_manager import get_global_output_handler

# `frappe_manager.commands` re-exports the `delete` FUNCTION under the same name, shadowing the
# module, so the module has to be imported explicitly to patch its globals.
delete_cmd = import_module("frappe_manager.commands.delete")

runner = CliRunner()

BENCH = "shop"
SITE_A = "a.example.com"
SITE_B = "b.example.com"


@dataclass(frozen=True)
class _Schema:
    """Stand-in for `SiteSchema`, the value `Bench.site_schemas()` yields per site on disk.

    `external_host` None means the schema is in the global-db container fm owns and fm may drop it;
    set means a server fm does not own. `schema` None means site_config.json could not be read.
    """

    site: str
    schema: str | None = None
    external_host: str | None = None

    @property
    def droppable(self) -> bool:
        return self.schema is not None and self.external_host is None

    @property
    def unreadable(self) -> bool:
        return self.schema is None


def _said(result) -> str:
    """`result.output` with rich's box drawing and line wrapping flattened.

    Same helper, and for the same reason, as `tests/unit/cli/test_address_argument.py`: typer draws
    a parameter refusal in an 80-column bordered box and rich word-wraps every other message, so an
    asserted phrase long enough to cross the boundary otherwise depends on terminal width rather
    than on what fm said.
    """
    text = result.output.replace("│", " ").replace("╭", " ").replace("╮", " ")
    text = text.replace("╰", " ").replace("╯", " ").replace("─", " ")
    return " ".join(text.split())


def _app():
    app = typer.Typer()
    app.command("delete")(delete_cmd.delete)
    return app


def _bench_on_disk(root, sites):
    """A bench directory whose `[sites]` table records `sites`, which is what the address callback
    validates a `BENCH/SITE` address against."""
    (root / BENCH).mkdir(parents=True)
    body = f'name = "{BENCH}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    body += "".join(f'\n[sites."{site}"]\n' for site in sites)
    (root / BENCH / "bench_config.toml").write_text(body)


class _Run:
    """One `fm delete` invocation: what it printed, and what it did in the order it did it."""

    def __init__(self, result, calls):
        self.result = result
        self.calls = calls

    @property
    def said(self) -> str:
        return _said(self.result)

    def index(self, name: str) -> int:
        """Position of the first `name` call, or -1. Ordering is asserted on these, never on
        `assert_called`, so "it prompted, THEN removed" is checkable."""
        for position, (what, _) in enumerate(self.calls):
            if what == name:
                return position
        return -1

    def payload(self, name: str):
        return next(payload for what, payload in self.calls if what == name)


def _run(argv, *, root, sites, schemas=None, answer=None, config_readable=True):
    """Invoke `fm delete` with the engine and the prompt recorded instead of performed."""
    handler = get_global_output_handler()
    calls: list[tuple[str, object]] = []

    if schemas is None:
        schemas = [_Schema(site, f"fm_{site.replace('.', '_')}") for site in sites]

    def _site_schemas():
        calls.append(("site_schemas", None))
        return list(schemas)

    def _remove_site(site, **kwargs):
        calls.append(("remove_site", (site, kwargs)))
        return True

    def _delete_bench(bench_name, **kwargs):
        calls.append(("delete_bench", (bench_name, kwargs)))
        return True

    def _prompt(**kwargs):
        calls.append(("prompt", kwargs))
        # No answer configured means the test does not expect a question; the empty string is the
        # answer that removes nothing, so an unexpected prompt cannot pass as consent.
        return "" if answer is None else answer

    bench = MagicMock(name="Bench")
    bench.site_schemas.side_effect = _site_schemas
    bench.remove_site.side_effect = _remove_site

    service = MagicMock(name="BenchService")
    service.delete_bench.side_effect = _delete_bench
    if config_readable:
        service.get_bench.return_value = bench
    else:
        service.get_bench.side_effect = FileNotFoundError("bench_config.toml")

    with (
        patch("frappe_manager.utils.callbacks.CLI_BENCHES_DIRECTORY", root),
        patch.object(delete_cmd, "BenchService", return_value=service),
        patch.object(handler, "prompt_ask", side_effect=_prompt),
    ):
        result = runner.invoke(_app(), argv, obj={"services": MagicMock(), "verbose": False})

    return _Run(result, calls)


@pytest.fixture
def one_site(tmp_path):
    root = tmp_path / "sites"
    _bench_on_disk(root, [SITE_A])
    return root


@pytest.fixture
def two_sites(tmp_path):
    root = tmp_path / "sites"
    _bench_on_disk(root, [SITE_A, SITE_B])
    return root


# ----------------------------------------------------------------- the stand-in is faithful


@pytest.mark.parametrize(
    ("schema", "external_host"),
    [
        ("fm_a_example_com_9f2c", None),
        ("prod_erp", "rds.internal"),
        (None, None),
        (None, "rds.internal"),
    ],
)
def test_the_schema_stand_in_agrees_with_the_real_one(schema, external_host):
    """A stand-in that drifts turns every assertion in this file into a tautology. Pinned on the
    truth table rather than the field list, because which of the three groups a site falls in is
    the whole of what `fm delete` reads off a `SiteSchema`."""
    from frappe_manager.site_manager.site import SiteSchema

    real = SiteSchema(site=SITE_A, schema=schema, external_host=external_host)
    stub = _Schema(SITE_A, schema, external_host)

    assert (stub.droppable, stub.unreadable) == (real.droppable, real.unreadable)


# ------------------------------------------------- a bench with one site is unchanged


def test_a_one_site_bench_with_yes_still_deletes(one_site):
    """The teardown script that exists today. `--yes` and nothing else must keep working."""
    run = _run([BENCH, "--yes"], root=one_site, sites=[SITE_A])
    bench_name, kwargs = run.payload("delete_bench")
    assert run.result.exit_code == 0
    assert bench_name == BENCH
    assert kwargs["yes"] is True


def test_a_one_site_bench_is_never_told_to_pass_all_sites(one_site):
    run = _run([BENCH, "--yes"], root=one_site, sites=[SITE_A])
    assert "--all-sites" not in run.said


def test_a_one_site_bench_is_not_asked_to_type_its_name(one_site):
    """Without `--yes` it keeps the yes/no question `remove_bench` asks, so the command must
    neither prompt itself nor pre-answer that question by handing on yes=True."""
    run = _run([BENCH], root=one_site, sites=[SITE_A])
    assert run.index("prompt") == -1
    assert run.payload("delete_bench")[1]["yes"] is False


def test_a_bench_with_no_sites_left_deletes_without_all_sites(one_site):
    """A half-finished create leaves a bench with no site directories, and that is precisely the
    bench that needs deleting. The guard counts sites, so nothing to count gates nothing."""
    run = _run([BENCH, "--yes"], root=one_site, sites=[SITE_A], schemas=[])
    assert run.index("delete_bench") >= 0
    assert run.result.exit_code == 0


def test_an_unloadable_bench_config_deletes_without_all_sites(one_site):
    """`delete_bench` exists to clean up a bench that will not load. Its sites cannot be
    enumerated, so the guard must not be the thing that makes the cleanup impossible."""
    run = _run([BENCH, "--yes"], root=one_site, sites=[SITE_A], config_readable=False)
    assert run.index("delete_bench") >= 0
    assert run.result.exit_code == 0


# ------------------------------------------------------ N sites needs --all-sites


def test_a_multi_site_bench_is_refused_without_all_sites(two_sites):
    run = _run([BENCH, "--yes"], root=two_sites, sites=[SITE_A, SITE_B])
    assert run.result.exit_code == 1
    assert run.index("delete_bench") == -1


def test_the_refusal_names_every_site_it_would_have_destroyed(two_sites):
    """Naming the count is not enough: the operator has to recognise what is in the blast radius,
    and one of those names is how they notice they addressed the wrong bench."""
    said = _run([BENCH, "--yes"], root=two_sites, sites=[SITE_A, SITE_B]).said
    assert SITE_A in said
    assert SITE_B in said


def test_the_refusal_names_the_flag_that_unblocks_it(two_sites):
    said = _run([BENCH, "--yes"], root=two_sites, sites=[SITE_A, SITE_B]).said
    assert "--all-sites" in said


def test_the_refusal_offers_the_single_site_address_as_the_other_way_out(two_sites):
    """An operator who meant one site should not have to go and look the syntax up."""
    said = _run([BENCH, "--yes"], root=two_sites, sites=[SITE_A, SITE_B]).said
    assert f"fm delete {BENCH}/{SITE_A}" in said


def test_a_multi_site_bench_with_all_sites_and_yes_deletes(two_sites):
    run = _run([BENCH, "--all-sites", "--yes"], root=two_sites, sites=[SITE_A, SITE_B])
    bench_name, kwargs = run.payload("delete_bench")
    assert run.result.exit_code == 0
    assert bench_name == BENCH
    assert kwargs["yes"] is True
    assert run.index("prompt") == -1


# ------------------------------------------------------- typing the bench name


def test_all_sites_without_yes_asks_for_the_bench_name(two_sites):
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=BENCH)
    assert "Type the bench name" in run.payload("prompt")["prompt"]


def test_the_question_offers_no_choices_so_a_keypress_cannot_answer_it(two_sites):
    """A yes/no list is one arrow key. The whole point of this guard is that the name is typed."""
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=BENCH)
    assert run.payload("prompt").get("choices") is None


def test_the_question_names_the_flag_that_answers_it_without_a_terminal(two_sites):
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=BENCH)
    assert "--yes" in run.payload("prompt")["required_flag"]


def test_the_right_name_proceeds(two_sites):
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=BENCH)
    assert run.result.exit_code == 0
    assert run.index("prompt") < run.index("delete_bench")


def test_the_typed_name_replaces_the_yes_no_question_rather_than_adding_to_it(two_sites):
    """`remove_bench` asks its own yes/no. Asking twice about one decision trains the operator to
    answer without reading, so the name having been typed is handed on exactly as --yes is."""
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=BENCH)
    assert run.payload("delete_bench")[1]["yes"] is True


@pytest.mark.parametrize("answer", ["", "yes", "y", "no", "Shop", "shop.localhost", "shop/a.example.com"])
def test_a_wrong_answer_removes_nothing(two_sites, answer):
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=answer)
    assert run.index("delete_bench") == -1
    assert run.index("remove_site") == -1
    assert run.result.exit_code == 1


def test_a_wrong_answer_says_what_went_wrong_and_that_nothing_happened(two_sites):
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer="nope").said
    assert "not the bench name" in said
    assert "Nothing was removed" in said


def test_surrounding_whitespace_in_the_typed_name_is_tolerated(two_sites):
    """A pasted name carries a trailing space. That is the right answer, typed clumsily."""
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer=f"  {BENCH}  ")
    assert run.index("delete_bench") >= 0


# ---------------------------------------------------------------- the blast radius


def test_the_sites_are_enumerated_before_the_question_is_asked(two_sites):
    """The radius is read off disk first; a question asked before fm knows the answer is theatre."""
    run = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer="no")
    assert run.index("site_schemas") < run.index("prompt")


def test_the_blast_radius_names_the_bench_and_counts_its_sites(two_sites):
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer="no").said
    assert f"permanently delete bench '{BENCH}'" in said
    assert f"2 sites {SITE_A}, {SITE_B}" in said


def test_the_blast_radius_names_the_schemas_it_will_drop(two_sites):
    schemas = [_Schema(SITE_A, "fm_a_example_com_9f2c"), _Schema(SITE_B, "fm_b_example_com_1d4e")]
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], schemas=schemas, answer="no").said
    assert "2 schemas dropped" in said
    assert "fm_a_example_com_9f2c, fm_b_example_com_1d4e" in said
    assert "global-db" in said


def test_the_blast_radius_says_the_containers_and_workspace_go_too(two_sites):
    """The parts that have no per-site half. Leaving them implicit is how a bench-wide delete gets
    mistaken for the sum of its sites."""
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], answer="no").said
    assert "containers, workspace, certificates" in said


def test_an_external_schema_is_named_as_kept_with_its_host(two_sites):
    """An external schema is never dropped and never asked about, so saying so here is the only
    way the operator learns it survives the delete and is theirs to clean up."""
    schemas = [_Schema(SITE_A, "fm_a_example_com_9f2c"), _Schema(SITE_B, "prod_erp", "rds.internal")]
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], schemas=schemas, answer="no").said
    assert "1 schema kept" in said
    assert "prod_erp on rds.internal" in said
    assert "external, not fm's" in said


def test_an_external_schema_is_not_counted_among_the_dropped(two_sites):
    schemas = [_Schema(SITE_A, "fm_a_example_com_9f2c"), _Schema(SITE_B, "prod_erp", "rds.internal")]
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], schemas=schemas, answer="no").said
    assert "1 schema dropped" in said
    assert "prod_erp  (global-db)" not in said


def test_an_unreadable_schema_is_reported_as_unreadable(two_sites):
    """`schema is None` means site_config.json could not be read: fm cannot drop a name it does not
    know and cannot promise it is gone. That is exactly the case that orphans a schema."""
    schemas = [_Schema(SITE_A, "fm_a_example_com_9f2c"), _Schema(SITE_B)]
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], schemas=schemas, answer="no").said
    assert "1 schema unreadable" in said
    assert SITE_B in said
    assert "may be left behind" in said


def test_an_unreadable_schema_is_counted_as_neither_dropped_nor_kept(two_sites):
    schemas = [_Schema(SITE_A, "fm_a_example_com_9f2c"), _Schema(SITE_B)]
    said = _run([BENCH, "--all-sites"], root=two_sites, sites=[SITE_A, SITE_B], schemas=schemas, answer="no").said
    assert "1 schema dropped" in said
    assert "kept" not in said


# ------------------------------------------------------------- fm delete BENCH/SITE


def test_an_address_removes_that_site_and_not_the_bench(one_site):
    run = _run([f"{BENCH}/{SITE_A}", "--yes"], root=one_site, sites=[SITE_A])
    site, _ = run.payload("remove_site")
    assert site == SITE_A
    assert run.index("delete_bench") == -1
    assert run.result.exit_code == 0


def test_an_address_needs_no_all_sites_even_on_a_multi_site_bench(two_sites):
    """The address names exactly one thing, so it IS the acknowledgement."""
    run = _run([f"{BENCH}/{SITE_A}", "--yes"], root=two_sites, sites=[SITE_A, SITE_B])
    assert run.payload("remove_site")[0] == SITE_A
    assert "--all-sites" not in run.said


def test_an_address_never_enumerates_the_other_sites(two_sites):
    """Proof the multi-site guard is SKIPPED on this path rather than merely satisfied by it."""
    run = _run([f"{BENCH}/{SITE_A}", "--yes"], root=two_sites, sites=[SITE_A, SITE_B])
    assert run.index("site_schemas") == -1


def test_an_address_combined_with_all_sites_is_refused(two_sites):
    run = _run([f"{BENCH}/{SITE_A}", "--all-sites", "--yes"], root=two_sites, sites=[SITE_A, SITE_B])
    assert run.result.exit_code == 1
    assert run.index("remove_site") == -1
    assert run.index("delete_bench") == -1


def test_the_contradiction_says_the_address_already_names_one_site(two_sites):
    """Refusing without saying which half to drop leaves the operator guessing which one fm
    believed, and one of those guesses deletes the whole bench."""
    said = _run([f"{BENCH}/{SITE_A}", "--all-sites", "--yes"], root=two_sites, sites=[SITE_A, SITE_B]).said
    assert "already names exactly one site" in said
    assert f"{BENCH}/{SITE_A}" in said
    assert "--all-sites" in said


def test_an_address_asks_the_yes_no_question_not_for_a_typed_name(two_sites):
    run = _run([f"{BENCH}/{SITE_A}"], root=two_sites, sites=[SITE_A, SITE_B], answer="yes")
    asked = run.payload("prompt")
    assert asked["choices"] == ["yes", "no"]
    assert asked["default"] == "no"
    assert SITE_A in asked["prompt"]


def test_declining_the_site_removal_removes_nothing(two_sites):
    run = _run([f"{BENCH}/{SITE_A}"], root=two_sites, sites=[SITE_A, SITE_B], answer="no")
    assert run.index("remove_site") == -1
    assert run.result.exit_code == 0


def test_the_operator_is_told_the_rest_of_the_bench_survives(two_sites):
    """`fm delete` has always meant "the bench is gone". The one-site form does not, and that
    difference has to be on screen before the question, not learned afterwards."""
    said = _run([f"{BENCH}/{SITE_A}"], root=two_sites, sites=[SITE_A, SITE_B], answer="no").said
    assert "other sites keep running" in said


# --------------------------------------------------------------- the database choice


def test_the_database_choice_reaches_a_single_site_removal(one_site):
    run = _run([f"{BENCH}/{SITE_A}", "--yes", "--no-delete-db-from-global-db"], root=one_site, sites=[SITE_A])
    assert run.payload("remove_site")[1] == {"delete_db_from_global_db": False}


def test_the_database_choice_stays_bench_wide_and_tri_state(one_site):
    """Neither flag passed stays None, which is what makes fm ask. It is deliberately not per-site:
    the only sites it can apply to are the fm-managed ones."""
    run = _run([BENCH, "--yes"], root=one_site, sites=[SITE_A])
    assert run.payload("delete_bench")[1]["delete_db_from_global_db"] is None


def test_the_database_choice_reaches_a_bench_wide_delete(one_site):
    run = _run([BENCH, "--yes", "--delete-db-from-global-db"], root=one_site, sites=[SITE_A])
    assert run.payload("delete_bench")[1]["delete_db_from_global_db"] is True


# ------------------------------------------------------------------- the help surface


def test_the_help_documents_the_address():
    said = _said(runner.invoke(_app(), ["--help"]))
    assert "bench/site" in said


def test_the_help_says_what_all_sites_is_for():
    said = _said(runner.invoke(_app(), ["--help"]))
    assert "--all-sites" in said
    assert "more than one site" in said
