import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from email_validator import validate_email
from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
    CLI_BENCH_CONFIG_FILE_NAME,
    STABLE_APP_BRANCH_MAPPING_LIST,
    EnableDisableOptionsEnum,
)
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.utils.site import domain_level
from frappe_manager.metadata_manager import FMConfigManager
from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES, LETSENCRYPT_PREFERRED_CHALLENGE
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.ssl_manager.letsencrypt_certificate import LetsencryptSSLCertificate


class BenchFactory:
    @classmethod
    def create_fake_bench(
        cls,
        bench_name: str,
        services: ServicesManager,
        bench_path: Path,
        compose_project: ComposeProject,
        verbose: bool = False,
    ) -> 'Bench':
        """Create a fake bench instance for deletion purposes when config doesn't exist"""
        fake_config = cls.create_bench_config(
            bench_name=bench_name,
            bench_path=bench_path,
            apps=[],
            environment=FMBenchEnvType.dev,
            developer_mode=EnableDisableOptionsEnum.disable,
            frappe_branch=STABLE_APP_BRANCH_MAPPING_LIST['frappe'],
            admin_pass='pass',
            ssl_certificate=SSLCertificate(domain=bench_name, ssl_type=SUPPORTED_SSL_TYPES.none),
        )

        # Import here to avoid circular imports
        from frappe_manager.site_manager.site import Bench

        return Bench(
            bench_path,
            bench_name,
            fake_config,
            compose_project,
            services=services,
            workers_check=False,
            verbose=verbose,
        )

    @classmethod
    def create_ssl_certificate(
        cls,
        bench_name: str,
        ssl_type: SUPPORTED_SSL_TYPES,
        fm_config_manager: FMConfigManager,
        letsencrypt_email: Optional[str] = None,
        letsencrypt_preferred_challenge: Optional[LETSENCRYPT_PREFERRED_CHALLENGE] = None,
    ) -> SSLCertificate:
        """Create appropriate SSL certificate instance based on type"""
        if ssl_type == SUPPORTED_SSL_TYPES.le:
            if not letsencrypt_preferred_challenge:
                if fm_config_manager.letsencrypt.exists:
                    letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.dns01
                else:
                    letsencrypt_preferred_challenge = LETSENCRYPT_PREFERRED_CHALLENGE.http01

            email = letsencrypt_email
            if not email:
                if fm_config_manager.letsencrypt.email != 'dummy@fm.fm':
                    email = fm_config_manager.letsencrypt.email
                else:
                    raise ValueError("Email required for Let's Encrypt certificate")

            validate_email(email, check_deliverability=False)

            return LetsencryptSSLCertificate(
                domain=bench_name,
                ssl_type=ssl_type,
                email=email,
                preferred_challenge=letsencrypt_preferred_challenge,
                api_key=fm_config_manager.letsencrypt.api_key,
                api_token=fm_config_manager.letsencrypt.api_token,
            )

        return SSLCertificate(domain=bench_name, ssl_type=SUPPORTED_SSL_TYPES.none)

    @classmethod
    def create_bench_config(
        cls,
        bench_name: str,
        bench_path: Path,
        apps: List[str],
        environment: FMBenchEnvType,
        developer_mode: EnableDisableOptionsEnum,
        frappe_branch: str,
        admin_pass: str,
        ssl_certificate: SSLCertificate,
    ) -> BenchConfig:
        """Create a new bench configuration"""
        return BenchConfig(
            name=bench_name,
            apps_list=apps,
            frappe_branch=frappe_branch,
            developer_mode=True
            if environment == FMBenchEnvType.dev
            else developer_mode == EnableDisableOptionsEnum.enable,
            admin_tools=True if environment == FMBenchEnvType.dev else False,
            admin_pass=admin_pass,
            environment_type=environment,
            root_path=bench_path / CLI_BENCH_CONFIG_FILE_NAME,
            ssl=ssl_certificate,
        )

    @classmethod
    def create_bench(
        cls,
        bench_name: str,
        services: ServicesManager,
        benches_path: Path = CLI_BENCHES_DIRECTORY,
        bench_config_file_name: str = CLI_BENCH_CONFIG_FILE_NAME,
        workers_check: bool = False,
        admin_tools_check: bool = False,
        verbose: bool = False,
    ) -> 'Bench':
        """
        Factory method to create a new Bench instance with all required components.

        Args:
            bench_name: Name of the bench to create
            services: ServicesManager instance
            benches_path: Base path for benches
            bench_config_file_name: Name of config file
            workers_check: Whether to check workers
            admin_tools_check: Whether to check admin tools
            verbose: Enable verbose output

        Returns:
            Bench: Configured bench instance
        """
        # Add domain suffix if needed
        bench_name = cls._normalize_bench_name(bench_name)

        # Setup paths
        bench_path = benches_path / bench_name
        bench_config_path = bench_path / bench_config_file_name

        # Initialize compose components
        compose_project = cls._setup_compose_project(bench_path, verbose)

        # Load bench configuration
        bench_config = BenchConfig.import_from_toml(bench_config_path)

        # Import Bench class here to avoid circular imports
        from frappe_manager.site_manager.site import Bench

        # Create bench parameters
        params: Dict[str, Any] = {
            'name': bench_name,
            'path': bench_path,
            'bench_config': bench_config,
            'compose_project': compose_project,
            'services': services,
            'workers_check': workers_check,
            'admin_tools_check': admin_tools_check,
        }

        return Bench(**params)

    @staticmethod
    def _normalize_bench_name(bench_name: str) -> str:
        """Add .localhost suffix if bench name has no domain level"""
        if domain_level(bench_name) == 0:
            return bench_name + ".localhost"
        return bench_name

    @staticmethod
    def _setup_compose_project(bench_path: Path, verbose: bool) -> ComposeProject:
        """Initialize ComposeFile and ComposeProject"""
        compose_file_manager = ComposeFile(bench_path / "docker-compose.yml")
        return ComposeProject(compose_file_manager, verbose=verbose)
