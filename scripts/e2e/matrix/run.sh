#!/usr/bin/env bash
# Run the fm end-to-end matrix. See notes/e2e-matrix.md for what each case defends.
#
#   ./run.sh                 every case, in declaration order
#   ./run.sh B               every case in group B
#   ./run.sh B8 C1 G2        those cases
#   ./run.sh --list          ids and descriptions, run nothing
#   ./run.sh --clean-images  delete the kept fixture images (~7 GB), run nothing
#
# Cases never stop the run. A failure is recorded and the next case starts, because the
# hour already spent getting to case 40 is the expensive part.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"

for f in "$HERE"/cases/*.sh; do
    [[ -e "$f" ]] || continue
    # shellcheck disable=SC1090
    source "$f"
done

if [[ ${#CASE_IDS[@]} -eq 0 ]]; then
    echo "no cases found under $HERE/cases/" >&2
    exit 2
fi

if [[ "${1:-}" == "--list" ]]; then
    for id in "${CASE_IDS[@]}"; do printf '%-5s %s\n' "$id" "${CASE_DESC[$id]}"; done
    exit 0
fi

# Fixture images are kept on purpose: a bake costs 5-10 minutes on the host this runs on, so
# the second run is free. This is how you get the ~7 GB back.
if [[ "${1:-}" == "--clean-images" ]]; then
    mapfile -t stale < <(docker images --format '{{.Repository}}:{{.Tag}}' |
        grep -E '^(fm-e2e-shared-appimage|localhost/fm-e2e-matrix/|local/e2e)' || true)
    if [[ ${#stale[@]} -eq 0 ]]; then
        echo "no e2e fixture images present" >&2
        exit 0
    fi
    printf '%s\n' "${stale[@]}" >&2
    docker rmi -f "${stale[@]}"
    exit 0
fi

selected=()
if [[ $# -eq 0 ]]; then
    selected=("${CASE_IDS[@]}")
else
    for want in "$@"; do
        for id in "${CASE_IDS[@]}"; do
            # A bare group letter selects the whole group; an id selects one case.
            if [[ "$id" == "$want" || ( ${#want} -eq 1 && "$id" == "$want"* ) ]]; then
                selected+=("$id")
            fi
        done
    done
    if [[ ${#selected[@]} -eq 0 ]]; then
        echo "no case matches: $*" >&2
        exit 2
    fi
fi

if [[ ! -x "$FM_BIN" ]]; then
    echo "FM_BIN is not executable: $FM_BIN" >&2
    exit 2
fi

# A missing tool must stop the run here. Discovered the hard way: with no JSON reader the
# first case reported 41 failed assertions, all of them the harness rather than fm.
missing=()
for tool in python3 curl timeout docker; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "missing required tools: ${missing[*]}" >&2
    exit 2
fi

mkdir -p "$E2E_RUN_DIR/logs"
: >"$E2E_REPORT"
started=$(date +%s)

printf '%s %s cases, fm at %s\n\n' "$(c_yellow '●')" "${#selected[@]}" "$("$FM_BIN" --version 2>&1 | head -1)" >&2
for id in "${selected[@]}"; do run_case "$id"; done

total=$(($(date +%s) - started))
passed=$(grep -c $'\tPASS\t' "$E2E_REPORT" || true)
failed=$(grep -c $'\tFAIL\t' "$E2E_REPORT" || true)

printf '\n%s\n' "────────────────────────────────────────────" >&2
printf '%s passed, %s failed, %ss total\n' "$(c_green "$passed")" "$(c_red "$failed")" "$total" >&2
if [[ $failed -gt 0 ]]; then
    printf '\nfailed:\n' >&2
    awk -F'\t' '$2=="FAIL" {printf "  %-5s %s\n", $1, $4}' "$E2E_REPORT" >&2
    printf '\nlogs: %s\n' "$E2E_RUN_DIR/logs" >&2
fi
printf 'report: %s\n' "$E2E_REPORT" >&2
[[ $failed -eq 0 ]]
