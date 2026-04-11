# Google API Development

Google OAuth does not allow redirect URIs that are wildcards like `*.localhost`. If you need to develop an app that uses Google APIs with a local site like `mybench.localhost`, create a symlink so the redirect matches exactly.

Steps:

1. Inside your bench sites directory, create a symlink from `localhost` to your site name:

```bash
cd ~/frappe/sites
ln -sfn mybench.localhost localhost
```

2. In Google Cloud Console, add `http://mybench.localhost/oauth2callback` (or your callback) as an authorized redirect URI.

3. Restart the bench and test the OAuth flow.

!!! note
    This is a local development workaround — use proper domains and HTTPS in production.
