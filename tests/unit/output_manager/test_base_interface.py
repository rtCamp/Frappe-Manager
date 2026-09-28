"""
Tests for the OutputHandler base interface.

Ensures the abstract base class correctly enforces the interface contract.
"""

import pytest

from frappe_manager.output_manager.base import CI_ENVIRONMENT_VARIABLES, OutputHandler, running_in_ci
from frappe_manager.output_manager.rich_output import RichOutputHandler


class TestOutputHandlerInterface:
    """Tests for the OutputHandler abstract base class."""

    def test_cannot_instantiate_abstract_base_class(self):
        """OutputHandler cannot be instantiated directly."""
        with pytest.raises(TypeError) as exc_info:
            OutputHandler()  # type: ignore

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_subclass_must_implement_all_methods(self):
        """Subclass must implement all abstract methods."""

        class IncompleteHandler(OutputHandler):
            """Incomplete implementation missing most methods."""

            def start(self, text: str) -> None:
                pass

        with pytest.raises(TypeError) as exc_info:
            IncompleteHandler()  # type: ignore

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_complete_subclass_can_be_instantiated(self):
        """Complete implementation of all methods allows instantiation."""

        class CompleteHandler(OutputHandler):
            """Complete implementation of OutputHandler."""

            def start(self, text: str) -> None:
                pass

            def change_head(self, text: str, style=None) -> None:
                pass

            def update_head(self, text: str) -> None:
                pass

            def stop(self) -> None:
                pass

            def print(self, text: str, emoji_code=":zap:", prefix=None, **kwargs) -> None:
                pass

            def debug(self, text: str, emoji_code=":bug:", **kwargs) -> None:
                pass

            def info(self, text: str, emoji_code=":information:", **kwargs) -> None:
                pass

            def display_error(self, text: str, emoji_code=":no_entry:") -> None:
                pass

            def error(self, text: str, exception: Exception, emoji_code=":no_entry:") -> None:
                if exception:
                    raise exception

            def warning(self, text: str, emoji_code=":warning:") -> None:
                pass

            def live_lines(
                self, data, stdout=True, stderr=True, lines=4, padding=(0, 0, 0, 2), stop_string=None, log_prefix="=>",
            ) -> None:
                pass

            def update_live(self, renderable=None, padding=(0, 0, 0, 0)) -> None:
                pass

            def prompt_ask(
                self,
                prompt: str = "",
                choices: list | None = None,
                default: str | None = None,
                force_yes: bool = False,
                required_flag: str | None = None,
                **kwargs,
            ) -> str:
                return ""

            def prompt_fuzzy(
                self,
                prompt: str,
                choices: list[str],
                default: str | None = None,
                required_flag: str | None = None,
                **kwargs,
            ) -> str:
                return ""

            @property
            def should_stream_docker(self) -> bool:
                return False

            def print_data(self, data, **kwargs) -> None:
                pass


        # Should not raise
        handler = CompleteHandler()
        assert isinstance(handler, OutputHandler)


class TestCiDetection:
    """A CI runner must never be offered a prompt: it cannot answer one."""

    @pytest.mark.parametrize(
        "name",
        ["CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TEAMCITY_VERSION", "JENKINS_URL"],
    )
    def test_a_runners_own_variable_is_enough(self, monkeypatch, name):
        # `CI` alone would miss every runner that does not set it.
        for other in CI_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(name, "true")

        assert running_in_ci() is True

    @pytest.mark.parametrize("value", ["false", "0", "", "  "])
    def test_an_explicitly_falsey_value_means_not_ci(self, monkeypatch, value):
        # Tooling exports `CI=false` to say exactly that; reading mere presence would make fm
        # unpromptable for anyone who does.
        for other in CI_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv("CI", value)

        assert running_in_ci() is False

    def test_a_clean_environment_stays_promptable(self, monkeypatch):
        for other in CI_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(other, raising=False)

        assert running_in_ci() is False

    def test_a_handler_built_under_ci_refuses_to_prompt(self, monkeypatch):
        """The reason this exists: a runner commonly allocates a pty and writes nothing to it,
        which no isatty check can tell apart from a human reading a menu, so the prompt waits
        forever and the job hangs until something outside kills it."""
        monkeypatch.setenv("CI", "true")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

        assert RichOutputHandler().is_interactive() is False
