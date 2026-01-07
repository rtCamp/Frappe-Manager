import typer
from typing import Annotated, Optional
from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.site_manager.bench_service import BenchService
from frappe_manager.site_manager.site import Bench
from frappe_manager.site_manager.exceptions import BenchSSLCertificateNotIssued
from frappe_manager.ssl_manager.certificate_exceptions import SSLCertificateNotDueForRenewalError
from frappe_manager.utils.callbacks import sitename_callback, sites_autocompletion_callback
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.exceptions import SSLCertificateError
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.logger import log
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler

ssl_root_command = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def get_output_handler(ctx: typer.Context, context: Optional[LoggerContext] = None) -> OutputHandler:
    """
    Get the appropriate output handler based on verbose flag.
    
    Args:
        ctx: Typer context containing verbose flag
        context: Optional logger context for structured logging
    
    Returns:
        LoggingOutputHandler wrapping RichOutputHandler with contextual logging
    """
    verbose = ctx.obj.get('verbose', False)
    
    # Create base handler with verbose setting
    rich = RichOutputHandler(verbose=verbose)
    
    # Get base logger
    base_logger = log.get_logger()
    
    # Wrap with context (empty context if not provided)
    contextual_logger = ContextualLogger(base_logger, context)
    
    # Wrap with logging for automatic file logging
    output = LoggingOutputHandler(rich, contextual_logger)
    
    return output


@ssl_root_command.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
):
    """Delete bench ssl certficate."""

    services_manager = ctx.obj["services"]
    
    # Create output handler with context for logging
    context = LoggerContext(bench=benchname, operation="ssl-delete")
    output = get_output_handler(ctx, context=context)
    bench = Bench.get_object(benchname, services_manager, output_handler=output)
    
    richprint.change_head("Removing SSL certificate")

    if not bench.has_certificate():
        richprint.error(f"{benchname} doesn't have SSL certificate issued.")
        raise SSLCertificateError(
            "Bench doesn't have SSL certificate issued.", details={"bench": benchname}
        )
    bench.remove_certificate()
    richprint.print("Removed SSL certificate.")


@ssl_root_command.command()
def renew(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(help="Name of the bench.", autocompletion=sites_autocompletion_callback),
    ] = None,
    all: Annotated[bool, typer.Option(help="Renew ssl cert for all benches.")] = False,
):
    """Renew bench ssl certficate."""

    services_manager = ctx.obj["services"]
    bench_service = BenchService(CLI_BENCHES_DIRECTORY, services_manager)

    if all:
        sites_list = bench_service.get_bench_names()
    else:
        sites_list = [benchname]

    for benchname in sites_list:
        # Create output handler with context for logging
        context = LoggerContext(bench=benchname, operation="ssl-renew")
        output = get_output_handler(ctx, context=context)
        bench = Bench.get_object(benchname, services_manager, output_handler=output)
        
        richprint.change_head("Renew certificate")
        try:
            bench.renew_certificate()
        except (BenchSSLCertificateNotIssued, SSLCertificateNotDueForRenewalError) as e:
            richprint.warning(e.message)

        except Exception as e:
            richprint.warning(str(e))
