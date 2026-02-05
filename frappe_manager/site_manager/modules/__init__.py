"""
Bench modules for focused responsibility separation.

This package contains specialized modules extracted from the monolithic Bench class.
Each module handles a specific concern with clear boundaries.
"""

from frappe_manager.site_manager.modules.bench_database import BenchDatabase
from frappe_manager.site_manager.modules.bench_devtools import BenchDevTools
from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps
from frappe_manager.site_manager.modules.bench_info import BenchInfo
from frappe_manager.site_manager.modules.bench_ssl import BenchSSL
from frappe_manager.site_manager.modules.bench_supervisor import BenchSupervisor
from frappe_manager.site_manager.modules.bench_workers import BenchWorkerCoordinator

__all__ = [
    "BenchDatabase",
    "BenchDevTools",
    "BenchDockerOps",
    "BenchInfo",
    "BenchSSL",
    "BenchSupervisor",
    "BenchWorkerCoordinator",
]
