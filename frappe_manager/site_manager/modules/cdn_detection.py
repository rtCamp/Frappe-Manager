"""Advisory-only detection of whether a domain currently resolves into a known CDN's edge range.

The one caller this exists for is an add-time nudge: an operator who did not pass `--behind-proxy`
but whose domain resolves into Cloudflare's published ranges is probably behind Cloudflare anyway,
and a hint at that moment is cheap to give. Nothing here may ever decide behaviour -- the result is
printed or it is not, and either way `fm ssl add` proceeds exactly as the flags it was given say to.

Composed entirely from primitives that already exist:
- `DNSValidator.validate_a_record` (frappe_manager.ssl_manager.dns_validator) does the DNS query and
  already collapses "no record", "timeout" and "dig missing" into a single `valid=False`.
- `CLOUDFLARE_FALLBACK_RANGES` (frappe_manager.site_manager.modules.realip) is the vendored CIDR list
  `fm self real-ip` already trusts and tests as valid; this module makes no network call of its own,
  because it runs on a latency-sensitive interactive path (see `detect_cloudflare_proxy`'s `timeout`).
- stdlib `ipaddress` for range membership.

Two design decisions worth stating rather than leaving implicit:

Three states, not a bool. A bool can only say yes or no, and "DNS timed out" is neither -- collapsing
it into "not proxied" would be actively wrong (a real Cloudflare domain reported as bare origin), and
collapsing it into "proxied" would be worse (a real bare-origin domain nudged to enable a mode it does
not need). `CDNProxyStatus.undetermined` is a first-class outcome precisely so a caller can choose to
say nothing rather than guess.

IPv4 only, on purpose, not by oversight. `CLOUDFLARE_FALLBACK_RANGES` holds both v4 and v6 ranges, and
`_cloudflare_ranges()` below matches an address of either family against it -- the membership check has
always supported both. What is v4-only is resolution: `DNSValidator` exposes `validate_a_record` and
nothing for AAAA, and extending it is out of scope for this module (a new file composing existing
primitives is not license to add a new DNS primitive). A domain that Cloudflare proxies only over IPv6
with no A record at all is therefore reported `undetermined`, not `not_proxied` -- "no A record" already
maps to undetermined for every domain, proxied or not, so this is not a silent gap specific to CDN
detection, it is the existing behaviour of the one query this module is allowed to make.
"""

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network
from typing import TYPE_CHECKING

from frappe_manager.site_manager.modules.realip import CLOUDFLARE_FALLBACK_RANGES
from frappe_manager.ssl_manager.dns_validator import DNSValidator

if TYPE_CHECKING:
    from frappe_manager.output_manager import OutputHandler

# Advisory only: a slow or unresponsive resolver must not make an interactive `fm ssl add` wait
# noticeably. On the success path `dig` typically answers in well under 100ms; this timeout only
# bounds the failure path, so worst-case wall time for this whole module is ~this many seconds.
DEFAULT_TIMEOUT_SECONDS = 3


# `StrEnum`, not `(str, Enum)` like this repo's other enums. Ruff's UP042 flags the `(str, Enum)`
# form, and the repo targets 3.13-only, where `StrEnum` exists and is the equivalent modern spelling.
# The 13 existing `(str, Enum)` classes carry that finding uncorrected inside the accepted baseline;
# that is pre-existing debt, not a convention to match here.
class CDNProxyStatus(StrEnum):
    """What `detect_cloudflare_proxy` was able to establish. Never a decision -- a caller reads
    this to decide whether to print a hint, nothing here decides whether fm does anything."""

    proxied = "proxied"
    not_proxied = "not_proxied"
    undetermined = "undetermined"


class CDNProvider(StrEnum):
    """Only Cloudflare is detectable today: it is the one CDN fm already vendors ranges for (see
    `frappe_manager.site_manager.modules.realip`). A future provider is another member here and
    another range source, not a reshape of `CDNDetectionResult` -- the shape does not pretend to
    cover an edge it cannot see."""

    cloudflare = "cloudflare"


@dataclass(frozen=True)
class CDNDetectionResult:
    """`status` is the only field a caller may branch on. `resolved_ip` and `detail` are for
    a debug line explaining the `status`, not for deciding anything -- `detail` in particular is a
    free-text diagnostic (`ValidationResult.message`, an exception string, ...) and its wording is
    not a contract."""

    status: CDNProxyStatus
    provider: CDNProvider | None = None
    resolved_ip: str | None = None
    detail: str | None = None


def _cloudflare_ranges() -> list[IPv4Network | IPv6Network]:
    """Parse the vendored list defensively: `test_realip_conf.py` already pins every entry as
    valid today, but this module must survive a future typo in that list without raising on an
    operator's terminal, so an unparseable entry is skipped rather than propagated."""
    ranges: list[IPv4Network | IPv6Network] = []
    for entry in CLOUDFLARE_FALLBACK_RANGES:
        try:
            ranges.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return ranges


def detect_cloudflare_proxy(
    domain: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    output: "OutputHandler | None" = None,
) -> CDNDetectionResult:
    """Advisory only: resolve `domain`'s A record and check it against Cloudflare's vendored
    ranges. Every failure mode -- no record, timeout, missing `dig`, a malformed response, a
    malformed vendored entry, or anything unforeseen -- returns `undetermined`. This function
    never raises; it is meant to sit directly on an interactive `fm ssl add` path where a stack
    trace over an advisory nudge would be a worse outcome than no nudge at all.
    """
    try:
        result = DNSValidator(output_handler=output).validate_a_record(domain, timeout=timeout)

        if not result.valid or not result.actual_value:
            return CDNDetectionResult(status=CDNProxyStatus.undetermined, detail=result.message)

        try:
            address = ipaddress.ip_address(result.actual_value)
        except ValueError:
            return CDNDetectionResult(
                status=CDNProxyStatus.undetermined,
                detail=f"'{result.actual_value}' is not a valid IP address",
            )

        for network in _cloudflare_ranges():
            if address in network:
                return CDNDetectionResult(
                    status=CDNProxyStatus.proxied,
                    provider=CDNProvider.cloudflare,
                    resolved_ip=str(address),
                )

        return CDNDetectionResult(status=CDNProxyStatus.not_proxied, resolved_ip=str(address))

    except Exception as e:
        return CDNDetectionResult(status=CDNProxyStatus.undetermined, detail=f"CDN detection error: {e}")
