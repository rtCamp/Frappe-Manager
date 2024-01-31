import importlib.resources as pkg_resources

from pathlib import Path
from typing import Optional, Union
from tomlkit import comment, document, dumps, loads, table, toml_document


class TomlManager:
    def __init__(self, config_file: Path, template: toml_document.TOMLDocument = document()):
        self.toml_file = config_file
        self.template = template
        self.toml: Union[Optional[toml_document.TOMLDocument],dict] = None

    def load(self):

        if not self.toml_file.exists():
            self.toml = self.template
            return

        with open(self.toml_file, 'r') as f:
            self.toml = loads(f.read())

    def save(self):
        with open(self.toml_file, 'w') as f:
            f.write(dumps(self.toml))

    def get(self, key):
        return self.toml[key]

    def set(self, key, value):
        self.toml[key] = value

    def get_all(self):
        return self.toml

    def set_all(self, toml):
        self.toml = toml
