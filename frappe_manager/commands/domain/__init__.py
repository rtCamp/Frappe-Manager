import typer
from typer_examples import install

domain_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")

install(domain_app)

from .add import add_domain
from .list import list_domains
from .remove import remove_domain

domain_app.command(name="add")(add_domain)
# `allow_extra_args`: the natural transcription of `fm domain add BENCH/SITE DOMAIN` into a
# removal puts the domain in a second positional, and Click's own "Got unexpected extra argument"
# names neither the grammar nor the command that would have worked. The command reads `ctx.args`
# and says both.
domain_app.command(name="remove", context_settings={"allow_extra_args": True})(remove_domain)
domain_app.command(name="list")(list_domains)

__all__ = ["domain_app"]
