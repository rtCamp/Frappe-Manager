"""Image transport helpers.

A baked image reaches the daemon that will run it in one of two ways, and which
one applies is discovered rather than configured: if the tag is already on that
daemon it is used as-is, otherwise it is pulled.

- Built here: a bake loads the image into the local daemon, so a same-host
  ``fm switch`` finds it and never contacts a registry.
- Built elsewhere: ``docker login`` from ``[registry]`` creds (env-substituted,
  ``--password-stdin``) then ``docker pull``. With no creds configured the ambient
  daemon credentials are used.

Airgap works without a mode flag: ship the image yourself (``docker save <img> |
ssh host docker load``) and the presence check finds it. If it is genuinely
missing and cannot be pulled, the pull failure says so.
"""

import os

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

    Present already (built here, or shipped by hand) means nothing to do. Anything
    missing is pulled, logging in first when ``[registry]`` carries creds.
    """
    from frappe_manager.docker import DockerException
    from frappe_manager.site_manager.modules.bake import BakeManager

    nginx_tag = BakeManager.nginx_image_tag(tag)
    missing = [t for t in (tag, nginx_tag) if not image_present(docker, t)]
    if not missing:
        return

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
