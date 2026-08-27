import atexit
import os
import signal

from frappe_manager import CLI_LOG_DIRECTORY
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.logger import get_logger, log
from frappe_manager.output_manager.globals import get_global_output_handler, set_global_output_handler
from frappe_manager.output_manager.rich_output import RichOutputHandler

# frappe_manager.commands, utils.docker and utils.helpers are imported at their use sites BELOW
# the root check, not here. Each one calls get_logger() at module scope, which creates CLI_DIR and
# opens logs/fm.log as an import side effect. Hoisting them back to the top would run that before
# cli_entrypoint() gets to refuse root, so `sudo -E fm` would leave a root-owned fm.log in the
# real user's ~/frappe that their next fm run cannot write. The other five imports above are
# side-effect free (verified by importing each in isolation).


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

    # Initialize basic output handler early (before app() runs)
    # This will be upgraded to LoggingOutputHandler in app_callback after CLI args are parsed
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

    # Refuse root before app() runs, i.e. before app_callback creates CLI_DIR and before any
    # command touches disk or docker. Root is not a supported way to run fm, and it fails in
    # ways that are worse than a refusal:
    #   * Frappe's own bench exits 1 as root unless `frappe_user` is set in the bench config
    #     (bench/cli.py change_uid), which fm does not set, so web and workers land in FATAL
    #     and the site serves 502 while the bench looks created.
    #   * the shared service containers are named fixedly (fm_global-db, fm_global-nginx-proxy),
    #     so a root fm fights the same containers as the non-root fm on that host.
    #   * anything written before the refusal is root-owned inside the user's own ~/frappe,
    #     which the user then cannot remove without sudo. Hence: check first, write nothing.
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
    except FrappeManagerException as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)  # apply configured file log level
        logger = get_logger(component="main")
        output = get_global_output_handler()

        output.display_error(f"[fm.error]Error Occurred[/fm.error] {str(e).strip()}")

        # getattr, not e.details: this is the last handler standing, so it must not raise on an
        # exception whose __init__ never reached FrappeManagerException. When it did raise, the
        # AttributeError escaped cli_entrypoint and the user got a traceback INSTEAD of the log
        # line below, so nothing was recorded anywhere.
        details = getattr(e, "details", None)
        if details:
            output.display_error(f"Details: {details}")

        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")
        output.stop()

        from frappe_manager.utils.helpers import capture_and_format_exception

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"FM Exception: {e.__class__.__name__}: {e!s}\n{exception_traceback}")
        exit(1)

    except Exception as e:
        try:
            from frappe_manager.metadata_manager import FMConfigManager

            fm_config = FMConfigManager.import_from_toml()
            file_level = fm_config.logs.file_level
        except Exception:
            file_level = "DEBUG"

        log.get_logger(file_level=file_level)  # apply configured file log level
        logger = get_logger(component="main")
        output = get_global_output_handler()

        output.display_error(f"[fm.error]Unexpected Error[/fm.error] {str(e).strip()}")
        output.print(f"More info about error is logged in {CLI_LOG_DIRECTORY / 'fm.log'}", emoji_code=":mag:")
        output.stop()

        from frappe_manager.utils.helpers import capture_and_format_exception

        exception_traceback: str = capture_and_format_exception()
        logger.error(f"Unexpected Exception:\n{exception_traceback}")
        exit(1)

    finally:
        atexit.register(exit_cleanup)


def exit_cleanup():
    """
    This function is used to perform cleanup at the exit.
    """
    from frappe_manager.utils.docker import process_opened
    from frappe_manager.utils.helpers import remove_zombie_subprocess_process

    remove_zombie_subprocess_process(process_opened)
    output = get_global_output_handler()
    output.stop()
