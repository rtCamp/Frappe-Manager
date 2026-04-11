# fm create

Create a new bench. This command sets up a workspace, downloads requested apps, and starts the services for a fresh Frappe site.

Usage:

```console
$ fm create BENCHNAME [OPTIONS]
```

Options:

| Flag | Description | Default |
|---|---|---|
| `-a, --apps` | Install apps, format `appname:branch` or `appname` | — |
| `-e, --environment` | `dev` or `prod` | `dev` |
| `--developer-mode` | Enable developer mode | off |
| `--template` | Use a custom bench template | — |
| `--admin-pass` | Set initial Administrator password | `admin` |
| `--alias-domains` | Comma separated alias domains | — |
| `-t, --github-token` | Token for private GitHub apps | — |
| `--python` | Python version to use | system default |
| `--node` | Node version to use | system default |
| `--restart` | Restart after create | true |
| `--allow-domain-conflicts` | Allow overlapping domains | false |

Common examples:

_Create a simple development bench named `mybench`_
```bash
fm create mybench
```

_Create and install ERPNext and HRMS_
```bash
fm create mybench --apps erpnext --apps hrms
```

_Create a production bench_
```bash
fm create mybench --environment prod --restart
```

_Create with specific app branches_
```bash
fm create mybench --apps frappe:version-16 --apps erpnext:version-16
```

_Private app using GitHub token_
```bash
fm create mybench --apps private-repo:main --github-token $GITHUB_TOKEN
```

_Set a custom Python and Node version_
```bash
fm create mybench --python 3.13 --node 20
```

!!! note
`fm create` starts the bench automatically. Use `fm start` only if you stopped the bench later.

!!! tip
    After create finishes, run `fm info mybench` to see URLs, installed apps, and admin tool credentials.
