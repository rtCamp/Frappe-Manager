from typing import List

from frappe_manager.docker_wrapper.subprocess_output import SubprocessOutput


class DockerException(Exception):
    def __init__(
        self,
        command_launched: List[str],
        output: SubprocessOutput,
    ):
        self.docker_command: List[str] = command_launched
        self.output = output

        command_launched_str = " ".join(command_launched)

        error_msg = (
            f"The docker command executed was `{command_launched_str}`.\n"
            f"It returned with code {self.output.exit_code}\n"
        )

        if self.output.stdout:
            error_msg += f"The content of stdout is '{self.output.stdout}'\n"
        else:
            error_msg += "The content of stdout can be found above the " "stacktrace (it wasn't captured).\n"

        if self.output.stderr:
            error_msg += f"The content of stderr is '{self.output.stderr}'\n"
        else:
            error_msg += "The content of stderr can be found above the " "stacktrace (it wasn't captured)."

        super().__init__(error_msg)


class NoSuchContainer(DockerException):
    pass


class NoSuchImage(DockerException):
    pass


class NoSuchService(DockerException):
    pass


class NotASwarmManager(DockerException):
    pass


class NoSuchVolume(DockerException):
    pass
