"""
Regression tests for BenchDockerOps.execute_command command quoting.

Historically the command string was built as f'{shell_path} -c "{command}"' and
then shlex-split by the compose wrapper (use_shlex_split=True). That naive
double-quote wrapping mangled any script containing double quotes, spaces inside
quotes, `$`, `&&`, etc. — e.g. `echo "=== cwd ==="` was split into
['echo ===', 'cwd', ...] and only printed `===`.

The fix uses shlex.quote so the built string shlex-splits back to exactly
[shell_path, "-c", command], preserving the script verbatim.
"""

import shlex
from unittest.mock import MagicMock

from frappe_manager.site_manager.modules.bench_docker import BenchDockerOps


def _make_ops():
    """A BenchDockerOps with docker interactions mocked, service 'running'."""
    ops = BenchDockerOps.__new__(BenchDockerOps)
    ops.docker_client = MagicMock()
    ops.output = MagicMock()
    ops.docker_client.compose.get_all_services_status.return_value = [
        {"Service": "frappe", "State": "running"}
    ]
    result = MagicMock()
    result.stdout = []
    result.stderr = []
    result.exit_code = 0
    ops.docker_client.compose.exec.return_value = result
    ops.docker_client.compose.run.return_value = result
    return ops


def _captured_command(exec_mock):
    """The `command` kwarg passed to the compose wrapper on its last call."""
    return exec_mock.call_args.kwargs["command"]


TRICKY_SCRIPTS = [
    'echo "=== cwd ==="\npwd',
    'echo "hello world"',
    'echo "a=b c"',
    "echo 'single quoted'",
    'echo $HOME && echo "done"',
    'python3 -c "print(1+2)"',
    'grep -R "needle" .',
    "echo a\necho b\necho c",
]


class TestExecuteCommandQuoting:
    """The command must reach `bash -c` verbatim regardless of quoting."""

    def test_exec_command_roundtrips_verbatim(self):
        for script in TRICKY_SCRIPTS:
            ops = _make_ops()
            code = ops.execute_command("frappe", script, user="frappe")
            assert code == 0
            built = _captured_command(ops.docker_client.compose.exec)
            # The compose wrapper shlex-splits this; it must reconstruct exactly.
            assert shlex.split(built, posix=True) == ["/bin/bash", "-c", script], script

    def test_run_command_roundtrips_verbatim(self):
        for script in TRICKY_SCRIPTS:
            ops = _make_ops()
            code = ops.execute_command("frappe", script, user="frappe", use_run=True)
            assert code == 0
            built = _captured_command(ops.docker_client.compose.run)
            assert shlex.split(built, posix=True) == ["/bin/bash", "-c", script], script

    def test_custom_shell_path_is_honoured(self):
        ops = _make_ops()
        ops.execute_command("frappe", 'echo "x y"', user="frappe", shell_path="/bin/sh")
        built = _captured_command(ops.docker_client.compose.exec)
        assert shlex.split(built, posix=True) == ["/bin/sh", "-c", 'echo "x y"']
