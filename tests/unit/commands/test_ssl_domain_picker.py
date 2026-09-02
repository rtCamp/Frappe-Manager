"""The domain half of a `BENCH/DOMAIN` address, offered rather than demanded.

`fm ssl add shop` and `fm ssl remove shop` used to run the bench picker and then refuse the answer
it produced: pick a bench from a menu, get "An address of the form BENCH/DOMAIN is required" and a
help dump. The command asked a question and then declined to use it.

They are the only two `ssl` subcommands where the second segment has no default. `list` and `renew`
both mean "every certificate this bench holds" when it is omitted, and `add`/`remove` cannot, since
a certificate is issued for one named hostname. That asymmetry is why the picker belongs on exactly
these two and nowhere else.

The property these tests defend is that the picker cannot offer a value the command then rejects.
Both callers verify a domain against `bench_config.domains` (`_remove_bench_certificate` reports
"is not configured for bench" otherwise), and `all` is the one extra form `_resolve_domains`
expands, so the offered list is derived from the same source rather than listed here.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from frappe_manager.commands.ssl.bench_helpers import _prompt_for_domain, _resolve_domains
from frappe_manager.utils.callbacks import RESERVED_BENCH_NAME

BENCH = "shop"
SERVED = ["shop.localhost", "b.example.com", "www.shop.example.com"]


def _ctx(domains, *, answer=None, raises=None):
    """A typer context whose bench serves `domains`, recording what it was asked to offer."""
    asked = {}

    def prompt_fuzzy(*, prompt, choices, **kwargs):
        asked["prompt"] = prompt
        asked["choices"] = list(choices)
        if raises is not None:
            raise raises
        return answer

    output = MagicMock()
    output.prompt_fuzzy = prompt_fuzzy
    bench = SimpleNamespace(bench_config=SimpleNamespace(domains=list(domains)))
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock(), "output_handler": output}
    return ctx, output, bench, asked


@pytest.fixture
def picker(monkeypatch):
    """`_prompt_for_domain` with `Bench.get_object` and the handler lookup pinned to the stand-in."""

    def run(domains, domain=None, *, answer=None, raises=None):
        ctx, output, bench, asked = _ctx(domains, answer=answer, raises=raises)
        monkeypatch.setattr(
            "frappe_manager.commands.ssl.bench_helpers.get_output_handler", lambda _c: output
        )
        monkeypatch.setattr(
            "frappe_manager.commands.ssl.bench_helpers.Bench.get_object",
            lambda *a, **k: bench,
        )
        return _prompt_for_domain(ctx, BENCH, domain), asked

    return run


# ------------------------- what it offers


def test_the_choices_are_whole_addresses_not_bare_hostnames(picker):
    """The argument's grammar is `BENCH/DOMAIN`, and this menu is where it is read rather than typed.

    Offering `b.example.com` beside a `[BENCH(/DOMAIN)]` usage line shows the operator the parts and
    hides the form: the same gap the metavar and parameter-name work closed everywhere else.
    """
    _, asked = picker(SERVED, answer=f"{BENCH}/{SERVED[0]}")
    assert asked["choices"] == [f"{BENCH}/{d}" for d in sorted(SERVED)] + [f"{BENCH}/{RESERVED_BENCH_NAME}"]


def test_every_offered_choice_is_one_the_command_would_accept(picker):
    """The point of the picker: choosing from it can never reach the caller's own refusal.

    `_remove_bench_certificate` rejects a domain that is not in `bench_config.domains`, and
    `_resolve_domains` treats only `all` as the expand-everything form. Anything offered has to
    satisfy one of those two, or the picker would hand the operator a value and then error on it.
    """
    _, asked = picker(SERVED, answer=f"{BENCH}/{SERVED[0]}")
    for choice in asked["choices"]:
        bench, _, part = choice.partition("/")
        assert bench == BENCH
        assert part in SERVED or part == RESERVED_BENCH_NAME


def test_the_prompt_names_the_address_rather_than_the_bench(picker):
    """The bench picker fires immediately before this one; two identical prompts read as a bug."""
    _, asked = picker(SERVED, answer=f"{BENCH}/{SERVED[0]}")
    assert "address" in asked["prompt"].lower()
    assert "select bench" not in asked["prompt"].lower()


# ------------------------- when it declines to ask


def test_an_explicit_domain_is_returned_untouched(picker):
    """A typed address is an answer already. Confirming it would be a prompt with one outcome."""
    result, asked = picker(SERVED, "b.example.com")
    assert result == "b.example.com"
    assert asked == {}


def test_a_single_domain_bench_is_still_asked_rather_than_assumed(picker):
    """The one case I got wrong first, and the sibling file already had the answer.

    Auto-answering a bench that serves one domain reads like a convenience, and `add`/`remove`
    refuse a bare `all` precisely because scope on these two is a rate limit and a blast radius.
    Filling in an incomplete address is that same inference over a smaller set: `fm ssl add shop`
    would issue a real certificate nobody named. A prompt with one option is a confirmation.
    """
    result, asked = picker(["only.localhost"], answer=f"{BENCH}/only.localhost")
    assert result == "only.localhost"
    assert asked["choices"] == [f"{BENCH}/only.localhost", f"{BENCH}/{RESERVED_BENCH_NAME}"]


def test_a_bench_serving_nothing_is_left_to_the_caller(picker):
    """An empty menu says nothing. The command's own error names the address form instead."""
    result, asked = picker([])
    assert result is None
    assert asked == {}


def test_no_terminal_leaves_the_caller_to_report_the_address(picker):
    """Scripts and cron must meet the command's error, not a prompt that cannot be answered.

    Returning None rather than raising is what keeps that message command-specific: `add` says the
    domain names the hostname a certificate is for, `remove` says it names the one to delete.
    """
    result, _ = picker(SERVED, raises=EOFError("not a terminal"))
    assert result is None


def test_an_unreadable_bench_is_left_to_the_caller(monkeypatch):
    """A half-built or unparseable bench has no domain list; the address error still fits."""
    ctx, output, _, _ = _ctx(SERVED)
    monkeypatch.setattr(
        "frappe_manager.commands.ssl.bench_helpers.get_output_handler", lambda _c: output
    )

    def explode(*a, **k):
        raise RuntimeError("bench config unreadable")

    monkeypatch.setattr("frappe_manager.commands.ssl.bench_helpers.Bench.get_object", explode)
    assert _prompt_for_domain(ctx, BENCH, None) is None


# ------------------------- the answer reaches the command


def test_the_bench_half_is_stripped_back_off_the_answer(picker):
    """Whole addresses are for reading. The callers already know the bench and validate the domain
    against that bench's own list, so returning `shop/www.shop.example.com` would fail their check
    with "is not configured for bench" on a value this function just offered."""
    result, _ = picker(SERVED, answer=f"{BENCH}/www.shop.example.com")
    assert result == "www.shop.example.com"


def test_a_dismissed_prompt_is_not_read_as_an_answer(picker):
    """`prompt_fuzzy` returning nothing must not be split into an address half."""
    result, _ = picker(SERVED, answer=None)
    assert result is None


def test_picking_all_expands_to_every_domain(monkeypatch):
    """`all` is offered because it is the only form that says "every hostname" without listing them.

    Asserted through `_resolve_domains` rather than by inspecting the string, since that function is
    what turns the picked value into the domains the command loops over.
    """
    ctx, output, bench, _ = _ctx(SERVED)
    monkeypatch.setattr(
        "frappe_manager.commands.ssl.bench_helpers.get_output_handler", lambda _c: output
    )
    monkeypatch.setattr(
        "frappe_manager.commands.ssl.bench_helpers.Bench.get_object", lambda *a, **k: bench
    )
    assert sorted(_resolve_domains(ctx, BENCH, RESERVED_BENCH_NAME)) == sorted(SERVED)
