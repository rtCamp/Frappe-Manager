# Frappe Local Setup

-   This requires **docker** to be installed in your system.
-   By default this will handle any site created as subdomain to localhost in frappe-bench.


## Links

| Service        | Url                               |
|-------------- |--------------------------------- |
| Frappe web app | <http://mysite.localhost>         |
| Mailhog        | <http://mysite.localhost/mailhog> |
| adminer        | <http://mysite.localhost/adminer> |
| rq-dashboard   | <http://mysite.localhost/rq-dash> |


# Customization availabe for frappe container using envrionmental Variable.

You can use this envrionmental varaible to change frappe container default configuration.

| Variable                | Default    | Accepted Value         | Purpose                                                                                                                                |
|----------------------- |---------- |---------------------- |-------------------------------------------------------------------------------------------------------------------------------------- |
| FRAPPE\_BRANCH          | version-14 | `string`               | Must be a valid frappe branch. Used to change the default branch at the time of bench setup.                                           |
| FRAPPE\_ADMIN\_PASS     | admin      | `string`               | Used to set the frappe web app administrator user password.                                                                            |
| MARIADB\_ROOT\_PASS     | root       | `string`               | User to set the password of the mariadb that will be used frappe.                                                                      |
| FRAPPE\_DEVELOPER\_MODE | 1          | `0 or 1` or  `boolean` | Used to tell frappe to enable developer\_mode(when you create a new doctype, it will be created on the file system).                   |
| BENCH\_START\_OFF       | null       | any `string`           | This will not run bench start at the start of the container instead this will use sleep command to make the container always avaialbe. |


## Usage


### Initial Setup

1.  Using docker compose

    -   Clone the repo.
    -   Change directory into repo.
    -   Build the containers. Building images takes time!!!.
        
        ```bash
          ./build.sh
        ```
    
    -   Change docker-compose.yml frappe contianer `USERID` and `USERGROUP` envrionmental variable to your current user `id` and `group` respectively.

        ```bash
          # your current user id and group can be found using this command.
          id
        ```
    -   Now run the docker compose command.
        
        ```bash
          docker compose up -d
        ```
    
    1.  Access frappe container shell
    
        ```bash
        # access frappe container shell
        docker compose exec --user frappe frappe bash
        ```

2.  Using vscode devcontainers

    -   Clone the repo.
    -   Build the containers.
        
        ```bash
          ./build.sh
        ```

    -   Change docker-compose.yml frappe contianer `USERID` and `USERGROUP` envrionmental variable to your current user `id` and `group` respectively.

        ```bash
          # your current user id and group can be found using this command.
          id
        ```
    -   Now open the repo in vscode.
    -   Now vscode will display a pop up saying reopen in contaienr, Click it.


### Destroy Container

```bash

# stop and remove containers
docker compose down
# stop and remove containers,volumes
docker compose down -v
# stop and remove containers,volumes, images
docker compose down -v --rmi
```


## Bonus


### Create one more site.

-   Run this commands in frappe container. Suppose you want to create `test.localhost`.
-   Change admin password as you want.
    
    ```bash
      bench new-site test.localhost --db-root-password root --admin-password testadmin
    ```


### Adding https support

1.  Install [mkcert](https://github.com/FiloSottile/mkcert) tool.
2.  Setup mkcert ca.
    
    ```bash
      # install ca-cert
      mkcert -install
      # create cert for one site
    ```

3.  Create certs for sites. Remember to keep the key and cert file name same as in the example.
    
    ```bash
      # syntax
      # mkcert -key-file key.pem -cert-file cert.pem <site-name1> <site-name2> <site-name3> ....
    
      cd certs
    
      # creating cert for mysite.localhost
      mkcert -key-file key.pem -cert-file cert.pem mysite.localhost
    
      # creating cert for mysite.localhost and test.localhost
      mkcert -key-file key.pem -cert-file cert.pem mysite.localhost test.localhost
    ```

4.  Now Change `ENABLE_SSL` environament variable to `true`.. By default this is `false` in the `docker-compose.yml`. 
5.  Now rebuild the nginx container. In the repo directory run this commands.
    
    ```bash
      docker compose up nginx -d
    ```
6.  You will need to repeat the steps step 3 and step 5 for new sites.


## Troubleshooting


### Error: `pymysql.err.OperationalError: (1045, "Access denied for user '_0d597d61fe3828a2'@'172.22.0.6' (using password: YES)") ?`

Since database is very crucial, for the below steps please use adminer and identify the main issue then take any actions. Use `Adminer` to troubeshoot this type of issues.

In this frappe local setup this error indicates that

1.  the user doesn&rsquo;t exist.

    -   Frappe creates databases based on site and the creds are stored in site\_config.json.
    -   The user of the datbase is same as the name of the database.
    -   Create the user and configure the password, if the password that you used is different then the site\_config.json password then update the site\_config.json file.

2.  the user password is wrong.

    -   Update the password for your user in the user table, if the password that you used is different then the site\_config.json password then update the site\_config.json file.

3.  the database doesn&rsquo;t exist.

    Reinstall the site using the below command so that the database can be created.
    
    ```bash
      bench --site mysite.localhost reinstall --admin-password admin --db-root-password root
    ```


### ERROR: `failed to solve: frappe-local-frappe: pull access denied, repository does not exist or may require authorization: server message: insufficient_scope: authorization failed`

-   This type of problem arises when you using different builder then the default one. Mostly happens when you have configured docker buildx.
-   Change builder to default.
    
    ```bash
      docker buildx use default
    ```


### Site not available or not accessible using browser.

-   This usually happes when container has stopped/existed.
-   Check if the continaer is running.
-   To start the container use this commands.
    
    ```bash
      docker compose up frappe -d
    ```
