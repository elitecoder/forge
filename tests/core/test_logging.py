# Copyright 2026. Tests for logging utilities — activity log, atomic write, StatusWriter.

import os
import threading
import time
from pathlib import Path

from forge.core.logging import utc_timestamp, log_activity, atomic_write_file, StatusWriter


class TestUtcTimestamp:
    def test_format(self):
        ts = utc_timestamp()
        assert len(ts) == 8  # HH:MM:SS
        assert ts[2] == ":"
        assert ts[5] == ":"


class TestLogActivity:
    def test_appends_to_file(self, tmp_path):
        log = str(tmp_path / "activity.log")
        log_activity(log, "step", "first message")
        log_activity(log, "step", "second message")
        content = Path(log).read_text()
        assert "first message" in content
        assert "second message" in content
        assert content.count("\n") == 2

    def test_creates_file(self, tmp_path):
        log = str(tmp_path / "new.log")
        log_activity(log, "src", "hello")
        assert Path(log).is_file()

    def test_empty_source(self, tmp_path):
        log = str(tmp_path / "log")
        log_activity(log, "", "msg")
        content = Path(log).read_text()
        assert "] msg\n" in content

    def test_with_source(self, tmp_path):
        log = str(tmp_path / "log")
        log_activity(log, "code", "msg")
        content = Path(log).read_text()
        assert "code  msg" in content

    def test_empty_path_is_noop(self):
        log_activity("", "src", "msg")  # Should not raise

    def test_handles_os_error(self, tmp_path):
        log_activity("/nonexistent/dir/log.txt", "src", "msg")  # Should not raise


class TestAtomicWriteFile:
    def test_writes_content(self, tmp_path):
        path = str(tmp_path / "out.txt")
        atomic_write_file(path, "hello world")
        assert Path(path).read_text() == "hello world"

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / "out.txt")
        Path(path).write_text("old")
        atomic_write_file(path, "new")
        assert Path(path).read_text() == "new"

    def test_no_temp_files_left(self, tmp_path):
        path = str(tmp_path / "out.txt")
        atomic_write_file(path, "data")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


class TestStatusWriter:
    def test_start_stop(self):
        calls = []
        writer = StatusWriter(lambda: calls.append(1), interval=0.05)
        writer.start()
        time.sleep(0.15)
        writer.stop()
        assert len(calls) >= 1

    def test_write_now(self):
        calls = []
        writer = StatusWriter(lambda: calls.append(1))
        writer.write_now()
        assert len(calls) == 1

    def test_stop_without_start(self):
        writer = StatusWriter(lambda: None)
        writer.stop()  # Should not raise

    def test_double_stop(self):
        writer = StatusWriter(lambda: None, interval=0.05)
        writer.start()
        writer.stop()
        writer.stop()  # Should not raise

    def test_exception_in_write_fn(self):
        def bad_fn():
            raise RuntimeError("boom")
        writer = StatusWriter(bad_fn, interval=0.05)
        writer.start()
        time.sleep(0.15)
        writer.stop()  # Should not raise — exceptions are swallowed

    def test_write_now_exception(self):
        def bad_fn():
            raise RuntimeError("boom")
        writer = StatusWriter(bad_fn)
        writer.write_now()  # Should not raise
