## `fm create`

Create a new bench with apps

**Usage**:

```console
$ fm create BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench name  [required]

**Options**:

* `-a, --apps`: Apps to install. Format: appname:branch or appname (e.g., erpnext:version-15)
* `-e, --environment`: Environment type (dev or prod)
* `--developer-mode`: Enable/disable developer mode
* `--template`: Create as template bench
* `--admin-pass`: Administrator password
* `--alias-domains`: Alias domains (comma-separated). Use 'fm ssl add' for SSL.
* `-t, --github-token`: GitHub token for private repos (or use GITHUB_TOKEN env var)
* `--python`: Python version (e.g., '3.11'). Auto-detected by default.
* `--node`: Node version (e.g., '18', '20'). Auto-detected by default.
* `--restart`: Docker restart policy. Defaults to 'no' (dev) or 'unless-stopped' (prod).
* `--allow-domain-conflicts`: Skip domain uniqueness validation (not recommended). Allows creating benches with duplicate domains.


**Examples**:

_Create bench with frappe only_
```bash
fm create mybench
```

_Create bench with erpnext and hrms_
```bash
fm create mybench --apps erpnext --apps hrms
```

_Create production bench_
```bash
fm create mybench -e prod
```

_Create bench with specific branch_
```bash
fm create mybench --apps erpnext:version-14
```

_Create bench with private app_
```bash
fm create mybench --apps myorg/private-app --github-token ghp_xxx
```

_Create bench with custom Python/Node versions_
```bash
fm create mybench --python 3.11 --node 20
```

_Create bench with alias domains_
```bash
fm create mybench --alias-domains www.example.com,api.example.com
```

