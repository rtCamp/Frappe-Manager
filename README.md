# Frappe-Manager

A CLI tool based on Docker Compose to easily manage Frappe based projects. As of now, only suitable for development in local machines running on Mac and Linux based OS.


![Frappe-Manager-Create-Site.svg](https://user-images.githubusercontent.com/28294795/283108791-0237d05a-2562-48be-987b-037a200d71a3.svg)


## Installation
**Requirements:** Python3.11+, Docker, VSCode(optional)

```bash
pip install frappe-manager
```

### Setup Autocompletion    
1. ```bash
   fm --install-completion
   ```
2. Restart shell or terminal


## Usage
### Create a site

```bash
# create example.localhost site with only frappe, version -> version-15
fm create example

# create example.localhost site with only frappe, version -> develop
fm create example --frappe-branch develop

# create example.localhost site with frappe, erpnext and hrms, version -> version-15
fm create example --apps erpnext:version-15 --apps hrms:version-15    
```

Visit CLI [Wiki](https://github.com/rtCamp/Frappe-Manager/wiki) for more examples

## Credits
Dockerfiles: [Frappe Docker](https://github.com/frappe/frappe_docker)
