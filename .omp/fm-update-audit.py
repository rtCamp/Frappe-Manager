#!/usr/bin/env python3
"""Instrumented audit harness for `fm update`, run ON the fm docker host.

For each step it captures, around a single `fm update` invocation:
  - every bench container's docker ID + StartedAt  (a changed ID == recreated,
    a changed StartedAt with the same ID == restarted)
  - sha256 of every file fm could rewrite (composes, bench_config.toml,
    common_site_config.json, site_config.json, nginx confs, supervisor confs)
  - the docker event stream for the command's own window, so "extra restart"
    is counted from the daemon, not inferred from output text

Usage:  fm-update-audit.py <bench> <label> -- <fm args...>
        fm-update-audit.py <bench> snapshot
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
FM = str(HOME / "fm-src" / ".venv" / "bin" / "fm")


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw).stdout.strip()


def containers(bench):
    """id + StartedAt + restart policy per container, for this bench only."""
    names = sh(
        "docker ps -a --format '{{.Names}}' | grep -E '^fm__%s__' | sort" % bench
    ).splitlines()
    out = {}
    for n in [x for x in names if x]:
        raw = sh(
            "docker inspect %s --format "
            "'{{.Id}}|{{.State.StartedAt}}|{{.State.Status}}|{{.HostConfig.RestartPolicy.Name}}'" % n
        )
        if "|" in raw:
            cid, started, status, policy = raw.split("|")
            out[n] = {"id": cid[:12], "started": started, "status": status, "restart": policy}
    return out


def env_of(bench, service, keys):
    raw = sh(
        "docker inspect fm__%s__%s --format '{{range .Config.Env}}{{println .}}{{end}}'" % (bench, service)
    )
    got = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k in keys:
                got[k] = v
    return got


def files(bench):
    root = HOME / "frappe" / "sites" / bench
    wanted = [
        "bench_config.toml",
        "docker-compose.yml",
        "workspace/frappe-bench/sites/common_site_config.json",
        f"workspace/frappe-bench/sites/{bench}.localhost/site_config.json",
        "configs/nginx/conf/conf.d/default.conf",
        "configs/nginx/conf/custom/upload-limit.conf",
        "workspace/frappe-bench/config/fm-web-server.sh",
        "workspace/frappe-bench/config/newrelic.ini",
        "workers/docker-compose.workers.yml",
        "admin-tools/docker-compose.admin-tools.yml",
    ]
    out = {}
    for rel in wanted:
        p = root / rel
        if p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        else:
            out[rel] = "ABSENT"
    return out


def diff(before, after):
    keys = sorted(set(before) | set(after))
    rows = []
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b != a:
            rows.append((k, b, a))
    return rows


def main():
    bench = sys.argv[1]
    label = sys.argv[2]

    if label == "snapshot":
        print(json.dumps({"containers": containers(bench), "files": files(bench)}, indent=2))
        return 0

    args = sys.argv[sys.argv.index("--") + 1 :]

    c0, f0 = containers(bench), files(bench)

    # Daemon-side truth for restarts/recreations during this command only.
    ev = subprocess.Popen(
        ["docker", "events", "--filter", "type=container",
         "--format", "{{.Actor.Attributes.name}} {{.Action}}"],
        stdout=subprocess.PIPE, text=True,
    )
    time.sleep(0.6)

    t0 = time.time()
    proc = subprocess.run([FM, "update", *args], capture_output=True, text=True,
                          cwd=str(HOME / "fm-src"))
    elapsed = time.time() - t0

    time.sleep(1.2)
    ev.terminate()
    events = [
        ln.strip() for ln in (ev.stdout.read() or "").splitlines()
        if f"fm__{bench}__" in ln and any(
            ln.endswith(a) for a in ("create", "start", "stop", "kill", "die", "destroy", "restart")
        )
    ]

    c1, f1 = containers(bench), files(bench)

    print("=" * 78)
    print(f"STEP {label}:  fm update {' '.join(args)}")
    print(f"exit={proc.returncode}  wall={elapsed:.1f}s")
    print("-- stdout (trimmed) " + "-" * 58)
    body = (proc.stdout or "") + (proc.stderr or "")
    for ln in [x for x in body.splitlines() if x.strip()][-28:]:
        print("  " + ln)
    print("-- container churn " + "-" * 59)
    rows = diff(c0, c1)
    if not rows:
        print("  none (no container touched)")
    for name, b, a in rows:
        if b is None:
            print(f"  + {name}: NEW {a['id']}")
        elif a is None:
            print(f"  - {name}: GONE")
        else:
            kind = "RECREATED" if b["id"] != a["id"] else "restarted-in-place"
            bits = [f"{kind} {b['id']}->{a['id']}"]
            if b["restart"] != a["restart"]:
                bits.append(f"restart-policy {b['restart']}->{a['restart']}")
            if b["started"] != a["started"] and b["id"] == a["id"]:
                bits.append(f"StartedAt {b['started']}->{a['started']}")
            print(f"  ~ {name}: {'; '.join(bits)}")
    print("-- daemon events " + "-" * 61)
    counts: dict = {}
    for e in events:
        counts[e] = counts.get(e, 0) + 1
    if not counts:
        print("  none")
    for e, n in sorted(counts.items()):
        print(f"  {n}x {e}")
    print("-- file churn " + "-" * 64)
    frows = diff(f0, f1)
    if not frows:
        print("  none (no file rewritten)")
    for name, b, a in frows:
        print(f"  ~ {name}: {b} -> {a}")
    print("-- newrelic/frappe env " + "-" * 55)
    print("  " + json.dumps(env_of(bench, "frappe", {"NEWRELIC_ENABLED", "NEWRELIC_LICENSE_KEY", "FRAPPE_ENV"})))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
