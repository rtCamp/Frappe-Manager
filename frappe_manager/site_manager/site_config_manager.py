# from pathlib import Path
# from frappe_manager.toml_manager import TomlManager
# from frappe_manager.templates.fm_metadata import metadata
# from frappe_manager.migration_manager.version import Version
# from tomlkit import comment, document, dumps, loads, table, toml_document
# from frappe_manager import CLI_METADATA_PATH

# class SiteConfigManager(TomlManager):
#     def __init__(self, file_path , template: toml_document.TOMLDocument = metadata):
#         super().__init__(metadata_file, template)
#         self.load()

#     def get_le_mail(self) -> Version:
#         return Version(str(self.get('version')))

#     def set_le_mail(self, version: Version):
#         self.set('version', str(version))
