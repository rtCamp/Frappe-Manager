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
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from frappe_manager.docker import DockerClient
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import AppConfig, BenchConfig
from frappe_manager.site_manager.modules.bench_app import BenchAppManager
from frappe_manager.site_manager.modules.transport import push_images
from frappe_manager.site_manager.provisioner import provision
from frappe_manager.utils.docker import host_run_cp, run_command_with_exit_code
from frappe_manager.utils.site import read_bench_app_refs, read_bench_node_version, read_bench_python_version


class BakeError(Exception):
    """Raised when a bake cannot proceed or fails."""


class BakeManager:
    """Bakes an immutable app image from a bench configuration."""

    def __init__(
        self,
        bench_config: BenchConfig,
        output_handler: OutputHandler | None = None,
    ):
        self.bench_config = bench_config
        self.output = output_handler or RichOutputHandler()
        self.logger = get_logger(component="bake")
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
        create-time / auto-detected versions)."""
        build = bench_config.build
        if not build:
            return
        if build.python_version:
            bench_config.python_version = build.python_version
        if build.node_version:
            bench_config.node_version = build.node_version

    @staticmethod
    def resolve_target_platform(
        platform: str | None, daemon_arch: str | None, source: str
    ) -> tuple[str | None, str | None]:
        """``(platform, cross_info)`` for a bake.

        ``None`` = build native (no --platform, no message). Otherwise honored;
        when it differs from the daemon's arch the bake cross-builds under
        emulation -- only valid with the ``provision`` source (a ``workspace``
        snapshot contains host-arch binaries) and requires binfmt/Rosetta on
        the daemon.

        More than one platform is refused. fm loads each built image into the local
        daemon so the pre-flight boot check and a same-host ``fm switch`` can find it,
        and docker cannot load a multi-platform manifest list.
        """
        if not platform:
            return None, None
        if "," in platform:
            raise BakeError(
                f"build.platform={platform} names more than one platform. fm loads the built "
                f"image into the local daemon so the pre-flight boot check and a same-host "
                f"fm switch can find it, and docker cannot load a multi-platform manifest "
                f"list. Bake one platform at a time, or push a manifest list yourself from a "
                f"container-driver buildx builder.",
            )
        cross = daemon_arch is not None and platform.split("/")[-1] != daemon_arch
        if not cross:
            return platform, None
        if source == "workspace":
            raise BakeError(
                f"build.platform={platform} differs from the daemon arch (linux/{daemon_arch}) "
                f"but build.source='workspace' snapshots host-arch binaries. Cross-arch bakes "
                f"need source='provision'.",
            )
        return platform, (
            f"Cross-building for {platform} on a linux/{daemon_arch} daemon via emulation "
            f"(slower; requires binfmt/QEMU or Rosetta on the build daemon)."
        )

    def _daemon_arch(self) -> str | None:
        """The build daemon's native architecture (e.g. 'amd64', 'arm64'), or None."""
        try:
            return self.docker_client.version().get("Server", {}).get("Arch")
        except Exception:
            return None

    def resolve_tag(self) -> str:
        """``<repo>:<UTC timestamp>-<git short sha|nogit>``.

        ``<repo>`` comes from the top-level ``image`` and is required.
        """
        repo = self.bench_config.image
        if not repo:
            raise BakeError(
                "No image configured. Set top-level image (or pass --image) before baking.",
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"{repo}:{timestamp}-{self._git_short_sha()}"

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

    def _resolve_bake_apps(self) -> list[AppConfig]:
        """Apps to bake: an explicit ``apps_list`` (standalone bake / config) wins;
        otherwise reconstruct it from the live bench's cloned repos."""
        return self.bench_config.apps_list or self._derive_apps_list()

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
            f"Could not find Docker/frappe/runtime.Dockerfile (looked in: {', '.join(str(c) for c in candidates)}).",
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
            f"Could not find Docker/nginx/Dockerfile (looked in: {', '.join(str(c) for c in candidates)}).",
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

    def _populate_context_from_workspace(self, dest_frappe_bench: Path) -> list[AppConfig]:
        """Snapshot the bench's on-disk frappe-bench into the build context (source=workspace).

        Copies code + venv + built assets as-is (no clone/install), excluding dev/site
        state (per-site data, logs, pids, sockets, caches, .git) so the image stays
        code+assets. Relies on fm's constant ``/workspace/frappe-bench`` container path,
        so the relocatable uv venv keeps working after the copy.
        """
        src = Path(self.bench_config.root_path).parent / "workspace" / "frappe-bench"
        if not src.is_dir():
            raise BakeError(f"Workspace not found for source=workspace: {src}")
        apps = self._derive_apps_list()  # read git specs from the real workspace first
        self._copy_workspace(src, dest_frappe_bench)
        return apps

    @staticmethod
    def _copy_workspace(src: Path, dest: Path) -> None:
        """Filtered copy of a frappe-bench tree for a workspace-source bake."""
        shutil.copytree(
            src,
            dest,
            symlinks=True,  # preserve the relocatable venv's /workspace/... symlinks
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.sock", ".cache", ".git"),
        )
        # Drop dev/site state under sites/: keep only assets + apps.txt/apps.json.
        sites = dest / "sites"
        if sites.is_dir():
            for child in sites.iterdir():
                if child.name in ("assets", "apps.txt", "apps.json"):
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        else:
            sites.mkdir(parents=True, exist_ok=True)
        # Reseed minimal sites files (the real ones are volume-mounted at runtime).
        apps_txt = sites / "apps.txt"
        if not apps_txt.exists():
            apps_txt.write_text("frappe\n")
        (sites / "common_site_config.json").write_text("{}")
        # Clear volatile dirs.
        for rel in ("logs", "config/pids"):
            volatile = dest / rel
            if volatile.is_dir():
                for item in volatile.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)

    def _apply_includes(self, frappe_bench_dir: Path, includes: list[str]) -> None:
        """Copy extra host paths into the build context (``[build].include``).

        Each entry is ``src`` or ``src:dest``; ``dest`` is relative to
        ``/workspace/frappe-bench`` (defaults to the src basename). Applied after
        the source populates the tree, so includes override existing files.
        """
        for entry in includes:
            src_str, _, dest_str = entry.partition(":")
            src = Path(src_str).expanduser()
            if not src.exists():
                raise BakeError(f"--include source not found: {src_str}")
            dest_rel = dest_str or src.name
            if dest_rel.startswith("/") or ".." in Path(dest_rel).parts:
                raise BakeError(
                    f"--include destination must be relative and inside the bench (no '..'): {dest_rel!r}",
                )
            dest = frappe_bench_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            self.output.print(f"Included {src_str} -> frappe-bench/{dest_rel}")

    @staticmethod
    def parse_manifest_architectures(manifest_json: str) -> set[str] | None:
        """Architectures offered by a ``docker manifest inspect`` output.

        None when undeterminable (single image manifest, garbage) -- callers
        must treat that as "cannot prove unsupported", never as a failure.
        Buildx attestation entries (architecture "unknown") are ignored.
        """
        try:
            data = json.loads(manifest_json)
        except (ValueError, TypeError):
            return None
        manifests = data.get("manifests") if isinstance(data, dict) else None
        if not manifests:
            return None
        arches = {entry.get("platform", {}).get("architecture") for entry in manifests if isinstance(entry, dict)}
        arches -= {None, "unknown"}
        return arches or None

    def _check_base_image_platform(self, base_image: str, platform: str) -> None:
        """Fail fast when the base image provably lacks the target platform.

        A local copy of the right arch satisfies (offline-friendly). Otherwise
        the registry manifest list decides: target arch missing -> BakeError
        BEFORE provisioning starts, instead of docker's mid-bake 'no matching
        manifest' error. Inspection failures stay silent (docker will enforce
        reality later anyway).
        """
        want = platform.split("/")[-1]
        try:
            result = run_command_with_exit_code(
                ["docker", "image", "inspect", base_image, "--format", "{{.Architecture}}"],
                stream=False,
            )
            if "".join(result.stdout).strip() == want:
                return
        except Exception:
            pass
        try:
            result = run_command_with_exit_code(["docker", "manifest", "inspect", base_image], stream=False)
            arches = self.parse_manifest_architectures("".join(result.stdout))
        except Exception:
            return
        if arches and want not in arches:
            raise BakeError(
                f"Base image {base_image} is not available for {platform} "
                f"(published architectures: {', '.join(sorted(arches))}). "
                f"Every bake starts FROM this image, so a {platform} build of it must exist first: "
                f"either target one of the published architectures via build.platform, or build/push "
                f"the base for {platform} yourself and point build.base_image at it.",
            )

    def bake(self, tag: str | None = None, push: bool | None = None) -> str:
        """Provision -> build the runtime image (+ optional registry push). Returns the built tag.

        ``tag`` overrides the auto-generated ``<repo>:<ts>-<sha>`` when given.
        ``push`` forces (``True``) or suppresses (``False``) the registry push;
        ``None`` (default) falls back to ``[build].push``.
        """
        base_image = self.resolve_base_image()
        self._assert_buildx()
        tag = tag or self.resolve_tag()
        dockerfile = self._runtime_dockerfile()

        self.output.print(f"Baking image {tag}")
        self.output.print(f"Base image: {base_image}")

        build_config = self.bench_config.build
        source_kind = (build_config.source if build_config else None) or "provision"
        configured = build_config.platform if build_config else None
        platform, cross_info = self.resolve_target_platform(configured, self._daemon_arch(), source_kind)
        if platform:
            self.output.print(f"Target platform: {platform} (from config)")
        if cross_info:
            self.output.warning(cross_info)
        if platform:
            self._check_base_image_platform(base_image, platform)

        # The two image builds get --platform explicitly. This env var is still set
        # because it also steers the PROVISIONING containers that run before them, which
        # take no platform argument of their own. Restored in finally.
        prior_platform = os.environ.get("DOCKER_DEFAULT_PLATFORM")
        if platform:
            os.environ["DOCKER_DEFAULT_PLATFORM"] = platform

        context_dir = Path(tempfile.mkdtemp(prefix="fm-bake-"))
        try:
            frappe_bench_dir = context_dir / "workspace" / "frappe-bench"
            source = (self.bench_config.build.source if self.bench_config.build else None) or "provision"

            if source == "workspace":
                self.output.change_head("Snapshotting bench workspace")
                apps = self._populate_context_from_workspace(frappe_bench_dir)
                self.bench_config.apps_list = apps
                self.output.print(f"Baking apps (workspace snapshot): {', '.join(a.name for a in apps)}")
            else:
                frappe_bench_dir.mkdir(parents=True, exist_ok=True)

                self.output.change_head("Resolving bench apps")
                apps = self._resolve_bake_apps()
                self.bench_config.apps_list = apps
                self.output.print(f"Baking apps: {', '.join(a.name for a in apps)}")

                self.output.change_head("Preparing build context")
                self._seed_bench_skeleton(frappe_bench_dir, base_image)

                app_manager = BenchAppManager(
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
                )

            includes = (self.bench_config.build.include if self.bench_config.build else None) or []
            if includes:
                self.output.change_head("Applying extra includes")
                self._apply_includes(frappe_bench_dir, includes)

            self.output.change_head(f"Building runtime image {tag}")
            py_version = read_bench_python_version(frappe_bench_dir)
            node_version = read_bench_node_version(frappe_bench_dir)
            git_src = (
                Path(self.bench_config.root_path).parent / "workspace" / "frappe-bench"
                if source == "workspace"
                else frappe_bench_dir
            )
            app_refs = read_bench_app_refs(git_src)
            labels = {
                "fm.python.version": py_version,
                "fm.node.version": node_version,
                "fm.apps": json.dumps(app_refs, separators=(",", ":")) if app_refs else None,
            }
            extra = ["--build-arg", f"BASE_IMAGE={base_image}"]
            for _k, _v in labels.items():
                if _v:
                    extra += ["--label", f"{_k}={_v}"]
            self._buildx(
                dockerfile=dockerfile,
                tag=tag,
                context=context_dir / "workspace",
                platform=platform,
                extra=extra,
            )

            self.output.print(f"Built image: {tag}", emoji_code=":white_check_mark:")

            nginx_tag = self._build_nginx_image(frappe_bench_dir, tag, platform=platform)

            if self._should_push(push):
                self._push_images([t for t in (tag, nginx_tag) if t])

            # NOTE: local tag pruning is opt-in via `fm prune` / `--keep N` on deploy/switch.
            return tag
        finally:
            if platform:
                if prior_platform is None:
                    os.environ.pop("DOCKER_DEFAULT_PLATFORM", None)
                else:
                    os.environ["DOCKER_DEFAULT_PLATFORM"] = prior_platform
            shutil.rmtree(context_dir, ignore_errors=True)

    def _registry_config(self):
        return getattr(self.bench_config, "registry", None)

    def _should_push(self, push: bool | None) -> bool:
        """Resolve the push decision: the explicit flag wins, else ``[build].push``.

        Deliberately not inferred from ``[registry]``: a bench configures a registry
        so it can PULL as well (``fm switch``, ``fm create``, ``fm update`` all use
        it), so the presence of creds says nothing about whether this bake should
        publish. The registry host is normally encoded in the top-level ``image``
        (e.g. ``localhost:5000/rtest``); a separate ``[registry].registry`` is only
        needed for ``docker login``.
        """
        if push is not None:
            return push
        build = self.bench_config.build
        return bool(build and build.push)

    def _push_images(self, tags: list[str]) -> None:
        reg = self._registry_config()
        push_images(self.docker_client, tags, reg, output=self.output)

    def _assert_buildx(self) -> None:
        """Fail early and clearly when the buildx plugin is absent."""
        try:
            run_command_with_exit_code(["docker", "buildx", "version"], stream=False, capture_output=True)
        except Exception as e:
            raise BakeError(
                "docker buildx is not available, and fm builds images with it. It ships with "
                "current Docker Engine and Docker Desktop; on a slim install add the "
                "docker-buildx-plugin package.",
            ) from e

    def _buildx(self, *, dockerfile: Path, tag: str, context: Path, platform: str | None, extra: list[str]) -> None:
        """Run one ``docker buildx build`` that loads the result into the local daemon.

        ``--load`` is not optional here, and it is the one thing to know about buildx
        versus ``docker build``: buildx does not guess where the image goes, so without
        an explicit output the tag would not exist on the daemon afterwards. Everything
        downstream expects it to: the pre-flight ``docker run`` on the new tag, the
        ``image_present`` check that lets a same-host ``fm switch`` skip the registry,
        and the push step that follows a bake.

        That is also why a multi-platform bake is refused rather than attempted (see
        ``resolve_target_platform``): docker cannot load a manifest list into a daemon,
        so multi-arch has to be pushed straight to a registry from a container-driver
        builder, which fm does not manage.
        """
        cmd = ["docker", "buildx", "build", "--load", "-f", str(dockerfile), "-t", tag]
        if platform:
            cmd += ["--platform", platform]
        cmd += extra
        cmd.append(str(context))
        run_command_with_exit_code(cmd, stream=False, capture_output=False)

    def _build_nginx_image(self, frappe_bench_dir: Path, tag: str, platform: str | None = None) -> str | None:
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

        # Build from a staging context with app assets resolved to REAL files. Each
        # `sites/assets/<app>` symlinks into `apps/<app>/.../public`, but the nginx image
        # has no `apps/`, so the symlink would dangle at runtime (assets 404).
        staging = Path(tempfile.mkdtemp(prefix="fm-bake-nginx-"))
        try:
            self._materialize_assets(assets_dir, frappe_bench_dir, staging / "sites" / "assets")
            for fname in ("template.conf", "502.html", "entrypoint.sh"):
                src = nginx_dockerfile.parent / fname
                if not src.exists():
                    raise BakeError(f"Missing nginx build file: {src}")
                shutil.copy2(src, staging / fname)

            self.output.change_head(f"Building app-nginx image {nginx_tag}")
            self._buildx(
                dockerfile=nginx_dockerfile,
                tag=nginx_tag,
                context=staging,
                platform=platform,
                extra=["--target", "app-assets"],
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self.output.print(f"Built image: {nginx_tag}", emoji_code=":white_check_mark:")
        return nginx_tag

    def _materialize_assets(self, assets_dir: Path, frappe_bench_dir: Path, dest: Path) -> None:
        """Copy ``sites/assets`` into ``dest`` with app symlinks resolved to real files.

        Each ``sites/assets/<app>`` is a symlink to an absolute *container* path
        (``/workspace/frappe-bench/apps/<app>/.../public``) that does not exist on the
        host, so a naive dereference yields nothing. Remap that container path back to the
        provisioned host tree so the built bundles land as real files (the nginx image has
        no ``apps/`` for the symlink to resolve at runtime).
        """
        container_root = "/workspace/frappe-bench"
        dest.mkdir(parents=True, exist_ok=True)
        for entry in assets_dir.iterdir():
            out = dest / entry.name
            if entry.is_symlink():
                link = str(entry.readlink())
                if link.startswith(container_root):
                    real = frappe_bench_dir / link[len(container_root) :].lstrip("/")
                elif Path(link).is_absolute():
                    real = Path(link)
                else:
                    real = entry.parent / link
                if not real.exists():
                    continue
                if real.is_dir():
                    shutil.copytree(real, out, symlinks=False, ignore_dangling_symlinks=True)
                else:
                    shutil.copy2(real, out)
            elif entry.is_dir():
                shutil.copytree(entry, out, symlinks=False, ignore_dangling_symlinks=True)
            else:
                shutil.copy2(entry, out)
