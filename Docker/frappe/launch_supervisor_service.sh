#!/bin/zsh
emer() {
   echo "$1"
   exit 1
}

cleanup() {
    echo "Received signal SIGTERM, stopping..."
    if [ -n "$running_script_pid" ]; then
        kill -s SIGTERM "$running_script_pid"
    fi
    exit 0
}

trap cleanup SIGTERM

if [[ -n "${WORKER_NAME:-}" ]]; then
    SERVICE_NAME="${WORKER_NAME}"
fi

[[ "${SERVICE_NAME:-}" ]] || emer "[ERROR] Please provide SERVICE_NAME environment variable."

SOCK_DIR='/fm-sockets'
SOCK_SERVICE_PATH="$SOCK_DIR/$SERVICE_NAME.sock"

echo "Setting supervisord sock directory to $SOCK_SERVICE_PATH"

mkdir -p /opt/user/conf.d $SOCK_DIR
chown "$USERID:$USERGROUP" $SOCK_DIR /opt/user/conf.d
rm -rf "$SOCK_SERVICE_PATH"

sed -i "s/\opt\/user\/supervisor\.sock/fm-sockets\/${SERVICE_NAME}\.sock/g" /opt/user/supervisord.conf
echo "supervisord configured $?"

if [[ -n "${SUPERVISOR_SERVICE_CONFIG_FILE_NAME:-}" ]]; then
    # Use the provided config file name
    CONFIG_FILE_NAME="${SUPERVISOR_SERVICE_CONFIG_FILE_NAME}"
elif [[ -n "${WORKER_NAME:-}" ]]; then
    CONFIG_FILE_NAME="${WORKER_NAME}.workers.fm.supervisor.conf"
elif [[ -n "${SERVICE_NAME:-}" ]]; then
    # Generate config file name from SERVICE_NAME
    CONFIG_FILE_NAME="${SERVICE_NAME}.fm.supervisor.conf"
else
    # Neither variable is set, error out
    emer "Either SUPERVISOR_SERVICE_CONFIG_FILE_NAME or WORKER_NAME or SERVICE_NAME env must be given."
fi

# Set SUPERVISOR_SERVICE_CONFIG_FILE_NAME for use in rest of the script
SUPERVISOR_SERVICE_CONFIG_FILE_NAME="${CONFIG_FILE_NAME}"

if [[ ! "${TIMEOUT:-}" ]]; then
    TIMEOUT=300
fi
if [[ ! "${CHECK_ITERATION:-}" ]]; then
    CHECK_ITERATION=10
fi

CHANGE_DIR=/workspace/frappe-bench/logs
WAIT_FOR="/workspace/frappe-bench/config/${SUPERVISOR_SERVICE_CONFIG_FILE_NAME}"

file_to_check="${WAIT_FOR}"
timeout_seconds="$TIMEOUT"

start_time=$(date +%s)
check_iteration="$CHECK_ITERATION"

total_iteration=1
iteration=1
IFS=',' read -A files <<< "$file_to_check"
while true; do
    all_files_exist=true

    for file in ${files[@]}; do
        if [[ ! -s "$file" ]]; then
            all_files_exist=false
            break
        fi
    done

    if $all_files_exist || [[ $(( $(date +%s) - start_time )) -ge "$timeout_seconds" ]]; then
        break
    fi

    sleep 1
    ((total_iteration++))
    ((iteration++))
    if [ $((iteration % check_iteration)) -eq 0 ]; then
        echo "Checked $iteration times..."
    fi
done

if [[ "${CHANGE_DIR:-}" ]];then
   cd "$CHANGE_DIR" || true
fi

source /opt/user/.zshrc

if $all_files_exist; then
    echo "$file_to_check populated within $total_iteration seconds."
    echo "Starting supervisor service for file: $SUPERVISOR_SERVICE_CONFIG_FILE_NAME"
    ln -sfn /workspace/frappe-bench/config/${SUPERVISOR_SERVICE_CONFIG_FILE_NAME} /opt/user/conf.d/${SUPERVISOR_SERVICE_CONFIG_NAME}
    supervisord -c /opt/user/supervisord.conf &
    running_script_pid=$!
    wait $running_script_pid
else
    echo "$file_to_check did not populate within $timeout_seconds seconds. Giving Up"
fi
