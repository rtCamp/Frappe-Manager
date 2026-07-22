"""Image bake for `fm bake` (image-based deploy, Phase 3).

Builds an immutable app image from a bench configuration:

1. Provision the bench's apps into a temporary build-context via plain
   ``docker run`` (image runner, see ``BenchAppManager._run_in_provision_image``)
   rather than a live compose service.
2. ``COPY`` the provisioned ``frappe-bench`` tree into a runtime image on top of
   the same base image (``Docker/frappe/runtime.Dockerfile``), keeping the
   existing supervisor entrypoint.

The provisioning step is the exact same shared path used by ``fm create``
(:func:`frappe_manager.site_manager.provisioner.provision`); only the
``BenchAppManager`` runner seam differs (``provision_image`` set -> docker run).
"""

import importlib.metadata
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from frappe_manager.docker import DockerClient
from frappe_manager.logger import log
from frappe_manager.logger.context import LoggerContext
from frappe_manager.logger.contextual import ContextualLogger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig
from frappe_manager.site_manager.modules.bench_app import BenchAppManager
from frappe_manager.site_manager.modules.transport import push_images
from frappe_manager.site_manager.provisioner import provision
from frappe_manager.utils.docker import host_run_cp, run_command_with_exit_code


class BakeError(Exception):
    """Raised when a bake cannot proceed or fails."""


class BakeManager:
    """Bakes an immutable app image from a bench configuration."""

    def __init__(
        self,
        bench_config: BenchConfig,
        output_handler: OutputHandler | None = None,
        logger: ContextualLogger | None = None,
    ):
        self.bench_config = bench_config
        self.output = output_handler or RichOutputHandler()
        self.logger = logger or ContextualLogger(
            log.get_logger(),
            context=LoggerContext(bench=bench_config.name, operation="bake"),
        )
        self.docker_client = DockerClient()

    def resolve_base_image(self) -> str:
        """Base image for provisioning and the runtime ``FROM``."""
        if self.bench_config.build and self.bench_config.build.base_image:
            return self.bench_config.build.base_image
        version = importlib.metadata.version("frappe-manager")
        return f"ghcr.io/rtcamp/frappe-manager-frappe:v{version}"

    @staticmethod
    def apply_build_overrides(bench_config, output=None) -> None:
        """Apply ``[build].python_version``/``node_version`` onto the bench config
        so provisioning bakes with them (they take precedence over the
        create-time / auto-detected versions). ``[build].platforms`` (multi- or
        cross-arch) is not yet honored: provision-then-COPY bakes host-arch
        binaries, so a foreign-arch image needs emulated provisioning."""
        build = bench_config.build
        if not build:
            return
        if build.python_version:
            bench_config.python_version = build.python_version
        if build.node_version:
            bench_config.node_version = build.node_version
        if output and build.platforms and build.platforms != ["linux/amd64"]:
            output.warning(
                f"[build].platforms={build.platforms} is not yet honored; building for the host "
                f"architecture only (multi/cross-arch needs emulated provisioning).",
            )

    def resolve_tag(self) -> str:
        """``<repo>:<UTC timestamp>-<git short sha|nogit>``.

        ``<repo>`` comes from ``[deploy].image`` and is required.
        """
        deploy = self.bench_config.deploy
        if deploy is None or not deploy.image:
            raise BakeError(
                "No deploy image configured. Set [deploy].image in the bench "
                "config (or pass --image) before baking.",
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"{deploy.image}:{timestamp}-{self._git_short_sha()}"

    def _git_short_sha(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
                cwd=str(self._repo_root()),
                capture_output=True,
                text=True,
                check=False,
            )
            sha = result.stdout.strip()
            if result.returncode == 0 and sha:
                return sha
        except Exception as e:
            self.logger.debug(f"git short sha unavailable: {e}")
        return "nogit"

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    def _derive_apps_list(self) -> list[AppConfig]:
        """Reconstruct the app clone specs from the live bench.

        ``apps_list`` is not persisted to ``bench_config.toml``; the source of
        truth for an existing bench is the cloned git repos under
        ``apps/`` (ordered by ``sites/apps.txt``, frappe first). Each app's
        origin URL + current branch become a reproducible clone spec.
        """
        bench_dir = Path(self.bench_config.root_path).parent
        frappe_bench = bench_dir / "workspace" / "frappe-bench"
        apps_dir = frappe_bench / "apps"
        if not apps_dir.is_dir():
            raise BakeError(f"Bench apps directory not found: {apps_dir}")

        names: list[str] = []
        apps_txt = frappe_bench / "sites" / "apps.txt"
        if apps_txt.exists():
            names = [n.strip() for n in apps_txt.read_text().splitlines() if n.strip()]
        for child in sorted(apps_dir.iterdir()):
            if child.name not in names and (child / ".git").exists():
                names.append(child.name)
        if "frappe" in names:
            names = ["frappe"] + [n for n in names if n != "frappe"]

        apps: list[AppConfig] = []
        for name in names:
            app_path = apps_dir / name
            if not (app_path / ".git").exists():
                self.output.warning(f"Skipping app '{name}': not a git repository")
                continue
            url = self._git_output(app_path, "config", "--get", "remote.origin.url")
            branch = self._git_output(app_path, "rev-parse", "--abbrev-ref", "HEAD")
            spec = url or name
            if branch and branch != "HEAD":
                spec = f"{spec}:{branch}"
            apps.append(AppConfig.from_string(spec))

        if not apps:
            raise BakeError(f"No git-backed apps found under {apps_dir} to bake.")
        return apps

    @staticmethod
    def _git_output(app_path: Path, *args: str) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(app_path), *args],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip() or None
        except Exception:
            return None
        return None

    def _runtime_dockerfile(self) -> Path:
        """Locate ``Docker/frappe/runtime.Dockerfile`` (repo root, else CWD)."""
        candidates = [
            self._repo_root() / "Docker" / "frappe" / "runtime.Dockerfile",
            Path.cwd() / "Docker" / "frappe" / "runtime.Dockerfile",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise BakeError(
            "Could not find Docker/frappe/runtime.Dockerfile "
            f"(looked in: {', '.join(str(c) for c in candidates)}).",
        )

    def _nginx_dockerfile(self) -> Path:
        """Locate ``Docker/nginx/Dockerfile`` (repo root, else CWD)."""
        candidates = [
            self._repo_root() / "Docker" / "nginx" / "Dockerfile",
            Path.cwd() / "Docker" / "nginx" / "Dockerfile",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise BakeError(
            "Could not find Docker/nginx/Dockerfile "
            f"(looked in: {', '.join(str(c) for c in candidates)}).",
        )

    @staticmethod
    def nginx_image_tag(tag: str) -> str:
        """Derive the app-nginx (assets) tag from the frappe app tag.

        ``<repo>:<tagpart>`` -> ``<repo>-nginx:<tagpart>``.
        """
        repo, _, tagpart = tag.rpartition(":")
        if not repo:
            raise BakeError(f"Malformed image tag (missing ':'): {tag}")
        return f"{repo}-nginx:{tagpart}"

    def _seed_bench_skeleton(self, frappe_bench_dir: Path, base_image: str) -> None:
        """Create the minimal frappe-bench skeleton provisioning expects.

        Mirrors ``BenchDockerManager.create_compose_dirs`` (sans compose/nginx):
        base directories, ``apps.txt`` + ``common_site_config.json`` seeds, and
        the prebaked ``.uv``/``.fnm`` runtimes copied out of the base image.
        """
        for sub in ("sites", "apps", "logs", "config", "config/pids"):
            (frappe_bench_dir / sub).mkdir(parents=True, exist_ok=True)

        apps_txt = frappe_bench_dir / "sites" / "apps.txt"
        if not apps_txt.exists():
            apps_txt.write_text("frappe\n")

        common_site_config = frappe_bench_dir / "sites" / "common_site_config.json"
        if not common_site_config.exists():
            common_site_config.write_text("{}")

        uv_dir = frappe_bench_dir / ".uv"
        if not uv_dir.exists():
            host_run_cp(
                base_image,
                source="/workspace/frappe-bench/.uv",
                destination=str(uv_dir.absolute()),
                docker=self.docker_client,
            )

        fnm_dir = frappe_bench_dir / ".fnm"
        if not fnm_dir.exists():
            host_run_cp(
                base_image,
                source="/workspace/frappe-bench/.fnm",
                destination=str(fnm_dir.absolute()),
                docker=self.docker_client,
            )

    def bake(self, tag: str | None = None, push: bool | None = None) -> str:
        """Provision -> build the runtime image (+ optional registry push). Returns the built tag.

        ``tag`` overrides the auto-generated ``<repo>:<ts>-<sha>`` when given.
        ``push`` forces (``True``) or suppresses (``False``) the registry push;
        ``None`` (default) pushes when ``[registry].distribution == 'registry'``.
        """
        base_image = self.resolve_base_image()
        tag = tag or self.resolve_tag()
        dockerfile = self._runtime_dockerfile()

        self.output.print(f"Baking image {tag}")
        self.output.print(f"Base image: {base_image}")

        context_dir = Path(tempfile.mkdtemp(prefix="fm-bake-"))
        try:
            frappe_bench_dir = context_dir / "workspace" / "frappe-bench"
            frappe_bench_dir.mkdir(parents=True, exist_ok=True)

            self.output.change_head("Resolving bench apps")
            apps = self._derive_apps_list()
            self.bench_config.apps_list = apps
            self.output.print(f"Baking apps: {', '.join(a.name for a in apps)}")

            self.output.change_head("Preparing build context")
            self._seed_bench_skeleton(frappe_bench_dir, base_image)

            app_manager = BenchAppManager(
                logger=self.logger,
                bench_name=self.bench_config.name,
                bench_path=context_dir,
                docker_client=self.docker_client,
                bench_config=self.bench_config,
                output_handler=self.output,
                provision_image=base_image,
            )

            self.apply_build_overrides(self.bench_config, self.output)
            self.output.change_head("Provisioning apps into build context")
            provision(
                app_manager,
                apps,
                output=self.output,
                use_uv=self.bench_config.use_uv,
                github_token=self.bench_config.github_token,
                use_run=True,
                deploy_config=self.bench_config.deploy,
            )

            self.output.change_head(f"Building runtime image {tag}")
            build_cmd = [
                "docker",
                "build",
                "--build-arg",
                f"BASE_IMAGE={base_image}",
                "-f",
                str(dockerfile),
                "-t",
                tag,
                str(context_dir / "workspace"),
            ]
            run_command_with_exit_code(build_cmd, stream=False, capture_output=False)

            self.output.print(f"Built image: {tag}", emoji_code=":white_check_mark:")

            nginx_tag = self._build_nginx_image(frappe_bench_dir, tag)

            if self._should_push(push):
                self._push_images([t for t in (tag, nginx_tag) if t])

            # NOTE: local tag pruning to releases_retain_limit is deferred.
            return tag
        finally:
            shutil.rmtree(context_dir, ignore_errors=True)

    def _registry_config(self):
        return getattr(self.bench_config, "registry", None)

    def _should_push(self, push: bool | None) -> bool:
        """Resolve the push decision: explicit flag wins; otherwise push when a
        ``[registry]`` table is configured with ``distribution == 'registry'``.

        The registry host is normally encoded in ``[deploy].image`` (e.g.
        ``localhost:5000/rtest``); a separate ``[registry].registry`` is only
        needed for ``docker login``.
        """
        reg = self._registry_config()
        default = bool(reg and reg.distribution == "registry")
        return default if push is None else push

    def _push_images(self, tags: list[str]) -> None:
        reg = self._registry_config()
        push_images(self.docker_client, tags, reg, output=self.output)

    def _build_nginx_image(self, frappe_bench_dir: Path, tag: str) -> str | None:
        """Build the app-nginx assets image (``<repo>-nginx:<tag>``).

        The nginx Dockerfile's ``app-assets`` target COPYs the built
        ``sites/assets`` into the image at nginx's configured root. We build with
        the provisioned ``frappe-bench`` tree as the context and stage the small
        nginx build files (template.conf/502.html/entrypoint.sh) into it so the
        base-stage COPYs resolve from the same single context.
        """
        assets_dir = frappe_bench_dir / "sites" / "assets"
        if not assets_dir.is_dir():
            self.output.warning(
                "No sites/assets in the baked bench; skipping app-nginx image build. "
                "Assets will not be served by the image-mode nginx.",
            )
            return None

        nginx_dockerfile = self._nginx_dockerfile()
        nginx_tag = self.nginx_image_tag(tag)

        # Stage the nginx base-stage build files into the frappe-bench context.
        for fname in ("template.conf", "502.html", "entrypoint.sh"):
            src = nginx_dockerfile.parent / fname
            if not src.exists():
                raise BakeError(f"Missing nginx build file: {src}")
            shutil.copy2(src, frappe_bench_dir / fname)

        self.output.change_head(f"Building app-nginx image {nginx_tag}")
        build_cmd = [
            "docker",
            "build",
            "-f",
            str(nginx_dockerfile),
            "--target",
            "app-assets",
            "-t",
            nginx_tag,
            str(frappe_bench_dir),
        ]
        run_command_with_exit_code(build_cmd, stream=False, capture_output=False)
        self.output.print(f"Built image: {nginx_tag}", emoji_code=":white_check_mark:")
        return nginx_tag
