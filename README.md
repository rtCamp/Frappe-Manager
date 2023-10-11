# FrappeManager

A docker compose based tool to easily manage frappe based projects.

## Infomation

-   This will create site as sub domain of localhost.
-   Each site is created in `frappe` directory in your user `home` directory.
-   Whenever new site is created, `frappe` is installed by default with latest stable branch(now i.e `version-14`).


### Dependencies

-   `python3.11+`
-   `docker`
-   `vscode`


## Installation

-   Download the latest release `.tar` file.
-   Install it using pip
    
    ```bash
      pip install fm-0.8.0.tar.gz
    ```


## Usage

-   You can directly run fm into the shell.
-   You can use `fm --help` to see all the available commands.
-   You can use `--help` in any command to view it&rsquo;s help.


### Example

1.  Creating a site

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

2.  Deleting a site

    ```bash
    # delete site example.localhost
    fm delete example
    ```


### Setup Autocompletion

-   Install completion using the below command.
    
    ```bash
    fm --install-completion
    ```
-   Restart Shell
