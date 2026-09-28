"""Card component shared by ``fm list`` and ``fm info``.

Views declare content (headline, facts, sections); rendering is decided by
the active STYLE profile (``output_manager.style``: rail / box / flat /
ascii) and colored by the semantic THEME tokens (``output_manager.theme``).
Views never hardcode glyphs, layout, or colors.

``fm list`` renders collapsed cards; ``fm info`` the same card expanded with
sections -- one grammar, one product. State is always carried by TEXT
(e.g. ``running``/``stopped``); tokens only enhance it (mono-theme safe).
"""

from dataclasses import dataclass, field

from rich.console import Group, RenderableType

from frappe_manager.output_manager.style import get_output_style


class _RailLine:
    """One card line whose rail survives wrapping.

    The rail used to be concatenated onto a plain string handed to rich, which wraps with no
    knowledge that the first cells are a gutter: every continuation row started at column 0 and
    fell out of the card. At the default 80 columns that is the common case, not an edge one, and
    an image reference carrying a `@sha256:` digest is a single unbreakable ~90 character word that
    folds even on a wide terminal.

    So wrapping happens here, against the real console width, and the rail is drawn on each row it
    produces. `overflow="fold"` because a digest has nowhere to break: folding keeps it inside the
    card, where truncation would corrupt a value written to be copied.
    """

    def __init__(self, rail: str, indent: str, label: str, value: str) -> None:
        self.rail = rail
        self.indent = indent
        self.label = label
        self.value = value

    def __rich_console__(self, console, options):
        from rich.text import Text

        rail = Text.from_markup(self.rail)
        head = rail + Text(self.indent) + Text.from_markup(self.label)
        if not self.value:
            yield head
            return

        # The rail is redrawn on every continuation, and only the SPACING after it is blanked:
        # blanking the whole prefix would reintroduce the gutterless line this class exists to
        # stop. Continuations hang under the value, so a fact reads as one value.
        hang = rail + Text(" " * (head.cell_len - rail.cell_len))
        wrapped = Text.from_markup(self.value).wrap(console, max(options.max_width - head.cell_len, 1), overflow="fold")
        for index, line in enumerate(wrapped):
            row = head.copy() if index == 0 else hang.copy()
            row.append_text(line)
            yield row


@dataclass
class Card:
    """A bench (or any entity) card: headline + labeled facts + sections."""

    name: str
    meta: str
    active: bool = True
    link: str | None = None
    _rows: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, label, value)

    def fact(self, label: str, value: str) -> "Card":
        self._rows.append(("fact", label, value))
        return self

    def section(self, title: str) -> "Card":
        self._rows.append(("section", title, ""))
        return self


    def _headline(self) -> str:
        name_markup = f"[link={self.link}]{self.name}[/link]" if self.link else self.name
        token = "fm.name" if self.active else "fm.name.inactive"
        return f"[{token}]{name_markup}[/{token}]   {self.meta}"

    def _fact_line(self, label: str, value: str, prefix: str) -> str:
        width = get_output_style().label_width
        return f"{prefix}[fm.label]{label:<{width}}[/fm.label] {value}"

    def __rich__(self) -> RenderableType:
        return self.render()

    def render(self) -> RenderableType:
        style = get_output_style()
        if style.card == "box":
            return self._render_box(style)
        return self._render_rail(style)  # "rail" and "flat" share the line layout

    def _render_rail(self, style) -> RenderableType:
        rail_token = "fm.rail.active" if self.active else "fm.rail.inactive"
        glyph = style.rail_active if self.active else style.rail_inactive
        rail = f"[{rail_token}]{glyph}[/{rail_token}] " if glyph else "  "
        width = get_output_style().label_width
        lines: list[RenderableType] = [_RailLine("", "", "", self._headline())]
        for kind, label, value in self._rows:
            if kind == "section":
                lines.append(_RailLine(rail, "", "", ""))
                lines.append(_RailLine(rail, "", "", f"[fm.section]{label}[/fm.section]"))
            else:
                lines.append(_RailLine(rail, "  ", f"[fm.label]{label:<{width}}[/fm.label] ", value))
        return Group(*lines)

    def _render_box(self, style) -> RenderableType:
        from rich import box as rich_box
        from rich.panel import Panel

        lines: list[str] = []
        for kind, label, value in self._rows:
            if kind == "section":
                if lines:
                    lines.append("")
                lines.append(f"[fm.section]{label}[/fm.section]")
            else:
                lines.append(self._fact_line(label, value, ""))
        border = "fm.rail.active" if self.active else "fm.rail.inactive"
        return Panel(
            Group(*lines),
            title=self._headline(),
            title_align="left",
            border_style=border,
            box=rich_box.ROUNDED,
            expand=False,
            padding=(0, 1),
        )


def cards(items: list[Card]) -> RenderableType:
    """Render a list of cards with style-appropriate separation."""
    style = get_output_style()
    blocks: list[RenderableType] = []
    for i, card in enumerate(items):
        if i and style.card != "box":  # panels separate themselves
            blocks.append(" ")
        blocks.append(card.render())
    return Group(*blocks)


def status_dot(state: str) -> str:
    """Service state as dot + text (state text carries meaning; dot enhances)."""
    style = get_output_style()
    if state == "running":
        return f"[fm.ok]{style.dot_ok}[/fm.ok]"
    return f"[fm.error]{style.dot_bad}[/fm.error] [fm.muted]{state}:[/fm.muted]"


def bench_meta(active: bool, runtime: str, environment: str, restart_policy: str) -> str:
    """Standard bench headline meta. Status is a WORD first (text carries state;
    tokens only enhance -- mono-theme safe)."""
    status_token = "fm.status.running" if active else "fm.status.stopped"
    status_word = "running" if active else "stopped"
    env_token = "fm.env.prod" if environment == "prod" else "fm.env.dev"
    return (
        f"[{status_token}]{status_word}[/{status_token}]"
        f" [fm.muted]· {runtime} ·[/fm.muted] [{env_token}]{environment}[/{env_token}]"
        f"[fm.muted] · restart:{restart_policy}[/fm.muted]"
    )
