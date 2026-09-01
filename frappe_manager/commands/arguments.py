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

* :data:`BenchDomainArgument` -- optional, and deliberately WITHOUT the must-exist
  check, for the `fm ssl` subcommands. They also manage certificates for domains
  that belong to no bench, so `sitename_callback`'s must-exist check would reject
  valid input; they resolve the bench themselves. Its second segment is a served
  DOMAIN, not a site: a certificate is keyed by hostname, so a bench's aliases are
  addressable here and are not in `BenchSiteArgument`.

* :data:`BenchAllArgument` -- a bench name or the `all` address, for `fm migrate`,
  the one bench-scoped command that can run over every bench at once. `all` is a
  reserved word refused as a bench NAME at create time, which is what lets it mean
  something else in this position without ambiguity.

* :data:`BenchSiteArgument` -- optional, validated, and the ONLY alias that
  accepts a site part. Three commands use it, for two different reasons.
  `fm shell` addresses a site because `FRAPPE_SITE` in the container makes bare
  `bench` commands inside the shell target it. `fm delete` and `fm reset`
  address one because a bench holds several sites now, and destroying one site
  is not destroying the bench. The bench name is what reaches the command body,
  exactly as with the other aliases; the site rides on `ctx.obj["site"]`.
  It is also the only alias whose shell completion offers sites: the others carry
  `sites_autocompletion_callback`, which completes bench names alone, so an
  argument that refuses a site part can never complete the operator into one.

Commands whose `benchname` genuinely differs (`create`, `migrate`,
`self compose`, `bake`, `deploy`, `maintenance`, `ssl dns-config cloudflare`)
keep their own inline declaration on purpose and must NOT be moved here.
"""

from typing import Annotated

import typer

from frappe_manager.utils.callbacks import (
    bench_all_autocompletion_callback,
    bench_all_callback,
    bench_domain_autocompletion_callback,
    bench_domain_callback,
    bench_site_autocompletion_callback,
    bench_site_callback,
    sitename_callback,
    sites_autocompletion_callback,
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

BenchAllArgument = Annotated[
    str | None,
    typer.Argument(
        help="Name of the bench, or all.",
        autocompletion=bench_all_autocompletion_callback,
        callback=bench_all_callback,
    ),
]
"""Bench name or the `all` address, for the commands that can run over every bench at once."""

RequiredBenchNameArgument = Annotated[
    str,
    typer.Argument(
        help="Name of the bench.",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
]
"""Validated bench name that must be given explicitly. Declared with no default."""

BenchSiteArgument = Annotated[
    str | None,
    typer.Argument(
        help="Bench, or bench/site.",
        autocompletion=bench_site_autocompletion_callback,
        callback=bench_site_callback,
    ),
]
"""Optional address. The only alias that accepts a site part; `fm shell`, `fm delete` and `fm reset` use it."""

BenchDomainArgument = Annotated[
    str | None,
    typer.Argument(
        help="Bench, bench/domain, or all.",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
]
"""Optional address whose second segment is a served DOMAIN, not a site.

The `ssl` commands use it: a certificate is keyed by domain, and a bench serves its sites' names
AND their aliases, so the population is wider than `BenchSiteArgument`'s. Like the standalone
alias it does no must-exist check, because `--standalone` puts an external domain in this same
position."""
