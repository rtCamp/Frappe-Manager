"""Shared `Annotated` aliases for the `benchname` positional argument.

Most `fm` commands take the same positional bench name, and the declaration --
help text, shell completion, and the `sitename_callback` validator that also
normalises a bare name to `<name>.localhost` -- used to be copy-pasted into
every command module. These aliases are that one declaration, so a change to the
help text or the validator lands on every command at once instead of drifting.

There are deliberately FOUR aliases, not one. They differ in ways a user or a
shell can observe, so they are not interchangeable:

* :data:`BenchNameArgument` -- optional, validated. The default for a command
  that operates on an existing bench: omitting the name falls back to the bench
  in the current directory and, failing that, `sitename_callback` opens an
  interactive picker. An address carrying a site part is REFUSED, because these
  commands act on the whole bench.

* :data:`RequiredBenchNameArgument` -- same help text and same validator, but
  REQUIRED. Used by `fm prune` and `fm switch`. This is the trap: because the
  help text is identical, the two look like duplicates of the optional alias and
  they are not. Making them optional hands a bench-less invocation to
  `sitename_callback`'s interactive picker, i.e. `fm prune` with no arguments
  would silently offer to prune whatever bench the picker lands on instead of
  refusing. Do not collapse these two aliases into one.

* :data:`StandaloneBenchNameArgument` -- optional, and deliberately WITHOUT the
  must-exist check, for the `fm ssl` subcommands. They also manage certificates
  for domains that belong to no bench, so `sitename_callback`'s must-exist check
  would reject valid input; they resolve the bench themselves. It does carry
  `standalone_address_callback`, which parses the address and refuses a site part
  but neither normalises the name nor requires the bench to exist. Without it a
  slashed value reached `Bench.get_object` and died as a not-found error on a
  nested path.

* :data:`BenchSiteArgument` -- optional, validated, and the ONLY alias that
  accepts a site part. Three commands use it, for two different reasons.
  `fm shell` addresses a site because `FRAPPE_SITE` in the container makes bare
  `bench` commands inside the shell target it. `fm delete` and `fm reset`
  address one because a bench holds several sites now, and destroying one site
  is not destroying the bench. The bench name is what reaches the command body,
  exactly as with the other aliases; the site rides on `ctx.obj["site"]`.

Commands whose `benchname` genuinely differs (`create`, `migrate`,
`self compose`, `bake`, `deploy`, `maintenance`, `ssl dns-config cloudflare`)
keep their own inline declaration on purpose and must NOT be moved here.
"""

from typing import Annotated

import typer

from frappe_manager.utils.callbacks import (
    bench_site_callback,
    sitename_callback,
    sites_autocompletion_callback,
    standalone_address_callback,
)

BenchNameArgument = Annotated[
    str | None,
    typer.Argument(
        help="Name of the bench.",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
]
"""Optional, validated bench name. Call sites keep their `= None` default."""

RequiredBenchNameArgument = Annotated[
    str,
    typer.Argument(
        help="Name of the bench.",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
]
"""Validated bench name that must be given explicitly. Declared with no default."""

StandaloneBenchNameArgument = Annotated[
    str | None,
    typer.Argument(
        help="Name of the bench (omit for standalone mode).",
        autocompletion=sites_autocompletion_callback,
        callback=standalone_address_callback,
    ),
]
"""Optional bench name, address-parsed but with no must-exist validation."""

BenchSiteArgument = Annotated[
    str | None,
    typer.Argument(
        help="Bench, or bench/site.",
        autocompletion=sites_autocompletion_callback,
        callback=bench_site_callback,
    ),
]
"""Optional address. The only alias that accepts a site part; `fm shell`, `fm delete` and `fm reset` use it."""
