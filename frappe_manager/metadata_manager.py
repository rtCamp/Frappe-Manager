from pathlib import Path
from pydantic import BaseModel, EmailStr
import tomlkit
from frappe_manager.migration_manager.version import Version
from frappe_manager import CLI_FM_CONFIG_PATH
from frappe_manager.utils.helpers import get_current_fm_version


class FMConfigManager(BaseModel):
    root_path: Path
    version: Version
    le_email: EmailStr

    def export_to_toml(self, path: Path = CLI_FM_CONFIG_PATH) -> bool:
        exclude = {'root_path'}

        if self.le_email == 'dummy@fm.fm':
            exclude.add('le_email')

        if self.version < Version('0.13.0'):
            path = CLI_FM_CONFIG_PATH.parent / '.fm.toml'

        # Convert the BenchConfig instance to a dictionary
        fm_config_dict = self.model_dump(exclude=exclude, exclude_none=True)

        # transform structure if needed
        fm_config_dict['version'] = self.version.version

        toml_doc = tomlkit.document()

        for key, value in fm_config_dict.items():
            toml_doc[key] = value

        try:
            with open(path, 'w') as f:
                f.write(tomlkit.dumps(toml_doc))
            return True
        except Exception as e:
            return False

    @classmethod
    def import_from_toml(cls, path: Path = CLI_FM_CONFIG_PATH) -> "FMConfigManager":
        # previously the name of the fm config was .fm.toml now changed to fm_cofig.toml
        input_data = {}

        old_config_path = path.parent / '.fm.toml'

        input_data['version'] = Version('0.8.3')
        input_data['le_email'] = 'dummy@fm.fm'
        input_data['root_path'] = str(path)

        if old_config_path.exists():
            old_data = tomlkit.parse(old_config_path.read_text())
            input_data['version'] = Version(old_data.get('version', '0.8.3'))
        elif path.exists():
            data = tomlkit.parse(path.read_text())
            input_data['version'] = Version(data.get('version', get_current_fm_version()))
            input_data['le_email'] = data.get('le_email', 'dummy@fm.fm')

        fm_config_instance = cls(**input_data)
        return fm_config_instance
