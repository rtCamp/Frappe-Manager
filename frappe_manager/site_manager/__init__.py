VSCODE_LAUNCH_JSON = {
    "version": "0.2.0",
    "configurations": [
        {
            "name": "fm-frappe-debug",
            "type": "debugpy",
            "request": "launch",
            "program": "/workspace/frappe-bench/apps/frappe/frappe/utils/bench_helper.py",
            "preLaunchTask": "fm-kill-port",
            "args": ["frappe", "serve", "--port", "80", "--noreload", "--nothreading"],
            "cwd": "/workspace/frappe-bench/sites",
            "consoleName": "Frappe Debug",
            "env": {"DEV_SERVER": "1"},
            "justMyCode": False,
        },
        {
            "name": "Debug Specific Queue",
            "type": "debugpy",
            "request": "launch",
            "program": "/workspace/frappe-bench/apps/frappe/frappe/utils/bench_helper.py",
            "args": [
                "frappe",
                "worker",
                "--queue",
                "${input:queue}",
            ],
            "consoleName": "${input:queue} worker",
            "cwd": "/workspace/frappe-bench/sites",
        },
        {
            "name": "Debug specific fuction",
            "type": "debugpy",
            "request": "launch",
            "program": "/workspace/frappe-bench/apps/frappe/frappe/utils/bench_helper.py",
            "cwd": "/workspace/frappe-bench/sites",
            "args": [
                "frappe",
                "execute",
                "${input:executable_path}",
            ],
            "consoleName": "Frappe Function",
        },
    ],
    "inputs": [
        {
            "id": "queue",
            "type": "command",
            "command": "extension.commandvariable.promptStringRemember",
            "args": {
                "key": "frappe_queue_name",
                "description": "Enter the queue name to debug.",
            },
        },
        {
            "id": "executable_path",
            "type": "command",
            "command": "extension.commandvariable.promptStringRemember",
            "args": {
                "key": "frappe_executable_path",
                "description": "Enter the path to frappe executable function",
            },
        },
    ],
}

VSCODE_TASKS_JSON = {
    "version": "2.0.0",
    "tasks": [
        {
            "label": "fm-kill-port",
            "type": "shell",
            "command": "/bin/bash",
            "args": ["-c", "supervisorctl -c /opt/user/supervisord.conf stop all && sleep 2"],
            "presentation": {"reveal": "never", "panel": "dedicated"},
            "options": {"ignoreExitCode": True},
            "problemMatcher": [],
        }
    ],
}

VSCODE_SETTINGS_JSON = {
    "python.defaultInterpreterPath": "/workspace/frappe-bench/env/bin/python",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": True,
        "editor.codeActionsOnSave": {"source.fixAll": True, "source.organizeImports": True},
        "editor.detectIndentation": True,
        "editor.tabSize": 4,
        "editor.insertSpaces": True,
    },
    "ruff.organizeImports": True,
    "ruff.fixAll": True,
    "ruff.lint.run": "onSave",
}

PREBAKED_SITE_APPS = {
    "https://github.com/frappe/frappe": "version-15",
    "https://github.com/frappe/erpnext": "version-15",
    "https://github.com/frappe/hrms": "version-15",
}
