from pathlib import Path
from frappe_manager.toml_manager import TomlManager
from frappe_manager.templates.fm_metadata import metadata
from frappe_manager.migration_manager.version import Version
from tomlkit import comment, document, dumps, loads, table, toml_document
from frappe_manager import CLI_METADATA_PATH

class MetadataManager(TomlManager):
    def __init__(self, metadata_file: Path = CLI_METADATA_PATH, template: toml_document.TOMLDocument = metadata):
        super().__init__(metadata_file, template)
        self.load()

    def get_version(self) -> Version:
        return Version(str(self.get('version')))

    def set_version(self, version: Version):
        self.set('version', str(version))
