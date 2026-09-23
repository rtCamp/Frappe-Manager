import copy
from pathlib import Path
from typing import Any

from jinja2 import Template
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap as OrderedDict
from ruamel.yaml.comments import CommentedSeq as OrderedList

from frappe_manager import CLI_DEFAULT_DELIMETER
from frappe_manager.docker import DockerVolumeMount
from frappe_manager.docker.compose_exceptions import (
    ComposeFileException,
    ComposeSecretNotFoundError,
    ComposeServiceNotFound,
)
from frappe_manager.migration_manager.version import Version
from frappe_manager.output_manager import get_global_output_handler
from frappe_manager.utils.helpers import ImageRef, get_docker_image_tag, get_template_path, represent_null_empty
from frappe_manager.utils.site import parse_docker_volume

yaml = YAML(typ="rt")
yaml.representer.ignore_aliases = lambda *args: True

yaml.default_flow_style = False
yaml.default_style = None


def _image_report(image: str) -> dict[str, str | None]:
    """Decompose ``image`` into the ``{name, tag, digest, image}`` shape ``get_all_images``
    reports to its callers, via ``ImageRef`` -- the one place fm parses
    a docker image reference -- rather than each re-deriving it with its own naive ``split``.

    This is a READ-ONLY report, so a floating untagged reference (no ``:tag``, no ``@digest``)
    reports the tag docker would actually resolve it to, the literal string ``"latest"``:
    existing callers compare against exactly that. A DIGEST reference is a different case: it
    has no tag at all, floating or otherwise, so ``"latest"`` would be a lie about what it is
    pinned to -- ``tag`` stays ``None`` there and the real pin surfaces through ``digest``
    instead, which is legitimate information a caller may want (e.g. to refuse retagging it).
    """
    ref = ImageRef.parse(image)
    tag = ref.tag if ref.has_tag else (None if ref.is_digest_pinned else "latest")
    return {"name": ref.name, "tag": tag, "digest": ref.digest, "image": image}


class ComposeFile:
    yml: dict[Any, Any]

    def __init__(
        self,
        loadfile: Path,
        template_name: str = "docker-compose.tmpl",
        template_dir: str | None = None,
    ):
        self.compose_path: Path = loadfile
        self.template_name = template_name
        self.is_template_loaded = False

        self.template_dir = "templates"

        if template_dir:
            self.template_dir = template_dir

        self._pending_changes: list[tuple[str, Any]] = []
        self._snapshot: dict | None = None
        self._pending_snapshot: list[tuple[str, Any]] = []

        self.reload()

    def exists(self):
        """
        Check if the compose file exists.

        Returns:
            bool: True if the compose file exists, False otherwise.
        """
        return self.compose_path.exists()

    def reload(self) -> "ComposeFile":
        """Re-read ``yml`` from ``compose_path``, discarding all in-memory state.

        A live ComposeFile is a cache of the file at load time: anything that rewrites the
        file behind it (a backup restore, another writer on the same path) leaves this
        instance stale, and a later save would clobber the newer content with the cached
        copy. Call this after such an external rewrite. Pending changes and snapshots are
        dropped too -- they described the abandoned state, not the file just read. Missing
        file falls back to the template, same as construction.
        """
        self._pending_changes.clear()
        self._snapshot = None
        self._pending_snapshot = []
        if self.exists():
            with open(self.compose_path) as f:
                self.yml = yaml.load(f)
            self.is_template_loaded = False
        else:
            self.yml = self.load_template()
            self.is_template_loaded = True
        return self

    def load_template(self):
        """
        Load the template file and return its contents as a YAML object.
        Renders Jinja2 template variables for dynamic Docker image tags.

        Returns:
            dict: The contents of the template file as a YAML object.
        """
        template_path: Path = get_template_path(self.template_name, self.template_dir)

        template = Template(template_path.read_text())
        image_tag = get_docker_image_tag()
        rendered_template = template.render(frappe_image_tag=image_tag, nginx_image_tag=image_tag)

        yml = yaml.load(rendered_template)
        return yml

    def set_container_names(self, prefix):
        """
        Sets the container names for each service in the Compose file.

        Args:
            prefix (str): The prefix to be added to the container names.
        """
        for service in self.yml["services"].keys():
            self.yml["services"][service]["container_name"] = prefix + CLI_DEFAULT_DELIMETER + service

    def get_container_names(self) -> dict:
        """
        Returns a dictionary of container names for each service defined in the Compose file.

        Returns:
            dict: A dictionary where the keys are service names and the values are container names.
        """
        container_names: dict = {}
        if self.exists():
            services = self.get_services_list()
            for service in services:
                container_names[service] = self.yml["services"][service]["container_name"]
        return container_names

    def get_services_list(self, exclude_disabled: bool = False) -> list:
        """
        Returns a list of services defined in the Compose file.

        Args:
            exclude_disabled: When True, services marked as disabled via the
                ``disabled`` profile are excluded from the returned list.

        Returns:
            list: A list of service names. May be filtered if ``exclude_disabled``
                is True.
        """
        # A compose file that is empty, or has no `services:` key, is an OPERATIONAL problem (a
        # truncated write, a hand-edited file), not a programming error. Left alone, `self.yml`
        # being None raises TypeError and a missing key raises KeyError, and callers that
        # legitimately tolerate a broken services stack cannot name those without also swallowing
        # real bugs. Raising the domain exception lets them be specific.
        services_map = (self.yml or {}).get("services")
        if not isinstance(services_map, dict):
            raise ComposeFileException(f"{self.compose_path} has no usable 'services' section")
        services = list(services_map.keys())
        if exclude_disabled:
            services = [s for s in services if not self.is_service_profile_disabled(s)]
        return services

    def set_user(self, service, uid, gid):
        """
        Set the user for a specific service in the Compose file.

        Args:
            service (str): The name of the service.
            uid (str): The user ID.
            gid (str): The group ID.
        """
        try:
            self.yml["services"][service]["user"] = f"{uid}:{gid}"
        except KeyError as e:
            output = get_global_output_handler()
            output.error("Issue in docker template. Not able to set user.", e)

    def set_root_networks_name(self, networks_name, prefix, external: bool = False):
        """
        Sets the name of the top-level network in the Compose file.

        Args:
            networks_name (str): The name of the network.
            prefix (str): The prefix to be added to the network name.
        """
        if not self.yml["networks"][networks_name]:
            self.yml["networks"][networks_name] = {"name": prefix + f"{CLI_DEFAULT_DELIMETER}network"}
        else:
            self.yml["networks"][networks_name]["name"] = prefix + f"{CLI_DEFAULT_DELIMETER}network"
            self.yml["networks"][networks_name]["external"] = external

    def set_network_alias(self, service_name, network_name, alias: list = []):
        """
        Sets the network alias for a given service in the Compose file.

        Args:
            service_name (str): The name of the service.
            network_name (str): The name of the network.
            alias (list, optional): List of network aliases to be set. Defaults to [].

        Returns:
            bool: True if the network alias is set successfully, False otherwise.
        """
        if alias:
            try:
                all_networks = self.yml["services"][service_name]["networks"]
                if network_name in all_networks:
                    self.yml["services"][service_name]["networks"][network_name] = {"aliases": alias}
                    return True
            except KeyError as e:
                return False
        else:
            return False

    def get_version(self):
        """
        Get the version of the compose file.

        Returns:
            int: The version of the compose file, or 0 if the version is not specified.
        """
        try:
            compose_version = self.yml["x-version"]
            return Version(compose_version)
        except KeyError:
            return Version("0.0.0")

    def set_version(self, version):
        """
        Sets the version of the Compose file.

        Args:
            version (str): The version to set.

        Returns:
            None
        """
        self.yml["x-version"] = version

    def set_all_users(self, users: dict):
        for service in users:
            user_data = users[service]
            if isinstance(user_data, tuple):
                uid, gid = user_data
            else:
                uid = user_data["uid"]
                gid = user_data["gid"]
            self.set_user(service, uid, gid)

    def set_all_envs(self, environments: dict, append: bool = True):
        """
        Sets environment variables for all containers in the Compose file.

        Args:
            environments (dict): A dictionary containing container names as keys and environment variables as values.

        """
        for container_name in environments:
            self.set_envs(container_name, environments[container_name], append=append)

    def set_all_labels(self, labels: dict):
        """
        Sets labels for all containers in the ComposeFile.

        Args:
            labels (dict): A dictionary containing container names as keys and labels as values.
        """
        for container_name in labels:
            self.set_labels(container_name, labels[container_name])

    def set_envs(self, container: str, env: dict, append=False):
        """
        Sets the environment variables for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            env (dict): A dictionary containing the environment variables to be set.
            append (bool, optional): If True, appends the new environment variables to the existing ones.
        """
        new_env = OrderedDict(env)

        if append and type(env) == dict:
            prev_env = self.get_envs(container)
            if prev_env:
                if not type(prev_env) == OrderedList:
                    env = OrderedDict(env)
                    new_env = prev_env | env

        try:
            self.yml["services"][container]["environment"] = new_env
        except KeyError as e:
            pass

    def get_envs(self, container: str) -> dict:
        """
        Get the environment variables for a specific container.

        Args:
            container (str): The name of the container.

        Returns:
            dict: A dictionary containing the environment variables for the container.
                  Returns None if the container or environment variables are not found.
        """
        try:
            env = self.yml["services"][container]["environment"]
            return env
        except KeyError:
            return None

    def set_labels(self, container: str, labels: dict):
        """
        Sets the labels for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            labels (dict): A dictionary containing the labels to be set.

        """
        try:
            self.yml["services"][container]["labels"] = labels
        except KeyError as e:
            pass

    def get_labels(self, container: str) -> dict:
        """
        Get the labels of a specific container.

        Args:
            container (str): The name of the container.

        Returns:
            dict: The labels of the container, or None if the container or labels are not found.
        """
        try:
            labels = self.yml["services"][container]["labels"]
            return labels
        except KeyError:
            return None

    def set_extrahosts(self, container: str, extrahosts: list):
        """
        Set the extra hosts for a specific container in the Compose file.

        Args:
            container (str): The name of the container.
            extrahosts (list): A list of extra hosts to be added.

        """
        try:
            self.yml["services"][container]["extra_hosts"] = extrahosts
        except KeyError as e:
            pass

    def write_to_file(self):
        """
        Writes the Docker Compose file to the specified path.
        """
        try:
            with open(self.compose_path, "w") as f:
                yaml.dump(self.yml, f, transform=represent_null_empty)
        except Exception as e:
            output = get_global_output_handler()
            output.error("Error in writing compose file.", e)

    def get_all_volumes(self):
        """
        Get all the root volumes.
        """

        try:
            volumes = self.yml["volumes"]
        except KeyError as e:
            return {}

        return volumes

    def get_service_volumes(self, service: str) -> list[DockerVolumeMount]:
        """Parsed mounts for one service, deduped, in the order the file lists them.

        `dict.fromkeys` rather than a `set`: both dedupe on the same hash equality, but the set
        also discarded ORDER, and every writer round-trips through this getter (`apply_specs` and
        `generate_compose` read, filter, append and write back). So each regen rewrote the volume
        list in a fresh arbitrary order, which changed docker's service config hash and left the
        container dirty -- the next plain `compose up` recreated it though nothing had been asked
        for, and the restart was attributed to whichever command ran next.

        Worse than "unstable": there was no fixed point to reach. Each run's set was built from
        the previous run's output, so with PYTHONHASHSEED pinned the output alternated between two
        orders forever instead of settling. Repeated identical regens are now byte-identical.
        """
        try:
            raw_volumes = self.yml["services"][service]["volumes"]
        except KeyError as e:
            # `from e` rather than bare: the missing key names WHICH level was absent (the
            # service, or its volumes), which the service-name-only exception drops.
            raise ComposeServiceNotFound(service_name=service) from e

        all_volumes = self.get_all_volumes()
        return [
            parse_docker_volume(volume, all_volumes, self.compose_path)
            for volume in dict.fromkeys(raw_volumes)
        ]

    def set_service_volumes(self, service: str, volumes: list[DockerVolumeMount]) -> None:
        """
        Set specific service volume mounts.
        """
        try:
            volumes_list = [str(volume) for volume in volumes]

            self.yml["services"][service]["volumes"] = volumes_list
        except KeyError as e:
            raise ComposeServiceNotFound(service_name=service)

    def set_root_volumes_names(self, volume_prefix: str) -> None:
        """
        Set names for root level volumes in the compose file with the given prefix.

        Args:
            volume_prefix (str): Prefix to add to volume names
        """
        try:
            volumes = self.yml.get("volumes", {})
            if volumes:
                for volume_name in volumes:
                    if volumes[volume_name] is None:
                        volumes[volume_name] = {}
                    volumes[volume_name]["name"] = volume_prefix + CLI_DEFAULT_DELIMETER + volume_name

        except KeyError as e:
            output = get_global_output_handler()
            output.warning(f"Error setting volume names: {e!s}")

    def set_secret_file_path(self, secret_name, file_path):
        try:
            self.yml["secrets"][secret_name]["file"] = file_path
        except KeyError:
            output = get_global_output_handler()
            output.warning("Not able to set secrets in compose")

    def get_secret_file_path(self, secret_name) -> Path:
        try:
            file_path = self.yml["secrets"][secret_name]["file"]
            return Path(file_path)
        except KeyError:
            raise ComposeSecretNotFoundError(secret_name, str(self.compose_path.absolute()))

    def remove_container_user(self, container):
        try:
            del self.yml["services"][container]["user"]
        except KeyError:
            output = get_global_output_handler()
            output.warning("user not present")

    def remove_container_name(self, container):
        """Drop the fixed ``container_name`` so the service can be scaled
        (docker compose rejects ``--scale`` on services with a container_name)."""
        self.yml["services"].get(container, {}).pop("container_name", None)

    def set_container_name(self, container, name):
        """Set a single service's fixed container_name (restores it after a
        rolling render stripped it for scaling)."""
        if container in self.yml["services"]:
            self.yml["services"][container]["container_name"] = name

    def get_all_images(self):
        """
        Retrieves all the images for each service in the Compose file.

        Returns:
            dict: A dictionary containing the service names as keys and their respective image
            names, tags, digests and full references as values (see ``_image_report``).
        """
        images = {}
        for service in self.yml["services"].keys():
            try:
                image = self.yml["services"][service]["image"]
            except KeyError:
                continue
            images[service] = _image_report(image)
        return images

    def set_all_images(self, images: dict):
        """
        Sets the image for all services in the ComposeFile.

        Args:
            images (dict): Service name -> ``{"name": repo, "tag": tag|None, "digest": digest|None}``
            (``tag``/``digest`` are optional keys; a caller with only ``name``/``tag``, the shape
            ``get_all_images`` has always returned, still works unchanged). A reference needs at
            most one of ``tag``/``digest`` in practice, but both are appended when both are given
            (docker itself allows ``repo:tag@digest``), so nothing here silently drops a digest a
            caller explicitly kept.
        """
        for service, image_info in images.items():
            image = image_info["name"]
            tag = image_info.get("tag")
            digest = image_info.get("digest")
            if tag:
                image += f":{tag}"
            if digest:
                image += f"@{digest}"
            if service in self.yml["services"]:
                self.yml["services"][service]["image"] = image

    def set_service_command(self, service: str, command: str) -> None:
        """
        Set the command for a specific service in the compose file.

        Args:
            service (str): The name of the service
            command (str): The command to set for the service
        """
        if service not in self.yml["services"]:
            raise KeyError(f"Service {service} not found in compose file")

        self.yml["services"][service]["command"] = command

    def set_service_restart(self, service: str, restart_policy: str) -> None:
        if service not in self.yml["services"]:
            raise KeyError(f"Service {service} not found in compose file")

        self.yml["services"][service]["restart"] = restart_policy

    def set_all_services_restart(self, restart_policy: str) -> None:
        services = self.get_services_list()
        for service in services:
            self.set_service_restart(service, restart_policy)


    def _apply_change(self, change: tuple[str, Any]):
        """
        Internal: Apply a single pending change.

        Args:
            change: Tuple containing (change_type, *args)
        """
        change_type = change[0]

        if change_type == "envs":
            _, envs, append = change
            self.set_all_envs(envs, append=append)
        elif change_type == "labels":
            _, labels = change
            self.set_all_labels(labels)
        elif change_type == "prefix":
            _, prefix, network_name = change
            self.set_container_names(prefix)
            self.set_root_volumes_names(prefix)
            self.set_root_networks_name(network_name, prefix)
        elif change_type == "version":
            _, version = change
            self.set_version(version)
        elif change_type == "users":
            _, users = change
            self.set_all_users(users)
        elif change_type == "restart":
            _, restart_policy = change
            self.set_all_services_restart(restart_policy)

    def commit(self) -> "ComposeFile":
        """Apply all pending changes and save to file. Returns self for chaining.

        Atomic in memory: a change failing mid-application restores ``yml`` to its
        pre-commit state and re-raises, so nothing half-applied survives. The queue is
        KEPT on failure -- the caller that fixes the cause retries the same commit;
        clearing it would silently drop the changes the retry is for.
        """
        before = copy.deepcopy(self.yml)
        try:
            for change in self._pending_changes:
                self._apply_change(change)
        except Exception:
            self.yml = before
            raise
        self._pending_changes.clear()
        self.write_to_file()
        return self

    def rollback(self) -> "ComposeFile":
        """
        Discard all pending changes without applying them.

        Returns:
            Self for chaining
        """
        self._pending_changes.clear()
        return self


    def with_envs(self, envs: dict, append: bool = True) -> "ComposeFile":
        """
        Fluent setter for environment variables.

        Args:
            envs: Dictionary of service names to environment variables
            append: If True, append to existing envs; if False, replace

        Returns:
            Self for chaining
        """
        self._pending_changes.append(("envs", envs, append))
        return self

    def with_labels(self, labels: dict) -> "ComposeFile":
        """
        Fluent setter for labels.

        Args:
            labels: Dictionary of service names to labels

        Returns:
            Self for chaining
        """
        self._pending_changes.append(("labels", labels))
        return self

    def with_prefix(self, prefix: str, network_name: str = "site-network") -> "ComposeFile":
        """
        Set prefix for containers, volumes, and networks at once.

        Args:
            prefix: Prefix to apply
            network_name: Network name to configure (default: "site-network")

        Returns:
            Self for chaining
        """
        self._pending_changes.append(("prefix", prefix, network_name))
        return self

    def with_version(self, version: str) -> "ComposeFile":
        """
        Fluent setter for compose file version.

        Args:
            version: Version string to set

        Returns:
            Self for chaining
        """
        self._pending_changes.append(("version", version))
        return self

    def with_users(self, users: dict) -> "ComposeFile":
        converted_users = {}
        for service, user_data in users.items():
            if isinstance(user_data, tuple):
                converted_users[service] = {"uid": str(user_data[0]), "gid": str(user_data[1])}
            else:
                converted_users[service] = user_data
        self._pending_changes.append(("users", converted_users))
        return self

    def with_restart(self, restart_policy: str) -> "ComposeFile":
        """
        Fluent setter for restart policy (applies to all services).

        Args:
            restart_policy: Docker restart policy ("no", "always", "on-failure", "unless-stopped")

        Returns:
            Self for chaining
        """
        self._pending_changes.append(("restart", restart_policy))
        return self


    def configure_bench(
        self,
        prefix: str,
        version: str,
        envs: dict | None = None,
        labels: dict | None = None,
        users: dict | None = None,
        network_name: str = "site-network",
        auto_save: bool = True,
    ) -> "ComposeFile":
        """
        Configure a complete bench in one call.

        Args:
            prefix: Container/volume/network prefix
            version: Compose file version
            envs: Environment variables by service
            labels: Labels by service
            users: User configurations by service (supports both dict and tuple formats)
            network_name: Network name to configure
            auto_save: Whether to save immediately

        Returns:
            Self for chaining
        """
        if envs:
            self.set_all_envs(envs)
        if labels:
            self.set_all_labels(labels)
        if users:
            converted_users = {}
            for service, user_data in users.items():
                if isinstance(user_data, tuple):
                    converted_users[service] = {"uid": str(user_data[0]), "gid": str(user_data[1])}
                else:
                    converted_users[service] = user_data
            self.set_all_users(converted_users)

        self.set_container_names(prefix)
        self.set_root_volumes_names(prefix)
        self.set_root_networks_name(network_name, prefix)
        self.set_version(version)

        if auto_save:
            self.write_to_file()

        return self

    def configure_service(
        self,
        service: str,
        env: dict | None = None,
        labels: dict | None = None,
        user: tuple[str, str] | None = None,  # (uid, gid)
        command: str | None = None,
        volumes: list[DockerVolumeMount] | None = None,
        auto_save: bool = True,
    ) -> "ComposeFile":
        """
        Configure a single service in one call.

        Args:
            service: Service name
            env: Environment variables
            labels: Service labels
            user: Tuple of (uid, gid)
            command: Service command
            volumes: Volume mounts
            auto_save: Whether to save immediately

        Returns:
            Self for chaining
        """
        if env:
            self.set_envs(service, env)
        if labels:
            self.set_labels(service, labels)
        if user:
            uid, gid = user
            self.set_user(service, uid, gid)
        if command:
            self.set_service_command(service, command)
        if volumes:
            self.set_service_volumes(service, volumes)

        if auto_save:
            self.write_to_file()

        return self


    def __enter__(self) -> "ComposeFile":
        """Enter context: snapshot current state (the yml AND the pending-change queue)."""
        self._snapshot = copy.deepcopy(self.yml)
        self._pending_snapshot = list(self._pending_changes)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit context: save on success, roll back on error.

        Rollback restores BOTH the yml and the pending-change queue: a ``with_*`` queued
        inside the failed block must not survive to be applied by a later ``commit()``
        as if the transaction had succeeded. The snapshot guard is ``is not None`` on
        purpose -- an empty-but-snapshotted yml is still the state to restore.
        """
        if exc_type is None:
            self.write_to_file()
        else:
            if self._snapshot is not None:
                self.yml = self._snapshot
            self._pending_changes = list(self._pending_snapshot)
            output = get_global_output_handler()
            output.warning(f"ComposeFile changes rolled back due to error: {exc_val}")
        self._snapshot = None
        self._pending_snapshot = []
        return False  # Don't suppress exceptions

    def is_service_profile_disabled(self, service: str) -> bool:
        try:
            service_definition = self.yml["services"][service]
            if not hasattr(service_definition, "get"):
                return False
            profiles = service_definition.get("profiles", [])
            if isinstance(profiles, str):
                return profiles == "disabled"
            return "disabled" in profiles
        except (KeyError, TypeError, AttributeError):
            return False

    def set_service_disabled(self, service: str, disabled: bool = True) -> None:
        """Add or remove the ``disabled`` compose profile for a service.

        The profile is how fm suppresses a service it must not start (a bench
        pointed at an external redis): docker compose never brings up a service
        whose profile is not activated, and ``get_services_list(exclude_disabled=True)``,
        the readiness wait and the running-status checks all skip it. Idempotent in
        both directions, other profiles on the service are preserved, and a service
        that needs no change is left byte-identical.
        """
        try:
            service_definition = self.yml["services"][service]
        except (KeyError, TypeError) as e:
            raise ComposeServiceNotFound(service) from e

        current = service_definition.get("profiles")
        profiles = [current] if isinstance(current, str) else list(current or [])
        desired = [profile for profile in profiles if profile != "disabled"]
        if disabled:
            desired.append("disabled")
        if desired == profiles:
            return
        if desired:
            service_definition["profiles"] = desired
        else:
            service_definition.pop("profiles", None)
