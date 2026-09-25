"""What a command needs from its host, declared ON the command.

The gates in ``app_callback`` (docker daemon, migration state, config readability, stack bringup)
have to know WHICH command is running before they run. Click resolves that a frame above the
callback -- ``Group.invoke`` calls ``resolve_command`` and only then invokes the group callback --
but it hands the callback nothing except ``ctx.invoked_subcommand``, which is the immediate child
only: "ssl", never "ssl ca status".

Reading ``sys.argv`` to recover the rest is what this module replaces. That re-parse has to know
every global option and which of them consume a value, and it silently mis-resolves when it does
not: ``fm --json ssl ca status`` resolved to "ssl", re-arming the very gates the command is exempt
from. Click has already parsed those options correctly by the time the root group is invoked.

So the root group walks the resolved command chain ONCE, up front, and records it. Requirements
are then a property of the command object, the way git's ``commands[]`` table carries RUN_SETUP
and NEED_WORK_TREE per builtin rather than matching command names in its dispatcher.

Declaring is positive: a command that says nothing gets every gate. Forgetting an annotation
yields a command that refuses too eagerly and is noticed, not one that skips a precondition.
"""

import builtins

import click
from typer.core import TyperCommand, TyperGroup

# ctx.meta is shared across the whole context tree, so the root group can record here and any
# callback can read it.
META_CHAIN = "fm.command_chain"
META_PATH = "fm.command_path"
META_ARGS = "fm.command_args"


class BrokenHostCommand(TyperCommand):
    """A command that must run on a host whose docker, migration state or config is broken.

    Teardown only: this is how you get fm OFF a machine, so its preconditions are the very things
    it removes. Being read-only is not a qualification.
    """

    tolerates_broken_host = True


class BrokenHostGroup(TyperGroup):
    """A whole group of :class:`BrokenHostCommand`; the flag applies to every command under it."""

    tolerates_broken_host = True


def record_command_chain(group: click.Group, ctx: click.Context) -> None:
    """Resolve the full command chain from the args click has already parsed.

    Stops at the first token that is not a subcommand of the current group -- a flag, a bench
    name, a typo. A typo is left for click's own error, which names the valid commands.
    """
    # `_protected_args` is click's own split of the group's leftovers; `Group.invoke` reads it the
    # same way one frame later, and it is cleared before the callback. The public `protected_args`
    # warns as deprecated on click 8 and goes away in click 9, where `args` carries every remaining
    # token on its own -- so read the private name defensively rather than hard-failing on upgrade.
    args = [*getattr(ctx, "_protected_args", []), *ctx.args]
    chain: builtins.list[click.Command] = []
    current: click.Command = group

    while args and isinstance(current, click.Group):
        found = current.get_command(ctx, args[0])
        if found is None:
            break
        chain.append(found)
        current = found
        args = args[1:]

    ctx.meta[META_CHAIN] = chain
    ctx.meta[META_PATH] = " ".join(command.name or "" for command in chain)
    ctx.meta[META_ARGS] = args


class FMGroup(TyperGroup):
    """The root group. Records the command chain before the callback that gates on it runs."""

    def invoke(self, ctx: click.Context):
        record_command_chain(self, ctx)
        return super().invoke(ctx)


def command_path(ctx: click.Context) -> str:
    """The resolved command, as a full path: "start", "ssl add", "ssl ca status"."""
    return ctx.meta.get(META_PATH, "")


def command_args(ctx: click.Context) -> "builtins.list[str]":
    """What follows the command: its arguments and its own flags, global flags already removed."""
    return ctx.meta.get(META_ARGS, [])


def tolerates_broken_host(ctx: click.Context) -> bool:
    """True when the command, or a group it sits under, is declared a teardown."""
    return any(getattr(command, "tolerates_broken_host", False) for command in ctx.meta.get(META_CHAIN, ()))


def will_print_help(ctx: click.Context) -> bool:
    """Whether click is about to print help instead of running the command.

    The callback's setup -- the docker probe, the migration gate, the host lock, creating and
    starting the shared stack, and on a first install pulling every image -- is pure waste when
    the answer is a help page. Click cannot be asked: its help option is eager on the SUBcommand's
    context, which is built after this group callback has already run and done all of that.

    Cobra, which docker's CLI is built on, returns `flag.ErrHelp` before any `PersistentPreRunE`
    hook, so this question never reaches docker's own code. Click inverts that order, so fm has to
    answer it -- but it answers the way cobra decides: from what the command DECLARES, never by
    searching the command line for text. The old check globbed sys.argv into one string and looked
    for "--help" anywhere in it, so `fm compose BENCH ps --format '{{.Name}} --help'` silently
    skipped every gate above.
    """
    chain = ctx.meta.get(META_CHAIN) or []
    if not chain:
        # Root-level `fm --help` never gets here: click's eager help exits inside `make_context`,
        # before the group is ever invoked.
        return False

    command = chain[-1]
    args = command_args(ctx)

    # Everything after `--` is an argument, never an option -- `fm shell BENCH -- --help` asks the
    # container for help, not fm.
    options = args[: args.index("--")] if "--" in args else args
    if set(options) & set(command.get_help_option_names(ctx)):
        return True

    # Cobra's second help return is `!c.Runnable()`; in click terms that is a command (or group)
    # that declares it shows help when given nothing.
    return bool(getattr(command, "no_args_is_help", False)) and not args
