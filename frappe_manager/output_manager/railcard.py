"""Rail-card rendering grammar shared by ``fm list`` and ``fm info``.

One visual language across the inventory and detail views: a status-colored
left rail (green heavy bar = active, dim light bar = inactive), a bold
headline (name + dim meta), and dim right-padded fact labels. ``fm list``
renders collapsed cards; ``fm info`` renders the same card expanded with
sections. Machine escape hatches (``--json``/``--paths``) bypass this
entirely.
"""

_LABEL_WIDTH = 9


def rail(active: bool) -> str:
    return "[green]┃[/green]" if active else "[dim]│[/dim]"


def headline(name: str, meta: str, active: bool, link: str | None = None) -> str:
    """Card head: bare bold (linked) name + caller-marked meta.

    No rail on the headline -- the rail hangs BELOW it (from the fact lines),
    so the card reads as a title generating its own line.
    """
    name_markup = f"[link={link}]{name}[/link]" if link else name
    style = "bold" if active else "bold dim"
    return f"[{style}]{name_markup}[/{style}]   {meta}"


def fact(label: str, value: str, active: bool) -> str:
    """One `label  value` line under the rail; empty label continues the previous fact."""
    return f"{rail(active)}   [dim]{label:<{_LABEL_WIDTH}}[/dim] {value}"


def blank(active: bool) -> str:
    return rail(active)


def section(title: str, active: bool) -> list[str]:
    """Section separator: blank rail line + dim bold title."""
    return [blank(active), f"{rail(active)} [bold dim]{title}[/bold dim]"]
