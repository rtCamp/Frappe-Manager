import typer
from typing import Annotated, Optional
from pathlib import Path
import os
from frappe_manager import (
    CLI_BENCHES_DIRECTORY,
    CLI_BENCH_CONFIG_FILE_NAME,
    STABLE_APP_BRANCH_MAPPING_LIST,
)

from frappe_manager.ssl_manager import SUPPORTED_SSL_TYPES
from frappe_manager.site_manager.SiteManager import BenchesManager
from frappe_manager.compose_manager.ComposeFile import ComposeFile
from frappe_manager.compose_project.compose_project import ComposeProject
from frappe_manager.site_manager.bench_config import BenchConfig, FMBenchEnvType
from frappe_manager.site_manager.site import Bench
from frappe_manager.ssl_manager.certificate import SSLCertificate
from frappe_manager.utils.callbacks import (
    sites_autocompletion_callback,
    sitename_callback
)
from frappe_manager.commands import app

@app.command()
def delete(
    ctx: typer.Context,
    benchname: Annotated[
        Optional[str],
        typer.Argument(
            help="Name of the bench.", autocompletion=sites_autocompletion_callback, callback=sitename_callback
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force delete bench.")] = False,
):
    """Delete a bench."""

    if benchname:
        services_manager = ctx.obj["services"]
        verbose = ctx.obj['verbose']
        benches = BenchesManager(CLI_BENCHES_DIRECTORY, services=services_manager, verbose=verbose)
        benches.set_typer_context(ctx)

        bench_path: Path = benches.root_path / benchname
        bench_compose_path = bench_path / 'docker-compose.yml'
        compose_file_manager = ComposeFile(bench_compose_path)
        bench_compose_project = ComposeProject(compose_file_manager)

        bench_config_path = bench_path / CLI_BENCH_CONFIG_FILE_NAME
        # try using bench object if not then create bench

        if not bench_config_path.exists():
            uid: int = os.getuid()
            gid: int = os.getgid()

            # generate fake bench
            fake_config = BenchConfig(
                name=benchname,
                userid=uid,
                usergroup=gid,
                apps_list=[],
                frappe_branch=STABLE_APP_BRANCH_MAPPING_LIST['frappe'],
                developer_mode=False,
                # TODO do something about this forcefully delete maybe
                admin_tools=False,
                admin_pass='pass',
                environment_type=FMBenchEnvType.dev,
                ssl=SSLCertificate(domain=benchname, ssl_type=SUPPORTED_SSL_TYPES.none),
                root_path=bench_config_path,
            )

            bench = Bench(
                bench_path,
                benchname,
                fake_config,
                bench_compose_project,
                services=services_manager,
                workers_check=False,
            )

        else:
            bench = Bench.get_object(benchname, services=services_manager, workers_check=False, admin_tools_check=False)

        benches.add_bench(bench)
        benches.remove_benches()
