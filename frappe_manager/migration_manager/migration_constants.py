"""
Migration manager constants.

Centralized constants for timeouts, versions, and configuration values.
"""

from frappe_manager.migration_manager.version import Version

MINIMUM_SUPPORTED_VERSION = Version("0.18.0")

DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS = 5
MIGRATION_BENCH_STOP_TIMEOUT_SECONDS = 100

TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS = 0.001

MIGRATION_CHECK_WHITELIST_COMMANDS: list[str] = [
    "list",
    "compose",
    "self update-images",
    "migrate",
    "services migrate",
    "bake",
    "switch",
    # Disk hygiene must work on a stale install: a full disk is exactly when you cannot
    # migrate. Both prune commands are read-the-tree + delete, never touching the state
    # the migrations manage.
    "prune",
    "services prune",
]

MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS: list[str] = ["maintenance"]
