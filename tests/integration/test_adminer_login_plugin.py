"""The Adminer login plugin, executed rather than inspected.

`000-fm-login.php` is the only executable artefact fm ships, and until now every test only asserted
that it lands on disk with the right bytes. What it DOES was untested, which mattered more
once a bench could serve several sites: the plugin builds one login card per site by globbing the
mounted sites directory, and it reads each site's own DB endpoint, so multisite correctness lives
entirely inside it.

Run under a throwaway `php:8-cli-alpine` container, because there is no PHP on a developer machine and none
in fm's own image. The Adminer classes the plugin requires are stubbed, so this tests fm's code and
not Adminer's: the assertion is the `$servers` / credentials / card map the constructor computes
from a fixture sites directory, which is exactly the data the rendered buttons carry.

Skips without docker, like every other test in this directory.
"""

import json
import shutil
import subprocess

import pytest

from frappe_manager.utils.helpers import get_template_path

pytestmark = [pytest.mark.integration, pytest.mark.timeout(240)]

PHP_IMAGE = "php:8-cli-alpine"

# Stubs for what the plugin `require_once`s out of the Adminer image. Only the base class shape
# matters: the plugin deliberately does NOT call parent::__construct().
STUB_LOGIN_SERVERS = "<?php\nclass AdminerLoginServers {\n    protected $servers = array();\n}\n"
STUB_REDIS_DRIVER = "<?php\n// driver stub\n"

# Reaches into the object the plugin returns and prints the three maps it built, which is what the
# cards are rendered from.
HARNESS = """<?php
$_POST = array();
set_include_path('/app');
$plugin = require '/app/000-fm-login.php';
$read = function ($obj, $prop) {
    $r = new ReflectionObject($obj);
    $p = $r->getProperty($prop);
    return $p->getValue($obj);
};
echo json_encode(array(
    'servers' => $read($plugin, 'servers'),
    'creds' => $read($plugin, 'fmCreds'),
    'meta' => $read($plugin, 'fmMeta'),
));
"""


def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(["docker", "info"], capture_output=True, timeout=60, check=False)  # noqa: S607
    return result.returncode == 0


requires_docker = pytest.mark.skipif(not _docker(), reason="needs a docker daemon to run php")


def _run(tmp_path, sites: dict[str, dict | str], common: dict | None = None) -> dict:
    """Build a fixture sites directory, mount it at /fm-sites, and run the plugin."""
    app = tmp_path / "app"
    (app / "plugins" / "drivers").mkdir(parents=True)
    (app / "plugins" / "login-servers.php").write_text(STUB_LOGIN_SERVERS)
    (app / "plugins" / "drivers" / "redis.php").write_text(STUB_REDIS_DRIVER)
    (app / "000-fm-login.php").write_bytes(get_template_path("adminer/000-fm-login.php").read_bytes())
    (app / "harness.php").write_text(HARNESS)

    fm_sites = tmp_path / "sites"
    fm_sites.mkdir(exist_ok=True)
    (fm_sites / "common_site_config.json").write_text(json.dumps(common or {}))
    for site, cfg in sites.items():
        (fm_sites / site).mkdir(exist_ok=True)
        # A raw string goes in verbatim, so a test can hand the plugin a file it cannot parse.
        body = cfg if isinstance(cfg, str) else json.dumps(cfg)
        (fm_sites / site / "site_config.json").write_text(body)
    # Not a site: no site_config.json, so the glob must pass it over.
    (fm_sites / "assets").mkdir()

    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{app}:/app:ro",
            "-v", f"{fm_sites}:/fm-sites:ro",
            # display_errors on so a notice becomes a visible failure here rather than something
            # only a production log would ever show.
            "-w", "/app", PHP_IMAGE, "php", "-d", "display_errors=1", "-d", "error_reporting=E_ALL",
            "harness.php",
        ],
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # PHP writes diagnostics to STDOUT, which for Adminer means into the response body ahead of its
    # own output, so the plugin has to be silent on a plain page load. It was not: reading
    # `$_POST["auth"]` on a GET warned every time. Anything before the JSON is such a diagnostic.
    assert proc.stdout.startswith("{"), proc.stdout[:400]
    return json.loads(proc.stdout)


@requires_docker
class TestMultisite:
    def test_every_site_gets_its_own_card(self, tmp_path):
        out = _run(
            tmp_path,
            {
                "shop.localhost": {"db_name": "db_shop", "db_password": "pw_shop"},
                "b.example.com": {"db_name": "db_b", "db_password": "pw_b"},
            },
        )

        assert set(out["meta"]) >= {"shop.localhost", "b.example.com"}
        assert out["meta"]["b.example.com"]["title"] == "b.example.com"

    def test_each_card_carries_that_sites_own_credentials(self, tmp_path):
        """One card holding the primary's password would open the wrong database from the other
        site's button, which is the same defect the info card had."""
        out = _run(
            tmp_path,
            {
                "shop.localhost": {"db_name": "db_shop", "db_password": "pw_shop"},
                "b.example.com": {"db_name": "db_b", "db_password": "pw_b"},
            },
        )

        assert out["creds"]["shop.localhost"] == ["db_shop", "pw_shop"]
        assert out["creds"]["b.example.com"] == ["db_b", "pw_b"]

    def test_a_directory_that_is_not_a_site_is_passed_over(self, tmp_path):
        """`sites/` also holds `assets/`, and a card for it would fail to connect to a database
        that does not exist."""
        out = _run(tmp_path, {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}})

        assert "assets" not in out["servers"]

    def test_a_site_on_an_external_server_points_at_that_server(self, tmp_path):
        out = _run(
            tmp_path,
            {
                "shop.localhost": {"db_name": "db_shop", "db_password": "pw", "db_host": "global-db"},
                "b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "rds.internal"},
            },
        )

        assert out["servers"]["shop.localhost"]["server"] == "global-db"
        assert out["servers"]["b.example.com"]["server"] == "rds.internal"


@requires_docker
class TestEndpointPort:
    def test_a_non_default_port_reaches_the_card(self, tmp_path):
        """fm writes `db_port` per site and honours it everywhere else. The card dropped it, so a
        site on an external server at 3307 had its button aimed at 3306: a refused connection, or
        the wrong instance answering."""
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "rds.internal", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "rds.internal:3307"
        assert "3307" in out["meta"]["b.example.com"]["sub"]

    def test_the_default_port_is_left_off_so_shared_cards_are_unchanged(self, tmp_path):
        """Every fm-written site file carries `db_port = 3306` for the shared container, so
        appending unconditionally would rewrite every existing bench's card for no gain."""
        out = _run(
            tmp_path,
            {"shop.localhost": {"db_name": "db_shop", "db_password": "pw", "db_host": "global-db", "db_port": 3306}},
        )

        assert out["servers"]["shop.localhost"]["server"] == "global-db"
        assert out["meta"]["shop.localhost"]["sub"] == "MariaDB · site database"

    def test_a_host_that_already_carries_a_port_is_not_given_a_second(self, tmp_path):
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "rds.internal:3307", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "rds.internal:3307"


@requires_docker
class TestFallbacks:
    def test_a_site_file_with_no_host_falls_back_to_the_shared_container(self, tmp_path):
        """The common file no longer carries `db_host` at all -- fm stopped writing it there when the
        endpoint became per-site -- so the literal default is the last resort and has to work."""
        out = _run(tmp_path, {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}})

        assert out["servers"]["shop.localhost"]["server"] == "global-db"

    def test_a_legacy_common_host_is_still_honoured(self, tmp_path):
        """A bench whose common file predates that change keeps working."""
        out = _run(
            tmp_path,
            {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}},
            common={"db_host": "legacy-db", "db_port": 3307},
        )

        assert out["servers"]["shop.localhost"]["server"] == "legacy-db:3307"

    def test_redis_cards_come_from_the_common_file(self, tmp_path):
        """Redis IS bench-wide: the workers run with no --site, so it has to be in common."""
        out = _run(
            tmp_path,
            {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}},
            common={"redis_cache": "redis://redis-cache:6379", "redis_queue": "redis://redis-queue:6379"},
        )

        assert out["servers"]["redis-cache"] == {"server": "redis-cache:6379", "driver": "redis"}
        assert out["servers"]["redis-queue"]["driver"] == "redis"

    def test_no_redis_in_common_means_no_redis_cards(self, tmp_path):
        out = _run(tmp_path, {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}})

        assert "redis-cache" not in out["servers"]

    def test_an_unparseable_site_file_does_not_take_the_whole_page_down(self, tmp_path):
        """The plugin builds every card in one constructor, so one bad file could have cost the
        operator access to all the others. It still gets a card, on the fallback endpoint, and the
        healthy sites are untouched."""
        out = _run(
            tmp_path,
            {
                "shop.localhost": {"db_name": "db_shop", "db_password": "pw", "db_host": "global-db"},
                "broken.localhost": "{not json",
            },
        )

        assert out["servers"]["shop.localhost"]["server"] == "global-db"
        assert out["creds"]["shop.localhost"] == ["db_shop", "pw"]
        assert out["creds"]["broken.localhost"] == ["", ""]


@requires_docker
class TestIPv6Endpoint:
    def test_a_plain_hostname_still_gets_its_port_appended(self, tmp_path):
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "rds.internal", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "rds.internal:3307"

    def test_a_bare_ipv6_host_is_bracketed_before_the_port(self, tmp_path):
        """The old `strpos($host, ':')` guard read an IPv6 literal's own colons as "already has a
        port" and silently dropped db_port, dialing 3306. Adminer's host_port() regex only accepts
        a port after `[ipv6]`, so bare `2001:db8::1:3307` would be one long hostname -- brackets
        are mandatory."""
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "2001:db8::1", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "[2001:db8::1]:3307"

    def test_an_already_bracketed_ipv6_host_gets_the_port_after_the_bracket(self, tmp_path):
        """An operator who pre-bracketed the literal should not end up with double brackets or a
        dropped port; `[...]:3307` is exactly what Adminer's regex parses."""
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "[2001:db8::1]", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "[2001:db8::1]:3307"

    def test_a_host_already_carrying_a_parseable_port_is_left_alone(self, tmp_path):
        """Appending again would make `[...]:3307:3307`, which matches neither branch of Adminer's
        `^(\\[(.+)]|([^:]+)):([^:]+)$` and so degrades to a nonsense hostname."""
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "[2001:db8::1]:3307", "db_port": 3307}},
        )

        assert out["servers"]["b.example.com"]["server"] == "[2001:db8::1]:3307"

    def test_a_bare_ipv6_on_the_default_port_is_passed_through_untouched(self, tmp_path):
        """No port appended means no brackets either: a lone `[2001:db8::1]` also fails Adminer's
        regex and would be treated as a literal hostname, so the safe form is the bare address."""
        out = _run(
            tmp_path,
            {"b.example.com": {"db_name": "db_b", "db_password": "pw", "db_host": "2001:db8::1", "db_port": 3306}},
        )

        assert out["servers"]["b.example.com"]["server"] == "2001:db8::1"
        assert out["meta"]["b.example.com"]["sub"] == "MariaDB · site database"


@requires_docker
class TestTlsAndSocket:
    def test_a_tls_pinned_site_keeps_its_card_and_is_labelled(self, tmp_path):
        """fm writes db_ssl_ca into the site file, but the CA sits under config/tls/, outside the
        sole sites -> /fm-sites mount, and this plugin has no connectSsl() override -- so the click
        either fails against an enforcing server or silently drops the pin. The card must say so
        rather than downgrade quietly, and everything else about it must stay as it was."""
        out = _run(
            tmp_path,
            {
                "shop.localhost": {
                    "db_name": "db_shop",
                    "db_password": "pw",
                    "db_host": "rds.internal",
                    "db_port": 3307,
                    "db_ssl_ca": "/workspace/frappe-bench/config/tls/shop.localhost/db-ca.pem",
                }
            },
        )

        assert out["servers"]["shop.localhost"]["server"] == "rds.internal:3307"
        assert out["meta"]["shop.localhost"]["sub"].endswith("· TLS not applied by Adminer")

    def test_a_site_without_a_pin_carries_no_tls_marker(self, tmp_path):
        """The label only means something if it singles out pinned sites; stamped on every card it
        would be noise nobody reads."""
        out = _run(tmp_path, {"shop.localhost": {"db_name": "db_shop", "db_password": "pw"}})

        assert "TLS" not in out["meta"]["shop.localhost"]["sub"]

    def test_a_socket_site_with_an_explicit_host_keeps_its_tcp_card(self, tmp_path):
        """db_socket overrides db_host for Frappe itself, and Adminer cannot reach a unix socket in
        another container -- but an operator who also named a TCP endpoint plausibly pointed it at
        the same server, and that path works, so taking the card away would cost them access."""
        out = _run(
            tmp_path,
            {
                "shop.localhost": {
                    "db_name": "db_shop",
                    "db_password": "pw",
                    "db_socket": "/var/run/mysqld/mysqld.sock",
                    "db_host": "rds.internal",
                }
            },
        )

        assert out["servers"]["shop.localhost"]["server"] == "rds.internal"

    def test_a_socket_site_without_a_host_gets_no_card_at_all(self, tmp_path):
        """Without db_host the plugin would fall back to the literal global-db -- a different,
        real, writable database than the one the site actually uses through its socket. A button
        that opens the wrong database is worse than no button, and one skipped site must not take
        a healthy sibling's card down with it."""
        out = _run(
            tmp_path,
            {
                "socket.localhost": {"db_name": "db_sock", "db_password": "pw", "db_socket": "/run/mysqld.sock"},
                "shop.localhost": {"db_name": "db_shop", "db_password": "pw", "db_host": "global-db"},
            },
        )

        assert "socket.localhost" not in out["servers"]
        assert "socket.localhost" not in out["creds"]
        assert "socket.localhost" not in out["meta"]
        assert out["servers"]["shop.localhost"]["server"] == "global-db"
