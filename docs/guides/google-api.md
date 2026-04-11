# Google API Development

Google OAuth requires public HTTPS redirect URIs. A local http://mybench.localhost URL will not work for OAuth redirect URIs. Use ngrok to get a temporary public HTTPS URL for development.

Start an ngrok tunnel for your bench:

```bash
fm ngrok mybench
```

If you have an ngrok auth token and want to save it for future use:

```bash
fm ngrok mybench --auth-token YOUR_TOKEN --save-token
```

Copy the public HTTPS URL that ngrok gives you and add it as an Authorized Redirect URI in the Google Cloud Console.

!!! tip
    ngrok URLs are ephemeral unless you have a paid ngrok account. Update the redirect URIs in Google Cloud each time the URL changes.
