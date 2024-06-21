#!/bin/bash

source /scripts/helper-function.sh

emer() {
   echo "$1"
   exit 1
}
cleanup() {
    echo "Received signal, stopping..."
    # Insert cleanup code here (e.g., stop services, clean temp files)
    if [ -n "$running_script_pid" ]; then
        kill -s SIGTERM "$running_script_pid"
    fi
    exit 0
}

trap cleanup SIGQUIT SIGTERM

[[ "${USERID:-}" ]] || emer "[ERROR] Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "[ERROR] Please provide USERGROUP environment variable."

echo "Setting up user"

update_uid_gid "${USERID}" "${USERGROUP}" "frappe" "frappe"

mkdir -p /opt/user/conf.d

start_time=$(date +%s.%N)
chown -R "$USERID":"$USERGROUP" /opt
end_time=$(date +%s.%N)
execution_time=$(awk "BEGIN {print $end_time - $start_time}")
echo "Time taken for chown /opt : $execution_time seconds"

if [[ ! -d "/workspace/.oh-my-zsh" ]]; then
   cp -pr /opt/user/.oh-my-zsh /workspace/
   cp -p /opt/user/fm.zsh-theme /workspace/.oh-my-zsh/custom/themes/
fi

if [[ ! -f "/workspace/.zshrc" ]]; then
   cp -p /opt/user/.zshrc  /workspace/
fi

if [[ ! -f "/workspace/.profile" ]]; then
   cp -p /opt/user/.profile  /workspace/
fi


# start_time=$(date +%s.%N)
# chown -R "$USERID":"$USERGROUP" /workspace
# end_time=$(date +%s.%N)
# execution_time=$(awk "BEGIN {print $end_time - $start_time}")
# echo "Time taken for chown /workspace : $execution_time seconds"

if [ "$#" -gt 0 ]; then
    gosu "$USERID":"$USERGROUP" "/scripts/$@" &
    running_script_pid=$!
else
    gosu "${USERID}":"${USERGROUP}" /scripts/user-script.sh &
    running_script_pid=$!
fi

wait $running_script_pid
