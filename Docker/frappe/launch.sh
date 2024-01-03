#!/bin/zsh
emer() {
   echo "$1"
   exit 1
}

[[ "${WAIT_FOR:-}" ]] || emer "The WAIT_FOR env is not given."
[[ "${COMMAND:-}" ]] || emer "COMMAND is not given."

if [[ ! "${TIMEOUT:-}" ]]; then
    TIMEOUT=300
fi
if [[ ! "${CHECK_ITERATION:-}" ]]; then
    CHECK_ITERATION=10
fi

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
    echo "Running Command: $COMMAND"
    eval "$COMMAND"
else
    echo "$file_to_check did not populate within $timeout_seconds seconds. Giving Up"
fi
