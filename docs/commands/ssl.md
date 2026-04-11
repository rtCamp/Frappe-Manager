# fm ssl

Manage TLS certificates for a bench. Subcommands include `renew`, `list`, `add`, and `remove`.

Usage examples:

```bash
# Add or request a certificate
fm ssl add mybench example.com --challenge http01 --letsencrypt-email you@example.com

# Renew
fm ssl renew mybench example.com

# List certificates
fm ssl list mybench

# Remove
fm ssl remove mybench example.com --yes
```

Subcommands and options cover advanced flows like acme.sh integration and Cloudflare DNS API keys.
