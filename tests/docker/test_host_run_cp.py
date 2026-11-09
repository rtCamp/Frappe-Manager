"""What `host_run_cp` leaves on disk, asked of a real daemon.

The unit suite replaces this function with a recorder that notes the call order, so no
test has ever observed the file it is supposed to produce. Every bug of the shape "the
app list was copied onto something that was not a file" lives here and nowhere else.
"""

from pathlib import Path

import pytest

from frappe_manager import CONTAINER_SITES_DIR
from frappe_manager.utils.docker import host_run_cp

APPS_TXT_IN_IMAGE = f"{CONTAINER_SITES_DIR}/apps.txt"
ABSENT_IN_IMAGE = f"{CONTAINER_SITES_DIR}/nothing-here.txt"
BAKED = "frappe\nerpnext\n"

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600)]


def test_file_source_lands_as_a_regular_file(docker_client, fixture_image, tmp_path: Path):
    """A file copied out of an image is a file on the host, carrying the image's bytes."""
    dest = tmp_path / "sites" / "apps.txt"
    dest.parent.mkdir(parents=True)

    host_run_cp(fixture_image, APPS_TXT_IN_IMAGE, str(dest), docker_client)

    assert dest.is_file(), f"{dest} is not a regular file"
    assert dest.read_text() == BAKED


def test_existing_file_destination_is_replaced_not_nested(docker_client, fixture_image, tmp_path: Path):
    """Re-copying over an existing file overwrites it; it never becomes a directory holding a copy."""
    dest = tmp_path / "sites" / "apps.txt"
    dest.parent.mkdir(parents=True)
    dest.write_text("stale\n")

    host_run_cp(fixture_image, APPS_TXT_IN_IMAGE, str(dest), docker_client)

    assert dest.is_file()
    assert dest.read_text() == BAKED
    assert not (dest.parent / "apps.txt" / "apps.txt").exists()


def test_directory_destination_does_not_silently_nest(docker_client, fixture_image, tmp_path: Path):
    """Asked for a file, `host_run_cp` yields a file -- or refuses; it never leaves a directory.

    Docker created the destination as a root-owned directory when a bind source was missing;
    `docker cp` of a file into a directory nests, and the caller's `read_text()` then dies far
    from the cause.
    """
    dest = tmp_path / "sites" / "apps.txt"
    dest.mkdir(parents=True)

    failure: Exception | None = None
    try:
        host_run_cp(fixture_image, APPS_TXT_IN_IMAGE, str(dest), docker_client)
    except Exception as exc:  # any refusal is acceptable, a wrong RESULT is not
        failure = exc

    if failure is not None:
        assert "apps.txt" in str(failure), f"refusal must name the destination, got: {failure}"
    else:
        assert dest.is_file(), f"{dest} is still a directory after a file copy"
        assert dest.read_text() == BAKED


def test_missing_parent_directory_is_reported_not_guessed(docker_client, fixture_image, tmp_path: Path):
    """With no parent directory, the copy either creates the file or fails naming the path."""
    dest = tmp_path / "sites" / "apps.txt"

    failure: Exception | None = None
    try:
        host_run_cp(fixture_image, APPS_TXT_IN_IMAGE, str(dest), docker_client)
    except Exception as exc:  # any refusal is acceptable, a wrong RESULT is not
        failure = exc

    if failure is not None:
        assert "apps.txt" in str(failure), f"refusal must name the destination, got: {failure}"
        assert not dest.exists(), "a failed copy must leave nothing at the destination"
    else:
        assert dest.is_file()
        assert dest.read_text() == BAKED


def test_absent_source_raises_and_leaves_nothing(docker_client, fixture_image, tmp_path: Path):
    """A path the image does not carry fails loudly and leaves no half-written destination."""
    dest = tmp_path / "sites" / "apps.txt"
    dest.parent.mkdir(parents=True)

    with pytest.raises(Exception) as caught:
        host_run_cp(fixture_image, ABSENT_IN_IMAGE, str(dest), docker_client)

    assert "Failed to copy" in str(caught.value)
    assert not dest.exists()


def test_failed_copy_reports_the_copy_not_its_own_cleanup(docker_client, fixture_image, tmp_path: Path):
    """When the destination already exists as a FILE, a failed copy still reports the copy.

    The cleanup path deletes the destination with `shutil.rmtree`, which raises
    `NotADirectoryError` on a file and replaces the real message with "Not a directory",
    pointing the reader at the wrong thing entirely.
    """
    dest = tmp_path / "sites" / "apps.txt"
    dest.parent.mkdir(parents=True)
    dest.write_text("stale\n")

    with pytest.raises(Exception) as caught:
        host_run_cp(fixture_image, ABSENT_IN_IMAGE, str(dest), docker_client)

    assert not isinstance(caught.value, NotADirectoryError), f"cleanup masked the real failure: {caught.value!r}"
    assert "Failed to copy" in str(caught.value)
