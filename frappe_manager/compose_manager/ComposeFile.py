import pkgutil
from pathlib import Path
import yaml
from typing import List
import typer

from frappe_manager.display_manager.DisplayManager import richprint
from frappe_manager.compose_manager.utils import represent_none

yaml.representer.ignore_aliases = lambda *args: True


class ComposeFile:
    def __init__(self, loadfile: Path, template_name: str = "docker-compose.tmpl"):
        self.compose_path: Path = loadfile
        self.template_name = template_name
        self.is_template_loaded = False
        self.yml = None
        self.init()

    def init(self):
        """
        The function initializes a YAML object by loading data from a file if it exists, otherwise it
        creates a new YAML object using a template.
        """
        # if the load file not found then the site not exits
        if self.exists():
            with open(self.compose_path, "r") as f:
                self.yml = yaml.safe_load(f)
        else:
            template = self.get_template(self.template_name)
            self.yml = yaml.safe_load(template)
            self.is_template_loaded = True

    def exists(self):
        """
        The function checks if a file or directory exists at the specified path.
        :return: a boolean value, if compose file exits `True` else  `False`.
        """
        return self.compose_path.exists()

    def get_compose_path(self):
        """
        Getter for getting compose file path.
        :return: The returns compose file path.
        """
        return self.compose_path

    def get_template(
        self, file_name: str, template_directory="templates"
    ) -> None | str:
        """
        The function `get_template` retrieves a template file and returns its contents as a string, or
        raises an error if the template file is not found.

        :param file_name: The `file_name` parameter is a string that represents the name of the template
        file. It is used to construct the file path by appending it to the "templates/" directory
        :type file_name: str
        :return: either None or a string.
        """
        file_name = f"{template_directory}/{file_name}"
        try:
            data = pkgutil.get_data(__name__, file_name)
        except Exception as e:
            richprint.exit(f"{file_name} template not found! Error:{e}")
        yml = data.decode()
        return yml

    def load_template(self):
        template = self.get_template(self.template_name)
        self.yml = yaml.safe_load(template)

    def set_container_names(self, prefix):
        """
        The function sets the container names for each service in a compose file based on the site name.
        """

        for service in self.yml["services"].keys():
            self.yml["services"][service]["container_name"] = prefix + f"-{service}"

    def get_container_names(self) -> dict:
        """
        The function `get_container_names` returns a dictionary of container names for each service in a
        compose file.
        :return: a dictionary containing the names of the containers specified in the compose file.
        """
        container_names: dict = {}

        # site_name = self.compose_path.parent.name

        if self.exists():
            services = self.get_services_list()
            for service in services:
                container_names[service] = self.yml["services"][service][
                    "container_name"
                ]
                # container_names[service] = site_name.replace('.','') + f'-{service}'

        return container_names

    def get_services_list(self) -> list:
        """
        Getting for getting all docker compose services name as a list.
        :return: list of docker composer servicers.
        """
        return list(self.yml["services"].keys())

    def is_services_name_same_as_template(self):
        """
        The function checks if the service names in the current YAML file are the same as the service names
        in the template YAML file.
        :return: a boolean value indicating whether the list of service names in the current YAML file is
        the same as the list of service names in the template YAML file.
        """
        template = self.get_template(self.template_name)
        template_yml = yaml.safe_load(template)
        template_service_name_list = list(template_yml["services"].keys())
        template_service_name_list.sort()
        current_service_name_list = list(self.yml["services"].keys())
        current_service_name_list.sort()
        return current_service_name_list == template_service_name_list

    def set_user(self, service, uid, gid):
        try:
            self.yml["services"][service]["user"] = f"{uid}:{gid}"
        except KeyError:
            richprint.exit("Issue in docker template. Not able to set user.")

    def get_user(self, service):
        try:
            user = self.yml[service]["user"]
            uid = user.split(":")[0]
            uid = user.split(":")[1]

        except KeyError:
            return None
        return user

    def set_top_networks_name(self, networks_name, prefix):
        """
        The function sets the network names for each service in a compose file based on the site name.
        """

        if not self.yml["networks"][networks_name]:
            self.yml["networks"][networks_name] = { "name" : prefix + f"-network" }
        else:
            self.yml["networks"][networks_name]["name"] = prefix + f"-network"


    def set_network_alias(self, service_name, network_name, alias: list = []):
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
        try:
            all_networks = self.yml["services"][service_name]["networks"]
            if network_name in all_networks:
                aliases = self.yml["services"][service_name]["networks"][network_name][
                    "aliases"
                ]
            return aliases
        except KeyError as e:
            return None
        else:
            return None

    def get_version(self):
        """
        The function `get_version` returns the value of the `x-version` key from composer file, or
        `None` if the key is not present.
        :return: the value of the 'x-version', if not found then
        it returns None.
        """
        try:
            compose_version = self.yml["x-version"]
        except KeyError:
            return 0
        return compose_version

    def set_version(self, version):
        """
        The function sets the value of the 'x-version' key in a YAML dictionary to the specified version.

        :param version: current fm version to set it to "x-version" key in the compose file.
        """
        self.yml["x-version"] = version

    def get_all_users(self):
        """
        The function `get_all_users` returns a dictionary of users for each service in a compose file.
        :return: a dictionary containing the users of the containers specified in the compose file.
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
        The function `set_all_users` sets the users for each service in a compose file.

        :param users: The `users` parameter is a dictionary that contains users for each service in a
        compose file.
        """
        for service in users.keys():
            self.set_user(service, users[service]["uid"], users[service]["gid"])

    def get_all_envs(self):
        """
        This functtion returns all the container environment variables
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
        This functtion returns all the container environment variables
        """
        for container_name in environments.keys():
            self.set_envs(container_name, environments[container_name], append=True)

    def get_all_labels(self):
        """
        This functtion returns all the container labels variables
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
        This functtion returns all the container environment variables
        """
        for container_name in labels.keys():
            self.set_labels(container_name, labels[container_name])

    def get_all_extrahosts(self):
        """
        This functtion returns all the container labels variables
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
        This functtion returns all the container environment variables
        """
        for container_name in extrahosts.keys():
            self.set_extrahosts(container_name, extrahosts[container_name])

    def set_envs(self, container: str, env: dict, append=False):
        """
        The function `set_envs` sets environment variables for a given container in a compose file.

        :param container: A string representing the name of the container
        :type container: str
        :param env: The `env` parameter is a dictionary that contains environment variables. Each key-value
        pair in the dictionary represents an environment variable, where the key is the variable name and
        the value is the variable value
        :type env: dict
        """
        # change dict to list
        if append and type(env) == dict:
            prev_env = self.get_envs(container)
            if prev_env:
                new_env = prev_env | env
            else:
                new_env = env
        else:
            new_env = env

        try:
            self.yml["services"][container]["environment"] = new_env
        except KeyError as e:
            pass

    def get_envs(self, container: str) -> dict:
        """
        The function `get_envs` retrieves the environment variables from a specified container in a compose
        file.

        :param container: A string representing the name of the container
        :type container: str
        :return: a dictionary containing the environment variables of the specified container.
        """
        try:
            env = self.yml["services"][container]["environment"]
            return env
        except KeyError:
            return None

    def set_labels(self, container: str, labels: dict):
        """
        The function `set_labels` sets the labels for a specified container in a compose file.

        :param container: The `container` parameter is a string that represents the name of a container in a
        YAML file
        :type container: str
        :param labels: The `labels` parameter is a dictionary that contains key-value pairs. Each key
        represents a label name, and the corresponding value represents the label value. These labels can be
        used to provide metadata or configuration information to the container specified by the `container`
        parameter
        :type labels: dict
        """
        try:
            self.yml["services"][container]["labels"] = labels
        except KeyError as e:
            pass

    def get_labels(self, container: str) -> dict:
        """
        The function `get_labels` takes a container name as input and returns the labels associated with
        that container from a compose file.

        :param container: The `container` parameter is a string that represents the name of a container
        :type container: str
        :return: a dictionary of labels.
        """
        try:
            labels = self.yml["services"][container]["labels"]
            return labels
        except KeyError:
            return None

    def set_extrahosts(self, container: str, extrahosts: list):
        """
        The function `set_extrahosts` sets the `extra_hosts` property of a container in a compose file.

        :param container: The container parameter is a string that represents the name of the container
        :type container: str
        :param extrahosts: A list of additional hostnames to be added to the container's /etc/hosts file.
        Each item in the list should be in the format "hostname:IP_address"
        :type extrahosts: list
        """
        try:
            self.yml["services"][container]["extra_hosts"] = extrahosts
        except KeyError as e:
            pass

    def get_extrahosts(self, container: str) -> list:
        """
        The function `get_extrahosts` returns a list of extra hosts for a given container.

        :param container: The `container` parameter is a string that represents the name of a container
        :type container: str
        :return: a list of extra hosts for a given container. If the container is not found or if there are
        no extra hosts defined for the container, an empty list is returned.
        """
        try:
            extra_hosts = self.yml["services"][container]["extra_hosts"]
            return extra_hosts
        except KeyError:
            return None

    def write_to_file(self):
        """
        The function writes the contents of a YAML object to a file.
        """

        # saving the docker compose to the directory
        with open(self.compose_path, "w") as f:
            yaml.add_representer(type(None), represent_none)
            f.write(yaml.dump(self.yml, default_flow_style=False))
