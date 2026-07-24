"""The compose wrapper auto-includes an adjacent user-owned `<name>.override.yml`
(deep-merged by Docker after the base, so it wins) and omits it when absent.
"""

from frappe_manager.docker.docker_compose import DockerComposeWrapper


def _base(tmp_path, name="docker-compose.yml"):
    p = tmp_path / name
    p.write_text("services: {}\n")
    return p


def test_no_override_only_base(tmp_path):
    base = _base(tmp_path)
    cmd = DockerComposeWrapper(base).docker_compose_cmd
    assert cmd.count("-f") == 1
    assert base.absolute().as_posix() in cmd


def test_override_appended_after_base(tmp_path):
    base = _base(tmp_path)
    ov = tmp_path / "docker-compose.override.yml"
    ov.write_text("services: {}\n")
    cmd = DockerComposeWrapper(base).docker_compose_cmd
    assert cmd.count("-f") == 2
    # override must come AFTER the base so Docker's merge lets it win
    assert cmd.index(ov.absolute().as_posix()) > cmd.index(base.absolute().as_posix())


def test_override_naming_matches_workers_file(tmp_path):
    base = _base(tmp_path, "docker-compose.workers.yml")
    ov = tmp_path / "docker-compose.workers.override.yml"
    ov.write_text("services: {}\n")
    cmd = DockerComposeWrapper(base).docker_compose_cmd
    assert ov.absolute().as_posix() in cmd
