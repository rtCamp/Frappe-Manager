#!/usr/bin/env bash
# Group C -- create refusals and rollback. Group D -- lifecycle, per runtime.

# fm's own mariadb joins fm-backend-network under the container name `fm_mariadb`, the same
# network every bench's frappe container joins (docker-compose.tmpl). C1 dials it as if it were
# an external server to exercise the real auth-failure path without any host outside this one.

# D7's final check follows a cold `start --force`: nginx answers immediately but gunicorn
# binds after that, and fm's own readiness probe budgets up to 60s for it. A single
# `expect_http` right after the command returns can catch that window as a 502 with
# nothing actually wrong, so poll instead of firing one shot.
_c_wait_http() {
    local host="$1" want="$2" path="${3:-/}" deadline=$((SECONDS + 60)) code
    while true; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: $host" "http://127.0.0.1${path}")"
        [[ "$code" == "$want" ]] && return 0
        (( SECONDS >= deadline )) && { fail "http://$host$path returned $code, wanted $want (after 60s)"; return 1; }
        sleep 2
    done
}

describe C1 "create refuses a wrong --db-admin-password in preflight"
case_C1() {
    local bench=e2ec1
    local leftover="$HOME/frappe/sites/${bench}nocleanup"

    fm services start mariadb
    expect_rc 0 "services start mariadb"

    fm create "$bench" --db-host fm_mariadb --db-name "${bench}_db" \
        --db-admin-user root --db-admin-password "wrong-$(date +%s)" --remove-on-failure
    expect_rc 1 "create with wrong db-admin-password"
    expect_out '1045'
    expect_absent "$HOME/frappe/sites/$bench"
    cleanup_bench "$bench"

    # Known open defect: without --remove-on-failure the bench directory survives the failed
    # create. Measured and logged, not failed on, because that is accepted behaviour today.
    fm create "${bench}nocleanup" --db-host fm_mariadb --db-name "${bench}nocleanup_db" \
        --db-admin-user root --db-admin-password "wrong-$(date +%s)"
    expect_rc 1 "create with wrong db-admin-password, no --remove-on-failure"
    cleanup_bench "${bench}nocleanup"
    if [[ -d "$leftover" ]]; then
        local size
        size="$(du -sh "$leftover" 2>/dev/null | cut -f1)"
        log "known open defect: leftover bench directory $leftover is $size"
    else
        log "no leftover directory this run -- the known defect may be fixed"
    fi
    rm -rf "$leftover"
}

describe C2 "create refuses an unreachable --db-host within a bounded time"
case_C2() {
    local bench=e2ec2
    SECONDS=0
    fm_t 40 create "$bench" --db-host 192.0.2.1 --db-name "${bench}_db" --db-password notused123
    local elapsed=$SECONDS
    expect_rc 1 "create against TEST-NET"
    expect_out 'reachable'
    cleanup_bench "$bench"

    # Was 83s, now ~17s (notes/e2e-matrix.md C2): bounded well above the current time, far below
    # the old one, so a regression toward it fails without pinning an exact number.
    if (( elapsed > 35 )); then
        fail "unreachable db-host took ${elapsed}s to refuse (regression toward the old 83s)"
    else
        log "unreachable db-host refused in ${elapsed}s"
    fi
}

describe C3 "create refuses a duplicate bench name before any write"
case_C3() {
    local bench=e2ec3

    fm create "$bench" --environment dev
    expect_rc 0 "first create"
    cleanup_bench "$bench"

    # The duplicate check runs in the argument CALLBACK, ahead of the command body: nothing
    # below it -- not even bench_config_from_inputs -- has executed when this fires.
    fm create "$bench" --environment dev
    expect_rc 2 "duplicate create"
    expect_out 'already exists'
}

describe C4 "create --apps refuses a nonexistent repo and a non-Frappe repo with distinct messages"
case_C4() {
    local bench_missing=e2ec4missing
    local bench_notapp=e2ec4notapp

    # Caught by the repo-existence preflight before bench_service.create_bench ever runs.
    # fm probes git access over HTTPS/SSH and cannot tell "does not exist" from "private":
    # both end up here, never at the "not found" wording a branch/tag lookup would use.
    fm create "$bench_missing" --apps e2ec4-missing-org/e2ec4-does-not-exist-xyz --remove-on-failure
    expect_rc 1 "nonexistent repo"
    expect_out 'Could not access repository'
    expect_out 'repo may be private'
    expect_absent "$HOME/frappe/sites/$bench_missing"
    cleanup_bench "$bench_missing"

    # A real, public, tiny repo that exists but carries no hooks.py: the repo-existence check
    # passes, so this fails later than the first case, with a different cause.
    fm create "$bench_notapp" --apps octocat/Hello-World --remove-on-failure
    expect_rc 1 "repo that is not a Frappe app"
    expect_not_out 'not found'
    expect_absent "$HOME/frappe/sites/$bench_notapp"
    cleanup_bench "$bench_notapp"
}

describe C5 "create refuses a domain already claimed by another bench; --allow-domain-conflicts permits it"
case_C5() {
    local one=e2ec5one
    local two=e2ec5two

    fm create "$one" --environment dev
    expect_rc 0 "first bench"
    cleanup_bench "$one"

    fm create "$two" --alias-domains "${one}.localhost" --environment dev
    expect_rc 1 "conflicting alias domain"
    expect_out 'onflict'

    fm create "$two" --alias-domains "${one}.localhost" --allow-domain-conflicts --environment dev
    expect_rc 0 "conflicting alias domain with --allow-domain-conflicts"
    cleanup_bench "$two"
}

describe C6 "create under -n exits 2 on a missing argument and names the flag a refusal needs"
case_C6() {
    # Every required argument omitted: the one required parameter is the BENCH(/SITE) address.
    fm create
    expect_rc 2 "missing BENCH argument"
    expect_out 'issing argument'
    local lines
    lines="$(printf '%s\n' "$FM_OUT" | wc -l)"
    (( lines < 40 )) || fail "missing-argument output was $lines lines, looks like a full help page, not a panel"

    # A refusal that needs a named flag: --db-no-verify-hostname is meaningless without --db-ca.
    # Credentials are supplied because the missing-credentials refusal is checked FIRST and
    # would otherwise be the one that answers.
    cleanup_bench e2ec6
    fm create e2ec6 --db-host 10.0.0.1 --db-name e2ec6db --db-password nevertried --db-no-verify-hostname
    expect_rc 2 "--db-no-verify-hostname without --db-ca"
    expect_out '\-\-db-ca'
}

describe D1 "stop, start and restart survive a cycle on a mount bench"
case_D1() {
    local bench=e2ed1

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"
    expect_http "${bench}.localhost" 200

    fm stop "$bench"
    expect_rc 0 "stop"
    fm start "$bench"
    expect_rc 0 "start"
    fm restart "$bench"
    expect_rc 0 "restart"

    expect_http "${bench}.localhost" 200
    expect_container_running "fm__${bench}__frappe"
}

describe D2 "stop, start and restart survive a cycle on an image bench, with no workspace mount"
case_D2() {
    local bench=e2ed2
    local app_tag="localhost/fm${bench}:test"
    local nginx_tag="${app_tag%:*}-nginx:${app_tag##*:}"

    fm bake --apps frappe --app-image "$app_tag"
    expect_rc 0 "bake"
    expect_out 'Baked image'

    fm create "$bench" --runtime image --app-image "$app_tag" --environment dev
    expect_rc 0 "create (image runtime)"
    cleanup_bench "$bench"

    # ImageShape.binds() (compose_shape.py) drops the workspace bind for CODE -- the image
    # carries apps/ and env/, so neither exists on the host -- but data_binds() still binds
    # each site's own directory under workspace/frappe-bench/sites, so the site's data IS
    # on the host even though the code tree never is.
    local workspace="$HOME/frappe/sites/$bench/workspace/frappe-bench"
    expect_dir "$workspace/sites/${bench}.localhost"
    expect_absent "$workspace/apps"
    expect_absent "$workspace/env"
    expect_http "${bench}.localhost" 200

    fm stop "$bench"
    expect_rc 0 "stop"
    fm start "$bench"
    expect_rc 0 "start"
    fm restart "$bench"
    expect_rc 0 "restart"
    expect_http "${bench}.localhost" 200

    docker rmi -f "$app_tag" "$nginx_tag" >/dev/null 2>&1 || true
}

describe D3 "restart --rolling swaps an image bench's web tier with a minimal drop, and recovers"
case_D3() {
    local bench=e2ed3
    local app_tag="localhost/fm${bench}:test"
    local nginx_tag="${app_tag%:*}-nginx:${app_tag##*:}"

    fm bake --apps frappe --app-image "$app_tag"
    expect_rc 0 "bake"

    fm create "$bench" --runtime image --app-image "$app_tag" --environment dev
    expect_rc 0 "create (image runtime)"
    cleanup_bench "$bench"
    expect_http "${bench}.localhost" 200

    local pollfile
    pollfile="$(mktemp)"
    "$FM_BIN" --non-interactive restart "$bench" --rolling --no-workers >>"$CURRENT_LOG" 2>&1 &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        curl -s -o /dev/null -w '%{http_code}\n' --max-time 2 -H "Host: ${bench}.localhost" http://127.0.0.1/ \
            >>"$pollfile"
        sleep 0.2
    done
    wait "$pid"
    local rolling_rc=$?
    [[ $rolling_rc -eq 0 ]] || fail "restart --rolling exited $rolling_rc"
    local bad
    bad="$(grep -vc '^200$' "$pollfile" 2>/dev/null)"
    # --no-workers scopes this to the web-tier swap the --rolling flag actually claims (it is
    # documented web-only): restarting the worker containers afterward -- which a bare
    # `fm restart --rolling` still does by default -- was measured separately to cause its own
    # ~40-probe 502 burst on the web tier, on a PLAIN restart too, so it is a distinct, general
    # `fm restart` issue unrelated to the rolling swap and not part of this claim.
    # Same bound and rationale as E7's rolling-swap check (scripts/e2e/matrix/cases/e_deploy.sh):
    # a rolling swap minimises the drop, it does not remove it.
    [[ "${bad:-0}" -le 3 ]] || fail "restart --rolling dropped $bad request(s): $(sort -u "$pollfile" | tr '\n' ' ')"
    rm -f "$pollfile"
    expect_http "${bench}.localhost" 200

    docker rmi -f "$app_tag" "$nginx_tag" >/dev/null 2>&1 || true
}

describe D4 "restart --recreate/--force/--service skip or override the drain"
case_D4() {
    local bench=e2ed4

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"

    fm restart "$bench" --recreate --force --service frappe
    expect_rc 0 "restart --recreate --force --service frappe"
    expect_http "${bench}.localhost" 200

    # --force always interrupts in-flight jobs, so pairing it with --drain is a contradiction
    # restart.py refuses outright.
    fm restart "$bench" --force --drain
    expect_rc 2 "--force with --drain"
    expect_out 'force'

    # --service restarts outside the drain gate entirely, so --drain has nothing to attach to.
    fm restart "$bench" --service frappe --drain
    expect_rc 2 "--service with --drain"
    expect_out 'service'
}

describe D5 "stop --drain reaches the drain gate; --no-drain skips it"
case_D5() {
    local bench=e2ed5

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"

    # No in-flight RQ job is enqueued here: frappe.enqueue needs a dotted path RQ can re-import
    # in the worker process, and nothing stock that sleeps on demand binds through frappe.call's
    # keyword matching reliably enough to script unattended. This only proves the two code paths
    # through `fm stop` differ observably, not that a real job survives one and not the other.
    fm stop "$bench"
    expect_rc 0 "stop (default --drain)"
    expect_not_out 'WITHOUT draining'

    fm start "$bench"
    expect_rc 0 "start"

    fm stop "$bench" --no-drain
    expect_rc 0 "stop --no-drain"
    expect_out 'WITHOUT draining'
}

describe D6 "start --reconfigure-* rewrites only its own artefact"
case_D6() {
    local bench=e2ed6

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"

    local root="$HOME/frappe/sites/$bench"
    local supervisor_dir="$root/workspace/frappe-bench/config"
    local common_config="$root/workspace/frappe-bench/sites/common_site_config.json"
    local workers_compose="$root/docker-compose.workers.yml"
    expect_dir "$supervisor_dir"
    expect_file "$common_config"
    expect_file "$workers_compose"

    local common_before workers_before
    common_before="$(sha256sum "$common_config")"
    workers_before="$(sha256sum "$workers_compose")"

    fm start "$bench" --reconfigure-supervisor
    expect_rc 0 "start --reconfigure-supervisor"
    local sup_after1 common_after1 workers_after1
    sup_after1="$(find "$supervisor_dir" -name '*.fm.supervisor.conf' -exec sha256sum {} \; | sort | sha256sum)"
    common_after1="$(sha256sum "$common_config")"
    workers_after1="$(sha256sum "$workers_compose")"
    [[ "$common_after1" == "$common_before" ]] || fail "--reconfigure-supervisor touched common_site_config.json"
    [[ "$workers_after1" == "$workers_before" ]] || fail "--reconfigure-supervisor touched docker-compose.workers.yml"

    fm start "$bench" --reconfigure-common-site-config
    expect_rc 0 "start --reconfigure-common-site-config"
    local sup_after2 common_after2 workers_after2
    sup_after2="$(find "$supervisor_dir" -name '*.fm.supervisor.conf' -exec sha256sum {} \; | sort | sha256sum)"
    common_after2="$(sha256sum "$common_config")"
    workers_after2="$(sha256sum "$workers_compose")"
    [[ "$sup_after2" == "$sup_after1" ]] || fail "--reconfigure-common-site-config touched the supervisor conf"
    [[ "$workers_after2" == "$workers_after1" ]] || fail "--reconfigure-common-site-config touched docker-compose.workers.yml"

    fm start "$bench" --reconfigure-workers
    expect_rc 0 "start --reconfigure-workers"
    local sup_after3 common_after3
    sup_after3="$(find "$supervisor_dir" -name '*.fm.supervisor.conf' -exec sha256sum {} \; | sort | sha256sum)"
    common_after3="$(sha256sum "$common_config")"
    [[ "$sup_after3" == "$sup_after2" ]] || fail "--reconfigure-workers touched the supervisor conf"
    [[ "$common_after3" == "$common_after2" ]] || fail "--reconfigure-workers touched common_site_config.json"
}

describe D7 "start --sync-dev-packages reinstalls dev packages; --force starts past a dirty state"
case_D7() {
    local bench=e2ed7

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"

    fm start "$bench" --sync-dev-packages
    expect_rc 0 "start --sync-dev-packages"
    expect_out 'dev packages'

    local frappe_container="fm__${bench}__frappe"
    local id_before id_after id_forced
    id_before="$(docker inspect -f '{{.Id}}' "$frappe_container" 2>/dev/null)"

    fm start "$bench"
    expect_rc 0 "start (no force)"
    id_after="$(docker inspect -f '{{.Id}}' "$frappe_container" 2>/dev/null)"
    [[ "$id_after" == "$id_before" ]] || fail "plain start recreated the frappe container without --force"

    fm start "$bench" --force
    expect_rc 0 "start --force"
    id_forced="$(docker inspect -f '{{.Id}}' "$frappe_container" 2>/dev/null)"
    [[ "$id_forced" != "$id_before" ]] || fail "--force did not recreate the frappe container"
    _c_wait_http "${bench}.localhost" 200
}

describe D8 "start refuses a bad custom worker before rendering the supervisor template"
case_D8() {
    local bench=e2ed8

    fm create "$bench" --environment dev
    expect_rc 0 "create"
    cleanup_bench "$bench"

    local common_config="$HOME/frappe/sites/$bench/workspace/frappe-bench/sites/common_site_config.json"
    expect_file "$common_config"
    local original
    original="$(cat "$common_config")"

    # Reserved queue name: collides with the built-in "default" worker/scheduler program.
    COMMON_CONFIG_PATH="$common_config" python3 - <<'PYEOF'
import json
import os

path = os.environ["COMMON_CONFIG_PATH"]
with open(path) as fh:
    data = json.load(fh)
data["workers"] = {"default": {"timeout": 60}}
with open(path, "w") as fh:
    json.dump(data, fh)
PYEOF
    fm start "$bench" --reconfigure-supervisor
    expect_rc 1 "reserved custom worker queue name"
    expect_out 'reserved'

    # Bad name: fails the queue-name pattern before CustomWorkerSpec or the template ever sees it.
    COMMON_CONFIG_PATH="$common_config" python3 - <<'PYEOF'
import json
import os

path = os.environ["COMMON_CONFIG_PATH"]
with open(path) as fh:
    data = json.load(fh)
data["workers"] = {"bad name!": {"timeout": 60}}
with open(path, "w") as fh:
    json.dump(data, fh)
PYEOF
    fm start "$bench" --reconfigure-supervisor
    expect_rc 1 "invalid custom worker queue name"
    expect_out 'invalid'

    printf '%s' "$original" >"$common_config"
    fm start "$bench" --reconfigure-supervisor
    expect_rc 0 "restored common_site_config.json starts clean"
}
