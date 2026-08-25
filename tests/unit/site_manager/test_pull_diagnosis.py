"""What fm tells you when a pull fails.

fm holds no registry credentials: authentication is the daemon's, from `docker login` or a
credential helper. That makes this message the only place fm can point a reader at the
real fix, and registries make that harder than it sounds. Verified against real ones:

    docker.io   pull access denied ... or may require 'docker login'
    ghcr.io     manifest unknown

GHCR's answer to an anonymous request for a private image is indistinguishable from a tag
that was never pushed, so an operator who is merely not logged in goes hunting through the
registry UI for a bad tag. fm can tell the difference, because `docker login` records the
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
    registry_host,
)

MODULE = "frappe_manager.site_manager.modules.transport"


def _docker_error(stderr: str) -> DockerException:
    """A real DockerException, since that is the only type fetch_image catches."""
    return DockerException(
        ["docker", "pull", "x"],
        SubprocessOutput(stdout=[], stderr=[stderr], combined=[stderr], exit_code=1),
    )


class TestRegistryHost:
    @pytest.mark.parametrize(
        ("tag", "host"),
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
    def test_the_host_is_read_by_dockers_own_rule(self, tag, host):
        assert registry_host(tag) == host


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
    def _fail(self, tag, stderr, logged_in):
        docker = MagicMock()
        docker.images.return_value = []
        docker.pull.side_effect = _docker_error(stderr)
        with patch(f"{MODULE}.logged_in_to", return_value=logged_in), pytest.raises(TransportError) as excinfo:
            fetch_image(docker, tag)
        return str(excinfo.value)

    def test_a_logged_out_pull_names_the_login_command(self):
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=False)

        assert "docker login ghcr.io" in message

    def test_the_action_comes_before_the_registry_text(self):
        """`manifest unknown` first would send the reader after a bad tag."""
        message = self._fail("ghcr.io/acme/app:v1", "manifest unknown", logged_in=False)

        assert message.index("docker login") < message.index("manifest unknown")

    def test_a_logged_in_pull_points_at_the_tag_instead(self):
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


class TestTheNginxImageIsStillOptional:
    def test_a_missing_assets_image_stays_a_warning(self):
        """The diagnosis must not promote the optional image's failure to fatal."""
        docker = MagicMock()
        docker.images.return_value = [{"Repository": "ghcr.io/acme/app", "Tag": "v1"}]
        docker.pull.side_effect = _docker_error("manifest unknown")
        output = MagicMock()

        with patch(f"{MODULE}.logged_in_to", return_value=False):
            fetch_image(docker, "ghcr.io/acme/app:v1", output=output)

        assert output.warning.call_count == 1
