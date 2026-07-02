# Windows / WSL

Use WSL2 with Docker Desktop's WSL2 backend for the best experience on Windows.

Tips:

- Keep the ~/frappe/ directory on the Linux filesystem (for example, /home/youruser/frappe), not on /mnt/c/.
- Windows 11: *.localhost usually resolves automatically — no hosts file edit needed.
- Windows 10: you may need to add an entry to the hosts file at C:\Windows\System32\drivers\etc\hosts:

```text
127.0.0.1 mybench.localhost
```

!!! tip
    Install fm from inside WSL exactly as you would on Linux. Run `uv tool install --python 3.13 frappe-manager` or use pipx inside WSL.
