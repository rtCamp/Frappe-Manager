# fm ssl dns-config cloudflare

Configure Cloudflare credentials for DNS-01 certificate challenges. This is required before running `fm ssl add ... --challenge dns01` with Cloudflare as the DNS provider.

Usage:

```console
$ fm ssl dns-config cloudflare [BENCH] [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `--api-token` | Cloudflare API Token (recommended; scoped permissions) |
| `--api-key` | Cloudflare Global API Key (legacy; full account access) |
| `--email` | Cloudflare account email (required when using `--api-key`) |
| `-n, --name` | Label for this credential set. Omit for the default account |
| `-s, --show` | List the stored credential sets, secrets masked. Writes nothing |
| `-r, --remove` | Delete one stored credential set |

## Credential scope

Two independent choices: `BENCH` picks the **file**, `--name` picks the **table** inside it.

| `BENCH` | `--name` | Written to |
|---|---|---|
| omitted | omitted | `[ssl.dns_providers.cloudflare]` in `~/frappe/fm_config.toml` |
| omitted | given | `[ssl.dns_providers.<name>]` in `~/frappe/fm_config.toml` |
| given | omitted | `[ssl.dns_providers.cloudflare]` in that bench's `bench_config.toml` |
| given | given | `[ssl.dns_providers.<name>]` in that bench's `bench_config.toml` |

The two scopes are structurally identical: same table, same fields, and a bench entry beats the global entry with the same label. There is no separate default table; without `--name` you are writing the label `cloudflare`, which is simply the label every certificate that names none falls back to. fm says which it did: writing that label globally prints `This is the default set: every bench uses it unless a certificate names another`, and any other label prints the `fm ssl add ... --dns-provider <label>` hint that binds a certificate to it.

With `--name` you are storing a **labelled credential set**, which only a certificate that names that label uses:

```bash
fm ssl dns-config cloudflare --name client-zones --api-token OTHER_TOKEN
fm ssl add mybench client.example.com --challenge dns01 --dns-provider client-zones
```

The label identifies the account, not the provider: every set stored through this command drives the Cloudflare API. A label is looked up in the bench table first and the global table second, and a certificate whose label is in neither refuses to issue and to renew. fm never substitutes a different set, because doing so would authenticate against the wrong Cloudflare account and report success. Full resolution order: [DNS providers](../reference/configuration.md#dns-providers).

## Authentication methods

### API Token (recommended)

API Tokens are scoped to specific zones and permissions, making them much safer than the Global API Key.

1. Go to <https://dash.cloudflare.com/profile/api-tokens>
2. Click **Create Token**
3. Use the **Edit zone DNS** template
4. Required permission: Zone → DNS → Edit
5. Restrict to the specific zone(s) you need

```bash
fm ssl dns-config cloudflare --api-token YOUR_API_TOKEN
```

### Global API Key (legacy)

The Global API Key has full account access. Only use it if API Tokens are not available.

```bash
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email you@example.com
```

`--email` is required when using `--api-key`.

## Showing and removing

`--show` lists every `[ssl.dns_providers.<label>]` entry fm can see and writes nothing; secrets appear as `*** (set)`. Each set is printed under its own table header, and the one a certificate without a label will pick, `cloudflare`, is marked `(default)`. With a bench name it prints the bench's sets first and the global ones after, which is how you check what a given certificate is actually going to use; a bench that stores none of its own says so and falls back to the global configuration. `--show --name LABEL` narrows both scopes to that label and reports per scope when it is absent.

`--remove` deletes one set and refuses to guess which:

| Invocation | Effect |
|---|---|
| `--remove --name LABEL` | Deletes that labelled set at the chosen scope and leaves every other label alone. Exits 1 if the label is not stored there, listing the labels that are |
| `--remove`, exactly one set stored at that scope | Removes it |
| `--remove`, several sets stored at that scope | Refuses and lists them, so name the one you meant with `--name` |
| `--remove`, nothing stored at that scope | Warns and changes nothing |

Those refusals exist because deleting the wrong set silently breaks renewal for every certificate that named it.

## Examples

```bash
# Set the global default credentials (API Token)
fm ssl dns-config cloudflare --api-token YOUR_TOKEN

# Set bench-specific default credentials, which override the global ones
fm ssl dns-config cloudflare mybench --api-token DIFFERENT_TOKEN

# Store a second account globally, under a label
fm ssl dns-config cloudflare --name client-zones --api-token OTHER_TOKEN

# Store a label for one bench only
fm ssl dns-config cloudflare mybench --name client-zones --api-token OTHER_TOKEN

# Use legacy Global API Key
fm ssl dns-config cloudflare --api-key YOUR_API_KEY --email you@example.com

# List every global credential set
fm ssl dns-config cloudflare --show

# List a bench's sets, then the global ones
fm ssl dns-config cloudflare mybench --show

# Show one label only
fm ssl dns-config cloudflare --show --name client-zones

# Remove the global default set, naming the label explicitly
fm ssl dns-config cloudflare --remove --name cloudflare

# Remove one global labelled set
fm ssl dns-config cloudflare --remove --name client-zones

# Remove a bench's labelled set
fm ssl dns-config cloudflare mybench --remove --name client-zones
```

After configuring credentials, issue certificates with:

```bash
fm ssl add mybench example.com --challenge dns01

# Or against a labelled account
fm ssl add mybench client.example.com --challenge dns01 --dns-provider client-zones
```

## Related

- [SSL / HTTPS guide](../guides/ssl.md)
- [DNS providers](../reference/configuration.md#dns-providers): the stored file format and resolution order
