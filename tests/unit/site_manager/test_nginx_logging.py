"""Structured JSON access-log format for both nginx layers."""

import json
import re
from pathlib import Path

from frappe_manager.site_manager.modules.nginx_logging import FM_JSON_LOG_FORMAT

_TEMPLATES = Path("frappe_manager/templates")
_NGINX_IMAGE_TEMPLATE = Path("Docker/nginx/template.conf")


def _render_sample(fmt: str) -> str:
    """Substitute every nginx variable with a plausible runtime value."""
    samples = {
        "time_iso8601": "2026-07-29T12:00:00+00:00",
        "request_id": "a" * 32,
        "remote_addr": "203.0.113.9",
        "http_x_forwarded_for": "203.0.113.9, 172.64.0.1",
        "host": "mybench.localhost",
        "scheme": "https",
        "request_method": "GET",
        "request_uri": "/api/method/ping?x=1",
        "status": "503",
        "body_bytes_sent": "182",
        "request_time": "0.004",
        # the three fields nginx renders as "-" or comma-lists on retries;
        # they MUST be quoted in the format for the line to stay valid JSON
        "upstream_addr": "-",
        "upstream_status": "-",
        "upstream_response_time": "-",
        "http_referer": "-",
        "http_user_agent": 'curl/8.0 "quoted"',
    }
    rendered = re.sub(r"\$([a-z_0-9]+)", lambda m: samples[m.group(1)], fmt)
    # nginx's escape=json escapes inner quotes at runtime; emulate for the UA.
    return rendered.replace('curl/8.0 "quoted"', 'curl/8.0 \\"quoted\\"')


def test_format_renders_valid_json_even_without_upstream():
    parsed = json.loads(_render_sample(FM_JSON_LOG_FORMAT))
    assert parsed["client"] == "203.0.113.9"
    assert parsed["status"] == 503  # numeric, unquoted
    assert parsed["upstream"] == "-"  # quoted: a bare 503 has no upstream
    assert parsed["request_time"] == 0.004


def test_bench_nginx_image_template_uses_the_format():
    # Bench nginx gets the format from its own image template (rendered to
    # conf.d/default.conf by the entrypoint), NOT from a host-side conf: the
    # format is static, and log_format is only valid at http context.
    text = _NGINX_IMAGE_TEMPLATE.read_text()
    assert f"log_format fm_json escape=json '{FM_JSON_LOG_FORMAT}';" in text, (
        "Docker/nginx/template.conf log_format drifted from FM_JSON_LOG_FORMAT"
    )
    assert "access_log  /var/log/nginx/access.log fm_json;" in text
    # log_format must sit outside the server block (http context).
    assert text.index("log_format fm_json") < text.index("server {")


def test_services_templates_stay_in_sync_with_python_constant():
    # The proxy consumes the SAME format via the LOG_FORMAT env in the
    # services compose templates, with $ escaped as $$ for docker compose
    # interpolation. Drift between the two means the two layers log
    # differently and ingestion breaks silently.
    expected = FM_JSON_LOG_FORMAT.replace("$", "$$")
    for template in ("docker-compose.services.tmpl", "docker-compose.services.osx.tmpl"):
        text = (_TEMPLATES / template).read_text()
        assert f"LOG_FORMAT: '{expected}'" in text, f"{template} LOG_FORMAT drifted from FM_JSON_LOG_FORMAT"
        assert "LOG_FORMAT_ESCAPE: json" in text
