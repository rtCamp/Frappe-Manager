"""`Docker/frappe/bench-dev-server`: which site a `dev` bench's `bench serve` answers for.

`bench serve`'s own CLI pins its Werkzeug process to ONE site at startup, reading
`default_site` out of `common_site_config.json` the moment no `--site`/`FRAPPE_SITE` is
given -- which is always, for fm's launch. On a bench with one site the pin matches the
only site there is, so it looks like per-request Host routing; on two or more it means
every hostname answers with the SAME site's data regardless of the Host header, with a
200 and no error anywhere. The fix: for a bench serving more than one site, the script
builds a throwaway sites directory of symlinks back to the real site folders, with its
own `common_site_config.json` carrying no `default_site`, and points frappe's `SITES_PATH`
at it, so frappe's dev server leaves its site pin unset and resolves each request by its
own Host header instead.

These tests run the real script under `bash`, with a stub `bench` recording what it was
invoked with and `SITES_DIR`/`FM_DEV_MULTISITE_SITES_PATH`/`BENCH_BIN` pointed at a
sandbox, so a regression here is caught on the actual shipped artifact rather than a
reimplementation of it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "Docker" / "frappe" / "bench-dev-server"


def _write_site(sites_dir: Path, name: str) -> None:
    site_dir = sites_dir / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": f"db_{name}"}))


def _write_common_config(sites_dir: Path, default_site: str | None) -> None:
    conf = {"developer_mode": True, "webserver_port": 80}
    if default_site is not None:
        conf["default_site"] = default_site
    (sites_dir / "common_site_config.json").write_text(json.dumps(conf))


def _write_stub_bench(bin_path: Path, record_file: Path) -> None:
    # Mirrors the two calls the real script makes: a `serve --help` probe (answered with
    # no `--host` flag, so HOST_ARG stays empty and that branch is not what this test is
    # about) and the real `serve` invocation, whose env and argv land in `record_file`.
    bin_path.write_text(
        "#!/bin/bash\n"
        'if [[ " $* " == *" --help "* ]]; then\n'
        '    echo "usage: bench serve [OPTIONS]"\n'
        "    exit 0\n"
        "fi\n"
        "{\n"
        '    echo "SITES_PATH=${SITES_PATH-<unset>}"\n'
        '    echo "ARGS=$*"\n'
        f'}} > "{record_file}"\n'
        "exit 0\n"
    )
    bin_path.chmod(0o755)


def _run_dev_server(tmp_path: Path, sites_dir: Path) -> tuple[dict[str, str], Path]:
    bench_bin = tmp_path / "bench"
    record_file = tmp_path / "bench-invocation.txt"
    farm_path = tmp_path / "farm"
    _write_stub_bench(bench_bin, record_file)

    env = dict(os.environ)
    env.update(
        {
            "BENCH_BIN": str(bench_bin),
            "SITES_DIR": str(sites_dir),
            "FM_DEV_MULTISITE_SITES_PATH": str(farm_path),
        }
    )
    # exe from shutil.which, the script is a fixed repo path: nothing here is user input.
    bash_exe = shutil.which("bash")
    subprocess.run([bash_exe, str(SCRIPT)], env=env, timeout=10, check=True)  # noqa: S603

    recorded: dict[str, str] = {}
    for line in record_file.read_text().splitlines():
        key, _, value = line.partition("=")
        recorded[key] = value
    return recorded, farm_path


class TestSingleSiteBenchIsUnaffected:
    def test_bench_serve_runs_with_no_sites_path_override(self, tmp_path):
        """One site: no farm, no `SITES_PATH` -- identical to the pre-fix invocation."""
        sites_dir = tmp_path / "sites"
        sites_dir.mkdir()
        _write_common_config(sites_dir, default_site="only.localhost")
        _write_site(sites_dir, "only.localhost")

        recorded, farm_path = _run_dev_server(tmp_path, sites_dir)

        assert recorded["SITES_PATH"] == "<unset>"
        assert not farm_path.exists()


class TestMultiSiteBenchLosesItsSitePin:
    def test_default_site_does_not_reach_bench_serve(self, tmp_path):
        """Two sites: `bench serve` must see a `SITES_PATH` whose config has no
        `default_site` key, the exact fact that let it pin to one site before."""
        sites_dir = tmp_path / "sites"
        sites_dir.mkdir()
        _write_common_config(sites_dir, default_site="primary.localhost")
        _write_site(sites_dir, "primary.localhost")
        _write_site(sites_dir, "secondary.localhost")

        recorded, farm_path = _run_dev_server(tmp_path, sites_dir)

        assert recorded["SITES_PATH"] == str(farm_path)
        farm_config = json.loads((farm_path / "common_site_config.json").read_text())
        assert "default_site" not in farm_config
        assert farm_config["developer_mode"] is True  # other keys survive the copy

    def test_farm_symlinks_every_real_site_back_to_its_own_directory(self, tmp_path):
        """A request for `secondary.localhost` must resolve `site_config.json` for
        `secondary.localhost`, not whichever site the farm happens to list first."""
        sites_dir = tmp_path / "sites"
        sites_dir.mkdir()
        _write_common_config(sites_dir, default_site="primary.localhost")
        _write_site(sites_dir, "primary.localhost")
        _write_site(sites_dir, "secondary.localhost")

        _, farm_path = _run_dev_server(tmp_path, sites_dir)

        for name in ("primary.localhost", "secondary.localhost"):
            link = farm_path / name
            assert link.is_symlink()
            assert link.resolve() == (sites_dir / name).resolve()
            # Following the symlink reaches the REAL site's own config, not a copy.
            assert json.loads((link / "site_config.json").read_text()) == {"db_name": f"db_{name}"}

    def test_farm_is_rebuilt_from_scratch_so_a_removed_site_does_not_linger(self, tmp_path):
        """A site removed since the last run must not leave a stale symlink the real
        `fm create BENCH/SITE`/`fm delete BENCH/SITE` restart cycle would otherwise hide."""
        sites_dir = tmp_path / "sites"
        sites_dir.mkdir()
        _write_common_config(sites_dir, default_site="primary.localhost")
        _write_site(sites_dir, "primary.localhost")
        _write_site(sites_dir, "secondary.localhost")

        farm_path = tmp_path / "farm"
        farm_path.mkdir()
        (farm_path / "stale.localhost").symlink_to(tmp_path)  # leftover from a prior run

        _run_dev_server(tmp_path, sites_dir)

        assert not (farm_path / "stale.localhost").exists()
        assert (farm_path / "primary.localhost").exists()
        assert (farm_path / "secondary.localhost").exists()

    def test_common_site_config_itself_is_not_symlinked_over_the_stripped_copy(self, tmp_path):
        """The farm's whole point is a `common_site_config.json` that differs from the
        real one; symlinking it instead would put `default_site` right back."""
        sites_dir = tmp_path / "sites"
        sites_dir.mkdir()
        _write_common_config(sites_dir, default_site="primary.localhost")
        _write_site(sites_dir, "primary.localhost")
        _write_site(sites_dir, "secondary.localhost")

        _, farm_path = _run_dev_server(tmp_path, sites_dir)

        assert not (farm_path / "common_site_config.json").is_symlink()


@pytest.mark.skipif(not SCRIPT.exists(), reason="Docker/frappe/bench-dev-server not found")
def test_script_exists_at_the_expected_path():
    assert SCRIPT.is_file()
