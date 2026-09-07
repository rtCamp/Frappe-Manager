"""Image transport helpers.

A baked image reaches the daemon that will run it in one of two ways, and which
one applies is discovered rather than configured: if the image is already on that
daemon it is used as-is, otherwise it is pulled.

- Built here: a bake loads the image into the local daemon, so a same-host
  ``fm switch`` finds it and never contacts a registry.
- Built elsewhere: ``docker pull``, with the daemon's own credentials.

Registry authentication is docker's, not fm's. ``~/.docker/config.json`` already
holds it, with multi-registry support and credential helpers (osxkeychain, pass,
ecr-login) that fm has no way to reach. So a private registry is a one-time
``docker login`` on the host, or a login step in CI, and everything here inherits it.

Airgap works without a mode flag, for a TAG reference: ship the image yourself
(``docker save <img> | ssh host docker load``) and the presence check finds it,
because save/load preserves the ``repo:tag`` docker printed it under and the
check matches on exactly that pair (see ``image_present``). If it is genuinely
missing and cannot be pulled, the pull failure says so.

A digest-pinned reference (``name@sha256:...``) is a different case: the
presence check has no way to see it (it matches only on ``Repository`` and
``Tag``), so shipping one this way always looks like a miss, and the
fallback pull then needs exactly the registry access airgapping was meant
to avoid.
"""

import os

from frappe_manager.docker import DockerClient
from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.utils.helpers import ImageRef


class TransportError(FrappeManagerException):
    """Raised when an image transport step fails."""


def normalized_domain(image: str) -> str:
    """The domain ``image`` pulls from, by docker's own rule, defaulted like
    ``ParseNormalizedNamed`` does when ``image`` names none.

    Delegates to ``ImageRef.parse``: the first path segment is a host only when it
    looks like one -- it contains a dot or a port, or is exactly ``localhost``.
    Otherwise the reference is a Docker Hub short name (``erpnext/app``), whose
    domain is ``docker.io``.
    """
    return ImageRef.parse(image).normalized_domain


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


def _auth_cause(host: str, *, when_out: str, when_in: str) -> str:
    """Word the login half of a transport failure -- shared fact, different conclusions.

    Pull and push both learn only one thing from docker: whether this host has ever run
    `docker login` (or has a credential helper configured) for ``host``. Each then draws a
    different conclusion from that same fact, so the two clauses are supplied by the caller.
    """
    return when_in if logged_in_to(host) else when_out


def _pull_failure_message(image: str, error: object) -> str:
    """Why a pull failed, leading with what to do about it.

    Registries disagree about how they refuse an anonymous request for a private image.
    Docker Hub says "may require 'docker login'". GHCR says ``manifest unknown``, which
    reads exactly like an image that was never pushed, so an operator who is merely not
    logged in goes hunting for a bad reference. fm holds no registry credentials of its own, so
    this message is the only place it can point at the real fix.

    The actionable sentence comes first and the registry's words last, because the reader
    stops at the first line.
    """
    host = normalized_domain(image)
    cause = _auth_cause(
        host,
        when_in=(
            f"this host is logged in to {host}, so check the image was actually pushed "
            f"(fm bake --push) and that this account can read it"
        ),
        when_out=(
            f"no docker login for {host} was found. If that image is private, run "
            f"`docker login {host}` here and retry: fm uses the daemon's own credentials "
            f"and holds none itself"
        ),
    )
    return f"Could not pull {image}: {cause}. The registry said: {_registry_said(error)}"


def _push_failure_message(image: str, error: object, pushed: list[str]) -> str:
    """Why a push failed, leading with what already landed and what to do about it.

    ``fm bake --push`` pushes the app image first, and it is a SEPARATE repository from
    its ``-nginx`` companion that follows -- so a registry that happily accepted the first
    can still refuse the second (most registries do not auto-create a repository on push,
    and even ones that do often gate it per account), leaving the app image live with its
    companion missing. That half-published state is exactly what an operator needs to know
    about before chasing the registry error, so it is named first here rather than left to
    a bare ``docker push`` traceback.
    """
    host = normalized_domain(image)
    cause = _auth_cause(
        host,
        when_in=(
            f"this host is logged in to {host}, so the push was denied rather than "
            f"unauthenticated: most likely the repository does not exist there yet, and "
            f"this registry either does not auto-create one on push or this account is not "
            f"permitted to"
        ),
        when_out=(
            f"no docker login for {host} was found. fm uses the daemon's own credentials "
            f"and holds none itself, so run `docker login {host}` here and retry"
        ),
    )
    repo = image.rpartition(":")[0]
    if repo.endswith("-nginx"):
        base_repo = repo.removesuffix("-nginx")
        cause += (
            f". Note {repo} is a SEPARATE repository from {base_repo}: being authorized "
            f"for one does not authorize the other"
        )
    already = f" ({', '.join(pushed)} already pushed successfully)" if pushed else ""
    return f"Could not push {image}{already}: {cause}. The registry said: {_registry_said(error)}"


def image_present(docker: DockerClient, image: str) -> bool:
    """True when ``image`` (repo:tag) is present on the target daemon.

    Matches on ``docker images``' own ``Repository`` and ``Tag`` columns, so this
    sees exactly a plain ``repo:tag`` reference. It cannot see a digest-pinned
    reference (``name@sha256:...``) at all: such an image is always reported
    missing here, regardless of whether it is actually sitting on the daemon,
    and callers pull it every time. A failed pull is now fatal, so a caller must
    not hand this a digest reference expecting a local image to satisfy it.

    A digest-aware match would need ``docker images -a --digests``: the default
    listing omits an image pulled purely by digest with no local tag, and only
    ``--digests`` ever populates ``Digest`` instead of the literal ``<none>``.
    It would also have to key on ``Repository`` + ``Digest``, since such an
    image's ``Tag`` is itself ``<none>``. Even that would not close the gap in
    general: ``Digest`` is registry provenance, not a locally computed content
    hash, so it stays empty (``RepoDigests: []``) for an image that was built
    locally or moved by ``docker save``/``docker load`` rather than pulled, and
    no flag combination recovers a digest docker was never given.
    """
    repo, _, tagpart = image.rpartition(":")
    try:
        for img in docker.images():
            if img.get("Repository") == repo and img.get("Tag") == tagpart:
                return True
    except Exception:
        return False
    return False


def fetch_image(docker: DockerClient, image: str, output=None) -> None:
    """Ensure ``image`` (+ its derived nginx image) is present on the target daemon.

    Present already (built here, or shipped by hand) means nothing to do. Anything
    missing is pulled with the daemon's own registry credentials. The companion nginx
    image is not optional: ``fm bake`` always builds one (even for an assetless bench),
    so a missing companion here is a real problem -- never pushed, wrong registry, no
    read permission -- and its pull failure is fatal exactly like the app image's, with
    the same diagnosis. This runs before the deploy pipeline renders compose or touches
    anything else, so raising here catches a missing companion before compose is ever
    pinned to it.
    """
    from frappe_manager.docker import DockerException
    from frappe_manager.site_manager.modules.bake import BakeManager

    nginx_image = BakeManager.nginx_image_ref(image)
    missing = [i for i in (image, nginx_image) if not image_present(docker, i)]
    if not missing:
        return

    for i in missing:
        if output is not None:
            output.print(f"Fetching {i} from registry")
        try:
            docker.pull(i, stream=False)
        except DockerException as e:
            raise TransportError(_pull_failure_message(i, e)) from e


def push_images(docker: DockerClient, images: list[str], output=None) -> None:
    """``docker push`` each image in ``images``, with the daemon's own credentials.

    The app image goes first and its ``-nginx`` companion second, and they are separate
    repositories, so a failure on the second leaves the first live on the registry. A raw
    ``DockerException`` here would surface only the registry's own text with none of that
    context, so a failure is re-raised as a ``TransportError`` naming what already pushed.
    """
    from frappe_manager.docker import DockerException

    images = [i for i in images if i]
    if not images:
        return
    pushed: list[str] = []
    for image in images:
        if output is not None:
            output.change_head(f"Pushing {image}")
        try:
            docker.push(image, stream=False)
        except DockerException as e:
            raise TransportError(_push_failure_message(image, e, pushed)) from e
        if output is not None:
            output.print(f"Pushed {image}", emoji_code=":white_check_mark:")
        pushed.append(image)

