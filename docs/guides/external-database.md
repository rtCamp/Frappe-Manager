# External Database

Use an external database when you need a managed database for production or you already have a MariaDB server.

Requirements:

- A MariaDB user that can connect from the Frappe Manager host (often use `%` as host) — this lets the bench connect remotely.
- Database naming and user privileges follow Frappe's rules: create a database and grant a user appropriate rights.

Example SQL (adjust names and password):

```sql
CREATE DATABASE mybench_db;
CREATE USER 'mybench_user'@'%' IDENTIFIED BY 'securepassword';
GRANT ALL PRIVILEGES ON mybench_db.* TO 'mybench_user'@'%';
FLUSH PRIVILEGES;
```

Configure `site_config.json` for the site to use the external DB:

```json
{
  "db_host": "db.example.com",
  "db_port": 3306,
  "db_name": "mybench_db",
  "db_password": "securepassword",
  "db_type": "mariadb"
}
```

To migrate a site from internal to external:

1. Backup the site: `fm shell mybench -- bench backup`
2. Stop the bench: `fm stop mybench`
3. Update the bench config to point to the external DB or edit `site_config.json`.
4. Start the bench and restore the backup into the external DB.

!!! warning
    Always test restores on a non-production bench first. Mistakes can overwrite data.
