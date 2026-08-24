"""
Migration manager constants.

Centralized constants for timeouts, versions, and configuration values.
"""

from frappe_manager.migration_manager.version import Version

MINIMUM_SUPPORTED_VERSION = Version("0.18.0")

DOCKER_COMPOSE_DOWN_TIMEOUT_SECONDS = 5
MIGRATION_BENCH_STOP_TIMEOUT_SECONDS = 100

MIGRATION_WIKI_URL = "https://github.com/rtCamp/Frappe-Manager/wiki/Migrations#manual-migration-procedure"

TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS = 0.001
TIMESTAMP_COLLISION_RETRY_DELAY_MS = TIMESTAMP_COLLISION_RETRY_DELAY_SECONDS

MIGRATION_CHECK_WHITELIST_COMMANDS: list[str] = [
    "list",
    "self compose",
    "self update-images",
    "migrate",
    "bake",
    "switch",
]

MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS: list[str] = ["maintenance"]
