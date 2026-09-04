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


class TestNginxCompanionBuild:
    """Fix 1a: `_build_nginx_image` is unconditional now. Every baked app image gets a
    companion, even an assetless bench, because `ImageShape.image("nginx")` derives that
    tag unconditionally and an image-mode deploy pins compose to it with no existence
    check -- a companion this build skipped would leave compose pinned to an image that
    was never built.
    """

    def _mgr(self):
        mgr = _manager()
        mgr.output = MagicMock()
        return mgr

    def test_an_assetless_bench_still_produces_a_companion_tag(self, tmp_path):
        """No `sites/assets` at all (bench-only, or a workspace snapshot that never ran
        `bench build`) must still return a resolvable `-nginx` tag rather than `None`."""
        frappe_bench_dir = tmp_path / "workspace" / "frappe-bench"
        frappe_bench_dir.mkdir(parents=True)  # sites/assets deliberately absent

        mgr = self._mgr()
        with patch(RUNNER) as runner:
            nginx_tag = mgr._build_nginx_image(frappe_bench_dir, "ghcr.io/acme/erp:v1")

        assert nginx_tag == "ghcr.io/acme/erp-nginx:v1"
        argv = runner.call_args.args[0]
        assert argv[argv.index("-t") + 1] == nginx_tag

    def test_the_staged_context_has_an_empty_assets_dir_so_the_copy_resolves(self, tmp_path):
        """The Dockerfile's `app-assets` stage does `COPY sites/assets ...`: docker refuses
        that build if the source path does not exist at all, so an empty dir has to be
        staged even when the bench built nothing (staging is cleaned up in a `finally`
        right after the build call, so it must be inspected from inside the mocked call)."""
        frappe_bench_dir = tmp_path / "workspace" / "frappe-bench"
        frappe_bench_dir.mkdir(parents=True)
        seen = {}

        def _capture(cmd, **kwargs):
            assets_dir = Path(cmd[-1]) / "sites" / "assets"
            seen["is_dir"] = assets_dir.is_dir()
            seen["contents"] = sorted(p.name for p in assets_dir.iterdir()) if assets_dir.is_dir() else None

        mgr = self._mgr()
        with patch(RUNNER, side_effect=_capture):
            mgr._build_nginx_image(frappe_bench_dir, "ghcr.io/acme/erp:v1")

        assert seen["is_dir"] is True
        assert seen["contents"] == []

    def test_a_bench_with_assets_still_materializes_them(self, tmp_path):
        """Regression guard: the always-build change must not skip materialization when
        assets DO exist."""
        frappe_bench_dir = tmp_path / "workspace" / "frappe-bench"
        assets = frappe_bench_dir / "sites" / "assets"
        assets.mkdir(parents=True)
        (assets / "assets.json").write_text("{}")
        seen = {}

        def _capture(cmd, **kwargs):
            assets_dir = Path(cmd[-1]) / "sites" / "assets"
            seen["assets_json"] = (assets_dir / "assets.json").read_text()

        mgr = self._mgr()
        with patch(RUNNER, side_effect=_capture):
            mgr._build_nginx_image(frappe_bench_dir, "ghcr.io/acme/erp:v1")

        assert seen["assets_json"] == "{}"


class TestNginxImageRef:
    """`BakeManager.nginx_image_ref` (renamed from `nginx_image_tag`, #digest-refs): derives
    the companion image reference from the app image reference BY NAME, so it refuses rather
    than mangles when that cannot work -- a digest reference (no second image's digest is
    derivable from another image's) or a reference with no explicit tag at all.
    """

    @pytest.mark.parametrize(
        ("image", "expected"),
        [
            ("app:v1", "app-nginx:v1"),
            ("org/app:v1", "org/app-nginx:v1"),
            ("ghcr.io/org/app:v1", "ghcr.io/org/app-nginx:v1"),
            ("ghcr.io/org/team/app:v1", "ghcr.io/org/team/app-nginx:v1"),
            ("localhost:5000/app:v1", "localhost:5000/app-nginx:v1"),
        ],
    )
    def test_tagged_references_derive_the_companion_by_name(self, image, expected):
        assert BakeManager.nginx_image_ref(image) == expected

    @pytest.mark.parametrize("image", ["app", "org/app", "ghcr.io/org/app", "ghcr.io/org/team/app"])
    def test_a_bare_repo_with_no_tag_is_refused(self, image):
        with pytest.raises(BakeError, match="missing an explicit"):
            BakeManager.nginx_image_ref(image)

    def test_an_untagged_host_port_reference_is_refused_not_mangled(self):
        """Regression: `rpartition(":")` used to split on the registry PORT colon here,
        silently returning 'localhost-nginx:5000/app' -- a different, wrong repository --
        instead of raising anything. `localhost:5000/app` has no tag at all; ImageRef knows
        the host:port colon does not name one, so this must refuse like any other bare repo."""
        with pytest.raises(BakeError, match="missing an explicit"):
            BakeManager.nginx_image_ref("localhost:5000/app")

    @pytest.mark.parametrize("image", ["app@sha256:abc", "ghcr.io/org/app:v1@sha256:abc"])
    def test_a_digest_reference_is_refused_with_the_reason(self, image):
        """A digest reference used to be silently mangled ('app@sha256-nginx:abc') instead of
        refused. The companion is a DIFFERENT image, so its digest cannot be derived from the
        app image's; the message says exactly that."""
        with pytest.raises(BakeError, match="content hash of ONE image") as excinfo:
            BakeManager.nginx_image_ref(image)
        assert image in str(excinfo.value)
