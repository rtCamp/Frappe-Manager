import logging
import time
from typing import Optional, Any, Dict
from xmlrpc.client import Fault

from fmx.display import display

logger = logging.getLogger(__name__)

from fmx.supervisor.constants import STOPPED_STATES, is_worker_process
from fmx.supervisor.fault_handler import _raise_exception_from_fault


def make_supervisor_api_name(group_name: Optional[str], process_name: str) -> str:
    if group_name and not process_name.startswith(f"{group_name}:"):
        return f"{group_name}:{process_name}"
    return process_name


def _wait_for_process_stop(supervisor_api, process_name: str, timeout: int) -> bool:
    logger.info(f"Waiting up to {timeout}s for graceful stop of {process_name}")
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            info = supervisor_api.getProcessInfo(process_name)
            if info['state'] in STOPPED_STATES:
                logger.info(f"Process {process_name} stopped gracefully")
                return True
        except Fault as e:
            if "BAD_NAME" in e.faultString:
                logger.info(f"Process {process_name} disappeared, assuming stopped")
                return True
            raise
        time.sleep(0.5)
    logger.warning(f"Timeout reached. Process {process_name} did not stop gracefully")
    return False


def _kill_process(supervisor_api, service_name: str, process_name: str) -> bool:
    try:
        info = supervisor_api.getProcessInfo(process_name)
        if info['state'] in STOPPED_STATES:
            logger.info(f"Process {process_name} already stopped")
            return True

        supervisor_api.signalProcess(process_name, 'USR1')
        logger.info(f"Sent SIGUSR1 to {process_name}")

        stopped = _wait_for_process_stop(supervisor_api, process_name, timeout=10)
        if stopped:
            return True

        logger.warning(f"{process_name} still running 10s after SIGUSR1, sending SIGKILL via stopProcess")
        supervisor_api.stopProcess(process_name, True)
        return True

    except Fault as e:
        fault_string = getattr(e, 'faultString', '')
        if "NOT_RUNNING" in fault_string or "ALREADY_DEAD" in fault_string or "BAD_NAME" in fault_string:
            logger.info(f"Process {process_name} already gone")
            return True
        logger.error(f"Error killing {process_name}: {fault_string}")
        _raise_exception_from_fault(e, service_name, "kill", process_name)
        return False


def _wait_for_worker_processes_stop(supervisor_api, service_name: str, timeout: int) -> bool:
    worker_process_names = []
    try:
        all_info = supervisor_api.getAllProcessInfo()
        worker_process_names = [info['name'] for info in all_info if is_worker_process(info['name'])]
    except Fault as e:
        display.error(f"Error getting process info to identify workers: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (worker wait identify)")
        return False

    if not worker_process_names:
        display.print("  No worker processes found to wait for.")
        return True

    num_workers = len(worker_process_names)
    worker_names_str = ", ".join(display.highlight(name) for name in worker_process_names)
    display.print(f"  Waiting up to {timeout}s for {num_workers} worker process(es) ({worker_names_str}) to stop...")

    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        all_workers_stopped_this_check = True
        try:
            current_all_info = supervisor_api.getAllProcessInfo()
            current_states = {info['name']: info['state'] for info in current_all_info}

            for worker_name in worker_process_names:
                current_state = current_states.get(worker_name)
                if current_state is None or current_state not in STOPPED_STATES:
                    all_workers_stopped_this_check = False
                    break

            if all_workers_stopped_this_check:
                display.success(f"  All {num_workers} identified worker process(es) stopped gracefully.")
                return True

        except Fault as e:
            if "SHUTDOWN_STATE" in e.faultString:
                display.print(
                    f"  Supervisor in {display.highlight(service_name)} is shutting down, assuming workers stopped."
                )
                return True
            display.error(f"Error checking worker status: {e.faultString}")
            all_workers_stopped_this_check = False

        if not all_workers_stopped_this_check:
            time.sleep(0.5)

    display.warning(f"Timeout reached. Not all identified worker processes stopped gracefully.")
    return False


def _stop_single_process_with_logic(
    supervisor_api,
    service_name: str,
    process_name: str,
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: bool = False,
    process_info: Optional[Dict[str, Any]] = None,
    verbose: bool = False,
) -> bool:
    action = "stop"

    original_process_name = process_name
    group_name = process_info.get('group') if process_info else None
    name_to_stop = make_supervisor_api_name(group_name, original_process_name)

    try:
        if verbose:
            display.print(
                f"Attempting to stop process {display.highlight(original_process_name)} in {display.highlight(service_name)} (API wait: {wait})..."
            )

        is_worker = is_worker_process(original_process_name)

        if is_worker and not wait_workers:
            return _kill_process(supervisor_api, service_name, name_to_stop)

        supervisor_api.stopProcess(name_to_stop, wait)

        if force_kill_timeout is not None and force_kill_timeout > 0:
            if is_worker:
                stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)
                if not stopped_gracefully:
                    return _kill_process(supervisor_api, service_name, original_process_name)
                return True
            else:
                logger.info(
                    f"Checking graceful stop for non-worker {original_process_name} (timeout: {force_kill_timeout}s)"
                )
                stopped_gracefully = _wait_for_process_stop(supervisor_api, original_process_name, force_kill_timeout)

                if not stopped_gracefully:
                    logger.info(f"Non-worker {original_process_name} didn't stop gracefully, force killing")
                    return _kill_process(supervisor_api, service_name, original_process_name)
                else:
                    logger.info(f"Non-worker {original_process_name} stopped gracefully")
                    return True

        else:
            if wait:
                if verbose:
                    display.success(
                        f"Stopped process {display.highlight(process_name)} in {display.highlight(service_name)} (waited)."
                    )
                return True
            else:
                if verbose:
                    display.print(
                        f"Stop signal sent to process {display.highlight(process_name)} in {display.highlight(service_name)} (no wait)."
                    )
                return True

    except Fault as e:
        fault_string = getattr(e, 'faultString', '')
        if "NOT_RUNNING" in fault_string:
            display.print(f"Process {display.highlight(process_name)} was already stopped.")
            return True
        elif "BAD_NAME" in fault_string:
            group_name = process_info.get('group', 'N/A') if process_info else 'N/A'
            display.print(
                f"Process {display.highlight(process_name)} (Group: {group_name}) already stopped or gone before stop signal could be sent."
            )
            return True
        else:
            _raise_exception_from_fault(e, service_name, action, process_name)
            return False
