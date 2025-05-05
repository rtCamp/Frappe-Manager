import re
from enum import IntEnum
from pathlib import Path
from typing import Optional, Tuple

# State directory for hybrid restart state files
FM_SUPERVISOR_STATE_DIR = Path("/fm-sockets")

# Constants for hybrid restart
DEFAULT_SUFFIXES = "blue,green"  # Default suffixes for rolling restart pairs

# Define Supervisor Process States Constants
class ProcessStates(IntEnum):
    STOPPED = 0
    STARTING = 10
    RUNNING = 20
    BACKOFF = 30
    STOPPING = 40
    EXITED = 100
    FATAL = 200
    UNKNOWN = 1000

STOPPED_STATES = (ProcessStates.STOPPED, ProcessStates.EXITED, ProcessStates.FATAL)

# Constants for worker process identification
WORKER_PROCESS_IDENTIFIERS = ["-worker", "worker-", "_worker", "worker_"]
def is_worker_process(process_name: str) -> bool:
    """Check if a process name indicates it's a worker process."""
    # Check if the process name contains a worker identifier
    return any(identifier in process_name.lower() for identifier in WORKER_PROCESS_IDENTIFIERS)

def get_base_worker_name(process_name: str, suffixes: str = DEFAULT_SUFFIXES) -> Tuple[str, Optional[str]]:
    """
    Extract base worker name and index by removing the last occurrence of a blue/green suffix
    (like '-blue' or '_blue'), even if followed by other characters (e.g., '-0').

    Returns:
        Tuple[str, Optional[str]]: A tuple containing the base name and the index string (e.g., "0", "1"),
                                   or None for the index if not found.
    """
    suffix_list = [s.strip() for s in suffixes.split(',')]
    best_match_index = -1
    base_name_at_match = process_name # Default to full name if no match
    trailer_at_match = "" # Default to empty trailer

    for suffix in suffix_list:
        # Find last occurrence of -suffix
        last_dash_index = process_name.rfind(f"-{suffix}")
        if last_dash_index > best_match_index:
            # Check if the character *after* the suffix is a separator or end of string
            # This avoids matching things like 'my-blue-process' if suffix is 'blue'
            char_after = process_name[last_dash_index + len(suffix) + 1 : last_dash_index + len(suffix) + 2]
            if not char_after or char_after in ['-', '_', ':']: # Allow separator or end of string
                 best_match_index = last_dash_index
                 base_name_at_match = process_name[:best_match_index]
                 trailer_at_match = process_name[best_match_index + len(suffix) + 1:] # Part after "-suffix"

        # Find last occurrence of _suffix
        last_underscore_index = process_name.rfind(f"_{suffix}")
        if last_underscore_index > best_match_index:
             # Check character after
             char_after = process_name[last_underscore_index + len(suffix) + 1 : last_underscore_index + len(suffix) + 2]
             if not char_after or char_after in ['-', '_', ':']:
                 best_match_index = last_underscore_index
                 base_name_at_match = process_name[:best_match_index]
                 trailer_at_match = process_name[best_match_index + len(suffix) + 1:] # Part after "_suffix"

    # --- Extract Index from Trailer ---
    index_str: Optional[str] = None
    # Match optional separator ('-' or '_') followed by one or more digits at the START of the trailer
    match = re.match(r"^[-_]?(\d+)", trailer_at_match)
    if match:
        index_str = match.group(1) # Capture the digits

    # If a valid suffix was found as the last occurrence
    if best_match_index != -1:
        return base_name_at_match, index_str

    # If no suffix was found, return the original name and no index
    return process_name, None

def has_worker_suffix(process_name: str, suffixes: str = DEFAULT_SUFFIXES) -> bool:
    """Check if process has one of the blue/green suffixes (with '-' or '_')."""
    suffix_list = [s.strip() for s in suffixes.split(',')]
    # Check for both separators
    return any(
        process_name.endswith(f"-{suffix}") or process_name.endswith(f"_{suffix}")
        for suffix in suffix_list
    )
