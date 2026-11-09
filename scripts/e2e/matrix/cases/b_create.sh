#!/usr/bin/env bash
# Group B -- create, the axis cross (B1-B16). See notes/e2e-matrix.md section 4.
#
# External database cases (B4-B7, B9) read credentials from env and SKIP (not fail) when
# unset, since a real external MariaDB/Postgres server is not something every host running
# this matrix has:
#   FM_E2E_EXT_MARIADB_HOST / _PORT (default 3306) / _ADMIN_USER / _ADMIN_PASSWORD / _CA
#   FM_E2E_EXT_POSTGRES_HOST / _PORT (default 5432) / _ADMIN_USER / _ADMIN_PASSWORD
# B16's external-redis portion reads FM_E2E_EXT_REDIS_URL (e.g. redis://host:6379, no path)
# and is skipped on its own -- the rest of B16 still runs without it.
#
# B8/B9/B10/B11 need an app image, and there is no stock one (notes/e2e-matrix.md line 136).
# FM_E2E_APP_IMAGE overrides with a ready-made one; left unset, each case bakes its OWN
# fixture into a FIXED, content-addressed local tag via `fm bake` and leaves it in the daemon
# rather than `docker rmi`-ing it at case end: a bake is 5-10 min on the 2-core host this
# matrix is budgeted for (notes/e2e-matrix.md section 2), and `docker image inspect` makes
# every run after the first free. Remove `fm-e2e-shared-appimage:*` by hand when done with it.

B_MARIADB_PORT="${FM_E2E_EXT_MARIADB_PORT:-3306}"
B_POSTGRES_PORT="${FM_E2E_EXT_POSTGRES_PORT:-5432}"

_b_ext_mariadb() {
    [[ -n "${FM_E2E_EXT_MARIADB_HOST:-}" && -n "${FM_E2E_EXT_MARIADB_ADMIN_USER:-}" \
        && -n "${FM_E2E_EXT_MARIADB_ADMIN_PASSWORD:-}" ]]
}

_b_ext_postgres() {
    [[ -n "${FM_E2E_EXT_POSTGRES_HOST:-}" && -n "${FM_E2E_EXT_POSTGRES_ADMIN_USER:-}" \
        && -n "${FM_E2E_EXT_POSTGRES_ADMIN_PASSWORD:-}" ]]
}

_b_skip() { log "  $(c_yellow skip:) $*"; }

# Host-bench-dir layout fm itself never varies (utils/site.py:host_bench_dir): the bind-mount
# source is always BENCH/workspace/frappe-bench, for every runtime.
_b_workspace() { printf '%s/frappe/sites/%s/workspace/frappe-bench' "$HOME" "$1"; }
_b_bench_root() { printf '%s/frappe/sites/%s' "$HOME" "$1"; }

# True when NAME is not a running container -- either absent or stopped. The inverse of
# expect_container_running, needed because fm disables a redis container via a compose
# profile rather than deleting its service block (compose_shape.py:BENCH_REDIS_SERVICES),
# so the block staying in docker-compose.yml proves nothing about whether it is up.
_b_not_running() {
    [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" != "true" ]] ||
        fail "container $1 is running but should have been suppressed"
}

# Best-effort: drop the schema/login an external-db case made. fm never does this itself
# (docs/guides/external-database.md: "On your server ... left in place, flag or no flag"),
# and the matrix host may not carry the client at all.
_b_drop_mariadb_schema() {
    local host="$1" port="$2" user="$3" pass="$4" name="$5"
    command -v mysql >/dev/null 2>&1 || { _b_skip "no mysql client; left $name on $host for manual cleanup"; return 0; }
    mysql -h "$host" -P "$port" -u "$user" -p"$pass" \
        -e "DROP DATABASE IF EXISTS \`$name\`; DROP USER IF EXISTS '$name'@'%';" 2>/dev/null || true
}

_b_drop_postgres_schema() {
    local host="$1" port="$2" user="$3" pass="$4" name="$5"
    command -v psql >/dev/null 2>&1 || { _b_skip "no psql client; left $name on $host for manual cleanup"; return 0; }
    PGPASSWORD="$pass" psql -h "$host" -p "$port" -U "$user" -d postgres \
        -c "DROP DATABASE IF EXISTS \"$name\";" -c "DROP ROLE IF EXISTS \"$name\";" >/dev/null 2>&1 || true
}

# Builds (once) or reuses the fixture app image named by $1, with apps $2... baked into it.
# Sets _B_IMAGE on success; a bake failure is a real case failure, not a skip (the daemon is
# known present by the time any case runs -- see run.sh's tool check).
_B_IMAGE=""
_b_app_image() {
    local label="$1"
    shift
    if [[ -n "${FM_E2E_APP_IMAGE:-}" ]]; then
        _B_IMAGE="$FM_E2E_APP_IMAGE"
        return 0
    fi
    _B_IMAGE="fm-e2e-shared-appimage:$label"
    if docker image inspect "$_B_IMAGE" >/dev/null 2>&1; then
        log "  $(c_dim "reusing cached fixture image $_B_IMAGE")"
        return 0
    fi
    local -a flags=()
    local a
    for a in "$@"; do flags+=(--apps "$a"); done
    fm_t 900 bake "${flags[@]}" --app-image "$_B_IMAGE"
    if [[ $FM_RC -ne 0 ]]; then
        fail "bake fixture image $_B_IMAGE"
        _B_IMAGE=""
        return 1
    fi
    return 0
}

describe B1 "mount + fm mariadb + dev: baseline, serves over http"
case_B1() {
    fm create e2eb1 --environment dev
    expect_rc 0 "create"
    cleanup_bench e2eb1
    expect_http e2eb1.localhost 200
}

describe B2 "mount + fm mariadb + prod: developer mode off, assets built"
case_B2() {
    fm create e2eb2 --environment prod
    expect_rc 0 "create"
    cleanup_bench e2eb2
    expect_http e2eb2.localhost 200

    local common
    common="$(_b_workspace e2eb2)/sites/common_site_config.json"
    expect_file "$common"
    local dev_mode
    dev_mode="$(json_get 'developer_mode' <"$common")"
    [[ "$dev_mode" == "false" ]] || fail "prod bench has developer_mode=$dev_mode, wanted false"

    local assets
    assets="$(_b_workspace e2eb2)/sites/assets"
    expect_dir "$assets"
    [[ -n "$(find "$assets" -type f -print -quit 2>/dev/null)" ]] || fail "sites/assets has no built files"
}

describe B3 "mount + fm postgres + dev: postgres starts on demand, info names the engine"
case_B3() {
    fm create e2eb3 --db-type postgres
    expect_rc 0 "create"
    cleanup_bench e2eb3
    expect_http e2eb3.localhost 200
    expect_container_running fm_postgres

    fm info e2eb3 --json
    expect_json
    expect_res 'sites.0.database' 'postgres'
}

describe B4 "mount + external mariadb + prod: fm writes nothing into its own mariadb"
case_B4() {
    if ! _b_ext_mariadb; then
        _b_skip "FM_E2E_EXT_MARIADB_HOST/_ADMIN_USER/_ADMIN_PASSWORD not set"
        return 0
    fi
    fm create e2eb4 --environment prod \
        --db-host "$FM_E2E_EXT_MARIADB_HOST" --db-port "$B_MARIADB_PORT" --db-name e2eb4 \
        --db-admin-user "$FM_E2E_EXT_MARIADB_ADMIN_USER" --db-admin-password "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD"
    expect_rc 0 "create"
    cleanup_bench e2eb4
    expect_http e2eb4.localhost 200

    fm info e2eb4 --json
    expect_json
    expect_res 'sites.0.external_database.host' "$FM_E2E_EXT_MARIADB_HOST"
    expect_res 'environment' 'prod'

    _b_drop_mariadb_schema "$FM_E2E_EXT_MARIADB_HOST" "$B_MARIADB_PORT" \
        "$FM_E2E_EXT_MARIADB_ADMIN_USER" "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" e2eb4
}

describe B5 "mount + external postgres + dev: database and role created on the server"
case_B5() {
    if ! _b_ext_postgres; then
        _b_skip "FM_E2E_EXT_POSTGRES_HOST/_ADMIN_USER/_ADMIN_PASSWORD not set"
        return 0
    fi
    fm create e2eb5 --db-type postgres \
        --db-host "$FM_E2E_EXT_POSTGRES_HOST" --db-port "$B_POSTGRES_PORT" --db-name e2eb5 \
        --db-admin-user "$FM_E2E_EXT_POSTGRES_ADMIN_USER" --db-admin-password "$FM_E2E_EXT_POSTGRES_ADMIN_PASSWORD"
    expect_rc 0 "create"
    cleanup_bench e2eb5
    expect_http e2eb5.localhost 200

    fm info e2eb5 --json
    expect_json
    expect_res 'sites.0.database' 'postgres'
    expect_res 'sites.0.external_database.host' "$FM_E2E_EXT_POSTGRES_HOST"

    _b_drop_postgres_schema "$FM_E2E_EXT_POSTGRES_HOST" "$B_POSTGRES_PORT" \
        "$FM_E2E_EXT_POSTGRES_ADMIN_USER" "$FM_E2E_EXT_POSTGRES_ADMIN_PASSWORD" e2eb5
}

describe B6 "mount + external mariadb --db-ca --db-no-verify-hostname: CA lands and is used"
case_B6() {
    if ! _b_ext_mariadb || [[ -z "${FM_E2E_EXT_MARIADB_CA:-}" ]]; then
        _b_skip "FM_E2E_EXT_MARIADB_HOST/_ADMIN_USER/_ADMIN_PASSWORD/_CA not set"
        return 0
    fi
    fm create e2eb6 \
        --db-host "$FM_E2E_EXT_MARIADB_HOST" --db-port "$B_MARIADB_PORT" --db-name e2eb6 \
        --db-admin-user "$FM_E2E_EXT_MARIADB_ADMIN_USER" --db-admin-password "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" \
        --db-ca "$FM_E2E_EXT_MARIADB_CA" --db-no-verify-hostname
    expect_rc 0 "create"
    cleanup_bench e2eb6
    expect_http e2eb6.localhost 200

    # site_manager/modules/db_tls.py: config/tls/<site>/db-ca.pem is the one place the CA lands.
    expect_file "$(_b_workspace e2eb6)/config/tls/e2eb6.localhost/db-ca.pem"

    _b_drop_mariadb_schema "$FM_E2E_EXT_MARIADB_HOST" "$B_MARIADB_PORT" \
        "$FM_E2E_EXT_MARIADB_ADMIN_USER" "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" e2eb6
}

describe B7 "--attach-existing-site + --encryption-key: encrypted secrets still decrypt"
case_B7() {
    if ! _b_ext_mariadb; then
        _b_skip "FM_E2E_EXT_MARIADB_HOST/_ADMIN_USER/_ADMIN_PASSWORD not set"
        return 0
    fi
    local pass="E2eB7Secret99x"
    local site_config
    site_config="$(_b_workspace e2eb7)/sites/e2eb7.localhost/site_config.json"

    fm create e2eb7 \
        --db-host "$FM_E2E_EXT_MARIADB_HOST" --db-port "$B_MARIADB_PORT" --db-name e2eb7 \
        --db-admin-user "$FM_E2E_EXT_MARIADB_ADMIN_USER" --db-admin-password "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" \
        --db-password "$pass"
    expect_rc 0 "create (provision)"

    local key
    key="$(json_get 'encryption_key' <"$site_config")"
    [[ "$key" != "<missing>" && "$key" != "<unparseable>" && -n "$key" ]] || fail "no encryption_key recorded on the provisioned site"

    # --attach-existing-site requires --db-host (create.py:772-785), so this is the bench
    # directory + containers only: the schema it is about to re-attach to is untouched.
    fm delete e2eb7 --yes --delete-fm-managed-db
    expect_rc 0 "delete (schema stays on the external server)"

    fm create e2eb7 \
        --db-host "$FM_E2E_EXT_MARIADB_HOST" --db-port "$B_MARIADB_PORT" --db-name e2eb7 \
        --db-password "$pass" --attach-existing-site --encryption-key "$key"
    expect_rc 0 "attach"
    cleanup_bench e2eb7
    expect_http e2eb7.localhost 200

    local got
    got="$(json_get 'encryption_key' <"$site_config")"
    [[ "$got" == "$key" ]] || fail "attach minted a new encryption_key instead of keeping the original"

    _b_drop_mariadb_schema "$FM_E2E_EXT_MARIADB_HOST" "$B_MARIADB_PORT" \
        "$FM_E2E_EXT_MARIADB_ADMIN_USER" "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" e2eb7
}

describe B8 "image runtime + fm mariadb: apps.txt is a FILE, Found N app(s), bench serves"
case_B8() {
    _b_app_image frappe-v1 frappe || return 0
    local img="$_B_IMAGE"

    fm create e2eb8 --runtime image --app-image "$img"
    expect_rc 0 "create"
    cleanup_bench e2eb8
    expect_out 'Found [0-9]+ app\(s\)'
    expect_http e2eb8.localhost 200

    # The reported bug (notes/e2e-matrix.md section 1): this must be a regular FILE, never a
    # directory Docker created because the bind source was missing.
    local apps_txt
    apps_txt="$(_b_workspace e2eb8)/sites/apps.txt"
    expect_file "$apps_txt"
    grep -q '^frappe$' "$apps_txt" || fail "apps.txt does not list frappe"
}

describe B9 "image runtime + external mariadb: the db gate runs on the app image itself"
case_B9() {
    if ! _b_ext_mariadb; then
        _b_skip "FM_E2E_EXT_MARIADB_HOST/_ADMIN_USER/_ADMIN_PASSWORD not set"
        return 0
    fi
    _b_app_image frappe-v1 frappe || return 0
    local img="$_B_IMAGE"

    fm create e2eb9 --runtime image --app-image "$img" \
        --db-host "$FM_E2E_EXT_MARIADB_HOST" --db-port "$B_MARIADB_PORT" --db-name e2eb9 \
        --db-admin-user "$FM_E2E_EXT_MARIADB_ADMIN_USER" --db-admin-password "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD"
    expect_rc 0 "create"
    cleanup_bench e2eb9
    expect_http e2eb9.localhost 200
    expect_container_running "fm__e2eb9__frappe"

    fm info e2eb9 --json
    expect_json
    expect_res 'sites.0.external_database.host' "$FM_E2E_EXT_MARIADB_HOST"

    _b_drop_mariadb_schema "$FM_E2E_EXT_MARIADB_HOST" "$B_MARIADB_PORT" \
        "$FM_E2E_EXT_MARIADB_ADMIN_USER" "$FM_E2E_EXT_MARIADB_ADMIN_PASSWORD" e2eb9
}

describe B10 "image runtime + --bench-only, then add the site: containers come up with no site"
case_B10() {
    _b_app_image frappe-v1 frappe || return 0
    local img="$_B_IMAGE"

    fm create e2eb10 --runtime image --app-image "$img" --bench-only
    expect_rc 0 "create --bench-only"
    cleanup_bench e2eb10
    expect_container_running "fm__e2eb10__frappe"
    expect_absent "$(_b_bench_root e2eb10)/sites/e2eb10.localhost"

    fm create e2eb10/site10.localhost
    expect_rc 0 "add site"
    expect_http site10.localhost 200
}

describe B11 "seeded --apps-from + --apps override: identity is the module name"
case_B11() {
    _b_app_image frappe-erpnext-v1 frappe erpnext || return 0
    local img="$_B_IMAGE"

    fm create e2eb11 --apps-from "$img" --apps erpnext
    expect_rc 0 "create"
    cleanup_bench e2eb11
    expect_http e2eb11.localhost 200

    local apps_txt
    apps_txt="$(_b_workspace e2eb11)/sites/apps.txt"
    expect_file "$apps_txt"
    grep -q '^frappe$' "$apps_txt" || fail "apps.txt lost the baked frappe app"
    local hits
    hits="$(grep -c '^erpnext$' "$apps_txt")"
    [[ "$hits" -eq 1 ]] || fail "erpnext appears $hits times in apps.txt, wanted exactly 1 (override must replace, not append)"
}

describe B12 "mount + --apps erpnext --apps wiki: apps.txt order; recorded vs installed"
case_B12() {
    # erpnext's own develop trunk breaks `bench build` on a yarn 1 workspace-link error
    # unrelated to fm; version-16 is the release branch STABLE_APP_BRANCH_MAPPING_LIST
    # names, so this measures fm rather than erpnext's trunk health.
    fm create e2eb12 --apps erpnext:version-16 --apps wiki
    expect_rc 0 "create"
    cleanup_bench e2eb12
    expect_http e2eb12.localhost 200

    local apps_txt
    apps_txt="$(_b_workspace e2eb12)/sites/apps.txt"
    expect_file "$apps_txt"
    mapfile -t lines <"$apps_txt"
    [[ "${lines[0]:-}" == "frappe" && "${lines[1]:-}" == "erpnext" && "${lines[2]:-}" == "wiki" ]] ||
        fail "apps.txt order is ${lines[*]:-<empty>}, wanted frappe erpnext wiki"

    fm apps list e2eb12 --json
    expect_json
    # --apps never persists to bench_config.toml (bench_config.py NOT_WRITTEN_TO_DISK): a fresh
    # load, as every later command does, has nothing recorded even though the apps are installed.
    expect_res 'recorded' '[]'
    expect_res 'installed' '["frappe", "erpnext", "wiki"]'
}

describe B13 "mount + --bench-only: no site; info does not invent one"
case_B13() {
    fm create e2eb13 --bench-only
    expect_rc 0 "create"
    cleanup_bench e2eb13
    expect_container_running "fm__e2eb13__frappe"

    fm info e2eb13 --json
    expect_json
    expect_res 'sites' '[]'
    expect_res 'url' 'None'
}

describe B14 "multi-site: BENCH/one, then BENCH/two --default-site; default flips"
case_B14() {
    fm create "e2eb14/one.localhost"
    expect_rc 0 "create first site"
    cleanup_bench e2eb14
    fm create "e2eb14/two.localhost" --default-site
    expect_rc 0 "add second site as default"

    expect_http one.localhost 200
    expect_http two.localhost 200
    # Each hostname must reach ITS OWN site: a 200 alone is also what a bench serving the
    # first site's data on the second hostname returns.
    expect_served_by one.localhost e2eb14 one.localhost
    expect_served_by two.localhost e2eb14 two.localhost

    local bench_root
    bench_root="$(_b_bench_root e2eb14)"
    expect_dir "$bench_root/workspace/frappe-bench/sites/one.localhost"
    expect_dir "$bench_root/workspace/frappe-bench/sites/two.localhost"

    local common="$bench_root/workspace/frappe-bench/sites/common_site_config.json"
    expect_file "$common"
    local default_site
    default_site="$(json_get 'default_site' <"$common")"
    [[ "$default_site" == "two.localhost" ]] || fail "default_site is '$default_site', wanted two.localhost"
}

describe B15 "--config + --dry-run: prints the resolved toml, creates nothing, exits 0"
case_B15() {
    fm create e2eb15 --dry-run --config 'environment = "prod"' --python 3.11
    expect_rc 0 "dry run"
    expect_out 'environment = "prod"'
    expect_out 'python_version = "3.11"'
    expect_absent "$(_b_bench_root e2eb15)"
}

describe B16 "--python --node --restart-policy always --redis-cache/--redis-queue --alias-domains --admin-pass --developer-mode enable"
case_B16() {
    local -a flags=(
        --python 3.14 --node 24 --restart-policy always
        --alias-domains e2eb16-alias.localhost --admin-pass "E2eB16Admin!9"
        --developer-mode enable
    )
    local redis_ok=0
    if [[ -n "${FM_E2E_EXT_REDIS_URL:-}" ]]; then
        flags+=(--redis-cache "${FM_E2E_EXT_REDIS_URL}/0" --redis-queue "${FM_E2E_EXT_REDIS_URL}/1")
        redis_ok=1
    else
        _b_skip "FM_E2E_EXT_REDIS_URL not set; creating without external redis"
    fi

    fm create e2eb16 "${flags[@]}"
    expect_rc 0 "create"
    cleanup_bench e2eb16
    expect_http e2eb16.localhost 200
    expect_http e2eb16-alias.localhost 200

    fm info e2eb16 --json
    expect_json
    local py node
    py="$(json_res 'python_version')"
    node="$(json_res 'node_version')"
    [[ "$py" == 3.14* ]] || fail "python_version is '$py', wanted 3.14.x"
    [[ "$node" == v24* || "$node" == 24* ]] || fail "node_version is '$node', wanted 24.x"

    grep -q 'restart: always' "$(_b_bench_root e2eb16)/docker-compose.yml" ||
        fail "docker-compose.yml does not carry the always restart policy"

    if [[ $redis_ok -eq 1 ]]; then
        # fm suppresses only the container for the side moved out: both sides moved here, so
        # neither per-bench redis container should be running (compose_shape.py:BENCH_REDIS_SERVICES).
        _b_not_running "fm__e2eb16__redis-cache"
        _b_not_running "fm__e2eb16__redis-queue"
    fi
}
