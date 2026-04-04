import logging
import signal
import time
from typing import List, Optional, Dict, Any
from xmlrpc.client import Fault

logger = logging.getLogger(__name__)

from fmx.supervisor.constants import STOPPED_STATES, is_worker_process
from fmx.display import display
from fmx.supervisor.fault_handler import _raise_exception_from_fault
from fmx.supervisor.stop_helpers import make_supervisor_api_name
from fmx.supervisor.stop_helpers import _stop_single_process_with_logic


def _handle_stop(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int],
    wait_workers: bool = False,
    called_from_restart: bool = False,
    verbose: bool = False,
    progress_callback=None,
) -> Dict[str, List[str]]:
    """
    Stops processes in a service with intelligent worker handling.

    Logic:
    1. Gets all running processes from supervisor
    2. Determines target processes (specified ones or all)
    3. For each process, applies worker-specific stop logic:
       - Workers: respects wait_workers parameter for graceful shutdown
       - Non-workers: uses standard wait parameter
    4. Optionally applies force kill timeout for stubborn processes
    5. Returns categorized results (stopped, already_stopped, failed)
    """
    logger.info(
        f"Handle stop called: service={service_name}, processes={process_names}, wait={wait}, force_kill_timeout={force_kill_timeout}, wait_workers={wait_workers}"
    )

    action = "stop"
    target_process_names: List[str] = []
    process_info_map: Dict[str, Dict[str, Any]] = {}

    stop_results = {"stopped": [], "already_stopped": [], "failed": []}

    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.print(f"No processes found running in {display.highlight(service_name)}.")
            return stop_results
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(f"Error getting process list for {service_name}: {e.faultString}")
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (stop)")
        return {"stopped": [], "already_stopped": [], "failed": ["<unexpected error>"]}
    if process_names:
        target_process_names = []
        missing_names = []
        for name in process_names:
            if name in process_info_map:
                target_process_names.append(name)
            else:
                missing_names.append(name)

        if missing_names:
            display.warning(f"Specified process(es) not found or not running: {', '.join(missing_names)}")
        if not target_process_names:
            display.error("None of the specified processes are currently running.")
            return stop_results
        if verbose:
            display.print(
                f"Preparing to stop specific process(es): {display.highlight(', '.join(target_process_names))} in {display.highlight(service_name)}..."
            )
    else:
        target_process_names = list(process_info_map.keys())
        if verbose:
            display.print(f"Preparing to stop all processes in {display.highlight(service_name)}...")

    if not called_from_restart and verbose:
        if wait_workers:
            display.dimmed("Stop calls for worker processes WILL wait for graceful shutdown.")
    for process_name in target_process_names:
        try:
            is_worker = is_worker_process(process_name)

            if is_worker:
                effective_wait_for_this_process = True if wait_workers else wait
            else:
                effective_wait_for_this_process = wait

            process_info = process_info_map.get(process_name)
            current_state = process_info.get('state') if process_info else None
            _pid = (process_info.get('pid') or 0) if process_info else 0

            if current_state in STOPPED_STATES:
                stop_results["already_stopped"].append(process_name)
                if progress_callback:
                    progress_callback(service_name, process_name, "stop", 0, "already stopped", 0.0)
                continue

            _t0 = time.time()
            success = _stop_single_process_with_logic(
                supervisor_api,
                service_name,
                process_name,
                wait=effective_wait_for_this_process,
                force_kill_timeout=force_kill_timeout,
                wait_workers=wait_workers,
                process_info=process_info,
                verbose=verbose,
            )
            _elapsed = time.time() - _t0

            if success:
                stop_results["stopped"].append(process_name)
                if progress_callback:
                    _method = "killed (USR1)" if is_worker else "stopped"
                    progress_callback(service_name, process_name, "stop", _pid, _method, _elapsed)
            else:
                stop_results["failed"].append(process_name)
                if progress_callback:
                    progress_callback(service_name, process_name, "stop", _pid, "failed", _elapsed)
        except Fault as e:
            display.error(f"Error during stop operation for process {process_name}: {e.faultString}")
            display.dimmed(f"  Check logs: supervisorctl tail {process_name}")
            _raise_exception_from_fault(e, service_name, action, process_name)
            stop_results["failed"].append(process_name)
        except Exception as e:
            display.error(f"Unexpected error stopping process {display.highlight(process_name)}: {e}")
            display.dimmed(f"  Check logs: supervisorctl tail {process_name}")
            stop_results["failed"].append(process_name)

    logger.info(f"Stop operation completed: service={service_name}, results={stop_results}")
    return stop_results


def _handle_start(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    verbose: bool = False,
    progress_callback=None,
) -> Dict[str, List[str]]:
    """
    Starts processes in a service with validation and conflict detection.

    Logic:
    1. Gets all defined processes from supervisor configuration
    2. Validates requested processes exist in the configuration
    3. For each target process:
       - Constructs proper API name (handles group prefixes)
       - Attempts to start the process
       - Handles ALREADY_STARTED, FATAL, and BAD_NAME errors gracefully
    4. Returns categorized results (started, already_running, failed)
    """
    logger.info(f"Handle start called: service={service_name}, processes={process_names}, wait={wait}")

    action = "start"
    processes_to_start_explicitly: List[str] = []
    start_results = {"started": [], "already_running": [], "failed": []}

    try:
        all_defined_processes_info = supervisor_api.getAllProcessInfo()
        if not all_defined_processes_info:
            display.warning(f"No processes defined or found in {display.highlight(service_name)}. Nothing to start.")
            return {"started": [], "already_running": [], "failed": []}

        defined_process_names = {info['name'] for info in all_defined_processes_info}

        if process_names:
            if verbose:
                display.print(
                    f"Attempting to start specific process(es) in {display.highlight(service_name)}: {', '.join(process_names)}"
                )
            missing_names = [name for name in process_names if name not in defined_process_names]
            if missing_names:
                display.warning(f"Specified process(es) not defined in supervisor config: {', '.join(missing_names)}")

            processes_to_start_explicitly = [name for name in process_names if name in defined_process_names]
            if not processes_to_start_explicitly:
                display.error("None of the specified processes are defined. Nothing to start.")
                return {"started": [], "already_running": [], "failed": process_names}

        else:
            if verbose:
                display.print(f"Attempting to start all defined processes in {display.highlight(service_name)}...")
            processes_to_start_explicitly = list(defined_process_names)
            if not processes_to_start_explicitly:
                display.print("  No processes defined to start.")
                return start_results

        if verbose:
            display.print(f"Final list of processes to start: {', '.join(processes_to_start_explicitly)}")

        process_info_map = {info['name']: info for info in all_defined_processes_info}

        for process_name_to_start in processes_to_start_explicitly:
            try:
                process_info = process_info_map.get(process_name_to_start)
                if not process_info:
                    display.warning(f"Skipping {display.highlight(process_name_to_start)} - info not found.")
                    start_results["failed"].append(process_name_to_start)
                    continue

                group_name = process_info.get('group')
                name_for_api = make_supervisor_api_name(group_name, process_name_to_start)

                _t0 = time.time()
                supervisor_api.startProcess(name_for_api, wait)
                _elapsed = time.time() - _t0
                start_results["started"].append(process_name_to_start)
                if progress_callback:
                    _new_pid = 0
                    try:
                        _info = supervisor_api.getProcessInfo(name_for_api)
                        _new_pid = _info.get('pid', 0)
                    except Exception:
                        pass
                    progress_callback(service_name, process_name_to_start, "start", _new_pid, "started", _elapsed)
            except Fault as start_fault:
                fault_string = getattr(start_fault, 'faultString', '')
                if "ALREADY_STARTED" in fault_string:
                    _elapsed = time.time() - _t0
                    start_results["already_running"].append(process_name_to_start)
                    if progress_callback:
                        progress_callback(service_name, process_name_to_start, "start", 0, "already running", _elapsed)
                elif "FATAL" in fault_string:
                    _elapsed = time.time() - _t0
                    display.error(
                        f"Process {display.highlight(process_name_to_start)} entered FATAL state during start."
                    )
                    display.dimmed(f"  Check logs: supervisorctl tail {process_name_to_start}")
                    display.dimmed(f"  Full details: fmx status --verbose")
                    start_results["failed"].append(process_name_to_start)
                    if progress_callback:
                        progress_callback(service_name, process_name_to_start, "start", 0, "failed (FATAL)", _elapsed)
                    _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                elif "BAD_NAME" in fault_string:
                    _elapsed = time.time() - _t0
                    display.error(
                        f"Process {display.highlight(process_name_to_start)} not found by supervisor (BAD_NAME)."
                    )
                    start_results["failed"].append(process_name_to_start)
                    if progress_callback:
                        progress_callback(service_name, process_name_to_start, "start", 0, "failed", _elapsed)
                    _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)
                elif "ABNORMAL_TERMINATION" in fault_string and is_worker_process(process_name_to_start):
                    retry_interval = 2
                    retry_timeout = 60
                    retry_start = time.monotonic()
                    attempt = 0
                    started = False
                    display.dimmed(
                        f"  Worker {display.highlight(process_name_to_start)} waiting for previous job to finish..."
                    )
                    while time.monotonic() - retry_start < retry_timeout:
                        attempt += 1
                        elapsed = int(time.monotonic() - retry_start)
                        logger.warning(
                            f"Worker {process_name_to_start} got ABNORMAL_TERMINATION "
                            f"(attempt {attempt}, {elapsed}s elapsed), retrying in {retry_interval}s"
                        )
                        time.sleep(retry_interval)
                        try:
                            supervisor_api.startProcess(name_for_api, wait)
                            _elapsed = time.time() - _t0
                            start_results["started"].append(process_name_to_start)
                            started = True
                            if progress_callback:
                                _new_pid = 0
                                try:
                                    _info = supervisor_api.getProcessInfo(name_for_api)
                                    _new_pid = _info.get('pid', 0)
                                except Exception:
                                    pass
                                progress_callback(
                                    service_name, process_name_to_start, "start", _new_pid, "started", _elapsed
                                )
                            break
                        except Fault as retry_fault:
                            retry_fs = getattr(retry_fault, 'faultString', '')
                            if "ABNORMAL_TERMINATION" in retry_fs:
                                continue
                            _elapsed = time.time() - _t0
                            start_results["failed"].append(process_name_to_start)
                            if progress_callback:
                                progress_callback(service_name, process_name_to_start, "start", 0, "failed", _elapsed)
                            _raise_exception_from_fault(retry_fault, service_name, action, process_name_to_start)
                            break
                    if not started and process_name_to_start not in start_results["failed"]:
                        _elapsed = time.time() - _t0
                        display.error(
                            f"Worker {display.highlight(process_name_to_start)} failed to start after {retry_timeout}s "
                            f"({attempt} attempts). Force-killed job did not exit in time. Try --wait-workers next time."
                        )
                        start_results["failed"].append(process_name_to_start)
                        if progress_callback:
                            progress_callback(service_name, process_name_to_start, "start", 0, "failed", _elapsed)
                else:
                    _elapsed = time.time() - _t0
                    start_results["failed"].append(process_name_to_start)
                    if progress_callback:
                        progress_callback(service_name, process_name_to_start, "start", 0, "failed", _elapsed)
                    _raise_exception_from_fault(start_fault, service_name, action, process_name_to_start)

        logger.info(f"Start operation completed: service={service_name}, results={start_results}")
        return start_results

    except Fault as e:
        _raise_exception_from_fault(e, service_name, action, process_names[0] if process_names else None)
        return {
            "started": [],
            "already_running": [],
            "failed": processes_to_start_explicitly or ["<error getting process list>"],
        }
    except Exception as e:
        return {"started": [], "already_running": [], "failed": [str(e)]}


def _handle_restart(
    supervisor_api,
    service_name: str,
    process_names: Optional[List[str]],
    wait: bool,
    force_kill_timeout: Optional[int] = None,
    wait_workers: bool = False,
    progress_callback=None,
) -> Dict[str, List[str]]:
    """
    Performs a complete service restart using stop-then-start strategy.

    Logic:
    1. Stops ALL processes in the service (ignores process_names parameter)
    2. Waits for complete shutdown with optional force kill
    3. If stop fails, aborts the restart to prevent inconsistent state
    4. Starts ALL defined processes (fresh start from configuration)
    5. Returns detailed results for both stop and start phases
    """
    logger.info(
        f"Handle restart called: service={service_name}, processes={process_names}, wait={wait}, force_kill_timeout={force_kill_timeout}, wait_workers={wait_workers}"
    )

    action = "restart"

    if process_names and len(process_names) == 0:
        process_names = None

    stop_results = _handle_stop(
        supervisor_api,
        service_name,
        process_names,
        wait,
        force_kill_timeout,
        wait_workers=wait_workers,
        called_from_restart=True,
        progress_callback=progress_callback,
    )

    if stop_results.get("failed"):
        display.error(
            f"Failed to stop some processes during standard restart of {display.highlight(service_name)}. Aborting start."
        )
        display.dimmed("  Run 'fmx status --verbose' for detailed process states")
        # Return combined results showing the failure
        return {
            "stopped": stop_results.get("stopped", []),
            "already_stopped": stop_results.get("already_stopped", []),
            "started": [],
            "already_running": [],
            "failed": stop_results.get("failed", []),
        }

    start_results = _handle_start(
        supervisor_api, service_name, process_names, wait, verbose=False, progress_callback=progress_callback
    )

    # Combine stop and start results
    combined_results = {
        "stopped": stop_results.get("stopped", []),
        "already_stopped": stop_results.get("already_stopped", []),
        "started": start_results.get("started", []),
        "already_running": start_results.get("already_running", []),
        "failed": start_results.get("failed", []),
    }

    logger.info(f"Restart operation completed: service={service_name}, results={combined_results}")
    return combined_results


def _handle_signal(supervisor_api, service_name: str, process_names: List[str], signal_name: str) -> bool:
    """
    Sends Unix signals to specific processes with validation and error handling.

    Logic:
    1. Validates the signal name exists in Python's signal module
    2. Gets current process list to verify targets exist
    3. For each target process:
       - Constructs proper API name (handles group prefixes)
       - Sends the signal via supervisor
       - Gracefully handles NOT_RUNNING and BAD_NAME cases
    4. Returns True if all signals sent successfully, False otherwise
    """
    logger.info(f"Handle signal called: service={service_name}, processes={process_names}, signal={signal_name}")

    action = "signal"
    results = {}
    signal_enum = getattr(signal, f"SIG{signal_name.upper()}", None)

    if signal_enum is None:
        display.error(f"Invalid signal name '{signal_name}' for service {display.highlight(service_name)}.")
        return False

    display.print(
        f"Sending signal {signal_name} ({int(signal_enum)}) to {len(process_names)} process(es) in {display.highlight(service_name)}..."
    )

    if not process_names:
        display.print("  No specific processes provided to signal.")
        return True

    try:
        all_info = supervisor_api.getAllProcessInfo()
        if not all_info:
            display.warning(f"No processes found running in {display.highlight(service_name)}. Cannot send signal.")
            return True
        process_info_map = {info['name']: info for info in all_info}
    except Fault as e:
        display.error(
            f"Error getting process list for {display.highlight(service_name)} before signaling: {e.faultString}"
        )
        _raise_exception_from_fault(e, service_name, "getAllProcessInfo (signal)")
        return False
    except Exception as e:
        display.error(
            f"Unexpected error getting process list for {display.highlight(service_name)} before signaling: {e}"
        )
        return False

    for requested_name in process_names:
        process_info = process_info_map.get(requested_name)

        if not process_info:
            display.warning(
                f"Process {display.highlight(requested_name)} not found or not running in {display.highlight(service_name)}. Skipping signal."
            )
            results[requested_name] = True
            continue

        group_name = process_info.get('group')
        name_for_api = make_supervisor_api_name(group_name, requested_name)

        try:
            supervisor_api.signalProcess(name_for_api, signal_name.upper())
            results[requested_name] = True
        except Fault as e:
            fault_string = getattr(e, 'faultString', '')
            if "BAD_NAME" in fault_string:
                display.dimmed(
                    f"Process {display.highlight(requested_name)} not found by supervisor (BAD_NAME). Skipping signal."
                )
                results[requested_name] = True
            elif "NOT_RUNNING" in fault_string:
                display.dimmed(
                    f"Process {display.highlight(requested_name)} not running (NOT_RUNNING). Skipping signal."
                )
                results[requested_name] = True
            else:
                display.error(f"Error signaling process {display.highlight(requested_name)}: {e.faultString}")
                _raise_exception_from_fault(e, service_name, action, requested_name)
                results[requested_name] = False
        except Exception as e:
            display.error(f"Unexpected error signaling process {display.highlight(requested_name)}: {e}")
            results[requested_name] = False

    success = all(results.values())
    logger.info(f"Signal operation completed: service={service_name}, signal={signal_name}, success={success}")
    return success


def _handle_info(supervisor_api, service_name: str):
    """
    Retrieves raw process information from supervisor for status display.

    Logic:
    1. Calls supervisor's getAllProcessInfo() API
    2. Returns the raw process data as a list of dictionaries
    3. Each dict contains: name, state, pid, uptime, etc.
    4. Used by status/info commands for detailed process information
    """
    action = "info"
    try:
        info_list = supervisor_api.getAllProcessInfo()
        return info_list if isinstance(info_list, list) else []
    except Fault as e:
        _raise_exception_from_fault(e, service_name, action)
