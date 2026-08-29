"""The `BENCH[/SITE]` positional address every `fm` command takes.

A bench holds exactly one site today and `bench.name` is simultaneously the bench
identity, the Frappe site name and the served domain, so nothing has ever needed
to say which site it means. That changes, and the address is how it will be said:
one positional argument, parsed here and nowhere else.

This module is deliberately pure. It does not call `validate_sitename`, which
reports through the global output handler, and it raises `ValueError` rather than
anything typer-shaped, because the CLI layer owns how a refusal reaches the
operator. `frappe_manager/utils/callbacks.py` translates these into
`typer.BadParameter`.

It lives in `utils/` and not `commands/` because `utils/callbacks.py` imports it
and `utils` must not import from `commands`.
"""

from dataclasses import dataclass

SEPARATOR = "/"


@dataclass(frozen=True)
class Address:
    """A parsed positional argument. `site` is None when only a bench was named."""

    bench: str
    site: str | None = None


def parse_address(raw: str) -> Address:
    """Split `BENCH[/SITE]`.

    Neither half may be empty and only one separator is allowed. Raises `ValueError`
    with a message naming the offending input, which the caller surfaces verbatim.
    """
    if not raw:
        raise ValueError("an address cannot be empty")

    if raw.count(SEPARATOR) > 1:
        raise ValueError(f"address {raw!r} has more than one {SEPARATOR!r}: write BENCH/SITE")

    if SEPARATOR not in raw:
        return Address(raw)

    bench, site = raw.split(SEPARATOR, 1)

    if not bench:
        raise ValueError(f"address {raw!r} has an empty bench: write BENCH/SITE")
    if not site:
        raise ValueError(f"address {raw!r} has an empty site: write BENCH/SITE or just BENCH")

    return Address(bench, site)
