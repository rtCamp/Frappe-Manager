#!/usr/bin/env bash
# Check that a host can receive `fm switch`, before anything is built.
#
# `fm switch` has to run on the machine that owns the bench: it rewrites that bench's
# config and compose file, drains its workers, dumps its database and moves its traffic.
# Driving it over SSH has one non-obvious failure mode, which is the reason this exists:
#
#   ssh host "fm switch ..."
#
# gets a NON-INTERACTIVE shell. That reads neither .bashrc nor .profile, so PATH is the
# bare system default. An fm installed by `uv tool install` lives in ~/.local/bin and is
# invisible there: `command -v fm` returns nothing on a host where fm is installed and
# working. So fm is resolved explicitly, against the places it actually gets installed.
#
# Run it by hand before wiring up CI, or to reproduce a CI failure:
#
#   scripts/deploy-preflight.sh --host prod.example.com --user deploy
#
# In CI the private key and known_hosts arrive through the environment rather than argv,
# because argv is visible to every other process on the box.

set -euo pipefail

HOST=""
USER_NAME=""
PORT="22"
KEY_FILE=""
KNOWN_HOSTS_FILE=""
FM_PATH=""
WORKDIR=""
GITHUB_OUTPUT_FILE=""
KEYSCAN="no"

# Where fm actually ends up, in the order worth trying. PATH first so an explicit
# system install wins; then uv's tool dir, which is the case that motivated all this.
FM_CANDIDATE_DIRS=("\$HOME/.local/bin" "/usr/local/bin" "/usr/bin")

usage() {
  cat <<'USAGE'
Usage: deploy-preflight.sh --host HOST --user USER [options]

Options:
  --host HOST            host that owns the bench (required)
  --user USER            SSH user on that host (required)
  --port PORT            SSH port (default 22)
  --key-file PATH        private key file; or pass the key CONTENT in SSH_PRIVATE_KEY
  --known-hosts PATH     known_hosts file; or pass its CONTENT in SSH_KNOWN_HOSTS
  --keyscan              no known_hosts supplied: accept whatever ssh-keyscan returns.
                         Trust on first use. Without this, ssh uses your own config.
  --fm-path PATH         absolute path to fm on the target, skipping discovery
  --workdir DIR          where generated files go (default: a temp dir, removed on exit)
  --github-output FILE   append key=, known-hosts= and fm-bin= to FILE
  -h, --help             this text

Environment:
  SSH_PRIVATE_KEY   private key content, written to WORKDIR with mode 600
  SSH_KNOWN_HOSTS   known_hosts content, written to WORKDIR

Exit status:
  0  the host is reachable and fm runs there
  1  bad usage, unreachable host, or no working fm
USAGE
}

log()  { printf '%s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host)          HOST="${2:-}"; shift 2 ;;
    --user)          USER_NAME="${2:-}"; shift 2 ;;
    --port)          PORT="${2:-}"; shift 2 ;;
    --key-file)      KEY_FILE="${2:-}"; shift 2 ;;
    --known-hosts)   KNOWN_HOSTS_FILE="${2:-}"; shift 2 ;;
    --keyscan)       KEYSCAN="yes"; shift ;;
    --fm-path)       FM_PATH="${2:-}"; shift 2 ;;
    --workdir)       WORKDIR="${2:-}"; shift 2 ;;
    --github-output) GITHUB_OUTPUT_FILE="${2:-}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *)               usage >&2; fail "unknown argument: $1" ;;
  esac
done

[ -n "$HOST" ] || { usage >&2; fail "--host is required"; }
[ -n "$USER_NAME" ] || { usage >&2; fail "--user is required"; }

if [ -n "$WORKDIR" ]; then
  mkdir -p "$WORKDIR"
else
  WORKDIR="$(mktemp -d)"
  # Only clean up a directory we created. A caller-supplied one is the caller's.
  trap 'rm -rf "$WORKDIR"' EXIT
fi
chmod 700 "$WORKDIR"

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=15 -p "$PORT")

if [ -n "${SSH_PRIVATE_KEY:-}" ]; then
  KEY_FILE="$WORKDIR/id"
  printf '%s\n' "$SSH_PRIVATE_KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
fi
if [ -n "$KEY_FILE" ]; then
  [ -f "$KEY_FILE" ] || fail "key file not found: $KEY_FILE"
  # IdentitiesOnly stops ssh trying agent keys first and tripping MaxAuthTries.
  ssh_opts+=(-i "$KEY_FILE" -o IdentitiesOnly=yes)
fi

if [ -n "${SSH_KNOWN_HOSTS:-}" ]; then
  KNOWN_HOSTS_FILE="$WORKDIR/known_hosts"
  printf '%s\n' "$SSH_KNOWN_HOSTS" > "$KNOWN_HOSTS_FILE"
elif [ -z "$KNOWN_HOSTS_FILE" ] && [ "$KEYSCAN" = "yes" ]; then
  KNOWN_HOSTS_FILE="$WORKDIR/known_hosts"
  log "warning: no known_hosts supplied; trusting the key ssh-keyscan returns"
  # -H hashes hostnames, matching what ssh writes itself. A non-default port is recorded
  # as [host]:port by keyscan and looked up the same way by ssh, so the two agree.
  ssh-keyscan -p "$PORT" -H "$HOST" > "$KNOWN_HOSTS_FILE" 2>/dev/null || true
  [ -s "$KNOWN_HOSTS_FILE" ] || fail "ssh-keyscan got no key from ${HOST}:${PORT}: wrong host or port, or the port is filtered"
fi
if [ -n "$KNOWN_HOSTS_FILE" ]; then
  [ -f "$KNOWN_HOSTS_FILE" ] || fail "known_hosts file not found: $KNOWN_HOSTS_FILE"
  # Strict, because the file is now authoritative: an unknown key is a failure, not a prompt.
  ssh_opts+=(-o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN_HOSTS_FILE")
fi

target="${USER_NAME}@${HOST}"

log "Preflight ${target} (port ${PORT})"

# Reachability first, with its own message. ssh returns 255 for its own failures and the
# remote command's status otherwise, so without this check an auth or host-key problem
# would surface below as "fm was not found", which sends the reader after the wrong thing.
if ! ssh "${ssh_opts[@]}" "$target" true 2>"$WORKDIR/ssh.err"; then
  log ""
  log "Could not open an SSH session to ${target} on port ${PORT}:"
  sed 's/^/  /' "$WORKDIR/ssh.err" >&2 || true
  log ""
  log "BatchMode is on, so an interactive password prompt counts as a failure."
  log "Check the key is authorized for ${USER_NAME}, and that the host key matches."
  fail "cannot reach ${target}"
fi

# One probe, run remotely, so discovery costs a single connection.
probe=$(cat <<'REMOTE'
for candidate in $FM_CANDIDATES; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then printf '%s\n' "$candidate"; exit 0; fi
done
exit 1
REMOTE
)

# $HOME must expand on the far side, so the candidate list is built as remote-shell text.
candidates="\$(command -v fm 2>/dev/null)"
if [ -n "$FM_PATH" ]; then
  candidates="$FM_PATH $candidates"
fi
for dir in "${FM_CANDIDATE_DIRS[@]}"; do
  candidates="$candidates $dir/fm"
done

# $candidates is built here and MUST expand client-side; only the `$(command -v fm)` part
# inside it is escaped, so that one runs on the target.
# shellcheck disable=SC2029
if ! fm_bin=$(ssh "${ssh_opts[@]}" "$target" "FM_CANDIDATES=\"$candidates\" bash -s" <<< "$probe"); then
  log ""
  log "fm was not found on ${target}."
  log "Looked on PATH, then: $(printf '%s ' "${FM_CANDIDATE_DIRS[@]}")"
  log ""
  log "Install it there, e.g.  uv tool install frappe-manager"
  log "or pass --fm-path /absolute/path/to/fm if it lives elsewhere."
  fail "no usable fm on ${target}"
fi

# Present is not working: an interrupted install still leaves the shim behind. fm prints
# --version to stderr, hence the redirect.
# $fm_bin is the path we just discovered, so client-side expansion is the point.
# shellcheck disable=SC2029
if ! fm_version=$(ssh "${ssh_opts[@]}" "$target" "'$fm_bin' --version 2>&1" | tr -d '\r'); then
  fail "found $fm_bin on ${target} but it failed to run"
fi

log "fm: ${fm_bin} (${fm_version# })"

if [ -n "$GITHUB_OUTPUT_FILE" ]; then
  {
    printf 'key=%s\n' "$KEY_FILE"
    printf 'known-hosts=%s\n' "$KNOWN_HOSTS_FILE"
    printf 'fm-bin=%s\n' "$fm_bin"
  } >> "$GITHUB_OUTPUT_FILE"
fi

printf '%s\n' "$fm_bin"
