FrappeManager is a docker compose based tool to easily manager frappe based projects. This is in beta phase as of now.


# Infomation

-   Only supports python3 and up.
-   This will create a `fm` directory in your user `home` directory.
-   Each site will have it&rsquo;s own directory in `/home/user/fm` directory.
-   This will create site as sub domain of localhost.
-   Docker needs to be installed before using this package.


# Installation

-   Download the latest release either .whl or .tar
-   Install it using pip
    
    ```bash
      pip install fm-0.2.0.tar.gz
      # or
      pip install fm-0.2.0.whl
    ```


# Usage

-   You can directly run fm into the shell.
-   You the `fm --help` to see all the available commands.
-   You can use `--help` in any command to view it&rsquo;s info.


## Creating a site

-   This comand will create a site `example.localhost`.
-   This command will also start the site.
-   By default this will install `frappe`, branch `version-14`.

```bash

# create example.localhost site with only frappe app, version -> version-14
fm create example

# create example.localhost site with only frappe app, version -> develop
fm create example --frappe-branch develop

# create example.localhost site with only frappe and erpnext with branch version-14
fm create example --apps erpnext:version-14

# create example.localhost site with frappe,erpnext and hrms, version -> version-14
fm create example --apps erpnext:version-14 --apps hrms:version-14
```

Remember to view the logs of the site using the below command since frappe installation will take place at the first installation.


# TODO

-   [ ] Beautify the cli.
-   [ ] Exceptions handling with error messages.
-   [ ] Better status messages.
-   [ ] Handle dependencies(mkcert,docker,code).
-   Add vscode devcontainer support.
-   Create cli with basic options.
