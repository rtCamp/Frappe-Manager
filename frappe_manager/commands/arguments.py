"""Shared `Annotated` aliases for the `benchname` positional argument.

Most `fm` commands take the same positional bench name, and the declaration --
help text, shell completion, and the `sitename_callback` validator that also
normalises a bare name to `<name>.localhost` -- used to be copy-pasted into
every command module. These aliases are that one declaration, so a change to the
help text or the validator lands on every command at once instead of drifting.

There are deliberately THREE aliases, not one. They differ in ways a user or a
shell can observe, so they are not interchangeable:

* :data:`BenchNameArgument` -- optional, validated. The default for a command
  that operates on an existing bench: omitting the name falls back to the bench
  in the current directory and, failing that, `sitename_callback` opens an
  interactive picker.

* :data:`RequiredBenchNameArgument` -- same help text and same validator, but
  REQUIRED. Used by `fm prune` and `fm switch`. This is the trap: because the
  help text is identical, the two look like duplicates of the optional alias and
  they are not. Making them optional hands a bench-less invocation to
  `sitename_callback`'s interactive picker, i.e. `fm prune` with no arguments
  would silently offer to prune whatever bench the picker lands on instead of
  refusing. Do not collapse these two aliases into one.

* :data:`StandaloneBenchNameArgument` -- optional and deliberately UNvalidated,
  for the `fm ssl` subcommands. They also manage certificates for domains that
  belong to no bench, so the must-exist check of `sitename_callback` would
  reject valid input; they resolve the bench themselves.

Commands whose `benchname` genuinely differs (`create`, `migrate`,
`self compose`, `bake`, `deploy`, `maintenance`, `ssl dns-config cloudflare`)
keep their own inline declaration on purpose and must NOT be moved here.
"""

from typing import Annotated

import typer

from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback

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
    ),
]
"""Optional bench name with completion but no must-exist validation."""
