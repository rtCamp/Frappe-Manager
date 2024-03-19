#!/usr/bin/bash

# Function: update_common_site_config
# Description: Updates the common site config file with the provided key-value pair.
# Parameters:
#   - key: The key to be updated in the config file.
#   - value: The value to be assigned to the key in the config file.
#   - is_value_json: Optional parameter. Set to true if the value provided is in JSON format.
update_common_site_config() {
    local key="$1"
    local value="$2"
    local is_value_json="$3" # set to true if any value provided
    local config_file="/workspace/frappe-bench/sites/common_site_config.json"

    # Check if the config file exists
    if [ ! -f "$config_file" ]; then
        echo "Error: Common site config file not found."
        return 1
    fi

    # Update the config file using jq
    if [[ "${is_value_json:-}" ]]; then
        jq -r --arg key "$key" --argjson value "$value" '.[$key] = $value' "$config_file" > "$config_file.tmp" && mv "$config_file.tmp" "$config_file"
    else
        jq -r --arg key "$key" --arg value "$value" '.[$key] = $value' "$config_file" > "$config_file.tmp" && mv "$config_file.tmp" "$config_file"
    fi

    echo "Updated $key => $value"
}

# Function to retrieve a value from the common site config file based on a given key
# Arguments:
#   - key: The key to search for in the common site config file
# Returns:
#   - The value corresponding to the given key, or "null" if the key does not exist
get_common_site_config() {
    local key="$1"
    local config_file="/workspace/frappe-bench/sites/common_site_config.json"

    # Check if the config file exists
    if [ ! -f "$config_file" ]; then
        echo "Error: Common site config file not found."
        return 1
    fi

    # Use jq to extract the value corresponding to the given key
    value=$(jq -r --arg key "$key" '.[$key]' "$config_file")

    # Check if the key exists
    if [ "$value" = "null" ]; then
        echo "null"
    else
        echo "$value"
    fi
}

# Function to install apps
# Parameters:
#   - apps_lists: comma-separated list of apps to install
#   - already_installed_apps: comma-separated list of apps already installed
install_apps() {
    local apps_lists
    local already_installed_apps
    local remove_apps

    apps_lists="$1"
    already_installed_apps="$2"

    keep_prebaked_apps=$(mktemp)

    # get apps_json if not available then default to empty list
    apps_json='[]'

    if [[ "${apps_lists:-}" ]]; then
        apps=$(awk -F ',' '{for (i=1; i<=NF; i++) {print $i}}' <<<"$apps_lists")
        for app in $apps; do

            app_contains_http=0

            if [[ $app == http* ]]; then
                app_contains_http=1
                app="${app//http:/https:}"
                app="${app//https:/https;}"
            fi

            app_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $1}')
            branch_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $2}')

            if [[ "${app_contains_http}" -gt 0 ]]; then
                app_name="${app_name//https\;/https:}"
            fi

            # check if app prebaked
            ALREADY_PREBAKED=$(grep -cw "$app" <<<"$already_installed_apps" || exit 0)

            if [[ "${ALREADY_PREBAKED}" -gt 0 ]]; then
                echo "${app} already prebaked and installed."
                echo "${app}" >> "$keep_prebaked_apps"
                # apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" '.+ [$app_name]')
                continue
            fi

            if [[ "${branch_name:-}" ]]; then
                echo "Installing app $app_name -> $branch_name"
                $BENCH_COMMAND get-app --overwrite --skip-assets --branch "${branch_name}" "${app_name}"
            else
                echo "Installing app $app_name"
                $BENCH_COMMAND get-app --overwrite --skip-assets "${app_name}"
            fi
        done
    else
        echo "No app provided to install."
    fi

    remove_apps=$(awk -F ',' '{for (i=1; i<=NF; i++) {print $i}}' <<<"$already_installed_apps")

    for app in $remove_apps; do
        app_name=$(echo "$app" | awk 'BEGIN {FS=":"}; {print $1}')

        is_app_installed=$(cat "$keep_prebaked_apps" | grep -cw "$app_name"  || exit 0)

        if [[ ! "$is_app_installed" -gt 0 ]]; then
            echo "remove app ${app_name}"
            (bench rm --no-backup --force "${app_name}" || exit 0)
            apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" 'del(.[] | select(. == $app_name))')
        fi
    done

    # create apps_txt
    local apps_list

    apps_txt=$(mktemp)

    apps_list=$(ls -1 apps || exit 0)

    for app_name in $(echo "$apps_list"); do
        get_app_name  "$app_name"
        echo "$APP_NAME" >> "$apps_txt"
    done

    cp "$apps_txt" sites/apps.txt

    # create apps_json
    for app_name in $(cat "$apps_txt" | grep -v 'frappe' || exit 0); do
        apps_json=$(echo "$apps_json" | jq -rc --arg app_name "${app_name}" '.+ [$app_name]')
    done

    update_common_site_config install_apps "$apps_json" 'true'
}


# Function: update_uid_gid
# Description: Updates the UID (User ID) and GID (Group ID) of a user and group in the system.
# Parameters:
#   - uid: The new UID to be assigned to the user.
#   - gid: The new GID to be assigned to the group.
#   - username: The username of the user.
#   - groupname: The name of the group.
# Returns:
#   - 0: If the UID and GID are updated successfully.
#   - 1: If the function is called with incorrect number of parameters or if the UID or GID are not numeric values.
# Notes:
#   - If a user or group with the same UID or GID already exists, it will be deleted and updated with the provided username or groupname.
update_uid_gid() {
    if [ "$#" -ne 4 ]; then
        echo "Usage: update_uid_gid <uid> <gid> <username> <groupname>"
        return 1
    fi

    uid="$1"
    gid="$2"
    username="$3"
    groupname="$4"

    # Validate numeric fields
    if [[ ! "$uid" =~ ^[0-9]+$ || ! "$gid" =~ ^[0-9]+$ ]]; then
        echo "Error: UID and GID must be numeric values."
        return 1
    fi

    # Check if UID and GID already exist
    existing_uid_user="$(getent passwd "$uid" | cut -d: -f1)"
    existing_gid_group="$(getent group "$gid" | cut -d: -f1)"

    if [[ ! -z "$existing_uid_user" && "$existing_uid_user" != "$username" ]]; then
        # User already registered, but with different username
        # Delete the user with existing UID and update with provided username
        userdel "$existing_uid_user"
        echo "User $existing_uid_user deleted."
    fi

    if [[ ! -z "$existing_gid_group" && "$existing_gid_group" != "$groupname" ]]; then
        # Group already registered, but with different groupname
        # Delete the group with existing GID and update with provided groupname
        groupdel "$existing_gid_group"
        echo "Group $existing_gid_group deleted."
    fi

    # Update UID and GID
    usermod -u "$uid" "$username"
    groupmod -g "$gid" "$groupname"

    echo "UID and GID updated successfully."
}

# this return the list of apps
# input
# $1 -> app_name respective to apps dir
get_app_name(){
    local app="$1"
    local app_dir
    app_dir="/workspace/frappe-bench/apps/${app}"
    hooks_py_path=$(find "$app_dir" -maxdepth 2 -type f -name hooks.py)

    # Extract the app name from the hooks.py file
    APP_NAME=$(awk -F'"' '/app_name/{print $2}' "$hooks_py_path" || exit 0)

    if ! [[ "${APP_NAME:-}" ]]; then
        # If the app name is not found, use app name from basename of the app dir
        APP_NAME=${app##*/}
    fi
}
