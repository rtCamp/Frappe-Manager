## `fm ssl`

Ssl commands.

**Usage**:

```console
$ fm ssl [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `renew`: Renew SSL certificates.
* `list`: List SSL certificates.
* `add`: Add SSL certificate for a domain.
* `remove`: Remove SSL certificate for a domain.
* `acme-sh`: Run acme.sh commands directly with FM's environment (advanced users).


### `fm ssl renew`

Renew SSL certificates.

Supports both bench mode (default) and standalone mode for external domains.
Use --dry-run to test with Let's Encrypt staging server.
Use --force to renew certificates regardless of expiry date.

**Usage**:

```console
$ fm ssl renew BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Specific domain to renew. If omitted, renews all certificates for the bench/standalone.

**Options**:

* `--all`: Renew ssl cert for all benches.
* `--standalone`: Renew certificates for external domains
* `--dry-run`: Test renewal using Let's Encrypt staging server without modifying the system.
* `-f, --force`: Force renewal even if certificate is not due for renewal.


**Examples**:

_Renew all certificates for mybench_
```bash
fm ssl renew mybench
```

_Renew certificate for specific domain on mybench_
```bash
fm ssl renew mybench example.com
```

_Renew all certificates for all benches_
```bash
fm ssl renew --all
```

_Test renewal with Let's Encrypt staging (dry-run)_
```bash
fm ssl renew mybench --dry-run
```

_Renew specific external (standalone) domain_
```bash
fm ssl renew --standalone example.com
```

_Renew all external (standalone) domains_
```bash
fm ssl renew --standalone --all
```


### `fm ssl list`

List SSL certificates.

List certificates for a specific bench, external domains, or all certificates.

**Usage**:

```console
$ fm ssl list BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).

**Options**:

* `--standalone`: List certificates for external (non-bench) domains
* `--all`: List all certificates (bench + external)


**Examples**:

_List SSL certificates for mybench_
```bash
fm ssl list mybench
```

_List all external (standalone) certificates_
```bash
fm ssl list --standalone
```

_List all certificates (bench + external)_
```bash
fm ssl list --all
```


### `fm ssl add`

Add SSL certificate for a domain.

Supports both bench mode (default) and standalone mode for external Docker projects.
Standalone mode allows managing SSL for any Docker project using FM's nginx-proxy.

Use --dry-run to test certificate generation with Let's Encrypt staging server
before committing to production. This validates DNS/HTTP configuration without
rate limits or system modifications.

**Usage**:

```console
$ fm ssl add BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Domain name for the certificate

**Options**:

* `-c, --challenge`: Challenge type
* `--cname`: CNAME delegation record for DNS-01 challenge (requires dns01)
* `--dry-run`: Test certificate generation using Let's Encrypt staging server without adding it to the system.
* `--standalone`: Manage SSL for external (non-bench) Docker project. Use with docker network 'fm-global-frontend-network'.
* `--skip-dns-check`: Skip DNS validation before certificate generation (use if DNS will be configured later).
* `--wait-for-dns`: Wait for DNS propagation (polls every 30s for up to 5 minutes).


**Examples**:

_Add SSL certificate for mybench with HTTP-01 challenge_
```bash
fm ssl add mybench example.com --challenge http01
```

_Add SSL certificate with DNS-01 challenge (Cloudflare)_
```bash
fm ssl add mybench example.com --challenge dns01
```

_Add for external Docker project (standalone mode)_
```bash
fm ssl add example.com --standalone
```

_Test with Let's Encrypt staging (dry-run)_
```bash
fm ssl add mybench example.com --dry-run
```

_Add with CNAME delegation for DNS validation_
```bash
fm ssl add mybench example.com --challenge dns01 --cname delegated.example.com
```


### `fm ssl remove`

Remove SSL certificate for a domain.

Supports both bench mode (default) and standalone mode for external domains.

**Usage**:

```console
$ fm ssl remove BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Domain name of the certificate to remove

**Options**:

* `-y, --yes`: Skip confirmation prompt
* `--standalone`: Remove certificate for external (non-bench) domain


**Examples**:

_Remove SSL certificate from mybench_
```bash
fm ssl remove mybench example.com
```

_Remove without confirmation_
```bash
fm ssl remove mybench example.com --yes
```

_Remove external (standalone) certificate_
```bash
fm ssl remove example.com --standalone
```


### `fm ssl acme-sh`

Run acme.sh commands directly with FM's environment (advanced users).

[bold yellow]⚠️  Advanced users only![/bold yellow]
This bypasses FM's certificate management. Use 'fm ssl add/remove/renew' for normal operations.

This command provides direct access to acme.sh for advanced operations like certificate
listing, info checking, manual renewals, revocations, and debugging.

All acme.sh commands run with FM's SSL directory configuration and full access to
certificate storage.

**Usage**:

```console
$ fm ssl acme-sh
```


**Examples**:

_Show acme.sh help and available commands_
```bash
fm ssl acme-sh
```

_List all certificates managed by acme.sh_
```bash
fm ssl acme-sh --list
```

_Show certificate information for a domain_
```bash
fm ssl acme-sh --info -d example.com
```

_Check acme.sh version_
```bash
fm ssl acme-sh --version
```

_Upgrade acme.sh to latest version_
```bash
fm ssl acme-sh --upgrade
```

_Force renew certificate for a domain_
```bash
fm ssl acme-sh --renew -d example.com --force
```


### `fm ssl dns-config`

Dns-Config commands.

**Usage**:

```console
$ fm ssl dns-config [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `cloudflare`: Configure Cloudflare DNS credentials for DNS-01 challenge.


#### `fm ssl dns-config cloudflare`

Configure Cloudflare DNS credentials for DNS-01 challenge.

Credentials can be configured at two levels:
- [bold]Global[/bold]: Used by all benches (omit benchname)
- [bold]Bench-specific[/bold]: Override for a specific bench (provide benchname)

[bold cyan]Authentication Methods:[/bold cyan]

1. [green]API Token[/green] (Recommended):
   - More secure with scoped permissions
   - Create at: https://dash.cloudflare.com/profile/api-tokens
   - Template: "Edit zone DNS"
   - Required permission: Zone > DNS > Edit

2. [yellow]Global API Key[/yellow] (Legacy):
   - Full account access (less secure)
   - Requires --email with your Cloudflare account email
   - Find at: https://dash.cloudflare.com/profile/api-tokens

**Usage**:

```console
$ fm ssl dns-config cloudflare BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench name for bench-specific credentials. Omit for global configuration.

**Options**:

* `--api-token`: Cloudflare API Token (recommended - scoped permissions)
* `--api-key`: Cloudflare Global API Key (legacy - full account access)
* `--email`: Cloudflare account email (required with Global API Key)
* `-s, --show`: Show current Cloudflare DNS credentials
* `-r, --remove`: Remove Cloudflare DNS credentials


**Examples**:

_Configure global Cloudflare credentials using API Token (recommended)_
```bash
fm ssl dns-config cloudflare --api-token YOUR_CLOUDFLARE_API_TOKEN
```

_Configure global Cloudflare credentials using API Key (legacy)_
```bash
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email admin@example.com
```

_Configure bench-specific Cloudflare credentials (overrides global)_
```bash
fm ssl dns-config cloudflare mybench --api-token BENCH_SPECIFIC_TOKEN
```

_Show global Cloudflare DNS credentials configuration_
```bash
fm ssl dns-config cloudflare --show
```

_Show bench-specific Cloudflare DNS credentials_
```bash
fm ssl dns-config cloudflare mybench --show
```

_Remove global Cloudflare DNS credentials_
```bash
fm ssl dns-config cloudflare --remove
```

_Remove bench-specific Cloudflare DNS credentials_
```bash
fm ssl dns-config cloudflare mybench --remove
```

