import pkgutil
from pathlib import Path
import yaml
from typing import List
import typer

from fm.site_manager.Richprint import richprint

def represent_none(self, _):
    return self.represent_scalar('tag:yaml.org,2002:null', '')


class SiteCompose:
    def __init__(self,loadfile: Path):
        self.compose_path:Path = loadfile
        self.yml: yaml | None = None
        self.init()

    def init(self):
        # if the load file not found then the site not exits
        if self.exists():
            with open(self.compose_path,'r') as f:
                self.yml = yaml.safe_load(f)
        else:
            template =self.__get_template('docker-compose.tmpl')
            self.yml = yaml.safe_load(template)

    def exists(self):
        return self.compose_path.exists()

    def get_compose_path(self):
        return self.compose_path

    def migrate_compose(self,version):
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
            self.write_to_file()

    def __get_template(self,file_name: str)-> None | str:
        file_name = f"templates/{file_name}"
        try:
            data = pkgutil.get_data(__name__,file_name)
        except:
            richprint.error(f"{file_name} template not found!")
            raise typer.Exit(1)
        yml = data.decode()
        return yml

    def get_services_list(self):
        return list(self.yml['services'].keys())

    def is_services_name_same_as_template(self):
        template = self.__get_template('docker-compose.tmpl')
        template_yml = yaml.safe_load(template)
        template_service_name_list = list(template_yml['services'].keys())
        template_service_name_list.sort()
        current_service_name_list = list(self.yml['services'].keys())
        current_service_name_list.sort()
        return current_service_name_list == template_service_name_list

    def get_version(self):
        try:
            compose_version = self.yml['x-version']
        except KeyError:
            return None
        return compose_version

    def set_version(self, version):
        self.yml['x-version'] = version

    def set_envs(self,container:str,env:dict):
        """Sets env to given container."""
        self.yml['services'][container]['environment'] = env

    def get_envs(self, container:str) -> dict:
        """Gets env from given container."""
        return self.yml['services'][container]['environment']

    def set_labels(self,container:str, labels:dict):
        self.yml['services'][container]['labels'] = labels

    def get_labels(self,container:str) -> dict:
        try:
            labels = self.yml['services'][container]['labels']
        except KeyError:
            return {}
        return labels

    def set_extrahosts(self,container:str,extrahosts:list):
        """Sets extrahosts to contianer."""
        self.yml['services'][container]['extra_hosts'] = extrahosts

    def get_extrahosts(self,container:str) -> list:
        try:
            extra_hosts = self.yml['services'][container]['extra_hosts']
        except KeyError:
            return []
        return extra_hosts

    def write_to_file(self):
        # saving the docker compose to the directory
        with open(self.compose_path,'w') as f:
            yaml.add_representer(type(None), represent_none)
            f.write(yaml.dump(self.yml))
