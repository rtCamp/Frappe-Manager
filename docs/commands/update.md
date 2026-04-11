# fm update

Update bench configuration or enable features like admin tools, environment type, or upload limits.

Usage:

```console
$ fm update BENCHNAME [OPTIONS]
```

Options (common):

| Flag | Description |
|---|---|
| `--admin-tools` | `enable` or `disable` admin tools |
| `-e, --environment` | `dev` or `prod` |
| `--developer-mode` | Turn developer mode on or off |
| `--mailpit-as-default-mail-server` | Use Mailpit as default mail server |
| `--add-alias` | Add an alias domain |
| `--remove-alias` | Remove an alias domain |
| `--upload-limit` | Set upload limit like `100M` |
| `--python` | Change Python version |
| `--node` | Change Node version |
| `--restart` | Restart after updating |

Example:

```bash
fm update mybench --admin-tools enable --upload-limit 200M
```
