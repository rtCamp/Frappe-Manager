#!/usr/bin/env bash
# Group A -- bare host, install, teardown. Group J -- cleanup and destruction.
#
# A1 and J5 tear down whatever fm install is on the host and are gated behind
# FM_E2E_DESTRUCTIVE=1. A2-A4 need a host with no fm_config.toml yet to mean anything; on a
# host fm already owns they log a skip rather than wiping it themselves, so a run of the whole
# matrix never depends on A-group's declaration order to be safe.

# True when nothing of fm's has touched this host yet (CLI_FM_CONFIG_PATH absent).
_bare_host() { [[ ! -e "$HOME/frappe/fm_config.toml" ]]; }

# Child command/group names from one `--help` page: any box panel whose title ends in
# "commands" (rich's own "Commands" for a group, or the top app's "BENCH[...] commands"
# panels) lists them one per row, continuation lines padded flush with the name column.
_a6_children() {
    local line inpanel=0 title content name
    while IFS= read -r line; do
        case "$line" in
        "╭"*)
            title="${line//─/}"
            title="${title//╭/}"
            title="${title//╮/}"
            title="${title#"${title%%[![:space:]]*}"}"
            title="${title%"${title##*[![:space:]]}"}"
            [[ "${title,,}" == *commands ]] && inpanel=1 || inpanel=0
            continue
            ;;
        "╰"*)
            inpanel=0
            continue
            ;;
        esac
        [[ $inpanel -eq 1 && "$line" == "│"* ]] || continue
        content="${line#│}"
        content="${content%│}"
        content="${content# }"
        [[ -n "$content" && "$content" != " "* ]] || continue
        name="${content%% *}"
        printf '%s\n' "$name"
    done
}

describe A1 "self uninstall --dry-run matches the real removal, wipes ~/frappe and ~/.cache/fm, and leaves a foreign container alone (DESTRUCTIVE: set FM_E2E_DESTRUCTIVE=1)"
case_A1() {
    if [[ "${FM_E2E_DESTRUCTIVE:-}" != "1" ]]; then
        log "FM_E2E_DESTRUCTIVE is not 1; A1 would wipe this host's fm install, skipping"
        return 0
    fi

    docker run -d --name e2ea1-foreign busybox:latest sleep 600 >/dev/null
    expect_container_running e2ea1-foreign

    # A standalone bake's app image is never referenced by any compose file or container, which
    # is exactly the shape plan_teardown's image scan cannot see (utils/uninstall.py:_fm_images).
    fm bake --app-image local/e2ea1-bake
    expect_rc 0 "fm bake --app-image local/e2ea1-bake"
    local baked_tag
    baked_tag="$(sed -n 's/^Baked image: //p' <<<"$FM_OUT" | tr -d '\r')"
    [[ -n "$baked_tag" ]] || fail "fm bake did not report a built tag"

    fm self uninstall --dry-run
    expect_rc 0 "self uninstall --dry-run"
    expect_out 'Nothing was changed'
    local dry_out="$FM_OUT" dry_summary
    [[ "$dry_out" != *"frappe-manager"* ]] ||
        fail "--dry-run already printed the package-removal hint, which only a real run should"
    dry_summary="$(grep -E '^(Benches|Container|Networks|Volumes|Files|Images|Trust)[[:space:]]*:' <<<"$dry_out")"

    fm self uninstall --yes
    expect_rc 0 "self uninstall --yes"
    local real_summary
    real_summary="$(grep -E '^(Benches|Container|Networks|Volumes|Files|Images|Trust)[[:space:]]*:' <<<"$FM_OUT")"
    [[ "$dry_summary" == "$real_summary" ]] ||
        fail "--dry-run plan differs from what the real run removed:
dry : $dry_summary
real: $real_summary"

    expect_absent "$HOME/frappe"
    expect_absent "$HOME/.cache/fm"
    expect_container_running e2ea1-foreign

    # Known open defect (notes/e2e-matrix.md#8): images built by `fm bake` survive, because the
    # plan's image scan is compose/container-based and a standalone bake's image is neither.
    # This pins the PROMISE in uninstall.py's own docstring, so it fails while that holds.
    if docker image inspect "$baked_tag" >/dev/null 2>&1; then
        fail "baked image $baked_tag survived self uninstall (known defect)"
    fi

    docker rm -f e2ea1-foreign >/dev/null 2>&1 || true
    docker rmi -f "$baked_tag" >/dev/null 2>&1 || true
}

describe A2 "the first command on a bare host installs the whole stack"
case_A2() {
    if ! _bare_host; then
        log "host already has an fm install; A2 needs a bare host, skipping"
        return 0
    fi

    fm list
    expect_rc 0 "fm list on a bare host"
    expect_file "$HOME/frappe/fm_config.toml"
    expect_container_running fm_mariadb
    expect_container_running fm_nginx-proxy

    # This case is the only thing that installed anything: undo it so the host is bare again
    # for whatever runs next, the same self-cleanup `cleanup_bench` does for a created bench.
    fm self uninstall --yes
    expect_rc 0 "restoring bare state"
    expect_absent "$HOME/frappe"
}

describe A3 "the first command refuses when port 80 is already taken, names services ports, and leaves nothing behind"
case_A3() {
    if ! _bare_host; then
        log "host already has an fm install; A3 needs a bare host, skipping"
        return 0
    fi

    docker run -d --name e2ea3-foreign -p 80:80 nginx:alpine >/dev/null
    expect_container_running e2ea3-foreign

    fm list
    expect_rc 1 "fm list with port 80 already taken"
    expect_out 'services ports'
    expect_absent "$HOME/frappe"

    docker rm -f e2ea3-foreign >/dev/null 2>&1 || true
}

describe A4 "services ports before any install only records the setting; the next create publishes there"
case_A4() {
    if ! _bare_host; then
        log "host already has an fm install; A4 needs a bare host, skipping"
        return 0
    fi

    fm services ports --http 8080 --https 8443
    expect_rc 0 "services ports before any install"
    expect_out 'Recorded'
    expect_absent "$HOME/frappe/services"

    fm create e2ea4 --environment dev
    expect_rc 0 "create e2ea4 on the recorded ports"

    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: e2ea4.localhost" "http://127.0.0.1:8080/")"
    [[ "$code" == "200" ]] || fail "http://e2ea4.localhost:8080 returned $code, wanted 200"

    fm self uninstall --yes
    expect_rc 0 "restoring bare state"
    expect_absent "$HOME/frappe"
}

describe A5 "self update-images pulls the stock images, idempotently"
case_A5() {
    fm self update-images
    expect_rc 0 "self update-images (first run)"
    expect_not_out 'Could not pull'

    local image
    for image in mariadb:11.8 postgres:18 redis:8-alpine jwilder/nginx-proxy:1.11 axllent/mailpit:v1.22 adminer:5; do
        docker image inspect "$image" >/dev/null 2>&1 || fail "stock image $image was not pulled"
    done

    fm self update-images
    expect_rc 0 "self update-images (second run, no-op)"
    expect_not_out 'Could not pull'
}

describe A6 "every --help page (walked, not hard-coded) exits 0 with no traceback and no leaked markup"
case_A6() {
    local -a queue=("")
    local path child total=0

    while [[ ${#queue[@]} -gt 0 ]]; do
        path="${queue[0]}"
        queue=("${queue[@]:1}")

        # shellcheck disable=SC2086
        fm $path --help
        total=$((total + 1))
        expect_rc 0 "fm $path --help"
        expect_not_out 'Traceback'
        # Rich renders an unescaped `[tag]`-shaped string as nothing; a literal `\[` is the
        # other failure mode, the escape leaking instead of being rendered (AGENTS.md).
        expect_not_out '\\\['

        local -a children=()
        while IFS= read -r child; do
            children+=("$child")
        done < <(_a6_children <<<"$FM_OUT")

        for child in "${children[@]}"; do
            if [[ -z "$path" ]]; then
                queue+=("$child")
            else
                queue+=("$path $child")
            fi
        done
    done

    log "walked $total --help page(s)"
}

describe J1 "prune's releases/backups/logs categories, its --keep* flags and --rotate-over are independent, and --dry-run changes nothing"
case_J1() {
    fm create e2ej1 --environment dev
    expect_rc 0 "create e2ej1"
    cleanup_bench e2ej1

    local log_dir="$HOME/frappe/sites/e2ej1/workspace/frappe-bench/logs"
    expect_dir "$log_dir"
    local before after
    before="$(ls -la "$log_dir")"

    fm prune e2ej1 --dry-run
    expect_rc 0 "prune --dry-run"
    after="$(ls -la "$log_dir")"
    [[ "$before" == "$after" ]] || fail "prune --dry-run changed $log_dir"

    fm prune e2ej1 --only releases --dry-run
    expect_rc 0 "prune --only releases"
    expect_out 'image-runtime'

    fm prune e2ej1 --only backups --dry-run
    expect_rc 0 "prune --only backups"

    fm prune e2ej1 --keep-releases 1 --keep-backups 1 --keep-logs 1 --rotate-over 500K --dry-run
    expect_rc 0 "prune with every --keep* flag and --rotate-over, dry-run"

    fm prune e2ej1 --only logs --rotate-over 1 --yes
    expect_rc 0 "prune --only logs --rotate-over 1"
    local rotated
    rotated="$(find "$log_dir" -maxdepth 1 -name '*.gz' 2>/dev/null | head -1)"
    [[ -n "$rotated" ]] || fail "no log archive appeared under $log_dir after --rotate-over 1"
}

describe J2 "reset BENCH/SITE reinstalls one site and sets its password, leaving the bench's other site untouched"
case_J2() {
    local bench=e2ej2 site1 site2 pass
    fm create "$bench" --environment dev
    expect_rc 0 "create $bench"
    cleanup_bench "$bench"

    site1="${bench}.localhost"
    site2="second.${bench}.e2e"
    fm create "$bench/$site2"
    expect_rc 0 "add a second site to $bench"

    local site1_cfg="$HOME/frappe/sites/$bench/workspace/frappe-bench/sites/$site1/site_config.json"
    expect_file "$site1_cfg"
    local before after
    before="$(cat "$site1_cfg")"

    pass='Reset-e2ej2-9!'
    fm reset "$bench/$site2" --admin-pass "$pass" --yes
    expect_rc 0 "reset $bench/$site2"

    after="$(cat "$site1_cfg")"
    [[ "$before" == "$after" ]] || fail "resetting $site2 touched $site1's site_config.json"

    expect_http "$site1" 200
    expect_http "$site2" 200

    # `reset` reinstalls the site; the password is in place the moment it returns (verified
    # directly with frappe.utils.password.check_password), but the first request after it can
    # still be refused while the app server finishes coming back.
    local login_code="" waited=0
    while ((waited < 60)); do
        login_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
            -H "Host: $site2" -X POST -d "usr=Administrator" -d "pwd=$pass" \
            "http://127.0.0.1/api/method/login")"
        [[ "$login_code" == "200" ]] && break
        sleep 3
        waited=$((waited + 3))
    done
    [[ "$login_code" == "200" ]] || fail "login as Administrator with the reset password returned $login_code"

    fm delete "$bench" --all-sites --delete-fm-managed-db --yes
    expect_rc 0 "delete $bench --all-sites --yes"
}

describe J3 "delete's plan is tri-state, and an undecided non-interactive run refuses before touching anything"
case_J3() {
    local bench=e2ej3
    fm create "$bench" --environment dev
    expect_rc 0 "create $bench"
    cleanup_bench "$bench"

    fm delete "$bench" --dry-run --delete-fm-managed-db
    expect_rc 0 "delete --dry-run --delete-fm-managed-db"
    expect_out 'dropped'

    fm delete "$bench" --dry-run --no-delete-fm-managed-db
    expect_rc 0 "delete --dry-run --no-delete-fm-managed-db"
    expect_out 'kept'

    fm delete "$bench" --dry-run
    expect_rc 0 "delete --dry-run, tri-state unset"
    expect_out 'undecided'

    # Neither flag given, non-interactive, no --dry-run: fm must refuse before removing anything.
    fm delete "$bench"
    expect_rc 2 "delete with the db flag undecided, non-interactive"
    expect_dir "$HOME/frappe/sites/$bench"
    expect_container_running "fm__${bench}__frappe"

    local site2="second.${bench}.e2e"
    fm create "$bench/$site2"
    expect_rc 0 "add a second site to $bench"

    fm delete "$bench" --delete-fm-managed-db
    expect_rc 1 "delete a multi-site bench without --all-sites"
    expect_out 'all-sites'
    expect_dir "$HOME/frappe/sites/$bench"

    fm delete "$bench" --all-sites --delete-fm-managed-db --delete-backups --yes
    expect_rc 0 "delete $bench --all-sites --yes"
    expect_absent "$HOME/frappe/sites/$bench"
}

describe J4 "self stop's --global-only and --benches-only halves are separable"
case_J4() {
    fm create e2ej4 --environment dev
    expect_rc 0 "create e2ej4"
    cleanup_bench e2ej4

    fm self stop --benches-only
    expect_rc 0 "self stop --benches-only"
    [[ "$(docker inspect -f '{{.State.Running}}' fm__e2ej4__frappe 2>/dev/null)" == "false" ]] ||
        fail "fm__e2ej4__frappe still running after self stop --benches-only"
    expect_container_running fm_mariadb
    expect_container_running fm_nginx-proxy

    fm start e2ej4
    expect_rc 0 "restart e2ej4 for the other half"
    expect_container_running fm__e2ej4__frappe

    fm self stop --global-only
    expect_rc 0 "self stop --global-only"
    expect_container_running fm__e2ej4__frappe
    [[ "$(docker inspect -f '{{.State.Running}}' fm_mariadb 2>/dev/null)" == "false" ]] ||
        fail "fm_mariadb still running after self stop --global-only"
    [[ "$(docker inspect -f '{{.State.Running}}' fm_nginx-proxy 2>/dev/null)" == "false" ]] ||
        fail "fm_nginx-proxy still running after self stop --global-only"

    fm services start all
    expect_rc 0 "restore global services for whatever runs after this case"
    expect_container_running fm_mariadb
    expect_container_running fm_nginx-proxy
}

describe J5 "self uninstall --only acts on exactly one tier at a time (DESTRUCTIVE: set FM_E2E_DESTRUCTIVE=1)"
case_J5() {
    if [[ "${FM_E2E_DESTRUCTIVE:-}" != "1" ]]; then
        log "FM_E2E_DESTRUCTIVE is not 1; J5 would tear this host's fm install apart tier by tier, skipping"
        return 0
    fi

    fm self uninstall --yes

    fm create e2ej5 --environment dev
    expect_rc 0 "create e2ej5"

    fm self uninstall --only trust --yes
    expect_rc 0 "self uninstall --only trust"
    expect_dir "$HOME/frappe/sites/e2ej5"
    expect_dir "$HOME/frappe/services"
    expect_file "$HOME/frappe/fm_config.toml"
    expect_container_running fm__e2ej5__frappe
    expect_container_running fm_mariadb

    fm self uninstall --only services --yes
    expect_rc 0 "self uninstall --only services"
    expect_absent "$HOME/frappe/services"
    docker inspect fm_mariadb >/dev/null 2>&1 && fail "fm_mariadb still exists after --only services"
    expect_dir "$HOME/frappe/sites/e2ej5"
    expect_file "$HOME/frappe/fm_config.toml"
    expect_container_running fm__e2ej5__frappe

    fm self uninstall --only benches --yes
    expect_rc 0 "self uninstall --only benches"
    expect_absent "$HOME/frappe/sites"
    docker inspect fm__e2ej5__frappe >/dev/null 2>&1 && fail "fm__e2ej5__frappe still exists after --only benches"
    expect_file "$HOME/frappe/fm_config.toml"

    fm self uninstall --only host --yes
    expect_rc 0 "self uninstall --only host"
    expect_absent "$HOME/frappe/fm_config.toml"
    expect_absent "$HOME/.cache/fm"

    return 0
}
