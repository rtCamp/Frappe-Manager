ARG BASE_IMAGE=ghcr.io/rtcamp/frappe-manager-frappe:latest
FROM ${BASE_IMAGE}
COPY --chown=frappe:frappe ./frappe-bench /workspace/frappe-bench
# NOTE: no `USER frappe` — the supervisor entrypoint must start as root to
# update_uid_gid/chown and gosu-drop to the host UID (same model as dev). The
# COPY above keeps the tree frappe-owned; the entrypoint aligns UID at runtime.
WORKDIR /workspace/frappe-bench
ENTRYPOINT ["/bin/bash","/entrypoint.sh"]
