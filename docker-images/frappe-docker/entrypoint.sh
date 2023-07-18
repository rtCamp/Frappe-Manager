#!/bin/bash
#set -x
emer() {
   echo "$1"
   exit 1
}

[[ "${USERID:-}" ]] || emer "Please provide USERID environment variable."
[[ "${USERGROUP:-}" ]] || emer "Please provide USERGROUP environment variable."

echo "Setting up user"
NAME='frappe'
groupadd -g "$USERGROUP" $NAME
useradd --no-log-init -r -m -u "$USERID" -g "$USERGROUP" -G sudo -s /bin/bash "$NAME"
usermod -a -G tty "$NAME"
echo "$NAME ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

chown -R "$USERID":"$USERGROUP" /opt
cat /opt/user/.bashrc >> /home/$NAME/.bashrc
cat /opt/user/.profile >> /home/$NAME/.profile
chown -R "$USERID":"$USERGROUP" /workspace

gosu "$USERID" /user-script.sh
