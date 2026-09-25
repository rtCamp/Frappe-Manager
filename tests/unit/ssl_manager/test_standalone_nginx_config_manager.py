"""Contract for the standalone (non-bench) nginx vhosts fm writes into the SHARED proxy conf.d.

These blocks are placeholders for a domain that has a certificate but no backend yet. They live
beside docker-gen's default.conf, so the questions that matter are: do they lose to a real backend,
does fm only ever touch its own files, and does a lost block come back.
"""

from pathlib import Path

from frappe_manager.ssl_manager.standalone_nginx_config_manager import (
    FILENAME_PREFIX,
    LEGACY_MARKER,
    STANDALONE_MARKER,
    StandaloneNginxConfigManager,
    reconcile_standalone_configs,
)

DOMAIN = "app.example.com"


def _manager(tmp_path: Path) -> StandaloneNginxConfigManager:
    return StandaloneNginxConfigManager(
        conf_dir=tmp_path / "confd",
        webroot_dir_container="/usr/share/nginx/html",
        certs_dir_container="/etc/nginx/certs",
    )


def test_the_config_filename_sorts_after_default_conf(tmp_path):
    """nginx includes conf.d/*.conf alphabetically and keeps the FIRST block for a duplicate
    server_name. A placeholder must therefore sort after docker-gen's default.conf, or an `api.`
    or `app.` domain shadows the real backend the moment one is connected."""
    manager = _manager(tmp_path)
    written = manager.create_http_config(DOMAIN)

    assert sorted([written.name, "default.conf"]) == ["default.conf", written.name]


def test_a_foreign_conf_of_the_same_name_is_never_claimed_or_deleted(tmp_path):
    """conf.d is shared with docker-gen and with whatever an operator put there. Ownership is
    decided by the marker, not the filename, or removing a domain would delete a stranger's vhost."""
    manager = _manager(tmp_path)
    foreign = manager.legacy_config_path(DOMAIN)
    foreign.write_text("server { server_name app.example.com; }\n")

    assert manager.owns(foreign) is False
    assert manager.config_state(DOMAIN) is None
    assert manager.remove_config(DOMAIN) is False
    assert foreign.exists()


def test_writing_migrates_a_legacy_named_config_instead_of_leaving_two(tmp_path):
    """A pre-1.0 install has the same vhost under `<domain>.conf`. Left behind it is a second
    server block for the same server_name -- and the one nginx keeps."""
    manager = _manager(tmp_path)
    legacy = manager.legacy_config_path(DOMAIN)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(f"{LEGACY_MARKER} {DOMAIN}\nserver {{ server_name {DOMAIN}; }}\n")

    manager.create_http_config(DOMAIN)

    assert not legacy.exists()
    assert manager.config_path(DOMAIN).exists()


def test_managed_configs_finds_an_orphan_no_registry_knows_about(tmp_path):
    """`add` writes the vhost before it registers the domain, so an interrupted run leaves a block
    serving 503 for a real hostname. Scanning by marker is what makes it findable at all."""
    manager = _manager(tmp_path)
    manager.create_http_config(DOMAIN)
    (manager.conf_dir / "default.conf").write_text("# docker-gen\nserver { server_name other; }\n")

    assert manager.managed_configs() == {DOMAIN: manager.config_path(DOMAIN)}


def test_config_state_distinguishes_the_challenge_only_block_from_the_tls_one(tmp_path):
    manager = _manager(tmp_path)

    manager.create_http_config(DOMAIN)
    assert manager.config_state(DOMAIN) == "http"

    manager.create_https_config(DOMAIN)
    assert manager.config_state(DOMAIN) == "https"


def test_remove_deletes_both_the_current_and_the_legacy_file(tmp_path):
    manager = _manager(tmp_path)
    manager.create_http_config(DOMAIN)
    legacy = manager.legacy_config_path(DOMAIN)
    legacy.write_text(f"{STANDALONE_MARKER} {DOMAIN}\n")

    assert manager.remove_config(DOMAIN) is True
    assert not manager.config_path(DOMAIN).exists()
    assert not legacy.exists()


def test_reconcile_rebuilds_a_vhost_that_went_missing(tmp_path):
    """The whole point: nothing else rebuilds these. A conf.d that lost them serves 503 for the
    domain AND blocks its HTTP-01 renewal, because the challenge location lives in that block."""
    manager = _manager(tmp_path)

    changed = reconcile_standalone_configs(manager, {DOMAIN: True})

    assert changed == [DOMAIN]
    assert manager.config_state(DOMAIN) == "https"


def test_reconcile_writes_http_only_when_the_certificate_files_are_absent(tmp_path):
    """An HTTPS block naming absent ssl_certificate files is a FATAL nginx config error, which
    takes down every bench the shared proxy fronts -- not just this domain."""
    manager = _manager(tmp_path)

    reconcile_standalone_configs(manager, {DOMAIN: False})

    assert manager.config_state(DOMAIN) == "http"
    assert "listen 443" not in manager.config_path(DOMAIN).read_text()


def test_reconcile_leaves_a_correct_config_untouched(tmp_path):
    """Runs on every services start, so it must not rewrite files (and invite a reload) for nothing."""
    manager = _manager(tmp_path)
    manager.create_https_config(DOMAIN)
    before = manager.config_path(DOMAIN).stat().st_mtime_ns

    assert reconcile_standalone_configs(manager, {DOMAIN: True}) == []
    assert manager.config_path(DOMAIN).stat().st_mtime_ns == before


def test_reconcile_upgrades_a_challenge_only_block_once_the_certificate_exists(tmp_path):
    """An `add` that issued the certificate but died before writing the HTTPS block leaves the
    domain on plain http forever; the next start or renew should finish the job."""
    manager = _manager(tmp_path)
    manager.create_http_config(DOMAIN)

    assert reconcile_standalone_configs(manager, {DOMAIN: True}) == [DOMAIN]
    assert manager.config_state(DOMAIN) == "https"


def test_reconcile_migrates_a_legacy_named_config_even_when_its_content_is_right(tmp_path):
    """The filename IS the fix for the shadowing bug, so a correct-but-legacy-named block still
    has to move."""
    manager = _manager(tmp_path)
    legacy = manager.legacy_config_path(DOMAIN)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(f"{STANDALONE_MARKER} {DOMAIN}\nlisten 443 ssl;\n")

    assert reconcile_standalone_configs(manager, {DOMAIN: True}) == [DOMAIN]
    assert not legacy.exists()
    assert manager.config_path(DOMAIN).name.startswith(FILENAME_PREFIX)


def test_reconcile_rewrites_a_block_whose_content_is_stale(tmp_path):
    """Comparing only "is there an https block" pins every existing domain to the template it was
    first written with, so a change to the placeholder page (or to the challenge location) would
    never reach a domain already configured -- which is exactly how the old 503 page, naming the
    internal docker network, survived being replaced."""
    manager = _manager(tmp_path)
    manager.create_https_config(DOMAIN)
    stale = manager.config_path(DOMAIN)
    stale.write_text(stale.read_text().replace("503 Service Unavailable", "503 Backend Not Connected"))

    assert reconcile_standalone_configs(manager, {DOMAIN: True}) == [DOMAIN]
    assert "503 Backend Not Connected" not in stale.read_text()


def test_the_placeholder_page_names_nothing_internal(tmp_path):
    """It is served to the public internet for a parked domain. It used to publish the compose
    snippet, the VIRTUAL_HOST/VIRTUAL_PORT wiring and the internal network name, which told any
    scanner the stack, the orchestration and that the hostname was unconfigured."""
    manager = _manager(tmp_path)

    for https in (False, True):
        body = manager.render(DOMAIN, https=https)
        served = [line for line in body.splitlines() if "return 503" in line]
        assert served, "the placeholder must still answer 503"
        for leak in ("VIRTUAL_HOST", "VIRTUAL_PORT", "docker", "compose", "fm-frontend-network", "Frappe"):
            assert leak not in served[0], f"{leak} is served to the public internet"
