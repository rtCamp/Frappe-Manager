# App Management

Install apps when you create a bench, or add and update them later with `fm update --apps`.

You never ask for Frappe itself: FM puts it at the head of the app list whether or not you name it, and pins `version-16` when you leave the ref off. Everything else you pass is installed after it, in the order you gave.

Install during create:

```bash
fm create mybench --apps erpnext
```

Add an app to an existing bench with `fm update --apps`:

```bash
fm update mybench --apps erpnext
```

FM clones the app, installs its Python and Node dependencies, installs it to the site, builds that app's assets, runs `bench migrate`, and cycles the web and worker processes so the new code is live.

!!! note "Mount benches only"
    `--apps` needs an editable workspace (the default `mount` runtime). On an `image` bench, app code is baked into the image: ship changes with `fm bake` then `fm switch`, or demote first with `fm update mybench --runtime mount`. See the [Deployment guide](../deploy/index.md).

Install a private app by passing a token (or `GITHUB_TOKEN` in the environment):

```bash
fm create mybench --apps org/private-app:main --github-token YOUR_TOKEN
```

For an `org/repo` spec FM tries each authentication method in turn: the token first, then plain HTTPS, then SSH with whatever keys the host has. A spec given as a full HTTPS or SSH URL is cloned from that URL only. The token is written to the bench's `bench_config.toml` in plain text, and later `fm update --apps` calls reuse it, which is why `fm update` has no `--github-token` of its own.

!!! tip "SSH instead of a token"
    Give the app as an SSH URL (`git@github.com:org/repo:main`) and FM clones with the host's keys, no token anywhere in the config file.

App string formats you can use:

```
erpnext                                    -> frappe/erpnext, repo default branch
erpnext:version-16                         -> frappe/erpnext, version-16 branch
frappe/erpnext:version-16                  -> organization/repo and branch
https://github.com/org/repo:main           -> full GitHub URL
git@github.com:org/repo:main               -> SSH URL for private repos
frappe/frappe:version-16#apps/frappe       -> monorepo subdirectory (repo#path/to/subdir)
frappe/erpnext:<40-char-sha>               -> full commit SHA as the ref
```

!!! tip "Monorepo apps"
    When an app lives inside a subdirectory of a larger repo (common in company monorepos), use the `#` separator: `org/repo:branch#apps/my-app`. FM clones the repo once and picks the correct subdirectory.

There is no `fm` command for removing an app. Use the bench CLI inside the bench:

```bash
fm shell mybench -c "bench --site mybench.localhost remove-app erpnext"
```

`fm shell mybench` without `-c` drops you into an interactive shell with the same access, which is where anything the `fm` commands do not cover belongs: `bench migrate`, `bench build`, `bench console`, and the rest.

!!! tip
    `fm info mybench` lists the installed apps with the ref each one sits on and its commit.

## Updating or switching an app's version

`fm update --apps` also works for apps that are already installed; it grafts the requested ref onto the running bench:

```bash
# Move an installed app to another branch or tag
fm update mybench --apps erpnext:version-16

# Several apps at once (repeatable)
fm update mybench --apps erpnext:version-16 --apps hrms:version-16
```

For each app FM replaces the app's code with a fresh clone at the requested ref, then reinstalls dependencies, rebuilds that app's assets, and runs `bench migrate`. The replaced code is **stashed, never deleted**: it moves to a timestamped `.fm-apps-stash-*` directory inside the workspace and FM prints the path, because it may hold uncommitted work. Nothing prunes it, so delete it yourself once you have looked.

!!! warning "Workers are cycled, not drained"
    The restart at the end of `fm update --apps` sends each worker SIGUSR1 and lets supervisor stop it once `[workers].kill_timeout` (15 seconds by default) elapses, so a job still running past that budget is interrupted. Pick a quiet window, or raise the budget in [`[workers]`](../reference/configuration.md#workers) first. `fm restart` is the command that drains: it waits for in-flight jobs and aborts rather than kill one that overruns `drain_timeout`. See the [fmx guide](fmx.md) for driving either from inside the container.

