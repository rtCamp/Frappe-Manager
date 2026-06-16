# fnm environment setup — respects externally passed env vars
# If FNM_DIR is already set (e.g. via docker exec --env), use it and skip fnm env.

if [ -n "${FNM_DIR:-}" ]; then
    _FNM_DIR_EXTERNAL=1
fi

export FNM_DIR="${FNM_DIR:-/workspace/frappe-bench/.fnm}"
export FNM_MULTISHELL_PATH="${FNM_MULTISHELL_PATH:-/workspace/frappe-bench/.fnm}"
export FNM_COREPACK_ENABLED="${FNM_COREPACK_ENABLED:-true}"

if [ -z "${_FNM_DIR_EXTERNAL:-}" ]; then
    eval "$(fnm env --shell bash 2>/dev/null || true)"
fi
unset _FNM_DIR_EXTERNAL

if [ -d "/workspace/frappe-bench/.uv/python-default/bin" ]; then
    export PATH="/workspace/frappe-bench/.uv/python-default/bin:$PATH"
fi

export PATH="/opt/user/.bin:$PATH"
