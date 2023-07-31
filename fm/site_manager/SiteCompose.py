import pkgutil
from pathlib import Path
import yaml
from typing import List

def represent_none(self, _):
    return self.represent_scalar('tag:yaml.org,2002:null', '')


class SiteCompose:
    def __init__(self,loadfile: Path):
        self.compose_path:Path = loadfile
        self.exists = loadfile.exists()
        self.yml: yaml | None = None
        self.init()

    def init(self):
        # if the load file not found then the site not exits
        if self.exists:
            with open(self.compose_path,'r') as f:
                self.yml = yaml.safe_load(f)
        else:
            # see if template exits
            template =self.__get_template('docker-compose.tmpl')
            if template == None:
                print("Template not found!")
                exit()
            # load the template file
            self.yml = yaml.safe_load(template)

    def get_compose_path(self):
        return self.compose_path

    def __get_template(self,file_name: str)-> None | str:
        file_name = f"templates/{file_name}"
        try:
            data = pkgutil.get_data(__name__,file_name)
        except:
            return None
        yml = data.decode()
        return yml

    def set_envs(self,container:str,env:dict):
        """Sets env to given container."""
        self.yml['services'][container]['environment'] = env

    def get_envs(self, container:str) -> dict:
        """Gets env from given container."""
        return self.yml['services'][container]['environment']

    def set_labels(self,container:str, labels:dict):
        self.yml['services'][container]['labels'] = labels

    def get_labels(self,container:str,labels:dict):
        return self.yml['services'][container]['labels']

    def set_extrahosts(self,container:str,extrahosts:list):
        """Sets extrahosts to contianer."""
        self.yml['services'][container]['extra_hosts'] = extrahosts

    def write_to_file(self):
        # saving the docker compose to the directory
        with open(self.compose_path,'w') as f:
            yaml.add_representer(type(None), represent_none)
            f.write(yaml.dump(self.yml))
