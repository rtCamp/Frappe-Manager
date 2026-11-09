#!/usr/bin/env bash
# Assertions and bookkeeping shared by every matrix case.
#
# A case is a bash function `case_<ID>`. It never calls `exit`: a failed assertion marks the
# case and returns, so a run of 70 cases reports 70 results instead of stopping at the first.

FM_BIN="${FM_BIN:-$HOME/fm-src/.venv/bin/fm}"
E2E_RUN_DIR="${E2E_RUN_DIR:-$HOME/fm-e2e}"
E2E_REPORT="${E2E_REPORT:-$E2E_RUN_DIR/report.tsv}"

declare -a CASE_IDS=()
declare -A CASE_DESC=()
declare -a CASE_CLEANUP=()
CURRENT_LOG=""
CASE_FAILURES=0

c_red() { printf '\033[31m%s\033[0m' "$*"; }
c_green() { printf '\033[32m%s\033[0m' "$*"; }
c_yellow() { printf '\033[33m%s\033[0m' "$*"; }
c_dim() { printf '\033[2m%s\033[0m' "$*"; }

# describe <ID> <one-line description>
describe() {
    CASE_IDS+=("$1")
    CASE_DESC["$1"]="$2"
}

log() { printf '%s\n' "$*" | tee -a "$CURRENT_LOG" >&2; }

fail() {
    CASE_FAILURES=$((CASE_FAILURES + 1))
    log "  $(c_red FAIL) $*"
    return 1
}

pass() { log "  $(c_green ok) $*"; }

# Everything fm does in a case goes through here. The streams are kept APART: a `--json`
# answer must be alone on stdout, while the spinner and every status line go to stderr, so a
# merged capture would fail the JSON contract on output that honours it.
#   FM_OUT    both, in the order a human saw them  -> expect_out
#   FM_STDOUT stdout only                          -> expect_json / expect_jv
#   FM_STDERR stderr only
FM_OUT=""
FM_STDOUT=""
FM_STDERR=""
FM_RC=0

_fm_capture() {
    local out err
    out="$(mktemp)"
    err="$(mktemp)"
    COLUMNS=500 "$@" >"$out" 2>"$err"
    FM_RC=$?
    FM_STDOUT="$(cat "$out")"
    FM_STDERR="$(cat "$err")"
    FM_OUT="$FM_STDOUT"
    [[ -n "$FM_STDERR" ]] && FM_OUT="${FM_OUT:+$FM_OUT$'\n'}$FM_STDERR"
    rm -f "$out" "$err"
    printf '%s\n' "$FM_OUT" >>"$CURRENT_LOG"
}

fm() {
    log "  $(c_dim "\$ fm $*")"
    _fm_capture "$FM_BIN" --non-interactive "$@"
    return 0
}

# Same, with a timeout, for commands that can block on a prompt that should not exist.
fm_t() {
    local secs="$1"
    shift
    log "  $(c_dim "\$ timeout ${secs}s fm $*")"
    _fm_capture timeout "$secs" "$FM_BIN" --non-interactive "$@"
    [[ $FM_RC -eq 124 ]] && fail "fm $* blocked for ${secs}s (a prompt that should not exist?)"
    return 0
}

expect_rc() {
    local want="$1" what="${2:-last command}"
    [[ $FM_RC -eq $want ]] || fail "$what: exit $FM_RC, wanted $want"
}

expect_out() {
    local pattern="$1"
    grep -Eq -- "$pattern" <<<"$FM_OUT" || fail "output does not match /$pattern/"
}

expect_not_out() {
    local pattern="$1"
    grep -Eq -- "$pattern" <<<"$FM_OUT" && fail "output unexpectedly matches /$pattern/"
    return 0
}

# jq is not installable on every host this must run on (the throwaway box has no passwordless
# sudo); python3 is already there, because fm runs on it.
# json_get <dotted.path>  -- reads the document on stdin; `.` descends dicts, an integer
# indexes a list. Prints `<missing>` for a path that is not there, never an error.
json_get() {
    python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except Exception:
    print("<unparseable>")
    sys.exit(0)
for key in sys.argv[1].split("."):
    if not key:
        continue
    if key.isdigit() and isinstance(doc, list) and int(key) < len(doc):
        doc = doc[int(key)]
    elif isinstance(doc, dict) and key in doc:
        doc = doc[key]
    else:
        print("<missing>")
        sys.exit(0)
if isinstance(doc, bool):
    print("true" if doc else "false")
elif isinstance(doc, (dict, list)):
    print(json.dumps(doc))
else:
    print(doc)
' "$1" 2>/dev/null
}

json_valid() { python3 -c 'import json,sys; json.load(sys.stdin)' >/dev/null 2>&1; }

expect_json() {
    json_valid <<<"$FM_STDOUT" || fail "stdout is not one parseable JSON document"
}

expect_jv() {
    local path="$1" want="$2" got
    got="$(json_get "$path" <<<"$FM_STDOUT")"
    [[ "$got" == "$want" ]] || fail "$path: got '$got', wanted '$want'"
}

# Every --json answer is the envelope {v, ok, result, error, subject, path, next}. `expect_jv`
# reads the envelope; these two read the PAYLOAD, which is what a case almost always wants.
json_res() { json_get "result${1:+.$1}" <<<"$FM_STDOUT"; }

expect_res() {
    local path="$1" want="$2" got
    got="$(json_res "$path")"
    [[ "$got" == "$want" ]] || fail "result.$path: got '$got', wanted '$want'"
}

expect_file() {
    [[ -f "$1" ]] || fail "$1 is not a regular file"
}

expect_dir() {
    [[ -d "$1" ]] || fail "$1 is not a directory"
}

expect_absent() {
    [[ -e "$1" ]] && fail "$1 still exists"
    return 0
}

# expect_http <host-header> <expected-code> [path]
expect_http() {
    local host="$1" want="$2" path="${3:-/}" code
    code="$(_http_code "$host" "$path")"
    [[ "$code" == "$want" ]] || fail "http://$host$path returned $code, wanted $want"
}

_http_code() {
    curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: $1" "http://127.0.0.1${2:-/}"
}

# expect_http_until <host> <code> [path] [deadline-seconds]
# nginx reload is asynchronous and docker-gen's re-render is debounced, so a single request
# fired the instant fm says "reloaded" can still be answered by the old workers.
expect_http_until() {
    local host="$1" want="$2" path="${3:-/}" deadline="${4:-30}" code="" waited=0
    while ((waited < deadline)); do
        code="$(_http_code "$host" "$path")"
        [[ "$code" == "$want" ]] && return 0
        sleep 2
        waited=$((waited + 2))
    done
    fail "http://$host$path was $code after ${deadline}s, wanted $want"
}

# expect_served_by <host-header> <bench> <site>
# "Something answered 200" is NOT "this site answered". A bench whose second hostname is
# served by its FIRST site returns 200 on every reachability probe while handing out another
# site's data; that defect sat undetected behind `expect_http` until a login refused the
# password it had just been given. This drops a marker into the site's own public files and
# reads it back through the proxy, so the answer names the site that produced it.
expect_served_by() {
    local host="$1" bench="$2" site="$3"
    local root="${FRAPPE_MANAGER_HOME:-$HOME/frappe}/sites/$bench/workspace/frappe-bench/sites/$site"
    local marker="$root/public/files/fm-e2e-whoami.txt" body
    mkdir -p "$(dirname "$marker")" 2>/dev/null
    printf '%s' "$site" >"$marker" 2>/dev/null || {
        fail "could not write the site marker at $marker"
        return
    }
    body="$(curl -s --max-time 20 -H "Host: $host" "http://127.0.0.1/files/fm-e2e-whoami.txt")"
    [[ "$body" == "$site" ]] || fail "http://$host is served by '$body', not '$site'"
    rm -f "$marker"
}

expect_container_running() {
    local name="$1"
    [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == "true" ]] ||
        fail "container $name is not running"
}

# Benches registered here are deleted when the case ends, pass or fail, so one broken case
# does not poison every later one.
cleanup_bench() { CASE_CLEANUP+=("$1"); }

_run_cleanup() {
    local b root
    root="${FRAPPE_MANAGER_HOME:-$HOME/frappe}/sites"
    for b in "${CASE_CLEANUP[@]}"; do
        # A case registers its bench BEFORE the create can fail, so a missing directory means
        # there is nothing to remove -- not a leak, and reporting one trains the reader to
        # ignore the word.
        [[ -d "$root/$b" ]] || continue
        log "  $(c_dim "cleanup: fm delete $b")"
        # Without --all-sites a bench serving more than one site refuses, and survives the
        # case that made it: on a 3.7 GB host that is what breaks every later case.
        if ! "$FM_BIN" --non-interactive delete "$b" --yes --all-sites --delete-fm-managed-db \
            >>"$CURRENT_LOG" 2>&1; then
            log "  $(c_red 'LEAK') cleanup could not delete $b -- later cases may fail"
        fi
    done
    CASE_CLEANUP=()
    # A bake leaves its build cache behind, and that cache is what actually fills the disk:
    # the kept fixture images are a few GB, the cache is tens. Dangling layers only -- the
    # tagged fixtures are deliberately kept so the next run does not re-bake.
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -f >/dev/null 2>&1 || true
}

# A full disk does not fail one case, it fails every case after it, and the report then reads
# like fm regressed. Measured twice: a batch filled 75 GB and every verdict after that point
# was junk. The floor is 25 GB because ONE image case can consume 20 GB on its own -- a 10 GB
# floor passed its check and then filled the disk inside the very case it had just cleared.
E2E_MIN_FREE_GB="${E2E_MIN_FREE_GB:-25}"

free_gb() { df -BG --output=avail / | tail -1 | tr -dc '0-9'; }

run_case() {
    local id="$1" start end secs status avail
    avail="$(free_gb)"
    if [[ -n "$avail" && "$avail" -lt "$E2E_MIN_FREE_GB" ]]; then
        printf '%s %sG free, floor is %sG. Stopping before %s rather than reporting junk.\n' \
            "$(c_red 'DISK')" "$avail" "$E2E_MIN_FREE_GB" "$id" >&2
        printf 'Reclaim with: ./scripts/e2e/matrix/run.sh --clean-images && docker image prune -af\n' >&2
        exit 3
    fi
    CURRENT_LOG="$E2E_RUN_DIR/logs/$id.log"
    CASE_FAILURES=0
    CASE_CLEANUP=()
    mkdir -p "$(dirname "$CURRENT_LOG")"
    : >"$CURRENT_LOG"

    printf '%s %s %s\n' "$(c_yellow "▶ $id")" "${CASE_DESC[$id]}" "" >&2
    start=$(date +%s)
    "case_$id" || CASE_FAILURES=$((CASE_FAILURES + 1))
    _run_cleanup
    end=$(date +%s)
    secs=$((end - start))

    if [[ $CASE_FAILURES -eq 0 ]]; then
        status=PASS
        printf '%s %s (%ss)\n' "$(c_green '  PASS')" "$id" "$secs" >&2
    else
        status=FAIL
        printf '%s %s (%ss, %s failed assertions) -- %s\n' \
            "$(c_red '  FAIL')" "$id" "$secs" "$CASE_FAILURES" "$CURRENT_LOG" >&2
    fi
    printf '%s\t%s\t%s\t%s\n' "$id" "$status" "$secs" "${CASE_DESC[$id]}" >>"$E2E_REPORT"
}
