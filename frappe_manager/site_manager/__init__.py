VSCODE_LAUNCH_JSON = {
    "version": "0.2.0",
    "configurations": [
        {
            "name": "fm-frappe-debug",
            "type": "debugpy",
            "request": "launch",
            "program": "/workspace/frappe-bench/apps/frappe/frappe/utils/bench_helper.py",
            "args": ["frappe", "serve", "--port", "80", "--noreload", "--nothreading"],
            "cwd": "/workspace/frappe-bench/sites",
            "env": {"DEV_SERVER": "1"},
            "preLaunchTask": "fm-kill-port-80",
        }
    ],
}

VSCODE_TASKS_JSON = {
    "version": "2.0.0",
    "tasks": [
        {
            "label": "fm-kill-port-80",
            "type": "shell",
            "command": "/bin/bash",
            "args": ["-c", "fuser -k 80/tcp || true"],
            "presentation": {"reveal": "never", "panel": "dedicated"},
            "options": {"ignoreExitCode": True},
        }
    ],
}
