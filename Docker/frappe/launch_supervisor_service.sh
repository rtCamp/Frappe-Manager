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

[[ "${SUPERVISOR_SERVICE_CONFIG_FILE_NAME:-}" ]] || emer "The SUPERVISOR_SERVICE_CONFIG_FILE_NAME env is not given."

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
