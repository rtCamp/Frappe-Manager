import json
import re
import subprocess
from pathlib import Path

from rich.table import Table

from frappe_manager import CLI_BENCHES_DIRECTORY
from frappe_manager.docker import DockerVolumeMount, DockerVolumeType
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.site_manager.exceptions import BenchException


def read_bench_python_version(frappe_bench_dir: Path) -> str | None:
    """Active Python version (e.g. "3.12.9") from the uv python-default symlink, or None."""
    symlink = frappe_bench_dir / ".uv" / "python-default"
    try:
        target = Path(symlink.readlink()).name  # cpython-3.12.9-linux-x86_64-gnu
        parts = target.split("-")
        return parts[1] if len(parts) > 1 else None
    except (OSError, IndexError):
        return None


def read_bench_node_version(frappe_bench_dir: Path) -> str | None:
    """Active Node version (e.g. "v22.11.0") from the fnm default alias symlink, or None."""
    symlink = frappe_bench_dir / ".fnm" / "aliases" / "default"
    try:
        target = symlink.readlink()  # ../node-versions/v22.11.0/installation
        return next((p for p in target.parts if p.startswith("v")), None)
    except OSError:
        return None


def _git(app_path: Path, *args: str) -> str | None:
    """Run a git command in ``app_path`` (safe.directory bypasses ownership checks)."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-c", "safe.directory=*", "-C", str(app_path), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        return None
    return None


def read_bench_app_refs(frappe_bench_dir: Path) -> list[dict]:
    """Per-app ``{name, ref, commit}`` from git under ``apps/`` (apps.txt order, frappe first).

    ``ref`` is the branch or tag HEAD points at (``None`` when detached on a bare commit);
    ``commit`` is the short HEAD sha. Used to stamp image labels at bake and to show
    mount-mode apps directly from disk.
    """
    apps_dir = frappe_bench_dir / "apps"
    if not apps_dir.is_dir():
        return []
    names: list[str] = []
    apps_txt = frappe_bench_dir / "sites" / "apps.txt"
    if apps_txt.exists():
        names = [n.strip() for n in apps_txt.read_text().splitlines() if n.strip()]
    for child in sorted(apps_dir.iterdir()):
        if child.name not in names and (child / ".git").exists():
            names.append(child.name)
    if "frappe" in names:
        names = ["frappe", *[n for n in names if n != "frappe"]]

    apps: list[dict] = []
    for name in names:
        app_path = apps_dir / name
        if not (app_path / ".git").exists():
            continue
        commit = _git(app_path, "rev-parse", "--short", "HEAD")
        ref = _git(app_path, "symbolic-ref", "--short", "-q", "HEAD") or _git(
            app_path, "describe", "--tags", "--exact-match"
        )
        apps.append({"name": name, "ref": ref, "commit": commit})
    return apps


def generate_services_table(services_status: dict):
    # running site services status
    services_table = Table(
        show_lines=False,
        show_edge=False,
        pad_edge=False,
        show_header=False,
        expand=True,
        box=None,
    )

    services_table.add_column("Service Status", ratio=1, no_wrap=True, width=None, min_width=20)
    services_table.add_column("Service Status", ratio=1, no_wrap=True, width=None, min_width=20)

    for index in range(0, len(services_status), 2):
        first_service_table = None
        second_service_table = None

        try:
            first_service = list(services_status.keys())[index]
            first_service_table = create_service_element(first_service, services_status[first_service])
        except IndexError:
            pass

        try:
            second_service = list(services_status.keys())[index + 1]
            second_service_table = create_service_element(second_service, services_status[second_service])
        except IndexError:
            pass

        services_table.add_row(first_service_table, second_service_table)

    return services_table


def create_service_element(service, running_status):
    service_table = Table(
        show_lines=False,
        show_header=False,
        highlight=True,
        expand=True,
        box=None,
    )
    service_table.add_column("Service", justify="left", no_wrap=True)
    service_table.add_column("Status", justify="right", no_wrap=True)
    service_status = "\u2713" if running_status == "running" else "\u2718"
    service_table.add_row(
        f"{service}",
        f"{service_status}",
    )
    return service_table


def parse_docker_volume(volume_string: str, root_volumes: dict, compose_path: Path):
    string_parts = volume_string.split(":")

    if len(string_parts) > 1:
        src = string_parts[0]
        dest = string_parts[0]

        is_bind_mount = True

        if string_parts[0] in root_volumes:
            is_bind_mount = False

        if len(string_parts) > 1:
            dest = string_parts[1]

        volume_type = DockerVolumeType.bind

        if not is_bind_mount:
            volume_type = DockerVolumeType.volume

        docker_volume = DockerVolumeMount(src, dest, volume_type, compose_path)

        return docker_volume


def is_fqdn(hostname: str) -> bool:
    """
    https://en.m.wikipedia.org/wiki/Fully_qualified_domain_name
    """
    if not 1 < len(hostname) < 253:
        return False

    # Remove trailing dot
    if hostname[-1] == ".":
        hostname = hostname[0:-1]

    #  Split hostname into list of DNS labels
    labels = hostname.split(".")

    #  Define pattern of DNS label
    #  Can begin and end with a number or letter only
    #  Can contain hyphens, a-z, A-Z, 0-9
    #  1 - 63 chars allowed
    fqdn = re.compile(r"^[a-z0-9]([a-z-0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)

    # Check that all labels match that pattern.
    return all(fqdn.match(label) for label in labels)


def is_wildcard_fqdn(hostname: str) -> bool:
    """
    Check if the hostname is a fully qualified domain name (FQDN) with optional wildcard.

    A wildcard domain can be specified with a leading asterisk in the first label (e.g., *.example.com).
    https://en.m.wikipedia.org/wiki/Fully_qualified_domain_name
    """
    if not 1 < len(hostname) < 253:
        return False

    # Remove trailing dot
    if hostname[-1] == ".":
        hostname = hostname[:-1]

    # Split hostname into list of DNS labels
    labels = hostname.split(".")

    # Define pattern for a standard DNS label
    fqdn_pattern = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)

    # Define pattern for a wildcard DNS label (only valid in the first label)
    wildcard_pattern = re.compile(r"^\*\.?$", re.IGNORECASE)

    status = (wildcard_pattern.match(labels[0])) and all(fqdn_pattern.match(label) for label in labels[1:])

    if status == None:
        status = False

    # Check the first label for wildcard pattern, then check all labels for standard pattern
    return status


def domain_level(domain):
    # Split the domain name into individual parts
    parts = domain.split(".")

    # Return the number of parts minus 1 (excluding the TLD)
    return len(parts) - 1


def validate_sitename(sitename: str | None) -> str:
    if sitename is None:
        raise ValueError("Sitename cannot be None")

    match = is_fqdn(sitename)

    if domain_level(sitename) == 0:
        sitename = sitename + ".localhost"

    if not match:
        output = get_global_output_handler()
        output.error(
            f"The {sitename} must follow Fully Qualified Domain Name (FQDN) format.",
            exception=BenchException(sitename, "Valid FQDN site name not provided."),
        )

    return sitename


def get_bench_db_connection_info(bench_name: str, bench_path: Path):
    db_info = {}
    site_config_file = bench_path / "workspace" / "frappe-bench" / "sites" / bench_name / "site_config.json"
    common_site_config_file = bench_path / "workspace" / "frappe-bench" / "sites" / "common_site_config.json"

    db_info["password"] = None

    if common_site_config_file.exists():
        with open(common_site_config_file) as f:
            common_site_config = json.load(f)
            if common_site_config:
                db_info["host"] = common_site_config.get("db_host")
                db_info["port"] = common_site_config.get("db_port")

    if site_config_file.exists():
        with open(site_config_file) as f:
            site_config = json.load(f)
            if site_config:
                db_info["name"] = site_config["db_name"]
                db_info["user"] = site_config["db_name"]
                db_info["password"] = site_config["db_password"]
                if "db_host" in site_config:
                    db_info["host"] = site_config["db_host"]
                if "db_port" in site_config:
                    db_info["port"] = site_config["db_port"]

    return db_info


def get_all_docker_images():
    from frappe_manager.docker import ComposeFile

    temp_bench_compose_file_manager = ComposeFile(loadfile=Path("/dev/null/docker-compose.yml"))
    services_manager_compose_file_manager = ComposeFile(
        loadfile=Path("/dev/null/docker-compose.yml"),
        template_name="docker-compose.services.tmpl",
    )
    admin_tools_manager_compose_file_manager = ComposeFile(
        loadfile=Path("/dev/null/docker-compose.yml"),
        template_name="docker-compose.admin-tools.tmpl",
    )

    images = temp_bench_compose_file_manager.get_all_images()

    images.update(services_manager_compose_file_manager.get_all_images())
    images.update(admin_tools_manager_compose_file_manager.get_all_images())

    return images


def pull_docker_images() -> bool:
    from frappe_manager.docker import DockerClient, DockerException

    docker = DockerClient()
    images = get_all_docker_images()
    images_list = []

    for _service, image_info in images.items():
        image = f"{image_info['name']}:{image_info['tag']}"
        images_list.append(image)

    # remove duplicates
    images_list = list(dict.fromkeys(images_list))

    no_error = True
    for image in images_list:
        output = get_global_output_handler()
        status = f"[blue]Pulling image[/blue] [bold][yellow]{image}[/yellow][/bold]"
        output.change_head(status, style=None)
        try:
            pull_output = docker.pull(container_name=image, stream=True)
            output.live_lines(pull_output, padding=(0, 0, 0, 2))
        except DockerException as e:
            no_error = False
            output.error(f"[bold][red]Error [/bold][/red]: Failed to pull {image}", e)
        output.print(f"[green]Pulled[/green] [blue]{image}[/blue]")

    return no_error


def get_sitename_from_current_path() -> str | None:
    current_path = Path().absolute()
    sites_path = CLI_BENCHES_DIRECTORY.absolute()

    if not current_path.is_relative_to(sites_path):
        return None

    sitename_list = list(current_path.relative_to(sites_path).parts)

    if not sitename_list:
        return None

    sitename = sitename_list[0]
    if is_fqdn(sitename):
        return sitename


def is_default_worker(worker_name: str) -> bool:
    default_workers = ["long-worker", "short-worker"]

    for dw in default_workers:
        if dw == worker_name:
            return True

    return False
