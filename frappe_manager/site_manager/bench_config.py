from enum import Enum
import os
import re
from frappe_manager.services_manager.database_service_manager import DatabaseServerServiceInfo
import tomlkit
from tomlkit.items import Array as TOMLArray
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
from frappe_manager import CLI_DEFAULT_DELIMETER, STABLE_APP_BRANCH_MAPPING_LIST
from frappe_manager.metadata_manager import FMConfigManager
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


def extract_python_version_requirement(frappe_app_path: Path) -> Optional[str]:
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
        import json

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
                    elif isinstance(python_dep, dict) and "version" in python_dep:
                        return str(python_dep["version"])

        return None
    except Exception:
        return None


def extract_node_version_requirement(frappe_app_path: Path) -> Optional[str]:
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


def parse_python_version_for_runtime(version_requirement: Optional[str]) -> Optional[str]:
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


def parse_node_version_for_runtime(version_requirement: Optional[str]) -> Optional[str]:
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
    else:
        if user_min < frappe_min:
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
    prod = 'prod'
    dev = 'dev'


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
    ref: Optional[str] = Field(None, description="Branch, tag, or commit SHA")
    repo_url: Optional[str] = Field(None, description="Full repo URL (auto-generated)")
    shallow_clone: bool = Field(True, description="Use shallow clone (depth=1)")
    subdir_path: Optional[str] = Field(None, description="Subdirectory path for monorepo apps")
    symlink: bool = Field(False, description="Use symlink for subdirectory apps")

    @property
    def is_commit(self) -> bool:
        """Check if ref is a commit SHA (40 hex characters)."""
        if self.ref is None:
            return False
        return len(self.ref) == 40 and all(c in '0123456789abcdef' for c in self.ref.lower())

    @classmethod
    def from_string(cls, app_string: str, github_token: Optional[str] = None) -> 'AppConfig':
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
            github_token: Optional GitHub token for private repos

        Returns:
            AppConfig instance
        """
        # Split on '#' for subdirectory
        if '#' in app_string:
            app_part, subdir_path = app_string.split('#', 1)
        else:
            app_part = app_string
            subdir_path = None

        # Split on ':' for branch/ref
        if ':' in app_part:
            repo_part, ref = app_part.split(':', 1)
        else:
            repo_part = app_part
            ref = None

        # Parse repo (e.g., "frappe/erpnext" or just "erpnext")
        if '/' in repo_part:
            repo = repo_part
            name = repo_part.split('/')[-1]
        else:
            name = repo_part
            repo = f"frappe/{name}"  # Default to frappe org

        # Override name if subdirectory specified
        if subdir_path:
            # Extract the actual app name from subdirectory path
            # e.g., "apps/frappe" → "frappe"
            name = subdir_path.split('/')[-1]

        # Generate repo URL
        repo_url = None
        if github_token:
            repo_url = f"https://{github_token}@github.com/{repo}.git"
        else:
            repo_url = f"https://github.com/{repo}.git"

        return cls(
            name=name,
            repo=repo,
            ref=ref,
            repo_url=repo_url,
            shallow_clone=True,  # Default to shallow clone
            subdir_path=subdir_path,
            symlink=False,  # Default to copy mode
        )

    @classmethod
    def from_dict(cls, app_dict: Dict[str, Optional[str]], github_token: Optional[str] = None) -> 'AppConfig':
        """
        Convert from simple dict format to AppConfig.

        Args:
            app_dict: {"app": "erpnext", "branch": "version-15"}
            github_token: Optional GitHub token

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

        return cls.from_string(app_string, github_token=github_token)


class DNSProviderConfig(BaseModel):
    """DNS provider credentials for DNS-01 challenge at bench level."""

    email: Optional[EmailStr] = Field(None, description="DNS provider account email (if required).")
    api_token: Optional[str] = Field(None, description="DNS provider API Token.")
    api_key: Optional[str] = Field(None, description="DNS provider API Key.")

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


def ssl_certificate_from_toml_data(ssl_data: Dict, domain: str) -> SSLCertificate:
    """Parse a single certificate from TOML data."""
    ssl_type = ssl_data.get('ssl_type', SUPPORTED_SSL_TYPES.none)

    if ssl_type == SUPPORTED_SSL_TYPES.le:
        # Email field removed - Let's Encrypt discontinued notifications (June 2025)
        # Remove email from TOML data if present (backward compatibility)
        ssl_data.pop('email', None)

        fm_config_manager = FMConfigManager.import_from_toml()

        # Read challenge_type (new field) or preferred_challenge (backward compat)
        challenge_type = ssl_data.get("challenge_type", None)
        if not challenge_type:
            # Fall back to preferred_challenge for backward compatibility
            challenge_type = ssl_data.get("preferred_challenge", None)

        api_token = ssl_data.get('api_token', None)
        if not api_token:
            api_token = fm_config_manager.cloudflare.api_token

        api_key = ssl_data.get('api_key', None)
        if not api_key:
            api_key = fm_config_manager.cloudflare.api_key

        # If no challenge type specified, infer from available credentials
        if not challenge_type:
            if fm_config_manager.cloudflare.exists:
                challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
            else:
                challenge_type = LETSENCRYPT_PREFERRED_CHALLENGE.http01

        # Read acme_client field (defaults to "acme.sh" if not specified)
        acme_client = ssl_data.get('acme_client', 'acme.sh')

        return LetsencryptSSLCertificate(
            domain=domain,
            ssl_type=ssl_type,
            # Email removed - credentials loaded from FM config
            challenge_type=challenge_type,
            api_key=api_key,
            api_token=api_token,
            acme_client=acme_client,
        )
    else:
        return SSLCertificate(domain=domain, ssl_type=SUPPORTED_SSL_TYPES.none)


def ssl_certificate_to_toml_doc(cert: SSLCertificate) -> Optional[tomlkit.TOMLDocument]:
    """Convert a single certificate to TOML document."""
    if cert.ssl_type == SUPPORTED_SSL_TYPES.none:
        return None

    # Explicitly exclude only the computed field, but INCLUDE domain
    model_dict = cert.model_dump(exclude={'toml_exclude'}, exclude_none=True)
    toml_doc = tomlkit.document()

    for key, value in model_dict.items():
        if isinstance(value, Path):
            toml_doc[key] = str(value.absolute())
        else:
            toml_doc[key] = value
    return toml_doc


def ssl_certificates_to_toml_array(certs: List[SSLCertificate]) -> TOMLArray:
    """Convert a list of certificates to TOML array-of-tables."""
    # Use aot() for array-of-tables format [[ssl_certificates]]
    toml_aot = tomlkit.aot()
    for cert in certs:
        if cert.ssl_type != SUPPORTED_SSL_TYPES.none:
            cert_doc = ssl_certificate_to_toml_doc(cert)
            if cert_doc:
                toml_aot.append(cert_doc)
    return toml_aot


class BenchConfig(BaseModel):
    name: str = Field(..., description="The name of the bench")
    developer_mode: bool = Field(..., description="Whether developer mode is enabled")
    admin_tools: bool = Field(..., description="Whether admin tools are enabled")
    environment_type: FMBenchEnvType = Field(..., description="The type of environment")

    # Multi-certificate support
    ssl_certificates: List[SSLCertificate] = Field(default=[], description="List of SSL certificates for this bench")

    # DNS provider credentials for DNS-01 challenge (optional, bench-specific override)
    dns_providers: Optional[Dict[str, DNSProviderConfig]] = Field(
        default=None, description="DNS provider credentials for DNS-01 challenge (e.g., {'cloudflare': {...}})"
    )

    alias_domains: List[str] = Field(default=[], description="List of alias domains for the bench")

    upload_limit: str = Field(default="50M", description="Maximum upload size (e.g., '50M', '100M', '500M', '1G')")

    admin_pass: str = Field('admin', description="The admin password")
    root_path: Path = Field(..., description="The root path")
    apps_list: List[Dict[str, Optional[str]]] = Field(default=[], description="List of apps")
    userid: int = Field(default_factory=os.getuid, description="The user ID of the current process")
    usergroup: int = Field(default_factory=os.getgid, description="The group ID of the current process")
    admin_tools_username: Optional[str] = Field(None, description="Username for admin tools basic auth")
    admin_tools_password: Optional[str] = Field(None, description="Password for admin tools basic auth")

    # NEW: GitHub token for private repositories
    github_token: Optional[str] = Field(None, description="GitHub personal access token for private repositories")

    # NEW: UV installation preference (always True, with fallback)
    use_uv: bool = Field(
        True, description="Use UV for faster Python package installation (with automatic fallback to pip)"
    )

    # NEW: Auto-detected Python and Node version requirements from frappe
    python_version: Optional[str] = Field(
        None, description="Python version requirement from frappe app (e.g., '>=3.10,<3.14')"
    )
    node_version: Optional[str] = Field(None, description="Node version requirement from frappe app (e.g., '>=18')")

    # Database name (randomized on creation to avoid conflicts)
    db_name: Optional[str] = Field(None, description="Database name for this bench (auto-generated random string)")

    def get_apps_config(self) -> List[AppConfig]:
        """
        Convert apps_list to AppConfig objects.

        Handles conversion from simple format to detailed AppConfig.

        Returns:
            List of AppConfig objects
        """
        configs = []
        for app_dict in self.apps_list:
            config = AppConfig.from_dict(app_dict, github_token=self.github_token)
            configs.append(config)
        return configs

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

    def get_all_domains(self) -> List[str]:
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
            'root_path',
            'mariadb_root_pass',
            'userid',
            'mariadb_host',
            'usergroup',
            'apps_list',
            'frappe_branch',
            'admin_pass',
        }

        # Convert the BenchConfig instance to a dictionary
        bench_dict = self.model_dump(exclude=exclude, exclude_none=True)

        # Handle SSL certificates
        if self.ssl_certificates:
            # Export as array
            certs_array = ssl_certificates_to_toml_array(self.ssl_certificates)
            if len(certs_array) > 0:
                bench_dict['ssl_certificates'] = certs_array
            else:
                # No active certificates, remove the key
                bench_dict.pop('ssl_certificates', None)
        else:
            # No certificates at all
            bench_dict.pop('ssl_certificates', None)

        # Handle DNS providers (convert to nested tables)
        if self.dns_providers:
            dns_providers_toml = tomlkit.table()
            for provider_name, provider_config in self.dns_providers.items():
                if provider_config.exists:
                    dns_providers_toml[provider_name] = provider_config.get_toml_doc()
            if len(dns_providers_toml) > 0:
                bench_dict['dns_providers'] = dns_providers_toml
            else:
                bench_dict.pop('dns_providers', None)
        else:
            bench_dict.pop('dns_providers', None)

        # Serialize the dictionary to a TOML string
        # Create a TOML document from the dictionary
        toml_doc = tomlkit.document()

        for key, value in bench_dict.items():
            if isinstance(value, Path):
                toml_doc[key] = str(value.absolute())
            else:
                toml_doc[key] = value
        try:
            with open(path, 'w') as f:
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
        data['root_path'] = str(path)

        domain: str = data.get('name', '')
        ssl_certificates_list: List[SSLCertificate] = []

        # Parse multi-certificate format
        ssl_certificates_data = data.get('ssl_certificates', None)
        if ssl_certificates_data and isinstance(ssl_certificates_data, list):
            for cert_data in ssl_certificates_data:
                cert_domain = cert_data.get('domain', domain)
                ssl_cert = ssl_certificate_from_toml_data(cert_data, cert_domain)
                ssl_certificates_list.append(ssl_cert)

        # If no certificates found, start with empty list
        # (default cert will be created via get_primary_certificate() when needed)

        # Read alias_domains from root level only
        alias_domains_list = data.get('alias_domains', [])

        # Parse DNS providers (nested tables)
        dns_providers_dict = {}
        dns_providers_data = data.get('dns_providers', None)
        if dns_providers_data and isinstance(dns_providers_data, dict):
            for provider_name, provider_data in dns_providers_data.items():
                if isinstance(provider_data, dict):
                    dns_providers_dict[provider_name] = DNSProviderConfig.import_from_toml_doc(provider_data)

        input_data = {
            'name': data.get('name', None),
            'developer_mode': data.get('developer_mode', None),
            'admin_tools': data.get('admin_tools', False),
            'environment_type': data.get('environment_type', None),
            'root_path': data.get('root_path', None),
            'ssl_certificates': ssl_certificates_list,
            'dns_providers': dns_providers_dict if dns_providers_dict else None,
            'alias_domains': alias_domains_list,
            'upload_limit': data.get('upload_limit', '50M'),
            'admin_tools_username': data.get('admin_tools_username', None),
            'admin_tools_password': data.get('admin_tools_password', None),
            'admin_pass': data.get('admin_pass', 'admin'),
            'apps_list': data.get('apps_list', []),
            'github_token': data.get('github_token', None),
            'use_uv': data.get('use_uv', True),
            'python_version': data.get('python_version', None),
            'node_version': data.get('node_version', None),
            'db_name': data.get('db_name'),
        }

        bench_config_instance = cls(**input_data)
        return bench_config_instance

    def get_commmon_site_config_data(self, db_server_info: DatabaseServerServiceInfo) -> Dict[str, Any]:
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

    def export_to_compose_inputs(self):
        all_domains = [self.name]
        if self.alias_domains:
            all_domains.extend(self.alias_domains)
        domains_string = ','.join(all_domains)

        environment = {
            "frappe": {
                "USERID": self.userid,
                "USERGROUP": self.usergroup,
                "SERVICE_NAME": "frappe",
            },
            "nginx": {
                "SITENAME": domains_string,
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
        }
        return template_inputs
