import pkgutil
from pathlib import Path
import yaml
from typing import List
import typer

from frappe_manager.site_manager.Richprint import richprint

def represent_none(self, _):
    """
    The function `represent_none` represents the value `None` as a null scalar in YAML format.
    
    :param _: The underscore (_) parameter is a convention in Python to indicate that the parameter is
    not going to be used in the function.
    :return: a representation of `None` as a YAML scalar with the tag `tag:yaml.org,2002:null` and an
    empty string as its value.
    """
    return self.represent_scalar('tag:yaml.org,2002:null', '')


class SiteCompose:
    def __init__(self,loadfile: Path):
        self.compose_path:Path = loadfile
        self.site_name:str = loadfile.parent.name
        self.yml: yaml | None = None
        self.init()

    def init(self):
        """
        The function initializes a YAML object by loading data from a file if it exists, otherwise it
        creates a new YAML object using a template.
        """
        # if the load file not found then the site not exits
        if self.exists():
            with open(self.compose_path,'r') as f:
                self.yml = yaml.safe_load(f)
        else:
            template =self.__get_template('docker-compose.tmpl')
            self.yml = yaml.safe_load(template)

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

    def migrate_compose(self,version) -> bool:
        """
        The `migrate_compose` function migrates a Docker Compose file by updating the version, environment
        variables, and extra hosts.
        
        :param version: current version of the fm.
        """
        if self.exists():
            frappe_envs = self.get_envs('frappe')
            nginx_envs = self.get_envs('nginx')
            extra_hosts = self.get_extrahosts('frappe')

            template =self.__get_template('docker-compose.tmpl')
            self.yml = yaml.safe_load(template)

            self.set_version(version)
            self.set_envs('frappe',frappe_envs)
            self.set_envs('nginx',nginx_envs)
            self.set_extrahosts('frappe',extra_hosts)
            self.set_container_names()
            self.write_to_file()
            return True
        return False

    def __get_template(self,file_name: str)-> None | str:
        """
        The function `__get_template` retrieves a template file and returns its contents as a string, or
        raises an error if the template file is not found.
        
        :param file_name: The `file_name` parameter is a string that represents the name of the template
        file. It is used to construct the file path by appending it to the "templates/" directory
        :type file_name: str
        :return: either None or a string.
        """
        file_name = f"templates/{file_name}"
        try:
            data = pkgutil.get_data(__name__,file_name)
        except:
            richprint.error(f"{file_name} template not found!")
            raise typer.Exit(1)
        yml = data.decode()
        return yml

    def set_container_names(self):
        """
        The function sets the container names for each service in a compose file based on the site name.
        """
        for service in self.yml['services'].keys():
            self.yml['services'][service]['container_name'] = self.site_name.replace('.','') + f'-{service}'

    def get_container_names(self) -> dict:
        """
        The function `get_container_names` returns a dictionary of container names for each service in a
        compose file.
        :return: a dictionary containing the names of the containers specified in the compose file.
        """
        if self.exists():
            container_names:dict = {}
            for service in self.yml['services'].keys():
                container_names[service] = self.yml['services'][service]['container_name']
            return container_names

    def get_services_list(self) -> list:
        """
        Getting for getting all docker compose services name as a list.
        :return: list of docker composer servicers.
        """
        return list(self.yml['services'].keys())

    def is_services_name_same_as_template(self):
        """
        The function checks if the service names in the current YAML file are the same as the service names
        in the template YAML file.
        :return: a boolean value indicating whether the list of service names in the current YAML file is
        the same as the list of service names in the template YAML file.
        """
        template = self.__get_template('docker-compose.tmpl')
        template_yml = yaml.safe_load(template)
        template_service_name_list = list(template_yml['services'].keys())
        template_service_name_list.sort()
        current_service_name_list = list(self.yml['services'].keys())
        current_service_name_list.sort()
        return current_service_name_list == template_service_name_list

    def get_version(self):
        """
        The function `get_version` returns the value of the `x-version` key from composer file, or
        `None` if the key is not present.
        :return: the value of the 'x-version', if not found then
        it returns None.
        """
        try:
            compose_version = self.yml['x-version']
        except KeyError:
            return None
        return compose_version

    def set_version(self, version):
        """
        The function sets the value of the 'x-version' key in a YAML dictionary to the specified version.
        
        :param version: current fm version to set it to "x-version" key in the compose file.
        """
        self.yml['x-version'] = version

    def set_envs(self,container:str,env:dict):
        """
        The function `set_envs` sets environment variables for a given container in a compose file.
        
        :param container: A string representing the name of the container
        :type container: str
        :param env: The `env` parameter is a dictionary that contains environment variables. Each key-value
        pair in the dictionary represents an environment variable, where the key is the variable name and
        the value is the variable value
        :type env: dict
        """
        self.yml['services'][container]['environment'] = env

    def get_envs(self, container:str) -> dict:
        """
        The function `get_envs` retrieves the environment variables from a specified container in a compose
        file.
        
        :param container: A string representing the name of the container
        :type container: str
        :return: a dictionary containing the environment variables of the specified container.
        """
        return self.yml['services'][container]['environment']

    def set_labels(self,container:str, labels:dict):
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
        self.yml['services'][container]['labels'] = labels

    def get_labels(self,container:str) -> dict:
        """
        The function `get_labels` takes a container name as input and returns the labels associated with
        that container from a compose file.
        
        :param container: The `container` parameter is a string that represents the name of a container
        :type container: str
        :return: a dictionary of labels.
        """
        try:
            labels = self.yml['services'][container]['labels']
        except KeyError:
            return {}
        return labels

    def set_extrahosts(self,container:str,extrahosts:list):
        """
        The function `set_extrahosts` sets the `extra_hosts` property of a container in a compose file.
        
        :param container: The container parameter is a string that represents the name of the container
        :type container: str
        :param extrahosts: A list of additional hostnames to be added to the container's /etc/hosts file.
        Each item in the list should be in the format "hostname:IP_address"
        :type extrahosts: list
        """
        self.yml['services'][container]['extra_hosts'] = extrahosts

    def get_extrahosts(self,container:str) -> list:
        """
        The function `get_extrahosts` returns a list of extra hosts for a given container.
        
        :param container: The `container` parameter is a string that represents the name of a container
        :type container: str
        :return: a list of extra hosts for a given container. If the container is not found or if there are
        no extra hosts defined for the container, an empty list is returned.
        """
        try:
            extra_hosts = self.yml['services'][container]['extra_hosts']
        except KeyError:
            return []
        return extra_hosts

    def write_to_file(self):
        """
        The function writes the contents of a YAML object to a file.
        """
        # saving the docker compose to the directory
        with open(self.compose_path,'w') as f:
            yaml.add_representer(type(None), represent_none)
            f.write(yaml.dump(self.yml))
