from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.exceptions import FrappeManagerException


class DockerException(FrappeManagerException):
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
