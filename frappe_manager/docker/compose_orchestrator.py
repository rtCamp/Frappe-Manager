"""
ComposeOrchestrator: High-level multi-step workflow orchestration.

This module provides optional orchestration utilities for complex Docker Compose
workflows that require multiple coordinated steps like deployment with health checks,
graceful teardowns with cleanup, and restart coordination.

Design Principles:
- Optional utility, not required for simple operations
- Contains business logic for complex workflows only
- Depends on DockerClient via dependency injection
- Handles retries, health checks, and multi-service coordination

When to use:
- Complex multi-step workflows (deploy, teardown with health checks)
- Operations requiring retry logic
- Multi-service coordination

When NOT to use:
- Simple operations (start, stop, exec, cp) → Use DockerClient directly
- Single-service commands → Use DockerClient directly
- Configuration management → Use ComposeFile directly
"""

from typing import List, Dict
from frappe_manager.docker.docker_client import DockerClient
from frappe_manager.docker.compose_orchestrator_exceptions import (
    DeploymentFailedError,
    TeardownFailedError,
    HealthCheckTimeoutError,
    RestartFailedError,
)


class ComposeOrchestrator:
    """
    Optional utility for complex multi-service workflows.
    NOT a wrapper - provides high-level orchestration logic only.
    """

    def __init__(self, docker_client: DockerClient):
        """
        Initialize orchestrator with injected DockerClient.

        Args:
            docker_client: DockerClient instance for executing Docker operations
        """
        self.docker_client = docker_client

    def deploy(
        self,
        services: List[str] | None = None,
        pull_images: bool = True,
        health_check: bool = True,
        health_check_timeout: int = 300,
        recreate: bool = False,
    ) -> None:
        """
        Deploy services with full orchestration.

        Workflow:
        1. Pull images if requested
        2. Start services (with --force-recreate if requested)
        3. Wait for health checks if requested
        4. Validate all services running

        Args:
            services: List of service names to deploy (None = all services)
            pull_images: Whether to pull latest images before starting
            health_check: Whether to wait for health checks to pass
            health_check_timeout: Maximum seconds to wait for health checks
            recreate: Whether to force recreate containers

        Raises:
            DeploymentFailedError: If any step fails with details about which step

        Example:
            >>> orchestrator.deploy(
            ...     services=['nginx', 'frappe'],
            ...     pull_images=True,
            ...     health_check=True,
            ...     health_check_timeout=300
            ... )
        """
        raise NotImplementedError("Week 1 - implementation pending")

    def teardown(
        self,
        services: List[str] | None = None,
        remove_volumes: bool = False,
        cleanup_networks: bool = True,
        force: bool = False,
    ) -> None:
        """
        Teardown services with cleanup.

        Workflow:
        1. Stop services gracefully (or forcefully if force=True)
        2. Remove containers
        3. Clean up volumes if requested
        4. Clean up networks if requested

        Args:
            services: List of service names to teardown (None = all services)
            remove_volumes: Whether to remove associated volumes
            cleanup_networks: Whether to clean up unused networks
            force: Whether to force stop containers

        Raises:
            TeardownFailedError: If any step fails with details about which step

        Example:
            >>> orchestrator.teardown(
            ...     services=['nginx'],
            ...     remove_volumes=True,
            ...     force=False
            ... )
        """
        raise NotImplementedError("Week 1 - implementation pending")

    def health_check(
        self,
        services: List[str] | None = None,
        timeout: int = 300,
        interval: int = 5,
    ) -> Dict[str, bool]:
        """
        Check health of services with retries.

        Polls service health status at regular intervals until all services
        are healthy or timeout is reached.

        Args:
            services: List of service names to check (None = all services)
            timeout: Maximum seconds to wait for services to become healthy
            interval: Seconds between health check polls

        Returns:
            Dict mapping service name -> healthy status (True/False)

        Raises:
            HealthCheckTimeoutError: If timeout exceeded before all services healthy

        Example:
            >>> status = orchestrator.health_check(
            ...     services=['frappe', 'nginx'],
            ...     timeout=300,
            ...     interval=5
            ... )
            >>> print(status)  # {'frappe': True, 'nginx': True}
        """
        raise NotImplementedError("Week 1 - implementation pending")

    def restart_with_health_check(
        self,
        services: List[str] | None = None,
        health_check_timeout: int = 300,
    ) -> None:
        """
        Restart services and wait for healthy state.

        Workflow:
        1. Stop services gracefully
        2. Start services
        3. Wait for health checks to pass
        4. Validate all services running

        Args:
            services: List of service names to restart (None = all services)
            health_check_timeout: Maximum seconds to wait for health checks

        Raises:
            RestartFailedError: If any step fails with details

        Example:
            >>> orchestrator.restart_with_health_check(
            ...     services=['frappe'],
            ...     health_check_timeout=300
            ... )
        """
        raise NotImplementedError("Week 1 - implementation pending")
