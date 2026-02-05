import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, EmailStr, Field, field_validator
from tomlkit.items import Array as TOMLArray

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo
from frappe_manager.ssl_manager import LETSENCRYPT_PREFERRED_CHALLENGE, SUPPORTED_SSL_TYPES
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate
from frappe_manager.utils.helpers import get_container_name_prefix


def extract_app_python_module_name(app_path: Path) -> str:
    """
    Extract Python module name from pyproject.toml or hooks.py.

    This is critical for subdirectory apps where the directory name may not match
    the Python module name. For example:
    - Directory: frappe-consent-management (with dashes)
    - Python module: frappe_consent_management (with underscores)

    Priority order:
    1. pyproject.toml [project] name field
    2. hooks.py app_name variable
    3. Fallback to directory name

    Args:
        app_path: Path to the app directory

    Returns:
        Python module name (e.g., "frappe_consent_management")
    """
    # Try pyproject.toml first (most reliable)
    pyproject = app_path / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomlkit.parse(pyproject.read_text())
            if "project" in data and "name" in data["project"]:
                return data["project"]["name"]
        except Exception:
            pass  # Fall through to next method

    # Try hooks.py (Frappe convention)
    # Search for hooks.py in immediate subdirectories (frappe convention: app_name/hooks.py)
    hooks_files = list(app_path.glob("*/hooks.py"))

    # Filter to only top-level hooks.py (not nested deeper)
    top_level_hooks = [f for f in hooks_files if len(f.relative_to(app_path).parts) == 2]

    if top_level_hooks:
        try:
            hooks_py = top_level_hooks[0]
            content = hooks_py.read_text()
            # Match: app_name = "module_name"
            match = re.search(r'app_name\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        except Exception:
            pass  # Fall through to fallback

    # Fallback: use directory name
    return app_path.name


def extract_python_version_requirement(frappe_app_path: Path) -> str | None:
    """
    Extract Python version requirement from frappe app's pyproject.toml.

    Reads the [project] requires-python field or [tool.poetry.dependencies] python field.

    Args:
        frappe_app_path: Path to the frappe app directory

    Returns:
        Python version requirement string (e.g., ">=3.10,<3.14") or None if not found
    """
    pyproject = frappe_app_path / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        data = tomlkit.parse(pyproject.read_text())

        # Check [project] requires-python (PEP 621 standard)
        if "project" in data and "requires-python" in data["project"]:
            return str(data["project"]["requires-python"])

        # Check [tool.poetry.dependencies] python (Poetry format)
        if "tool" in data and "poetry" in data["tool"]:
            if "dependencies" in data["tool"]["poetry"]:
                if "python" in data["tool"]["poetry"]["dependencies"]:
                    python_dep = data["tool"]["poetry"]["dependencies"]["python"]
                    # Poetry format can be string or dict
                    if isinstance(python_dep, str):
                        return python_dep
                    if isinstance(python_dep, dict) and "version" in python_dep:
                        return str(python_dep["version"])

        return None
    except Exception:
        return None


def extract_node_version_requirement(frappe_app_path: Path) -> str | None:
    """
    Extract Node version requirement from frappe app's package.json.

    Reads the engines.node field.

    Args:
        frappe_app_path: Path to the frappe app directory

    Returns:
        Node version requirement string (e.g., ">=18") or None if not found
    """
    package_json = frappe_app_path / "package.json"
    if not package_json.exists():
        return None

    try:
        import json

        data = json.loads(package_json.read_text())

        # Check engines.node
        if "engines" in data and "node" in data["engines"]:
            return str(data["engines"]["node"])

        return None
    except Exception:
        return None


def parse_python_version_for_runtime(version_requirement: str | None) -> str | None:
    """
    Parse Python version requirement string to extract a usable Python version.

    Handles various formats:
    - ">=3.10,<3.14" -> "3.10"
    - ">=3.14,<3.15" -> "3.14"
    - "^3.11" -> "3.11"
    - "3.11" -> "3.11"
    - "3.10.5" -> "3.10"

    Strategy: Extract the minimum compatible version for maximum compatibility.

    Args:
        version_requirement: Version requirement string from pyproject.toml

    Returns:
        Python version string suitable for UV (e.g., "3.10", "3.14")
        Returns None if parsing fails
    """
    if not version_requirement:
        return None

    try:
        import re

        # Remove whitespace
        version_str = version_requirement.strip()

        # Handle poetry caret (^3.11 -> 3.11)
        if version_str.startswith("^"):
            version_str = version_str[1:]

        # Extract version numbers using regex
        # Match patterns like: >=3.10, 3.10.5, 3.10
        match = re.search(r"(\d+)\.(\d+)(?:\.\d+)?", version_str)
        if match:
            major = match.group(1)
            minor = match.group(2)
            return f"{major}.{minor}"

        return None
    except Exception:
        return None


def parse_node_version_for_runtime(version_requirement: str | None) -> str | None:
    """
    Parse Node version requirement string to extract a usable Node version.

    Handles various formats:
    - ">=18" -> "18"
    - ">=24" -> "24"
    - "^18.0.0" -> "18"
    - "18.x" -> "18"
    - "18.12.0" -> "18"

    Strategy: Extract the major version for fnm compatibility.

    Args:
        version_requirement: Version requirement string from package.json

    Returns:
        Node major version string suitable for fnm (e.g., "18", "24")
        Returns None if parsing fails
    """
    if not version_requirement:
        return None

    try:
        import re

        # Remove whitespace
        version_str = version_requirement.strip()

        # Handle poetry/npm caret (^18.0.0 -> 18.0.0)
        if version_str.startswith("^"):
            version_str = version_str[1:]

        # Extract major version number
        # Match patterns like: >=18, 18.12.0, 18.x, 18
        match = re.search(r"(\d+)", version_str)
        if match:
            return match.group(1)

        return None
    except Exception:
        return None


def validate_python_version_compatibility(user_version: str, frappe_requirement: str) -> tuple[bool, str]:
    """
    Validate if user-provided Python version is compatible with Frappe requirement.

    Args:
        user_version: User-provided version (e.g., "3.11", ">=3.10,<3.14")
        frappe_requirement: Frappe's requirement (e.g., ">=3.14,<3.15")

    Returns:
        Tuple of (is_compatible, error_message)
    """
    import re

    def parse_version_range(version_str: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        min_ver = None
        max_ver = None

        match_min = re.search(r">=(\d+)\.(\d+)", version_str)
        if match_min:
            min_ver = (int(match_min.group(1)), int(match_min.group(2)))

        match_max = re.search(r"<(\d+)\.(\d+)", version_str)
        if match_max:
            max_ver = (int(match_max.group(1)), int(match_max.group(2)))

        exact_match = re.match(r"^(\d+)\.(\d+)$", version_str.strip())
        if exact_match:
            ver = (int(exact_match.group(1)), int(exact_match.group(2)))
            min_ver = ver
            max_ver = (ver[0], ver[1] + 1)

        return min_ver, max_ver

    user_min, user_max = parse_version_range(user_version)
    frappe_min, frappe_max = parse_version_range(frappe_requirement)

    if not user_min:
        return False, f"Could not parse user version: {user_version}"

    if not frappe_min:
        return True, ""

    if frappe_max:
        if user_min < frappe_min or (user_max and user_max > frappe_max):
            return False, f"Python {user_version} is incompatible with Frappe requirement {frappe_requirement}"
    elif user_min < frappe_min:
        return False, f"Python {user_version} is incompatible with Frappe requirement {frappe_requirement}"

    return True, ""


def validate_node_version_compatibility(user_version: str, frappe_requirement: str) -> tuple[bool, str]:
    """
    Validate if user-provided Node version is compatible with Frappe requirement.

    Args:
        user_version: User-provided version (e.g., "18", ">=20")
        frappe_requirement: Frappe's requirement (e.g., ">=24")

    Returns:
        Tuple of (is_compatible, error_message)
    """
    import re

    def extract_min_version(version_str: str) -> int | None:
        match = re.search(r">=?(\d+)", version_str)
        if match:
            return int(match.group(1))

        exact_match = re.match(r"^(\d+)$", version_str.strip())
        if exact_match:
            return int(exact_match.group(1))

        return None

    user_min = extract_min_version(user_version)
    frappe_min = extract_min_version(frappe_requirement)

    if user_min is None:
        return False, f"Could not parse user version: {user_version}"

    if frappe_min is None:
        return True, ""

    if user_min < frappe_min:
        return False, f"Node {user_version} is incompatible with Frappe requirement {frappe_requirement}"

    return True, ""


class FMBenchEnvType(str, Enum):
    prod = "prod"
    dev = "dev"


class RestartPolicyEnum(str, Enum):
    """
    Docker Compose restart policy options.

    - no: Never restart (default, current behavior)
    - always: Always restart, even after manual stop
    - on_failure: Restart only on non-zero exit code
    - unless_stopped: Restart unless manually stopped (recommended for production)
    """

    no = "no"
    always = "always"
    on_failure = "on-failure"
    unless_stopped = "unless-stopped"


@dataclass
class AppValidationResult:
    """Result of validating a single app repository."""

    app_name: str
    repo: str
    ref: str | None
    success: bool
    auth_method: str | None
    validated_url: str | None
    error_message: str | None = None

    @property
    def display_message(self) -> str:
        """Get user-friendly display message."""
        if self.success:
            ref_info = (
                f"commit {self.ref[:8]}..."
                if self.ref and len(self.ref) == 40
                else f"branch '{self.ref}'"
                if self.ref
                else "default branch"
            )
            auth = self.auth_method.lower().replace("github token", "token")
            return f"{self.app_name} → {self.repo}:{ref_info} accessible via {auth}"
        return self.error_message or f"{self.app_name} ({self.repo}): Validation failed"


@dataclass
class AppBatchValidationResult:
    """Result of validating multiple app repositories."""

    results: list[AppValidationResult]

    @property
    def all_valid(self) -> bool:
        """Check if all apps validated successfully."""
        return all(r.success for r in self.results)

    @property
    def success_count(self) -> int:
        """Count of successfully validated apps."""
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        """Count of failed validations."""
        return sum(1 for r in self.results if not r.success)

    @property
    def messages(self) -> list[str]:
        """Get all display messages (success + error)."""
        return [r.display_message for r in self.results]


class AppConfig(BaseModel):
    """
    Configuration for a single Frappe app.

    Supports multiple input formats:
    - Simple: "erpnext:version-15"
    - Repo: "frappe/erpnext:version-15"
    - Full URL: "https://github.com/frappe/erpnext:version-15"
    - Subdirectory: "frappe/frappe:version-15#apps/frappe"
    """

    name: str = Field(..., description="App name (e.g., 'erpnext')")
    repo: str = Field(..., description="GitHub repo (e.g., 'frappe/erpnext')")
    ref: str | None = Field(None, description="Branch, tag, or commit SHA")
    repo_url: str | None = Field(None, description="Full repo URL (auto-generated)")
    shallow_clone: bool = Field(True, description="Use shallow clone (depth=1)")
    subdir_path: str | None = Field(None, description="Subdirectory path for monorepo apps")
    symlink: bool = Field(False, description="Use symlink for subdirectory apps")

    @property
    def is_commit(self) -> bool:
        """Check if ref is a commit SHA (40 hex characters)."""
        if self.ref is None:
            return False
        return len(self.ref) == 40 and all(c in "0123456789abcdef" for c in self.ref.lower())

    @classmethod
    def from_string(cls, app_string: str) -> "AppConfig":
        """
        Parse app string into AppConfig.

        Formats:
        - "erpnext" → frappe/erpnext:default-branch
        - "erpnext:version-15" → frappe/erpnext:version-15
        - "frappe/erpnext:version-15" → frappe/erpnext:version-15
        - "mycompany/custom-app:main" → mycompany/custom-app:main
        - "frappe/frappe:version-15#apps/frappe" → subdirectory app

        Args:
            app_string: String describing the app to install

        Returns:
            AppConfig instance
        """
        # Split on '#' for subdirectory
        if "#" in app_string:
            app_part, subdir_path = app_string.split("#", 1)
        else:
            app_part = app_string
            subdir_path = None

        # Check if this is a full URL (starts with protocol or git@)
        is_full_url = app_part.startswith(("http://", "https://"))
        is_ssh_url = app_part.startswith("git@")

        # Split on ':' for branch/ref
        # For URLs: skip protocol colon, find the LAST colon (if any) for ref
        # For SSH: format is git@host:org/repo:ref, so find LAST colon after @
        # For short form: any colon is ref separator
        ref = None
        if is_full_url:
            # For http(s):// URLs, look for colon AFTER the protocol and host
            # e.g., "https://github.com/frappe/frappe:version-15"
            #       split at ":" -> ["https", "//github.com/frappe/frappe", "version-15"]
            parts = app_part.split(":")
            if len(parts) > 2:  # Has protocol + potential ref
                # Rejoin protocol + host/path, last part is ref
                repo_part = ":".join(parts[:-1])
                ref = parts[-1]
            else:
                repo_part = app_part
        elif is_ssh_url:
            # For git@ URLs: git@github.com:frappe/frappe:version-15
            # Find last colon after @ for ref
            at_index = app_part.index("@")
            remainder = app_part[at_index + 1 :]
            if remainder.count(":") > 1:
                # Multiple colons: last one is ref separator
                last_colon_idx = remainder.rfind(":")
                repo_part = app_part[: at_index + 1 + last_colon_idx]
                ref = remainder[last_colon_idx + 1 :]
            else:
                repo_part = app_part
        # Short form: org/repo:ref or app:ref
        elif ":" in app_part:
            repo_part, ref = app_part.split(":", 1)
        else:
            repo_part = app_part

        # Parse repo (e.g., "frappe/erpnext" or just "erpnext" or full URL)
        if is_full_url or is_ssh_url:
            # Full URL: keep as-is, extract name from path
            repo = repo_part
            # Extract name from URL path (last segment before .git)
            path_part = repo_part.split("/")[-1]
            name = path_part.replace(".git", "")
        elif "/" in repo_part:
            repo = repo_part
            name = repo_part.split("/")[-1]
        else:
            name = repo_part
            repo = f"frappe/{name}"  # Default to frappe org

        # Override name if subdirectory specified
        if subdir_path:
            name = subdir_path.split("/")[-1]

        return cls(
            name=name,
            repo=repo,
            ref=ref,
            repo_url=None,
            shallow_clone=True,
            subdir_path=subdir_path,
        )

    @classmethod
    def from_dict(cls, app_dict: dict[str, str | None], github_token: str | None = None) -> "AppConfig":
        """
        Convert from simple dict format to AppConfig.

        Args:
            app_dict: {"app": "erpnext", "branch": "version-15"}
            github_token: Optional GitHub token (unused, kept for backward compatibility)

        Returns:
            AppConfig instance
        """
        app_name = app_dict.get("app")
        if not app_name:
            raise ValueError("app_dict must contain 'app' key with non-empty value")

        branch = app_dict.get("branch")

        if branch:
            app_string: str = f"{app_name}:{branch}"
        else:
            app_string: str = app_name

        return cls.from_string(app_string)

    @staticmethod
    def validate_github_token(github_token: str) -> tuple[bool, str | None]:
        """
        Validate GitHub token by making an API call to GitHub.

        Args:
            github_token: GitHub personal access token

        Returns:
            Tuple of (is_valid, error_message)
        """
        import subprocess

        from frappe_manager.logger import log

        logger = log.get_logger()

        try:
            cmd = ["git", "ls-remote", f"https://{github_token}@github.com/octocat/hello-world.git", "HEAD"]
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            env["GIT_ASKPASS"] = "echo"

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)

            if result.returncode == 0:
                logger.debug("GitHub token validated successfully")
                return (True, None)
            error = result.stderr.strip()
            if "bad credentials" in error.lower() or "authentication failed" in error.lower():
                return (False, "Invalid or expired GitHub token")
            if "could not resolve host" in error.lower():
                return (False, "Network error: Cannot reach GitHub")
            return (False, f"Token validation failed: {error}")

        except subprocess.TimeoutExpired:
            return (False, "Timeout while validating token (network issue?)")
        except FileNotFoundError:
            logger.error("Git is not installed or not in PATH")
            return (False, "Git is not installed")
        except Exception as e:
            logger.debug(f"Exception during token validation: {e}")
            return (False, f"Unexpected error: {e!s}")

    def get_auth_methods(self, github_token: str | None = None) -> list[tuple[str, str]]:
        """
        Get ordered list of authentication methods to try for this app.

        Returns list of (method_name, repo_url) tuples to try in fallback order.
        If self.repo_url is already set (from previous validation), returns it first,
        but ALWAYS includes fallback methods for resilience.

        Args:
            github_token: Optional GitHub token for private repos

        Returns:
            List of (method_name, url) tuples ordered by priority
        """
        methods = []

        if self.repo_url:
            methods.append(("Validated URL", self.repo_url))

        if self.repo.startswith(("http://", "https://", "git@")):
            if self.repo not in [m[1] for m in methods]:
                methods.append(("Full URL", self.repo))
        else:
            if github_token:
                token_url = f"https://{github_token}@github.com/{self.repo}.git"
                if token_url != self.repo_url:
                    methods.append(("GitHub Token", token_url))

            https_url = f"https://github.com/{self.repo}.git"
            if https_url != self.repo_url:
                methods.append(("HTTPS", https_url))

            ssh_url = f"git@github.com:{self.repo}.git"
            if ssh_url != self.repo_url:
                methods.append(("SSH", ssh_url))

        return methods

    def validate_repo_exists(self, github_token: str | None = None) -> AppValidationResult:
        """
        Validate that this app's repository exists and is accessible.

        Uses git ls-remote to check repository without cloning.
        Tries authentication methods in priority order:
        - With token: Token → HTTPS → SSH
        - Without token: HTTPS → SSH
        Validates GitHub token before attempting to use it.

        Args:
            github_token: Optional GitHub token for private repos

        Returns:
            AppValidationResult with success status, auth method used, and validated URL
        """
        from frappe_manager.logger import log

        logger = log.get_logger()

        if github_token and "github.com" in self.repo:
            is_valid, error_msg = AppConfig.validate_github_token(github_token)
            if not is_valid:
                logger.warning(f"GitHub token validation failed: {error_msg}")
                logger.warning(f"Will attempt to validate {self.name} without token authentication")

        auth_methods = self.get_auth_methods(github_token)
        last_error = None
        last_method = None

        for method_name, url in auth_methods:
            try:
                if self.is_commit:
                    cmd = ["git", "ls-remote", url, "HEAD"]
                else:
                    cmd = ["git", "ls-remote", "--heads", "--tags", url]
                    if self.ref:
                        cmd.extend([self.ref])

                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_ASKPASS"] = "echo"

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)

                if result.returncode == 0:
                    if self.is_commit:
                        if result.stdout:
                            self.repo_url = url
                            logger.info(f"Validated {self.name} ({self.repo}) using {method_name}")
                            return AppValidationResult(
                                app_name=self.name,
                                repo=self.repo,
                                ref=self.ref,
                                success=True,
                                auth_method=method_name,
                                validated_url=url,
                            )
                        last_error = "Repository exists but appears empty or inaccessible"
                        last_method = method_name
                    elif self.ref and not result.stdout:
                        last_error = f"Branch/tag '{self.ref}' not found"
                        last_method = method_name
                    else:
                        self.repo_url = url
                        logger.info(f"Validated {self.name} ({self.repo}) using {method_name}")
                        return AppValidationResult(
                            app_name=self.name,
                            repo=self.repo,
                            ref=self.ref,
                            success=True,
                            auth_method=method_name,
                            validated_url=url,
                        )
                else:
                    last_error = result.stderr.strip()
                    last_method = method_name
                    logger.debug(f"Validation failed for {self.name} using {method_name}: {last_error}")

            except subprocess.TimeoutExpired:
                last_error = "Timeout while checking repository (network issue?)"
                last_method = method_name
                logger.debug(f"Timeout validating {self.name} using {method_name}")
            except FileNotFoundError:
                logger.error("Git is not installed or not in PATH")
                return AppValidationResult(
                    app_name=self.name,
                    repo=self.repo,
                    ref=self.ref,
                    success=False,
                    auth_method=None,
                    validated_url=None,
                    error_message="❌ Git is not installed or not in PATH",
                )
            except Exception as e:
                last_error = str(e)
                last_method = method_name
                logger.debug(f"Exception validating {self.name} using {method_name}: {e}")

        ref_info = f" (ref: {self.ref})" if self.ref else ""

        if last_error and "not found" in last_error.lower():
            error_msg = f"❌ {self.name} ({self.repo}{ref_info}): Repository not found on GitHub"
        elif last_error and self.ref and "branch/tag" in last_error.lower():
            error_msg = (
                f"❌ {self.name} ({self.repo}): Branch/tag '{self.ref}' not found\n"
                f"   Check branch name or use a valid tag/commit"
            )
        elif github_token:
            logger.warning(f"GitHub token provided but failed for {self.name}")
            error_msg = (
                f"❌ {self.name} ({self.repo}{ref_info}): Could not access repository\n"
                f"   • HTTPS: Failed\n"
                f"   • GitHub Token: Failed (token may be invalid/expired/insufficient permissions)\n"
                f"   • SSH: Failed\n"
                f"   Last error ({last_method}): {last_error}"
            )
        else:
            error_msg = (
                f"❌ {self.name} ({self.repo}{ref_info}): Could not access repository\n"
                f"   • HTTPS: Failed (repo may be private)\n"
                f"   • SSH: Failed\n"
                f"   💡 If private repo: use --github-token or configure SSH keys\n"
                f"   Last error ({last_method}): {last_error}"
            )

        logger.error(f"Failed to validate {self.name}: {last_error}")
        return AppValidationResult(
            app_name=self.name,
            repo=self.repo,
            ref=self.ref,
            success=False,
            auth_method=None,
            validated_url=None,
            error_message=error_msg,
        )

    @classmethod
    def validate_repos_batch(
        cls,
        apps: list["AppConfig"],
        github_token: str | None = None,
        max_workers: int = 10,
    ) -> AppBatchValidationResult:
        """
        Validate multiple app repositories in parallel.

        Args:
            apps: List of AppConfig objects to validate
            github_token: Optional GitHub token for private repos
            max_workers: Maximum parallel workers (default: 10)

        Returns:
            AppBatchValidationResult with all individual results
        """
        results = []

        with ThreadPoolExecutor(max_workers=min(max_workers, len(apps))) as executor:
            futures = [executor.submit(app.validate_repo_exists, github_token) for app in apps]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if not result.success and "Git is not installed" in result.error_message:
                    break

        return AppBatchValidationResult(results=results)


class DNSProviderConfig(BaseModel):
    """DNS provider credentials for DNS-01 challenge at bench level."""

    email: EmailStr | None = Field(None, description="DNS provider account email (if required).")
    api_token: str | None = Field(None, description="DNS provider API Token.")
    api_key: str | None = Field(None, description="DNS provider API Key.")

    @property
    def exists(self) -> bool:
        """Check if any DNS credentials are configured."""
        return bool(self.api_token or self.api_key)

    def get_toml_doc(self):
        """Convert to TOML document."""
        model_dict = self.model_dump(exclude_none=True)
        toml_doc = tomlkit.document()

        for key, value in model_dict.items():
            toml_doc[key] = value
        return toml_doc

    @classmethod
    def import_from_toml_doc(cls, toml_doc):
        """Import from TOML document."""
        return cls(**toml_doc)


def ssl_certificate_from_toml_data(ssl_data: dict, domain: str) -> SSLCertificate:
    """Parse a single certificate from TOML data."""
    ssl_type = ssl_data.get("ssl_type", SUPPORTED_SSL_TYPES.none)

    if ssl_type == SUPPORTED_SSL_TYPES.le:
        # Email field removed - Let's Encrypt discontinued notifications (June 2025)
        # Remove email from TOML data if present (backward compatibility)
        ssl_data.pop("email", None)

        fm_config_manager = FMConfigManager.import_from_toml()

        # Read challenge_type (new field) or preferred_challenge (backward compat)
        challenge_type = ssl_data.get("challenge_type")
        if not challenge_type:
            # Fall back to preferred_challenge for backward compatibility
            challenge_type = ssl_data.get("preferred_challenge")

        api_token = ssl_data.get("api_token")
        if not api_token:
            api_token = fm_config_manager.cloudflare.api_token

        api_key = ssl_data.get("api_key")
        if not api_key:
            api_key = fm_config_manager.cloudflare.api_key

        # If no challenge type specified, infer from available credentials
        if not challenge_type:
            if fm_config_manager.cloudflare.exists:
                challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
            else:
                challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # Read acme_client field (defaults to "acme.sh" if not specified)
        acme_client = ssl_data.get("acme_client", "acme.sh")

        return LetsencryptSSLCertificate(
            domain=domain,
            ssl_type=ssl_type,
            # Email removed - credentials loaded from FM config
            challenge_type=challenge_type,
            api_key=api_key,
            api_token=api_token,
            acme_client=acme_client,
        )
    return SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)


def ssl_certificate_to_toml_doc(cert: SSLCertificate) -> tomlkit.TOMLDocument | None:
    """Convert a single certificate to TOML document."""
    if cert.ssl_type == SUPPORTED_SSL_TYPES.none:
        return None

    # Explicitly exclude only the computed field, but INCLUDE domain
    model_dict = cert.model_dump(exclude={"toml_exclude"}, exclude_none=True)
    toml_doc = tomlkit.document()

    for key, value in model_dict.items():
        if isinstance(value, Path):
            toml_doc[key] = str(value.absolute())
        else:
            toml_doc[key] = value
    return toml_doc


def ssl_certificates_to_toml_array(certs: list[SSLCertificate]) -> TOMLArray:
    """Convert a list of certificates to TOML array-of-tables."""
    # Use aot() for array-of-tables format [[ssl_certificates]]
    toml_aot = tomlkit.aot()
    for cert in certs:
        if cert.ssl_type != SUPPORTED_SSL_TYPES.none:
            cert_doc = ssl_certificate_to_toml_doc(cert)
            if cert_doc:
                toml_aot.append(cert_doc)
    return toml_aot


class MigrationState(BaseModel):
    migrated_to: str | None = Field(None, description="Version bench is migrated to (e.g., '0.19.0')")
    last_migration_date: str | None = Field(None, description="ISO timestamp of last migration")


class BenchConfig(BaseModel):
    name: str = Field(..., description="The name of the bench")
    developer_mode: bool = Field(..., description="Whether developer mode is enabled")
    admin_tools: bool = Field(..., description="Whether admin tools are enabled")
    environment_type: FMBenchEnvType = Field(..., description="The type of environment")

    # Multi-certificate support
    ssl_certificates: list[SSLCertificate] = Field(default=[], description="List of SSL certificates for this bench")

    # DNS provider credentials for DNS-01 challenge (optional, bench-specific override)
    dns_providers: dict[str, DNSProviderConfig] | None = Field(
        default=None,
        description="DNS provider credentials for DNS-01 challenge (e.g., {'cloudflare': {...}})",
    )

    alias_domains: list[str] = Field(default=[], description="List of alias domains for the bench")

    upload_limit: str = Field(default="50M", description="Maximum upload size (e.g., '50M', '100M', '500M', '1G')")

    admin_pass: str = Field("admin", description="The admin password")
    root_path: Path = Field(..., description="The root path")
    apps_list: list["AppConfig"] = Field(default=[], description="List of apps")
    userid: int = Field(default_factory=os.getuid, description="The user ID of the current process")
    usergroup: int = Field(default_factory=os.getgid, description="The group ID of the current process")
    admin_tools_username: str | None = Field(None, description="Username for admin tools basic auth")
    admin_tools_password: str | None = Field(None, description="Password for admin tools basic auth")

    # NEW: GitHub token for private repositories
    github_token: str | None = Field(None, description="GitHub personal access token for private repositories")

    # NEW: UV installation preference (always True, with fallback)
    use_uv: bool = Field(
        True,
        description="Use UV for faster Python package installation (with automatic fallback to pip)",
    )

    # NEW: Auto-detected Python and Node version requirements from frappe
    python_version: str | None = Field(
        None,
        description="Python version requirement from frappe app (e.g., '>=3.10,<3.14')",
    )
    node_version: str | None = Field(None, description="Node version requirement from frappe app (e.g., '>=18')")

    # Database name (randomized on creation to avoid conflicts)
    db_name: str | None = Field(None, description="Database name for this bench (auto-generated random string)")

    restart_policy: RestartPolicyEnum | None = Field(
        default=None,
        description="Docker Compose restart policy for all services in this bench",
    )

    migration_state: MigrationState | None = Field(
        None,
        description="Migration state tracking (managed by migration system)",
    )

    @field_validator("restart_policy", mode="before")
    @classmethod
    def set_default_restart_policy(cls, v, info):
        if v is None and "environment_type" in info.data:
            env_type = info.data["environment_type"]
            if env_type == FMBenchEnvType.prod:
                return RestartPolicyEnum.unless_stopped
            return RestartPolicyEnum.no
        return v if v is not None else RestartPolicyEnum.no

    def get_apps_config(self) -> list[AppConfig]:
        """
        Convert apps_list to AppConfig objects.

        Handles conversion from simple format to detailed AppConfig.

        Returns:
            List of AppConfig objects
        """
        return self.apps_list

    def get_primary_certificate(self) -> SSLCertificate:
        """
        Get the primary SSL certificate (certificate for the bench's primary domain).

        Returns the first certificate in ssl_certificates list, which should be
        the certificate for the bench's primary domain, or creates a default
        disabled certificate if the list is empty.

        Note: With individual certificates, each domain has its own entry in
        ssl_certificates. The first entry is conventionally the primary domain.
        """
        if self.ssl_certificates:
            return self.ssl_certificates[0]

        # Return default disabled certificate
        return SSLCertificate(domain=self.name, ssl_type=SUPPORTED_SSL_TYPES.none)

    def set_primary_certificate(self, certificate: SSLCertificate):
        """
        Set the primary SSL certificate (certificate for the bench's primary domain).

        Updates the first certificate in the list, or creates a new list with this
        certificate. This is typically used when enabling/disabling SSL for the
        primary domain.

        Note: For managing individual domain certificates, use create_individual_certificates()
        or directly manipulate the ssl_certificates list.
        """
        if self.ssl_certificates:
            self.ssl_certificates[0] = certificate
        else:
            self.ssl_certificates = [certificate]

    def create_individual_certificates(self, template_certificate: SSLCertificate) -> None:
        """
        Create individual certificate entries for primary domain and all alias domains.

        This replaces any existing certificates with individual certificate entries
        for each domain (primary + aliases), using the template certificate as a base.

        Args:
            template_certificate: Certificate configuration to use as template
                                 (email, acme_client, ssl_type, etc.)
        """
        from copy import deepcopy

        new_certificates = []

        # Create certificate for primary domain
        primary_cert = deepcopy(template_certificate)
        primary_cert.domain = self.name
        new_certificates.append(primary_cert)

        # Create individual certificates for each alias domain
        for alias_domain in self.alias_domains:
            alias_cert = deepcopy(template_certificate)
            alias_cert.domain = alias_domain
            new_certificates.append(alias_cert)

        # Replace all certificates with individual ones
        self.ssl_certificates = new_certificates

    @property
    def container_name_prefix(self):
        return get_container_name_prefix(self.name)

    def get_all_domains(self) -> list[str]:
        """
        Get all domains configured for this bench (primary + aliases).

        Returns:
            List of all domains that can have SSL certificates.
        """
        all_domains = [self.name]
        if self.alias_domains:
            all_domains.extend(self.alias_domains)
        return all_domains

    def export_to_toml(self, path: Path) -> bool:
        """
        Export bench configuration to TOML file.
        """
        exclude = {
            "root_path",
            "mariadb_root_pass",
            "userid",
            "mariadb_host",
            "usergroup",
            "apps_list",
            "frappe_branch",
            "admin_pass",
        }

        # Convert the BenchConfig instance to a dictionary
        bench_dict = self.model_dump(exclude=exclude, exclude_none=True)

        # Handle SSL certificates
        if self.ssl_certificates:
            # Export as array
            certs_array = ssl_certificates_to_toml_array(self.ssl_certificates)
            if len(certs_array) > 0:
                bench_dict["ssl_certificates"] = certs_array
            else:
                # No active certificates, remove the key
                bench_dict.pop("ssl_certificates", None)
        else:
            # No certificates at all
            bench_dict.pop("ssl_certificates", None)

        # Handle DNS providers (convert to nested tables)
        if self.dns_providers:
            dns_providers_toml = tomlkit.table()
            for provider_name, provider_config in self.dns_providers.items():
                if provider_config.exists:
                    dns_providers_toml[provider_name] = provider_config.get_toml_doc()
            if len(dns_providers_toml) > 0:
                bench_dict["dns_providers"] = dns_providers_toml
            else:
                bench_dict.pop("dns_providers", None)
        else:
            bench_dict.pop("dns_providers", None)

        # Serialize the dictionary to a TOML string
        toml_doc = tomlkit.document()

        for key, value in bench_dict.items():
            if isinstance(value, Path):
                toml_doc[key] = str(value.absolute())
            else:
                toml_doc[key] = value

        try:
            with open(path, "w") as f:
                f.write(tomlkit.dumps(toml_doc))
            return True
        except Exception as e:
            return False

    @classmethod
    def import_from_toml(cls, path: Path) -> "BenchConfig":
        """
        Import bench configuration from TOML file.

        Uses the multi-certificate format (ssl_certificates array).
        """
        data = tomlkit.parse(path.read_text())
        data["root_path"] = str(path)

        domain: str = data.get("name", "")
        ssl_certificates_list: list[SSLCertificate] = []

        # Parse multi-certificate format
        ssl_certificates_data = data.get("ssl_certificates", None)
        if ssl_certificates_data and isinstance(ssl_certificates_data, list):
            for cert_data in ssl_certificates_data:
                cert_domain = cert_data.get("domain", domain)
                ssl_cert = ssl_certificate_from_toml_data(cert_data, cert_domain)
                ssl_certificates_list.append(ssl_cert)

        # If no certificates found, start with empty list
        # (default cert will be created via get_primary_certificate() when needed)

        # Read alias_domains from root level only
        alias_domains_list = data.get("alias_domains", [])

        # Parse DNS providers (nested tables)
        dns_providers_dict = {}
        dns_providers_data = data.get("dns_providers", None)
        if dns_providers_data and isinstance(dns_providers_data, dict):
            for provider_name, provider_data in dns_providers_data.items():
                if isinstance(provider_data, dict):
                    dns_providers_dict[provider_name] = DNSProviderConfig.import_from_toml_doc(provider_data)

        migration_state_data = data.get("migration_state", None)
        migration_state_obj = None
        if migration_state_data and isinstance(migration_state_data, dict):
            migration_state_obj = MigrationState(**migration_state_data)

        input_data = {
            "name": data.get("name", None),
            "developer_mode": data.get("developer_mode", None),
            "admin_tools": data.get("admin_tools", False),
            "environment_type": data.get("environment_type", None),
            "root_path": data.get("root_path", None),
            "ssl_certificates": ssl_certificates_list,
            "dns_providers": dns_providers_dict if dns_providers_dict else None,
            "alias_domains": alias_domains_list,
            "upload_limit": data.get("upload_limit", "50M"),
            "admin_tools_username": data.get("admin_tools_username", None),
            "admin_tools_password": data.get("admin_tools_password", None),
            "admin_pass": data.get("admin_pass", "admin"),
            "apps_list": data.get("apps_list", []),
            "github_token": data.get("github_token", None),
            "use_uv": data.get("use_uv", True),
            "python_version": data.get("python_version", None),
            "node_version": data.get("node_version", None),
            "db_name": data.get("db_name"),
            "restart_policy": data.get("restart_policy", None),
            "migration_state": migration_state_obj,
        }

        bench_config_instance = cls(**input_data)
        return bench_config_instance

    def get_commmon_site_config_data(self, db_server_info: DatabaseServerServiceInfo) -> dict[str, Any]:
        common_site_config_data = {
            "install_apps": [],
            "db_host": db_server_info.host,
            "db_port": db_server_info.port,
            "redis_cache": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
            "redis_queue": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-queue:6379",
            "redis_socketio": f"redis://{self.container_name_prefix}{CLI_DEFAULT_DELIMETER}redis-cache:6379",
            "webserver_port": 80,
            "socketio_port": 80,
            "restart_supervisor_on_update": 0,
            "developer_mode": self.developer_mode,
        }

        return common_site_config_data

    def get_site_mappings(self) -> dict[str, str]:
        mappings = {}
        mappings[self.name] = self.name
        if self.alias_domains:
            for alias in self.alias_domains:
                mappings[alias] = self.name
        return mappings

    def export_to_compose_inputs(self):
        all_domains = [self.name]
        if self.alias_domains:
            all_domains.extend(self.alias_domains)
        domains_string = ",".join(all_domains)

        environment = {
            "frappe": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "frappe",
            },
            "nginx": {
                "SITE_MAPPINGS": json.dumps(self.get_site_mappings()),
                "VIRTUAL_HOST": domains_string,
                "VIRTUAL_PORT": 80,
                "HTTPS_METHOD": "noredirect",
                "HSTS": self.get_primary_certificate().hsts,
                "CLIENT_MAX_BODY_SIZE": self.upload_limit.lower(),
            },
            "worker": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
            },
            "schedule": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "schedule",
            },
            "socketio": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "socketio",
            },
        }

        users: dict = {"nginx": {"uid": self.userid, "gid": self.usergroup}}
        template_inputs: dict = {
            "environment": environment,
            "user": users,
            "restart_policy": self.restart_policy.value,
        }
        return template_inputs
