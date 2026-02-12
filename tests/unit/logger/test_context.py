"""
Tests for LoggerContext.

This module tests the LoggerContext dataclass that provides structured
context information for logging (bench name, operation, component).
"""


from frappe_manager.logger.context import LoggerContext


class TestLoggerContextCreation:
    """Test creation and initialization of LoggerContext."""

    def test_create_empty_context(self):
        """Test creating an empty LoggerContext with no fields."""
        ctx = LoggerContext()

        assert ctx.bench is None
        assert ctx.operation is None
        assert ctx.component is None
        assert ctx.extra == {}

    def test_create_with_bench_only(self):
        """Test creating context with only bench name."""
        ctx = LoggerContext(bench="mybench")

        assert ctx.bench == "mybench"
        assert ctx.operation is None
        assert ctx.component is None

    def test_create_with_operation_only(self):
        """Test creating context with only operation."""
        ctx = LoggerContext(operation="create")

        assert ctx.bench is None
        assert ctx.operation == "create"
        assert ctx.component is None

    def test_create_with_component_only(self):
        """Test creating context with only component."""
        ctx = LoggerContext(component="docker")

        assert ctx.bench is None
        assert ctx.operation is None
        assert ctx.component == "docker"

    def test_create_with_all_fields(self):
        """Test creating context with all standard fields."""
        ctx = LoggerContext(bench="mybench", operation="create", component="docker")

        assert ctx.bench == "mybench"
        assert ctx.operation == "create"
        assert ctx.component == "docker"

    def test_create_with_extra_fields(self):
        """Test creating context with extra custom fields."""
        ctx = LoggerContext(bench="mybench", extra={"user": "admin", "step": 1})

        assert ctx.bench == "mybench"
        assert ctx.extra["user"] == "admin"
        assert ctx.extra["step"] == 1


class TestLoggerContextFormatting:
    """Test formatting of LoggerContext as string prefixes."""

    def test_empty_context_formats_to_empty_string(self):
        """Test that empty context formats to empty string."""
        ctx = LoggerContext()
        assert ctx.format() == ""

    def test_bench_only_formats_correctly(self):
        """Test formatting with only bench name."""
        ctx = LoggerContext(bench="mybench")
        assert ctx.format() == "[bench=mybench]"

    def test_operation_only_formats_correctly(self):
        """Test formatting with only operation."""
        ctx = LoggerContext(operation="create")
        assert ctx.format() == "[op=create]"

    def test_component_only_formats_correctly(self):
        """Test formatting with only component."""
        ctx = LoggerContext(component="docker")
        assert ctx.format() == "[component=docker]"

    def test_bench_and_operation_formats_correctly(self):
        """Test formatting with bench and operation."""
        ctx = LoggerContext(bench="mybench", operation="create")
        assert ctx.format() == "[bench=mybench] [op=create]"

    def test_full_context_formats_correctly(self):
        """Test formatting with all standard fields."""
        ctx = LoggerContext(bench="mybench", operation="create", component="docker")
        expected = "[bench=mybench] [op=create] [component=docker]"
        assert ctx.format() == expected

    def test_extra_fields_included_in_format(self):
        """Test that extra fields are included in formatted output."""
        ctx = LoggerContext(bench="mybench", extra={"user": "admin", "step": 1})

        formatted = ctx.format()
        assert "bench=mybench" in formatted
        assert "user=admin" in formatted
        assert "step=1" in formatted

    def test_extra_fields_with_none_value_excluded(self):
        """Test that extra fields with None values are excluded."""
        ctx = LoggerContext(bench="mybench", extra={"user": "admin", "empty": None})

        formatted = ctx.format()
        assert "user=admin" in formatted
        assert "empty" not in formatted

    def test_format_order_is_consistent(self):
        """Test that format order is bench, operation, component, then extra."""
        ctx = LoggerContext(bench="mybench", operation="create", component="docker", extra={"step": 1})

        formatted = ctx.format()
        # Check order by finding indices
        bench_idx = formatted.find("bench=")
        op_idx = formatted.find("op=")
        component_idx = formatted.find("component=")
        step_idx = formatted.find("step=")

        assert bench_idx < op_idx < component_idx < step_idx


class TestLoggerContextChildCreation:
    """Test creating child contexts with inherited values."""

    def test_child_inherits_all_parent_values(self):
        """Test that child context inherits all parent values."""
        parent = LoggerContext(bench="mybench", operation="create", component="docker")
        child = parent.child()

        assert child.bench == "mybench"
        assert child.operation == "create"
        assert child.component == "docker"

    def test_child_can_override_bench(self):
        """Test that child can override parent's bench value."""
        parent = LoggerContext(bench="mybench", operation="create")
        child = parent.child(bench="otherbench")

        assert child.bench == "otherbench"
        assert child.operation == "create"  # Inherited

    def test_child_can_override_operation(self):
        """Test that child can override parent's operation value."""
        parent = LoggerContext(bench="mybench", operation="create")
        child = parent.child(operation="update")

        assert child.bench == "mybench"  # Inherited
        assert child.operation == "update"

    def test_child_can_override_component(self):
        """Test that child can override parent's component value."""
        parent = LoggerContext(bench="mybench", component="docker")
        child = parent.child(component="database")

        assert child.bench == "mybench"  # Inherited
        assert child.component == "database"

    def test_child_can_add_component(self):
        """Test that child can add component to parent without one."""
        parent = LoggerContext(bench="mybench", operation="create")
        child = parent.child(component="docker")

        assert child.bench == "mybench"
        assert child.operation == "create"
        assert child.component == "docker"

    def test_child_can_override_multiple_fields(self):
        """Test that child can override multiple fields at once."""
        parent = LoggerContext(bench="mybench", operation="create")
        child = parent.child(operation="update", component="docker")

        assert child.bench == "mybench"  # Inherited
        assert child.operation == "update"  # Overridden
        assert child.component == "docker"  # Added

    def test_child_inherits_extra_fields(self):
        """Test that child inherits parent's extra fields."""
        parent = LoggerContext(bench="mybench", extra={"user": "admin", "step": 1})
        child = parent.child(component="docker")

        assert child.extra["user"] == "admin"
        assert child.extra["step"] == 1

    def test_child_can_add_extra_fields(self):
        """Test that child can add new extra fields."""
        parent = LoggerContext(bench="mybench", extra={"user": "admin"})
        child = parent.child(extra={"step": 2})

        assert child.extra["user"] == "admin"  # Inherited
        assert child.extra["step"] == 2  # Added

    def test_child_can_override_extra_fields(self):
        """Test that child can override parent's extra fields."""
        parent = LoggerContext(bench="mybench", extra={"user": "admin", "step": 1})
        child = parent.child(extra={"step": 2, "phase": "build"})

        assert child.extra["user"] == "admin"  # Inherited
        assert child.extra["step"] == 2  # Overridden
        assert child.extra["phase"] == "build"  # Added

    def test_grandchild_inherits_from_parent_chain(self):
        """Test that grandchild inherits through multiple generations."""
        parent = LoggerContext(bench="mybench")
        child = parent.child(operation="create")
        grandchild = child.child(component="docker")

        assert grandchild.bench == "mybench"
        assert grandchild.operation == "create"
        assert grandchild.component == "docker"

    def test_parent_unchanged_after_child_creation(self):
        """Test that parent context is not modified when creating child."""
        parent = LoggerContext(bench="mybench", operation="create")
        original_bench = parent.bench
        original_operation = parent.operation

        child = parent.child(operation="update", component="docker")

        assert parent.bench == original_bench
        assert parent.operation == original_operation
        assert parent.component is None  # Not affected by child


class TestLoggerContextDictConversion:
    """Test conversion of LoggerContext to dictionary."""

    def test_empty_context_to_dict(self):
        """Test converting empty context to dictionary."""
        ctx = LoggerContext()
        d = ctx.to_dict()

        assert d["bench"] is None
        assert d["operation"] is None
        assert d["component"] is None
        assert d["correlation_id"] is None
        assert len(d) == 4

    def test_partial_context_to_dict(self):
        """Test converting context with some fields to dictionary."""
        ctx = LoggerContext(bench="mybench", operation="create")
        d = ctx.to_dict()

        assert d["bench"] == "mybench"
        assert d["operation"] == "create"
        assert d["component"] is None

    def test_full_context_to_dict(self):
        """Test converting full context to dictionary."""
        ctx = LoggerContext(bench="mybench", operation="create", component="docker")
        d = ctx.to_dict()

        assert d["bench"] == "mybench"
        assert d["operation"] == "create"
        assert d["component"] == "docker"

    def test_context_with_extra_fields_to_dict(self):
        """Test that extra fields are included in dictionary."""
        ctx = LoggerContext(bench="mybench", extra={"user": "admin", "step": 1})
        d = ctx.to_dict()

        assert d["bench"] == "mybench"
        assert d["user"] == "admin"
        assert d["step"] == 1

    def test_to_dict_includes_none_values(self):
        """Test that to_dict includes None values (unlike format)."""
        ctx = LoggerContext(bench="mybench")
        d = ctx.to_dict()

        assert "operation" in d
        assert d["operation"] is None
        assert "component" in d
        assert d["component"] is None


class TestLoggerContextBooleanEvaluation:
    """Test boolean evaluation of LoggerContext."""

    def test_empty_context_evaluates_to_false(self):
        """Test that empty context evaluates to False."""
        ctx = LoggerContext()
        assert not ctx
        assert bool(ctx) is False

    def test_context_with_bench_evaluates_to_true(self):
        """Test that context with bench evaluates to True."""
        ctx = LoggerContext(bench="mybench")
        assert ctx
        assert bool(ctx) is True

    def test_context_with_operation_evaluates_to_true(self):
        """Test that context with operation evaluates to True."""
        ctx = LoggerContext(operation="create")
        assert ctx
        assert bool(ctx) is True

    def test_context_with_component_evaluates_to_true(self):
        """Test that context with component evaluates to True."""
        ctx = LoggerContext(component="docker")
        assert ctx
        assert bool(ctx) is True

    def test_context_with_extra_evaluates_to_true(self):
        """Test that context with extra fields evaluates to True."""
        ctx = LoggerContext(extra={"user": "admin"})
        assert ctx
        assert bool(ctx) is True

    def test_context_with_any_field_evaluates_to_true(self):
        """Test that context with any non-None field evaluates to True."""
        ctx = LoggerContext(bench="mybench", operation="create", component="docker")
        assert ctx
        assert bool(ctx) is True


class TestLoggerContextEdgeCases:
    """Test edge cases and special scenarios."""

    def test_context_with_empty_string_values(self):
        """Test context with empty string values."""
        ctx = LoggerContext(bench="", operation="create")

        # Empty string should not be included in format
        formatted = ctx.format()
        assert "bench=" not in formatted
        assert "op=create" in formatted

    def test_context_with_special_characters(self):
        """Test context with special characters in values."""
        ctx = LoggerContext(bench="my-bench_123", operation="create/update", component="docker.compose")

        formatted = ctx.format()
        assert "bench=my-bench_123" in formatted
        assert "op=create/update" in formatted
        assert "component=docker.compose" in formatted

    def test_context_with_unicode_values(self):
        """Test context with unicode characters."""
        ctx = LoggerContext(bench="mybench", extra={"user": "admin™", "note": "测试"})

        formatted = ctx.format()
        assert "user=admin™" in formatted
        assert "note=测试" in formatted

    def test_extra_field_overwrites_during_child_creation(self):
        """Test that extra field keys are properly overwritten in child."""
        parent = LoggerContext(extra={"key": "value1"})
        child = parent.child(extra={"key": "value2"})

        assert child.extra["key"] == "value2"

    def test_multiple_children_dont_affect_each_other(self):
        """Test that multiple children from same parent are independent."""
        parent = LoggerContext(bench="mybench")
        child1 = parent.child(operation="create")
        child2 = parent.child(operation="delete")

        assert child1.operation == "create"
        assert child2.operation == "delete"
        assert parent.operation is None

    def test_context_with_numeric_values_in_extra(self):
        """Test context with various numeric types in extra fields."""
        ctx = LoggerContext(extra={"int_val": 42, "float_val": 3.14, "bool_val": True})

        formatted = ctx.format()
        assert "int_val=42" in formatted
        assert "float_val=3.14" in formatted
        assert "bool_val=True" in formatted
