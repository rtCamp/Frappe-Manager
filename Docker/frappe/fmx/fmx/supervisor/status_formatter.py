from datetime import datetime
from typing import Any, Dict, Optional
from rich.tree import Tree
from rich.table import Table


def format_timestamp(timestamp: int) -> str:
    """Format a Unix timestamp into a human-readable string."""
    if not timestamp:
        return "N/A"
    try:
        dt_object = datetime.fromtimestamp(timestamp)
        return dt_object.strftime('%Y-%m-%d %H:%M:%S %Z')
    except Exception:
        return f"{timestamp} (timestamp)"


def create_process_details_table(process_info: Dict[str, Any], verbose: bool = False) -> Table:
    """Create a formatted table of process details (only verbose details)."""
    table = Table(show_lines=False, show_edge=False, pad_edge=False, show_header=False, box=None, padding=(0, 1, 0, 1))
    table.add_column(style="dim", justify="right")
    table.add_column()

    if not verbose:
        return table

    fields_to_display = [
        ("group", "Group"),
        ("start", "Start Time"),
        ("stop", "Stop Time"),
        ("now", "Server Time"),
        ("spawnerr", "Spawn Error"),
        ("exitstatus", "Exit Status"),
        ("stdout_logfile", "Stdout Log"),
        ("stderr_logfile", "Stderr Log"),
        ("description", "Description"),
    ]

    for field, label in fields_to_display:
        value = process_info.get(field)
        if field == 'pid' and value == 0:
            value = "N/A (Not Running)"
        elif field == 'exitstatus' and process_info.get('statename') == 'RUNNING':
            continue
        elif field in ['start', 'stop', 'now']:
            value = format_timestamp(value if isinstance(value, int) else 0)

        if value is not None or field in ['pid', 'exitstatus']:
            table.add_row(f"{label}:", str(value))

    return table


def format_service_info(service_name: str, process_info_list: list, verbose: bool = False) -> Tree:
    """Format service and process information as a Rich Tree.

    Args:
        service_name: Name of the service to format info for
        process_info_list: List of process information dictionaries
        verbose: If True, shows more detailed information in the output
    """
    root = Tree(f"📄 [highlight]{service_name}[/highlight]", highlight=True)

    if not process_info_list:
        root.add("[i]No processes found for this service.[/i]")
        return root

    for process in process_info_list:
        process_name = process.get('name', 'N/A')
        state = process.get('statename', 'UNKNOWN')
        state_color = (
            'green' if state == 'RUNNING' else ('yellow' if state in ['STARTING', 'BACKOFF', 'STOPPING'] else 'red')
        )

        process_tree = root.add(
            f"([dimmed]PID: {process.get('pid', 0) or 'N/A'}[/dimmed]) "
            f"([{state_color}]{state}[/{state_color}]) "
            f"[heading]Process:[/heading] [b]{process_name}[/b]"
        )

        details_table = create_process_details_table(process, verbose=verbose)
        if details_table.row_count > 0:
            process_tree.add(details_table)

    return root


def format_rq_status(rq_status: Optional[dict], verbose: bool = False) -> Optional[Tree]:
    """Format RQ worker status as a Rich Tree.

    Args:
        rq_status: Dictionary with 'suspended' and 'workers' keys
        verbose: If True, shows more detailed information

    Returns:
        Tree object with formatted RQ status, or None if no status available
    """
    if not rq_status:
        return None

    suspended = rq_status.get('suspended', False)
    workers = rq_status.get('workers', [])

    suspension_status = "🔴 SUSPENDED" if suspended else "🟢 ACTIVE"
    root = Tree(f"⚙️  [highlight]RQ Workers[/highlight] ({suspension_status})", highlight=True)

    if not workers:
        root.add("[dimmed]No RQ workers registered[/dimmed]")
        return root

    for worker_name, state, current_job, job_stats in workers:
        state_color = 'green' if state in ['idle', 'busy'] else 'yellow' if state == 'suspended' else 'red'

        total_jobs = job_stats.get('total', 0)
        successful = job_stats.get('successful', 0)
        failed = job_stats.get('failed', 0)
        working_time = job_stats.get('working_time', 0.0)

        worker_label = (
            f"([{state_color}]{state.upper()}[/{state_color}]) "
            f"[heading]Worker:[/heading] [b]{worker_name}[/b] "
            f"[dimmed]│ Jobs: {total_jobs}[/dimmed]"
        )

        if verbose:
            if current_job:
                worker_label += f"\n  [dimmed]Current job: {current_job}[/dimmed]"
            worker_label += (
                f"\n  [dimmed]✓ Successful: {successful}  ✗ Failed: {failed}  ⏱ Time: {working_time:.1f}s[/dimmed]"
            )

        root.add(worker_label)

    return root
