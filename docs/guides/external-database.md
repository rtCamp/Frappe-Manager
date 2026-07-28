# External Database

Use an external database when you need a managed database for production or you already have a MariaDB server.

Requirements:

- A MariaDB user that can connect from the Frappe Manager host (often use `%` as host) - this lets the bench connect remotely.
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

1. Backup the site: `fm shell mybench -c "bench --site mybench backup"`
2. Stop the bench: `fm stop mybench`
3. Edit the site's `site_config.json` (workspace/frappe-bench/sites/<site>/site_config.json) with the external DB settings.
4. Start the bench again so the new config is picked up:

```bash
fm start mybench
```

5. Restore your backup into the external DB as needed.

!!! warning
    Always test restores on a non-production bench first. Mistakes can overwrite data.
