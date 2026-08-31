"""What `fm reset` is allowed to do, and how the password it carries is quoted.

Two things about `bench reinstall`, both about a database fm does not own:

D04 -- `reinstall` drops and recreates the site's schema. On the `global-db` container fm owns
that is the whole point of `fm reset`. On a server named by a `[database]` entry it is the exact
operation `fm delete` refuses to perform ("the schema is not fm's to drop"), and reaching it means
first sending the global-db ROOT password -- which means nothing on that host -- through a MySQL
auth handshake and into the container's process listing. So the external case is refused before
any argv is built.

D09 -- the argv is a list joined into one string, and `compose.exec` shlex-splits that string
again. An unquoted password containing a space fragments into extra positional arguments (bench
aborts with "unexpected extra argument"); one containing an apostrophe raises
`ValueError: No closing quotation` from inside the docker wrapper, nowhere near the user's input.
Both levels are quoted, so what `bench` sees is one token equal to what the operator typed.

Nothing here touches Docker: `docker_client.compose` is a mock and the assertions read the
`command` kwarg it was handed, which is the last string fm controls.
"""

import shlex
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from frappe_manager.site_manager.bench_config import BenchConfig
from frappe_manager.site_manager.exceptions import BenchOperationException
from frappe_manager.site_manager.modules.bench_site import BenchSiteManager

GLOBAL_DB_SITE = "local.localhost"
EXTERNAL_SITE = "app.example.com"
EXTERNAL_HOST = "mydb.abc.rds.amazonaws.com"
SCHEMA = "app_prod"
ROOT_USER = "root"
ROOT_PASSWORD = "global-db-root-secret"

# Passwords an operator can legitimately choose. Each one breaks a different way when joined into
# a shell string unquoted: word splitting, an unbalanced quote, a command separator.
TRICKY_PASSWORDS = [
    "my secret pw",
    "it's-me",
    'say "hi"',
    "a;rm -rf /x",
    "back\\slash",
    "$(whoami)",
    "plain-ascii-1234",
]


def _config(tmp_path: Path, *, name: str, external_site: str | None = None) -> BenchConfig:
    """A real BenchConfig so the per-site `[database]` lookup under test is the real one."""
    toml = f'name = "{name}"\ndeveloper_mode = false\nadmin_tools = false\nenvironment = "prod"\n'
    if external_site:
        toml += f'\n[sites."{external_site}".database]\nhost = "{EXTERNAL_HOST}"\nname = "{SCHEMA}"\nuser = "app_svc"\n'
    path = tmp_path / f"{name}.toml"
    path.write_text(toml)
    return BenchConfig.import_from_toml(path)


def _manager(config: BenchConfig, site: str | None = None) -> BenchSiteManager:
    """A BenchSiteManager with the REAL `_container_run`, stopped at the compose seam.

    Keeping `_container_run` real is deliberate: the outer `/bin/bash -c` wrapping is half of the
    quoting, so a test that captured the command before it is applied would pass on a broken wrap.
    """
    manager = object.__new__(BenchSiteManager)  # bypass __init__ (no Docker, no services stack)
    manager.bench_name = site or config.name
    manager.bench_cli_cmd = ["bench"]
    manager.bench_config = config
    manager.docker_client = MagicMock()
    manager.output = MagicMock()
    info = manager.services = MagicMock()
    info.database_manager.database_server_info.user = ROOT_USER
    info.database_manager.database_server_info.password = ROOT_PASSWORD
    info.database_manager.database_server_info.host = "global-db"
    info.database_manager.database_server_info.port = 3306
    return manager


def _commands(manager: BenchSiteManager) -> list[str]:
    """Every `command` string handed to `compose.exec`, in order."""
    return [call.kwargs["command"] for call in manager.docker_client.compose.exec.call_args_list]


def _argv(exec_command: str) -> list[str]:
    """The argv `bench` really receives.

    Two decodings, because there are two of them in production: `compose.exec` shlex-splits the
    string fm built (docker_compose.py, `use_shlex_split=True`), and the `bash -c` that survives
    that split parses its script argument.
    """
    parts = shlex.split(exec_command, posix=True)
    assert parts[:2] == ["/bin/bash", "-c"], exec_command
    assert len(parts) == 3, f"the wrapper leaked extra words: {parts}"
    return shlex.split(parts[2], posix=True)


def _value_of(argv: list[str], flag: str) -> str:
    """The single token following `flag`; raises if the flag fragmented or vanished."""
    assert argv.count(flag) == 1, f"{flag} appears {argv.count(flag)} times in {argv}"
    return argv[argv.index(flag) + 1]


# --------------------------------------------------------------- D04: whose schema is being dropped


def test_reset_refuses_a_site_whose_database_fm_does_not_own(tmp_path):
    """The refusal `fm delete` already makes, at the one other seam that would drop the schema."""
    manager = _manager(_config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE))

    with pytest.raises(BenchOperationException) as excinfo:
        manager.reset_bench_site("new-admin-pass")

    message = str(excinfo.value)
    assert EXTERNAL_HOST in message  # name the host, so the operator knows who owns it
    assert SCHEMA in message
    # Refused while still an argv: no handshake, no reinstall, nothing in a process listing.
    manager.docker_client.compose.exec.assert_not_called()
    manager.docker_client.compose.run.assert_not_called()


def test_the_refusal_never_builds_the_root_credential_into_a_command(tmp_path):
    """Even if the refusal were downgraded to a warning, this is the payload at stake."""
    manager = _manager(_config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE))

    with pytest.raises(BenchOperationException):
        manager.reset_bench_site("new-admin-pass")

    assert ROOT_PASSWORD not in " ".join(_commands(manager))


def test_the_refusal_resolves_per_site_not_per_bench(tmp_path):
    """`--site <name>` is what reinstall acts on, so the lookup follows that name.

    One bench, two sites: the `global-db` one resets, the external one is refused.
    """
    config = _config(tmp_path, name=GLOBAL_DB_SITE, external_site=EXTERNAL_SITE)

    _manager(config, site=GLOBAL_DB_SITE).reset_bench_site("pw")  # no raise

    with pytest.raises(BenchOperationException, match=EXTERNAL_HOST):
        _manager(config, site=EXTERNAL_SITE).reset_bench_site("pw")


def test_reset_on_global_db_still_sends_the_root_credential(tmp_path):
    """Unchanged behaviour on the container fm owns: this is the reset that must keep working."""
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))

    manager.reset_bench_site("new-admin-pass")

    argv = _argv(_commands(manager)[0])
    assert argv[:4] == ["bench", "--site", GLOBAL_DB_SITE, "reinstall"]
    assert _value_of(argv, "--db-root-username") == ROOT_USER
    assert _value_of(argv, "--db-root-password") == ROOT_PASSWORD
    assert _value_of(argv, "--admin-password") == "new-admin-pass"
    assert "--yes" in argv


# ------------------------------------------------------- D09: the password as one argument, always


@pytest.mark.parametrize("password", TRICKY_PASSWORDS)
def test_a_reset_password_reaches_bench_as_exactly_one_argument(tmp_path, password):
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))

    manager.reset_bench_site(password)

    argv = _argv(_commands(manager)[0])
    assert _value_of(argv, "--admin-password") == password
    # `--yes` is the last thing appended: a fragmented password puts stray words after it, which
    # bench reads as positional arguments it has no parameter for.
    assert argv[-1] == "--yes"


def test_a_reset_root_password_reaches_bench_as_exactly_one_argument(tmp_path):
    """The global-db root password is generated, but it is joined into the same string."""
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))
    manager.services.database_manager.database_server_info.password = "r00t pass'word"

    manager.reset_bench_site("pw")

    assert _value_of(_argv(_commands(manager)[0]), "--db-root-password") == "r00t pass'word"


@pytest.mark.parametrize("password", TRICKY_PASSWORDS)
def test_a_create_admin_password_reaches_bench_as_exactly_one_argument(tmp_path, password):
    """`fm create` builds its argv the same way; the global-db branch, where new-site provisions."""
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))

    manager.create_bench_site(admin_pass=password)

    argv = _argv(_commands(manager)[0])
    assert "new-site" in argv
    assert _value_of(argv, "--admin-password") == password
    assert _value_of(argv, "--db-root-password") == ROOT_PASSWORD
    assert argv[-1] == GLOBAL_DB_SITE  # the site name is still the only positional argument


@pytest.mark.parametrize("password", TRICKY_PASSWORDS)
def test_a_create_admin_password_survives_on_the_external_branch_too(tmp_path, password):
    """The external branch sends an admin password but no root password; quoting is the same."""
    manager = _manager(_config(tmp_path, name=EXTERNAL_SITE, external_site=EXTERNAL_SITE))

    manager.create_bench_site(admin_pass=password)

    argv = _argv(_commands(manager)[0])
    assert _value_of(argv, "--admin-password") == password
    assert "--db-root-password" not in argv
    assert argv[-1] == EXTERNAL_SITE


@pytest.mark.parametrize("script", ["echo it's fine", 'echo "a b"', "python3 -c 'print(1)'"])
def test_the_exec_wrapper_delivers_the_command_verbatim(tmp_path, script):
    """The wrapper alone. `/bin/bash -c '<command>'` came apart on any inner apostrophe -- the
    ValueError surfaced from inside the docker wrapper, nowhere near the input that caused it --
    and silently dropped the quotes when the count happened to balance.

    Only the OUTER decoding is asserted: whatever `bash -c` then makes of the script is the
    caller's business, and that is exactly the property the wrapper has to preserve.
    """
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))

    manager._container_run(script)

    assert shlex.split(_commands(manager)[0], posix=True) == ["/bin/bash", "-c", script]


def test_the_run_path_wrapper_survives_it_as_well(tmp_path):
    """`use_run=True` folds the workdir in first, then wraps; both must round-trip."""
    manager = _manager(_config(tmp_path, name=GLOBAL_DB_SITE))

    manager._container_run("echo it's fine", use_run=True, workdir="/wd")

    command = manager.docker_client.compose.run.call_args.kwargs["command"]
    parts = shlex.split(command, posix=True)
    assert parts == ["/bin/bash", "-c", "cd /wd && echo it's fine"]
