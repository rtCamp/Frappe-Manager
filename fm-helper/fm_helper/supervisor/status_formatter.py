from datetime import datetime
from typing import Any, Dict
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

def get_state_color(state: str) -> str:
    """Get the appropriate color for a process state."""
    if state == 'RUNNING':
        return 'green'
    elif state in ['STARTING', 'BACKOFF', 'STOPPING']:
        return 'yellow'
    return 'red'

def create_process_details_table(process_info: Dict[str, Any]) -> Table:
    """Create a formatted table of process details."""
    table = Table(
        show_lines=False,
        show_edge=False,
        pad_edge=False,
        show_header=False,
        box=None,
        padding=(0, 1, 0, 1)
    )
    table.add_column(style="dim", justify="right")
    table.add_column()

    fields_to_display = [
        ("group", "Group"),
        ("pid", "PID"),
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
        if value or field in ['pid', 'exitstatus']:
            if field in ['start', 'stop', 'now']:
                value = format_timestamp(value)
            elif field == 'pid' and value == 0:
                value = "N/A (Not Running)"
            elif field == 'exitstatus' and process_info.get('statename') == 'RUNNING':
                continue

            table.add_row(f"{label}:", str(value))

    return table

def format_service_info(service_name: str, process_info_list: list) -> Tree:
    """Format service and process information as a Rich Tree."""
    root = Tree(f"📄 [b magenta]{service_name}[/b magenta]", highlight=True)
    
    if not process_info_list:
        root.add("[i]No processes found for this service.[/i]")
        return root

    for process in process_info_list:
        process_name = process.get('name', 'N/A')
        state = process.get('statename', 'UNKNOWN')
        state_color = get_state_color(state)

        process_tree = root.add(
            f"[b cyan]Process:[/b cyan] [b]{process_name}[/b] "
            f"([{state_color}]{state}[/{state_color}])"
        )
        
        details_table = create_process_details_table(process)
        if details_table.row_count > 0:
            process_tree.add(details_table)

    return root
