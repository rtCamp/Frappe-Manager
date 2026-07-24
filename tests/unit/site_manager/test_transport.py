"""Unit tests for Phase 5 image transport helpers.

Covers the DOCKER_HOST set/restore contextmanager, remote DOCKER_HOST
construction from the [deploy] remote target (DeployConfig), and env-substitution
of registry creds.
"""

import os

import pytest

from frappe_manager.site_manager.bench_config import DeployConfig
from frappe_manager.site_manager.modules.transport import (
    build_docker_host,
    docker_host_env,
    expand_env,
    remote_docker_host,
)


def test_docker_host_env_sets_and_restores_prior_value(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://old@prior:22")
    with docker_host_env("ssh://frappe@remote:2222"):
        assert os.environ["DOCKER_HOST"] == "ssh://frappe@remote:2222"
    assert os.environ["DOCKER_HOST"] == "ssh://old@prior:22"


def test_docker_host_env_unsets_when_no_prior(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    with docker_host_env("ssh://frappe@remote:2222"):
        assert os.environ["DOCKER_HOST"] == "ssh://frappe@remote:2222"
    assert "DOCKER_HOST" not in os.environ


def test_docker_host_env_noop_when_falsy(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://keep@me:22")
    with docker_host_env(None):
        assert os.environ["DOCKER_HOST"] == "ssh://keep@me:22"
    assert os.environ["DOCKER_HOST"] == "ssh://keep@me:22"


def test_docker_host_env_restores_on_exception(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "ssh://old@prior:22")
    with pytest.raises(RuntimeError), docker_host_env("ssh://frappe@remote:2222"):
        raise RuntimeError("boom")
    assert os.environ["DOCKER_HOST"] == "ssh://old@prior:22"


def test_build_docker_host_uses_remote_config_user_port():
    rc = DeployConfig(ssh_server="ignored", ssh_user="deploy", ssh_port=2200)
    assert build_docker_host("prod.example", rc) == "ssh://deploy@prod.example:2200"


def test_build_docker_host_defaults_without_config():
    assert build_docker_host("prod.example") == "ssh://frappe@prod.example:22"


def test_remote_docker_host_from_config():
    rc = DeployConfig(ssh_server="prod.example", ssh_user="frappe", ssh_port=22)
    assert remote_docker_host(rc) == "ssh://frappe@prod.example:22"


def test_remote_docker_host_none_when_no_server():
    assert remote_docker_host(None) is None
    assert remote_docker_host(DeployConfig()) is None


def test_expand_env_substitutes(monkeypatch):
    monkeypatch.setenv("FM_REG_TOKEN", "s3cr3t")
    assert expand_env("${FM_REG_TOKEN}") == "s3cr3t"
    assert expand_env("$FM_REG_TOKEN") == "s3cr3t"
    assert expand_env(None) is None
    assert expand_env("plain") == "plain"
