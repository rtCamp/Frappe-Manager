#!/usr/bin/env sh
if [ "$1" = "-m" ]; then
  if [ "$2" = "venv" ]; then
    shift 2
    exec /usr/local/bin/uv venv --seed "$@"
  elif [ "$2" = "pip" ]; then
    shift 2
    exec /usr/local/bin/uv pip "$@"
  fi
fi
exec /opt/user/python/$(basename "$0") "$@"
