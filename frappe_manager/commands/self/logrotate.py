"""Generate and optionally install a logrotate configuration for Frappe Manager logs."""

import os
import platform
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from jinja2 import Template
from typer_examples import example

from frappe_manager import CLI_DIR
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.helpers import get_template_path


@example(
    "Generate logrotate config to ~/frappe/logrotate.conf",
    "",
    detail="Renders the logrotate configuration for all FM log paths and saves it under ~/frappe/.",
)
@example(
    "Install logrotate config system-wide (requires sudo)",
    "--install",
    detail="Copies the generated config to /etc/logrotate.d/frappe-manager (Linux only).",
)
def logrotate(
    ctx: typer.Context,
    install: Annotated[
        bool,
        typer.Option(
            "--install",
            help="Install the config to /etc/logrotate.d/frappe-manager (Linux, requires sudo).",
        ),
    ] = False,
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Custom path to write the generated config file.",
            writable=True,
        ),
    ] = None,
):
    """Generate a logrotate configuration for all Frappe Manager log paths.

    Covers bench application logs (web, workers, scheduler, socketio),
    per-site nginx logs, global nginx-proxy logs, global MariaDB logs,
    and the FM CLI log.

    The config uses [bold]copytruncate[/bold] so running containers do not need
    to be restarted after rotation.
    """
    output = get_global_output_handler()
    output.change_head("Generating logrotate config")

    current_system = platform.system()
    if install and current_system == "Darwin":
        output.error(
            "System-wide logrotate installation is not supported on macOS. "
            "Use newsyslog or install logrotate via Homebrew and manage manually."
        )
        raise typer.Exit(1)

    # Resolve output path
    dest: Path = output_path or (CLI_DIR / "logrotate.conf")

    # Render template
    template_path = get_template_path("logrotate.tmpl")
    template_text = template_path.read_text()
    rendered = Template(template_text).render(
        frappe_dir=str(CLI_DIR),
        user=_current_username(),
        output_path=str(dest),
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered)
    output.print(f":white_check_mark: Logrotate config written to [blue]{dest}[/blue]")

    if install:
        system_dest = Path("/etc/logrotate.d/frappe-manager")
        try:
            result = subprocess.run(
                ["sudo", "cp", str(dest), str(system_dest)],
                check=True,
                capture_output=True,
                text=True,
            )
            output.print(
                f":white_check_mark: Installed to [blue]{system_dest}[/blue]. "
                "Logs will rotate daily."
            )
        except subprocess.CalledProcessError as e:
            output.error(
                f"Failed to install config to {system_dest}: {e.stderr.strip() or e}",
                exception=e,
            )
            raise typer.Exit(1)
    else:
        if current_system == "Linux":
            output.print(
                f"\nTo enable system-wide rotation run:\n"
                f"  [bold]sudo cp {dest} /etc/logrotate.d/frappe-manager[/bold]\n"
                f"\nOr run directly:\n"
                f"  [bold]fm self logrotate --install[/bold]"
            )
        else:
            output.print(
                f"\nmacOS: install logrotate via Homebrew, then:\n"
                f"  [bold]brew install logrotate[/bold]\n"
                f"  [bold]sudo cp {dest} /usr/local/etc/logrotate.d/frappe-manager[/bold]"
            )


def _current_username() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "root")
