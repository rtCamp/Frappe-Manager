"""
Contract tests for ambient logging context (logger.ambient + FMLogger + ContextInjectFilter).

Defends:
- set_context/bind/reset scoping semantics (incl. exception safety, nesting)
- thread propagation via ctx_submit (and the loss without it)
- record stamping: fm_ctx token, component + extra_fields merge
- file pipeline: formatter renders context; foreign stdlib records never KeyError
- get_logger(file_level=...) honored on the CACHED logger (the dead-config bug)
- gzip namer attached (backups get .gz names)
"""

import logging
import logging.handlers
from concurrent.futures import ThreadPoolExecutor

from frappe_manager.logger import (
    FMLogger,
    bind,
    ctx_submit,
    current_context,
    get_logger,
    reset_context,
    set_context,
)
from frappe_manager.logger.log import ContextInjectFilter, namer


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _fresh_logger(name: str, with_filter: bool = True) -> tuple[logging.Logger, _Capture]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    capture = _Capture()
    if with_filter:
        capture.addFilter(ContextInjectFilter())
    logger.addHandler(capture)
    return logger, capture


class TestAmbientScoping:
    def setup_method(self):
        reset_context()

    def teardown_method(self):
        reset_context()

    def test_default_context_is_empty(self):
        assert not current_context()

    def test_set_context_merges_and_overrides(self):
        set_context(bench="a", operation="create")
        set_context(bench="b")
        ctx = current_context()
        assert ctx.bench == "b"
        assert ctx.operation == "create"

    def test_reset_clears(self):
        set_context(bench="a")
        reset_context()
        assert not current_context()

    def test_bind_is_scoped_and_nests(self):
        set_context(bench="a")
        with bind(operation="outer"):
            assert current_context().operation == "outer"
            with bind(operation="inner", component="x"):
                assert current_context().operation == "inner"
                assert current_context().bench == "a"
            assert current_context().operation == "outer"
            assert current_context().component is None
        assert current_context().operation is None
        assert current_context().bench == "a"

    def test_bind_restores_on_exception(self):
        try:
            with bind(operation="boom"):
                raise ValueError
        except ValueError:
            pass
        assert current_context().operation is None

    def test_ctx_submit_propagates_to_worker_thread(self):
        set_context(bench="threaded")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert ctx_submit(pool, lambda: current_context().bench).result() == "threaded"
            # plain submit: context does NOT cross by itself -- documents why ctx_submit exists
            assert pool.submit(lambda: current_context().bench).result() is None


class TestRecordStamping:
    def setup_method(self):
        reset_context()

    def teardown_method(self):
        reset_context()

    def test_fm_ctx_token_rendered_from_ambient(self):
        logger, capture = _fresh_logger("test_ambient_stamp")
        set_context(correlation_id="550e8400-aaaa", bench="mybench")
        FMLogger(logger, component="docker").info("hello")
        record = capture.records[0]
        assert record.fm_ctx == " [corr=550e8400] [bench=mybench] [component=docker]"

    def test_extra_fields_ride_the_record(self):
        logger, capture = _fresh_logger("test_ambient_extra")
        FMLogger(logger).info("hello", extra_fields={"domain": "x.io"})
        assert "[domain=x.io]" in capture.records[0].fm_ctx

    def test_empty_context_renders_empty_token(self):
        logger, capture = _fresh_logger("test_ambient_empty")
        logger.info("bare")
        assert capture.records[0].fm_ctx == ""

    def test_foreign_stdlib_record_never_keyerrors_formatter(self):
        _fresh_logger("test_ambient_foreign")
        capture = logging.getLogger("test_ambient_foreign").handlers[0]
        formatter = logging.Formatter("%(levelname)s:%(fm_ctx)s %(message)s")
        set_context(bench="tagged")
        logging.getLogger("test_ambient_foreign").warning("from a lib")
        line = formatter.format(capture.records[0])
        assert line == "WARNING: [bench=tagged] from a lib"


class TestGetLoggerPipeline:
    def test_get_logger_returns_component_adapter(self):
        adapter = get_logger(component="unit")
        assert isinstance(adapter, FMLogger)
        assert adapter.component == "unit"

    def test_file_level_honored_on_cached_logger(self, tmp_path):
        from frappe_manager.logger import log as log_mod

        log_mod.get_logger(log_dir=tmp_path, log_file_name="flvl")
        logger = log_mod.get_logger(log_dir=tmp_path, log_file_name="flvl", file_level="ERROR")
        [handler] = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert handler.level == logging.ERROR
        log_mod.loggers.pop("flvl", None)

    def test_gzip_namer_attached_and_appends_gz(self, tmp_path):
        from frappe_manager.logger import log as log_mod

        logger = log_mod.get_logger(log_dir=tmp_path, log_file_name="rot")
        [handler] = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert handler.namer is namer
        assert namer("rot.log.1") == "rot.log.1.gz"
        log_mod.loggers.pop("rot", None)

    def test_invalid_level_raises_configuration_error(self, tmp_path):
        import pytest

        from frappe_manager.exceptions import ConfigurationError
        from frappe_manager.logger import log as log_mod

        log_mod.get_logger(log_dir=tmp_path, log_file_name="badlvl")
        with pytest.raises(ConfigurationError):
            log_mod.get_logger(log_dir=tmp_path, log_file_name="badlvl", file_level="TRACE")
        log_mod.loggers.pop("badlvl", None)
