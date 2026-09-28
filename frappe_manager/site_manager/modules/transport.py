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

Airgap works without a mode flag: ship the image yourself
(``docker save <img> | ssh host docker load``) and the presence check finds it, because
the check asks the daemon to resolve the reference rather than matching printed columns
(see ``image_present``). If it is genuinely missing and cannot be pulled, the pull
failure says so.

A digest reference (``name@sha256:...``) is answered by the same rule, with one honest
limit: ``save``/``load`` does not carry registry provenance, so a hand-shipped image
satisfies the tag it was saved under and not a digest.
"""

import os

from frappe_manager import FM_IMAGE_PREFIX
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


def _registry_refusal_remedy(image: str, host: str, said: str) -> str | None:
    """The fix for a pull failure the registry (or docker's own credential layer) described
    precisely enough to act on, or None when it did not.

    Checked BEFORE the login-presence heuristic below, because that heuristic answers one
    question -- "has this host run `docker login`?" -- and most first-install pull failures are
    not about that at all. Answering "run docker login" to a DNS failure or a full disk sends the
    reader to the wrong place, which is worse than saying nothing.

    The signatures are matched on the registry's own words, because the exit code is 1 for all of
    them. The credential, auth, resolution and network ones are strings measured from a real
    `docker pull`; the rate-limit and disk ones are the daemon's documented wording.
    """
    remedy = None
    if "error getting credentials" in said or "docker-credential-" in said:
        remedy = (
            "docker cannot run the credential helper named in ~/.docker/config.json, so it cannot "
            "pull anything at all, public images included. Install that helper or delete the "
            '"credsStore"/"credHelpers" entry naming it'
        )
    elif "denied" in said or "unauthorized" in said or "authentication required" in said:
        remedy = (
            (
                f"{host} rejected this host's stored credentials. fm's own images are public, so "
                f"`docker logout {host}` is usually the fix; log in again if the image is private "
                f"and the token has expired"
            )
            if logged_in_to(host)
            else (
                f"{host} refused an anonymous request. If that image is private, run "
                f"`docker login {host}` here and retry: fm uses the daemon's own credentials and "
                f"holds none itself"
            )
        )
    elif "toomanyrequests" in said or "rate limit" in said:
        remedy = (
            f"{host} is rate-limiting this host's anonymous pulls. Run `docker login {host}` with "
            f"any account to raise the limit, or wait for the window to reset"
        )
    elif "no such host" in said or "dial tcp" in said or "i/o timeout" in said or "connection refused" in said:
        remedy = (
            f"this host could not reach {host} at all. Check DNS, the network, and any HTTP proxy "
            f"the docker daemon needs (proxies are the daemon's setting, not the shell's)"
        )
    elif "x509" in said or "certificate signed by unknown authority" in said:
        remedy = (
            f"the TLS certificate {host} presented is not trusted by the docker daemon, which is "
            f"what an intercepting proxy looks like. Add its CA to the daemon's trust store"
        )
    elif "no space left on device" in said:
        remedy = "the disk is full. Reclaim space (`fm prune`, `docker system prune`) and retry"
    elif ("not found" in said or "manifest unknown" in said) and image.startswith(FM_IMAGE_PREFIX):
        remedy = (
            "no such image is published. fm only publishes images for RELEASED versions, so a dev "
            "or git checkout has none: install a released fm, or build the images yourself "
            "(Docker/build.sh)"
        )
    return remedy


def pull_failure_cause(image: str, error: object) -> str:
    """Why a pull failed, without naming the image.

    Separate from the message because one cause commonly fails SEVERAL pulls -- a first install
    asks for ten images, and a broken credential helper or an unreachable registry refuses all of
    them identically. A caller with a list groups by this and prints it once.

    Registries disagree about how they refuse an anonymous request for a private image.
    Docker Hub says "may require 'docker login'". GHCR says ``manifest unknown``, which
    reads exactly like an image that was never pushed, so an operator who is merely not
    logged in goes hunting for a bad reference. fm holds no registry credentials of its own, so
    this is the only place it can point at the real fix.

    The actionable sentence comes first and the registry's words last, because the reader
    stops at the first line.
    """
    host = normalized_domain(image)
    said = _registry_said(error)
    cause = _registry_refusal_remedy(image, host, said.lower()) or _auth_cause(
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
    return f"{cause}. The registry said: {said}"


def pull_failure_message(image: str, error: object) -> str:
    """`pull_failure_cause` with the image named, for a caller pulling exactly one."""
    return f"Could not pull {image}: {pull_failure_cause(image, error)}"


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
    repo = ImageRef.parse(image).name
    if repo.endswith("-nginx"):
        base_repo = repo.removesuffix("-nginx")
        cause += (
            f". Note {repo} is a SEPARATE repository from {base_repo}: being authorized "
            f"for one does not authorize the other"
        )
    already = f" ({', '.join(pushed)} already pushed successfully)" if pushed else ""
    return f"Could not push {image}{already}: {cause}. The registry said: {_registry_said(error)}"


def image_present(docker: DockerClient, image: str) -> bool:
    """True when the target daemon already has ``image``, by tag reference or by digest.

    Asks the daemon to resolve the reference (``docker image inspect``) rather than
    matching ``docker images``' ``Repository``/``Tag`` columns. Those columns cannot
    represent a digest at ALL, so every ``name@sha256:...`` reference was reported
    missing here whatever the daemon actually held: a pull on every deploy, and a
    ``docker save``/``docker load`` airgap that could never satisfy the check. Resolution
    is docker's own, so it answers for both reference shapes with one rule.

    A locally built image that was never pushed still does not satisfy a digest
    reference, and that is correct rather than a remaining gap: a digest names content
    the registry attested, and the daemon has no such record for it.
    """
    return docker.image_exists(image)


def fetch_one(docker: DockerClient, image: str, output=None) -> None:
    """Pull ``image`` if the target daemon does not already have it; no-op otherwise.

    The shared primitive under both ``fetch_image`` (a known pair) and
    ``resolve_recorded_nginx_image`` (which must have ``image`` locally before it can read a
    label baked INTO it). Splitting it out means resolving a companion never pulls its app
    image twice: whichever call gets there first satisfies ``image_present`` for the other.
    """
    from frappe_manager.docker import DockerException

    if image_present(docker, image):
        return
    if output is not None:
        output.print(f"Fetching {image} from registry")
    try:
        docker.pull(image, stream=False)
    except DockerException as e:
        raise TransportError(pull_failure_message(image, e)) from e


def fetch_image(docker: DockerClient, image: str, nginx_image: str, output=None) -> None:
    """Ensure ``image`` and its companion ``nginx_image`` are present on the target daemon.

    The pair is passed in, never worked out from ``image``: which companion belongs to an app
    image is recorded when the pair is built, and reconstructing it here from the name is the
    guess that could pull a stranger's image into the container that terminates the site's
    traffic.

    Present already (built here, or shipped by hand) means nothing to do. Anything missing is
    pulled with the daemon's own registry credentials. The companion is not optional: ``fm bake``
    always builds one (even for an assetless bench), so a missing companion is a real problem --
    never pushed, wrong registry, no read permission -- and its pull failure is fatal exactly like
    the app image's, with the same diagnosis. This runs before the deploy pipeline renders compose
    or touches anything else, so raising here catches it before compose is ever pinned to it.
    """
    fetch_one(docker, image, output=output)
    fetch_one(docker, nginx_image, output=output)


def recorded_nginx_image(docker: DockerClient, image: str) -> str | None:
    """The companion recorded ON ``image`` by the bake that built it, or None.

    ``fm bake`` stamps ``fm.nginx.image``. Reading it is a lookup of a recorded fact, not a
    derivation: the label travels with the artifact, so a bake in CI and a deploy on a server
    agree without sharing anything else. None means the image predates the label or was not built
    by fm, and the caller must ask for the companion rather than invent one.
    """
    return docker.image_labels(image).get("fm.nginx.image") or None


def resolve_recorded_nginx_image(docker: DockerClient, image: str, output=None) -> str | None:
    """The ``fm.nginx.image`` label read off ``image``, pulling it first if needed.

    The label lives INSIDE the image, so it cannot be read until the image itself is present on
    the daemon -- ``fetch_one`` first, then ``recorded_nginx_image``. None (never a raise) when
    the label is absent: the caller decides between an explicit ``--nginx-image`` and refusing
    (see notes/image-pairing-design.md, "Where the nginx reference comes from").
    """
    fetch_one(docker, image, output=output)
    return recorded_nginx_image(docker, image)


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

