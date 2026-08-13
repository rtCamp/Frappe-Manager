"""Reading the active Python/Node version out of a bench's toolchain symlinks.

``read_bench_python_version`` splits the uv symlink target
(``cpython-3.12.9-linux-x86_64-gnu``) on ``-`` and returns element ``[1]`` — but ONLY when the
target actually has more than one element. The ``len(parts) > 1`` guard is the boundary between
"there is a version field" and "there is not"; the equality case (a single-element target such as
a bare ``cpython`` or a plain directory name) has no version to report and must come back as
None, because callers render this straight into `fm info` / `fm bench versions`.
"""

from frappe_manager.utils.site import read_bench_node_version, read_bench_python_version


def _uv_symlink(bench, target: str):
    link_dir = bench / ".uv"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "python-default").symlink_to(target)


def _fnm_symlink(bench, target: str):
    link_dir = bench / ".fnm" / "aliases"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "default").symlink_to(target)


class TestReadBenchPythonVersion:
    def test_returns_the_version_field_of_a_full_uv_target(self, tmp_path):
        _uv_symlink(tmp_path, "cpython-3.12.9-linux-x86_64-gnu")

        assert read_bench_python_version(tmp_path) == "3.12.9"

    def test_two_element_target_is_the_smallest_readable_case(self, tmp_path):
        """`len(parts) == 2` is on the reporting side of the boundary."""
        _uv_symlink(tmp_path, "cpython-3.13")

        assert read_bench_python_version(tmp_path) == "3.13"

    def test_single_element_target_has_no_version_field(self, tmp_path):
        """The equality case: `len(parts) == 1`, so there is no `parts[1]` to report."""
        _uv_symlink(tmp_path, "cpython")

        assert read_bench_python_version(tmp_path) is None

    def test_empty_target_has_no_version_field(self, tmp_path):
        _uv_symlink(tmp_path, "-3.12.9")

        # A leading separator makes parts[0] empty but parts[1] real; still the version field.
        assert read_bench_python_version(tmp_path) == "3.12.9"

    def test_missing_symlink_returns_none(self, tmp_path):
        assert read_bench_python_version(tmp_path) is None

    def test_regular_file_instead_of_symlink_returns_none(self, tmp_path):
        link_dir = tmp_path / ".uv"
        link_dir.mkdir()
        (link_dir / "python-default").write_text("not a symlink")

        assert read_bench_python_version(tmp_path) is None


class TestReadBenchNodeVersion:
    def test_returns_the_v_prefixed_component(self, tmp_path):
        _fnm_symlink(tmp_path, "../node-versions/v22.11.0/installation")

        assert read_bench_node_version(tmp_path) == "v22.11.0"

    def test_target_without_a_v_component_returns_none(self, tmp_path):
        _fnm_symlink(tmp_path, "../node-versions/current/installation")

        assert read_bench_node_version(tmp_path) is None

    def test_missing_symlink_returns_none(self, tmp_path):
        assert read_bench_node_version(tmp_path) is None
