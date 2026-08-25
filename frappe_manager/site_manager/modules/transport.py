"""Image transport helpers.

A baked image reaches the daemon that will run it in one of two ways, and which
one applies is discovered rather than configured: if the tag is already on that
daemon it is used as-is, otherwise it is pulled.

- Built here: a bake loads the image into the local daemon, so a same-host
  ``fm switch`` finds it and never contacts a registry.
- Built elsewhere: ``docker pull``, with the daemon's own credentials.

Registry authentication is docker's, not fm's. ``~/.docker/config.json`` already
holds it, with multi-registry support and credential helpers (osxkeychain, pass,
ecr-login) that fm has no way to reach. So a private registry is a one-time
``docker login`` on the host, or a login step in CI, and everything here inherits it.

Airgap works without a mode flag: ship the image yourself (``docker save <img> |
ssh host docker load``) and the presence check finds it. If it is genuinely
missing and cannot be pulled, the pull failure says so.
"""

from frappe_manager.docker import DockerClient


class TransportError(Exception):
    """Raised when an image transport step fails."""


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


def fetch_image(docker: DockerClient, tag: str, output=None) -> None:
    """Ensure ``tag`` (+ its derived nginx tag) is present on the target daemon.

    Present already (built here, or shipped by hand) means nothing to do. Anything
    missing is pulled with the daemon's own registry credentials.
    """
    from frappe_manager.docker import DockerException
    from frappe_manager.site_manager.modules.bake import BakeManager

    nginx_tag = BakeManager.nginx_image_tag(tag)
    missing = [t for t in (tag, nginx_tag) if not image_present(docker, t)]
    if not missing:
        return

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


def push_images(docker: DockerClient, tags: list[str], output=None) -> None:
    """``docker push`` each tag in ``tags``, with the daemon's own credentials."""
    tags = [t for t in tags if t]
    if not tags:
        return
    for tag in tags:
        if output is not None:
            output.change_head(f"Pushing {tag}")
        docker.push(tag, stream=False)
        if output is not None:
            output.print(f"Pushed {tag}", emoji_code=":white_check_mark:")
