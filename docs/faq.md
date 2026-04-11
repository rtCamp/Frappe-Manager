# FAQ

??? question "How do I use an external database?"
    Backup your site, stop the bench, configure the external DB (create database and user with remote access), update `site_config.json`, and restore the backup. Always test on a copy first.

??? question "How do I increase upload limits?"
    There are three places: global nginx proxy, bench nginx, and Frappe system settings. The easiest is `fm update mybench --upload-limit 100M` which updates the right file and restarts services.

??? question "How do I restart the server?"
    Inside a bench use `bench restart` or from outside use `fm restart mybench`.

??? question "Where are logs?"
    CLI logs: `~/frappe/logs/fm.log`. Bench web logs: `~/frappe/sites/<bench>/workspace/frappe-bench/logs/web.log`. Use `fm logs mybench --service <service>` to tail logs.

??? question "How do I install apps?"
    Use `fm create mybench --apps erpnext` during create or `fm shell mybench` then `bench get-app` and `bench install-app` for existing benches.

??? question "How do I run bench commands?"
    Use `fm shell mybench -c "bench <command>"` for single commands or `fm shell mybench` for an interactive shell.

??? question "Does VSCode work?"
    Yes. `fm code mybench` opens VSCode attached to the bench. Use `-e` to add extensions and `--debugger` to enable debug support.

??? question "What about Google OAuth and localhost?"
    Google disallows wildcard `*.localhost` redirects. As a workaround, create a symlink from `mybench.localhost` to `localhost` in `~/frappe/sites` and use the full domain as the redirect URI in Google Console.

??? question "Why does create fail with GH registry errors?"
    Your GitHub registry token may be expired. Run `docker logout ghcr.io` and retry with a fresh token.

??? question "Does this work on WSL?"
    Yes on WSL2. Add a hosts entry in Windows (127.0.0.1 mybench.localhost) to access sites from Windows.
