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
    bench_served_domain_callback,
    bench_site_all_callback,
    bench_site_autocompletion_callback,
    bench_site_callback,
    sitename_callback,
    sites_autocompletion_callback,
)

# Metavars use `BENCH(/SITE)` rather than the conventional `BENCH[/SITE]`, and not by preference.
# Rich reads `[/SITE]` as a CLOSING markup tag and raises MarkupError, which takes down any path
# that prints a usage line through fm's output handler. `[SITE]` without the slash survives but
# reads as a second argument rather than an optional suffix, so parentheses carry the optionality
# and the slash carries the grammar. See tests/unit/output_manager/test_markup_escaping.py.
BenchNameArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH",
        help="Bench to act on. Omit to pick from the benches you have.",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
]
"""Optional, validated bench name. Call sites keep their `= None` default."""

BenchAllArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH|all",
        help="Bench to act on, or 'all' for every bench fm manages. Omit to act on nothing but fm itself.",
        autocompletion=bench_all_autocompletion_callback,
        callback=bench_all_callback,
    ),
]
"""Bench name or the `all` address, for the commands that can run over every bench at once."""

RequiredBenchNameArgument = Annotated[
    str,
    typer.Argument(
        metavar="BENCH",
        help="Bench to act on.",
        autocompletion=sites_autocompletion_callback,
        callback=sitename_callback,
    ),
]
"""Validated bench name that must be given explicitly. Declared with no default."""

BenchSiteArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH(/SITE)",
        help="Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.",
        autocompletion=bench_site_autocompletion_callback,
        callback=bench_site_callback,
    ),
]
"""Optional address. The only alias that accepts a site part; `fm shell`, `fm delete` and `fm reset` use it."""

BenchSiteAllArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH(/SITE|all)",
        help="Bench, BENCH/SITE for one of its sites, or BENCH/all for every site it serves.",
        autocompletion=bench_site_autocompletion_callback,
        callback=bench_site_all_callback,
    ),
]
"""The same address as :data:`BenchSiteArgument`, plus `BENCH/all`.

A separate alias AND a separate callback, unlike the domain pair below. The domain callback does no
must-exist check, so `all` passes through it for free and each body decides; the site callback does
check, so `all` needs explicit permission. Granting it in the shared callback would have made
`fm delete shop/all` and `fm reset shop/all` parse, and a body that forgot to refuse would drop or
reinstall every schema on the bench. `fm update` uses this because installing an app is per-site
work that legitimately fans out; nothing else needs it yet."""

BenchDomainArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH(/DOMAIN)",
        help="Bench, or BENCH/DOMAIN to act on one hostname it serves. 'BENCH/all' means every domain of that bench; a bare domain is for --standalone.",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
]
"""Optional address whose second segment is a served DOMAIN, not a site.

The `ssl` commands use it: a certificate is keyed by domain, and a bench serves its sites' names
AND their aliases, so the population is wider than `BenchSiteArgument`'s. It does no must-exist
check, because `--standalone` puts an external domain in this same position."""

BenchServedDomainArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH(/DOMAIN)",
        help="Bench, or BENCH/DOMAIN to reach one hostname it serves. Without a domain part, the bench's primary site is used.",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_served_domain_callback,
    ),
]
"""The same second segment as :data:`BenchDomainArgument`, but the bench must EXIST.

Same metavar, different callback. The `ssl` commands accept a domain belonging to no bench under
`--standalone`, so theirs cannot require one; a command without that mode keeps the CWD fallback,
the picker and the missing-bench refusal that every other bench argument has. `fm ngrok` uses it:
the tunnel rewrites the `Host:` header, and a host can be an alias, so the population is domains
rather than sites."""

BenchDomainAllArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH(/DOMAIN)|all",
        help="Bench, BENCH/DOMAIN for one hostname, 'BENCH/all' for every domain of that bench, or 'all' for every bench. A bare domain is for --standalone.",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
]
"""The same address as :data:`BenchDomainArgument`, for the `ssl` commands that also accept a bare
`all`.

Same callback and same completion: the difference is only which forms the command will act on, and
that is enforced in each body. It is a separate alias so the USAGE LINE cannot claim a form the
command refuses. `fm ssl add all` and `fm ssl remove all` are refused (a certificate per domain of
every bench crosses Let's Encrypt's rate limit, and dropping every certificate is a fleet-wide move
to plain HTTP), while `fm ssl renew all` is the whole point of having it."""

BenchOnlyAllArgument = Annotated[
    str | None,
    typer.Argument(
        metavar="BENCH|all",
        help="Bench, or 'all' for every bench and the external domains together. Naming a single domain is refused: this reports every certificate the bench holds.",
        autocompletion=bench_domain_autocompletion_callback,
        callback=bench_domain_callback,
    ),
]
"""`BENCH|all` for `fm ssl list`, which reports per BENCH and refuses a domain part.

Carries the domain callback so a slashed value is still parsed and reported by the command rather
than dying in the parser, but the metavar does not offer a form the command will not act on."""
