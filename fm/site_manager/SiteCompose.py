from pathlib import Path
from yaml import safe_load

class SiteCompose:
    def __init__(self,loadfile: Path):
        self.compose_path:Path = loadfile
        self.init()

    def init(self):
        # see if template path exits
        # if the load file not found then the site not exits
        # load the template file
        with open(self.file_path,'r')
        self.yaml = safe_load()

    def exists(self):
        self.file_path.exists()
