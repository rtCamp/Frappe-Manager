#!/usr/bin/env bash
# Group K -- what fm writes to stdout, and what it exits with. Read-only: safe on a live host.

K_BENCH="${K_BENCH:-}"

# Every command that takes --json, as BENCH-less and BENCH-taking halves.
K_JSON_GLOBAL=("list" "services info" "services trusted-proxies status" "ssl ca status" "maintenance status")
K_JSON_BENCH=("info" "ssl list" "apps list" "domain list" "tools status" "telemetry status" "auth status")

_k_pick_bench() {
    [[ -n "$K_BENCH" ]] && return 0
    fm list --json
    K_BENCH="$(json_get 'result.0.name' <<<"$FM_STDOUT")"
    [[ -n "$K_BENCH" && "$K_BENCH" != "<missing>" ]]
}

describe K1 "every --json command answers one parseable envelope"
case_K1() {
    local cmd
    for cmd in "${K_JSON_GLOBAL[@]}"; do
        # shellcheck disable=SC2086
        fm $cmd --json
        expect_json
        expect_jv 'v' '1'
        expect_jv 'ok' 'true'
    done

    if ! _k_pick_bench; then
        log "  $(c_dim 'no bench on this host; per-bench --json commands skipped')"
        return 0
    fi
    for cmd in "${K_JSON_BENCH[@]}"; do
        # shellcheck disable=SC2086
        fm $cmd "$K_BENCH" --json
        expect_json
        expect_jv 'ok' 'true'
    done

    # The envelope must also be the shape of a FAILURE, which is what a program most needs.
    fm info definitely-not-a-bench --json
    expect_rc 2 "fm info on a missing bench"
    expect_json
    expect_jv 'ok' 'false'
}

describe K2 "--events json is pure JSONL, every line levelled, sealed with an exit event"
case_K2() {
    fm --events json list
    expect_rc 0 "fm --events json list"

    local bad
    bad="$(grep -vcE '^\{' <<<"$FM_OUT")"
    [[ "$bad" -eq 0 ]] || fail "$bad non-JSON line(s) on the event stream"

    while IFS= read -r line; do
        json_valid <<<"$line" || fail "unparseable event line: ${line:0:80}"
        [[ "$(json_get 'level' <<<"$line")" != "<missing>" ]] || fail "event without a level: ${line:0:80}"
    done <<<"$FM_STDOUT"

    local last
    last="$(tail -1 <<<"$FM_STDOUT")"
    [[ "$(json_get 'event' <<<"$last")" == "run.finished" ]] ||
        fail "stream not sealed with run.finished, last was: ${last:0:80}"
    [[ "$(json_get 'code' <<<"$last")" != "<missing>" ]] ||
        fail "the sealing event carries no exit code"

    # Rich markup must never reach a parser.
    expect_not_out '\[/?(bold|fm\.[a-z]+)\]'
}

describe K3 "0 for success, 1 for an operational failure, 2 for a wrong command line"
case_K3() {
    fm list
    expect_rc 0 "fm list"

    fm --nosuchflag list
    expect_rc 2 "unknown flag"

    fm --log-level nosuchlevel list
    expect_rc 2 "bad flag VALUE"

    fm create
    expect_rc 2 "missing required argument"
    expect_out 'Missing argument'
    # A 228-line help page with no error line in it is not an answer.
    [[ "$(wc -l <<<"$FM_OUT")" -lt 40 ]] || fail "usage error printed $(wc -l <<<"$FM_OUT") lines"

    fm info definitely-not-a-bench
    expect_rc 2 "unknown bench name"

    fm --version
    expect_rc 0 "fm --version"
}

describe K4 "--no-color strips ANSI; --progress and --log-level are honoured"
case_K4() {
    fm --no-color list
    expect_rc 0 "fm --no-color list"
    grep -q $'\033\[' <<<"$FM_OUT" && fail "--no-color still emitted ANSI escapes"

    fm --progress none list
    expect_rc 0 "fm --progress none"
    fm --progress plain list
    expect_rc 0 "fm --progress plain"

    local lvl
    for lvl in debug info warning error; do
        fm --log-level "$lvl" list
        expect_rc 0 "fm --log-level $lvl"
    done

    fm -v -v list
    expect_rc 0 "fm -v -v"

    # Escaped table-shaped text must survive as text, and must not leak its backslash.
    fm create --help
    expect_rc 0 "fm create --help"
    expect_not_out '\\\['
}

describe K5 "fm compose passes its arguments through to docker compose"
case_K5() {
    if ! _k_pick_bench; then
        log "  $(c_dim 'no bench on this host; compose passthrough skipped')"
        return 0
    fi

    # `ps --services` alone lists only RUNNING containers; J4's `self stop` stops every bench
    # on the host and restarts none, so a bench picked here may be stopped. --all makes this
    # independent of what earlier cases left running.
    fm compose "$K_BENCH" ps --services --all
    expect_rc 0 "compose ps"
    expect_out 'frappe'

    fm compose "$K_BENCH" config --quiet
    expect_rc 0 "compose config"

    # A typo must never reach docker compose as a bench name.
    fm compose definitely-not-a-bench ps
    expect_rc 2 "compose with an unknown bench"
}

describe K6 "fm self upgrade never downgrades a build that is ahead of PyPI"
case_K6() {
    fm --version
    expect_rc 0 "fm --version"
    local before="$FM_OUT"

    fm_t 60 self upgrade
    expect_rc 0 "self upgrade on a dev build"

    fm --version
    [[ "$FM_OUT" == "$before" ]] || fail "version changed: '$before' -> '$FM_OUT'"
}
