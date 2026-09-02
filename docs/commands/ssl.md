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

Renews every certificate of one bench by default, or a single one when the address names a domain. 'all' covers every bench, and --standalone switches to the external Docker project domains.

A certificate that is not yet due is reported and left alone, unless you pass --force.

**Usage**:

```console
$ fm ssl renew BENCH(/DOMAIN)|all [OPTIONS]
```

**Arguments**:

* `BENCH(/DOMAIN)|all`: Bench, BENCH/DOMAIN for one hostname, 'BENCH/all' for every domain of that bench, or 'all' for every bench. A bare domain is for --standalone.

**Options**:

* `--standalone`: Renew an external (non-bench) domain.
* `--dry-run`: Rehearse against Let's Encrypt staging.
* `--force`: Renew even when the certificate is not due.


## Examples

### Renew every certificate on a bench

```bash
fm ssl renew mybench
```

### Renew one domain

```bash
fm ssl renew mybench/example.com
```

### Renew every certificate on one bench, named explicitly

```bash
fm ssl renew mybench/all
```

### Renew every bench

'all' goes where a bench name goes. One bench failing is reported and the rest still renew.

```bash
fm ssl renew all
```

### Renew an external domain

An external domain belongs to no bench, so it is named bare rather than as an address.

```bash
fm ssl renew example.com --standalone
```

### Renew one that is not due yet

```bash
fm ssl renew mybench/example.com --force
```


### `fm ssl list`

List SSL certificates with their expiry and renewal status.

Lists one bench by default, including its domains that have no certificate yet. 'all' lists every bench and the external domains together, and --standalone lists only the external Docker project domains.

The DNS Provider column names the \[ssl.dns_providers] credential set each DNS-01 certificate authenticates with, "default" for the unlabelled account, and "(missing)" when the label or the default account is not stored at either scope.

**Usage**:

```console
$ fm ssl list BENCH|all [OPTIONS]
```

**Arguments**:

* `BENCH|all`: Bench, or 'all' for every bench and the external domains together. Naming a single domain is refused: this reports every certificate the bench holds.

**Options**:

* `--standalone`: List external (non-bench) domains instead of a bench.


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

Every bench and the external domains together. A bench fm cannot read is reported in place, not fatal.

```bash
fm ssl list all
```


### `fm ssl add`

Issue an SSL certificate for a domain and point nginx at it.

Bench mode takes a bench name and one of its configured domains (add new ones with fm update --add-alias). Naming just the bench offers its domains to pick from. --standalone issues for an external Docker project instead.

**Usage**:

```console
$ fm ssl add BENCH(/DOMAIN) [OPTIONS]
```

**Arguments**:

* `BENCH(/DOMAIN)`: Bench, or BENCH/DOMAIN to act on one hostname it serves. 'BENCH/all' means every domain of that bench; a bare domain is for --standalone.

**Options**:

* `-c, --challenge`: ACME validation method.
* `--cname`: Delegated zone for _acme-challenge. dns01 only.
* `--dns-provider`: Label of the \[ssl.dns_providers] credential set that authenticates this domain, from fm ssl dns-config cloudflare --name. Omit for the default account. dns01 only.
* `--dry-run`: Rehearse against Let's Encrypt staging. Nothing is kept: no certificate, no nginx change.
* `--standalone`: For an external Docker project on the fm-global-frontend-network network.
* `--dev`: Issue from fm's local CA, so no internet or public DNS is needed. Bench mode only.
* `--skip-dns-check`: Skip the DNS pre-check. Standalone mode only.
* `--wait-for-dns`: Wait up to 5 min for the CNAME. Standalone only.


## Examples

### Issue a certificate for a bench domain

```bash
fm ssl add mybench/example.com
```

### Issue one when the domain has no public A record

DNS-01 needs provider credentials, see fm ssl dns-config.

```bash
fm ssl add mybench/example.com --challenge dns01
```

### Rehearse against the staging server first

```bash
fm ssl add mybench/example.com --dry-run
```

### Issue for every domain the bench serves

One certificate per hostname, each site's own name and its aliases. Bare 'all' is refused here: issuing across every bench at once can cross Let's Encrypt's rate limit.

```bash
fm ssl add mybench/all
```

### Issue for an external Docker project

```bash
fm ssl add example.com --standalone
```

### Validate through a delegated zone

acme.sh looks for _acme-challenge.acme.example.net instead of the bench's own zone.

```bash
fm ssl add mybench/example.com --challenge dns01 --cname acme.example.net
```

### Authenticate DNS-01 against a second Cloudflare account

acct-b is a label stored by fm ssl dns-config cloudflare --name acct-b, at either global or bench scope.

```bash
fm ssl add mybench/example.com --challenge dns01 --dns-provider acct-b
```


### `fm ssl remove`

Delete an SSL certificate and go back to serving the domain over plain HTTP.

Naming just the bench offers the domains it serves to pick from. Asks for confirmation unless you pass --yes. --standalone deletes an external Docker project's certificate and nginx config instead of a bench's.

**Usage**:

```console
$ fm ssl remove BENCH(/DOMAIN) [OPTIONS]
```

**Arguments**:

* `BENCH(/DOMAIN)`: Bench, or BENCH/DOMAIN to act on one hostname it serves. 'BENCH/all' means every domain of that bench; a bare domain is for --standalone.

**Options**:

* `-y, --yes`: Delete without asking for confirmation.
* `--standalone`: Target an external (non-bench) domain.


## Examples

### Delete a bench certificate

```bash
fm ssl remove mybench/example.com
```

### Delete without the confirmation prompt

```bash
fm ssl remove mybench/example.com --yes
```

### Delete every certificate the bench holds

Back to plain HTTP on every domain of that bench. Bare 'all' is refused here.

```bash
fm ssl remove mybench/all
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

A --name stores the credentials as a labelled set, so one host or bench can hold several Cloudflare accounts (or several least-privilege tokens) at once. A certificate picks one with fm ssl add --dns-provider LABEL; certificates that name no label keep using the unlabelled default.

**Usage**:

```console
$ fm ssl dns-config cloudflare BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to configure. Omit for global credentials.

**Options**:

* `--api-token`: Cloudflare API token, scoped to the zones you issue for.
* `--api-key`: Legacy Global API Key, which grants full account access. Requires --email.
* `--email`: Cloudflare account email. Required with --api-key only.
* `-n, --name`: Label for this credential set, e.g. an account name. Omit for the default account.
* `-s, --show`: Print the stored credentials, secrets masked. Writes nothing.
* `-r, --remove`: Delete the stored credentials.


## Examples

### Store a global API token

```bash
fm ssl dns-config cloudflare --api-token cf_AbCdEf1234567890
```

### Store a second account under a label

Labelled sets go to [ssl.dns_providers.acct-b]; bind one with fm ssl add BENCH DOMAIN --challenge dns01 --dns-provider acct-b.

```bash
fm ssl dns-config cloudflare --api-token cf_ZyXwVu0987654321 --name acct-b
```

### Override the token for one bench

```bash
fm ssl dns-config cloudflare mybench --api-token cf_ZyXwVu0987654321
```

### Store a labelled set for one bench only

```bash
fm ssl dns-config cloudflare mybench --api-token cf_QqRrSs1122334455 --name acct-b
```

### Use a legacy Global API Key instead

```bash
fm ssl dns-config cloudflare --api-key 1234567890abcdef1234 --email admin@example.com
```

### Show what is stored

Lists every labelled set at both scopes, secrets masked. With a bench name, prints that bench's sets as well as the global ones, and --name narrows to one label.

```bash
fm ssl dns-config cloudflare --show
```

### Drop one labelled set

Without --name, a scope holding more than one set is refused rather than guessed at.

```bash
fm ssl dns-config cloudflare --remove --name acct-b
```

## Related

- [SSL / HTTPS guide](../guides/ssl.md)
