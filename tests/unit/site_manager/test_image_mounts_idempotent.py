"""Image-mode mount conversion is a ONE-TIME transform (mount template -> data-only).

A re-pin (deploy/switch/rollback) must change only the image tag and leave the
service's volumes untouched: no duplicated data binds, and any user-added mount
preserved. Regression guard for `_apply_image_mounts` (shared logic with
`render_image_compose`). Reads dedupe via a set, so assertions inspect the raw
compose yaml where duplicates would actually be written.
"""

from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.site_manager.modules.deploy_orchestrator import _apply_image_mounts

COMPOSE = """\
services:
  frappe:
    image: base:1
    volumes:
      - fm-sockets:/fm-sockets
      - ./workspace:/workspace
volumes:
  fm-sockets:
"""


def _raw(cfm, svc):
    return list(cfm.yml["services"][svc]["volumes"])


def _write(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(COMPOSE)
    return p


def test_first_render_converts_workspace_to_data_binds(tmp_path):
    p = _write(tmp_path)
    cfm = ComposeFile(p)

    _apply_image_mounts(cfm, "s.localhost", ["frappe"])

    raw = _raw(cfm, "frappe")
    assert "./workspace:/workspace" not in raw  # wholesale bind dropped
    assert "fm-sockets:/fm-sockets" in raw  # named volume kept
    assert "./workspace/frappe-bench/logs:/workspace/frappe-bench/logs" in raw  # data bind added
    assert len(raw) == len(set(raw))  # no duplicates


def test_repin_is_image_only_and_preserves_user_mount(tmp_path):
    p = _write(tmp_path)
    cfm = ComposeFile(p)

    _apply_image_mounts(cfm, "s.localhost", ["frappe"])  # first: convert

    # user adds a custom mount for their convenience
    vols = cfm.get_service_volumes("frappe")
    vols.append(
        DockerVolumeMount(host="./mydata", container="/mydata", type=DockerVolumeType.bind, compose_path=p)
    )
    cfm.set_service_volumes("frappe", vols)
    before = _raw(cfm, "frappe")

    _apply_image_mounts(cfm, "s.localhost", ["frappe"])  # second (deploy re-pin): must be a no-op on volumes
    after = _raw(cfm, "frappe")

    assert after == before  # volumes untouched
    assert any(v.endswith(":/mydata") for v in after)  # user mount preserved
    assert len(after) == len(set(after))  # no duplicated data binds
