from rich.table import Table
import re

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

    services_table.add_column(
        "Service Status", ratio=1, no_wrap=True, width=None, min_width=20
    )
    services_table.add_column(
        "Service Status", ratio=1, no_wrap=True, width=None, min_width=20
    )

    for index in range(0,len(services_status),2):
        first_service_table = None
        second_service_table = None

        try:
            first_service = list(services_status.keys())[index]
            first_service_table = create_service_element(first_service, services_status[first_service])
        except IndexError:
            pass

        try:
            second_service = list(services_status.keys())[index+1]
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

def parse_docker_volume(volume_string):

    string_parts = volume_string.split(':')

    if len(string_parts) > 1:

        volume = {"src": string_parts[0], "dest": string_parts[0]}

        is_bind_mount = string_parts[0].startswith('./')

        if len(string_parts) > 2:
            volume = {"src": string_parts[0], "dest": string_parts[1]}

        volume['type'] = 'bind'

        if not is_bind_mount:
            volume['type'] = 'volume'

        return volume

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

def domain_level(domain):
    # Split the domain name into individual parts
    parts = domain.split('.')

    # Return the number of parts minus 1 (excluding the TLD)
    return len(parts) - 1
