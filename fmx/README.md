# fmx

A command-line utility designed to interact with `supervisord` instances managed within the [Frappe Manager](https://github.com/rtCamp/Frappe-Manager) environment.

It allows users inside the Frappe Docker container to easily:

*   **Stop** services/processes
*   **Start** services/processes
*   **Restart** services/processes
*   Check the **Status** of services/processes

## Overview

Frappe Manager uses `supervisord` to manage background processes like the Frappe web server (gunicorn/nginx unit), scheduler, and workers. This tool (`fmx`) provides a convenient interface to control these processes directly from the command line within the container, using the socket files created by `supervisord`.

## Installation

This tool is typically intended to be used *inside* the Docker containers managed by Frappe Manager. It should be pre-installed or made available within the container's environment (e.g., added to the `PATH`).

If manual installation is needed (e.g., during development or customization):

```bash
# Ensure you are inside the relevant Docker container
pip install .
```

(This assumes you are in the `fmx` directory containing `pyproject.toml`).

## Usage

The tool uses a simple command structure:

```bash
fmx [COMMAND] [SERVICE_NAMES...] [OPTIONS]
```

**Commands:**

*   `stop`: Stop services or specific processes.
*   `start`: Start services or specific processes.
*   `restart`: Restart services (gracefully by default).
*   `status`: Show the status of services and their processes.

**Arguments:**

*   `SERVICE_NAMES`: (Optional) One or more service names (e.g., `frappe`, `nginx`). If omitted, the command applies to *all* detected services.

**Options:**

*   `--process` / `-p`: Target specific process(es) within a service (e.g., `-p worker_short -p worker_long`). Can be used multiple times.
*   `--force` / `-f`: (For `restart`) Use a less graceful, potentially faster restart method.
*   `--wait` / `--no-wait`: (For `start`/`stop`/`restart`) Wait for the operation to complete before the command exits (default is `--wait`).
*   `--wait-jobs`: Wait for active Frappe background jobs to finish before stopping/restarting.
*   `--site-name`: Frappe site name (required when using `--wait-jobs`).
*   `--wait-jobs-timeout`: Maximum seconds to wait for jobs (default: 300).
*   `--wait-jobs-poll`: Job check interval in seconds (default: 5).
*   `--queue` / `-q`: Target specific job queue(s) when using `--wait-jobs`. Can be used multiple times.
*   `--help`: Show help message for the tool or a specific command.

**Examples:**

```bash
# Check status of all services
fmx status

# Check status of only the 'frappe' service
fmx status frappe

# Stop all processes in all services
fmx stop

# Start only the 'frappe' service
fmx start frappe

# Restart the 'frappe' service gracefully
fmx restart frappe

# Stop only the 'worker_short' process within the 'frappe' service
fmx stop frappe --process worker_short

# Restart all services forcefully (less common)
fmx restart --force
```

## Environment Variables

*   `SUPERVISOR_SOCKET_DIR`: Specifies the directory where `supervisord` socket files (`.sock`) are located. Defaults to `/fm-sockets`.

## License

This project is licensed under the MIT License - see the [LICENSE](../LICENSE) file in the main Frappe Manager repository for details.
