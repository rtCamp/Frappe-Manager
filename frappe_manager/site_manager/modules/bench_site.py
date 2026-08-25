"""
BenchSiteManager - Frappe Site Lifecycle Management Module

This module handles all Frappe site-related operations within a bench including
site creation, deletion, migration, reset, and status checking.

Extracted from the monolithic Bench class and BenchOperations for better
separation of concerns.
"""

import json
import shlex
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.docker import DOCKER_LINE_NOISE, DockerClient, DockerException
from frappe_manager.docker.compose_file import ComposeFile
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.services_manager.services import ServicesManager
from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import (
    BenchOperationBenchSiteCreateFailed,
    BenchOperationException,
    BenchOperationWaitForRequiredServiceFailed,
)
from frappe_manager.site_manager.modules import db_tls
from frappe_manager.site_manager.modules.db_probe import SITES_CONTAINER_ROOT, get_lock_sql, lock_refusal
from frappe_manager.utils.docker import run_command_with_exit_code
from frappe_manager.utils.helpers import get_redis_cache_addr, get_redis_queue_addr

# The bench virtualenv interpreter, the only one that can import frappe. Site directory creation
# and the direct provisioning call both need it: neither can go through `bench execute`.
BENCH_PYTHON = "/workspace/frappe-bench/env/bin/python"

# Redis' own default, shared by `redis://` and `rediss://`.
DEFAULT_REDIS_PORT = 6379

# Exit code the provisioning script uses for "the advisory lock is already held", so that refusal
# is distinguishable from any other provisioning failure. 75 is EX_TEMPFAIL: retry, nothing broke.
LOCK_UNAVAILABLE_EXIT_CODE = 75


class BenchSiteManager:
    """
    Manages Frappe site lifecycle operations within a bench.

    This module is responsible for all site-related operations including:
    - Site creation and initialization
    - Site deletion and cleanup
    - Site migration and updates
    - Site reset (reinstall)
    - Site status checking
    - Service availability checks

    The module encapsulates bench command execution and provides a clean
    interface for site management operations.

    Attributes:
        bench_name: Name of the bench/site
        bench_path: Path to the bench directory
        docker_client: Docker client for container operations
        bench_config: Bench configuration object
        services: Services manager for database/Redis access
        logger: Logger instance
        frappe_bench_dir: Path to frappe-bench directory inside container
        bench_cli_cmd: Base bench command prefix

    Example:
        >>> site_manager = BenchSiteManager(
        ...     bench_name="example.localhost",
        ...     bench_path=Path("/home/user/frappe/example.localhost"),
        ...     docker_client=docker_client,
        ...     bench_config=bench_config,
        ...     services=services,
        ... )
        >>> site_manager.create_site(admin_pass="admin")
        >>> if site_manager.is_site_created():
        ...     print("Site created successfully")
    """

    def __init__(
        self,
        bench_name: str,
        bench_path: Path,
        docker_client: DockerClient,
        bench_config: BenchConfig,
        services: ServicesManager,
        compose_file_manager: ComposeFile | None = None,
        output_handler: OutputHandler | None = None,
    ):
        """
        Initialize BenchSiteManager.

        Args:
            bench_name: Name of the bench (typically the site domain)
            bench_path: Path to the bench directory on host
            docker_client: Docker client for container operations
            bench_config: Bench configuration object
            services: Services manager providing database/Redis access
            compose_file_manager: Optional ComposeFile instance for the bench's
                docker-compose file. When provided, ``wait_for_required_services``
                will skip health checks for any required services that are
                marked as disabled in that compose file. Pass ``None`` (default)
                to always perform all health checks regardless of service profiles.
            output_handler: Optional output handler for displaying information
        """
        self.logger = get_logger(component="site_manager")
        self.bench_name = bench_name
        self.bench_path = bench_path
        self.docker_client = docker_client
        self.bench_config = bench_config
        self.services = services
        self.compose_file_manager = compose_file_manager
        self.output = output_handler or RichOutputHandler()

        self.frappe_bench_dir: Path = bench_path / "workspace" / "frappe-bench"
        self.bench_cli_cmd = ["/opt/user/.bin/bench"]

    def is_site_created(self, site_name: str | None = None) -> bool:
        """
        Check if a Frappe site exists in the bench.

        Args:
            site_name: Name of the site to check. Defaults to bench_name.

        Returns:
            True if the site exists, False otherwise.

        Example:
            >>> if site_manager.is_site_created():
            ...     print("Site already exists")
        """
        if site_name is None:
            site_name = self.bench_name

        site_path: Path = self.frappe_bench_dir / "sites" / site_name
        return site_path.exists()

    def wait_for_required_services(self, timeout: int = 120) -> None:
        """
        Wait for required services (database, Redis) to be available.

        This method checks if database and Redis services are reachable
        before proceeding with site operations. It will block until all
        services are available or timeout is reached.

        A candidate carries the compose service name and the endpoint to probe separately,
        because they are only the same string for the fm managed containers: `global-db` is both
        a compose service and the DNS name the site dials. An external endpoint has no compose
        service at all, so there is nothing to profile-check and the real host and port are
        probed directly.

        Args:
            timeout: Maximum time to wait in seconds (default: 120)

        Raises:
            BenchOperationWaitForRequiredServiceFailed: If any service is not available

        Example:
            >>> site_manager.wait_for_required_services(timeout=60)
        """
        self.output.change_head("Checking if required services are available")

        # (compose file manager, compose service, host, port); the first two are None for an
        # endpoint fm does not run.
        candidates: list[tuple[ComposeFile | None, str | None, str, int]] = []

        database_config = self.bench_config.get_database_config()
        if database_config:
            candidates.append((None, None, database_config.host, database_config.port))
        else:
            db_info = self.services.database_manager.database_server_info
            candidates.append((self.services.compose_file_manager, db_info.host, db_info.host, db_info.port))

        redis_config = self.bench_config.redis
        if redis_config:
            cache_host, cache_port = self._redis_endpoint(redis_config.cache, "cache")
            queue_host, queue_port = self._redis_endpoint(redis_config.queue, "queue")
            candidates.append((None, None, cache_host, cache_port))
            candidates.append((None, None, queue_host, queue_port))
        else:
            cache_host, cache_port = get_redis_cache_addr(self.bench_config.container_name_prefix)
            queue_host, queue_port = get_redis_queue_addr(self.bench_config.container_name_prefix)
            candidates.append((self.compose_file_manager, "redis-cache", cache_host, cache_port))
            candidates.append((self.compose_file_manager, "redis-queue", queue_host, queue_port))

        for cfm, compose_service, host, port in candidates:
            if cfm and compose_service and cfm.is_service_profile_disabled(compose_service):
                continue
            output: SubprocessOutput = self._wait_for_service(host=host, port=port, timeout=timeout)
            if output.combined:
                command_output = output.combined[-1].replace("wait-for-it: ", "")
                service_name = command_output.split(" ")[0]
                simplified_service_name = service_name.split(":")[0]
                simplified_service_name = simplified_service_name.split(CLI_DEFAULT_DELIMETER)[-1]
                self.output.print(command_output.replace(service_name, simplified_service_name), highlight=False)

    def _redis_endpoint(self, url: str, which: str) -> tuple[str, int]:
        """Host and port out of a `redis://` or `rediss://` URL, for the readiness probe.

        Only the endpoint matters here: the logical index and any inline credentials are the
        framework's business, and TLS is the server's, so a `rediss://` URL probes the same
        host and port as a plain one.
        """
        parsed = urlsplit(url)
        if not parsed.hostname:
            raise BenchOperationException(
                self.bench_name,
                f"[redis].{which} = {url!r} has no host; expected a redis://host:port/db URL.",
            )
        return parsed.hostname, parsed.port or DEFAULT_REDIS_PORT

    def _wait_for_service(self, host: str, port: int, timeout: int = 120) -> SubprocessOutput:
        """
        Wait for a specific service to be available.

        Args:
            host: Service hostname
            port: Service port
            timeout: Maximum time to wait in seconds

        Returns:
            SubprocessOutput with the wait-for-it command output

        Raises:
            BenchOperationWaitForRequiredServiceFailed: If service is not available
        """
        return cast(
            "SubprocessOutput",
            self._container_run(
                f"wait-for-it -t {timeout} {host}:{port}",
                on_failure=lambda: BenchOperationWaitForRequiredServiceFailed(
                    bench_name=self.bench_name,
                    host=host,
                    port=str(port),
                    timeout=timeout,
                ),
                capture_output=True,
            ),
        )

    def create_bench_site(self, admin_pass: str | None = None, force: bool = False) -> None:
        """
        Create a new Frappe site in the bench.

        This method runs the 'bench new-site' command with appropriate database
        credentials and configuration. It also sets the site as default and
        enables the scheduler.

        On the `global-db` container the invocation is unchanged: fm owns that server, so it
        hands new-site the root password and lets Frappe create the schema, the user and the
        grant. For a site with a `[database]` entry the schema work has already happened (either
        `provision_external_schema` or the operator did it), the endpoint and the TLS keys are
        already in `sites/<site>/site_config.json`, and no admin credential is sent at all.

        Args:
            admin_pass: Administrator password. Defaults to bench_config.admin_pass.
            force: Pass --force to new-site. Implied on the external path, where the site
                directory always exists by the time this runs.

        Raises:
            BenchOperationBenchSiteCreateFailed: If site creation fails
            BenchOperationException: If post-creation setup fails, or if the command being built
                would pair --force with schema setup on an external database

        Example:
            >>> site_manager.create_bench_site(admin_pass="secure_password")
        """
        if admin_pass is None:
            admin_pass = self.bench_config.admin_pass

        database_config = self.bench_config.get_database_config()
        site_env = self._site_env()

        # Build new-site command
        new_site_command = self.bench_cli_cmd + ["new-site"]
        if database_config:
            # External server. --db-root-password is deliberately absent: the global-db root
            # password means nothing here and must never be sent to a host fm does not own, and
            # any admin credential this flow needed was already spent by the direct
            # setup_database call, over stdin.
            #
            # --force is required and is provably inert. Required because the create pipeline
            # wrote sites/<site>/site_config.json before this runs (it is the only per-site config
            # Frappe reads, so TLS cannot be configured any other way) and `_new_site` refuses a
            # site directory that already exists. Inert because `force` reaches exactly one thing,
            # `setup_database(force, ...)`, and that call sits inside `if setup:`, which
            # --no-setup-db turns off: there is no code path from here to a DROP. Do not "fix"
            # this by dropping --no-setup-db to make --force meaningful; that pairing is what
            # destroys a schema, and the assertion below refuses to build it.
            new_site_command += ["--no-setup-db", "--force"]
            # Every value below is joined into one string that `_container_run` hands to
            # `compose.exec`, which shlex-splits it again, so a password carrying a space or a
            # quote has to be quoted here or it fragments into extra positional arguments.
            new_site_command += ["--admin-password", shlex.quote(admin_pass)]
            new_site_command += ["--verbose"]
        else:
            new_site_command += [
                "--db-root-password",
                shlex.quote(self.services.database_manager.database_server_info.password),
            ]
            if self.bench_config.db_name:
                new_site_command += ["--db-name", self.bench_config.db_name]
            new_site_command += ["--db-host", self.services.database_manager.database_server_info.host]
            new_site_command += ["--admin-password", shlex.quote(admin_pass)]
            new_site_command += ["--db-port", str(self.services.database_manager.database_server_info.port)]
            new_site_command += ["--verbose", "--mariadb-user-host-login-scope", "%"]
            if force:
                # Image runtime pre-binds sites/<site>, so `compose up` created an empty dir;
                # --force lets new-site populate that existing (empty) dir instead of aborting.
                new_site_command += ["--force"]
        new_site_command += [self.bench_name]

        # The guard, asserted on the argv rather than trusted to the branch above: never --force
        # together with schema setup against a database fm does not own.
        if database_config and "--force" in new_site_command and "--no-setup-db" not in new_site_command:
            raise BenchOperationException(
                self.bench_name,
                f"refusing to run new-site with --force and schema setup against external database "
                f"{database_config.name} on {database_config.host}: that combination drops the schema.",
            )

        new_site_command = " ".join(new_site_command)

        # Create the site
        self._container_run(
            new_site_command,
            on_failure=lambda: BenchOperationBenchSiteCreateFailed(self.bench_name),
            env=site_env,
        )

        # Set as default site
        self._container_run(
            " ".join(self.bench_cli_cmd + [f"use {self.bench_name}"]),
            on_failure=lambda: BenchOperationException(
                self.bench_name,
                f"Failed to set {self.bench_name} as default site.",
            ),
            env=site_env,
        )

        # Enable scheduler
        self._container_run(
            " ".join(self.bench_cli_cmd + [f"--site {self.bench_name} scheduler enable"]),
            on_failure=lambda: BenchOperationException(
                self.bench_name,
                f"Failed to enable {self.bench_name}'s scheduler.",
            ),
            env=site_env,
        )

    def provision_external_schema(self, *, admin_user: str, admin_password: str, site: str | None = None) -> None:
        """Create the schema, the login and the grant on an external server, by calling Frappe's
        own `setup_database` directly, under an advisory lock.

        Not `bench new-site --db-root-username`, which provisions perfectly well but only after a
        root connection made at a point where the site directory must not exist yet, so the only
        config it can read is `common_site_config.json`. `db_ssl_ca` there is bench wide, and that
        was measured breaking a sibling `global-db` site, which began failing with
        `TLS/SSL error: self-signed certificate` for as long as the key was present. `setup_database`
        is a plain function, so calling it after the site file exists removes the ordering
        constraint: the root connection reads the per site TLS keys like any other connection.

        fm still issues no DDL of its own. Frappe owns the `CREATE USER` dialect and the privilege
        list, and refuses on its own if the schema turns out to exist. The one statement fm adds is
        `GET_LOCK`, and it is issued from inside the container on the connection that provisions.

        The lock has to be taken here rather than by the caller, because an advisory lock lives
        exactly as long as the connection that took it, and a separate exec would drop it before
        this one started. `get_root_connection` caches on `frappe.local.flags.root_connection` and
        `setup_database` calls it, so the lock holder and the provisioner are one connection; its
        closing line, `root_conn.close()`, is what releases the lock, and an abort anywhere releases
        it with the process. Two `fm create` runs can otherwise both read "schema absent" and both
        proceed, which the emptiness re-check only narrows.

        `make_site_dirs` runs first and in the same interpreter: the database logger opens
        `sites/<site>/logs/database.log` on connect, so no connection can be made before that
        directory exists, and the call that creates it cannot go through `bench execute` for the
        same reason.

        Args:
            admin_user: Administrative login on the external server (its `db_root_username`).
            admin_password: That login's password. Travels on the container's stdin only.
            site: Site to provision for; defaults to this bench's own name.

        Raises:
            BenchOperationException: If the site has no `[database]` entry, if the advisory lock is
                already held, or if provisioning fails.
        """
        site = site or self.bench_name

        database_config = self.bench_config.get_database_config(site)
        if database_config is None:
            raise BenchOperationException(
                self.bench_name,
                f"refusing to provision a schema for {site}: it has no [database] entry, so its"
                " database is the global-db container, which new-site provisions itself.",
            )

        script = "\n".join(
            [
                "import sys",
                "import frappe",
                f'frappe.init({json.dumps(site)}, sites_path=".")',
                "from frappe.installer import make_site_dirs",
                "make_site_dirs()",
                f"frappe.flags.root_login = {json.dumps(admin_user)}",
                "frappe.flags.root_password = sys.stdin.readline().strip()",
                # setup_database's own first line; set here because the lock query runs before it.
                'frappe.local.session = frappe._dict({"user": "Administrator"})',
                "from frappe.database.mariadb.setup_db import get_root_connection",
                "from frappe.database import setup_database",
                f"rows = get_root_connection().sql({json.dumps(get_lock_sql(database_config.name))})",
                # GET_LOCK is 1 when taken, 0 on timeout and NULL on error.
                "if not rows or rows[0][0] != 1:",
                f"    sys.exit({LOCK_UNAVAILABLE_EXIT_CODE})",
                'setup_database(False, True, "%")',
            ],
        )

        try:
            self._container_exec_argv(
                [BENCH_PYTHON, "-c", script],
                stdin_data=f"{admin_password}\n",
                workdir=SITES_CONTAINER_ROOT,
                env=self._site_env(site),
            )
        except DockerException as e:
            if e.output.exit_code == LOCK_UNAVAILABLE_EXIT_CODE:
                raise BenchOperationException(self.bench_name, lock_refusal(database_config.name)) from e
            failure = BenchOperationException(
                self.bench_name,
                f"Failed to provision schema {database_config.name} for {site} on {database_config.host}.",
            )
            failure.set_output(e.output)
            raise failure from e

    def create_site_dirs(self, site: str | None = None) -> None:
        """Create `sites/<site>/{public,private,locks,logs}` through Frappe's own `make_site_dirs`.

        This is what attach uses instead of `new-site`: `bootstrap_database` runs unconditionally
        in `new-site`, in any form, and opens with a `DROP TABLE IF EXISTS` per core doctype, so
        no shape of that command is safe against a schema that already holds a site.

        Calling Frappe's function keeps the layout authoritative instead of fm hardcoding five
        paths, and it runs as the container user so ownership is right. It cannot go through
        `bench execute`, whose init opens `sites/<site>/logs/database.log`, one of the directories
        this creates.

        Args:
            site: Site whose directories to create; defaults to this bench's own name.

        Raises:
            BenchOperationException: If directory creation fails.
        """
        site = site or self.bench_name

        script = "\n".join(
            [
                "import frappe",
                f'frappe.init({json.dumps(site)}, sites_path=".")',
                "from frappe.installer import make_site_dirs",
                "make_site_dirs()",
            ],
        )

        self._container_exec_argv(
            [BENCH_PYTHON, "-c", script],
            workdir=SITES_CONTAINER_ROOT,
            env=self._site_env(site),
            on_failure=lambda: BenchOperationException(
                self.bench_name,
                f"Failed to create the site directories for {site}.",
            ),
        )

    def _site_env(self, site: str | None = None) -> dict[str, str]:
        """Environment for every bench command fm issues for one site; empty when the site is not
        external.

        `MYSQL_HOME` is what carries TLS to the `mariadb` CLI. `get_command`
        (`frappe/database/__init__.py`) builds the `mariadb` and `mariadb-dump` invocations from
        user, socket, host, port and password only and never reads `frappe.conf.db_ssl_*`, so
        every shell-out (the initial SQL import, restores, dump based backups, `bench mariadb`)
        would connect in plaintext and be refused with 3159 by a server that enforces TLS. The
        client reads `<MYSQL_HOME>/my.cnf`, which fm writes per site carrying that site's own CA.
        """
        site = site or self.bench_name
        if self.bench_config.get_database_config(site) is None:
            return {}
        return {"MYSQL_HOME": db_tls.site_mysql_home(site)}

    def reset_bench_site(self, admin_password: str) -> None:
        """
        Reset (reinstall) a Frappe site, wiping all data.

        This method runs 'bench reinstall' which drops and recreates the
        site's database, effectively resetting it to a fresh state.

        Only for a site on the `global-db` container fm owns. A site with a `[database]` entry is
        refused, for the reason `_handle_database_deletion` gives when `fm delete` skips the same
        schema: `reinstall` drops and recreates it, and it is not fm's to drop. The refusal is also
        what keeps the global-db root credential -- which means nothing on a host fm does not own --
        out of the argv, out of the container's process listing and off the wire.

        Args:
            admin_password: New administrator password for the reset site

        Raises:
            BenchOperationException: If the site's database lives on a server fm does not own, or
                if the site reset fails

        Warning:
            This operation is destructive and will delete all site data!

        Example:
            >>> site_manager.reset_bench_site(admin_password="new_admin_pass")
        """
        # Keyed on the site being reinstalled, which is what `--site` below names: one bench can
        # hold two sites on two different servers.
        database_config = self.bench_config.get_database_config(self.bench_name)
        if database_config is not None:
            raise BenchOperationException(
                bench_name=self.bench_name,
                message=f"Refusing to reset {self.bench_name}: its database '{database_config.name}' lives on "
                f"'{database_config.host}', a server fm does not own. `bench reinstall` drops and recreates the "
                f"schema, and that schema is not fm's to drop.",
            )

        # Only global-db sites get here, so there is no per-site MYSQL_HOME to carry: `_site_env()`
        # is empty for exactly the sites this method does not refuse.
        global_db_info = self.services.database_manager.database_server_info

        # The list is joined into one string that `_container_run` hands to `compose.exec`, which
        # shlex-splits it again, so an unquoted password carrying a space fragments into extra
        # positional arguments and one carrying an apostrophe breaks the split outright.
        reset_bench_site_command = self.bench_cli_cmd + ["--site", self.bench_name]
        reset_bench_site_command += ["reinstall", "--admin-password", shlex.quote(admin_password)]
        reset_bench_site_command += ["--db-root-username", global_db_info.user]
        reset_bench_site_command += ["--db-root-password", shlex.quote(global_db_info.password)]
        reset_bench_site_command += ["--yes"]

        reset_bench_site_command = " ".join(reset_bench_site_command)

        self._container_run(
            reset_bench_site_command,
            on_failure=lambda: BenchOperationException(
                bench_name=self.bench_name,
                message=f"Failed to reset bench site {self.bench_name}.",
            ),
        )

    def _container_run(
        self,
        command: str,
        on_failure: Callable[[], BenchOperationException] | None = None,
        capture_output: bool = False,
        user: str = "frappe",
        workdir: str = "/workspace/frappe-bench",
        service: str = "frappe",
        use_run: bool = False,
        env: dict[str, str] | None = None,
    ) -> SubprocessOutput | None:
        """
        Execute a command inside the bench container.

        This is an internal helper method that wraps docker_client.compose.exec
        or docker_client.compose.run depending on use_run parameter.

        Args:
            command: Shell command to execute
            on_failure: Builds the exception to raise if the command fails. Called only on failure
            capture_output: Whether to capture output instead of streaming
            user: User to run command as (default: frappe)
            workdir: Working directory (default: /workspace/frappe-bench)
            service: Docker service name (default: frappe)
            use_run: If True, use 'docker compose run --rm' instead of 'exec' (default: False)
            env: Extra environment variables for the command, as `--env K=V`. Use `_site_env()`
                so an external site carries its MYSQL_HOME; empty or None changes nothing.

        Returns:
            SubprocessOutput if capture_output=True, None otherwise

        Raises:
            BenchOperationException: If command fails and on_failure is provided
            DockerException: If command fails and no exception object provided
        """
        # Empty or None leaves the invocation byte identical to what fm issued before `[database]`
        # existed, which is what every bench on the global-db container still gets.
        env_options = [f"{name}={value}" for name, value in env.items()] if env else None

        # `compose.run`/`compose.exec` shlex-split the string they are handed, so the wrapping is
        # quoted rather than concatenated: `'{command}'` breaks apart the moment `command` carries
        # a quote of its own (a password, say), and the split then fails or drops characters.
        try:
            if use_run:
                wrapped_command = f"cd {workdir} && {command}"
                run_command = f"/bin/bash -c {shlex.quote(wrapped_command)}"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.run(
                            service=service,
                            command=run_command,
                            rm=True,
                            stream=False,
                            entrypoint="/exec-entrypoint.sh",
                            env=env_options,
                        ),
                    )
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.run(
                        service=service,
                        command=run_command,
                        rm=True,
                        entrypoint="/exec-entrypoint.sh",
                        env=env_options,
                        stream=True,
                    ),
                )
                self.output.live_lines(output, line_filters=DOCKER_LINE_NOISE)
            else:
                exec_command = f"/bin/bash -c {shlex.quote(command)}"
                if capture_output:
                    output = cast(
                        "SubprocessOutput",
                        self.docker_client.compose.exec(
                            service=service,
                            command=exec_command,
                            user=user,
                            workdir=workdir,
                            env=env_options,
                            stream=False,
                        ),
                    )
                    return output
                output = cast(
                    "Iterator[tuple[str, bytes]]",
                    self.docker_client.compose.exec(
                        service=service,
                        command=exec_command,
                        workdir=workdir,
                        user=user,
                        env=env_options,
                        stream=True,
                    ),
                )
                self.output.live_lines(output, line_filters=DOCKER_LINE_NOISE)

        except DockerException as e:
            if on_failure is not None:
                # Built here, not by the caller: the old signature took a ready-made exception, so
                # every call constructed one on a path that usually succeeds, and the helper then
                # mutated that caller-owned object.
                error = on_failure()
                error.set_output(e.output)
                raise error from e
            raise

    def _container_exec_argv(
        self,
        argv: list[str],
        stdin_data: str | None = None,
        on_failure: Callable[[], BenchOperationException] | None = None,
        user: str = "frappe",
        workdir: str = "/workspace/frappe-bench",
        service: str = "frappe",
        env: dict[str, str] | None = None,
    ) -> None:
        """`docker compose exec` with the command as argv, optionally with data on its stdin.

        Two things `_container_run` cannot do, both needed by the direct Frappe calls above.

        Stdin: `compose.exec` has no stdin channel, so a secret sent through `_container_run`
        would have to travel as a flag, an environment variable or a file, all of which are
        readable from outside the process. This uses `run_command_with_exit_code(input_data=...)`,
        the same path `docker login --password-stdin` takes.

        Argv: `_container_run` wraps its command in `/bin/bash -c <command>` and that string is
        then shlex split, so a python one liner still passes through a shell that will expand and
        word-split whatever the quoting did not cover. Passing argv straight through puts no shell
        between here and the interpreter at all.

        `-T` because stdin is a pipe rather than a terminal.

        Args:
            argv: Command and arguments, passed to the container verbatim.
            stdin_data: Text fed to the command's stdin, or None to inherit fm's.
            on_failure: Builds the exception to raise if the command fails. Called only on failure.
            user: User to run the command as (default: frappe).
            workdir: Working directory (default: /workspace/frappe-bench).
            service: Docker service name (default: frappe).
            env: Extra environment variables, as `--env K=V`.

        Raises:
            BenchOperationException: If the command fails and on_failure is provided.
            DockerException: If the command fails and no exception object is provided.
        """
        full_cmd = self.docker_client.compose.docker_compose_cmd + [
            "exec",
            "-T",
            "--user",
            user,
            "--workdir",
            workdir,
        ]
        for name, value in (env or {}).items():
            full_cmd += ["--env", f"{name}={value}"]
        full_cmd += [service, *argv]

        try:
            # capture_output=False is the only path that honours input_data; the container's
            # output therefore goes straight to the terminal, which is where the operator wants
            # Frappe's own "Created database" / "Granted privileges" lines anyway.
            run_command_with_exit_code(
                full_cmd,
                stream=False,
                capture_output=False,
                input_data=stdin_data.encode() if stdin_data is not None else None,
            )
        except DockerException as e:
            if on_failure is not None:
                # Built here, not by the caller: the old signature took a ready-made exception, so
                # every call constructed one on a path that usually succeeds, and the helper then
                # mutated that caller-owned object.
                error = on_failure()
                error.set_output(e.output)
                raise error from e
            raise
