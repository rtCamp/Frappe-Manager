#!/usr/bin/env bash
fuser -k 80/tcp
fuser -k 9000/tcp
bench start --procfile /workspace/frappe-bench/Procfile.local_setup
