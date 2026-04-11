# Admin Tools

Admin tools are small helper services that make development easier: Mailpit (capture outgoing email), Adminer (browse the database), and small dashboards for Redis.

Enable admin tools for a bench:

```bash
fm update mybench --admin-tools enable
```

Access patterns (replace `mybench` and `service`):

- Mailpit: http://mybench.localhost:8025
- Adminer: http://mybench.localhost:8080
- Redis dashboard: http://mybench.localhost:9000

Disable admin tools:

```bash
fm update mybench --admin-tools disable
```

!!! info
    Admin tools are safe for development. Avoid exposing them on the public internet without proper access control.
