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

VSCODE_SETTINGS_JSON = {
  "python.defaultInterpreterPath": "/workspace/frappe-bench/env/bin/python",
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter",
    "editor.detectIndentation": True,
    "editor.tabSize": 4,
    "editor.insertSpaces": True,
  },
  "black-formatter.importStrategy" : "useBundled"
}

PREBAKED_SITE_APPS= {
    "https://github.com/frappe/frappe": "version-15",
    "https://github.com/frappe/erpnext": "version-15",
    "https://github.com/frappe/hrms": "version-15",
}
