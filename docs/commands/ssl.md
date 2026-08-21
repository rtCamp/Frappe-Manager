## `fm ssl`

Ssl commands.

**Usage**:

```console
$ fm ssl [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `renew`: Renew SSL certificates before they expire.
* `list`: List SSL certificates with their expiry and renewal status.
* `add`: Issue an SSL certificate for a domain and point nginx at it.
* `remove`: Delete an SSL certificate and go back to serving the domain over plain HTTP.
* `acme-sh`: Run the bundled acme.sh directly, against fm's certificate home.


### `fm ssl renew`

Renew SSL certificates before they expire.

Renews one bench by default, or a single domain of it if you name one. --all covers every bench, and --standalone switches to the external Docker project domains.

A certificate that is not yet due is reported and left alone, unless you pass --force.

**Usage**:

```console
$ fm ssl renew BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Domain to renew. Omit to renew every certificate in scope.

**Options**:

* `--all`: Renew every bench, or with --standalone every external domain.
* `--standalone`: Renew an external (non-bench) domain.
* `--dry-run`: Rehearse against Let's Encrypt staging. Nothing on disk changes.
* `-f, --force`: Renew even when the certificate is not near expiry yet.


## Examples

### Renew every certificate on a bench

```bash
fm ssl renew mybench
```

### Renew one domain

```bash
fm ssl renew mybench example.com
```

### Renew every bench

```bash
fm ssl renew --all
```

### Renew an external domain

```bash
fm ssl renew --standalone example.com
```

### Renew one that is not due yet

```bash
fm ssl renew mybench example.com --force
```


### `fm ssl list`

List SSL certificates with their expiry and renewal status.

Lists one bench by default, including its domains that have no certificate yet. --standalone lists external Docker project domains instead, and --all lists both.

**Usage**:

```console
$ fm ssl list BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).

**Options**:

* `--standalone`: List external (non-bench) domains instead of a bench.
* `--all`: List both external domains and every bench.


## Examples

### List a bench's certificates

```bash
fm ssl list mybench
```

### List the external domains

```bash
fm ssl list --standalone
```

### List every certificate fm manages

```bash
fm ssl list --all
```


### `fm ssl add`

Issue an SSL certificate for a domain and point nginx at it.

Bench mode takes a bench name and one of its configured domains (add new ones with fm update --add-alias). --standalone issues for an external Docker project instead.

**Usage**:

```console
$ fm ssl add BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Domain to issue the certificate for.

**Options**:

* `-c, --challenge`: ACME validation method.
* `--cname`: Delegated zone for _acme-challenge. dns01 only.
* `--dry-run`: Rehearse against Let's Encrypt staging. Nothing is kept: no certificate, no nginx change.
* `--standalone`: For an external Docker project on the fm-global-frontend-network network.
* `--dev`: Issue from fm's local CA, so no internet or public DNS is needed. Bench mode only.
* `--skip-dns-check`: Skip the DNS pre-check. Standalone mode only.
* `--wait-for-dns`: Wait up to 5 min for the CNAME. Standalone only.


## Examples

### Issue a certificate for a bench domain

```bash
fm ssl add mybench example.com
```

### Issue one when the domain has no public A record

DNS-01 needs provider credentials, see fm ssl dns-config.

```bash
fm ssl add mybench example.com --challenge dns01
```

### Rehearse against the staging server first

```bash
fm ssl add mybench example.com --dry-run
```

### Issue for an external Docker project

```bash
fm ssl add example.com --standalone
```

### Validate through a delegated zone

acme.sh looks for _acme-challenge.acme.example.net instead of the bench's own zone.

```bash
fm ssl add mybench example.com --challenge dns01 --cname acme.example.net
```


### `fm ssl remove`

Delete an SSL certificate and go back to serving the domain over plain HTTP.

Asks for confirmation unless you pass --yes. --standalone deletes an external Docker project's certificate and nginx config instead of a bench's.

**Usage**:

```console
$ fm ssl remove BENCHNAME DOMAIN [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Name of the bench (omit for standalone mode).
* `DOMAIN`: Domain whose certificate to delete.

**Options**:

* `-y, --yes`: Delete without asking for confirmation.
* `--standalone`: Target an external (non-bench) domain.


## Examples

### Delete a bench certificate

```bash
fm ssl remove mybench example.com
```

### Delete without the confirmation prompt

```bash
fm ssl remove mybench example.com --yes
```

### Delete an external domain's certificate

```bash
fm ssl remove example.com --standalone
```


### `fm ssl acme-sh`

Run the bundled acme.sh directly, against fm's certificate home.

An escape hatch for inspection and recovery. fm does not see what you change this way, so use fm ssl add, renew and remove for normal work.

acme.sh is installed by the first fm ssl add, and this command refuses to run until then.

**Usage**:

```console
$ fm ssl acme-sh
```


## Examples

### Show acme.sh's own help

```bash
fm ssl acme-sh
```

### List the certificates acme.sh holds

```bash
fm ssl acme-sh --list
```

### Inspect one certificate

```bash
fm ssl acme-sh --info -d example.com
```

### Force a renewal acme.sh's own way

Bypasses fm's not-due check and its bookkeeping. fm ssl renew --force is the supported route.

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

* `cloudflare`: Store Cloudflare API credentials for DNS-01 certificate issuance.


#### `fm ssl dns-config cloudflare`

Store Cloudflare API credentials for DNS-01 certificate issuance.

Credentials are global; pass a bench name to override them for that bench alone. An API token needs Zone > DNS > Edit, created at https://dash.cloudflare.com/profile/api-tokens

**Usage**:

```console
$ fm ssl dns-config cloudflare BENCHNAME [OPTIONS]
```

**Arguments**:

* `BENCHNAME`: Bench to configure. Omit for global credentials.

**Options**:

* `--api-token`: Cloudflare API token, scoped to the zones you issue for.
* `--api-key`: Legacy Global API Key, which grants full account access. Requires --email.
* `--email`: Cloudflare account email. Required with --api-key only.
* `-s, --show`: Print the stored credentials, secrets masked. Writes nothing.
* `-r, --remove`: Delete the stored credentials.


## Examples

### Store a global API token

```bash
fm ssl dns-config cloudflare --api-token cf_AbCdEf1234567890
```

### Override the token for one bench

```bash
fm ssl dns-config cloudflare mybench --api-token cf_ZyXwVu0987654321
```

### Use a legacy Global API Key instead

```bash
fm ssl dns-config cloudflare --api-key 1234567890abcdef1234 --email admin@example.com
```

### Show what is stored

With a bench name, prints that bench's entry as well as the global one.

```bash
fm ssl dns-config cloudflare --show
```

## Related

- [SSL / HTTPS guide](../guides/ssl.md)
