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

import os

from frappe_manager.docker import DockerClient


class TransportError(Exception):
    """Raised when an image transport step fails."""


def registry_host(tag: str) -> str:
    """The registry a tag pulls from, by docker's own rule.

    The first path segment is a host only when it looks like one: it contains a dot or a
    port, or is exactly ``localhost``. Otherwise the reference is a Docker Hub short name
    (``erpnext/app``), whose host is ``docker.io``.
    """
    first = tag.split("/", 1)[0] if "/" in tag else ""
    if first and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"


def logged_in_to(host: str) -> bool:
    """Whether ``~/.docker/config.json`` shows a login for ``host``.

    ``docker login`` records the host under ``auths`` even when the secret itself lives in
    a credential helper, so the host's presence is a reliable signal that a login happened
    and its absence that one did not. A ``credHelpers`` entry counts too: that is a
    per-registry helper configured by hand.

    Only ever used to sharpen an error message, so an unreadable or absent config is
    treated as "no login" rather than raised.
    """
    import json
    from pathlib import Path

    config = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    try:
        data = json.loads(config.read_text())
    except (OSError, ValueError):
        return False
    return host in (data.get("auths") or {}) or host in (data.get("credHelpers") or {})


def _registry_said(error: object) -> str:
    """The registry's own words, without docker's command and exit-code preamble.

    ``DockerException``'s message is six lines of framing (the command, the exit code, a
    note about stdout) with the one useful sentence at the bottom. Quoting all of it buries
    the diagnosis below it, which is the whole thing this module is trying to avoid.
    """
    stderr = getattr(getattr(error, "output", None), "stderr", None)
    if not stderr:
        return str(error)
    text = " ".join(line.strip().strip("'") for line in stderr if line.strip())
    return text.replace("Error response from daemon:", "").strip() or str(error)


def _pull_failure_message(tag: str, error: object) -> str:
    """Why a pull failed, leading with what to do about it.

    Registries disagree about how they refuse an anonymous request for a private image.
    Docker Hub says "may require 'docker login'". GHCR says ``manifest unknown``, which
    reads exactly like a tag that was never pushed, so an operator who is merely not
    logged in goes hunting for a bad tag. fm holds no registry credentials of its own, so
    this message is the only place it can point at the real fix.

    The actionable sentence comes first and the registry's words last, because the reader
    stops at the first line.
    """
    host = registry_host(tag)
    if logged_in_to(host):
        cause = (
            f"this host is logged in to {host}, so check the tag was actually pushed "
            f"(fm bake --push) and that this account can read it"
        )
    else:
        cause = (
            f"no docker login for {host} was found. If that image is private, run "
            f"`docker login {host}` here and retry: fm uses the daemon's own credentials "
            f"and holds none itself"
        )
    return f"Could not pull {tag}: {cause}. The registry said: {_registry_said(error)}"


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
            raise TransportError(_pull_failure_message(t, e)) from e


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
