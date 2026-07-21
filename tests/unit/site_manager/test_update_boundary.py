"""Unit tests for the image-mode update boundary predicate."""

from frappe_manager.commands.update import is_immutable_update_request


def test_no_runtime_change_is_allowed():
    assert is_immutable_update_request(python_version=None, node_version=None) is False


def test_python_change_is_immutable():
    assert is_immutable_update_request(python_version="3.11", node_version=None) is True


def test_node_change_is_immutable():
    assert is_immutable_update_request(python_version=None, node_version="20") is True


def test_both_changes_are_immutable():
    assert is_immutable_update_request(python_version="3.11", node_version="20") is True
