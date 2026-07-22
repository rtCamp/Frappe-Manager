"""Phase 5 image transport helpers.

Distributing a baked image to a (possibly remote) prod daemon:

- **registry** (default): ``docker login`` from ``[registry]`` creds
  (env-substituted, ``--password-stdin``) then ``docker push``/``pull``. When no
  creds are configured the ambient daemon credentials are used.
- **save_load** (airgap): ``docker save <imgs> | ssh <remote> docker load``.
- **--remote**: run the local orchestrator against a remote daemon by setting
  ``DOCKER_HOST=ssh://<user>@<host>:<port>`` (save/restore the prior value).
"""

import contextlib
import os
import subprocess
from collections.abc import Iterator

from frappe_manager.docker import DockerClient


class TransportError(Exception):
    """Raised when an image transport step fails."""


def expand_env(value: str | None) -> str | None:
    """Substitute ``${VAR}``/``$VAR`` from the environment; ``None`` passthrough."""
    if value is None:
        return None
    return os.path.expandvars(value)


def registry_login(docker: DockerClient, registry_config, output=None) -> bool:
    """``docker login`` from ``[registry]`` creds when both username+password
    (and a registry host) are set (Decision 8).

    Returns ``True`` when a login was performed, ``False`` for ambient creds.
    """
    if registry_config is None:
        return False
    user = expand_env(registry_config.username)
    password = expand_env(registry_config.password)
    registry = registry_config.registry
    if user and password and registry:
        if output is not None:
            output.change_head(f"Logging in to registry {registry}")
        docker.login(registry, user, password)
        return True
    return False


def image_present(docker: DockerClient, tag: str) -> bool:
    """True when ``tag`` (repo:tag) is present on the target daemon."""
    repo, _, tagpart = tag.rpartition(":")
    try:
        for img in docker.images():
            if img.get("Repository") == repo and img.get("Tag") == tagpart:
                return True
    except Exception:
        return False
    return False


def fetch_image(docker: DockerClient, registry_config, tag: str, output=None) -> None:
    """Ensure ``tag`` (+ its derived nginx tag) is present on the target daemon.

    registry mode: ``docker login`` (when creds set) then ``docker pull`` any
    missing tags. save_load mode: a missing tag is a hard error (transport it
    first). local/absent registry: pull if missing.
    """
    from frappe_manager.docker import DockerException
    from frappe_manager.site_manager.modules.bake import BakeManager

    nginx_tag = BakeManager.nginx_image_tag(tag)
    missing = [t for t in (tag, nginx_tag) if not image_present(docker, t)]
    if not missing:
        return

    distribution = registry_config.distribution if registry_config else "registry"
    if distribution == "save_load":
        raise TransportError(
            f"Image(s) {', '.join(missing)} not present and distribution='save_load'; "
            "transport the image(s) (docker save/load) to this daemon before switching.",
        )

    registry_login(docker, registry_config, output=output)
    for t in missing:
        if output is not None:
            output.print(f"Fetching {t} from registry")
        try:
            docker.pull(t, stream=False)
        except DockerException as e:
            # The nginx image is optional (absent when the bench has no assets).
            if t == nginx_tag:
                if output is not None:
                    output.warning(f"Could not pull nginx image {t} (continuing): {e}")
                continue
            raise TransportError(f"Failed to fetch image {t} from registry: {e}") from e


def push_images(docker: DockerClient, tags: list[str], registry_config, output=None) -> None:
    """Log in (if creds) then ``docker push`` each tag in ``tags``."""
    tags = [t for t in tags if t]
    if not tags:
        return
    registry_login(docker, registry_config, output=output)
    for tag in tags:
        if output is not None:
            output.change_head(f"Pushing {tag}")
        docker.push(tag, stream=False)
        if output is not None:
            output.print(f"Pushed {tag}", emoji_code=":white_check_mark:")


def present_tags(docker: DockerClient, tags: list[str]) -> list[str]:
    """Filter ``tags`` to those actually present on the local daemon (for save)."""
    wanted = [t for t in tags if t]
    try:
        present = {f"{img.get('Repository')}:{img.get('Tag')}" for img in docker.images()}
    except Exception:
        return wanted
    return [t for t in wanted if t in present]


def ssh_target(remote_config) -> tuple[str, int]:
    """``(user@host, port)`` from a ``RemoteConfig``."""
    if remote_config is None or not remote_config.ssh_server:
        raise TransportError("Remote transport requires [remote].ssh_server.")
    user = remote_config.ssh_user or "frappe"
    return f"{user}@{remote_config.ssh_server}", int(remote_config.ssh_port or 22)


def transport_save_load(tags: list[str], remote_config, output=None) -> None:
    """Stream ``docker save <tags>`` into ``ssh <remote> docker load``.

    Airgap/``distribution == "save_load"`` path: transports the images to the
    remote daemon without a registry.
    """
    tags = [t for t in tags if t]
    if not tags:
        return
    target, port = ssh_target(remote_config)
    if output is not None:
        output.change_head(f"Transporting {len(tags)} image(s) to {target} via docker save/load")

    save_cmd = ["docker", "save", *tags]
    load_cmd = ["ssh", "-p", str(port), target, "docker", "load"]

    save_proc = subprocess.Popen(save_cmd, stdout=subprocess.PIPE)  # noqa: S603
    try:
        load_proc = subprocess.run(load_cmd, stdin=save_proc.stdout, check=False)  # noqa: S603
    finally:
        if save_proc.stdout is not None:
            save_proc.stdout.close()
        save_ret = save_proc.wait()

    if save_ret != 0:
        raise TransportError(f"docker save failed (exit {save_ret}).")
    if load_proc.returncode != 0:
        raise TransportError(f"remote docker load failed (exit {load_proc.returncode}).")
    if output is not None:
        output.print(f"Loaded {len(tags)} image(s) on {target}", emoji_code=":white_check_mark:")


def build_docker_host(host: str, remote_config=None) -> str:
    """Build ``ssh://<user>@<host>:<port>`` from an explicit host, using the
    ``RemoteConfig`` for the user/port defaults when present."""
    user = "frappe"
    port = 22
    if remote_config is not None:
        user = remote_config.ssh_user or user
        port = int(remote_config.ssh_port or port)
    return f"ssh://{user}@{host}:{port}"


def remote_docker_host(remote_config) -> str | None:
    """``ssh://<user>@<host>:<port>`` from a ``RemoteConfig``, or ``None`` when
    no ``ssh_server`` is configured."""
    if remote_config is None or not remote_config.ssh_server:
        return None
    return build_docker_host(remote_config.ssh_server, remote_config)


@contextlib.contextmanager
def docker_host_env(docker_host: str | None) -> Iterator[None]:
    """Temporarily set ``os.environ['DOCKER_HOST']`` for the block, restoring the
    prior value (or unsetting it) on exit. No-op when ``docker_host`` is falsy.

    Mirrors fmd/runner/docker.py's set/restore so the local orchestrator can
    drive a remote daemon via ``DOCKER_HOST=ssh://``.
    """
    if not docker_host:
        yield
        return
    sentinel = object()
    prior = os.environ.get("DOCKER_HOST", sentinel)
    os.environ["DOCKER_HOST"] = docker_host
    try:
        yield
    finally:
        if prior is sentinel:
            os.environ.pop("DOCKER_HOST", None)
        else:
            os.environ["DOCKER_HOST"] = prior  # type: ignore[arg-type]
