# FrappeManager

A docker compose based tool to easily manage frappe based projects.

  - This app is currently in its beta phase.
  - It allows you to create a new site, which will be accessible as a subdomain of your localhost.
  - Each site you create is stored within the 'frappe' directory located in your user's home directory.
  - When a new site is created, the app automatically installs 'frappe' with the latest stable branch, which is currently 'version-14'.

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

  - You have the option to execute fm directly within the shell.
  - To view a list of all available commands, you can utilize `fm --help`.
  - For any specific command's help, simply use `--help` in conjunction with that command.
  - You can access the complete CLI reference [here](./cli_reference.md).

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
