"""
Exceptions for ComposeOrchestrator operations.

This module defines the exception hierarchy for orchestrator workflows
including deployment, teardown, health checks, and restart operations.
"""

from typing import List


class ComposeOrchestratorException(Exception):
    """Base exception for all orchestrator operations."""
    pass


class DeploymentFailedError(ComposeOrchestratorException):
    """
    Raised when deployment workflow fails.
    
    Attributes:
        step: The deployment step that failed (e.g., 'pull', 'up', 'health_check')
        underlying_error: The original exception that caused the failure
    """
    def __init__(self, message: str, step: str, underlying_error: Exception | None = None):
        self.step = step
        self.underlying_error = underlying_error
        super().__init__(message)


class TeardownFailedError(ComposeOrchestratorException):
    """
    Raised when teardown workflow fails.
    
    Attributes:
        step: The teardown step that failed (e.g., 'stop', 'down', 'cleanup')
        underlying_error: The original exception that caused the failure
    """
    def __init__(self, message: str, step: str, underlying_error: Exception | None = None):
        self.step = step
        self.underlying_error = underlying_error
        super().__init__(message)


class HealthCheckTimeoutError(ComposeOrchestratorException):
    """
    Raised when health check times out waiting for services.
    
    Attributes:
        service: The service that failed health check
        timeout: The timeout value in seconds
    """
    def __init__(self, message: str, service: str, timeout: int):
        self.service = service
        self.timeout = timeout
        super().__init__(message)


class RestartFailedError(ComposeOrchestratorException):
    """
    Raised when restart workflow fails.
    
    Attributes:
        services: List of services that failed to restart
        underlying_error: The original exception that caused the failure
    """
    def __init__(self, message: str, services: List[str], underlying_error: Exception | None = None):
        self.services = services
        self.underlying_error = underlying_error
        super().__init__(message)
