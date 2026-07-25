"""
Integration tests for ambient contextual logging.

End-to-end: context set via set_context/bind flows through the real file
pipeline (ContextInjectFilter + %(fm_ctx)s formatter) onto every record --
business logs, LoggingOutputHandler mirror lines, worker threads, and bare
stdlib loggers alike.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from frappe_manager.logger import (
    bind,
    ctx_submit,
    get_logger,
    reset_context,
    set_context,
)
from frappe_manager.logger.log import ContextInjectFilter, FMLogger
from frappe_manager.output_manager.logging_output import LoggingOutputHandler
from frappe_manager.output_manager.silent_output import SilentOutputHandler

FILE_FORMAT = "[%(asctime)s] %(levelname)s:%(fm_ctx)s %(message)s"


@pytest.fixture(autouse=True)
def clean_context():
    reset_context()
    yield
    reset_context()


@pytest.fixture
def file_logger(tmp_path):
    """A real logger wired exactly like the fm file pipeline."""
    logger = logging.getLogger("test_ambient_integration")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    log_file = tmp_path / "test.log"
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    handler.addFilter(ContextInjectFilter())
    logger.addHandler(handler)

    yield logger, log_file
    logger.handlers.clear()


class TestAmbientFlowToFile:
    def test_context_flows_to_every_record(self, file_logger):
        logger, log_file = file_logger
        set_context(bench="mybench", operation="create")

        fm = FMLogger(logger, component="docker")
        fm.info("Starting operation")
        fm.info("Processing step 1")
        fm.info("Processing step 2")

        contents = log_file.read_text()
        assert contents.count("[bench=mybench]") == 3
        assert contents.count("[op=create]") == 3
        assert contents.count("[component=docker]") == 3

    def test_bind_switches_context_between_benches(self, file_logger):
        logger, log_file = file_logger
        fm = FMLogger(logger)

        with bind(bench="bench1", operation="start"):
            fm.info("starting bench1")
        with bind(bench="bench2", operation="stop"):
            fm.info("stopping bench2")

        lines = log_file.read_text().splitlines()
        assert "[bench=bench1] [op=start]" in lines[0]
        assert "[bench=bench2] [op=stop]" in lines[1]

    def test_bare_stdlib_logger_gets_ambient_tags(self, file_logger):
        logger, log_file = file_logger
        set_context(correlation_id="cafebabe-0000", bench="tagged")

        logger.warning("raw stdlib record")  # no FMLogger involved

        contents = log_file.read_text()
        assert "[corr=cafebabe]" in contents
        assert "[bench=tagged]" in contents

    def test_worker_thread_records_keep_context(self, file_logger):
        logger, log_file = file_logger
        fm = FMLogger(logger, component="app_cloner")
        set_context(bench="threaded", correlation_id="12345678-x")

        def clone():
            fm.info("cloning in worker")

        with ThreadPoolExecutor(max_workers=1) as pool:
            ctx_submit(pool, clone).result()

        contents = log_file.read_text()
        assert "[corr=12345678] [bench=threaded] [component=app_cloner]" in contents


class TestOutputMirrorAmbient:
    def test_mirror_lines_carry_ambient_context(self, file_logger, tmp_path):
        logger, log_file = file_logger
        set_context(correlation_id="deadbeef-1", bench="mybench", operation="delete")

        handler = LoggingOutputHandler(SilentOutputHandler())
        handler.logger = FMLogger(logger, component="output")

        handler.print("Removing volumes")
        handler.warning("Volume not found")

        contents = log_file.read_text()
        assert "[OUTPUT] Removing volumes" in contents
        assert contents.count("[corr=deadbeef] [bench=mybench] [op=delete] [component=output]") == 2
        assert "WARNING:" in contents

    def test_extra_fields_merge_with_ambient(self, file_logger):
        logger, log_file = file_logger
        set_context(bench="mybench")

        FMLogger(logger, component="ssl_manager").info(
            "Issued certificate",
            extra_fields={"domain": "example.com"},
        )

        contents = log_file.read_text()
        assert "[bench=mybench] [component=ssl_manager] [domain=example.com]" in contents


class TestPublicApiSingleton:
    def test_get_logger_component_lands_in_fm_log(self, tmp_path):
        from frappe_manager.logger import log as log_mod

        raw = log_mod.get_logger(log_dir=tmp_path, log_file_name="ambient_e2e")
        try:
            set_context(bench="api-bench", correlation_id="abcd1234-z")
            FMLogger(raw, component="bench_service").info("via public api shape")

            contents = (tmp_path / "ambient_e2e.log").read_text()
            assert "[corr=abcd1234] [bench=api-bench] [component=bench_service] via public api shape" in contents
        finally:
            log_mod.loggers.pop("ambient_e2e", None)

    def test_get_logger_returns_adapter_over_singleton(self):
        a = get_logger(component="x")
        b = get_logger(component="y")
        assert a.logger is b.logger  # one underlying "fm" logger
        assert (a.component, b.component) == ("x", "y")


class TestPrintDataMirror:
    def test_rich_renderable_logs_text_not_repr(self, file_logger):
        from rich.console import Group
        from rich.text import Text

        logger, log_file = file_logger
        handler = LoggingOutputHandler(SilentOutputHandler())
        handler.logger = FMLogger(logger, component="output")

        handler.print_data(Group(Text("fm.alok.rt.gw", style="fm.ok"), Text("apps frappe")))

        contents = log_file.read_text()
        assert "<rich.console.Group object" not in contents
        assert "fm.alok.rt.gw" in contents
        assert "apps frappe" in contents

    def test_plain_data_logged_verbatim(self, file_logger):
        logger, log_file = file_logger
        handler = LoggingOutputHandler(SilentOutputHandler())
        handler.logger = FMLogger(logger, component="output")

        handler.print_data({"status": "ok"})

        assert "DATA: {'status': 'ok'}" in log_file.read_text()
