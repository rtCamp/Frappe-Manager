"""What `fm update` will do, decided before anything is done.

`fm update` is the one command that mutates a live bench along many independent axes at once,
and it used to decide and act in the same pass: a long `if flag:` table where each arm wrote
config, re-rendered compose and force-recreated containers on the spot. Three defects came
straight out of that shape, and all three are structural rather than careless:

* **A refused update changed things.** Validation that lives inside a worker fires mid-table.
  `fm update BENCH -e prod --upload-limit BOGUS` exited 1 having already recreated the frappe
  container as prod, while `bench_config.toml` still said dev and `fm info` still reported dev
  -- and `republish_site_map` re-injects FRAPPE_ENV from that file, so an unrelated later
  command would silently flip serving back. Three checks had already been hoisted by hand, each
  after its own incident, each with a comment explaining the same scar.
* **A no-op did work.** `-e prod` on a bench already in prod recreated the web container every
  time, because only `--restart-policy` had an equality check.
* **One run restarted the same container repeatedly.** Three arms each called
  `compose.up(force_recreate=True)`; a four-flag invocation destroyed and recreated the frappe
  container three times, measured from the docker event stream.

So planning is separated from applying, and the split is the fix: **planning may refuse and
touches nothing; applying takes a built plan and has no refusal path.** "A refused `fm update`
must change nothing" stops being a comment to remember and becomes a property of the shape --
there is nothing to undo, because nothing has run. Container work is accumulated into a SET and
performed once. An empty plan is reported and does nothing at all.

Same plan/execute seam `utils/prune.py` already uses (`plan_session_prune` ->
`execute_session_prune`), which is also what makes `--dry-run` honest here rather than a second
code path that can drift from the real one.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import typer
from pydantic import ValidationError

from frappe_manager import EnableDisableOptionsEnum
from frappe_manager.site_manager.bench_config import (
    BenchRuntime,
    FMBenchEnvType,
    RedisConfig,
    RestartPolicyEnum,
    WorkersConfig,
    extract_node_version_requirement,
    extract_python_version_requirement,
    parse_node_version_for_runtime,
    parse_python_version_for_runtime,
    requests_immutable_runtime_inputs,
    validate_node_version_compatibility,
    validate_python_version_compatibility,
)
from frappe_manager.site_manager.exceptions import BenchNotRunning
from frappe_manager.site_manager.modules.compose_shape import unsupported_redis_scheme
from frappe_manager.site_manager.site import UPLOAD_LIMIT_RE, Bench
from frappe_manager.utils.site import host_bench_dir

# Named rather than derived so the plan can be reported without a docker call.
_ALL_BENCH_SERVICES = "all bench services"


def is_immutable_update_request(
    python_version: str | None,
    node_version: str | None,
    developer_mode: EnableDisableOptionsEnum | None = None,
) -> bool:
    """True when an update requests changes that are immutable in image runtime.

    Thin adapter over ``requests_immutable_runtime_inputs``, which holds the rule beside the
    schema so ``fm create`` enforces the same one. This maps update's tri-state
    ``--developer-mode`` onto the predicate's boolean. Apps no longer reach this command -- they
    moved to ``fm apps add``, which answers the same image-runtime redirect on its own -- so this
    adapter, unlike the shared predicate, carries no ``apps`` parameter.
    """
    return requests_immutable_runtime_inputs(
        python_version=python_version,
        node_version=node_version,
        developer_mode_enable=developer_mode == EnableDisableOptionsEnum.enable,
    )



@dataclass
class UpdatePlan:
    """One `fm update` invocation's decided work.

    Built by `plan_update`, which validates and may refuse. Consumed by `apply_update`, which
    cannot refuse: by the time a plan exists, every check has passed.

    Targets are `None` when that axis is not changing -- either because the flag was not passed,
    or because the requested value already equals the recorded one. That second case is the whole
    point of planning: it is how a no-op stays a no-op instead of recreating a container to arrive
    where it already is.
    """

    bench_name: str

    # -- config targets (None == not changing) --------------------------------
    demote_to_mount: bool = False
    demotion_image: str | None = None
    db_ca: Path | None = None
    db_ca_had_previous: bool = False
    developer_mode: bool | None = None
    environment: FMBenchEnvType | None = None
    restart_policy: RestartPolicyEnum | None = None
    upload_limit: str | None = None
    python_version: str | None = None
    node_version: str | None = None
    recreate_python_env: bool = True
    # `redis` carries None both for "not changing" and for "revert to the fm-managed
    # containers", so the intent needs its own flag. Everything else on this list can say
    # "unchanged" with None because None is not a legal value for it.
    redis: RedisConfig | None = None
    redis_change: bool = False
    # Pending work on the redis the bench is leaving. Producers must be paused and the backlog
    # drained before the switch, because queued jobs do NOT move with the endpoint.
    quiesce_producers: bool = False
    queued_jobs: int = 0

    # -- derived work ---------------------------------------------------------
    regenerate_compose: bool = False
    recreate_services: set[str] = field(default_factory=set)
    recreate_everything: bool = False
    run_runtime_setup: bool = False
    restart_web: bool = False
    restart_workers: bool = False
    kill_timeout: int = 0

    # -- report material -----------------------------------------------------
    changes: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def writes_bench_config(self) -> bool:
        """Whether `apply_update` must save bench_config.toml itself.

        `--upload-limit` and the runtime demotion are excluded because each owns its own save at
        the point its other writes need it; counting them here produced a second, redundant write
        of the same file.
        """
        # `redis_change` is tested, not `redis`: clearing `[redis]` sets the target to None, which
        # an `is not None` scan reads as "not changing" and would silently skip the save, leaving
        # the recreated containers on a config the file no longer describes.
        return self.redis_change or any(
            value is not None
            for value in (
                self.db_ca,
                self.developer_mode,
                self.environment,
                self.restart_policy,
                self.python_version,
                self.node_version,
            )
        )

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to change.

        Deliberately ignores `already`: those are the axes a flag asked for and found already
        satisfied, which is precisely the case that must do nothing.
        """
        return not self.changes

    @property
    def touches_workers(self) -> bool:
        """Whether applying this plan interrupts RQ workers.

        BOTH paths count. The runtime restart cycles them through supervisor, and a
        restart-policy change recreates the whole workers project -- the more violent of the two,
        since a recreated container never gets the SIGUSR1 courtesy at all, and the one that used
        to kill in-flight jobs with no warning whatsoever.
        """
        return self.restart_workers or self.recreate_everything

    @property
    def container_work(self) -> str | None:
        """One line naming every container this plan will touch, or None for none.

        Built from the accumulated set rather than per-arm, which is what keeps a multi-flag run
        from recreating the same container once per flag.
        """
        if self.recreate_everything:
            return f"recreate {_ALL_BENCH_SERVICES}, workers and admin tools"
        parts = []
        if self.recreate_services:
            parts.append(f"recreate {', '.join(sorted(self.recreate_services))}")
        if self.restart_web:
            parts.append("restart web processes (frappe, socketio)")
        if self.restart_workers:
            parts.append("restart workers")
        return "; ".join(parts) or None

    def worker_work(self, *, drain: bool, drain_timeout: int) -> str | None:
        """How the workers will be treated, for the plan report.

        Stated up front precisely because it is the part that can destroy someone's work: a
        20-minute report job either gets waited for or gets killed, and which one it is should be
        visible from `--dry-run` rather than discovered afterwards.
        """
        if not self.touches_workers:
            return None
        if drain:
            return f"drain first (wait up to {drain_timeout}s for in-flight jobs, abort if still busy)"
        return f"interrupt in-flight jobs (SIGUSR1, force-stop after {self.kill_timeout}s)"


def _plan_redis(
    plan: UpdatePlan,
    config,
    *,
    redis_cache: str | None,
    redis_queue: str | None,
    no_redis_cache: bool,
    no_redis_queue: bool,
    no_redis: bool,
    abandon_queued: bool,
    queue_depth,
) -> None:
    """Move either redis side between an external server and fm's own per-bench container.

    `[redis]` used to be a one-way door: `fm create` wrote it and nothing changed it afterwards,
    so moving a bench onto a managed redis (or back off one) meant hand-editing
    bench_config.toml -- which was also the only path with NO validation, since create's scheme
    refusal never sees a hand edit.

    The two sides are independent, which is what the usual managed-redis shape actually wants:
    the queue moves out because it is the stateful half worth paying a provider for, while the
    cache stays local because it is throwaway and latency-sensitive. Frappe has always taken the
    two as separate config keys; requiring both was fm's restriction, not the framework's.
    """
    if no_redis and (redis_cache or redis_queue or no_redis_cache or no_redis_queue):
        raise typer.BadParameter(
            "--no-redis already covers both sides; it cannot combine with the per-side flags."
        )
    for flag, url, revert in (
        ("cache", redis_cache, no_redis_cache),
        ("queue", redis_queue, no_redis_queue),
    ):
        if url and revert:
            raise typer.BadParameter(
                f"--redis-{flag} and --no-redis-{flag} are opposite intents: one points that side at an "
                "external server, the other brings it back to fm's own container."
            )

    if not (redis_cache or redis_queue or no_redis_cache or no_redis_queue or no_redis):
        return

    current = config.redis
    # Start from what is recorded and apply only the sides this invocation names, so moving the
    # queue out does not silently drag the cache with it.
    wanted_cache = current.cache if current else None
    wanted_queue = current.queue if current else None
    if no_redis:
        wanted_cache = wanted_queue = None
    if redis_cache:
        wanted_cache = redis_cache
    if redis_queue:
        wanted_queue = redis_queue
    if no_redis_cache:
        wanted_cache = None
    if no_redis_queue:
        wanted_queue = None

    for flag, url in (("--redis-cache", redis_cache), ("--redis-queue", redis_queue)):
        problem = unsupported_redis_scheme(url) if url else None
        if problem:
            raise typer.BadParameter(f"{flag}: {problem}")

    wanted: RedisConfig | None = None
    if wanted_cache or wanted_queue:
        try:
            wanted = RedisConfig(cache=wanted_cache, queue=wanted_queue)
        except ValidationError as error:
            raise typer.BadParameter(f"--redis-cache / --redis-queue: {error.errors()[0]['msg']}") from error

    if (wanted_cache, wanted_queue) == (
        current.cache if current else None,
        current.queue if current else None,
    ):
        plan.already.append(
            "redis is already " + (_describe_redis(current) if current else "fm's own per-bench containers")
        )
        return

    plan.redis, plan.redis_change = wanted, True
    plan.changes.append(f"redis  {_describe_redis(current)} -> {_describe_redis(wanted)}")

    # The queue side is the only redis data that cannot be regenerated. The cache is derived
    # (doctype meta, table columns) and WANTS to be cold after a cutover; realtime is pub/sub with
    # nothing at rest. Pending jobs are work someone is waiting for, and they stay on the server
    # being left behind -- so rather than copy redis (whose worker-registry keys would import
    # phantom workers, and whose transport is blocked on the managed providers people move TO),
    # producers are paused and the backlog is drained to zero first. Nothing to migrate then.
    queue_side_moves = (wanted_queue or None) != (current.queue if current else None)
    if queue_side_moves and not abandon_queued:
        depth = queue_depth() if queue_depth else None
        if depth is None:
            # "Could not count" is not "empty": an unreachable endpoint or a provider that
            # restricts the commands RQ uses lands here, and silence would read as nothing to lose.
            plan.warnings.append(
                "could not count what is queued on the current redis; if jobs are pending they "
                "will be left behind -- check before proceeding, or pass --abandon-queued to accept it.",
            )
        elif sum(depth) > 0:
            pending, started = depth
            plan.quiesce_producers = True
            plan.queued_jobs = pending
            plan.changes.append(
                f"queue  {pending} pending and {started} in flight: producers pause (maintenance page, "
                "HTTP 503) until the backlog drains, since queued jobs do not move with the endpoint"
            )
        else:
            plan.already.append("the current queue is empty, so no maintenance window is needed")

    # Every process holds its redis connection from `common_site_config.json`, and a side's
    # per-bench CONTAINER appears or disappears with it (`redis_service_specs` keys each
    # container's compose profile off that side being named). So the whole bench is re-rendered
    # and recreated rather than a named subset: leaving the old container up would keep a queue
    # nothing reads, and leaving frappe up would keep it dialling the endpoint it booted with.
    plan.regenerate_compose = True
    plan.recreate_everything = True
    plan.warnings.append(
        "queued jobs and cached sessions do NOT move with the endpoint: anything still in the old "
        "queue is left there, and sessions are invalidated by a cache change.",
    )


def _describe_redis(redis: RedisConfig | None) -> str:
    """One phrase naming where each side lives, for the plan report.

    Spelled per side because a split is the interesting case: "external" alone would hide which
    half actually moved.
    """
    if redis is None:
        return "fm's own per-bench containers"
    local = "fm's own container"
    return f"cache {redis.cache or local}, queue {redis.queue or local}"


def _refuse_immutable_runtime(bench: Bench, output, runtime: BenchRuntime | None) -> None:
    if runtime == BenchRuntime.mount:
        output.display_error(
            "--runtime mount cannot combine with Python/Node/developer-mode changes in the same run: "
            f"demote first with 'fm update {bench.name} --runtime mount', then re-run with the workspace flags.",
        )
    else:
        output.display_error(
            f"{bench.name} is image runtime; code, apps, Python/Node and developer mode are immutable -- "
            "ship changes with 'fm bake' then 'fm switch', install apps with 'fm apps add', or demote to "
            f"an editable workspace first with 'fm update {bench.name} --runtime mount'. "
            "'fm update' on an image bench still changes environment, restart policy and the database CA, "
            "and APM is 'fm telemetry enable'.",
        )
    raise typer.Exit(1)


def plan_update(
    bench: Bench,
    output,
    *,
    runtime: BenchRuntime | None = None,
    environment: FMBenchEnvType | None = None,
    developer_mode: EnableDisableOptionsEnum | None = None,
    upload_limit: str | None = None,
    restart_policy: RestartPolicyEnum | None = None,
    python_version: str | None = None,
    node_version: str | None = None,
    skip_version_check: bool = False,
    recreate_python_env: bool | None = None,
    db_ca: Path | None = None,
    redis_cache: str | None = None,
    redis_queue: str | None = None,
    no_redis_cache: bool = False,
    no_redis_queue: bool = False,
    no_redis: bool = False,
    abandon_queued: bool = False,
    # Injected rather than called from here: planning must stay side-effect free and unit
    # testable, and counting a queue means execing a probe inside the bench container.
    queue_depth: Callable[[], tuple[int, int] | None] | None = None,
) -> UpdatePlan:
    """Decide the whole update, refusing anything invalid, without touching the bench.

    Every refusal in this command lives here. Nothing below writes a file, renders a compose or
    speaks to docker, so a refusal cannot leave half an update applied -- which is what made the
    old mid-table validation a data-integrity bug rather than a usability one.
    """
    plan = UpdatePlan(bench_name=bench.name)
    config = bench.bench_config

    # -- refusals ------------------------------------------------------------
    if config.runtime == BenchRuntime.image and is_immutable_update_request(
        python_version=python_version, node_version=node_version, developer_mode=developer_mode
    ):
        _refuse_immutable_runtime(bench, output, runtime)

    if runtime == BenchRuntime.image and config.runtime == BenchRuntime.mount:
        output.display_error(
            "mount -> image conversion runs through the deploy pipeline (it must migrate the site onto the "
            f"baked image) -- run 'fm switch {bench.name} REPO:TAG'.",
        )
        raise typer.Exit(1)

    database_config = None
    if db_ca is not None:
        database_config = config.get_database_config()
        if database_config is None:
            output.display_error(
                f"{bench.site_name} has no \\[database] entry in bench_config.toml: the bench uses the fm-managed "
                "'mariadb' container, whose TLS material fm owns, so there is no external CA to refresh.",
            )
            raise typer.Exit(1)

    # Hoisted out of `Bench.update_upload_limit`, which keeps its own guard for its other callers.
    # Raised from in there it fired mid-table, after an --environment change in the same run had
    # already recreated the frappe container, and before the terminal save.
    if upload_limit is not None and not UPLOAD_LIMIT_RE.match(upload_limit):
        raise typer.BadParameter(
            f"Invalid upload limit format: '{upload_limit}'. Use format like '50M' or '1G'.",
        )

    # Preserved as a precondition of planning, not of applying: every container action below needs
    # a running bench, so a plan for a stopped one could never be executed.
    if not bench.running:
        raise BenchNotRunning(bench_name=bench.name)

    if runtime == BenchRuntime.mount and config.runtime != BenchRuntime.mount:
        deploy_state = config.deploy_state
        plan.demotion_image = deploy_state.current_image if deploy_state else None
        if not plan.demotion_image:
            output.display_error("No deployed image recorded; cannot materialize the workspace.")
            raise typer.Exit(1)
        plan.demote_to_mount = True
        plan.changes.append(f"runtime  image -> mount (workspace extracted from {plan.demotion_image})")
    elif runtime is not None:
        plan.already.append(f"runtime is already '{config.runtime.value}'")

    # -- version validation, both halves before either is accepted ------------
    current_versions: dict = {}
    frappe_python_req: str | None = None
    frappe_node_req: str | None = None
    if python_version or node_version:
        frappe_app_path = host_bench_dir(bench.path) / "apps" / "frappe"
        current_versions = bench.app_manager.get_current_runtime_versions(use_run=True)

        if frappe_app_path.exists():
            if python_version:
                frappe_python_req = extract_python_version_requirement(frappe_app_path)
            if node_version:
                frappe_node_req = extract_node_version_requirement(frappe_app_path)

        if python_version and frappe_python_req and not skip_version_check:
            is_compatible, error_msg = validate_python_version_compatibility(python_version, frappe_python_req)
            if not is_compatible:
                output.change_head("Python version validation failed")
                output.print(f"Python: {current_versions.get('python') or 'not set'} -> {python_version}")
                output.print(f"Frappe requires: {frappe_python_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_python_version_for_runtime(frappe_python_req)
                if suggested:
                    output.print(f"Hint: Try --python {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

        if node_version and frappe_node_req and not skip_version_check:
            is_compatible, error_msg = validate_node_version_compatibility(node_version, frappe_node_req)
            if not is_compatible:
                output.change_head("Node version validation failed")
                output.print(f"Node: {current_versions.get('node') or 'not set'} -> {node_version}")
                output.print(f"Frappe requires: {frappe_node_req}")
                output.display_error(f"{error_msg}", emoji_code=":cross_mark:")
                suggested = parse_node_version_for_runtime(frappe_node_req)
                if suggested:
                    output.print(f"Hint: Try --node {suggested}", emoji_code=":light_bulb:")
                output.print("Use --skip-version-check to bypass this validation (not recommended)")
                raise typer.Exit(code=1)

    # -- accepted work -------------------------------------------------------
    if db_ca is not None:
        assert database_config is not None
        plan.db_ca = db_ca
        plan.db_ca_had_previous = bool(database_config.ca)
        plan.changes.append(f"database CA  reinstall from {db_ca}")

    if developer_mode is not None:
        wanted = developer_mode == EnableDisableOptionsEnum.enable
        if wanted == bool(config.developer_mode):
            plan.already.append(f"developer mode is already {'enabled' if wanted else 'disabled'}")
        else:
            plan.developer_mode = wanted
            plan.changes.append(f"developer mode  {'disabled -> enabled' if wanted else 'enabled -> disabled'}")

    if environment is not None:
        if environment == config.environment_type:
            # `--restart-policy` was the only arm with this check, so `-e prod` on a prod bench
            # recreated the web container on every invocation.
            plan.already.append(f"environment is already '{environment.value}'")
        else:
            plan.environment = environment
            plan.changes.append(f"environment  {config.environment_type.value} -> {environment.value}")
            plan.regenerate_compose = True
            plan.recreate_services.add("frappe")

    if restart_policy is not None:
        if restart_policy == config.restart_policy:
            plan.already.append(f"restart policy is already '{restart_policy.value}'")
        else:
            plan.restart_policy = restart_policy
            plan.changes.append(f"restart policy  {config.restart_policy.value} -> {restart_policy.value}")
            plan.regenerate_compose = True
            # Docker cannot change a container's restart policy in place through compose, so the
            # whole project is recreated -- including the workers and admin-tools projects, which
            # have their own compose files and would otherwise keep running under the old policy
            # while the rendered files claim the new one.
            plan.recreate_everything = True
            if restart_policy == RestartPolicyEnum.no and config.environment_type == FMBenchEnvType.prod:
                plan.warnings.append(
                    "restart policy 'no' on a production bench: containers will not auto-recover "
                    "from failures or system reboots",
                )

    if upload_limit is not None:
        normalized = upload_limit.upper()
        if normalized == (config.upload_limit or "").upper():
            plan.already.append(f"upload limit is already {normalized}")
        else:
            plan.upload_limit = normalized
            plan.changes.append(f"upload limit  {config.upload_limit} -> {normalized}")

    _plan_redis(
        plan,
        config,
        redis_cache=redis_cache,
        redis_queue=redis_queue,
        no_redis_cache=no_redis_cache,
        no_redis_queue=no_redis_queue,
        no_redis=no_redis,
        abandon_queued=abandon_queued,
        queue_depth=queue_depth,
    )

    if python_version and python_version != config.python_version:
        plan.python_version = python_version
        plan.changes.append(f"python  {config.python_version or 'not set'} -> {python_version}")
    elif python_version:
        plan.already.append(f"python is already '{python_version}'")

    if node_version and node_version != config.node_version:
        plan.node_version = node_version
        plan.changes.append(f"node  {config.node_version or 'not set'} -> {node_version}")
    elif node_version:
        plan.already.append(f"node is already '{node_version}'")

    # `--recreate-python-env` on its own, with no version change, is the explicit venv rebuild.
    # It used to be reachable only as a side effect of re-passing the version the bench already
    # had, which cost ~2 minutes and an undrained worker restart to discover; now that an
    # unchanged version is a no-op, the rebuild needs a name of its own -- and this flag already
    # meant "rebuild the venv", so it gets the standalone meaning rather than a new flag.
    version_changed = bool(plan.python_version or plan.node_version)
    rebuild_only = recreate_python_env is True and not version_changed

    if version_changed or rebuild_only:
        # Default True alongside a version change: a new interpreter needs a fresh venv.
        plan.recreate_python_env = True if recreate_python_env is None else recreate_python_env
        plan.run_runtime_setup = True
        plan.restart_web = True
        plan.restart_workers = True
        plan.kill_timeout = (config.workers or WorkersConfig()).kill_timeout
        if rebuild_only:
            plan.changes.append(
                f"runtime  rebuild the venv at the recorded python {config.python_version or 'default'} "
                f"/ node {config.node_version or 'default'}, and reinstall the apps",
            )
        if skip_version_check:
            _warn_incompatible(plan, requested=plan.python_version, req=frappe_python_req, node=False)
            _warn_incompatible(plan, requested=plan.node_version, req=frappe_node_req, node=True)

    # A full recreate already restarts every process, so the runtime-setup restarts would be a
    # second bounce of containers that came up seconds earlier.
    if plan.recreate_everything:
        plan.recreate_services.clear()
        plan.restart_web = False
        plan.restart_workers = False

    return plan


def _warn_incompatible(plan: UpdatePlan, *, requested: str | None, req: str | None, node: bool) -> None:
    """`--skip-version-check` accepted an incompatible version; say so in the plan.

    Reported rather than refused, which is what the flag is for -- but it belongs in the plan so
    `--dry-run` shows it before the venv is rebuilt, not after.
    """
    if not requested or not req:
        return
    validate = validate_node_version_compatibility if node else validate_python_version_compatibility
    compatible, _ = validate(requested, req)
    if compatible:
        return
    label = "Node" if node else "Python"
    flag = "--node" if node else "--python"
    plan.warnings.append(f"{label} {requested} is incompatible with frappe's requirement {req}")
    suggested = (parse_node_version_for_runtime if node else parse_python_version_for_runtime)(req)
    if suggested:
        plan.warnings.append(f"consider {flag} {suggested} instead")


def report_plan(output, plan: UpdatePlan, *, dry_run: bool, drain: bool, drain_timeout: int) -> None:
    """Print what the plan will do, or that it will do nothing.

    Always prints a line for an empty plan, never nothing: `fm migrate --dry-run` prints nothing
    when there is nothing pending, and its own documentation has to warn scripts not to trust the
    exit code alone. One unconditional line is cheaper than that footnote.
    """
    if plan.is_empty:
        already = f" ({'; '.join(plan.already)})" if plan.already else ""
        output.print(f"{plan.bench_name}: nothing to do{already}")
        return

    output.print(f"{'Would change' if dry_run else 'Changing'} {plan.bench_name}:")
    for change in plan.changes:
        output.print(f"  {change}", emoji_code="")
    for line in plan.already:
        output.print(f"  unchanged: {line}", emoji_code="")
    container_work = plan.container_work
    if container_work:
        output.print(f"  containers: {container_work}", emoji_code="")
    worker_work = plan.worker_work(drain=drain, drain_timeout=drain_timeout)
    if worker_work:
        output.print(f"  workers: {worker_work}", emoji_code="")
    for warning in plan.warnings:
        output.warning(warning)
