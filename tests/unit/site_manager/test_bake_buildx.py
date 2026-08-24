"""How fm invokes the image build.

`docker buildx build` does not guess where the built image goes, which is the one
behavioural difference from `docker build` that matters here: without an explicit
output the tag does not exist on the daemon afterwards. Three things downstream
expect it to, so `--load` is mandatory rather than a preference:

- the pre-flight boot check runs `docker run <tag>` before anything is swapped,
- `image_present` is what lets a same-host `fm switch` skip the registry entirely,
- and a push, when asked for, pushes the tag the build just produced.

That is also why more than one platform is refused instead of attempted: docker
cannot load a multi-platform manifest list into a daemon.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.site_manager.modules.bake import BakeError, BakeManager

RUNNER = "frappe_manager.site_manager.modules.bake.run_command_with_exit_code"


def _manager() -> BakeManager:
    """A BakeManager with __init__ bypassed: the build argv depends on nothing else."""
    return object.__new__(BakeManager)


def _build(platform=None, extra=None):
    mgr = _manager()
    with patch(RUNNER) as runner:
        mgr._buildx(
            dockerfile=Path("/ctx/Dockerfile"),
            tag="ghcr.io/acme/erp:v42",
            context=Path("/ctx/workspace"),
            platform=platform,
            extra=extra or [],
        )
    return runner.call_args.args[0]


class TestBuildInvocation:
    def test_the_build_is_buildx_and_loads_into_the_local_daemon(self):
        argv = _build()

        assert argv[:4] == ["docker", "buildx", "build", "--load"]

    def test_the_tag_and_dockerfile_are_passed(self):
        argv = _build()

        assert argv[argv.index("-t") + 1] == "ghcr.io/acme/erp:v42"
        assert argv[argv.index("-f") + 1] == "/ctx/Dockerfile"

    def test_the_context_is_last_because_docker_takes_it_positionally(self):
        argv = _build(extra=["--target", "app-assets"])

        assert argv[-1] == "/ctx/workspace"

    def test_no_platform_flag_when_none_is_configured(self):
        """Native build: pass nothing rather than guessing the daemon's arch."""
        assert "--platform" not in _build()

    def test_a_configured_platform_is_passed_to_the_build(self):
        """It used to reach the build only through DOCKER_DEFAULT_PLATFORM. That env var
        still exists because it also steers the provisioning containers, which take no
        platform argument, but the builds now say what they want."""
        argv = _build(platform="linux/arm64")

        assert argv[argv.index("--platform") + 1] == "linux/arm64"

    def test_extra_flags_are_kept_and_stay_before_the_context(self):
        argv = _build(extra=["--target", "app-assets", "--label", "fm.apps=x"])

        assert argv.index("--target") < argv.index("/ctx/workspace")
        assert argv[argv.index("--target") + 1] == "app-assets"
        assert argv[argv.index("--label") + 1] == "fm.apps=x"


class TestBuildxPrecheck:
    def test_a_missing_buildx_plugin_fails_with_an_actionable_message(self):
        mgr = _manager()
        probe_fails = patch(RUNNER, side_effect=FileNotFoundError("docker: buildx"))

        with probe_fails, pytest.raises(BakeError, match="docker-buildx-plugin"):
            mgr._assert_buildx()

    def test_a_working_buildx_is_probed_once_and_quietly(self):
        mgr = _manager()
        with patch(RUNNER) as runner:
            mgr._assert_buildx()

        assert runner.call_args.args[0] == ["docker", "buildx", "version"]


class TestOutputHandlerIsNotNeeded:
    def test_the_probe_and_the_build_never_touch_the_output_handler(self):
        """Both run before any bake state exists, so they must not assume one."""
        mgr = _manager()
        mgr.output = MagicMock()
        with patch(RUNNER):
            mgr._assert_buildx()
            mgr._buildx(dockerfile=Path("/d"), tag="t:1", context=Path("/c"), platform=None, extra=[])

        mgr.output.assert_not_called()
