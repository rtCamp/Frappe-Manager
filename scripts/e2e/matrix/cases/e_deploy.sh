#!/usr/bin/env bash
# Group E -- bake, switch, deploy state. Group F -- update and migrate.

EF_FRAPPE_HOME="${FRAPPE_MANAGER_HOME:-$HOME/frappe}"

# fm's own venv python: guaranteed to have tomlkit (fm itself imports it to run at all),
# unlike a bare system python3, which this host may not have it on.
_ef_fm_py() {
    local dir
    dir="$(dirname "$FM_BIN")"
    if [[ -x "$dir/python3" ]]; then
        printf '%s' "$dir/python3"
    else
        printf '%s' python3
    fi
}

# _ef_toml_get <file> <dotted.path> -- the scalar at that path, or empty when absent. jq is
# not installable on this host; a hand-rolled TOML reader is too easy to misquote.
_ef_toml_get() {
    "$(_ef_fm_py)" -c '
import sys, tomlkit
doc = tomlkit.parse(open(sys.argv[1]).read())
node = doc
for key in sys.argv[2].split("."):
    if not isinstance(node, dict) or key not in node:
        print("")
        sys.exit(0)
    node = node[key]
print(node)
' "$1" "$2" 2>/dev/null
}

# _ef_toml_set_str <file> <dotted.path> <value> -- writes a string scalar, creating any
# missing intermediate table.
_ef_toml_set_str() {
    "$(_ef_fm_py)" -c '
import sys, tomlkit
path, dotted, value = sys.argv[1], sys.argv[2], sys.argv[3]
doc = tomlkit.parse(open(path).read())
node = doc
keys = dotted.split(".")
for key in keys[:-1]:
    if key not in node or not isinstance(node[key], dict):
        node[key] = tomlkit.table()
    node = node[key]
node[keys[-1]] = value
with open(path, "w") as fh:
    fh.write(tomlkit.dumps(doc))
' "$1" "$2" "$3"
}

# _ef_json_len <dotted.path> -- length of the list/dict/string on stdin at that path, or -1.
# Same convention as lib.sh's own json_get (python3, since jq is absent), kept here because
# counting a list is the one thing json_get does not do.
_ef_json_len() {
    python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except Exception:
    print(-1)
    sys.exit(0)
for key in sys.argv[1].split("."):
    if not key:
        continue
    if isinstance(doc, list) and key.isdigit():
        doc = doc[int(key)]
    elif isinstance(doc, dict) and key in doc:
        doc = doc[key]
    else:
        print(-1)
        sys.exit(0)
print(len(doc) if isinstance(doc, (list, dict, str)) else -1)
' "$1"
}

# Used by E1/E3-E7: bakes are 5-10 minutes, so the app image + nginx companion are built
# ONCE under a FIXED tag and found again with `docker image inspect` (b_create.sh's
# _b_app_image does the same) rather than a timestamped one, which would miss every time.
# No case removes the pair: like fm-e2e-shared-appimage it is deliberately kept, so every
# later case in the group -- and the next full run -- reuses the ~2.3 GB fixture for free.
# `./scripts/e2e/matrix/run.sh --clean-images` is what removes it.
EF_BAKE_BENCH="e2eebake"
EF_APP_IMAGE=""
EF_NGINX_IMAGE=""

_ef_ensure_baked_image() {
    [[ -n "$EF_APP_IMAGE" ]] && return 0
    local app_tag="localhost/fm-e2e-matrix/ebake:shared"
    local nginx_tag="${app_tag%:*}-nginx:${app_tag##*:}"
    if docker image inspect "$app_tag" >/dev/null 2>&1; then
        log "  $(c_dim "reusing cached fixture image $app_tag")"
        EF_APP_IMAGE="$app_tag"
        EF_NGINX_IMAGE="$nginx_tag"
        return 0
    fi
    fm create "$EF_BAKE_BENCH" --environment dev
    cleanup_bench "$EF_BAKE_BENCH"
    expect_rc 0 "create the bench the shared bake sources from" || return 1
    fm bake "$EF_BAKE_BENCH" --app-image "$app_tag" --source workspace
    expect_rc 0 "bake the shared e2e image" || return 1
    EF_APP_IMAGE="$app_tag"
    EF_NGINX_IMAGE="$nginx_tag"
    return 0
}

describe E1 "bake builds an app image + nginx companion from a bench's live workspace and loads both locally"
case_E1() {
    _ef_ensure_baked_image || return

    [[ -n "$(docker images -q "$EF_APP_IMAGE" 2>/dev/null)" ]] || fail "$EF_APP_IMAGE is not on the local daemon"
    [[ -n "$(docker images -q "$EF_NGINX_IMAGE" 2>/dev/null)" ]] || fail "$EF_NGINX_IMAGE is not on the local daemon"

    local apps
    apps="$(docker inspect -f '{{index .Config.Labels "fm.apps"}}' "$EF_APP_IMAGE" 2>/dev/null)"
    [[ "$apps" == *frappe* ]] || fail "fm.apps label does not record frappe: $apps"
}

describe E2 "a standalone bake needs no bench: --apps and --config both drive it, --python/--node/--base-image honoured"
case_E2() {
    # --base-image is validated before anything builds: an unpinned repo is refused.
    fm bake --apps frappe --app-image "localhost/fm-e2e-matrix/e2-badbase:1" --base-image ghcr.io/rtcamp/frappe-manager-frappe
    expect_rc 2 "an unpinned --base-image is refused before any build starts"
    expect_out 'pinned'

    local tag1 node_label
    tag1="localhost/fm-e2e-matrix/e2-apps:$(date +%s)"
    fm bake --apps frappe --app-image "$tag1" --python 3.14 --node 24
    expect_rc 0 "standalone bake driven by --apps/--python/--node, no bench involved"
    expect_out 'Baked image'
    [[ "$(docker inspect -f '{{index .Config.Labels "fm.python.version"}}' "$tag1" 2>/dev/null)" == 3.14* ]] ||
        fail "fm.python.version label does not reflect --python 3.14"
    node_label="$(docker inspect -f '{{index .Config.Labels "fm.node.version"}}' "$tag1" 2>/dev/null)"
    # The label carries fnm's own alias verbatim, which is a `v`-prefixed full version.
    [[ "$node_label" == v24* || "$node_label" == 24* ]] ||
        fail "fm.node.version label does not reflect --node 24"

    local tag2
    tag2="localhost/fm-e2e-matrix/e2-config:$(date +%s)"
    fm bake --app-image "$tag2" --config $'[[apps]]\nname = "frappe"\nrepo = "frappe/frappe"\n' --source provision
    expect_rc 0 "standalone bake driven entirely by --config: no --apps, no bench"
    expect_out 'Baked image'

    docker rmi -f "$tag1" "${tag1%:*}-nginx:${tag1##*:}" "$tag2" "${tag2%:*}-nginx:${tag2##*:}" >/dev/null 2>&1 || true
}

describe E3 "bake's --nginx-image names the companion; switch resolves it from the fm.nginx.image label, never a derived name"
case_E3() {
    _ef_ensure_baked_image || return

    local tag
    tag="localhost/fm-e2e-matrix/e3-app:$(date +%s)"
    local nginx_tag="localhost/fm-e2e-matrix/e3-side-assets:custom"
    local naive_derived="${tag%:*}-nginx:${tag##*:}"

    fm bake --apps frappe --app-image "$tag" --nginx-image "$nginx_tag"
    expect_rc 0 "standalone bake with an explicit --nginx-image"
    if [[ $FM_RC -eq 0 ]]; then
        [[ "$(docker inspect -f '{{index .Config.Labels "fm.nginx.image"}}' "$tag" 2>/dev/null)" == "$nginx_tag" ]] ||
            fail "fm.nginx.image label on $tag is not $nginx_tag"
        docker image inspect "$naive_derived" >/dev/null 2>&1 &&
            fail "the naively-derived companion $naive_derived exists locally; it would mask a derivation bug"

        fm create e2ee3 --runtime image --app-image "$EF_APP_IMAGE" --environment dev
        cleanup_bench e2ee3
        expect_rc 0 "create an image bench to switch from"
        if [[ $FM_RC -eq 0 ]]; then
            fm switch e2ee3 "$tag"
            expect_rc 0 "switch with no --nginx-image: resolve the companion from the label"
            expect_container_running "fm__e2ee3__nginx"
        fi
    fi

    docker rmi -f "$tag" "$nginx_tag" >/dev/null 2>&1 || true
}

describe E4 "switch refuses a mount-runtime bench by design, and moves an image bench to a new deploy"
case_E4() {
    _ef_ensure_baked_image || return
    local second=""

    fm create e2ee4mount --environment dev
    cleanup_bench e2ee4mount
    expect_rc 0 "create a mount bench"
    if [[ $FM_RC -eq 0 ]]; then
        fm switch e2ee4mount "$EF_APP_IMAGE"
        expect_rc 1 "switch refuses a mount-runtime bench"
        expect_out 'image runtime'

        fm create e2ee4 --runtime image --app-image "$EF_APP_IMAGE" --environment dev
        cleanup_bench e2ee4
        expect_rc 0 "create an image bench from the shared baked image"
        if [[ $FM_RC -eq 0 ]]; then
            second="${EF_APP_IMAGE%:*}:e4-$(date +%s)"
            docker tag "$EF_APP_IMAGE" "$second"

            fm switch e2ee4 "$second"
            expect_rc 0 "switch to a second deploy"

            fm info e2ee4 --json
            expect_res 'image' "$second"
            expect_res 'previous_image' "$EF_APP_IMAGE"
        fi
    fi

    [[ -n "$second" ]] && docker rmi -f "$second" >/dev/null 2>&1
}

describe E5 "switch --previous rolls back by reading the state table, and --keep N trims history"
case_E5() {
    _ef_ensure_baked_image || return
    local img_b="" img_c=""

    fm create e2ee5 --runtime image --app-image "$EF_APP_IMAGE" --environment dev
    cleanup_bench e2ee5
    expect_rc 0 "create an image bench from the shared baked image"
    if [[ $FM_RC -eq 0 ]]; then
        img_b="${EF_APP_IMAGE%:*}:e5b-$(date +%s)"
        img_c="${EF_APP_IMAGE%:*}:e5c-$(date +%s)"
        docker tag "$EF_APP_IMAGE" "$img_b"
        docker tag "$EF_APP_IMAGE" "$img_c"

        fm switch e2ee5 "$img_b"
        expect_rc 0 "first switch"
        fm switch e2ee5 "$img_c"
        expect_rc 0 "second switch"

        fm switch e2ee5 --previous
        expect_rc 0 "switch --previous"
        fm info e2ee5 --json
        # The target comes from [deployments].previous.app_image, read off bench_config.toml --
        # never from docker's own notion of "latest" or which tag was built most recently.
        expect_res 'image' "$img_b"
        expect_res 'previous_image' "$img_c"

        fm switch e2ee5 "$img_c" --keep 1
        expect_rc 0 "forward switch with --keep 1"
        fm info e2ee5 --json
        local depth
        depth="$(_ef_json_len 'result.deploys' <<<"$FM_STDOUT")"
        [[ "$depth" -eq 1 ]] || fail "deploy history has $depth row(s) after --keep 1, wanted 1"
    fi

    [[ -n "$img_b" ]] && docker rmi -f "$img_b" "$img_c" >/dev/null 2>&1
}

describe E6 "switch --restore-db needs --yes to proceed unattended; --no-migrate still skips the bench migrate"
case_E6() {
    _ef_ensure_baked_image || return
    local second=""

    fm create e2ee6 --runtime image --app-image "$EF_APP_IMAGE" --environment dev
    cleanup_bench e2ee6
    expect_rc 0 "create an image bench from the shared baked image"
    if [[ $FM_RC -eq 0 ]]; then
        second="${EF_APP_IMAGE%:*}:e6-$(date +%s)"
        docker tag "$EF_APP_IMAGE" "$second"

        fm switch e2ee6 "$second"
        expect_rc 0 "first switch records a DB backup on the deploy it leaves behind"

        fm switch e2ee6 "$EF_APP_IMAGE" --restore-db --no-migrate
        expect_rc 1 "--restore-db refuses unattended without --yes"

        fm switch e2ee6 "$EF_APP_IMAGE" --restore-db --yes --no-migrate
        expect_rc 0 "--yes answers the restore confirmation"
        expect_out 'Restored'

        fm info e2ee6 --json
        expect_res 'image' "$EF_APP_IMAGE"
        expect_res 'deploys.0.migrate_status' 'skipped'
    fi

    [[ -n "$second" ]] && docker rmi -f "$second" >/dev/null 2>&1
}

describe E7 "switch --rolling minimises dropped requests through the swap and recovers; --no-rolling recreates and makes no such claim"
case_E7() {
    _ef_ensure_baked_image || return
    local img_b="" img_c=""

    fm create e2ee7 --runtime image --app-image "$EF_APP_IMAGE" --environment dev
    cleanup_bench e2ee7
    expect_rc 0 "create an image bench from the shared baked image"
    if [[ $FM_RC -eq 0 ]]; then
        expect_http e2ee7.localhost 200

        img_b="${EF_APP_IMAGE%:*}:e7b-$(date +%s)"
        img_c="${EF_APP_IMAGE%:*}:e7c-$(date +%s)"
        docker tag "$EF_APP_IMAGE" "$img_b"
        docker tag "$EF_APP_IMAGE" "$img_c"

        local probe_log probe_pid
        probe_log="$(mktemp)"
        (
            while true; do
                curl -s -o /dev/null -w '%{http_code}\n' --max-time 2 -H "Host: e2ee7.localhost" http://127.0.0.1/ >>"$probe_log" 2>/dev/null
                sleep 0.2
            done
        ) &
        probe_pid=$!

        fm switch e2ee7 "$img_b" --rolling --no-migrate
        expect_rc 0 "rolling switch"
        expect_out 'Rolling web swap'

        kill "$probe_pid" 2>/dev/null
        wait "$probe_pid" 2>/dev/null

        local bad
        bad="$(grep -vc '^200$' "$probe_log" 2>/dev/null)"
        # A rolling swap minimises the request-drop window, it does not close it: the shared
        # proxy is a stock image whose workers re-read routing asynchronously. A manual
        # 769-probe repro of this exact swap measured exactly one dropped probe; 3 absorbs that
        # known race three times over while still failing loudly on a regression toward the
        # mechanism this matrix once measured at 2118 bad probes (docker network disconnect).
        [[ "${bad:-0}" -le 3 ]] || fail "rolling swap served $bad non-200 probe(s): $(sort -u "$probe_log" | tr '\n' ' ')"
        rm -f "$probe_log"
        expect_http e2ee7.localhost 200

        fm switch e2ee7 "$img_c" --no-rolling --no-migrate
        expect_rc 0 "non-rolling switch"
        expect_out 'recreate'
        expect_not_out 'Rolling web swap'
    fi

    [[ -n "$img_b" ]] && docker rmi -f "$img_b" "$img_c" >/dev/null 2>&1
}

describe F1 "update --dry-run plans the same change --yes later applies: environment, restart-policy, upload-limit"
case_F1() {
    fm create e2ef1 --environment dev
    cleanup_bench e2ef1
    expect_rc 0 "create" || return

    fm update e2ef1 --environment prod --developer-mode disable --restart-policy on-failure --upload-limit 50M --dry-run
    expect_rc 0 "update --dry-run"
    expect_out 'prod'
    expect_out 'on-failure'
    expect_out '50M'

    fm update e2ef1 --environment prod --developer-mode disable --restart-policy on-failure --upload-limit 50M --yes
    expect_rc 0 "apply the planned update"

    fm info e2ef1 --json
    expect_res 'environment' 'prod'
    expect_res 'restart_policy' 'on-failure'
    expect_res 'sites.0.upload_limit' '50M'
    grep -Eq 'developer_mode[[:space:]]*=[[:space:]]*false' "${EF_FRAPPE_HOME}/sites/e2ef1/bench_config.toml" ||
        fail "developer_mode not persisted as disabled"
}

describe F2 "update --python/--node recreate the venv; --recreate-python-env rebuilds it in place"
case_F2() {
    fm create e2ef2 --environment dev
    cleanup_bench e2ef2
    expect_rc 0 "create" || return

    fm update e2ef2 --python 3.14 --node 24 --yes
    expect_rc 0 "bump python/node (recreate is the default alongside --python)"

    fm shell e2ef2 -c 'python3 --version'
    expect_rc 0 "read the venv's python version"
    expect_out '3\.14'

    fm update e2ef2 --recreate-python-env --yes
    expect_rc 0 "rebuild the venv in place at the recorded versions, reinstalling every app"
    expect_http e2ef2.localhost 200
}

describe F3 "update's redis flags are per-bench; --no-redis-cache/--no-redis-queue/--no-redis return a side to fm's own container"
case_F3() {
    fm create e2ef3 --environment dev
    cleanup_bench e2ef3
    expect_rc 0 "create" || return

    fm update e2ef3 --no-redis-cache
    expect_rc 0 "--no-redis-cache is a no-op when the cache is already fm's own"
    fm update e2ef3 --no-redis-queue
    expect_rc 0 "--no-redis-queue is a no-op when the queue is already fm's own"
    fm update e2ef3 --no-redis
    expect_rc 0 "--no-redis brings both sides back at once"

    if [[ -z "${FM_E2E_EXT_REDIS_URL:-}" ]]; then
        log "  $(c_dim 'FM_E2E_EXT_REDIS_URL not set; skipping --redis-cache/--redis-queue against a real external redis')"
        return
    fi

    fm update e2ef3 --redis-cache "${FM_E2E_EXT_REDIS_URL}/0" --redis-queue "${FM_E2E_EXT_REDIS_URL}/1"
    expect_rc 0 "point both sides at the external redis"
    grep -q '\[redis\]' "${EF_FRAPPE_HOME}/sites/e2ef3/bench_config.toml" || fail "[redis] table not written"

    fm update e2ef3 --no-redis-queue --abandon-queued
    expect_rc 0 "--abandon-queued moves the queue back without waiting for the backlog to drain"
}

describe F4 "fm migrate refuses while the services tier is behind, naming fm services migrate"
case_F4() {
    local cfg="${EF_FRAPPE_HOME}/fm_config.toml"
    local backup
    backup="$(mktemp)"
    cp "$cfg" "$backup"

    fm create e2ef4 --environment dev
    cleanup_bench e2ef4
    expect_rc 0 "create" || { rm -f "$backup"; return; }

    local real_version
    real_version="$(_ef_toml_get "$cfg" schema.version)"
    # Only the GATE's own read needs to see a behind services tier: migrate's check runs
    # before it ever opens the migration executor, so this never touches real infrastructure.
    _ef_toml_set_str "$cfg" schema.version "0.18.0"

    fm migrate e2ef4
    expect_rc 1 "migrate while the services tier is behind"
    expect_out 'services migrate'

    cp "$backup" "$cfg"
    rm -f "$backup"
    [[ "$(_ef_toml_get "$cfg" schema.version)" == "$real_version" ]] || fail "ledger not restored to v$real_version"
}

# Mirrors scripts/e2e/fm.sh's InjectFailingMigration/RemoveFailingMigration: a real migration
# class dropped into the installed package, version-stamped at the current release and named
# to sort after every shipped migration ('zz'), so it is the only one that ever fails. No fm
# code knows this file exists.
_ef_migrations_dir() {
    "$(_ef_fm_py)" -c 'import frappe_manager.migration_manager.migrations as m; print(m.__path__[0])' 2>/dev/null
}

_ef_inject_failing_migration() {
    local dir
    dir="$(_ef_migrations_dir)"
    [[ -n "$dir" ]] || return 1
    cat >"$dir/migrate_zz_e2e_rollback_drill.py" <<'PYEOF'
"""E2E matrix F5: an always-failing migration, injected the same way
scripts/e2e/fm.sh's rollback drill does."""

from packaging.version import Version as PV

from frappe_manager.migration_manager.migration_base import MigrationBase
from frappe_manager.migration_manager.version import Version
from frappe_manager.utils.helpers import get_current_fm_version


class MigrationE2ERollbackDrill(MigrationBase):
    version = Version(PV(get_current_fm_version()).base_version)

    def up(self):
        raise RuntimeError("e2e matrix F5: injected failure")

    def down(self):
        self.output.print("e2e matrix F5: nothing to undo for the injected failure")
PYEOF
}

_ef_remove_failing_migration() {
    local dir
    dir="$(_ef_migrations_dir)"
    [[ -n "$dir" ]] && rm -f "$dir/migrate_zz_e2e_rollback_drill.py"
}

describe F5 "services migrate's --on-failure rollback/halt honour --skip-*-backup under --rerun; migrate all --exclude-bench targets correctly"
case_F5() {
    local cfg="${EF_FRAPPE_HOME}/fm_config.toml"
    local before
    before="$(_ef_toml_get "$cfg" schema.version)"

    _ef_inject_failing_migration || { fail "could not inject the rollback-drill migration"; return; }

    # Rollback: the failed run undoes itself and leaves the ledger where it found it. --rerun
    # is required here: this host is already fully migrated, and without it the command would
    # see "nothing to do" and never reach the injected failure.
    fm services migrate --yes --on-failure rollback --rerun
    expect_rc 1 "services migrate with the injected failure, --on-failure rollback"
    expect_out 'Rollback complete'
    # A failure may never stamp the ledger ABOVE the running fm, which is the defect this
    # pins; a host carrying a poisoned value from an older fm is CORRECTED downwards here,
    # so the invariant is the cap, not equality with whatever was there before.
    local after fmver
    after="$(_ef_toml_get "$cfg" schema.version)"
    fmver="$("$FM_BIN" --version 2>&1 | tr -d ' ')"
    [[ "$after" == "$before" || "$after" == "$fmver" ]] ||
        fail "ledger is v$after after a rolled-back migration (was v$before, fm is v$fmver)"

    # Halt: state left exactly as the failure stopped it, with the backups it took named.
    fm services migrate --yes --on-failure halt --rerun
    expect_rc 1 "services migrate with the injected failure, --on-failure halt"
    expect_out 'Halted as requested'
    expect_out 'Backups taken before the failure'

    # --skip-db-backup/--skip-config-backup honoured: no backups taken, so none are named.
    fm services migrate --yes --on-failure halt --rerun --skip-db-backup --skip-config-backup
    expect_rc 1 "services migrate with the injected failure, skipping both backup kinds"
    expect_out 'Halted as requested'
    expect_not_out 'Backups taken before the failure'

    _ef_remove_failing_migration

    fm create e2ef5a --environment dev
    cleanup_bench e2ef5a
    expect_rc 0 "create e2ef5a" || return
    fm create e2ef5b --environment dev
    cleanup_bench e2ef5b
    expect_rc 0 "create e2ef5b" || return

    fm migrate all --exclude-bench e2ef5b --yes --dry-run
    expect_rc 0 "migrate all --exclude-bench --dry-run"
    expect_out 'e2ef5a'
    expect_not_out 'e2ef5b'
}
