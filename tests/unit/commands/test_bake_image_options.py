"""`fm bake`'s image flags: `--image` names what is PRODUCED, `--base-image` what it builds FROM.

`--image` accepts both forms of a reference and the form decides the tag:

- a full ref (``ghcr.io/acme/mysite:v42``) is handed straight to ``BakeManager.bake(tag=...)`` and
  built verbatim, so `bench_config.image` is left alone;
- a bare repo (``ghcr.io/acme/mysite``) becomes `bench_config.image` and `tag=None`, so
  ``resolve_tag()`` generates ``<repo>:<UTC timestamp>-<git short sha>``.

The discrimination is `has_explicit_tag`, which looks for a colon only after the last ``/`` -- a
registry host-port (``localhost:5000/repo``) is therefore a bare repo, not a repo tagged
``5000/repo``. That case is pinned here because getting it wrong builds an unpushable image named
after a port.

`--base-image` is the opposite direction and must be pinned: a base is a specific thing you pin, so
a tagless value is refused rather than silently resolving to ``:latest``.

These tests drive the real ``BakeManager`` (so ``resolve_tag`` is the production one) and replace
only ``bake()`` itself, which is where the image would actually be built.
"""

import re
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from frappe_manager.commands.bake import bake
from frappe_manager.site_manager.modules.bake import BakeManager

runner = CliRunner()


@pytest.fixture
def cli():
    test_app = typer.Typer()
    test_app.command("bake")(bake)
    return test_app


@pytest.fixture
def baked(monkeypatch):
    """Capture what the command hands to the bake, plus the tag that would be built.

    ``built`` mirrors the real ``bake()``'s own first line (``tag = tag or self.resolve_tag()``),
    so the generated form is produced by production code. Only the git sha is fixed, to keep the
    assertion exact; the timestamp stays real and is asserted by shape.
    """
    calls: dict = {}

    def fake_bake(self, tag=None, push=None):
        calls["bench_config"] = self.bench_config
        calls["tag"] = tag
        calls["push"] = push
        calls["built"] = tag or self.resolve_tag()
        return calls["built"]

    monkeypatch.setattr(BakeManager, "bake", fake_bake)
    monkeypatch.setattr(BakeManager, "_git_short_sha", lambda self: "testsha")
    monkeypatch.setattr("frappe_manager.site_manager.modules.bake.DockerClient", MagicMock())
    return calls


def _invoke(cli, *args):
    """Standalone bake: no bench directory, no compose project, nothing on disk."""
    return runner.invoke(cli, ["--apps", "frappe:version-15", *args])


def test_bare_repo_gets_a_generated_timestamp_tag(cli, baked):
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite")

    assert result.exit_code == 0, result.output
    # A bare repo is a repo: it becomes the configured image and resolve_tag() supplies the tag.
    assert baked["bench_config"].image == "ghcr.io/acme/mysite"
    assert baked["tag"] is None
    assert re.fullmatch(r"ghcr\.io/acme/mysite:\d{14}-testsha", baked["built"]), baked["built"]


def test_full_reference_is_built_verbatim(cli, baked):
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite:v42")

    assert result.exit_code == 0, result.output
    assert baked["tag"] == "ghcr.io/acme/mysite:v42"
    assert baked["built"] == "ghcr.io/acme/mysite:v42"
    # The ref is not a repo, so it must not be written over the configured image repo.
    assert baked["bench_config"].image is None
    assert "Baked image: ghcr.io/acme/mysite:v42" in result.output


def test_registry_host_port_is_not_mistaken_for_a_tag(cli, baked):
    """`localhost:5000/repo` is a bare repo; the colon belongs to the registry host."""
    result = _invoke(cli, "--image", "localhost:5000/mysite")

    assert result.exit_code == 0, result.output
    assert baked["bench_config"].image == "localhost:5000/mysite"
    assert baked["tag"] is None
    assert re.fullmatch(r"localhost:5000/mysite:\d{14}-testsha", baked["built"]), baked["built"]


def test_full_reference_still_names_the_standalone_bake_after_the_repo(cli, baked):
    """The standalone config builder sees the raw value, and `_bake_name` strips the tag."""
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite:v42")

    assert result.exit_code == 0, result.output
    assert baked["bench_config"].name == "mysite"


def test_base_image_lands_on_build_base_image(cli, baked):
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite", "--base-image", "ghcr.io/acme/frappe-custom:v15")

    assert result.exit_code == 0, result.output
    assert baked["bench_config"].build.base_image == "ghcr.io/acme/frappe-custom:v15"
    # --base-image is the FROM, never the thing produced.
    assert baked["bench_config"].image == "ghcr.io/acme/mysite"
    assert re.fullmatch(r"ghcr\.io/acme/mysite:\d{14}-testsha", baked["built"]), baked["built"]


def test_tagless_base_image_is_rejected(cli, baked):
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite", "--base-image", "ghcr.io/acme/frappe-custom")

    assert result.exit_code != 0
    assert "--base-image must include a tag" in result.output
    # Refused at parse time, so no bake is attempted.
    assert baked == {}


def test_base_image_host_port_alone_is_not_a_tag(cli, baked):
    """The refusal uses the same colon rule, so a host-port does not satisfy it."""
    result = _invoke(cli, "--image", "ghcr.io/acme/mysite", "--base-image", "localhost:5000/frappe-custom")

    assert result.exit_code != 0
    assert "--base-image must include a tag" in result.output
    assert baked == {}


def test_tag_option_is_gone(cli, baked):
    """`--tag` was deleted outright; nothing aliases it, so it must not be accepted."""
    result = _invoke(cli, "--tag", "ghcr.io/acme/mysite:v42")

    assert result.exit_code != 0
    assert "No such option" in result.output
    assert baked == {}
