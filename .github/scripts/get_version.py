import os
import re

env_file = os.getenv("GITHUB_ENV")

with open("frappe_manager/__about__.py") as f:
    content = f.read()

match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
if not match:
    raise RuntimeError("Could not find __version__ in frappe_manager/__about__.py")

version = match.group(1)

with open(env_file, "a") as myfile:
    myfile.write("PYAPP_PROJECT_VERSION=" + version + "\n")
    path = os.path.abspath(f"./dist/frappe_manager-{version}.tar.gz")
    myfile.write(f"PYAPP_PROJECT_PATH={path}\n")
