"""The rotating log file (#211): writes into the given folder, rotates, and a
test never leaves a handler on the root logger."""

from __future__ import annotations

import io
import logging
import os
import sys

import pytest

import logging_setup


def test_setup_writes_into_logs_and_detaches(tmp_path):
    logger = logging.getLogger("lb-test-log-setup")
    logger.propagate = False
    log_dir = logging_setup.log_dir_for(str(tmp_path))
    assert log_dir == os.path.join(str(tmp_path), "logs")
    handler = logging_setup.setup_file_logging(log_dir, logger=logger)
    try:
        logger.info("hello from the test")
        handler.flush()
        text = open(os.path.join(log_dir, logging_setup.LOG_FILE_NAME), encoding="utf-8").read()
        assert "hello from the test" in text
        # Idempotent per folder: no second handler for the same file.
        assert logging_setup.setup_file_logging(log_dir, logger=logger) is handler
        assert logger.handlers.count(handler) == 1
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert logger.handlers == []


def test_handler_rotates(tmp_path):
    handler = logging_setup.build_file_handler(str(tmp_path / "logs"), max_bytes=400, backups=2)
    logger = logging.getLogger("lb-test-log-rotate")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for i in range(60):
            logger.info("line %03d %s", i, "x" * 40)
    finally:
        logger.removeHandler(handler)
        handler.close()
    names = sorted(os.listdir(tmp_path / "logs"))
    assert names == ["localbib.log", "localbib.log.1", "localbib.log.2"]


def test_unwritable_folder_does_not_stop_the_app(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    logger = logging.getLogger("lb-test-log-fail")
    logger.propagate = False
    assert logging_setup.setup_file_logging(str(blocker / "logs"), logger=logger) is None
    assert logger.handlers == []


def test_console_handler_stays_at_warning():
    assert logging_setup.console_handler().level == logging.WARNING


def test_url_loggers_stay_out_of_info():
    # httpx logs every request URL on INFO - OpenAlex's ``api_key`` included.
    httpx_logger = logging.getLogger("httpx")
    before = httpx_logger.level
    try:
        logging_setup.quiet_url_loggers()
        assert not httpx_logger.isEnabledFor(logging.INFO)
        assert httpx_logger.isEnabledFor(logging.WARNING)
    finally:
        httpx_logger.setLevel(before)


# --- the console-less .exe (#214): stdout/stderr into the same file ----------

@pytest.fixture
def root_logger():
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield root
    for handler in root.handlers:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_missing_std_streams_write_into_the_log_file(tmp_path, monkeypatch, root_logger):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    handler = logging_setup.redirect_missing_std_streams(str(tmp_path / "logs"))
    assert handler is not None
    assert sys.stdout.isatty() is False  # what uvicorn asks before anything else
    print("hello from print")
    sys.stderr.write("Traceback line one\nline two")
    sys.stderr.flush()
    handler.flush()
    text = (tmp_path / "logs" / logging_setup.LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "[INFO] stdout: hello from print" in text
    assert "[WARNING] stderr: Traceback line one" in text
    assert "[WARNING] stderr: line two" in text
    # One handler owns the file - a second open handle would break rotation.
    assert logging_setup.redirect_missing_std_streams(str(tmp_path / "logs")) is handler


def test_an_existing_stream_is_left_alone(tmp_path, monkeypatch, root_logger):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(sys, "stderr", None)
    logging_setup.redirect_missing_std_streams(str(tmp_path / "logs"))
    assert sys.stdout is console
    assert isinstance(sys.stderr, logging_setup.LogStream)


def test_without_a_writable_folder_the_streams_become_a_sink(tmp_path, monkeypatch, root_logger):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert logging_setup.redirect_missing_std_streams(str(blocker / "logs")) is None
    try:
        print("goes nowhere, fails nothing")
        assert sys.stdout is not None and sys.stderr is not None
    finally:
        sys.stdout.close()
        sys.stderr.close()
