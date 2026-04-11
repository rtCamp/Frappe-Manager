# Windows / WSL

Frappe Manager works on WSL2. If you use Windows with WSL, the benches run inside the WSL environment and you can access sites from Windows using `*.localhost` names.

To add a site to the Windows hosts file (so `mybench.localhost` resolves):

1. Open Notepad as Administrator.
2. Edit `C:\Windows\System32\drivers\etc\hosts`.
3. Add the line:

```
127.0.0.1 mybench.localhost
```

Tested versions: WSL2 on Windows 11 and 10 (recent updates). You need Administrator access to edit the hosts file.

!!! note
    If you cannot edit the hosts file, ask an administrator or use the built-in Windows network settings.
