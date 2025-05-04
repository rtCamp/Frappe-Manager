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
