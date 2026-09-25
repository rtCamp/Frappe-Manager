# App management

Install apps when you create a bench, or add and update them later with `fm apps add`.

You never ask for Frappe itself: FM puts it at the head of the app list whether or not you name it, and pins `version-16` when you leave the ref off. Everything else you pass is installed after it, in the order you gave.

Install during create:

```bash
fm create mybench --apps erpnext
```

Add an app to an existing bench with `fm apps add`:

```bash
# Fetch the code and record it on the bench (installs into nothing yet)
fm apps add mybench erpnext

# Fetch the code and install it into every site the bench serves
fm apps add mybench/all erpnext

# Fetch the code and install it into one site only
fm apps add mybench/SITE erpnext
```

FM always clones the app, installs its Python and Node dependencies, and builds that app's assets. A bare `BENCH` stops there: the app is recorded on the bench but installed into nothing, so any site created afterwards picks it up. Naming a site with `BENCH/SITE` or `BENCH/all` also installs the app into that site's database, runs `bench migrate`, and cycles the web and worker processes so the new code is live; `BENCH/all` reports a per-site failure and keeps going instead of stopping at the first.

!!! note "Mount benches only"
    `fm apps add` needs an editable workspace (the default `mount` runtime). On an `image` bench, app code is baked into the image: ship changes with `fm bake` then `fm switch`. Runtime is fixed at create time; if you need an editable copy of what an image bench runs, create a new bench with `fm create NAME --seed-image REPO:TAG`. See the [Deployment guide](../deploy/index.md).

Install a private app by passing a token (or `GITHUB_TOKEN` in the environment):

```bash
fm create mybench --apps org/private-app:main --github-token YOUR_TOKEN
```

For an `org/repo` spec FM tries each authentication method in turn: the token first, then plain HTTPS, then SSH with whatever keys the host has. A spec given as a full HTTPS or SSH URL is cloned from that URL only. The token is written to the bench's `bench_config.toml` in plain text, and later `fm apps add` calls reuse it, which is why `fm apps add` has no `--github-token` of its own.

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
    `fm apps list mybench` prints the apps recorded in `bench_config.toml` next to what's actually installed on disk (`sites/apps.txt`). `fm info mybench` lists the installed apps with the ref each one sits on and its commit.

## Updating or switching an app's version

`fm apps add` also works for apps that are already installed; it grafts the requested ref onto the running bench:

```bash
# Move an installed app to another branch or tag, on every site the bench serves
fm apps add mybench/all erpnext:version-16

# Several apps at once
fm apps add mybench/all erpnext:version-16 hrms:version-16
```

For each app FM replaces the app's code with a fresh clone at the requested ref, reinstalls dependencies, and rebuilds that app's assets, whether or not a site is named. Naming a site (`mybench/SITE` or `mybench/all`) is what actually installs the new code into that site: it runs `bench migrate` there and cycles the web and worker processes. A bare `fm apps add mybench erpnext:version-16` only grafts the code, the same as fetching a new app. The replaced code is **stashed, never deleted**: it moves to a timestamped `.fm-apps-stash-*` directory inside the workspace and FM prints the path, because it may hold uncommitted work. Nothing prunes it, so delete it yourself once you have looked.

!!! note "Draining, not just cycling"
    `fm apps add` drains RQ workers by default: before installing or migrating, it suspends them and waits (up to `[workers].drain_timeout`, 300 seconds by default) for in-flight jobs to finish, then cycles the web and worker supervisor processes and resumes the workers. A drain that times out changes nothing: the workers resume and the command exits non-zero, so raise `[workers].drain_timeout` or re-run in a quieter window. Every command that drains follows that same rule, `fm stop` included -- a timeout there leaves the bench running rather than stopping it with jobs mid-flight. `--no-drain` skips the wait, so a job still running when the SIGUSR1 cycle lands is interrupted once `[workers].kill_timeout` (15 seconds by default) elapses. `fm restart` follows the same drain/`--no-drain` rule for a plain restart, and so do `fm stop` (a container stop kills a running job after ten seconds and leaves it recorded as started forever) and `fm update` for the flags that disturb workers, and so does `fm update` for the flags that disturb workers (`--python`, `--node`, `--recreate-python-env`, and `--restart-policy`, which recreates the worker containers outright). See the [fmx guide](fmx.md) for driving either from inside the container, and [`[workers]`](../reference/configuration.md#workers) for the timeouts.

