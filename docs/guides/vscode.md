# VSCode Integration

This guide explains what `fm code` does and how to use it. It attaches Visual Studio Code to a running bench so you can edit files and use the integrated terminal.

Open a bench in VSCode:

```bash
fm code mybench
```

Add debugger support:

```bash
fm code mybench --debugger
```

Install extensions when opening:

```bash
fm code mybench -e ms-python.python -e ms-toolsai.jupyter
```

Once open, use the integrated terminal to run bench commands, and restart the dev server with `bench restart` when code changes require it.

!!! tip
    If VSCode does not attach, make sure the bench is started with `fm start mybench` before running `fm code`.
