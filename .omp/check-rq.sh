#!/bin/bash
# Did the drain leave RQ suspended? The flag lives in redis, so it outlives the command that
# set it: a drain without a matching resume is a silent outage (nothing processes the queue).
cd ~/fm-src || exit 1
./.venv/bin/fm shell audit <<'SHELL' 2>&1 | tail -4
/workspace/frappe-bench/env/bin/python - <<'PY'
import redis
r = redis.Redis(host="redis-queue", port=6379)
print("rq:suspended =", r.get("rq:suspended"))
PY
SHELL
