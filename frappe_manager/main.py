import atexit
import os
import signal

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


def cli_entrypoint():
    """
    Main CLI entry point.

    Initializes a basic RichOutputHandler early, which will be upgraded
    to LoggingOutputHandler in app_callback (commands/__init__.py) after
    CLI arguments are parsed. Exception handling uses bare richprint for
    backward compatibility.
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

    # Deferred on purpose: see the import comment at the top of this module.
    from frappe_manager.commands import app

    try:
        app()
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

        # No "Error Occurred" prefix: the ⛔ glyph and the red style already say that, and the
        # words pushed the part that matters -- what went wrong -- further right on every error.
        output.display_error(str(e).strip())

        # getattr, not e.details: this is the last handler standing, and must not itself raise on
        # an exception whose __init__ never reached FrappeManagerException -- that would let an
        # AttributeError escape cli_entrypoint, giving the user a bare traceback instead of the
        # log line below.
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
        _emit_json_exit(ok=False, code=1)
        exit(1)

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
        from frappe_manager.output_manager.json_output import JSONOutputHandler

        handler = get_global_output_handler()
        delegate = getattr(handler, "delegate", handler)
        if isinstance(delegate, JSONOutputHandler):
            delegate.emit_exit(ok=ok, code=code)
    except Exception:
        # The terminal event is telemetry; it must never mask the run's own outcome.
        pass


def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    from frappe_manager.utils.docker import process_opened
    from frappe_manager.utils.helpers import remove_zombie_subprocess_process

    remove_zombie_subprocess_process(process_opened)
    output = get_global_output_handler()
    output.stop()
