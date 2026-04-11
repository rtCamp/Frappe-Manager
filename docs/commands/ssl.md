fm ssl

Manage TLS certificates using acme.sh and Let’s Encrypt.

Common subcommands:

```bash
# Issue a certificate using HTTP-01 (default)
fm ssl add mybench example.com

# Dry run (test issuance)
fm ssl add mybench example.com --dry-run

# Use Cloudflare DNS-01: first configure credentials
fm ssl dns-config cloudflare --api-token YOUR_TOKEN
fm ssl add mybench example.com --challenge dns01

# List, renew, and remove
fm ssl list mybench
fm ssl renew mybench
fm ssl renew --all
fm ssl remove mybench example.com

# Pass-through to acme.sh
fm ssl acme-sh -- --list
```

!!! note
    Do not use deprecated flags like `--letsencrypt-email` or `--dns-provider`. Use the dns-config subcommand for Cloudflare.
