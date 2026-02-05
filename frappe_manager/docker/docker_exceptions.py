from frappe_manager.docker.subprocess_output import SubprocessOutput


class DockerException(Exception):
    """Exception raised when Docker command execution fails."""

    def __init__(
        self,
        command_launched: list[str],
        output: SubprocessOutput,
    ):
        self.docker_command: list[str] = command_launched
        self.output = output

        command_launched_str = " ".join(command_launched)

        error_msg = (
            f"The docker command executed was `{command_launched_str}`.\n"
            f"It returned with code {self.output.exit_code}\n"
        )

        if self.output.stdout:
            stdout_output = "\n".join(self.output.stdout)
            error_msg += f"The content of stdout is \n{'--' * 10}\n'{stdout_output}'\n"
        else:
            error_msg += "The content of stdout can be found above the stacktrace (it wasn't captured).\n"

        if self.output.stderr:
            stderr_output = "\n".join(self.output.stderr)
            error_msg += f"The content of stderr is \n{'--' * 10}\n'{stderr_output}'\n"
        else:
            error_msg += "The content of stderr can be found above the stacktrace (it wasn't captured)."

        super().__init__(error_msg)


def is_port_conflict_error(exception: DockerException) -> tuple[bool, list[int]]:
    """
    Detect if DockerException was caused by port binding conflict.

    Returns:
        Tuple of (is_conflict, list_of_conflicting_ports)

    Examples:
        >>> exc = DockerException(["compose", "up"], output_with_port_error)
        >>> is_conflict, ports = is_port_conflict_error(exc)
        >>> is_conflict
        True
        >>> ports
        [80, 443]
    """
    import re

    stderr_text = "\n".join(exception.output.stderr) if exception.output.stderr else ""

    if not ("port is already allocated" in stderr_text or "address already in use" in stderr_text):
        return False, []

    ports = []
    port_patterns = [
        r"Bind for (?:0\.0\.0\.0|:::):(\d+)",
        r"port (\d+) is already allocated",
        r"address already in use.*:(\d+)",
    ]

    for pattern in port_patterns:
        matches = re.findall(pattern, stderr_text)
        for match in matches:
            port = int(match)
            if port not in ports:
                ports.append(port)

    return len(ports) > 0, sorted(ports)
