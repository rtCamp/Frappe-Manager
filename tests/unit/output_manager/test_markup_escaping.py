r"""Config-table names in user-facing strings must survive rich.

Rich reads `[word]` as a style tag and silently drops it when it is not a known style.
Every config table fm documents is spelled exactly like a style tag, so an unescaped
`[build]` in a help string does not render as `[build]`: it renders as nothing.

That is how `fm bake --base-image` shipped with the help text "Defaults to .base_image".
The reader is told to set a key in a table whose name has been eaten.

Escaping is `\[build]` in the source, which rich renders as `[build]`.

Which paths interpret markup is NOT uniform, and that difference is the whole reason this
file exists rather than a blanket rule:

| path                               | markup interpreted | escape? |
|------------------------------------|--------------------|---------|
| typer `help=`                      | yes                | yes     |
| output handler print/warning/error | yes                | yes     |
| `@example` `detail=`               | no                 | NEVER   |
| `typer.BadParameter`               | no                 | NEVER   |

Escaping a path that does NOT interpret markup is equally wrong: the backslashes render
literally. All four rows are pinned below against the real renderers, so a rich or typer
upgrade that changes any of them fails here instead of quietly mangling the CLI.
"""

import ast
import io
import re
from pathlib import Path

import pytest
from rich.console import Console

PACKAGE = Path("frappe_manager")

# Every table fm defines in bench_config.toml. These are the words rich will eat.
TABLES = (
    "build",
    "switch",
    "workers",
    "registry",
    "database",
    "redis",
    "monitoring",
    "auth",
    "deploy",
    "apps",
    "ssl",
    "deploy_state",
)
UNESCAPED = re.compile(r"(?<!\\)\[(" + "|".join(TABLES) + r")\]")
ESCAPED = re.compile(r"\\\[")

MARKUP_METHODS = frozenset({"print", "warning", "display_error", "change_head", "exit"})
RAW_KEYWORDS = frozenset({"detail"})


def render(text: str) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=200, no_color=True, highlight=False).print(text)
    return buffer.getvalue().rstrip("\n")


def _strings_in(node: ast.AST):
    """Every string literal inside a node, including the pieces of an f-string."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            yield inner.lineno, inner.value


def _is_output_call(call: ast.Call) -> bool:
    """True for ``output.print(...)`` and ``self.output.warning(...)``."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in MARKUP_METHODS:
        return False
    owner = func.value
    if isinstance(owner, ast.Name) and owner.id == "output":
        return True
    return isinstance(owner, ast.Attribute) and owner.attr == "output"


def _markup_strings():
    """Strings handed to a markup-interpreting renderer, located by AST.

    Deliberately not a line-window heuristic: that cannot tell a docstring which merely
    sits near an ``output.print`` from an argument to one, and it flagged exactly that.
    """
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_output_call(node):
                for arg in node.args:
                    yield from ((path, n, s) for n, s in _strings_in(arg))
            for keyword in node.keywords:
                if keyword.arg == "help":
                    yield from ((path, n, s) for n, s in _strings_in(keyword.value))


def _verbatim_strings():
    """Strings rendered as typed: `@example` details and BadParameter messages."""
    for path, tree in _parsed():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg in RAW_KEYWORDS:
                    yield from ((path, n, s) for n, s in _strings_in(keyword.value))
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "BadParameter":
                for arg in node.args:
                    yield from ((path, n, s) for n, s in _strings_in(arg))


def _parsed():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, ast.parse(path.read_text())


class TestSourceStrings:
    def test_no_config_table_name_is_eaten_by_rich(self):
        """An unescaped table name in a help string or handler message renders as nothing."""
        offenders = [f"{path}:{line}: {text[:90]}" for path, line, text in _markup_strings() if UNESCAPED.search(text)]

        assert offenders == [], "unescaped table names, rich drops these:\n" + "\n".join(offenders)

    def test_no_backslash_leaks_into_a_string_rendered_verbatim(self):
        r"""`@example` details and BadParameter messages are not markup, so a `\[` shows up."""
        offenders = [f"{path}:{line}: {text[:90]}" for path, line, text in _verbatim_strings() if ESCAPED.search(text)]

        assert offenders == [], "escaped brackets in verbatim strings, backslashes show:\n" + "\n".join(offenders)

    def test_the_scan_actually_reaches_the_strings_it_claims_to_check(self):
        """A selector that matches nothing would pass both checks above forever."""
        assert sum(1 for _ in _markup_strings()) > 200
        assert sum(1 for _ in _verbatim_strings()) > 50


class TestRendererBehaviour:
    """The table in the module docstring is a claim about rich and typer. Pin it."""

    def test_rich_eats_an_unescaped_table_name(self):
        assert render("Defaults to [build].push") == "Defaults to .push"

    def test_the_escape_produces_a_literal_bracket(self):
        assert render(r"Defaults to \[build].push") == "Defaults to [build].push"

    def test_a_doubled_bracket_does_not_self_escape(self):
        """`[[apps]]` looks like it escapes itself. It does not; it collapses to `[]`."""
        assert render("[[apps]]") == "[]"

    def test_the_output_handler_shares_rich_markup_semantics(self):
        """The handler is not a separate escaping regime; it is a rich Console underneath."""
        from frappe_manager.output_manager.rich_output import RichOutputHandler

        handler = RichOutputHandler()
        buffer = io.StringIO()
        handler.stderr = Console(file=buffer, width=200, no_color=True, highlight=False)
        handler.print("Wrote [switch].migrate = false")

        assert "Wrote .migrate = false" in buffer.getvalue()

    def test_typer_bad_parameter_does_not_interpret_markup(self):
        """Which is why escaping one would leak backslashes to the user."""
        import typer
        from typer.testing import CliRunner

        app = typer.Typer()

        @app.command()
        def cmd(value: str = "x"):
            raise typer.BadParameter("declare [database] in an overlay")

        result = CliRunner().invoke(app, [])

        assert "[database]" in result.output


@pytest.mark.parametrize("table", TABLES)
def test_every_declared_table_is_a_word_rich_would_eat(table: str):
    """TABLES is only meaningful if these names really are style-shaped. If rich stops
    treating a bare word as a tag, this check is obsolete rather than silently passing."""
    assert f"[{table}]" not in render(f"see [{table}] here")
