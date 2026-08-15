"""Maintenance must share the per-domain vhost.d file with other writers.

jwilder/nginx-proxy has exactly ONE vhost.d/<domain> file, and fm's upload
limit feature (plus hand-written directives) already lives there. Maintenance
owns a marked block inside it; enable/disable must never destroy the rest.
"""

from importlib import import_module
from unittest.mock import MagicMock, patch

from frappe_manager.commands.maintenance import (
    _bench_domains,
    _has_fm_block,
    _strip_fm_block,
    _vhost_conf,
)
from frappe_manager.output_manager import get_global_output_handler

# frappe_manager.commands re-exports the `maintenance` FUNCTION under the same
# name, shadowing the submodule attribute, so plain `import ... as` binds the
# function. import_module resolves through sys.modules and returns the module.
maintenance_cmd = import_module("frappe_manager.commands.maintenance")

FOREIGN = "client_max_body_size 50m;\n"


def _block() -> str:
    return _vhost_conf("mybench", "a" * 32, "/usr/share/nginx/html", 503, 300, [], [], secure_cookie=False)


def test_block_is_detectable_and_strippable():
    block = _block()
    assert _has_fm_block(block)
    assert _strip_fm_block(block).strip() == ""


def test_enable_over_foreign_content_preserves_it():
    # what enable writes when the file already holds an upload limit
    existing = FOREIGN
    merged = _block() + _strip_fm_block(existing).strip("\n") + "\n"
    assert "client_max_body_size 50m;" in merged
    assert _has_fm_block(merged)
    # disable removes only the block, leaving the foreign directive
    remainder = _strip_fm_block(merged).strip("\n")
    assert remainder == "client_max_body_size 50m;"
    assert not _has_fm_block(remainder)


def test_reenable_replaces_block_without_duplicating_foreign_lines():
    merged = _block() + FOREIGN
    remerged = _block() + _strip_fm_block(merged).strip("\n") + "\n"
    assert remerged.count("client_max_body_size") == 1
    assert remerged.count("# fm:maintenance BEGIN") == 1


def _write_bench_config(root, benchname: str, body: str = "") -> None:
    bench_dir = root / benchname
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench_config.toml").write_text(f'name = "{benchname}"\n{body}')


def test_tls_is_read_from_the_per_certificate_ssl_type(tmp_path, monkeypatch):
    # Regression: the probe used to read a top-level `ssl.ssl_type`, a key
    # export_to_toml never writes, so every bench looked like plain http and the
    # bypass cookie never got its Secure flag. ssl_type lives one level deeper.
    monkeypatch.setattr(maintenance_cmd, "CLI_BENCHES_DIRECTORY", tmp_path)
    _write_bench_config(
        tmp_path,
        "mybench.localhost",
        '\n[[ssl.certificates]]\ndomain = "mybench.localhost"\nssl_type = "letsencrypt"\n',
    )

    domains, domain_ssl = _bench_domains("mybench.localhost")

    assert domains == ["mybench.localhost"]
    assert domain_ssl == {"mybench.localhost": True}


def test_tls_state_is_tracked_per_domain_not_per_bench(tmp_path, monkeypatch):
    # A certificate covers one domain, so an alias without one must stay http:
    # handing it a Secure-only cookie would make its bypass link silently fail.
    monkeypatch.setattr(maintenance_cmd, "CLI_BENCHES_DIRECTORY", tmp_path)
    _write_bench_config(
        tmp_path,
        "mybench.localhost",
        'alias_domains = ["secure.example.com", "plain.example.com"]\n'
        '\n[[ssl.certificates]]\ndomain = "secure.example.com"\nssl_type = "letsencrypt"\n',
    )

    domains, domain_ssl = _bench_domains("mybench.localhost")

    assert domains == ["mybench.localhost", "secure.example.com", "plain.example.com"]
    assert domain_ssl == {
        "mybench.localhost": False,
        "secure.example.com": True,
        "plain.example.com": False,
    }


def test_a_disabled_certificate_entry_is_not_tls(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance_cmd, "CLI_BENCHES_DIRECTORY", tmp_path)
    _write_bench_config(
        tmp_path,
        "mybench.localhost",
        '\n[[ssl.certificates]]\ndomain = "mybench.localhost"\nssl_type = "none"\n',
    )

    _, domain_ssl = _bench_domains("mybench.localhost")

    assert domain_ssl == {"mybench.localhost": False}


def test_no_ssl_table_at_all_is_not_tls(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance_cmd, "CLI_BENCHES_DIRECTORY", tmp_path)
    _write_bench_config(tmp_path, "mybench.localhost", "admin_tools = true\n")

    _, domain_ssl = _bench_domains("mybench.localhost")

    assert domain_ssl == {"mybench.localhost": False}


def test_secure_cookie_follows_the_domain():
    # The per-domain flag has to reach the rendered block, which is what actually
    # sets Secure on the bypass cookie.
    args = ("mybench", "a" * 32, "/usr/share/nginx/html", 503, 300, [], [])
    assert "; Secure" in _vhost_conf(*args, secure_cookie=True)
    assert "; Secure" not in _vhost_conf(*args, secure_cookie=False)


# --------------------------------------------------------------------------- #
# Domains that left the bench
#
# `fm update B --remove-alias x` drops the domain from bench_config.toml but leaves
# vhost.d/x on disk. Both maintenance paths iterate the CURRENT config, so `--off` used to
# walk straight past that file: the block stayed live, --status kept listing it, and the
# next bench to claim the domain inherited the maintenance page and the old bypass token.
# --------------------------------------------------------------------------- #


def _maint_env(tmp_path):
    vhostd = tmp_path / "vhost.d"
    vhostd.mkdir(parents=True)
    benches = tmp_path / "benches"
    services = MagicMock()
    services.proxy_storage.dirs.vhostd.host = str(vhostd)
    services.proxy_storage.dirs.html.host = str(tmp_path / "html")
    services.proxy_storage.dirs.html.container = "/usr/share/nginx/html"
    return services, vhostd, benches


def _enabled_block(bench: str = "mybench") -> str:
    return _vhost_conf(bench, "a" * 32, "/usr/share/nginx/html", 503, 300, [], [], secure_cookie=False)


def _run_off(services, benches):
    """`fm maintenance mybench --off` with only the proxy and the benches dir faked."""
    ctx = MagicMock()
    ctx.obj = {"services": services}
    handler = get_global_output_handler()
    with (
        patch.object(maintenance_cmd, "CLI_BENCHES_DIRECTORY", benches),
        patch.object(maintenance_cmd, "check_bench_migration_required"),
        patch.object(handler, "print") as printed,
    ):
        maintenance_cmd.maintenance(
            ctx,
            benchname="mybench",
            off=True,
            status=False,
            response_code=503,
            retry_after=300,
            allow_ip=[],
            allow_path=[],
            message=None,
            page=None,
            rotate_token=False,
        )
    return "\n".join(call.args[0] for call in printed.call_args_list if call.args)


def test_off_clears_the_vhost_of_an_alias_that_left_the_bench(tmp_path):
    services, vhostd, benches = _maint_env(tmp_path)
    _write_bench_config(benches, "mybench", 'alias_domains = ["alias.example.com"]\n')
    for domain in ("mybench", "alias.example.com"):
        (vhostd / domain).write_text(_enabled_block())
    # ... and then `fm update mybench --remove-alias alias.example.com` happened.
    _write_bench_config(benches, "mybench")

    reported = _run_off(services, benches)

    assert not (vhostd / "alias.example.com").exists()
    assert not (vhostd / "mybench").exists()
    services.nginx_controller.reload.assert_called_once_with()
    assert "alias.example.com" in reported


def test_an_orphaned_vhost_is_the_only_thing_left_and_is_still_a_real_disable(tmp_path):
    """No current domain is in maintenance, so the loop over the config finds nothing: the
    command must still clean the orphan and reload, not report "was not enabled"."""
    services, vhostd, benches = _maint_env(tmp_path)
    _write_bench_config(benches, "mybench")
    (vhostd / "alias.example.com").write_text(_enabled_block())

    reported = _run_off(services, benches)

    assert not (vhostd / "alias.example.com").exists()
    services.nginx_controller.reload.assert_called_once_with()
    assert "Maintenance was not enabled" not in reported


def test_an_orphaned_vhost_keeps_the_directives_maintenance_does_not_own(tmp_path):
    services, vhostd, benches = _maint_env(tmp_path)
    _write_bench_config(benches, "mybench")
    (vhostd / "alias.example.com").write_text(_enabled_block() + FOREIGN)

    _run_off(services, benches)

    assert (vhostd / "alias.example.com").read_text() == FOREIGN


def test_off_never_touches_a_domain_another_bench_put_into_maintenance(tmp_path):
    """The sweep is scoped by the bench name the block itself records; a foreign domain in
    maintenance is not this bench's leftover."""
    services, vhostd, benches = _maint_env(tmp_path)
    _write_bench_config(benches, "mybench")
    (vhostd / "mybench").write_text(_enabled_block())
    (vhostd / "other.example.com").write_text(_enabled_block("otherbench"))

    _run_off(services, benches)

    assert _has_fm_block((vhostd / "other.example.com").read_text())
