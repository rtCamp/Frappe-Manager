"""One bench menu, two callers that disagree about an unanswerable prompt.

The prompt string and its option list used to be written twice, and a third copy arrived with the
`ssl add`/`ssl remove` address picker. They are one function now, `_pick_bench_name`, which is only
safe to share because the thing that actually differs between the callers is their FAILURE, not
their menu:

- `_resolve_bench` backs a parameter callback. There is nothing else the command can mean, so no
  terminal raises `NonInteractiveError` naming the argument to pass instead.
- `prompt_for_bench_selection` backs an `ssl` command body. That argument may still be an external
  domain under `--standalone`, so the bench cannot be demanded in the callback and no terminal is
  "no answer": the body reports the address form itself.

Swapping those two is invisible to every other test in the suite, and would turn a script's clear
refusal into a stack trace or a stack trace into silence. That is what this file pins.
"""

from unittest.mock import patch

import pytest
import typer

from frappe_manager.exceptions import NonInteractiveError
from frappe_manager.utils.callbacks import (
    _pick_bench_name,
    _resolve_bench,
    prompt_for_bench_selection,
)

CALLBACKS = "frappe_manager.utils.callbacks"
BENCHES = ["annex", "shop"]


@pytest.fixture
def no_terminal():
    """Benches exist, the CWD is not one of them, and the prompt cannot be answered."""
    with (
        patch(f"{CALLBACKS}._bench_names", return_value=list(BENCHES)),
        patch(f"{CALLBACKS}.get_sitename_from_current_path", return_value=None),
        patch(f"{CALLBACKS}.get_global_output_handler") as handler,
        patch(f"{CALLBACKS}.update_sites_cache"),
    ):
        handler.return_value.prompt_fuzzy.side_effect = EOFError("not a terminal")
        yield handler.return_value.prompt_fuzzy


@pytest.fixture
def picks():
    """The same world, with a terminal that takes the first bench offered."""
    with (
        patch(f"{CALLBACKS}._bench_names", return_value=list(BENCHES)),
        patch(f"{CALLBACKS}.get_sitename_from_current_path", return_value=None),
        patch(f"{CALLBACKS}.get_global_output_handler") as handler,
        patch(f"{CALLBACKS}.update_sites_cache") as cache,
    ):
        handler.return_value.prompt_fuzzy.return_value = BENCHES[0]
        yield handler.return_value.prompt_fuzzy, cache


# ------------------------- the failure each caller wants


def test_a_parameter_callback_refuses_by_name_when_it_cannot_ask(no_terminal):
    """`fm start` in a script has no second meaning, so it says which argument was missing."""
    with pytest.raises(NonInteractiveError) as exc:
        _resolve_bench(None)

    assert "non-interactive" in str(exc.value).lower()
    assert no_terminal.called


def test_an_ssl_command_body_gets_no_answer_rather_than_an_exception(no_terminal):
    """`--standalone` means the same argument can be an external domain, so the body decides."""
    assert prompt_for_bench_selection(None) is None
    assert no_terminal.called


def test_the_shared_picker_lets_the_failure_through_to_them(no_terminal):
    """Neither semantic can be built on a helper that swallows: it has to propagate."""
    with pytest.raises(EOFError):
        _pick_bench_name()


# ------------------------- the menu they share


def test_both_callers_route_through_the_one_shared_picker():
    """Comparing the two callers' prompts to each other cannot fail once they ARE one call, which a
    mutation proved: renaming the prompt changed both and the assertion held.

    What can still regress is a third copy appearing, the way the address picker nearly became one.
    So this stubs the shared function and requires both callers to be the ones that hit it: an
    inlined `prompt_fuzzy` beside it would leave this unhit and fail here.
    """
    with patch(f"{CALLBACKS}._pick_bench_name", return_value=BENCHES[0]) as shared:
        assert prompt_for_bench_selection(None) == BENCHES[0]
        assert shared.call_count == 1

        with (
            patch(f"{CALLBACKS}.get_sitename_from_current_path", return_value=None),
            patch(f"{CALLBACKS}.validate_sitename", side_effect=lambda n: f"{n}.localhost"),
            patch("pathlib.Path.exists", return_value=True),
        ):
            assert _resolve_bench(None) == BENCHES[0]
        assert shared.call_count == 2


def test_the_bench_menu_is_not_mistakable_for_the_address_menu(picks):
    """Both fire back to back on a bare `fm ssl add`, so two identically worded prompts read as a
    bug rather than as two questions. This is the half of "same prompt" that can still fail."""
    prompt_fuzzy, _ = picks
    prompt_for_bench_selection(None)

    assert "bench" in prompt_fuzzy.call_args.kwargs["prompt"].lower()
    assert "address" not in prompt_fuzzy.call_args.kwargs["prompt"].lower()


def test_the_menu_is_the_benches_that_exist(picks):
    prompt_fuzzy, _ = picks
    prompt_for_bench_selection(None)

    assert sorted(prompt_fuzzy.call_args.kwargs["choices"]) == sorted(BENCHES)


def test_a_pick_is_remembered_for_the_next_invocation(picks):
    """The recent-sites cache is what puts your last bench at the top of the list next time."""
    prompt_fuzzy, cache = picks
    assert prompt_for_bench_selection(None) == BENCHES[0]
    cache.assert_called_once_with(BENCHES[0])


# ------------------------- what still short-circuits the menu


def test_a_named_bench_never_opens_the_menu(picks):
    prompt_fuzzy, _ = picks
    assert prompt_for_bench_selection("shop") == "shop"
    assert not prompt_fuzzy.called


def test_the_cwd_answers_before_the_menu_does(picks):
    """Standing inside a bench directory is already an answer; asking would be theatre."""
    prompt_fuzzy, _ = picks
    with patch(f"{CALLBACKS}.get_sitename_from_current_path", return_value="annex"):
        assert prompt_for_bench_selection(None) == "annex"
    assert not prompt_fuzzy.called


def test_no_benches_at_all_is_not_an_empty_menu(picks):
    """An empty fuzzy list is a dead end with no way out; the caller reports it instead."""
    prompt_fuzzy, _ = picks
    with patch(f"{CALLBACKS}._bench_names", return_value=[]):
        assert prompt_for_bench_selection(None) is None
        with pytest.raises(typer.BadParameter):
            _resolve_bench(None)
    assert not prompt_fuzzy.called
