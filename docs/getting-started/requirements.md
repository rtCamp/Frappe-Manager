# Requirements

This page lists what you need before installing fm.

- Python 3.13 - required only to run the fm tool itself (fm needs `>=3.13,<3.14`; `uv tool install --python 3.13` downloads it for you). Benches use their own Python inside containers.
- Docker Desktop (Mac/Windows) or Docker Engine (Linux) - benches run inside Docker containers.
- Git - required to clone Frappe apps during bench creation.
- A non-root user with permission to use Docker.
- Ports 80 and 443 must be free on the machine - the global nginx proxy uses them.

!!! tip "Quick checks"
    Run these to confirm your environment:

    ```bash
    python3 --version
    docker --version
    git --version
    ```

On Windows, see the [WSL guide](../guides/wsl.md).
