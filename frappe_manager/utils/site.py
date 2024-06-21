from rich.table import Table
from pathlib import Path
import re
import json

from frappe_manager.compose_manager import DockerVolumeMount, DockerVolumeType
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.site_manager.site_exceptions import BenchException


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

    string_parts = volume_string.split(':')

    if len(string_parts) > 1:

        src = string_parts[0]
        dest = string_parts[0]

        is_bind_mount = True

        if string_parts[0] in root_volumes.keys():
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
    if hostname[-1] == '.':
        hostname = hostname[0:-1]

    #  Split hostname into list of DNS labels
    labels = hostname.split('.')

    #  Define pattern of DNS label
    #  Can begin and end with a number or letter only
    #  Can contain hyphens, a-z, A-Z, 0-9
    #  1 - 63 chars allowed
    fqdn = re.compile(r'^[a-z0-9]([a-z-0-9-]{0,61}[a-z0-9])?$', re.IGNORECASE)

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
    if hostname[-1] == '.':
        hostname = hostname[:-1]

    # Split hostname into list of DNS labels
    labels = hostname.split('.')

    # Define pattern for a standard DNS label
    fqdn_pattern = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', re.IGNORECASE)

    # Define pattern for a wildcard DNS label (only valid in the first label)
    wildcard_pattern = re.compile(r'^\*\.?$', re.IGNORECASE)

    status = (wildcard_pattern.match(labels[0])) and all(fqdn_pattern.match(label) for label in labels[1:])

    if status == None:
        status = False

    # Check the first label for wildcard pattern, then check all labels for standard pattern
    return status


def domain_level(domain):
    # Split the domain name into individual parts
    parts = domain.split('.')

    # Return the number of parts minus 1 (excluding the TLD)
    return len(parts) - 1


def validate_sitename(sitename: str) -> str:
    match = is_fqdn(sitename)

    if domain_level(sitename) == 0:
        sitename = sitename + ".localhost"

    if not match:
        richprint.error(
            f"The {sitename} must follow Fully Qualified Domain Name (FQDN) format.",
            exception=BenchException(sitename, f"Valid FQDN site name not provided."),
        )

    return sitename


def get_bench_db_connection_info(bench_name: str, bench_path: Path):
    db_info = {}
    site_config_file = bench_path / "workspace" / "frappe-bench" / "sites" / bench_name / "site_config.json"
    if site_config_file.exists():
        with open(site_config_file, "r") as f:
            site_config = json.load(f)
            db_info["name"] = site_config["db_name"]
            db_info["user"] = site_config["db_name"]
            db_info["password"] = site_config["db_password"]
    else:
        db_info["name"] = str(bench_name).replace(".", "-")
        db_info["user"] = str(bench_name).replace(".", "-")
        db_info["password"] = None
    return db_info


def get_all_docker_images():
    from frappe_manager.compose_manager.ComposeFile import ComposeFile

    temp_bench_compose_file_manager = ComposeFile(loadfile=Path('/dev/null/docker-compose.yml'))
    services_manager_compose_file_manager = ComposeFile(
        loadfile=Path('/dev/null/docker-compose.yml'), template_name='docker-compose.services.tmpl'
    )
    admin_tools_manager_compose_file_manager = ComposeFile(
        loadfile=Path('/dev/null/docker-compose.yml'), template_name='docker-compose.admin-tools.tmpl'
    )

    images = temp_bench_compose_file_manager.get_all_images()
    images.update(services_manager_compose_file_manager.get_all_images())
    images.update(admin_tools_manager_compose_file_manager.get_all_images())
    return images


def pull_docker_images() -> bool:
    from frappe_manager.docker_wrapper.DockerException import DockerException
    from frappe_manager.docker_wrapper.DockerClient import DockerClient

    docker = DockerClient()
    images = get_all_docker_images()
    images_list = []

    for service, image_info in images.items():
        image = f"{image_info['name']}:{image_info['tag']}"
        images_list.append(image)

    # remove duplicates
    images_list = list(dict.fromkeys(images_list))

    no_error = True
    for image in images_list:
        status = f"[blue]Pulling image[/blue] [bold][yellow]{image}[/yellow][/bold]"
        richprint.change_head(status, style=None)
        try:
            output = docker.pull(container_name=image, stream=True)
            richprint.live_lines(output, padding=(0, 0, 0, 2))
        except DockerException as e:
            no_error = False
            richprint.error(f"[bold][red]Error [/bold][/red]: Failed to pull {image}.")
        richprint.print(f"[green]Pulled[/green] [blue]{image}[/blue].")

    return no_error
