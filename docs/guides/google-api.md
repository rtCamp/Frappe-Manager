# Google API Development

Google OAuth requires public HTTPS redirect URIs. A local http://mybench.localhost URL will not work for OAuth redirect URIs. Use ngrok to get a temporary public HTTPS URL for development.

!!! warning "Auth token required"
    `fm ngrok` requires an ngrok auth token. If you don't have one yet, sign up for a free account at [ngrok.com](https://ngrok.com) and copy your token from the dashboard.

Start an ngrok tunnel for your bench:

```bash
fm ngrok mybench --auth-token YOUR_TOKEN
```

Save the token so you don't have to pass it every time:

```bash
fm ngrok mybench --auth-token YOUR_TOKEN --save-token
```

On subsequent runs the saved token is used automatically:

```bash
fm ngrok mybench
```

Copy the public HTTPS URL that ngrok gives you and add it as an Authorized Redirect URI in the Google Cloud Console.

!!! tip
    ngrok URLs are ephemeral unless you have a paid ngrok account. Update the redirect URIs in Google Cloud each time the URL changes.
