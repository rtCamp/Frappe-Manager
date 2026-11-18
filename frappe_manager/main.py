import atexit
import os
import signal
import sys
from pathlib import Path

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.logger import get_logger, log
from frappe_manager.output_manager.globals import get_global_output_handler, set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler

# frappe_manager.commands, utils.docker and utils.helpers are imported at their use sites BELOW
# the root check, not here: each calls get_logger() at module scope, opening logs/fm.log as an
# import side effect, so hoisting them to the top would run that before cli_entrypoint() gets to
# refuse root -- `sudo -E fm` would then leave a root-owned fm.log the next non-root fm run cannot
# write.


def _run_app(app) -> None:
    """Invoke the CLI, answering a USAGE error in JSON when the caller asked for JSON.

    A stray argument is rejected by click before any command body runs, so the per-command error
    path cannot see it: four of the twelve `--json` commands answered their most likely mistake
    with rich usage text on stdout and no JSON at all. Standalone mode is given up only for a
    `--json` run, so the human path keeps click's own rendering and exit codes untouched.
    """
    # typer 0.27 vendored click: `main()` below raises typer's OWN UsageError, and catching
    # click's lets a `--json` usage error escape with empty stdout, which is the bug this guards.
    from typer._click.exceptions import UsageError

    if not _argv_asks_for_json():
        app()
        return

    from typer.main import get_command

    try:
        # Click RETURNS a typer.Exit's code here rather than exiting, because standalone mode is
        # what normally turns it into SystemExit. Dropping this value made every `--json` command
        # that REFUSES -- the migration gate, a guard that exits 1 -- report success with empty
        # output, while the same command without `--json` printed its reason and exited 1.
        code = get_command(app).main(standalone_mode=False)
        if code:
            raise SystemExit(code)
    except UsageError as e:
        from frappe_manager.exceptions import MissingArgumentError

        hint = f"Run 'fm {e.ctx.command_path.removeprefix('fm ')} --help'" if e.ctx else "Pass --help"
        _emit_json_error(MissingArgumentError(e.format_message(), [hint]), get_global_output_handler())
        raise SystemExit(e.exit_code) from e


def _argv_asks_for_json() -> bool:
    """`--json` before any `--`; after it the words belong to the child command."""
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[: argv.index("--")]
    return "--json" in argv


def cli_entrypoint():
    """
    Main CLI entry point.

    Initializes a basic RichOutputHandler early; app_callback (commands/__init__.py) replaces it
    with a SinkOutputHandler once the command line is parsed, attaching the file sink and, under
    `--events json`, the stream sink.
    """
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    basic_handler = RichOutputHandler()
    set_global_output_handler(basic_handler)

    # Apply output theme/style early (env/default) so even pre-config output is
    # themed; re-applied with fm_config values in app_callback.
    from frappe_manager.output_manager.style import set_output_style
    from frappe_manager.output_manager.theme import apply_output_theme

    try:
        apply_output_theme()
        set_output_style()
    except Exception as e:  # cosmetic subsystem: NEVER brick the CLI
        basic_handler.warning(f"Output theme/style: {e} -- using defaults.")
        os.environ.pop("FM_THEME", None)
        os.environ.pop("FM_STYLE", None)
        apply_output_theme()
        set_output_style()

    # Refuse root before app() runs, before app_callback creates CLI_DIR or any command touches
    # disk/docker: as root, Frappe's own bench exits 1 unless `frappe_user` is set (fm does not
    # set it, so web/workers land in FATAL behind a 502), the fixedly-named service containers
    # (fm_mariadb, fm_nginx-proxy) collide with a non-root fm on the same host, and anything
    # written before the refusal is root-owned inside ~/frappe, requiring sudo to remove.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # display_error + SystemExit rather than handler.exit(os_exit=True): the latter routes
        # through builtins `exit`, which only exists while `site` is loaded, and this refusal has
        # to hold in every packaging of fm. Same :no_entry: styling either way.
        basic_handler.display_error(
            "fm must not run as root. Run it as the user that owns the benches, "
            "and put that user in the 'docker' group if docker is unreachable."
        )
        raise SystemExit(1)

    # click runs the program with _FM_COMPLETE set to collect tab completions; a stray line there corrupts them.
    # After the root refusal on purpose: `sudo fm` must refuse without nagging about a rename first.
    if Path(sys.argv[0]).name == "fm" and "_FM_COMPLETE" not in os.environ:
        basic_handler.warning("'fm' is being renamed to 'fmx' and will stop working in 1.0.0; use 'fmx'.")

    # Deferred on purpose: see the import comment at the top of this module.
    from frappe_manager.commands import app

    try:
        _run_app(app)
        _emit_json_exit(ok=True, code=0)
    except SystemExit as e:
        # typer.Exit / click abort paths: SystemExit is not an Exception, so it
        # bypasses the handlers below; the --events stream still gets its terminal event.
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        _emit_json_exit(ok=code == 0, code=code)
        raise
    except FrappeManagerException as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)
        logger = get_logger(component="main")
        output = get_global_output_handler()

        # JSON mode replaces the HUMAN rendering and nothing else: the file log and the teardown
        # below still run. Exiting here instead made a failed `--json` run the one failure fm
        # never recorded, which is the opposite of what the file is for.
        rendered_as_json = _emit_json_error(e, output)

        if not rendered_as_json:
            # No "Error Occurred" prefix: the ⛔ glyph and the red style already say that, and the
            # words pushed the part that matters -- what went wrong -- further right on every error.
            output.display_error(str(e).strip())

            # getattr, not e.details: this is the last handler standing, and must not itself raise
            # on an exception whose __init__ never reached FrappeManagerException -- that would let
            # an AttributeError escape cli_entrypoint, giving the user a bare traceback instead of
            # the log line below.
            details = getattr(e, "details", None)
            if details:
                output.display_error(f"Details: {details}")

            # One render for every exception class, so a subclass only has to carry the strings.
            for suggestion in getattr(e, "suggestions", None) or []:
                output.print(f"  • {suggestion}", emoji_code="")

            output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")

        output.stop()

        from frappe_manager.utils.helpers import capture_and_format_exception

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"FM Exception: {e.__class__.__name__}: {e!s}\n{exception_traceback}")
        # getattr for the same reason as `details` above: the last handler standing must not
        # itself raise on an exception whose __init__ never reached FrappeManagerException.
        code = getattr(e, "exit_code", 1)
        _emit_json_exit(ok=False, code=code)
        exit(code)

    except Exception as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)
        logger = get_logger(component="main")
        output = get_global_output_handler()

        output.display_error(f"[fm.error]Unexpected Error[/fm.error] {str(e).strip()}")
        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")
        output.stop()

        from frappe_manager.utils.helpers import capture_and_format_exception

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"Unexpected Exception:\n{exception_traceback}")
        _emit_json_exit(ok=False, code=1)
        exit(1)

    finally:
        atexit.register(exit_cleanup)


def _emit_json_exit(ok: bool, code: int) -> None:
    """Close the ``fm --events json`` JSONL stream with an exit event; no-op in rich mode."""
    try:
        handler = get_global_output_handler()
        emit_exit = getattr(handler, "emit_exit", None)
        if emit_exit is not None:
            emit_exit(ok=ok, code=code)
    except Exception:
        # The terminal event is telemetry; it must never mask the run's own outcome.
        pass


def _emit_json_error(error: Exception, output) -> bool:
    """Print the failure as the same envelope a success takes. False when nobody asked for one.

    `suggestions` already exist on fm's own exceptions, rendered as bullets for a human; they are
    the `next` field here, so a program is told what to run instead of having to read prose.
    """
    try:
        if not getattr(output, "wants_structured_data", False):
            return False

        import json

        from rich.text import Text

        from frappe_manager.output_manager.envelope import json_error_envelope

        envelope = json_error_envelope(
            # The message was written for a terminal and carries rich markup; a parser must not
            # be handed `[bold]` as part of the reason.
            error=Text.from_markup(str(error).strip()).plain,
            subject=getattr(error, "subject", None),
            path=str(getattr(error, "path", "")) or None,
            next_commands=list(getattr(error, "suggestions", None) or []),
        )
        print(json.dumps(envelope, indent=2, default=str))  # noqa: T201 -- stdout is the caller's here
        return True
    except Exception:
        # The envelope must never mask the failure it describes.
        return False


def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    from frappe_manager.utils.docker import process_opened
    from frappe_manager.utils.helpers import remove_zombie_subprocess_process

    remove_zombie_subprocess_process(process_opened)
    output = get_global_output_handler()
    output.stop()
