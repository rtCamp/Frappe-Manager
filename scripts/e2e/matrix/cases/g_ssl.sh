#!/usr/bin/env bash
# Group G -- SSL. Every issuer (dev CA, acme.sh/Let's Encrypt, custom, standalone) and the host
# trust store. Bench-mode certificates are served by the SHARED nginx-proxy, not a per-bench
# nginx, so every add/remove/renew here restarts (not reloads) that one proxy -- the assertions
# below curl it directly rather than trust a file on disk, because a restart that silently no-ops
# (nginx-proxy not running) still reports success.

G_SERVICES_DIR="$HOME/frappe/services/nginx-proxy"
G_SSL_DIR="$G_SERVICES_DIR/ssl"
G_CA_DIR="$G_SSL_DIR/dev/ca"
G_CONFD_DIR="$G_SERVICES_DIR/confd"

# curl sends no SNI for an IP-literal URL, and the proxy's generated fallback TLS server
# (server_name _; listen 443 ssl; ssl_reject_handshake on;) carries no default certificate, so
# a plain `curl https://127.0.0.1/ -H Host: ...` handshake is rejected before any Host header
# is ever read -- a Host header only selects an HTTP vhost. --resolve dials 127.0.0.1 while
# presenting the domain as real SNI, which is what nginx's per-vhost TLS selection keys on.
# _https_probe <domain> <want-code> [note] -- <curl-arg>...
_https_probe() {
    local domain="$1" want="$2" note="$3" code
    shift 3
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$domain:443:127.0.0.1" "$@" "https://$domain/")"
    [[ "$code" == "$want" ]] || fail "https://$domain returned $code${note:+ $note}, wanted $want"
}

describe G1 "ssl ca install/status/remove touch the host trust store once, guarded by a sentinel, and never crash"
case_G1() {
    fm_t 15 ssl ca status --json
    expect_rc 0 "ssl ca status (before)"
    local had_ca pre_stores
    had_ca="$(json_get 'result.exists' <<<"$FM_STDOUT")"
    pre_stores="$(json_get 'result.trusted_stores' <<<"$FM_STDOUT")"

    if [[ "$had_ca" != "true" ]]; then
        # install refuses outright with no CA on disk; mint one the only way fm does, through a
        # dev certificate, so install/status/remove below have something real to act on.
        fm create e2eg1 --environment dev
        expect_rc 0 "create e2eg1"
        cleanup_bench e2eg1
        fm_t 30 ssl add e2eg1/e2eg1.localhost --dev
        expect_rc 0 "ssl add --dev (mints the CA)"
    fi

    fm_t 30 ssl ca install
    local install_rc=$FM_RC
    # Best-effort (TrustStoreManager.install): no sudo, a locked keychain -- still 1, never a
    # traceback, which is the "works with a broken host" half of this case.
    [[ $install_rc -eq 0 || $install_rc -eq 1 ]] || fail "ssl ca install: exit $install_rc"
    [[ $install_rc -ne 0 ]] || expect_file "$G_CA_DIR/.installed"

    fm_t 15 ssl ca status --json
    expect_rc 0 "ssl ca status (after install)"
    expect_jv 'result.exists' 'true'

    # The sentinel's whole job: a second explicit install must not error just because the first
    # one already ran.
    fm_t 30 ssl ca install
    [[ $FM_RC -eq 0 || $FM_RC -eq 1 ]] || fail "second ssl ca install: exit $FM_RC"

    if [[ "$had_ca" != "true" ]]; then
        # Ours from the start, so nothing else on the host could depend on it: take it all the
        # way back out, cert and key included, leaving the host as found.
        fm_t 30 ssl ca remove --yes --delete-ca
        [[ $FM_RC -eq 0 || $FM_RC -eq 1 ]] || fail "ssl ca remove --delete-ca: exit $FM_RC"
        expect_absent "$G_CA_DIR/.installed"
        expect_absent "$G_CA_DIR/rootCA.pem"
    elif [[ "$pre_stores" == "[]" ]]; then
        # Existed but was not trusted before this case touched it: put that back, keep the CA
        # itself since other benches may already hold certificates signed by it.
        fm_t 30 ssl ca remove --yes
        [[ $FM_RC -eq 0 || $FM_RC -eq 1 ]] || fail "ssl ca remove: exit $FM_RC"
    fi
}

describe G2 "ssl add --dev issues a certificate nginx actually serves over https, trusted by fm's own CA"
case_G2() {
    fm create e2eg2 --environment dev
    expect_rc 0 "create e2eg2"
    cleanup_bench e2eg2

    fm_t 30 ssl add e2eg2/e2eg2.localhost --dev
    expect_rc 0 "ssl add --dev"
    expect_file "$G_SSL_DIR/dev/e2eg2.localhost/fullchain.pem"
    expect_file "$G_SSL_DIR/dev/e2eg2.localhost/key.pem"

    fm ssl list e2eg2 --json
    expect_rc 0 "ssl list e2eg2"
    expect_jv 'result.0.domain' 'e2eg2.localhost'
    expect_jv 'result.0.certificate_type' 'dev'
    expect_jv 'result.0.status' 'issued'

    # expect_http only speaks plain HTTP; the https half needs curl directly, trusting fm's own
    # CA rather than the host's, so this survives a host that never ran 'fm ssl ca install'.
    local ca="$G_CA_DIR/rootCA.pem"
    expect_file "$ca"
    _https_probe "e2eg2.localhost" "200" "" --cacert "$ca"

    fm_t 30 ssl renew e2eg2/e2eg2.localhost --force
    expect_rc 0 "ssl renew --force"
    expect_file "$G_SSL_DIR/dev/e2eg2.localhost/fullchain.pem"

    _https_probe "e2eg2.localhost" "200" "after renew" --cacert "$ca"

    fm ssl remove e2eg2/e2eg2.localhost --yes
    expect_rc 0 "ssl remove"
    expect_absent "$G_SSL_DIR/dev/e2eg2.localhost"

    # `ssl remove` restarts nginx; a single request fired the instant fm returns can still land
    # on the old workers, or on none at all.
    expect_http_until e2eg2.localhost 200
}

describe G3 "ssl add --custom validates the cert/key pair before installing it, and refuses a mismatched one"
case_G3() {
    fm create e2eg3 --environment dev
    expect_rc 0 "create e2eg3"
    cleanup_bench e2eg3
    # No explicit `ssl remove` before teardown: `fm delete` already walks remove_bench ->
    # remove_certificate -> SSLCertificateManager.remove_all_certificates for every certificate
    # the bench owns, so cleanup_bench's delete sweeps this custom cert on its own.

    local tmp
    tmp="$(mktemp -d)"
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
        -keyout "$tmp/a.key" -out "$tmp/a.crt" \
        -subj "/CN=e2eg3.localhost" -addext "subjectAltName=DNS:e2eg3.localhost" >/dev/null 2>&1
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
        -keyout "$tmp/b.key" -out "$tmp/b.crt" \
        -subj "/CN=e2eg3.localhost" -addext "subjectAltName=DNS:e2eg3.localhost" >/dev/null 2>&1

    # a's own certificate paired with b's key: same subject and SAN, different keypair.
    fm ssl add e2eg3/e2eg3.localhost --custom --cert "$tmp/a.crt" --key "$tmp/b.key"
    expect_rc 1 "mismatched cert/key"
    expect_out "match"
    expect_absent "$G_SSL_DIR/custom/e2eg3.localhost"

    fm ssl add e2eg3/e2eg3.localhost --custom --cert "$tmp/a.crt" --key "$tmp/a.key"
    expect_rc 0 "matching cert/key"
    expect_file "$G_SSL_DIR/custom/e2eg3.localhost/fullchain.pem"
    expect_file "$G_SSL_DIR/custom/e2eg3.localhost/key.pem"

    _https_probe "e2eg3.localhost" "200" "(custom cert)" -k

    rm -rf "$tmp"
}

describe G4 "ssl add --standalone writes a server block whose ACME challenge path actually answers, for a domain fronting no container"
case_G4() {
    local domain="e2eg4.invalid"
    local out_file
    out_file="$(mktemp)"

    "$FM_BIN" --non-interactive ssl add "$domain" --standalone --skip-dns-check >"$out_file" 2>&1 &
    local pid=$!

    # The http config + proxy reload (local, no network) happen before acme.sh is ever invoked,
    # so the window below exists regardless of whether the host can reach Let's Encrypt at all.
    local code="" ready=0 waited=0
    while [[ $waited -lt 100 ]]; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 -H "Host: $domain" "http://127.0.0.1/.well-known/acme-challenge/e2eg4-probe")"
        if [[ "$code" == "404" ]]; then
            ready=1
            break
        fi
        sleep 0.3
        waited=$((waited + 1))
    done

    # A made-up token must 404 through the vhost, not "connection refused" or the host's default
    # backend -- that is what proves the standalone server block is live, not merely on disk.
    [[ $ready -eq 1 ]] || fail "standalone ACME challenge path for $domain never answered 404 (last code: $code)"

    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$out_file"

    # The add was interrupted before it could fail and clean up after itself (no real DNS to
    # validate against); this is exactly the orphan 'ssl remove --standalone' is built to find.
    "$FM_BIN" --non-interactive ssl remove "$domain" --standalone --yes >/dev/null 2>&1
    expect_absent "$G_CONFD_DIR/zz-fm-standalone-${domain}.conf"
}

describe G5 "ssl add --test-ca issues against Let's Encrypt staging, and the staging env var never leaks into the next issuance"
case_G5() {
    if [[ -z "${FM_E2E_LE_DOMAIN:-}" ]]; then
        log "FM_E2E_LE_DOMAIN not set (a real public domain with port 80 reachable at this host); skipping staging issuance"
        return 0
    fi

    fm create e2eg5 --environment dev
    expect_rc 0 "create e2eg5"
    cleanup_bench e2eg5

    fm domain add e2eg5 "$FM_E2E_LE_DOMAIN"
    expect_rc 0 "domain add $FM_E2E_LE_DOMAIN"

    fm_t 120 ssl add "e2eg5/${FM_E2E_LE_DOMAIN}" --test-ca
    expect_rc 0 "ssl add --test-ca"
    expect_out 'STAGING|staging'

    # A rehearsal persists nothing: no symlinks, no vhost, no entry to renew or remove later.
    fm ssl list e2eg5 --json
    expect_rc 0 "ssl list e2eg5 after rehearsal"
    expect_jv 'result.0.status' 'not_issued'

    # The leak this case exists to catch: the NEXT issuance in a fresh fm process must not see
    # staging mode, which it only could if some earlier run's env mutation ever escaped that run.
    fm_t 30 ssl add e2eg5/e2eg5.localhost --dev
    expect_rc 0 "ssl add --dev right after a staging rehearsal"
    expect_not_out 'STAGING|staging'
}

describe G6 "ssl dns-config cloudflare stores credentials per-bench and globally with secrets redacted; dns01 issuance needs a real Cloudflare zone"
case_G6() {
    fm create e2eg6 --environment dev
    expect_rc 0 "create e2eg6"
    cleanup_bench e2eg6

    local fake_global="cf_e2eg6_global_FAKETOKEN0000" fake_bench="cf_e2eg6_bench_FAKETOKEN0000"

    fm ssl dns-config cloudflare --api-token "$fake_global" --name e2eg6-global
    expect_rc 0 "dns-config cloudflare (global, labelled)"

    fm ssl dns-config cloudflare e2eg6 --api-token "$fake_bench" --name e2eg6-bench
    expect_rc 0 "dns-config cloudflare (bench, labelled)"

    fm ssl dns-config cloudflare --show --name e2eg6-global
    expect_rc 0 "dns-config --show (global)"
    expect_out "e2eg6-global"
    expect_not_out "$fake_global"

    fm ssl dns-config cloudflare e2eg6 --show --name e2eg6-bench
    expect_rc 0 "dns-config --show (bench)"
    expect_out "e2eg6-bench"
    expect_not_out "$fake_bench"

    fm ssl dns-config cloudflare --remove --name e2eg6-global
    expect_rc 0 "dns-config --remove (global)"
    fm ssl dns-config cloudflare e2eg6 --remove --name e2eg6-bench
    expect_rc 0 "dns-config --remove (bench)"

    fm ssl dns-config cloudflare --show --name e2eg6-global
    expect_out "No 'e2eg6-global'"

    if [[ -z "${FM_E2E_CF_TOKEN:-}" || -z "${FM_E2E_CF_DOMAIN:-}" ]]; then
        log "FM_E2E_CF_TOKEN / FM_E2E_CF_DOMAIN not set; skipping real dns01 issuance against Cloudflare"
        return 0
    fi

    fm domain add e2eg6 "$FM_E2E_CF_DOMAIN"
    expect_rc 0 "domain add $FM_E2E_CF_DOMAIN"

    fm ssl dns-config cloudflare --api-token "$FM_E2E_CF_TOKEN"
    expect_rc 0 "dns-config cloudflare (real token, default label)"

    fm_t 180 ssl add "e2eg6/${FM_E2E_CF_DOMAIN}" --challenge dns01 --dns-provider cloudflare
    expect_rc 0 "ssl add dns01 against a real Cloudflare zone"

    fm ssl remove "e2eg6/${FM_E2E_CF_DOMAIN}" --yes
    expect_rc 0 "ssl remove dns01 certificate"

    fm ssl dns-config cloudflare --remove
    expect_rc 0 "dns-config cloudflare cleanup (real token)"
}

describe G7 "ssl add --skip-dns-check and --wait-for-dns are standalone's own DNS pre-check and nothing else"
case_G7() {
    fm ssl add e2eg7/e2eg7.localhost --skip-dns-check
    expect_rc 2 "--skip-dns-check without --standalone"
    expect_out "skip-dns-check"

    fm ssl add e2eg7/e2eg7.localhost --wait-for-dns
    expect_rc 2 "--wait-for-dns without --standalone"
    expect_out "wait-for-dns"

    local domain="e2eg7.invalid" confpath out
    confpath="$G_CONFD_DIR/zz-fm-standalone-${domain}.conf"

    fm_t 20 ssl add "$domain" --standalone --challenge dns01 --cname "delegate.${domain}"
    expect_rc 1 "cname DNS check against a made-up delegation, no --skip-dns-check"
    expect_out "DNS validation failed"
    expect_absent "$confpath"

    # What happens to the ACME issuance after the check is skipped is G4's concern, and real
    # network timing makes that unbounded here, so only the check itself -- not the run's outcome
    # -- is asserted on.
    out="$(timeout 90 "$FM_BIN" --non-interactive ssl add "$domain" --standalone --challenge dns01 --cname "delegate.${domain}" --skip-dns-check 2>&1 || true)"
    [[ "$out" != *"DNS validation failed"* ]] || fail "--skip-dns-check did not skip the DNS check"

    "$FM_BIN" --non-interactive ssl remove "$domain" --standalone --yes >/dev/null 2>&1 || true
    expect_absent "$confpath"
}

describe G8 "ssl renew all --force renews every bench's certificates independently, one certificate per domain"
case_G8() {
    fm create e2eg8 --environment dev
    expect_rc 0 "create e2eg8"
    cleanup_bench e2eg8
    # No explicit `ssl remove` for either domain before teardown: `fm delete` walks remove_bench
    # -> remove_certificate -> SSLCertificateManager.remove_all_certificates for every
    # certificate the bench owns, so cleanup_bench's delete sweeps both the primary and the
    # alias cert on its own.

    fm domain add e2eg8 e2eg8-alias.localhost
    expect_rc 0 "domain add alias"

    fm_t 30 ssl add e2eg8/e2eg8.localhost --dev
    expect_rc 0 "ssl add primary"
    fm_t 30 ssl add e2eg8/e2eg8-alias.localhost --dev
    expect_rc 0 "ssl add alias"

    local primary_before alias_before primary_after alias_after
    primary_before="$(openssl x509 -noout -serial -in "$G_SSL_DIR/dev/e2eg8.localhost/fullchain.pem")"
    alias_before="$(openssl x509 -noout -serial -in "$G_SSL_DIR/dev/e2eg8-alias.localhost/fullchain.pem")"

    fm_t 180 ssl renew all --force
    # Other benches already on the host may legitimately fail to renew for reasons this case
    # cannot see; only our own two domains below are the actual assertion.
    [[ $FM_RC -eq 0 || $FM_RC -eq 1 ]] || fail "ssl renew all --force: exit $FM_RC"

    primary_after="$(openssl x509 -noout -serial -in "$G_SSL_DIR/dev/e2eg8.localhost/fullchain.pem")"
    alias_after="$(openssl x509 -noout -serial -in "$G_SSL_DIR/dev/e2eg8-alias.localhost/fullchain.pem")"

    # Each domain got its OWN fresh certificate -- one unchanged serial would mean "one
    # certificate per domain" did not actually happen for it.
    [[ "$primary_after" != "$primary_before" ]] || fail "e2eg8.localhost certificate was not renewed"
    [[ "$alias_after" != "$alias_before" ]] || fail "e2eg8-alias.localhost certificate was not renewed"

    _https_probe "e2eg8.localhost" "200" "after ssl renew all --force" -k

    # _ecc is an acme.sh (Let's Encrypt) storage detail; a dev certificate never produces one, so
    # exercising that fallback needs a real Let's Encrypt certificate (see G5).
    if [[ -z "${FM_E2E_LE_DOMAIN:-}" ]]; then
        log "FM_E2E_LE_DOMAIN not set; the _ecc fallback path is not exercised by this case"
    fi
}

describe G9 "ssl acme-sh passes its arguments through to acme.sh unmangled"
case_G9() {
    local acmesh_bin="$G_SSL_DIR/acmesh/.acme.sh/acme.sh"

    if [[ ! -x "$acmesh_bin" ]]; then
        # acme.sh installs itself the first time ANY Let's Encrypt-backed certificate is
        # attempted, before any domain validation -- a bogus standalone target triggers that
        # without needing a real DNS name.
        timeout 60 "$FM_BIN" --non-interactive ssl add e2eg9-trigger.invalid --standalone --skip-dns-check >/dev/null 2>&1 || true
        "$FM_BIN" --non-interactive ssl remove e2eg9-trigger.invalid --standalone --yes >/dev/null 2>&1 || true
    fi

    if [[ ! -x "$acmesh_bin" ]]; then
        log "acme.sh could not be installed on this host (no internet reachable from here?); skipping passthrough check"
        return 0
    fi

    fm ssl acme-sh --list
    expect_rc 0 "ssl acme-sh --list"
    # output.info() renders only under -v: the command's OWN commentary is not assertable here.
    # acme.sh's own relayed table is -- its header is unconditional.
    expect_out 'Main_Domain'

    # A value round-tripped unmangled is one even a naive implementation could lose to quoting.
    fm ssl acme-sh --info -d "nonexistent-e2eg9.example"
    expect_out "nonexistent-e2eg9\.example"
}
