from pathlib import Path
from rich import inspect
from ruamel.yaml import YAML
import platform
from ruamel.yaml.comments import (
    CommentedMap as OrderedDict,
    CommentedSeq as OrderedList,
)
from frappe_manager.compose_manager.compose_file_exceptions import ComposeFileException
from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.utils.site import parse_docker_volume
from frappe_manager.utils.helpers import represent_null_empty
import importlib.resources as pkg_resources
from frappe_manager.migration_manager.version import Version

yaml = YAML(typ="rt")
yaml.representer.ignore_aliases = lambda *args: True

# Set the default flow style to None to preserve the null representation
yaml.default_flow_style = False
yaml.default_style = None


class ComposeFile:
    def __init__(self, loadfile: Path, template_name: str = "docker-compose.tmpl"):
        self.compose_path: Path = loadfile
        self.template_name = template_name
        self.is_template_loaded = False
        self.yml = None
        self.init()

    def init(self):
        """
        Initializes the ComposeFile object.
        """
        if self.exists():
            with open(self.compose_path, "r") as f:
                self.yml = yaml.load(f)
        else:
            self.yml = self.load_template()
            self.is_template_loaded = True

    def exists(self):
        """
        Check if the compose file exists.

        Returns:
            bool: True if the compose file exists, False otherwise.
        """
        return self.compose_path.exists()

    def get_compose_path(self):
        """
        Returns the path of the compose file.
        """
        return self.compose_path

    def get_template(self, file_name: str):
        """
        Get the file path of a template.

        Args:
            file_name (str): The name of the template file.
            template_directory (str, optional): The directory where the templates are located. Defaults to "templates".

        Returns:
            Optional[str]: The file path of the template, or None if the template is not found.
        """

        try:
            template_path = f"templates/{file_name}"
            return Path(
                str(pkg_resources.files("frappe_manager").joinpath(template_path))
            )
        except FileNotFoundError as e:
            richprint.error(f"{file_name} template not found.",e)

    def load_template(self):
        """
        Load the template file and return its contents as a YAML object.

        Returns:
            dict: The contents of the template file as a YAML object.
        """
        template_path = self.get_template(self.template_name)
        if template_path:
            with open(template_path, "r") as f:
                yml = yaml.load(f)
                return yml

    def set_container_names(self, prefix):
        """
        Sets the container names for each service in the Compose file.

        Args:
            prefix (str): The prefix to be added to the container names.
        """
        for service in self.yml["services"].keys():
            self.yml["services"][service]["container_name"] = prefix + f"-{service}"

    def get_container_names(self) -> dict:
        """
        Returns a dictionary of container names for each service defined in the Compose file.

        Returns:
            dict: A dictionary where the keys are service names and the values are container names.
        """
        container_names: dict = {}
        if self.exists():
            services = self.get_services_list()
            for service in services:
                container_names[service] = self.yml["services"][service][
                    "container_name"
                ]
        return container_names

    def get_services_list(self) -> list:
        """
        Returns a list of services defined in the Compose file.

        Returns:
            list: A list of service names.
        """
        return list(self.yml["services"].keys())

    def is_services_name_same_as_template(self):
        """
        Checks if the service names in the current Compose file are the same as the template file.

        Returns:
            bool: True if the service names are the same, False otherwise.
        """
        template_yml = self.load_template()
        template_service_name_list = list(template_yml["services"].keys())
        template_service_name_list.sort()
        current_service_name_list = list(self.yml["services"].keys())
        current_service_name_list.sort()
        return current_service_name_list == template_service_name_list

    def set_user(self, service, uid, gid):
        """
        Set the user for a specific service in the Compose file.

        Args:
            service (str): The name of the service.
            uid (str): The user ID.
            gid (str): The group ID.
        """
        try:
            self.yml["services"][service]["user"] = f"{uid}:{gid}"
        except KeyError as e:
            richprint.error("Issue in docker template. Not able to set user.",e)

    def get_user(self, service):
        """
        Get the user associated with the specified service.

        Args:
            service (str): The name of the service.

        Returns:
            str or None: The user associated with the service, or None if not found.
        """
        try:
            user = self.yml[service]["user"]
            uid = user.split(":")[0]
            uid = user.split(":")[1]

        except KeyError:
            return None
        return user

    def set_top_networks_name(self, networks_name, prefix):
        """
        Sets the name of the top-level network in the Compose file.

        Args:
            networks_name (str): The name of the network.
            prefix (str): The prefix to be added to the network name.
        """
        if not self.yml["networks"][networks_name]:
            self.yml["networks"][networks_name] = {"name": prefix + f"-network"}
        else:
            self.yml["networks"][networks_name]["name"] = prefix + f"-network"

    def set_network_alias(self, service_name, network_name, alias: list = []):
        """
        Sets the network alias for a given service in the Compose file.

        Args:
            service_name (str): The name of the service.
            network_name (str): The name of the network.
            alias (list, optional): List of network aliases to be set. Defaults to [].

        Returns:
            bool: True if the network alias is set successfully, False otherwise.
        """
        if alias:
            try:
                all_networks = self.yml["services"][service_name]["networks"]
                if network_name in all_networks:
                    self.yml["services"][service_name]["networks"][network_name] = {
                        "aliases": alias
                    }
                    return True
            except KeyError as e:
                return False
        else:
            return False

    def get_network_alias(self, service_name, network_name) -> list | None:
        """
        Retrieves the network aliases for a given service and network name.

        Args:
            service_name (str): The name of the service.
            network_name (str): The name of the network.

        Returns:
            list | None: A list of network aliases if found, otherwise None.
        """
        try:
            all_networks = self.yml["services"][service_name]["networks"]
            if not network_name in all_networks:
                return None

            aliases = self.yml["services"][service_name]["networks"][network_name][
                "aliases"
            ]
            return aliases
        except KeyError as e:
            return None

    def get_version(self):
        """
        Get the version of the compose file.

        Returns:
            int: The version of the compose file, or 0 if the version is not specified.
        """
        try:
            compose_version = self.yml["x-version"]
            return Version(compose_version)
        except KeyError:
            return Version("0.0.0")

    def set_version(self, version):
        """
        Sets the version of the Compose file.

        Args:
            version (str): The version to set.

        Returns:
            None
        """
        self.yml["x-version"] = version

    def get_all_users(self):
        """
        Retrieves a dictionary of all users defined in the Compose file.

        Returns:
            dict: A dictionary where the keys are service names and the values are dictionaries
                  containing the user's UID and GID.
        """
        users: dict = {}

        if self.exists():
            services = self.get_services_list()
            for service in services:
                if "user" in self.yml["services"][service]:
                    user_data = self.yml["services"][service]["user"]
                    uid = user_data.split(":")[0]
                    gid = user_data.split(":")[1]
                    users[service] = {"uid": uid, "gid": gid}
        return users

    def set_all_users(self, users: dict):
        """
        Sets the UID and GID for all services in the ComposeFile.

        Args:
            users (dict): A dictionary containing the service names as keys and the UID and GID as values.
        """
        for service in users.keys():
            self.set_user(service, users[service]["uid"], users[service]["gid"])

    def get_all_envs(self):
        """
        Retrieves all the environment variables for each service in the Compose file.

        Returns:
            dict: A dictionary containing the service names as keys and their respective environment variables as values.
        """
        envs = {}
        for service in self.yml["services"].keys():
            try:
                env = self.yml["services"][service]["environment"]
                envs[service] = env
            except KeyError:
                pass
        return envs

    def set_all_envs(self, environments: dict):
        """
        Sets environment variables for all containers in the Compose file.

        Args:
            environments (dict): A dictionary containing container names as keys and environment variables as values.

        """
        for container_name in environments.keys():
            self.set_envs(container_name, environments[container_name], append=True)

    def get_all_labels(self):
        """
        Retrieves all the labels for each service in the Compose file.

        Returns:
            dict: A dictionary containing the service names as keys and their respective labels as values.
        """
        labels = {}
        for service in self.yml["services"].keys():
            try:
                label = self.yml["services"][service]["labels"]
                labels[service] = label
            except KeyError:
                pass
        return labels

    def set_all_labels(self, labels: dict):
        """
        Sets labels for all containers in the ComposeFile.

        Args:
            labels (dict): A dictionary containing container names as keys and labels as values.
        """
        for container_name in labels.keys():
            self.set_labels(container_name, labels[container_name])

    def get_all_extrahosts(self):
        """
        Returns a dictionary of all the extra hosts for each service in the Compose file.

        Returns:
            dict: A dictionary where the keys are the service names and the values are the extra hosts.
        """
        extrahosts = {}
        for service in self.yml["services"].keys():
            try:
                extrahost = self.yml["services"][service]["extra_hosts"]
                extrahosts[service] = extrahost
            except KeyError:
                pass
        return extrahosts

    def set_all_extrahosts(self, extrahosts: dict, skip_not_found: bool = False):
        """
        Sets the extrahosts for all containers in the ComposeFile.

        Args:
            extrahosts (dict): A dictionary containing container names as keys and their corresponding extrahosts as values.
            skip_not_found (bool, optional): If True, skips setting extrahosts for containers that are not found. Defaults to False.
        """
        for container_name in extrahosts.keys():
            self.set_extrahosts(container_name, extrahosts[container_name])

    def set_envs(self, container: str, env: dict, append=False):
        """
        Sets the environment variables for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            env (dict): A dictionary containing the environment variables to be set.
            append (bool, optional): If True, appends the new environment variables to the existing ones.
        """
        new_env = OrderedDict(env)

        if append and type(env) == dict:
            prev_env = self.get_envs(container)
            if prev_env:
                if not type(prev_env) == OrderedList:
                    env = OrderedDict(env)
                    new_env = prev_env | env

        try:
            self.yml["services"][container]["environment"] = new_env
        except KeyError as e:
            pass

    def get_envs(self, container: str) -> dict:
        """
        Get the environment variables for a specific container.

        Args:
            container (str): The name of the container.

        Returns:
            dict: A dictionary containing the environment variables for the container.
                  Returns None if the container or environment variables are not found.
        """
        try:
            env = self.yml["services"][container]["environment"]
            return env
        except KeyError:
            return None

    def set_labels(self, container: str, labels: dict):
        """
        Sets the labels for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            labels (dict): A dictionary containing the labels to be set.

        """
        try:
            self.yml["services"][container]["labels"] = labels
        except KeyError as e:
            pass

    def get_labels(self, container: str) -> dict:
        """
        Get the labels of a specific container.

        Args:
            container (str): The name of the container.

        Returns:
            dict: The labels of the container, or None if the container or labels are not found.
        """
        try:
            labels = self.yml["services"][container]["labels"]
            return labels
        except KeyError:
            return None

    def set_extrahosts(self, container: str, extrahosts: list):
        """
        Set the extra hosts for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            extrahosts (list): A list of extra hosts to be added.

        """
        try:
            self.yml["services"][container]["extra_hosts"] = extrahosts
        except KeyError as e:
            pass

    def get_extrahosts(self, container: str) -> list:
        """
        Get the extra hosts for a specific container.

        Args:
            container (str): The name of the container.

        Returns:
            list: A list of extra hosts for the container, or None if not found.
        """
        try:
            extra_hosts = self.yml["services"][container]["extra_hosts"]
            return extra_hosts
        except KeyError:
            return None

    def write_to_file(self):
        """
        Writes the Docker Compose file to the specified path.
        """
        try:
            # saving the docker compose to the directory
            with open(self.compose_path, "w") as f:
                yaml.dump(self.yml, f, transform=represent_null_empty)
        except Exception as e:
            richprint.error(f"Error in writing compose file.",e)

    def get_all_volumes(self):
        """
        Get all the root volumes.
        """

        volumes = self.yml["volumes"]

        return volumes

    def get_all_services_volumes(self):
        """
        Get all the volume mounts.
        """
        volumes_set = set()

        services = self.get_services_list()

        for service in services:
            try:
                volumes_list = self.yml["services"][service]["volumes"]
                for volume in volumes_list:
                    volumes_set.add(volume)
            except KeyError as e:
                continue

        volumes_list = []

        for volume in volumes_set:
            volumes_list.append((parse_docker_volume(volume)))

        return volumes_list

    def set_secret_file_path(self, secret_name, file_path):
        try:
            self.yml["secrets"][secret_name]["file"] = file_path
        except KeyError:
            richprint.warning("Not able to set secrets in compose.")

    def get_secret_file_path(self, secret_name):
        try:
            file_path = self.yml["secrets"][secret_name]["file"]
            return file_path
        except KeyError:
            richprint.warning("Not able to set secrets in compose.")

    def remove_secrets_from_container(self, container):
        try:
            del self.yml["services"][container]["secrets"]
        except KeyError:
            richprint.warning(f"Not able to remove secrets from {container}.")

    def remove_root_secrets_compose(self):
        try:
            del self.yml["secrets"]
        except KeyError:
            richprint.warning(f"root level secrets not present.")

    def remove_container_user(self, container):
        try:
            del self.yml["services"][container]["user"]
        except KeyError:
            richprint.warning(f"user not present.")

    def get_all_images(self):
        """
        Retrieves all the images for each service in the Compose file.

        Returns:
            dict: A dictionary containing the service names as keys and their respective image names and tags as values.
        """
        images = {}
        for service in self.yml["services"].keys():
            try:
                image = self.yml["services"][service]["image"]
                name, tag = image.split(":") if ":" in image else (image, "latest")
                images[service] = {"name": name, "tag": tag}
            except KeyError:
                pass
        return images

    def set_all_images(self, images: dict):
        """
        Sets the image for all services in the ComposeFile.

        Args:
            images (dict): A dictionary containing the service names as keys and the image names and tags as values.
        """
        for service, image_info in images.items():
            image = f'{image_info["name"]}:{image_info["tag"]}'
            if service in self.yml["services"]:
                self.yml["services"][service]["image"] = image
