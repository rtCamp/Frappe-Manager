from enum import IntEnum

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

# Signal number used to ask Frappe RQ workers for graceful shutdown
# This corresponds to SIGRTMIN on Linux, which bench worker listens for.
SIGNAL_NUM_WORKER_GRACEFUL_EXIT = 34

# Constants for worker process identification
WORKER_PROCESS_IDENTIFIERS = ["-worker", "worker-", "_worker", "worker_"]
def is_worker_process(process_name: str) -> bool:
    """Check if a process name indicates it's a worker process."""
    # Check if the process name contains a worker identifier
    return any(identifier in process_name.lower() for identifier in WORKER_PROCESS_IDENTIFIERS)

