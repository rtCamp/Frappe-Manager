"""
DNS validation utilities for SSL certificate generation.

Validates DNS configuration before attempting ACME challenges to provide
early feedback and better error messages to users.
"""

import subprocess
import time
from dataclasses import dataclass

from frappe_manager.output_manager import OutputHandler


@dataclass
class ValidationResult:
    """Result of a DNS validation check."""

    valid: bool
    actual_value: str | None
    expected_value: str | None
    message: str
    dns_query_output: str  # Raw dig output for debugging


@dataclass
class PropagationStatus:
    """DNS propagation status across multiple checks."""

    propagated: bool
    checks_passed: int
    checks_total: int
    message: str


class DNSValidator:
    """
    Validates DNS configuration for SSL certificate generation.

    Provides pre-flight checks for:
    - CNAME records (for DNS-01 challenges with delegation)
    - A records (for HTTP-01 challenges)
    - DNS propagation status
    """

    def __init__(self, output_handler: OutputHandler | None = None):
        """
        Initialize DNS validator.

        Args:
            output_handler: Optional output handler for user messages
        """
        self.output = output_handler

    def validate_cname_for_acme(self, domain: str, challenge_alias: str, timeout: int = 10) -> ValidationResult:
        """
        Validate CNAME for ACME DNS-01 challenge with challenge-alias.

        When using acme.sh with --challenge-alias, the expected setup is:
          _acme-challenge.<domain>  →  _acme-challenge.<challenge_alias>

        Example:
          domain = "gg.alok.rtmake.com"
          challenge_alias = "alok.rt.gw"

          Expected CNAME:
            _acme-challenge.gg.alok.rtmake.com  →  _acme-challenge.alok.rt.gw

        Args:
            domain: The domain to issue certificate for
            challenge_alias: The challenge alias domain (base domain without _acme-challenge prefix)
            timeout: DNS query timeout in seconds

        Returns:
            ValidationResult with status and details
        """
        challenge_domain = f"_acme-challenge.{domain}"
        expected_target = f"_acme-challenge.{challenge_alias}."  # Note trailing dot for FQDN

        if self.output:
            self.output.debug(f"Validating CNAME: {challenge_domain} → {expected_target}")

        try:
            # Query DNS using dig
            result = subprocess.run(
                ["dig", "+short", challenge_domain, "CNAME"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            actual_value = result.stdout.strip()
            dns_output = result.stdout + (result.stderr if result.stderr else "")

            if not actual_value:
                return ValidationResult(
                    valid=False,
                    actual_value=None,
                    expected_value=expected_target,
                    message=f"CNAME record not found for {challenge_domain}",
                    dns_query_output=dns_output,
                )

            # Normalize (add trailing dot if missing for comparison)
            normalized_actual = actual_value if actual_value.endswith(".") else f"{actual_value}."

            if normalized_actual == expected_target:
                return ValidationResult(
                    valid=True,
                    actual_value=actual_value,
                    expected_value=expected_target,
                    message="CNAME record is correctly configured",
                    dns_query_output=dns_output,
                )
            return ValidationResult(
                valid=False,
                actual_value=actual_value,
                expected_value=expected_target,
                message=f"CNAME mismatch: found {actual_value}, expected {expected_target}",
                dns_query_output=dns_output,
            )

        except subprocess.TimeoutExpired:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value=expected_target,
                message=f"DNS query timeout after {timeout} seconds",
                dns_query_output="",
            )
        except FileNotFoundError:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value=expected_target,
                message="'dig' command not found. Install dnsutils/bind-tools package.",
                dns_query_output="",
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value=expected_target,
                message=f"DNS validation error: {e!s}",
                dns_query_output="",
            )

    def validate_a_record(self, domain: str, timeout: int = 10) -> ValidationResult:
        """
        Validate that domain has an A record.

        Used for HTTP-01 challenge validation to ensure domain resolves.
        Does not check if it points to the correct IP (since that varies by setup).

        Args:
            domain: The domain to check
            timeout: DNS query timeout in seconds

        Returns:
            ValidationResult with status and IP address if found
        """
        if self.output:
            self.output.debug(f"Validating A record for: {domain}")

        try:
            result = subprocess.run(["dig", "+short", domain, "A"], capture_output=True, text=True, timeout=timeout)

            actual_value = result.stdout.strip()
            dns_output = result.stdout + (result.stderr if result.stderr else "")

            if not actual_value:
                return ValidationResult(
                    valid=False,
                    actual_value=None,
                    expected_value="An IP address",
                    message=f"No A record found for {domain}",
                    dns_query_output=dns_output,
                )

            # Check if it looks like an IP address (basic validation)
            ip_parts = actual_value.split("\n")[0].split(".")  # Take first line if multiple IPs
            if len(ip_parts) == 4 and all(part.isdigit() for part in ip_parts):
                return ValidationResult(
                    valid=True,
                    actual_value=actual_value.split("\n")[0],  # Return first IP
                    expected_value=None,
                    message=f"Domain resolves to {actual_value.split()[0]}",
                    dns_query_output=dns_output,
                )
            return ValidationResult(
                valid=False,
                actual_value=actual_value,
                expected_value="An IP address",
                message=f"Invalid A record response: {actual_value}",
                dns_query_output=dns_output,
            )

        except subprocess.TimeoutExpired:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value="An IP address",
                message=f"DNS query timeout after {timeout} seconds",
                dns_query_output="",
            )
        except FileNotFoundError:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value="An IP address",
                message="'dig' command not found. Install dnsutils/bind-tools package.",
                dns_query_output="",
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                actual_value=None,
                expected_value="An IP address",
                message=f"DNS validation error: {e!s}",
                dns_query_output="",
            )

    def wait_for_cname_propagation(
        self,
        domain: str,
        challenge_alias: str,
        timeout: int = 300,
        check_interval: int = 30,
    ) -> PropagationStatus:
        """
        Wait for CNAME record to propagate.

        Polls DNS every check_interval seconds until CNAME is valid or timeout reached.

        Args:
            domain: The domain to issue certificate for
            challenge_alias: The challenge alias domain
            timeout: Maximum time to wait in seconds (default: 5 minutes)
            check_interval: Time between checks in seconds (default: 30 seconds)

        Returns:
            PropagationStatus indicating if propagation completed
        """
        max_checks = timeout // check_interval
        checks_done = 0

        if self.output:
            self.output.print(
                f"Waiting for DNS propagation (checking every {check_interval}s, max {timeout}s)...",
                emoji_code=":hourglass:",
            )

        start_time = time.time()

        while checks_done < max_checks:
            checks_done += 1
            elapsed = int(time.time() - start_time)

            if self.output:
                self.output.print(f"Check {checks_done}/{max_checks} (elapsed: {elapsed}s)...", emoji_code="")

            validation = self.validate_cname_for_acme(domain, challenge_alias)

            if validation.valid:
                return PropagationStatus(
                    propagated=True,
                    checks_passed=checks_done,
                    checks_total=max_checks,
                    message=f"CNAME propagated after {elapsed} seconds",
                )

            if checks_done < max_checks:
                time.sleep(check_interval)

        return PropagationStatus(
            propagated=False,
            checks_passed=0,
            checks_total=max_checks,
            message=f"CNAME did not propagate within {timeout} seconds",
        )

    def get_nameservers(self, domain: str) -> list[str]:
        """
        Get authoritative nameservers for a domain.

        Useful for checking propagation across multiple nameservers.

        Args:
            domain: Domain to query

        Returns:
            List of nameserver hostnames
        """
        try:
            result = subprocess.run(["dig", "+short", domain, "NS"], capture_output=True, text=True, timeout=10)

            nameservers = [ns.strip() for ns in result.stdout.strip().split("\n") if ns.strip()]
            return nameservers
        except Exception:
            return []
