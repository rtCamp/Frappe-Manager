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
    # Configures what creating the services will do, on a host that may have none yet: gating it
    # on a migration it cannot reach is a dead end (see PRE_INSTALL_COMMANDS).
    "services ports",
    # Teardown commands are NOT listed here: they declare `tolerates_broken_host` on the command
    # itself (commands/gating.py), which the same gate honours.
]

MIGRATION_CHECK_WHITELIST_BENCH_COMMANDS: list[str] = ["maintenance"]

# The published reference, not the GitHub wiki: the wiki is unversioned and drifted from the
# behaviour this command actually has. site_url in zensical.toml is the same base.
MIGRATION_DOCS_URL = "https://opensource.rtcamp.com/Frappe-Manager/reference/migrations/"
