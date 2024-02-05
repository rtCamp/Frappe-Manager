#!/usr/bin/env python3

import sys
import configparser
from pathlib import Path

print(sys.argv)
conf_file_path= Path(sys.argv[1])
conf_file_absolute_path = conf_file_path.absolute()

if not len(sys.argv) == 2:
    print(f"Generates individual program conf from supervisor.conf.\nUSAGE: {sys.argv[0]} SUPERVISOR_CONF_PATH\n\n SUPERVISOR_CONF_PATH -> Absolute path to supervisor.conf")
    sys.exit(0)

config = configparser.ConfigParser(allow_no_value=True,strict=False,interpolation=None)

superconf = open(conf_file_absolute_path,'r+')

config.read_file(superconf)

print(f"Divided {conf_file_absolute_path} into ")

for section_name in config.sections():
    if not 'group:' in section_name:

        section_config = configparser.ConfigParser(interpolation=None)
        section_config.add_section(section_name)
        for key, value in config.items(section_name):
            if 'frappe-bench-frappe-web' in section_name:
                if key == 'command':
                    value = value.replace("127.0.0.1:80","0.0.0.0:80")
            section_config.set(section_name, key, value)

        if 'worker' in section_name:
            file_name = f"{section_name.replace('program:','')}.workers.fm.supervisor.conf"
        else:
            file_name = f"{section_name.replace('program:','')}.fm.supervisor.conf"

        with open(conf_file_path.parent / file_name, 'w') as section_file:
            section_config.write(section_file)
        print(f"  - {section_name} => {file_name}")
