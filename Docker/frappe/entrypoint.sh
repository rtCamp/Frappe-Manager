#!/bin/bash

emer() {
   echo "$1"
   exit 1
}

[[ "${USERID:-}" ]] || emer "[ERROR] Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "[ERROR] Please provide USERGROUP environment variable."

echo "Setting up user"

NAME='frappe'
groupadd -g "$USERGROUP" $NAME
useradd --no-log-init -r -m -u "$USERID" -g "$USERGROUP" -G sudo -s /usr/bin/zsh -d /workspace "$NAME"
usermod -a -G tty "$NAME"
echo "$NAME ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

mkdir -p /opt/user/conf.d

chown -R "$USERID":"$USERGROUP" /opt

if [[ ! -d "/workspace/.oh-my-zsh" ]]; then
   cp -r /opt/user/.oh-my-zsh /workspace/.oh-my-zsh
fi

if [[ ! -f "/workspace/.zshrc" ]]; then
   cat /opt/user/.zshrc > /workspace/.zshrc
fi

if [[ ! -f "/workspace/.profile" ]]; then
   cat /opt/user/.profile > /workspace/.profile
fi

chown -R "$USERID":"$USERGROUP" /workspace

if [ "$#" -gt 0 ]; then
    gosu "$USERID":"$USERGROUP" "/scripts/$@"
else
    gosu "${USERID}":"${USERGROUP}" /scripts/user-script.sh
fi
