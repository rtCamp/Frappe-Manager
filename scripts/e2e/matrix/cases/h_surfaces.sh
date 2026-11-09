#!/usr/bin/env bash
# Group H -- per-bench surfaces (apps, domain, tools, auth, maintenance, telemetry, shell,
# logs/code/ngrok). Group I -- the services tier. Every bench this file creates and the site
# names it invents are torn down via cleanup_bench; anything else (baked images, host config
# files) is cleaned up inline before the case returns.

# json_get cannot descend into a dict keyed by a hostname (the dot is its own separator), so any
# field keyed by a site name is read with a small inline python3 snippet instead. Each path
# segment is its own argument -- never split on "." -- so a hostname key like
# "two.e2eh4.test" survives whole. _json_field <key> [key...] reads from the envelope's
# `result`, same as json_res/expect_res, not the envelope root.
_json_field() {
    python3 -c '
import json, sys
doc = json.load(sys.stdin)
cur = doc.get("result") if isinstance(doc, dict) else None
for key in sys.argv[1:]:
    if isinstance(cur, list) and key.lstrip("-").isdigit():
        cur = cur[int(key)]
    elif isinstance(cur, dict):
        cur = cur.get(key)
    else:
        cur = None
print("<missing>" if cur is None else cur if isinstance(cur, str) else json.dumps(cur))
' "$@" <<<"$FM_STDOUT"
}

describe H1 "apps add fans BENCH/BENCH-SITE/BENCH-all into fetch-only, one-site install, and all-sites install"
case_H1() {
    fm create e2eh1 --environment dev
    expect_rc 0 "create e2eh1"
    cleanup_bench e2eh1
    fm create "e2eh1/two.e2eh1.test"
    expect_rc 0 "add second site"

    fm apps add e2eh1 wiki:master
    expect_rc 0 "apps add BENCH (code only)"
    expect_out 'Grafted app code into the bench'

    fm apps list e2eh1 --json
    expect_json
    expect_res 'installed.1' 'wiki'

    fm apps add e2eh1/e2eh1.localhost wiki:master --drain
    expect_rc 0 "apps add BENCH/SITE"
    expect_out 'Grafted apps applied'

    local site_cfg="$HOME/frappe/sites/e2eh1/workspace/frappe-bench/sites/two.e2eh1.test/site_config.json"
    chmod 000 "$site_cfg"

    fm_t 300 apps add e2eh1/all wiki:master --no-drain
    expect_rc 1 "apps add BENCH/all with one broken site"
    expect_out 'sites failed'
    expect_out 'two\.e2eh1\.test'

    chmod 644 "$site_cfg"
}

describe H2 "apps list reports the installed section on both an image bench and a mount bench"
case_H2() {
    fm bake --apps frappe --app-image local/e2eh2:e2eh2 --nginx-image local/e2eh2-nginx:e2eh2 --source provision
    expect_rc 0 "standalone bake"

    fm create e2eh2img --runtime image --app-image local/e2eh2:e2eh2
    expect_rc 0 "create image bench"
    cleanup_bench e2eh2img

    fm apps list e2eh2img --json
    expect_json
    expect_res 'installed.0' 'frappe'

    fm create e2eh2mnt --environment dev
    expect_rc 0 "create mount bench"
    cleanup_bench e2eh2mnt

    fm apps list e2eh2mnt --json
    expect_json
    expect_res 'installed.0' 'frappe'

    docker rmi -f local/e2eh2:e2eh2 local/e2eh2-nginx:e2eh2 >/dev/null 2>&1 || true
}

describe H3 "domain add/remove/list claims aliases and the removal strands no proxy config"
case_H3() {
    fm create e2eh3 --environment dev
    expect_rc 0 "create e2eh3"
    cleanup_bench e2eh3

    fm domain add e2eh3 alias1.e2eh3.test alias2.e2eh3.test
    expect_rc 0 "domain add two aliases"

    fm domain list e2eh3 --json
    expect_json
    expect_res '0.aliases.0' 'alias1.e2eh3.test'
    expect_res '0.aliases.1' 'alias2.e2eh3.test'

    fm create e2eh3b --environment dev
    expect_rc 0 "create e2eh3b"
    cleanup_bench e2eh3b

    fm domain add e2eh3b alias1.e2eh3.test
    expect_rc 1 "conflicting domain refused"
    expect_out 'already'

    fm domain add e2eh3b alias1.e2eh3.test --allow-domain-conflicts
    expect_rc 0 "conflicting domain permitted"

    fm domain remove "e2eh3/alias1.e2eh3.test"
    expect_rc 0 "domain remove"

    fm domain list e2eh3 --json
    expect_json
    expect_res '0.aliases.0' 'alias2.e2eh3.test'
}

describe H4 "tools enable/disable per-site writes the drop-in; both hostnames still reach one container pair"
case_H4() {
    fm create e2eh4 --environment dev
    expect_rc 0 "create e2eh4"
    cleanup_bench e2eh4
    fm create "e2eh4/two.e2eh4.test"
    expect_rc 0 "add second site"

    fm tools enable e2eh4
    expect_rc 0 "tools enable (bench-wide)"
    expect_container_running "fm__e2eh4__adminer"
    expect_container_running "fm__e2eh4__mailpit"

    expect_http e2eh4.localhost 200 /adminer/
    expect_http two.e2eh4.test 200 /adminer/

    fm tools status e2eh4 --json
    expect_json
    local routed_two
    routed_two="$(_json_field sites "two.e2eh4.test")"
    [[ "$routed_two" == "true" ]] || fail "two.e2eh4.test not reported routed before disable"

    fm tools disable "e2eh4/two.e2eh4.test"
    expect_rc 0 "tools disable one site"

    local custom="$HOME/frappe/sites/e2eh4/configs/nginx/conf/custom"
    expect_absent "$custom/two.e2eh4.test/admin-tools.conf"
    expect_file "$custom/e2eh4.localhost/admin-tools.conf"

    expect_http_until e2eh4.localhost 200 /adminer/
    # The unrouted path falls through to Frappe, which redirects the trailing-slash URL --
    # never a 404 -- so "not 200" is the actual contract of a disabled drop-in.
    local code waited=0
    while ((waited < 30)); do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: two.e2eh4.test" "http://127.0.0.1/adminer/")"
        [[ "$code" != "200" ]] && break
        sleep 2
        waited=$((waited + 2))
    done
    [[ "$code" != "200" ]] || fail "http://two.e2eh4.test/adminer/ still returned 200 after tools disable"
}

describe H5 "tools enable --mailpit-as-default-mail-server points the bench's mail config at mailpit"
case_H5() {
    fm create e2eh5 --environment dev
    expect_rc 0 "create e2eh5"
    cleanup_bench e2eh5

    fm tools enable e2eh5 --mailpit-as-default-mail-server
    expect_rc 0 "tools enable --mailpit-as-default-mail-server"

    local common="$HOME/frappe/sites/e2eh5/workspace/frappe-bench/sites/common_site_config.json"
    expect_file "$common"
    local mail_server
    mail_server="$(python3 -c 'import json; print(json.load(open("'"$common"'")).get("mail_server"))')"
    [[ "$mail_server" == "fm__e2eh5__mailpit" ]] || fail "common_site_config.json mail_server: got '$mail_server', wanted fm__e2eh5__mailpit"
}

describe H6 "auth enable/disable owns only custom/SITE/auth.conf, never a hand-written sibling file"
case_H6() {
    fm create e2eh6 --environment dev
    expect_rc 0 "create e2eh6"
    cleanup_bench e2eh6

    local site_dir="$HOME/frappe/sites/e2eh6/configs/nginx/conf/custom/e2eh6.localhost"
    mkdir -p "$site_dir"
    local foreign_auth="# hand-written by the operator, not fm
allow 10.0.0.0/8;
"
    local foreign_sibling="# an unrelated operator drop-in
add_header X-Operator yes;
"
    printf '%s' "$foreign_auth" >"$site_dir/auth.conf"
    printf '%s' "$foreign_sibling" >"$site_dir/00-operator.conf"

    fm auth enable e2eh6 --tools --user tuser --password tpass
    expect_rc 0 "auth enable --tools (web untouched)"
    # cmp, not ==: "$(cat f)" strips f's trailing newline while the literal on the right keeps
    # its, so a string comparison fails here unconditionally regardless of what fm did.
    cmp -s <(printf '%s' "$foreign_auth") "$site_dir/auth.conf" || fail "foreign auth.conf touched while web stayed off"
    cmp -s <(printf '%s' "$foreign_sibling") "$site_dir/00-operator.conf" || fail "unrelated sibling file touched"

    fm auth status e2eh6 --json
    expect_json
    expect_res 'web' 'false'
    expect_res 'tools' 'true'

    fm auth enable e2eh6 --web --insecure --user wuser --password wpass \
        --allow-ip 203.0.113.5 --allow-path /api/method/ping
    expect_rc 0 "auth enable --web claims the scope"
    [[ "$(head -c 8 "$site_dir/auth.conf")" == "# fm:aut" ]] || fail "auth.conf not rewritten with fm's own marker once wanted"
    cmp -s <(printf '%s' "$foreign_sibling") "$site_dir/00-operator.conf" || fail "unrelated sibling file touched by a write fm did want"

    fm auth status e2eh6 --json
    expect_json
    expect_res 'web' 'true'
    expect_res 'allow_ips.0' '203.0.113.5/32'
    expect_res 'allow_paths.0' '/api/method/ping'

    fm auth enable e2eh6 --web --rotate --clear-exemptions
    expect_rc 0 "rotate and clear exemptions"
    fm auth status e2eh6 --json
    expect_json
    expect_res 'allow_ips.0' '<missing>'
    expect_res 'allow_paths.0' '<missing>'

    fm auth disable e2eh6
    expect_rc 0 "auth disable (both surfaces off)"
    expect_absent "$site_dir/auth.conf"
    cmp -s <(printf '%s' "$foreign_sibling") "$site_dir/00-operator.conf" || fail "unrelated sibling file lost on disable sweep"
}

describe H7 "auth status/disable report truthfully across an enable/disable cycle"
case_H7() {
    fm create e2eh7 --environment dev
    expect_rc 0 "create e2eh7"
    cleanup_bench e2eh7

    fm auth status e2eh7 --json
    expect_json
    expect_res 'web' 'false'

    fm auth enable e2eh7 --web --insecure --user u --password p
    expect_rc 0 "auth enable --web --insecure"
    fm auth status e2eh7 --json
    expect_json
    expect_res 'web' 'true'
    expect_http e2eh7.localhost 401

    fm auth disable e2eh7
    expect_rc 0 "auth disable"
    fm auth status e2eh7 --json
    expect_json
    expect_res 'web' 'false'
    expect_http e2eh7.localhost 200
}

describe H8 "maintenance bypass token actually bypasses, and --rotate-token invalidates the old one"
case_H8() {
    fm create e2eh8 --environment dev
    expect_rc 0 "create e2eh8"
    cleanup_bench e2eh8

    fm maintenance enable e2eh8 --yes
    expect_rc 0 "maintenance enable"
    local token
    token="$(grep -oE '/fm-bypass/[0-9a-f]+' <<<"$FM_OUT" | head -1 | sed 's#/fm-bypass/##')"
    [[ -n "$token" ]] || fail "no bypass token printed"

    expect_http_until e2eh8.localhost 503

    local cookie_jar
    cookie_jar="$(mktemp)"
    curl -s -o /dev/null -H "Host: e2eh8.localhost" -c "$cookie_jar" "http://127.0.0.1/fm-bypass/$token"
    local with_cookie
    with_cookie="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: e2eh8.localhost" -b "$cookie_jar" "http://127.0.0.1/")"
    [[ "$with_cookie" == "200" ]] || fail "bypass cookie did not bypass maintenance: got $with_cookie"

    fm maintenance enable e2eh8 --yes --rotate-token
    expect_rc 0 "maintenance enable --rotate-token"
    local new_token
    new_token="$(grep -oE '/fm-bypass/[0-9a-f]+' <<<"$FM_OUT" | head -1 | sed 's#/fm-bypass/##')"
    [[ -n "$new_token" && "$new_token" != "$token" ]] || fail "rotate-token did not mint a new token"

    local old_cookie_rc
    old_cookie_rc="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: e2eh8.localhost" -b "$cookie_jar" "http://127.0.0.1/")"
    [[ "$old_cookie_rc" == "503" ]] || fail "old bypass token still bypasses after rotation: got $old_cookie_rc"
    rm -f "$cookie_jar"

    fm maintenance status e2eh8 --json
    expect_json
    expect_res '0.maintenance' 'true'

    fm maintenance disable e2eh8
    expect_rc 0 "maintenance disable"
    expect_http_until e2eh8.localhost 200
}

describe H9 "telemetry enable/status/disable writes per-bench config and never echoes the key"
case_H9() {
    fm create e2eh9 --environment dev
    expect_rc 0 "create e2eh9"
    cleanup_bench e2eh9

    fm telemetry enable e2eh9 newrelic --license-key sekrit-ingest-key
    expect_rc 0 "telemetry enable"
    expect_not_out 'sekrit-ingest-key'

    fm telemetry status e2eh9 --json
    expect_json
    expect_res 'providers.newrelic.enabled' 'true'
    expect_res 'providers.newrelic.license_key_stored' 'true'
    expect_res 'providers.newrelic.reporting' 'true'
    expect_file "$HOME/frappe/sites/e2eh9/workspace/frappe-bench/config/newrelic.ini"

    fm telemetry enable e2eh9 newrelic --force-config
    expect_rc 0 "telemetry enable --force-config"

    fm telemetry disable e2eh9 newrelic
    expect_rc 0 "telemetry disable"
    fm telemetry status e2eh9 --json
    expect_json
    expect_res 'providers.newrelic.enabled' 'false'
    expect_res 'providers.newrelic.license_key_stored' 'true'
}

describe H10 "shell passthrough reaches the container; --run works on a stopped bench"
case_H10() {
    fm create e2eh10 --environment dev
    expect_rc 0 "create e2eh10"
    cleanup_bench e2eh10

    fm shell e2eh10 -c whoami
    expect_rc 0 "shell -c"
    expect_out '^frappe$'

    fm shell e2eh10 --user root -c whoami
    expect_rc 0 "shell --user root -c"
    expect_out '^root$'

    # whoami needs a passwd entry for the mapped UID; the nginx image has none, so fm correctly
    # relays that failure -- `id -u` reads the kernel's uid directly and needs no NSS lookup.
    fm shell e2eh10 --service nginx -c "id -u"
    expect_rc 0 "shell --service nginx -c"

    fm shell e2eh10 --bench-console -c 'print(frappe.local.site)'
    expect_rc 0 "shell --bench-console -c"
    expect_out 'e2eh10\.localhost'

    fm shell e2eh10 -- echo hello-passthrough
    expect_rc 0 "shell -- extra args"
    expect_out 'hello-passthrough'

    fm stop e2eh10
    expect_rc 0 "stop before --run"
    fm shell e2eh10 --run -c whoami
    expect_rc 0 "shell --run on a stopped bench"
    expect_out '^frappe$'
}

describe H11 "logs/code reach their real surface; ngrok skips cleanly without a token"
case_H11() {
    fm create e2eh11 --environment dev
    expect_rc 0 "create e2eh11"
    cleanup_bench e2eh11

    fm logs e2eh11
    expect_rc 0 "logs (host files)"

    fm logs e2eh11 --service nginx
    expect_rc 0 "logs --service nginx"

    timeout 5 "$FM_BIN" --non-interactive logs e2eh11 --service nginx -f >/dev/null 2>&1
    local follow_rc=$?
    [[ "$follow_rc" == "124" || "$follow_rc" == "0" ]] || fail "logs -f exited $follow_rc instead of streaming or finishing"

    fm code e2eh11 --no-attach --extension ms-python.python --debugger
    expect_rc 0 "code --no-attach --extension --debugger"
    expect_not_out 'Waiting for VS Code'

    if [[ -z "${FM_E2E_NGROK_TOKEN:-}" ]]; then
        log "$(c_dim 'FM_E2E_NGROK_TOKEN not set; skipping the ngrok tunnel itself')"
    else
        fm_t 20 ngrok e2eh11 --auth-token "$FM_E2E_NGROK_TOKEN"
        expect_rc 0 "ngrok"
    fi
}

describe I1 "services info/start/stop/restart/shell give mariadb per-service control; shell all is refused"
case_I1() {
    fm create e2ei1 --environment dev
    expect_rc 0 "create e2ei1 (keeps mariadb alive for the case)"
    cleanup_bench e2ei1

    fm services info --json
    expect_json
    grep -q '"mariadb"' <<<"$FM_STDOUT" || fail "services info --json does not mention mariadb"

    fm services stop mariadb
    expect_rc 0 "services stop mariadb"
    fm services info --json
    expect_json
    local state
    state="$(_json_field services mariadb)"
    [[ "$state" != "running" ]] || fail "mariadb still reports running after services stop"

    fm services start mariadb
    expect_rc 0 "services start mariadb"
    fm services info --json
    expect_json
    expect_res 'services.mariadb' 'running'

    fm services restart mariadb
    expect_rc 0 "services restart mariadb"

    fm_t 10 services shell mariadb
    expect_rc 0 "services shell mariadb"

    fm services shell all
    expect_rc 2 "services shell all is refused"
    expect_out 'not supported'
}

describe I2 "services ports moves a live proxy's published ports and benches follow; ports are restored after"
case_I2() {
    fm create e2ei2 --environment dev
    expect_rc 0 "create e2ei2"
    cleanup_bench e2ei2

    fm services info --json
    expect_json
    local orig_http orig_https
    orig_http="$(_json_field proxy ports http)"
    orig_https="$(_json_field proxy ports https)"
    if [[ ! "$orig_http" =~ ^[0-9]+$ || ! "$orig_https" =~ ^[0-9]+$ ]]; then
        fail "could not read the current proxy ports (got http=$orig_http https=$orig_https)"
        return
    fi

    fm services ports --http 18080 --https 18443 --yes
    expect_rc 0 "services ports --http 18080 --https 18443"

    local moved_code
    moved_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: e2ei2.localhost" "http://127.0.0.1:18080/")"
    [[ "$moved_code" == "200" ]] || fail "e2ei2 did not follow the proxy to :18080, got $moved_code"

    fm services ports --http "$orig_http" --https "$orig_https" --yes
    expect_rc 0 "restore the original ports"
    expect_http e2ei2.localhost 200
}

describe I3 "services prune --dry-run prints the plan and changes nothing"
case_I3() {
    fm services prune --only backups --dry-run
    expect_rc 0 "services prune --only backups --dry-run"

    fm services prune --only logs --keep-logs 1 --rotate-over 1K --dry-run
    expect_rc 0 "services prune --only logs --keep-logs --rotate-over --dry-run"

    fm services prune --only bogus
    expect_rc 2 "services prune --only with an unknown category"
}

describe I4 "trusted-proxies set rewrites the proxy's real-ip conf; the proxy recreates only when trust crosses empty<->non-empty"
case_I4() {
    local conf="$HOME/frappe/services/nginx-proxy/confd/fm-real-ip.conf"
    local proxy_id_before proxy_id_mid proxy_id_after

    # TRUST_DOWNSTREAM_PROXY is a container env var fixed at creation, so the proxy only ever
    # needs recreating when trust crosses empty<->non-empty (services_manager/services.py,
    # trusted_proxies.py) -- start from a known untrusted state so that boundary is unambiguous.
    fm services trusted-proxies clear
    expect_rc 0 "trusted-proxies clear (start from a known untrusted state)"

    fm services trusted-proxies set --trust 203.0.113.0/24 --client-ip-header X-E2EI4-Real-IP
    expect_rc 0 "trusted-proxies set (crosses empty -> non-empty)"
    expect_file "$conf"
    grep -q 'X-E2EI4-Real-IP' "$conf" || fail "fm-real-ip.conf does not carry the configured header"
    grep -q '203.0.113.0/24' "$conf" || fail "fm-real-ip.conf does not carry the configured range"

    fm services trusted-proxies status --json
    expect_json
    expect_res 'trusted' 'true'

    proxy_id_before="$(docker inspect -f '{{.Id}}' fm_nginx-proxy 2>/dev/null)"

    # A second `set` that stays non-empty never crosses the boundary: a reload, not a recreate.
    fm services trusted-proxies set --trust 203.0.114.0/24 --client-ip-header X-E2EI4-Real-IP
    expect_rc 0 "trusted-proxies set (stays non-empty)"
    grep -q '203.0.114.0/24' "$conf" || fail "fm-real-ip.conf was not rewritten with the new range"

    proxy_id_mid="$(docker inspect -f '{{.Id}}' fm_nginx-proxy 2>/dev/null)"
    [[ -z "$proxy_id_before" || "$proxy_id_before" == "$proxy_id_mid" ]] ||
        fail "a second set that stayed non-empty recreated the proxy instead of reloading it"

    # `clear` crosses non-empty -> empty: the recreate here is the correct, documented behaviour.
    fm services trusted-proxies clear
    expect_rc 0 "trusted-proxies clear (crosses non-empty -> empty)"
    expect_absent "$conf"

    proxy_id_after="$(docker inspect -f '{{.Id}}' fm_nginx-proxy 2>/dev/null)"
    [[ -z "$proxy_id_mid" || "$proxy_id_mid" != "$proxy_id_after" ]] ||
        fail "trusted-proxies clear crossed non-empty -> empty but did not recreate the proxy"

    fm services trusted-proxies status --json
    expect_json
    expect_res 'trusted' 'false'
}
