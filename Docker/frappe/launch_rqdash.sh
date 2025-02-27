#!/usr/bin/env sh
set -e

# Wait for env directory to be created (timeout: 5 mins)
timeout=300
interval=10
env_dir="/workspace/frappe-bench/env"

while [ ! -d "$env_dir" ]; do
    if [ "$timeout" -le 0 ]; then
        echo "Timeout waiting for $env_dir to be created"
        exit 1
    fi
    echo "Waiting for $env_dir to be created..."
    sleep $interval
    timeout=$((timeout - interval))
done

# Install rq-dashboard
$env_dir/bin/pip install --quiet git+https://github.com/Parallels/rq-dashboard.git@v0.8.2

# Get Redis queue URL from common site config
REDIS_QUEUE_URL=$(jq -r '.redis_queue' /workspace/frappe-bench/sites/common_site_config.json)

if [ -z "$REDIS_QUEUE_URL" ]; then
    echo "Failed to get Redis queue URL from config"
    exit 1
fi

# Launch RQ dashboard
exec $env_dir/bin/python -m rq_dashboard --redis-url "$REDIS_QUEUE_URL" --port 9181 --bind 0.0.0.0
