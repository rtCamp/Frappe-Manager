"""What fm tells you when a pull fails.

fm holds no registry credentials: authentication is the daemon's, from `docker login` or a
credential helper. That makes this message the only place fm can point a reader at the
real fix, and registries make that harder than it sounds. Verified against real ones:

    docker.io   pull access denied ... or may require 'docker login'
    ghcr.io     manifest unknown

GHCR's answer to an anonymous request for a private image is indistinguishable from an image
that was never pushed, so an operator who is merely not logged in goes hunting through the
registry UI for a bad image. fm can tell the difference, because `docker login` records the
host in `~/.docker/config.json` even when the secret lives in a helper.

The diagnosis therefore comes first and the registry's words last: readers stop at the
first line, and docker's own exception text is six lines of preamble before its one useful
sentence.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.site_manager.modules.transport import (
    TransportError,
    _registry_said,
    fetch_image,
    logged_in_to,
    normalized_domain,
)

MODULE = "frappe_manager.site_manager.modules.transport"


def _docker_error(stderr: str) -> DockerException:
    """A real DockerException, since that is the only type fetch_image catches."""
    return DockerException(
        ["docker", "pull", "x"],
        SubprocessOutput(stdout=[], stderr=[stderr], combined=[stderr], exit_code=1),
    )


class TestNormalizedDomain:
    @pytest.mark.parametrize(
        ("image", "host"),
        [
            ("ghcr.io/acme/app:v1", "ghcr.io"),
            ("registry.example.com:5000/acme/app:v1", "registry.example.com:5000"),
            ("localhost:5000/app:v1", "localhost:5000"),
            ("localhost/app:v1", "localhost"),
            # No dot, no port, not localhost: a Docker Hub namespace, not a host.
            ("erpnext/app:v1", "docker.io"),
            ("ubuntu:24.04", "docker.io"),
        ],
    )
    def test_the_host_is_read_by_dockers_own_rule(self, image, host):
        assert normalized_domain(image) == host


class TestLoggedInDetection:
    def _config(self, tmp_path, payload):
        (tmp_path / "config.json").write_text(json.dumps(payload))
        return tmp_path

    def test_a_host_under_auths_counts_as_logged_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKER_CONFIG", str(self._config(tmp_path, {"auths": {"ghcr.io": {}}})))

        assert logged_in_to("ghcr.io") is True

    def test_an_auths_entry_with_no_secret_still_counts(self, tmp_path, monkeypatch):
        """With a credsStore the secret lives in the keychain and `auths` holds only the
        host. That is the normal shape on a developer machine, so it must not read as
        logged out."""
        payload = {"auths": {"ghcr.io": {}}, "credsStore": "osxkeychain"}
        monkeypatch.setenv("DOCKER_CONFIG", str(self._config(tmp_path, payload)))

        assert logged_in_to("ghcr.io") is True

    def test_a_per_registry_helper_counts(self, tmp_path, monkeypatch):
        payload = {"credHelpers": {"123.dkr.ecr.eu-west-1.amazonaws.com": "ecr-login"}}
        monkeypatch.setenv("DOCKER_CONFIG", str(self._config(tmp_path, payload)))

        assert logged_in_to("123.dkr.ecr.eu-west-1.amazonaws.com") is True

    def test_a_different_host_does_not_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKER_CONFIG", str(self._config(tmp_path, {"auths": {"docker.io": {}}})))

        assert logged_in_to("ghcr.io") is False

    def test_a_missing_config_is_not_logged_in_rather_than_an_error(self, tmp_path, monkeypatch):
        """Only ever used to sharpen a message, so it must never raise on the way."""
        monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "nothing-here"))

        assert logged_in_to("ghcr.io") is False

    def test_unparseable_config_is_not_logged_in_rather_than_an_error(self, tmp_path, monkeypatch):
        (tmp_path / "config.json").write_text("{not json")
        monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))

        assert logged_in_to("ghcr.io") is False


class TestTheMessage:
    def _fail(self, image, stderr, logged_in, nginx_image=None):
        docker = MagicMock()
        docker.image_exists.return_value = False
        docker.pull.side_effect = _docker_error(stderr)
        nginx_image = nginx_image or f"{image}-nginx"
        with patch(f"{MODULE}.logged_in_to", return_value=logged_in), pytest.raises(TransportError) as excinfo:
            fetch_image(docker, image, nginx_image)
        return str(excinfo.value)

    def test_a_logged_out_pull_names_the_login_command(self):
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=False)

        assert "docker login ghcr.io" in message

    def test_the_action_comes_before_the_registry_text(self):
        """`manifest unknown` first would send the reader after a bad image."""
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=False)

        assert message.index("docker login") < message.index("manifest unknown")

    def test_a_logged_in_pull_points_at_the_image_instead(self):
        """Blaming auth when they are authenticated would send them in a circle."""
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=True)

        assert "docker login" not in message
        assert "fm bake --push" in message

    def test_the_registrys_own_words_survive(self):
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=True)

        assert "manifest unknown" in message

    def test_dockers_preamble_is_not_quoted(self):
        """The exception's own str is command + exit code + a stdout note, then the point."""
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=False)

        assert "returned with code" not in message
        assert "docker pull" not in message

    def test_the_daemon_error_prefix_is_stripped(self):
        message = self._fail("ghcr.io/acme/app:v1", "Error response from daemon: manifest unknown", logged_in=False)

        assert "Error response from daemon" not in message
        assert "manifest unknown" in message

    def test_a_hub_short_name_is_diagnosed_against_docker_io(self):
        message = self._fail("erpnext/app:v1", "pull access denied", logged_in=False)

        assert "docker login docker.io" in message

    def test_an_error_without_stderr_falls_back_to_its_own_text(self):
        """Not every failure carries a stderr; the message must still say something."""
        assert _registry_said(RuntimeError("socket hung up")) == "socket hung up"


class TestFailuresThatAreNotAboutLogin:
    """Every stderr here was captured from a real `docker pull`, because the whole point is that
    the login question -- the only one the fallback can answer -- is the WRONG question for most
    of what goes wrong on a first install. Answering "run docker login" to a full disk or a DNS
    failure costs the reader the one line they actually read.
    """

    def _fail(self, image, stderr, logged_in=False):
        docker = MagicMock()
        docker.image_exists.return_value = False
        docker.pull.side_effect = _docker_error(stderr)
        with patch(f"{MODULE}.logged_in_to", return_value=logged_in), pytest.raises(TransportError) as excinfo:
            fetch_image(docker, image, f"{image}-nginx")
        return str(excinfo.value)

    def test_a_stale_login_for_a_public_registry_says_log_out(self):
        """The trap this exists for: fm's images are PUBLIC, so a host that never logged in pulls
        them fine and a host holding an expired ghcr token is refused. Telling that reader to log
        in leaves them exactly where they were."""
        message = self._fail(
            "ghcr.io/rtcamp/frappe-manager-frappe:v1.0.0",
            "Error response from daemon: error from registry: denied",
            logged_in=True,
        )

        assert "docker logout ghcr.io" in message

    def test_a_credential_helper_that_is_not_installed_is_named_as_the_cause(self):
        """A config.json copied from a mac names `docker-credential-desktop`, which no Linux host
        has, and then NOTHING pulls -- public images included. The registry is never even reached,
        so blaming it sends the reader to the wrong machine."""
        message = self._fail(
            "ghcr.io/rtcamp/frappe-manager-frappe:v1.0.0",
            'error getting credentials - err: exec: "docker-credential-desktop": '
            "executable file not found in $PATH, out: ``",
        )

        assert "credsStore" in message
        assert "docker login" not in message

    def test_an_unpublished_fm_version_says_so_instead_of_blaming_auth(self):
        """fm publishes images for RELEASED versions only, so a git checkout asks for a tag that
        was never pushed. That is the single most likely first-install failure for a contributor,
        and `not found` reads like a private-image refusal."""
        message = self._fail(
            "ghcr.io/rtcamp/frappe-manager-frappe:v99.99.99",
            'failed to resolve reference "ghcr.io/rtcamp/frappe-manager-frappe:v99.99.99": not found',
        )

        assert "RELEASED versions" in message
        assert "docker login" not in message

    def test_an_unreachable_registry_is_a_network_problem_not_an_auth_one(self):
        message = self._fail(
            "ghcr.io/acme/app:v1",
            'failed to do request: Head "https://ghcr.io/v2/": dial tcp: lookup ghcr.io on 127.0.0.53:53: no such host',
        )

        assert "DNS" in message
        assert "docker login" not in message

    def test_a_rate_limited_registry_is_told_apart_from_a_refusal(self):
        """Docker Hub answers an over-limit anonymous pull with a 429, and six of the ten images
        fm prefetches come from Hub. Logging in is the fix here, but for a different reason than
        a private image, and the wait-and-retry option only exists for this one."""
        message = self._fail("redis:8-alpine", "toomanyrequests: You have reached your pull rate limit")

        assert "rate-limiting" in message
        assert "docker login docker.io" in message

    def test_an_intercepting_proxy_is_named_rather_than_the_registry(self):
        message = self._fail("ghcr.io/acme/app:v1", "x509: certificate signed by unknown authority")

        assert "trust store" in message

    def test_a_full_disk_is_not_reported_as_a_registry_problem(self):
        message = self._fail("ghcr.io/acme/app:v1", "write /var/lib/docker/tmp/x: no space left on device")

        assert "disk is full" in message
        assert "docker login" not in message

    def test_a_third_party_private_image_still_gets_the_login_advice(self):
        """The fallback must survive: a 401 from a registry this host never logged in to IS the
        login case, and the signatures above must not swallow it."""
        message = self._fail("quay.io/acme/app:v1", "unexpected status from HEAD request: 401 Unauthorized")

        assert "docker login quay.io" in message



class TestTheCompanionImageIsNoLongerOptional:
    """Reversed contract (was `TestTheNginxImageIsStillOptional`): `fm bake` now always
    builds the `-nginx` companion (bake.py's `_build_nginx_image` no longer skips it for an
    assetless bench), so its absence here means a real problem -- never pushed, wrong
    registry, no permission -- not an optional extra. Tolerating it used to let compose get
    pinned to a tag that was never built; the recreate-swap that later discovered that threw
    past the deploy's health-gate rollback entirely (that net only catches an unhealthy swap
    that succeeded, never one that raised). So a companion pull failure is fatal now, exactly
    like the app image's, with the same diagnosis.
    """

    def test_a_missing_companion_image_is_now_fatal(self):
        docker = MagicMock()
        docker.image_exists.side_effect = lambda ref: ref == "ghcr.io/acme/app:v1"
        docker.pull.side_effect = _docker_error("manifest unknown")
        output = MagicMock()

        with patch(f"{MODULE}.logged_in_to", return_value=False), pytest.raises(TransportError) as err:
            fetch_image(docker, "ghcr.io/acme/app:v1", "ghcr.io/acme/app-nginx:v1", output=output)

        assert "ghcr.io/acme/app-nginx:v1" in str(err.value)
        output.warning.assert_not_called()
