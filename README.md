FrappeManager is a docker compose based tool to easily manager frappe based projects. This is in beta phase as of now.


# Infomation

-   This will create a `frappe` directory in your user `home` directory.
-   Each site will have it&rsquo;s own directory in `/home/user/frappe` directory.
-   This will create site as sub domain of localhost.
-   Whenever new site is created, `frappe` is installed by default with latest stable branch(now i.e `version-14`).


## Dependencies

-   `python3+`
-   `docker`
-   `vscode`


# Installation

-   Download the latest release either .whl or .tar
-   Install it using pip
    
    ```bash
      pip install fm-0.4.0.tar.gz
      # or
      pip install fm-0.4.0-py3-none-any.whl
    ```


# Usage

-   You can directly run fm into the shell.
-   You the `fm --help` to see all the available commands.
-   You can use `--help` in any command to view it&rsquo;s help.


## Creating a site

-   This comand will create a site `example.localhost`.
-   This command will also start the site.
-   By default this will install `frappe`, branch `version-14`.

```bash

# create example.localhost site with only frappe, version -> version-14
fm create example

# create example.localhost site with only frappe, version -> develop
fm create example --frappe-branch develop

# create example.localhost site with only frappe and erpnext with branch version-14
fm create example --apps erpnext:version-14

# create example.localhost site with frappe, erpnext and hrms, version -> version-14
fm create example --apps erpnext:version-14 --apps hrms:version-14

# create example.localhost site with frappe, erpnext and hrms, version -> version-15-beta
fm create example --frappe-branch version-15-beta --apps erpnext:version-15-beta --apps hrms:version-15-beta
```


# TODO

-   [ ] Beautify the cli.
-   [ ] Exceptions handling with error messages.
-   [ ] Better status messages.
-   [ ] Handle dependencies(mkcert,docker,code).
-   [ ] Add Https support.
-   Add vscode devcontainer support.
-   Create cli with basic options.
