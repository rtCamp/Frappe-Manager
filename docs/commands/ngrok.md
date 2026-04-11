# fm ngrok

Create an ngrok tunnel so you can share your local bench with an external URL.

Usage:

```console
$ fm ngrok BENCHNAME [OPTIONS]
```

Options:

| Flag | Description |
|---|---|
| `-t, --auth-token` | ngrok authentication token |
| `--save-token` / `--no-save-token` | Persist the token in config |

Example:

```bash
fm ngrok mybench -t YOUR_NGROK_TOKEN --save-token
```
