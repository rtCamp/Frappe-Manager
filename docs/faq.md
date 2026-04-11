# FAQ

Q: How do I update FM itself?

A: Run these two commands. The first updates the CLI; the second updates benches and infrastructure.

```bash
fm self update
fm migrate --all-benches
```

Q: How do I reset a bench to a clean state?

A: `fm reset mybench` drops the bench database and reinstalls apps. This is destructive. Backup first.

Q: How do I change the Administrator password?

A: Option A (reinstall):

```bash
fm reset mybench --admin-pass newpass
```

Option B (without reinstall):

```bash
fm shell mybench -c "bench set-admin-password newpass"
```

Q: My site won't load at mybench.localhost — what should I check?

A: Check these items:

- Docker is running: `docker ps`
- The bench is listed: `fm list`
- Ports 80 and 443 are free on the host
- On Windows 10, add `127.0.0.1 mybench.localhost` to your hosts file if needed

Q: How do I install ERPNext?

A: During create:

```bash
fm create mybench --apps erpnext
```

After create (on an existing bench):

```bash
fm shell mybench -c "bench get-app erpnext && bench --site mybench.localhost install-app erpnext"
```

Q: How do I use a private GitHub repo for an app?

A: Provide the repo and token when creating the bench:

```bash
fm create mybench --apps org/private-app:main --github-token YOUR_TOKEN
```

Or set `GITHUB_TOKEN` in your environment before running fm create.

Q: How do I share my bench for testing?

A: Create a temporary public URL with ngrok:

```bash
fm ngrok mybench
```

Q: How do I check installed apps and versions?

A: Run:

```bash
fm info mybench
```

Q: Docker images fail to pull from GHCR. What can I try?

A: Try logging out and logging in again:

```bash
docker logout ghcr.io
docker login ghcr.io
```

Q: How do I run bench commands like migrate or build?

A: Use fm shell to run a single command or open an interactive shell:

```bash
fm shell mybench -c "bench migrate"
fm shell mybench
```

Q: Can I run multiple benches on the same machine?

A: Yes. Each bench is isolated. Create multiple benches and list them with `fm list`.

Q: What happened to pyenv and nvm?

A: In v0.19.0 FM switched to uv for Python and fnm for Node. After upgrading run:

```bash
fm migrate mybench
```

Q: How do I back up my bench?

A: From the Frappe web UI: Setup → Download Backup. From the command line:

```bash
fm shell mybench -c "bench backup --with-files"
```
