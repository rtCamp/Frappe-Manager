"""
BenchOrchestrator - Complex workflow orchestration for bench operations

This module handles multi-step orchestration workflows that require coordination
between multiple modules and services. It extracts complex business logic from
the main Bench class to keep it as a thin facade.

The orchestrator encapsulates:
- Bench creation workflow
- Bench startup workflow
- Alias domain updates workflow
- Other complex multi-step operations

By centralizing orchestration logic here, we maintain separation of concerns:
- Individual modules handle specific responsibilities
- Orchestrator coordinates between modules
- Bench class remains a simple interface
"""

import copy
import json
import re
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from frappe_manager.docker import DockerException
from frappe_manager.docker.subprocess_output import SubprocessOutput
from frappe_manager.logger import get_logger
from frappe_manager.output_manager import OutputHandler
from frappe_manager.output_manager.rich_output import RichOutputHandler
from frappe_manager.site_manager.bench_config import BenchRuntime, DatabaseConfig, FMBenchEnvType, SwitchConfig
from frappe_manager.site_manager.exceptions import BenchException, BenchOperationException
from frappe_manager.site_manager.modules import db_probe, db_tls
from frappe_manager.site_manager.provisioner import provision

if TYPE_CHECKING:
    from frappe_manager.site_manager.site import Bench

# Where the bench's own interpreter lives. Stage two of the probe runs `python -c` and needs
# pymysql, which is installed into `env/`; the container's bare `python` resolves to the uv
# default interpreter and carries no database driver at all, so the probe exec puts the venv
# first on PATH and a bare `python` becomes the interpreter the site itself runs.
BENCH_VENV_BIN = "/workspace/frappe-bench/env/bin"

FRAPPE_BENCH_DIR = "/workspace/frappe-bench"

# `compose run --rm` narrates the throwaway container's lifecycle on the same stream as the
# command's own output (" Container <name> Creating", "... Created", and so on). The probe parses
# the FIRST line of a reply positionally, so an unfiltered lifecycle line is read as the query
# result: a schema that exists is reported absent, which then picks the wrong flow. Stripping
# belongs here at the docker boundary rather than in db_probe, which stays transport-agnostic.
_COMPOSE_LIFECYCLE_NOISE = re.compile(
    r"^\s*(Container|Network|Volume|Image)\s+\S+\s+"
    r"(Creating|Created|Starting|Started|Stopping|Stopped|Removing|Removed|"
    r"Recreate|Recreated|Waiting|Healthy|Running|Pulling|Pulled|Built|Building)\s*$"
)


def _strip_compose_noise(lines: list[str]) -> str:
    """Drop compose's lifecycle narration, keeping the command's real output."""
    return "\n".join(line for line in lines if not _COMPOSE_LIFECYCLE_NOISE.match(line))


class BenchOrchestrator:
    """
    Orchestrator for complex multi-step bench workflows.

    This class coordinates between multiple modules to execute complex
    workflows that require specific sequencing and error handling.

    Attributes:
        bench: Reference to parent Bench instance
        logger: Logger instance for this orchestrator

    Example:
        >>> orchestrator = BenchOrchestrator(bench_instance)
        >>> orchestrator.create_bench(is_template=False)
    """

    def __init__(self, bench: "Bench", output_handler: OutputHandler | None = None):
        """
        Initialize orchestrator with bench reference.

        Args:
            bench: Parent Bench instance that owns this orchestrator
            output_handler: Output handler for UI/logging (defaults to RichOutputHandler)
        """
        self.logger = get_logger(component="orchestrator")
        self.bench = bench
        self.output = output_handler or RichOutputHandler()

        # Decided by the external database gate between phase 1 and phase 2. None means this
        # bench has no `[database]` entry and sits on the `global-db` container, where nothing
        # in this feature runs and the create is what it has always been.
        self._external_flow: db_probe.Flow | None = None
        # The schema this run provisioned, so a later phase failing can name exactly what fm
        # created and offer to drop it. Never survives the run: a later invocation holds no
        # admin credentials and has no business dropping anything.
        self._provisioned: DatabaseConfig | None = None

    def create_bench(self, is_template_bench: bool = False) -> None:
        """
        Orchestrate the complete bench creation workflow using 5-phase approach.

        Phase 1: Prepare Structure
            - Check Docker images
            - Create directories
            - Generate docker-compose.yml with FRAPPE_ENV

        Phase 2: Initialize Bench (using docker compose run --rm)
            - Configure common_site_config.json
            - Setup supervisor configs
            - Clone apps
            - Detect Python/Node versions
            - Install dependencies
            - Build assets
            - All done in one-off containers (auto-removed)

        Phase 3: Start and Verify Bench
            - Start containers with docker compose up
            - Wait for services
            - Verify bench server responding

        Phase 4: Create Site
            - Create empty site in running container (no apps installed yet)
            - Set admin password and sync config

        Phase 5: Finalize
            - Setup workers
            - Set migration state
            - Save config
            - Verify infrastructure

        Phase 6: Install Apps
            - Install all apps to site
            - Run bench migrate
            - Graceful failure (bench remains functional if app installation fails)

        Args:
            is_template_bench: If True, creates a minimal bench without site creation

        Raises:
            Exception: If any step in the creation process fails
        """
        bench = self.bench

        bench.docker_ops.check_required_docker_images_available()

        apps_installed = self._run_creation(is_template_bench)

        if apps_installed is False:
            # The teardown was already offered (and possibly declined); what is left is to fail.
            # Falling off the end here reported exit 0 on a bench whose site was never finished.
            # Phase 6 fails for either reason, so this stays generic: the specific cause was already
            # printed as a warning, with the exact command to re-run.
            raise BenchException(
                bench.name,
                message="Bench creation failed: the site was not fully set up. See the warning above.",
            )

    def _run_creation(self, is_template_bench: bool) -> bool | None:
        """Run the creation pipeline and hand back phase 6's verdict.

        None means there is no verdict: a template bench has no phase 6, and a phase that raised
        was already reported and cleaned up after by `_handle_creation_failure`.
        """
        bench = self.bench

        try:
            self._phase1_prepare_structure()

            if is_template_bench:
                self._create_template_bench()
                return None

            if bench.bench_config.runtime == BenchRuntime.image:
                return self._create_image_bench()

            # Between phase 1 and phase 2, and the placement is the whole point. See
            # `_external_database_gate`. No-op for a bench on the `global-db` container.
            self._external_database_gate()

            if bench.bench_config.seed_image:
                self._phase2_seed_from_image()
            else:
                self._phase2_initialize_bench()
            self._phase3_start_and_verify_bench()
            self._phase4_create_site()
            self._phase5_finalize()

            apps_installed = self._skip_phase6_for_attach() if self._attaching else self._phase6_install_apps()

            if bench.bench_config.seed_image:
                # The phase-3 health probe hits the server BEFORE the site exists;
                # Frappe caches that route miss in redis -- flush it or the fresh
                # site 404s until a manual clear-cache.
                self.output.change_head("Clearing website route cache")
                clear_cmd = " ".join(bench.app_manager.bench_cli_cmd + ["--site", bench.site_name, "clear-cache"])
                try:
                    bench.app_manager._container_run(clear_cmd)
                except Exception as e:
                    self.logger.warning(f"{bench.name}: clear-cache failed: {e}")

            self._report_created_bench(apps_installed)
            return apps_installed

        except Exception as e:
            self._handle_creation_failure(e)
            return None

    def _report_created_bench(self, apps_installed: bool) -> None:
        """Describe the finished bench, or offer to tear down one whose apps never installed."""
        bench = self.bench

        if apps_installed:
            bench.info()

            if ".localhost" not in bench.primary_domain:
                self.output.print(
                    "Please note that You will have to add a host entry to your system's hosts file to access the bench locally.",
                )
        else:
            remove_status = bench.remove_bench(default_choice=False)
            if not remove_status:
                bench.info()

    def _create_image_bench(self) -> bool:
        """Bootstrap an image-mode bench from a pre-built app image.

        No provisioning: the image already carries app code, Python/Node and
        baked assets. Phase 1/5 generation projects the image shape (compose_shape),
        so this path only creates the site and installs the image's baked apps.
        """
        from frappe_manager.site_manager.bench_config import AppConfig
        from frappe_manager.site_manager.modules.transport import fetch_image
        from frappe_manager.utils.docker import host_run_cp

        bench = self.bench
        tag = bench.bench_config.deploy_state.current_tag

        # Host-side config + supervisor (mode-agnostic, no image needed).
        common_site_config_data = bench.bench_config.get_commmon_site_config_data()
        bench.set_common_bench_config(common_site_config_data)
        bench.supervisor.setup_supervisor(bench.path, force=True, use_run=True)

        # Ensure the app image (+ its nginx-assets image) is present.
        fetch_image(bench.docker_client, tag, output=self.output)

        # Seed apps.txt from the baked image and drive apps_list off it.
        apps_txt = bench.path / "workspace" / "frappe-bench" / "sites" / "apps.txt"
        host_run_cp(tag, "/workspace/frappe-bench/sites/apps.txt", str(apps_txt), bench.docker_client)
        baked = [n.strip() for n in apps_txt.read_text().splitlines() if n.strip()]
        bench.bench_config.apps_list = [AppConfig.from_string(n) for n in baked]

        # The same gate the mount runtime runs between phase 1 and phase 2, placed at the first
        # point this path can run a container at all: there is no phase 2 here, and the probe
        # goes through `compose run --rm` on the `frappe` service, whose image is the app image
        # `fetch_image` just made local. Still ahead of the containers, the site and every write,
        # and `apps_list` is already the baked set the attach parity check compares against.
        self._external_database_gate()

        # Pre-create the site dir (frappe-owned) so the per-site bind isn't auto-created
        # root-owned by `compose up`; new-site --force then populates that existing empty
        # dir. (The compose was already projected to the image shape in phase 1.)
        (bench.path / "workspace" / "frappe-bench" / "sites" / bench.site_name).mkdir(parents=True, exist_ok=True)
        self._phase3_start_and_verify_bench()
        self._phase4_create_site(force=True)

        apps_installed = self._skip_phase6_for_attach() if self._attaching else self._phase6_install_apps()

        self._phase5_finalize()

        # Workers compose was generated image-shaped in phase 5; just bring them up.
        bench.workers.docker_client.compose.up(services=[], detach=True, pull="never", wait=True, stream=False)

        self._report_created_bench(apps_installed)
        return apps_installed

    def _phase1_prepare_structure(self) -> None:
        """Phase 1: Create directories and docker-compose.yml"""
        bench = self.bench

        self.output.change_head("Creating Bench Directory")
        bench.path.mkdir(parents=True, exist_ok=True)

        self.output.change_head("Generating bench compose")
        compose_inputs = bench.bench_config.export_to_compose_inputs()

        if "environment" not in compose_inputs:
            compose_inputs["environment"] = {}

        compose_inputs["environment"]["frappe"] = compose_inputs["environment"].get("frappe", {})
        compose_inputs["environment"]["frappe"]["FRAPPE_ENV"] = bench.bench_config.environment_type.value

        bench.generate_compose(compose_inputs)

        base_image = bench.bench_config.base_image
        if base_image:
            repo, _, tag = base_image.rpartition(":")
            present = any(
                img.get("Repository") == repo and img.get("Tag") == tag for img in bench.docker_client.images()
            )
            if not present:
                self.output.change_head(f"Pulling base image {base_image}")
                bench.docker_client.pull(base_image, stream=False)
        # Seeded creates get .uv/.fnm (and everything else) from the SEED image --
        # pre-copying runtimes from the base image would version-mismatch the venv.
        bench.create_compose_dirs(
            copy_runtimes=bench.bench_config.runtime != BenchRuntime.image and not bench.bench_config.seed_image
        )

    def _phase2_initialize_bench(self) -> None:
        """Phase 2: Initialize bench using docker compose run (no persistent containers)"""
        bench = self.bench

        self.output.change_head("Initializing bench (this may take several minutes)")

        self.output.change_head("Configuring common_site_config.json")
        common_site_config_data = bench.bench_config.get_commmon_site_config_data()
        bench.set_common_bench_config(common_site_config_data)
        self.output.print("Configured common_site_config.json")

        bench.supervisor.setup_supervisor(bench.path, force=True, use_run=True)

        provision(
            bench.app_manager,
            bench.bench_config.apps_list,
            output=self.output,
            use_uv=bench.bench_config.use_uv,
            github_token=bench.bench_config.github_token,
            use_run=True,
        )

    def _phase2_seed_from_image(self) -> None:
        """Phase 2 (seeded): materialize the workspace from a baked image instead of
        provisioning -- the image already carries apps (with .git), env, runtimes and
        built assets at the exact paths the mount bind exposes, so no clone /
        dependency install / asset build is needed. ``--apps`` entries are OVERRIDES
        applied on top: cloned fresh (identity = the cloned app's Python module name,
        not the repo string), replacing the baked copy or adding a new app."""
        from frappe_manager.site_manager.bench_config import AppConfig
        from frappe_manager.site_manager.modules.transport import fetch_image
        from frappe_manager.site_manager.modules.workspace_seed import materialize_workspace_from_image
        from frappe_manager.utils.docker import host_run_cp

        bench = self.bench
        tag = bench.bench_config.seed_image
        # For seeded creates, create() stores the raw --apps entries (no frappe
        # auto-injection) -- they are override requests, not the bench app set.
        overrides = list(bench.bench_config.apps_list)

        self.output.change_head(f"Seeding workspace from image {tag}")
        fetch_image(bench.docker_client, tag, output=self.output)
        frappe_bench_dir = bench.path / "workspace" / "frappe-bench"
        materialize_workspace_from_image(bench.docker_client, tag, frappe_bench_dir, output=self.output)

        # The baked app set drives apps.txt and the per-site installs.
        apps_txt = frappe_bench_dir / "sites" / "apps.txt"
        host_run_cp(tag, "/workspace/frappe-bench/sites/apps.txt", str(apps_txt), bench.docker_client)
        baked = [n.strip() for n in apps_txt.read_text().splitlines() if n.strip()]
        bench.bench_config.apps_list = [AppConfig.from_string(n) for n in baked]
        self.output.print(f"Seeded workspace from {tag} (apps: {', '.join(baked)})")

        if overrides:
            self._apply_seed_overrides(overrides, frappe_bench_dir, baked)

        if bench.bench_config.python_version or bench.bench_config.node_version:
            # --python/--node with --seed-image: swap the seeded toolchain. The
            # setup helper no-ops when the image's venv already satisfies the
            # requirement; otherwise it recreates the venv, and every app (baked +
            # overrides) is reinstalled into it (same sequence as `fm update`).
            self.output.change_head("Applying requested Python/Node versions to the seeded runtime")
            venv_recreated = bench.app_manager.setup_python_and_node_environments(
                use_run=True, recreate_python_env=True
            )
            if venv_recreated:
                self.output.change_head("Reinstalling apps into the recreated venv")
                bench.app_manager.install_apps(
                    apps_list=bench.bench_config.apps_list,
                    github_token=bench.bench_config.github_token,
                    use_uv=bench.bench_config.use_uv,
                    skip_clone=True,
                    use_run=True,
                )

        self.output.change_head("Configuring common_site_config.json")
        common_site_config_data = bench.bench_config.get_commmon_site_config_data()
        bench.set_common_bench_config(common_site_config_data)
        bench.supervisor.setup_supervisor(bench.path, force=True, use_run=True)

    def _apply_seed_overrides(self, overrides: list, frappe_bench_dir, baked: list[str]) -> None:
        """Graft ``--apps`` overrides onto the seeded workspace.

        Delegates to :meth:`BenchAppManager.graft_apps` (shared with
        ``fm update --apps``). Fresh bench: replaced baked copies are removed,
        not stashed. Added apps need no site handling here -- phase 6 installs
        every app in ``apps_list`` to the new site.
        """
        self.bench.app_manager.graft_apps(overrides, stash=False, use_run=True)

    def _phase3_start_and_verify_bench(self) -> None:
        """Phase 3: Start containers and verify bench server responding"""
        bench = self.bench

        self.output.change_head("Starting bench services")
        bench.docker_client.compose.up(
            services=[],
            detach=True,
            pull="never",
            force_recreate=False,
        )
        self.output.print("Started bench services")

        bench.site_manager.wait_for_required_services()

        self.verify_bench_server_responding()

    def verify_bench_server_responding(self) -> None:
        """Verify bench server is working before site creation"""
        bench = self.bench

        self.output.change_head("Verifying bench server is responding")

        if not bench.supervisor.is_supervisord_running(timeout=30):
            raise Exception("Supervisord not running after 30 seconds")

        max_retries = 30

        for i in range(max_retries):
            try:
                result = bench.docker_client.compose.exec(
                    service="frappe",
                    command='curl -s -o /dev/null -w "%{http_code}" http://localhost:80',
                    user="frappe",
                    stream=False,
                )
                status_code = "".join(result.stdout).strip()

                if status_code in ["200", "404"]:
                    self.output.print("Bench server is responding correctly")
                    return

            except Exception as e:
                self.logger.debug(f"Bench server check attempt {i + 1}: {e}")

            if i < max_retries - 1:
                time.sleep(2)

        raise Exception("Bench server not responding after 60 seconds")

    def _phase4_create_site(self, force: bool = False) -> None:
        """Phase 4: Create empty site (no apps installed yet)

        Provisioning an external schema happens HERE and not at probe time. Phases 2 and 3 take
        minutes, and a failure in either would otherwise strand a schema and a login on a server
        fm cannot clean up on any later run. The cost of the split is that the probe's emptiness
        verdict is minutes stale by now, so it is re-checked immediately before the write, which
        is the check standing between `--force` and someone's data.
        """
        bench = self.bench

        if self._attaching:
            self._attach_existing_site()
            bench.sync_bench_config_configuration()
            return

        if self._external_flow is not None:
            self._recheck_external_schema()

        if self._external_flow is db_probe.Flow.provision:
            self._provision_external_schema()

        self.output.change_head(f"Creating bench site {bench.site_name}")
        bench.site_manager.create_bench_site(force=force)

        bench.set_bench_site_config({"admin_password": bench.bench_config.admin_pass})
        bench.sync_bench_config_configuration()

    # ------------------------------------------------------------------ external database

    @property
    def _attaching(self) -> bool:
        """True once the gate has decided this create attaches to an existing Frappe site.

        Every skip attach needs hangs off this one predicate, evaluated at the call site: no
        `bench new-site` command is constructed anywhere on that path, and phase 6 is not called.
        Neither is a flag checked inside those code paths, because a flag saying "do not destroy
        the data" is not a safety mechanism. `bootstrap_database` runs OUTSIDE `new-site`'s
        `if setup:` block, so `--no-setup-db` does not skip it, and it opens with a
        `DROP TABLE IF EXISTS` per core doctype: there is no shape of that command which is safe
        against a schema that already holds a site.
        """
        return self._external_flow is db_probe.Flow.attach

    def _external_database(self) -> DatabaseConfig:
        """The `[database."<site>"]` entry driving this create. Only called once the gate ran."""
        database = self.bench.bench_config.get_database_config(self.bench.site_name)
        if database is None:
            raise BenchOperationException(
                self.bench.name,
                "the external database configuration for this site went missing mid-create.",
            )
        return database

    def _external_database_gate(self) -> None:
        """Stage one of the database preflight, the flow decision, and the per-site config file.

        Returns immediately when this site has no `[database]` entry, which is every bench on the
        `global-db` container: that create runs exactly the phases it has always run, in the same
        order, and never opens a probe connection.

        Why it sits between phase 1 and phase 2. The compose file exists by now, so the probe
        reaches the server through `compose run --rm` on the bench's REAL networks rather than
        the default bridge, and a preflight that passes from the wrong network is the worst kind
        of preflight. Phase 1 has written nothing but directories and that compose file, both of
        which `_handle_creation_failure` already cleans up. And everything expensive -- cloning
        apps, installing dependencies, building assets, fetching the app image -- is still ahead,
        which is the entire reason to probe here rather than at the first connection.

        Why the `mariadb` client. It is all that exists in the image at this moment. The
        container's `python` resolves to the uv default interpreter and `pymysql` lives in
        `env/`, both of which phase 2 creates, and the image runtime has no phase 2 at all. That
        is not a compromise either: the CLI is the stack Frappe shells out to for the initial SQL
        import, for restores and for dumps, and it is the half that needs the option file.
        """
        bench = self.bench
        config = bench.bench_config
        database = config.get_database_config(bench.site_name)

        if database is None:
            return

        mysql_home = None
        if database.ca:
            self.output.change_head("Installing the database CA")
            db_tls.install_site_ca(bench.path, bench.site_name, Path(database.ca))
            mysql_home = db_tls.site_mysql_home(bench.site_name)
            self.output.print(f"Installed {database.ca} for {bench.site_name} and refreshed the CA bundle")

        attach = config.attach_existing_site
        # Only a password the OPERATOR supplied can be authenticated. On the provisioning path fm
        # mints one for a login that does not exist yet, and offering it to the probe would both
        # fail the credentials check and suppress the refusal that catches a pre-existing login
        # whose password fm does not know.
        site_password = None if config.db_password_generated else config.db_password

        self.output.change_head(f"Probing {database.host}:{database.port} from the bench container")
        result = db_probe.probe_stage_one(
            self._probe_runner(use_run=True),
            host=database.host,
            port=database.port,
            admin_user=config.db_admin_user,
            admin_password=config.db_admin_password,
            site_user=database.login_user,
            site_password=site_password,
            schema=database.name,
            mysql_home=mysql_home,
            bench_apps=tuple(app.name for app in config.apps_list),
            attach=attach,
        )
        self._report_probe_checks(result)

        decision = db_probe.decide_flow(
            result,
            attach=attach,
            credentials=db_probe.CredentialInputs(
                site_password_given=site_password is not None,
                admin_given=bool(config.db_admin_user and config.db_admin_password),
                db_name=database.name,
                db_user=database.user,
            ),
            schema=database.name,
            host=database.host,
        )
        if decision.refused:
            raise BenchOperationException(bench.name, decision.message)

        self._external_flow = decision.flow
        self.output.print(decision.message)

        # The only per-site config source Frappe reads, and it has to exist before anything
        # connects, because TLS has no CLI flag. Phase 4's direct `setup_database` call reads its
        # `db_ssl_*` keys and `rds_db` like any other connection, which is the whole reason
        # provisioning is a direct call rather than `new-site --db-root-username`. `rds_db` goes
        # in only on the provisioning path: `grant_all_privileges` is the single thing in Frappe
        # that reads it and only `setup_database` reaches it, so on adopt-empty or attach the key
        # would imply behaviour it does not have.
        self.output.change_head(f"Writing sites/{bench.site_name}/site_config.json")
        bench.create_bench_site_config(
            config.get_site_config_data(bench.site_name, provisioning=decision.flow is db_probe.Flow.provision)
        )

        if self._attaching:
            self._disable_migrate_for_attach()
            self._report_attach_warnings()

    def _probe_runner(self, *, use_run: bool) -> db_probe.Runner:
        """Run one probe command in the bench container and hand back its combined output.

        `use_run` picks `compose run --rm` over `exec`. Stage one happens between phase 1 and
        phase 2, when no container is up, and going through the bench's own compose service is
        what puts the probe on the bench's real networks. Stage two runs in phase 4, where the
        containers are already up and an exec is both cheaper and closer to how the site runs.

        The command is `shlex.quote`d rather than concatenated into the shell string: both
        `compose.run` and `compose.exec` shlex-split what they are handed, and every probe
        command carries quotes of its own (the SQL, the python source), so quoting is the only
        thing that survives the round trip intact.

        A non-zero exit is answered with the output rather than an exception, because the
        client's own `ERROR <code> (…)` line is where the entire diagnosis lives and `db_probe`
        parses it.
        """
        bench = self.bench
        # `env/bin` first so a bare `python` is the interpreter the site itself runs, the only one
        # carrying pymysql; the image's `python` is the uv default and has no database driver.
        # Harmless before the venv exists, since stage one runs no python at all.
        prefix = f"export PATH={BENCH_VENV_BIN}:$PATH; "

        def run(command: str) -> str:
            wrapped = f"/bin/bash -c {shlex.quote(prefix + command)}"
            try:
                if use_run:
                    output = cast(
                        "SubprocessOutput",
                        bench.docker_client.compose.run(
                            service="frappe",
                            command=wrapped,
                            rm=True,
                            entrypoint="/exec-entrypoint.sh",
                            stream=False,
                        ),
                    )
                else:
                    output = cast(
                        "SubprocessOutput",
                        bench.docker_client.compose.exec(
                            service="frappe",
                            command=wrapped,
                            user="frappe",
                            workdir=FRAPPE_BENCH_DIR,
                            stream=False,
                        ),
                    )
            except DockerException as e:
                return _strip_compose_noise(e.output.combined) if e.output else str(e)
            return _strip_compose_noise(output.combined)

        return run

    def _report_probe_checks(self, result: db_probe.ProbeResult) -> None:
        """Print every check individually, which is the point of running a preflight at all.

        One pass/fail verdict hides which of a dozen independent things is wrong, and nearly all
        of them are separately actionable: a server setting, a missing grant, a CA that does not
        verify, a certificate that cannot name the endpoint. Failures are not raised from here;
        `decide_flow` folds them into one refusal so the operator reads the decision in the
        probe's own words rather than fm's paraphrase.
        """
        for check in result.checks:
            if check.status is db_probe.CheckStatus.fail:
                self.output.display_error(f"{check.name}: {check.detail}")
            elif check.status is db_probe.CheckStatus.warn:
                self.output.warning(f"{check.name}: {check.detail}")
            else:
                self.output.print(f"{check.name}: {check.detail}")

    def _report_attach_warnings(self) -> None:
        """The two attach warnings the probe cannot collect, because they are not about the server.

        App parity and files-on-disk come out of `probe_stage_one` and were already printed, one
        per line, by `_report_probe_checks`. These two are facts about fm's own state: whether an
        encryption key was handed over, and whether another bench fm manages already points at
        this schema.

        None of them refuse. Attach writes nothing to the database, so every one is recoverable
        afterwards, and a create command has no business interrogating someone's data to
        second-guess them about it.
        """
        bench = self.bench
        database = self._external_database()

        if not bench.bench_config.encryption_key:
            self.output.warning(
                "no encryption key provided; if this database holds encrypted secrets (mail"
                " passwords, OAuth secrets, API tokens) Frappe will mint a new key on first use"
                " and those values will not be readable. Hashed login passwords are unaffected:"
                " they are stored key-independently."
            )

        for other in self._benches_sharing_schema(database):
            self.output.warning(
                f"bench {other} already points at {database.name} on {database.host}. Frappe"
                " prefixes its redis keys with db_name and not with the site name, so two benches"
                " on one schema share cache keys, and a restore calling delete_keys('') on either"
                " clears the other one too."
            )

    def _benches_sharing_schema(self, database: DatabaseConfig) -> list[str]:
        """Other benches fm manages whose site already points at this schema on this host."""
        from frappe_manager import CLI_BENCHES_DIRECTORY

        sharing: list[str] = []
        if not CLI_BENCHES_DIRECTORY.is_dir():
            return sharing

        for bench_dir in sorted(CLI_BENCHES_DIRECTORY.iterdir()):
            if not bench_dir.is_dir() or bench_dir.name == self.bench.name:
                continue
            sites_dir = bench_dir / "workspace" / "frappe-bench" / "sites"
            for site_config_path in sites_dir.glob("*/site_config.json"):
                try:
                    site_config = json.loads(site_config_path.read_text())
                except (OSError, ValueError) as e:
                    self.logger.debug(f"{bench_dir.name}: unreadable site config {site_config_path}: {e}")
                    continue
                if site_config.get("db_name") == database.name and site_config.get("db_host") == database.host:
                    sharing.append(bench_dir.name)
                    break
        return sharing

    def _recheck_external_schema(self) -> None:
        """Re-take the emptiness verdict immediately before phase 4 writes anything.

        The probe's answer is minutes stale by now: phases 2 and 3 sit in between and both take
        real time. This is the check standing between `--force` and someone's data, so it is
        deliberately re-run against the server rather than remembered from the gate.

        Which stack it runs on follows from which login exists. On the adopt-empty path the site
        login is already on the server, so stage two runs: pymysql out of the bench venv, reading
        the exact `db_ssl_*` shapes from the site file, which is the exact driver and config the
        site will use, and a probe that exercises only one of the two stacks can pass while the
        create fails. On the provisioning path that login does not exist yet, so stage one
        re-runs with the admin credentials instead, and Frappe's own `setup_database` connection
        moments later is the driver-level check there.
        """
        bench = self.bench
        config = bench.bench_config
        database = self._external_database()

        self.output.change_head(f"Re-checking schema {database.name} on {database.host}")

        if self._external_flow is db_probe.Flow.provision:
            result = db_probe.probe_stage_one(
                self._probe_runner(use_run=False),
                host=database.host,
                port=database.port,
                admin_user=config.db_admin_user,
                admin_password=config.db_admin_password,
                site_user=database.login_user,
                schema=database.name,
                mysql_home=db_tls.site_mysql_home(bench.site_name) if database.ca else None,
            )
        else:
            result = db_probe.probe_stage_two(
                self._probe_runner(use_run=False),
                site=bench.site_name,
                schema=database.name,
            )

        self._report_probe_checks(result)

        decision = db_probe.decide_flow(result, attach=False, schema=database.name, host=database.host)
        if decision.refused or decision.flow is not self._external_flow:
            raise BenchOperationException(
                bench.name,
                "the external schema is no longer what the preflight found minutes ago, so fm"
                f" stopped before writing anything to it. {decision.message}",
            )

    def _provision_external_schema(self) -> None:
        """Have Frappe create the schema, the login and the grant, under the advisory lock.

        fm issues no SQL of its own here. `provision_external_schema` calls Frappe's
        `setup_database` directly, so Frappe keeps ownership of the `CREATE USER` dialect and the
        privilege list it already maintains, and it takes `GET_LOCK('fm:create:<schema>', 0)` on
        the very connection that provisions. That is what closes the window the re-check above
        only narrows: two operators, or two `fm create` runs, can otherwise both read "absent"
        and both proceed. The admin password travels on the container's stdin and never reaches a
        flag, a file or a process listing.
        """
        bench = self.bench
        config = bench.bench_config
        database = self._external_database()

        if not config.db_admin_user or not config.db_admin_password:
            raise BenchOperationException(
                bench.name,
                f"schema {database.name!r} does not exist on {database.host} and no admin"
                " credentials were supplied, so fm has nothing to create it with. Pass"
                " --db-admin-user together with --db-admin-password, or point --db-name at a"
                " schema that already exists.",
            )

        self.output.change_head(f"Provisioning schema {database.name} on {database.host}")
        bench.site_manager.provision_external_schema(
            admin_user=config.db_admin_user,
            admin_password=config.db_admin_password,
            site=bench.site_name,
        )
        # Only from here does a later failure have something to offer to undo.
        self._provisioned = database
        self.output.print(f"Frappe created schema {database.name} and login {database.login_user} on {database.host}")

    def _attach_existing_site(self) -> None:
        """Build the site directory around a database that already holds a Frappe site.

        A Frappe site is a directory plus a database. The database is already there, tables and
        all, so the only thing missing is the directory, and `create_site_dirs` calls Frappe's
        own `make_site_dirs` to make it: the layout stays authoritative instead of five paths fm
        hardcodes, and it runs as the container user so the ownership is right.

        No `bench new-site` in any form, no bootstrap, no migrate, no install-app. Attach
        performs zero writes to the database and the schema is left exactly as found.

        `admin_password` is deliberately not written into `site_config.json` the way the normal
        path writes it: fm did not set this site's Administrator password and recording one would
        make `fm info` report a password that does not open the site.
        """
        bench = self.bench
        database = self._external_database()

        self.output.change_head(f"Attaching {bench.name} to the existing site in {database.name}")
        bench.site_manager.create_site_dirs(bench.site_name)
        self.output.print(
            f"Created the site directories for {bench.site_name}. Nothing was written to {database.name} on {database.host}."
        )

    def _disable_migrate_for_attach(self) -> None:
        """Turn `[switch].migrate` off as soon as the attach decision is made.

        This is what keeps attach's promise PAST the create. The key already exists and already
        has a reader, so no provenance field is needed: `false` short-circuits the `"auto"` probe,
        `schema_step` becomes false, and the maintenance window and the pre-migrate dump fall away
        with it, which also makes a zero-downtime rolling swap eligible. Left at its `True`
        default, `fm switch` would migrate data that predates fm, against an app set the parity
        check only warns about.

        It is written here, beside the site file, rather than after the pipeline. It is a setting,
        not a record of completion, and a create that dies in a later phase still leaves the
        bench directory and its `[database]` entry on disk. Writing it last meant a phase-5
        failure produced exactly the bench this flag exists to protect: attached to someone's
        data, with migrate still on. Measured, not hypothetical.
        """
        bench = self.bench
        if bench.bench_config.switch is None:
            bench.bench_config.switch = SwitchConfig(migrate=False)
        else:
            bench.bench_config.switch.migrate = False
        bench.save_bench_config()
        self.output.print("Wrote \\[switch].migrate = false to bench_config.toml")

    def _skip_phase6_for_attach(self) -> bool:
        """Phase 6 does not run on attach.

        `_phase6_install_apps` and the `_run_bench_migrate` it calls both write to the database,
        which is the one thing attach promises not to do, so neither is called. Returns True in
        place of phase 6's success flag: nothing ran, so nothing failed, and the bench is
        complete. Supervisor, the nginx site map and the workers are unaffected.
        """
        bench = self.bench

        self.output.change_head("Skipping app installation and bench migrate")
        self.output.print(
            "Attached site: phase 6 is skipped entirely, because bench install-app and bench"
            " migrate both write to the database. If the schema does need reconciling against"
            f" this bench's apps, that is yours to run when you choose: fm shell {bench.name},"
            f" then bench --site {bench.site_name} migrate."
        )

        return True

    def _offer_to_drop_provisioned_schema(self) -> None:
        """Offer to undo the one thing this run created on a server fm does not own.

        Reachable only when THIS run provisioned: fm still holds the admin credentials in memory
        and knows the schema was absent seconds before it created it, so it can name exactly what
        it made. Prompted, never automatic, and never on a later invocation, which holds neither
        the credentials nor the knowledge. Declining is the default and the non-interactive
        answer, and it leaves the schema in place for the create to simply be re-run.

        The drop goes through Frappe's own `DbManager`, the same way the provisioning went
        through Frappe's own `setup_database`, so fm still authors no SQL and the admin password
        still travels only on stdin. It runs before `remove_bench`, which takes the compose file
        and the container with it.
        """
        database = self._provisioned
        if database is None:
            return
        # One offer per run, whatever else fails on the way out.
        self._provisioned = None

        bench = self.bench
        admin_user = bench.bench_config.db_admin_user
        admin_password = bench.bench_config.db_admin_password
        if not admin_user or not admin_password:
            return

        self.output.stop()
        answer = self.output.prompt_ask(
            prompt=(
                f"This run created schema '{database.name}' and login '{database.login_user}'@'%' on"
                f" '{database.host}'. Neither existed when it started. Drop them?"
            ),
            choices=["yes", "no"],
            default="no",
        )
        if answer != "yes":
            self.output.print(f"Left schema {database.name} on {database.host} exactly as it is")
            return

        from frappe_manager.site_manager.modules.bench_site import BENCH_PYTHON

        script = "\n".join(
            [
                "import sys",
                "import frappe",
                f'frappe.init({json.dumps(bench.site_name)}, sites_path=".")',
                f"frappe.flags.root_login = {json.dumps(admin_user)}",
                "frappe.flags.root_password = sys.stdin.readline().strip()",
                'frappe.local.session = frappe._dict({"user": "Administrator"})',
                "from frappe.database.mariadb.setup_db import get_root_connection",
                "from frappe.database.db_manager import DbManager",
                "manager = DbManager(get_root_connection())",
                f"manager.drop_database({json.dumps(database.name)})",
                f'manager.delete_user({json.dumps(database.login_user)}, "%")',
            ],
        )

        try:
            bench.site_manager._container_exec_argv(
                [BENCH_PYTHON, "-c", script],
                stdin_data=f"{admin_password}\n",
                workdir=db_probe.SITES_CONTAINER_ROOT,
            )
        except Exception as e:
            self.output.display_error(
                f"Could not drop schema {database.name} on {database.host}: {e}. It is still"
                " there, and fm will not hold the admin credentials on any later run, so this"
                " one is yours to clean up by hand."
            )
            return

        self.output.print(f"Dropped schema {database.name} and login {database.login_user} on {database.host}")

    def _phase5_finalize(self) -> None:
        """Phase 5: Finalize bench infrastructure"""
        bench = self.bench

        self.output.change_head("Configuring bench workers")
        bench.sync_workers_compose(
            force_recreate=True,
            setup_supervisor=False,
            start=bench.bench_config.runtime != BenchRuntime.image,
        )
        self.output.print("Configured bench workers")

        # The site exists by now, so site_config.json and the proxy vhost can both take the limit.
        # Without this a new bench advertised its configured upload_limit and served nginx's 1M
        # default, so the value only became true after an unrelated `fm update --upload-limit`.
        self.output.change_head("Applying upload size limit")
        bench.apply_upload_limit()
        self.output.print(f"Applied upload size limit ({bench.bench_config.upload_limit})")

        from datetime import datetime

        from frappe_manager.migration_manager.version import Version
        from frappe_manager.site_manager.bench_config import MigrationState
        from frappe_manager.utils.helpers import get_current_fm_version

        current_fm_version = Version(get_current_fm_version())
        bench.bench_config.migration_state = MigrationState(
            migrated_to=str(current_fm_version.version),
            last_migration_date=datetime.now().isoformat(),
        )

        bench.save_bench_config()

        self.output.change_head("Verifying bench infrastructure")
        if not bench.is_bench_created():
            raise Exception("Bench site is inactive or unresponsive.")

        self.output.print("Bench infrastructure ready")
        self.logger.info(f"{bench.name}: Bench infrastructure verified and ready.")

    def _phase6_install_apps(self) -> bool:
        """Phase 6: Install apps to site with graceful failure handling

        Returns:
            True if apps installed successfully, False if failed
        """
        bench = self.bench

        self.output.change_head("Installing apps to site")

        try:
            bench.app_manager.install_apps_to_site()
            self.output.print("All apps installed successfully")

            self.output.change_head("Running bench migrate")
            if not self._run_bench_migrate():
                # The site schema is unmigrated, so the bench is not usable. Printing
                # "Database migrations completed" and returning True here reported a
                # successful create and exited 0 on an unmigrated schema.
                return False
        except Exception as e:
            from frappe_manager import CLI_DIR
            from frappe_manager.utils.helpers import capture_and_format_exception

            self.logger.error(f"{bench.name}: App installation to site failed: {e}\n{capture_and_format_exception()}")

            self.output.stop()
            self.output.warning(
                "App Installation Failed\n\n"
                f"Error: {e}\n\n"
                "Good News: The bench is configured correctly and running!\n"
                "- Containers are healthy ✓\n"
                "- Site created ✓\n"
                "- Workers configured ✓\n"
                "- All apps available at bench level ✓\n\n"
                "What happened?\n"
                "Some apps failed to install in the site. This is usually due to:\n"
                "- App dependency conflicts\n"
                "- Database migration errors\n"
                "- Missing Python packages\n\n"
                "How to fix:\n"
                f"1. Shell into the bench: fm shell {bench.name}\n"
                "2. Install apps manually:\n"
                f"   bench --site {bench.site_name} install-app <app_name>\n"
                "3. Check logs for specific errors:\n"
                f"   fm logs {bench.name} -f\n\n"
                f"📋 Check detailed logs at: {CLI_DIR / 'logs' / 'fm.log'}\n",
            )
            return False
        else:
            self.output.print("Database migrations completed")
            return True

    def _run_bench_migrate(self) -> bool:
        """Run bench migrate after app installation.

        Returns True when the migration ran, False when it failed, so the caller can fail the
        phase instead of announcing migrations that never completed.
        """
        bench = self.bench

        migrate_cmd = " ".join(bench.app_manager.bench_cli_cmd + ["--site", bench.site_name, "migrate"])

        try:
            bench.app_manager._container_run(
                migrate_cmd,
                on_failure=lambda: BenchOperationException(bench.name, "bench migrate failed"),
            )
        except Exception as e:
            self.logger.warning(f"{bench.name}: bench migrate failed: {e}")
            self.output.stop()
            self.output.warning(
                "⚠️  Database migration failed. The site schema is NOT migrated. You may need to run:\n"
                # This line used the same expression twice, once as the `fm shell` address (the
                # BENCH) and once as the `--site` argument (the SITE). The address form says both
                # at once, and `fm shell BENCH/SITE` exports FRAPPE_SITE so the bare `bench`
                # command inside targets that site with no second name to keep in step.
                f"  fm shell {bench.name}/{bench.site_name} -- bench migrate",
            )
            return False
        else:
            return True

    def _create_template_bench(self):
        """Create a template bench (minimal configuration without full site setup)."""
        bench = self.bench
        bench.sync_bench_common_site_config()

        from datetime import datetime

        from frappe_manager.migration_manager.version import Version
        from frappe_manager.site_manager.bench_config import MigrationState
        from frappe_manager.utils.helpers import get_current_fm_version

        current_fm_version = Version(get_current_fm_version())
        bench.bench_config.migration_state = MigrationState(
            migrated_to=str(current_fm_version.version),
            last_migration_date=datetime.now().isoformat(),
        )

        bench.save_bench_config()
        self.output.print(f"Created template bench: {bench.name}", emoji_code=":white_check_mark:")

    def _handle_creation_failure(self, exception: Exception):
        """Handle failures during bench creation with cleanup."""
        from frappe_manager import CLI_DIR
        from frappe_manager.utils.helpers import capture_and_format_exception

        bench = self.bench

        self.output.display_error(f"[fm.error][bold]Error Occured: [/bold][/fm.error]{exception}")

        exception_traceback_str = capture_and_format_exception()
        self.logger.error(f"{bench.name}: NOT WORKING\n Exception: {exception_traceback_str}")

        log_path = CLI_DIR / "logs" / "fm.log"
        error_message = [
            "There has been some error creating/starting the bench.",
            f":mag: Please check the logs at {log_path}",
        ]
        self.output.display_error("\n".join(error_message))

        self._offer_to_drop_provisioned_schema()

        if bench.exists:
            remove_status = bench.remove_bench(default_choice=False)
            if not remove_status:
                bench.info()

    def start_bench(
        self,
        force: bool = False,
        reconfigure_workers: bool = False,
        include_default_workers: bool = False,
        include_custom_workers: bool = False,
        reconfigure_supervisor: bool = False,
        reconfigure_common_site_config: bool = False,
        sync_dev_packages: bool = False,
    ):
        """
        Orchestrate the bench startup workflow.

        This method coordinates starting a bench with various configuration options:
        - Starting Docker containers
        - Reconfiguring services if requested
        - Starting admin tools
        - Starting workers
        - Syncing configuration changes

        Args:
            force: Force recreate containers
            reconfigure_workers: Regenerate worker configuration
            include_default_workers: Include default workers in reconfiguration
            include_custom_workers: Include custom workers in reconfiguration
            reconfigure_supervisor: Regenerate supervisord configuration
            reconfigure_common_site_config: Reconfigure common_site_config.json
            sync_dev_packages: Install/remove dev packages based on environment
        """
        bench = self.bench

        bench.docker_ops.check_required_docker_images_available()

        if reconfigure_common_site_config:
            self.output.print("Reconfiguring common_site_config with defaults")
            bench.sync_bench_common_site_config()

        self.output.change_head("Starting bench services")
        bench.docker_ops.start(services=[], force_recreate=force, pull="never")

        if bench.admin_tools.compose_file_manager.compose_path.exists():
            self.output.change_head("Starting admin tools services")
            if force or not bench.admin_tools.is_running():
                bench.admin_tools.enable(force_recreate_container=force)
            self.output.print("Started admin tools services")

            if not bench._is_service_running("nginx"):
                bench.docker_ops.start(services=["nginx"], force_recreate=False, pull="never")

        bench.site_manager.wait_for_required_services()

        if reconfigure_supervisor:
            self.output.print("Reconfiguring supervisord")
            bench.supervisor.setup_supervisor(bench.path, force=True)

        if reconfigure_workers:
            self.output.print("Reconfiguring workers")
            bench.sync_workers_compose(
                include_default_workers=include_default_workers,
                include_custom_workers=include_custom_workers,
            )

        if sync_dev_packages:
            self.output.print("Syncing dev packages")
            if bench.bench_config.environment_type == FMBenchEnvType.dev:
                bench.install_dev_packages()
            else:
                bench.remove_dev_packages()

        if bench.workers.compose_file_manager.exists():
            self.output.change_head("Starting bench workers services")
            bench.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=force,
            )
            self.output.print("Started bench workers services")

        bench.save_bench_config()
        self.output.print("Started bench services")

    def update_alias_domains(self, add_domains: list[str] | None = None, remove_domains: list[str] | None = None):
        """
        Update alias domains without restarting services.

        SSL certificates are NOT automatically generated for new alias domains.
        Users must explicitly add SSL certificates using: fm ssl add <bench> <domain>
        """
        bench = self.bench

        backup_aliases = copy.deepcopy(bench.bench_config.alias_domains or [])
        current_aliases = set(backup_aliases)

        add_list = add_domains if add_domains else []
        remove_list = remove_domains if remove_domains else []

        if bench.primary_domain in add_list:
            self.output.warning(f"Skipping '{bench.primary_domain}' - primary domain cannot be added as alias")
            add_list = [d for d in add_list if d != bench.primary_domain]

        if bench.primary_domain in remove_list:
            self.output.stop()
            raise ValueError(f"Cannot remove primary domain '{bench.primary_domain}'. Only alias domains can be removed.")

        added_domains = []
        for domain in add_list:
            if domain in current_aliases:
                self.output.warning(f"Domain '{domain}' is already an alias. Skipping")
            else:
                current_aliases.add(domain)
                added_domains.append(domain)

        for domain in added_domains:
            if domain.startswith("*."):
                self.output.warning(f"Wildcard domain '{domain}' requires DNS-01 challenge and Cloudflare credentials")

        removed_domains = []
        for domain in remove_list:
            if domain not in current_aliases:
                self.output.warning(f"Domain '{domain}' is not an alias. Skipping")
            else:
                current_aliases.remove(domain)
                removed_domains.append(domain)

        if not added_domains and not removed_domains:
            self.output.print("No changes to apply")
            return

        if added_domains:
            self.output.print(f"Adding aliases: {', '.join(added_domains)}")
        if removed_domains:
            self.output.print(f"Removing aliases: {', '.join(removed_domains)}")

        updated_aliases = sorted(list(current_aliases))
        bench.bench_config.alias_domains = updated_aliases

        try:
            # An alias is only real once compose and nginx carry it, so bench_config.toml is
            # written after the render succeeds. Nothing in that path reads the file back.
            self._update_alias_domains_lightweight()

            self.output.change_head("Saving configuration")
            bench.save_bench_config()
            self.output.print("Configuration saved")

            if added_domains:
                self.output.print("To add SSL certificates for new alias domains, use:", emoji_code="")
                for domain in added_domains:
                    self.output.print(f"  fm ssl add {bench.name} {domain}", emoji_code="")

        except Exception as e:
            bench.bench_config.alias_domains = backup_aliases
            # `ensure_fm_nginx_confs`, reached through generate_compose, writes the file on its
            # own when it mints an auth password, so the restore is persisted rather than left
            # as an in-memory assignment the next fm run reads straight past.
            try:
                bench.save_bench_config(print_message=False)
            except Exception as restore_error:
                self.logger.error(f"{bench.name}: failed to restore alias domains on disk: {restore_error}")
            self.output.stop()
            self.logger.error(f"Failed to update alias domains: {e}")
            raise Exception(f"Failed to update alias domains: {e}") from e

    def _update_alias_domains_lightweight(self):
        bench = self.bench

        self.output.change_head("Updating compose configuration")
        bench.generate_compose(bench.bench_config.export_to_compose_inputs())
        self.output.print("Updated compose configuration with new domains")

        self.output.change_head("Applying changes")

        nginx_config_path = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        if nginx_config_path.exists():
            nginx_config_path.unlink()

        bench.docker_client.compose.up(
            services=["nginx"],
            detach=True,
            pull="never",
            force_recreate=True,
        )

        self.output.print("Applied configuration changes")

    def _restart_services_with_updated_config(self):
        """Restart all bench services with updated configuration."""
        bench = self.bench

        self.output.change_head("Updating services")
        bench.docker_client.compose.stop(services=[], timeout=10)

        nginx_config_path = bench.path / "configs" / "nginx" / "conf" / "conf.d" / "default.conf"
        if nginx_config_path.exists():
            nginx_config_path.unlink()

        bench.generate_compose(bench.bench_config.export_to_compose_inputs())
        bench.docker_client.compose.up(
            services=[],
            detach=True,
            pull="never",
            force_recreate=True,
        )

        if bench.admin_tools.compose_file_manager.compose_path.exists():
            bench.admin_tools.enable(force_recreate_container=True)

        bench.site_manager.wait_for_required_services()

        if bench.workers.compose_file_manager.exists():
            bench.workers.docker_client.compose.up(
                services=[],
                detach=True,
                pull="never",
                force_recreate=True,
            )

        self.output.print("Services restarted with updated configuration")
