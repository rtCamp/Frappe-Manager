"""HTTP basic auth conf rendering (web surface + admin tools) and the [auth] round trip."""

import re

import pytest
from passlib.apache import HtpasswdFile
from pydantic import ValidationError

from frappe_manager.site_manager.bench_config import (
    AuthConfig,
    BenchConfig,
    BenchRuntime,
    FMBenchEnvType,
)
from frappe_manager.site_manager.modules.auth import (
    REALM,
    build_auth_map_conf,
    build_server_auth_conf,
    build_tools_auth_block,
    container_htpasswd_path,
    is_fm_auth_conf,
    validate_credentials,
    write_htpasswd,
)

_AUTH_FILE = container_htpasswd_path("mybench.localhost")


def test_server_conf_is_marked_and_gates_with_the_literal_realm():
    conf = build_server_auth_conf(_AUTH_FILE)
    # The marker is what lets ensure_fm_nginx_confs overwrite/remove the file; a
    # conf that fails is_fm_auth_conf would be treated as hand-written and left.
    assert is_fm_auth_conf(conf)
    assert f'auth_basic "{REALM}";' in conf
    assert f"auth_basic_user_file {_AUTH_FILE};" in conf


def test_server_conf_allow_ips_keeps_the_password_for_everyone_else():
    conf = build_server_auth_conf(_AUTH_FILE, allow_ips=["10.1.0.0/16", "203.0.113.9"])
    assert "satisfy any;" in conf
    assert "allow 10.1.0.0/16;" in conf
    assert "allow 203.0.113.9;" in conf
    # Without the closing `deny all` every address satisfies the access module and
    # nginx never asks for the password at all: the allow list would silently turn
    # auth off instead of exempting two addresses.
    assert "deny all;" in conf
    assert conf.index("deny all;") > conf.index("allow 203.0.113.9;")


def test_server_conf_defers_to_the_map_variable_when_paths_are_exempt():
    conf = build_server_auth_conf(_AUTH_FILE, allow_paths=["/api/method/ping"])
    # A quoted realm is a literal, so the map would never be consulted and the
    # exempt path would still prompt.
    assert "auth_basic $fm_auth_realm;" in conf
    assert '"$fm_auth_realm"' not in conf
    assert REALM not in conf


def test_map_conf_defaults_to_the_realm_and_switches_exempt_paths_off():
    conf = build_auth_map_conf(["/api/method/ping"])
    assert is_fm_auth_conf(conf)
    assert "map $uri $fm_auth_realm {" in conf
    assert f'default "{REALM}";' in conf
    assert "~^/api/method/ping off;" in conf


def test_map_conf_escapes_regex_metacharacters_in_paths():
    conf = build_auth_map_conf(["/api/method/a.b"])
    entry = next(line.strip() for line in conf.splitlines() if line.strip().startswith("~^"))
    assert entry == r"~^/api/method/a\.b off;"

    # An unescaped dot is any character, so the exemption would leak to sibling
    # endpoints that merely look alike.
    pattern = entry.removesuffix(" off;").removeprefix("~")
    assert re.match(pattern, "/api/method/a.b")
    assert re.match(pattern, "/api/method/axb") is None


def test_ips_and_paths_together_leave_no_access_module_denial_to_defeat_the_exemption():
    # D23: `satisfy any; allow <ip>; deny all;` records a 403 in the access phase,
    # which runs BEFORE auth_basic; a realm of `off` makes the auth_basic handler
    # DECLINE rather than return OK, so nothing clears the recorded refusal and the
    # allow-listed PATH answered 403 to every client outside the IP allow list --
    # exactly the command's own `--allow-ip ... --allow-path ...` example.
    conf = build_server_auth_conf(_AUTH_FILE, allow_ips=["203.0.113.0/24"], allow_paths=["/api/method/ping"])
    assert "auth_basic $fm_auth_realm;" in conf
    assert "deny all;" not in conf
    assert "satisfy any;" not in conf
    assert "allow 203.0.113.0/24;" not in conf


def test_ips_and_paths_together_exempt_both_through_the_realm_map():
    # The IP exemption dropped from the server conf above has to reappear here, or
    # --allow-ip would silently stop working; `geo` is the only directive that
    # matches a CIDR into a variable, and it is http-context only.
    conf = build_auth_map_conf(["/api/method/ping"], ["203.0.113.0/24"])
    assert is_fm_auth_conf(conf)
    assert "geo $fm_auth_ip_exempt {" in conf
    assert "    default 0;" in conf
    assert "    203.0.113.0/24 1;" in conf
    assert "map $uri $fm_auth_path_exempt {" in conf
    assert "    ~^/api/method/ping 1;" in conf
    # Either flag set is enough, and an unmatched request keeps the realm.
    assert 'map "$fm_auth_ip_exempt$fm_auth_path_exempt" $fm_auth_realm {' in conf
    assert f'    default "{REALM}";' in conf
    assert '    "~1" off;' in conf


def test_tools_only_locations_carry_the_gate_themselves():
    block = build_tools_auth_block(web=False, tools=True, auth_file=_AUTH_FILE)
    assert f'    auth_basic "{REALM}";' in block
    assert f"    auth_basic_user_file {_AUTH_FILE};" in block


def test_tools_only_honours_allow_ips():
    block = build_tools_auth_block(web=False, tools=True, auth_file=_AUTH_FILE, allow_ips=["10.1.0.0/16"])
    assert "    satisfy any;" in block
    assert "    allow 10.1.0.0/16;" in block
    assert "    deny all;" in block


def test_web_only_opts_the_tools_locations_out_of_the_inherited_gate():
    # The server-level gate is inherited by every location, so tools=False is only
    # honoured by an explicit opt-out. Updated for D30: the equality below used to
    # pin `auth_basic off;` alone, which opts out of only half the inherited gate --
    # with --allow-ip the inherited `satisfy any; allow <ip>; deny all;` still 403'd
    # /adminer/ and /mailpit/ for everyone outside the allow list, because the access
    # phase runs first and `auth_basic off` DECLINEs instead of clearing the refusal.
    assert (
        build_tools_auth_block(web=True, tools=False, auth_file=_AUTH_FILE) == "    auth_basic off;\n    allow all;\n"
    )


def test_web_only_replaces_the_inherited_ip_allow_list_instead_of_inheriting_it():
    block = build_tools_auth_block(web=True, tools=False, auth_file=_AUTH_FILE, allow_ips=["203.0.113.0/24"])
    # A location-level allow list replaces the inherited one and returns OK, which is
    # what clears the recorded denial under the inherited `satisfy any`.
    assert "    allow all;" in block
    assert "    auth_basic off;" in block
    assert "deny" not in block


def test_both_surfaces_on_still_names_the_benchs_file_rather_than_inheriting():
    # This used to emit nothing, on the grounds that re-declaring the gate was redundant. It was,
    # while one credential pair served both surfaces. Per-site web auth ended that: inheriting the
    # server gate would let a site's own password open the bench-wide Adminer, which reaches every
    # schema on the bench. A location-level `auth_basic` overrides the server-level one.
    block = build_tools_auth_block(web=True, tools=True, auth_file=_AUTH_FILE)
    assert f"auth_basic_user_file {_AUTH_FILE};" in block
    assert "auth_basic off" not in block


def test_both_surfaces_off_emits_nothing():
    # Anything non-empty here lands in the rendered location and either gates or
    # un-gates a bench that asked for neither.
    assert build_tools_auth_block(web=False, tools=False, auth_file=_AUTH_FILE) == ""


def test_validate_credentials_accepts_a_normal_pair():
    assert validate_credentials("admin", "s3cret") is None


def test_validate_credentials_allows_colon_in_the_password():
    # Only the first colon of an `Authorization` pair separates the fields, so a
    # colon inside the password is legal; rejecting it would break generated
    # passwords for no reason.
    assert validate_credentials("admin", "pa:ss") is None


@pytest.mark.parametrize(
    ("user", "password", "field"),
    [
        ("", "s3cret", "username"),
        ("ad:min", "s3cret", "username"),
        ("ad min", "s3cret", "username"),
        ("ad\tmin", "s3cret", "username"),
        ("admin", "", "password"),
        ("admin", "   ", "password"),
    ],
)
def test_validate_credentials_rejects_unusable_input_naming_the_field(user, password, field):
    # A colon or whitespace in the username corrupts the htpasswd line / the
    # Authorization header, locking the bench out with no error at request time.
    with pytest.raises(ValueError, match=field):
        validate_credentials(user, password)


def test_write_htpasswd_creates_a_verifiable_file(tmp_path):
    path = tmp_path / "http_auth" / "mybench.localhost.htpasswd"
    assert write_htpasswd(path, "admin", "s3cret") is True
    assert HtpasswdFile(str(path)).check_password("admin", "s3cret")


def test_write_htpasswd_is_idempotent_for_unchanged_credentials(tmp_path):
    path = tmp_path / "mybench.htpasswd"
    write_htpasswd(path, "admin", "s3cret")
    # The hash is salted, so a byte comparison would differ every run and report a
    # change, reloading nginx on every `fm` invocation.
    assert write_htpasswd(path, "admin", "s3cret") is False


def test_write_htpasswd_reports_a_password_change(tmp_path):
    path = tmp_path / "mybench.htpasswd"
    write_htpasswd(path, "admin", "s3cret")
    assert write_htpasswd(path, "admin", "rotated") is True
    assert HtpasswdFile(str(path)).check_password("admin", "rotated")


def test_write_htpasswd_drops_the_previous_username(tmp_path):
    path = tmp_path / "mybench.htpasswd"
    write_htpasswd(path, "admin", "s3cret")
    assert write_htpasswd(path, "ops", "s3cret") is True

    ht = HtpasswdFile(str(path))
    # Appending instead of truncating would leave the renamed-away user able to
    # log in forever.
    assert ht.users() == ["ops"]
    assert ht.check_password("admin", "s3cret") is None


def _bench(path, auth=None):
    return BenchConfig(
        name="mybench.localhost",
        developer_mode=False,
        admin_tools=True,
        environment_type=FMBenchEnvType.prod,
        root_path=path,
        runtime=BenchRuntime.mount,
        auth=auth,
    )


def test_auth_defaults_to_none_and_writes_no_table(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _bench(path)
    assert bc.auth is None
    bc.export_to_toml(path)
    assert "[auth]" not in path.read_text()


def test_auth_survives_the_toml_round_trip(tmp_path):
    path = tmp_path / "bench_config.toml"
    bc = _bench(
        path,
        AuthConfig(
            user="ops",
            password="s3cret",
            web=True,
            tools=False,
            allow_ips=["10.1.0.0/16"],
            allow_paths=["/api/method/ping"],
        ),
    )
    bc.export_to_toml(path)
    assert "[auth]" in path.read_text()

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.auth is not None
    assert reloaded.auth.user == "ops"
    assert reloaded.auth.password == "s3cret"
    assert reloaded.auth.web is True
    assert reloaded.auth.tools is False
    assert reloaded.auth.allow_ips == ["10.1.0.0/16"]
    assert reloaded.auth.allow_paths == ["/api/method/ping"]


def test_unset_password_is_absent_rather_than_empty(tmp_path):
    path = tmp_path / "bench_config.toml"
    _bench(path, AuthConfig(web=True)).export_to_toml(path)
    # An empty string would round-trip as a real (blank) password and be written
    # into the htpasswd file instead of triggering generation on first enable.
    assert 'password = ""' not in path.read_text()

    reloaded = BenchConfig.import_from_toml(path)
    assert reloaded.auth is not None
    assert reloaded.auth.password is None
    assert reloaded.auth.user == "admin"


def test_auth_rejects_unknown_keys():
    # extra="forbid" turns a typo in bench_config.toml into a loud error instead of
    # a silently ignored setting the user believes is applied.
    with pytest.raises(ValidationError):
        AuthConfig(allow_ip=["10.1.0.0/16"])
