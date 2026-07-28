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
* `add`: Add an SSL certificate for a domain.
* `remove`: Remove an SSL certificate for a domain.
* `acme-sh`: Run acme.sh commands directly with FM's environment (advanced users).


### `fm ssl renew`

Renew SSL certificates.

Supports bench and standalone modes. Use --dry-run to validate against Let's Encrypt staging; --force forces renewal.

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


## Examples

### Renew all certificates for a bench

Renews all TLS certificates associated with the specified bench.

```bash
fm ssl renew mybench
```

### Renew certificate for specific domain

Renews a single domain certificate for the given bench.

```bash
fm ssl renew mybench example.com
```

### Renew all certificates for all benches

Renews certificates across all benches managed by FM when run by an administrator.

```bash
fm ssl renew --all
```

### Test renewal with Let's Encrypt staging (dry-run)

Simulates the renewal using Let's Encrypt staging environment to validate configuration.

```bash
fm ssl renew mybench --dry-run
```

### Renew specific external (standalone) domain

Renews a standalone external domain certificate managed outside benches.

```bash
fm ssl renew --standalone example.com
```

### Renew all external (standalone) domains

Renews all external standalone certificates managed by FM.

```bash
fm ssl renew --standalone --all
```


### `fm ssl list`

List SSL certificates.

Use without flags to list certificates for a bench, or pass --standalone or --all to change scope.

**Usage**:

```console
$ fm ssl list BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).

**Options**:

* `--standalone`: List certificates for external (non-bench) domains
* `--all`: List all certificates (bench + external)


## Examples

### List SSL certificates for a bench

Shows certificates installed for a specific bench.

```bash
fm ssl list mybench
```

### List all external (standalone) certificates

Lists certificates managed in standalone (external project) mode.

```bash
fm ssl list --standalone
```

### List all certificates (bench + external)

Lists both bench-installed and external certificates together.

```bash
fm ssl list --all
```


### `fm ssl add`

Add an SSL certificate for a domain.

Supports bench mode (adds certificate to a bench) and standalone mode for external Docker projects using FM nginx-proxy. Use --dry-run to validate issuance against Let's Encrypt staging first.

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
* `--dev`: Issue a locally-trusted dev certificate using a local CA (no internet required).
* `--skip-dns-check`: Skip DNS validation before certificate generation (use if DNS will be configured later).
* `--wait-for-dns`: Wait for DNS propagation (polls every 30s for up to 5 minutes).


## Examples

### Add SSL certificate with HTTP-01 challenge

Requests a certificate using an HTTP-01 challenge and installs it into the bench's nginx configuration.

```bash
fm ssl add mybench example.com --challenge http01
```

### Add SSL certificate with DNS-01 challenge (Cloudflare)

Requests a certificate using DNS-01 validation; configure DNS provider credentials first when required.

```bash
fm ssl add mybench example.com --challenge dns01
```

### Add for external Docker project (standalone mode)

Manage SSL for an external Docker project by using standalone mode and FM's nginx-proxy.

```bash
fm ssl add example.com --standalone
```

### Test with Let's Encrypt staging (dry-run)

Performs a dry-run against Let's Encrypt staging environment to validate configuration without issuing production certs.

```bash
fm ssl add mybench example.com --dry-run
```

### Add with CNAME delegation for DNS validation

Uses a CNAME delegation target for DNS-01 validation when the DNS zone is delegated to another provider.

```bash
fm ssl add mybench example.com --challenge dns01 --cname delegated.example.com
```


### `fm ssl remove`

Remove an SSL certificate for a domain.

Works in bench mode (removes from a bench) or standalone mode for external Docker projects.

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


## Examples

### Remove SSL certificate from a bench

Removes the certificate from the bench and its nginx configuration. Use --yes to skip confirmation.

```bash
fm ssl remove mybench example.com
```

### Remove without confirmation

Removes the certificate immediately without prompting for confirmation.

```bash
fm ssl remove mybench example.com --yes
```

### Remove external (standalone) certificate

Removes a certificate managed in standalone mode for external Docker projects.

```bash
fm ssl remove example.com --standalone
```


### `fm ssl acme-sh`

Run acme.sh commands directly with FM's environment (advanced users).

Advanced users only: this bypasses FM's certificate management. Prefer 'fm ssl add/remove/renew' for normal workflows.

**Usage**:

```console
$ fm ssl acme-sh
```


## Examples

### Show acme.sh help and available commands

Displays acme.sh help. Use for learning available subcommands and flags.

```bash
fm ssl acme-sh
```

### List all certificates managed by acme.sh

Lists certificates that acme.sh currently manages in its home directory.

```bash
fm ssl acme-sh --list
```

### Show certificate information for a domain

Shows detailed information for a managed certificate for the domain.

```bash
fm ssl acme-sh --info -d example.com
```

### Check acme.sh version

Prints the installed acme.sh version used by FM.

```bash
fm ssl acme-sh --version
```

### Upgrade acme.sh to latest version

Upgrades the bundled acme.sh installation to the latest release.

```bash
fm ssl acme-sh --upgrade
```

### Force renew certificate for a domain

Forces a renewal for a certificate using acme.sh; advanced option for recovery and testing.

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


## Examples

### Configure global Cloudflare credentials using API Token (recommended)

Stores a global Cloudflare API token for DNS-01 challenges. Recommended for scoped permissions.

```bash
fm ssl dns-config cloudflare --api-token YOUR_CLOUDFLARE_API_TOKEN
```

### Configure global Cloudflare credentials using API Key (legacy)

Stores legacy Global API Key credentials; less secure and requires account email.

```bash
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email admin@example.com
```

### Configure bench-specific Cloudflare credentials (overrides global)

Sets Cloudflare credentials for a specific bench, overriding global configuration.

```bash
fm ssl dns-config cloudflare --api-token BENCH_SPECIFIC_TOKEN
```

### Show global Cloudflare DNS credentials configuration

Displays stored global Cloudflare credentials (if any).

```bash
fm ssl dns-config cloudflare --show
```

### Show bench-specific Cloudflare DNS credentials

Displays stored Cloudflare credentials for the specified bench.

```bash
fm ssl dns-config cloudflare --show
```

### Remove global Cloudflare DNS credentials

Removes global Cloudflare credential configuration.

```bash
fm ssl dns-config cloudflare --remove
```

### Remove bench-specific Cloudflare DNS credentials

Removes Cloudflare credential configuration for the specified bench.

```bash
fm ssl dns-config cloudflare --remove
```

## Related

- [SSL / HTTPS guide](../guides/ssl.md)
