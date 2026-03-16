# Copyright 2026. Tests for LockedStateManager — atomic file-backed state with fcntl locking.

import json
import threading
from pathlib import Path

from forge.core.state import LockedStateManager


def _make_mgr(tmp_path: Path) -> LockedStateManager[dict]:
    return LockedStateManager(
        state_file=tmp_path / "state.json",
        serialize=lambda d: d,
        deserialize=lambda d: d,
    )


class TestExists:
    def test_false_when_no_file(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        assert not mgr.exists()

    def test_true_after_save(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"x": 1})
        assert mgr.exists()


class TestSaveAndLoad:
    def test_round_trip(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"key": "value", "n": 42})
        loaded = mgr.load()
        assert loaded == {"key": "value", "n": 42}

    def test_overwrite(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"v": 1})
        mgr.save({"v": 2})
        assert mgr.load() == {"v": 2}

    def test_json_formatting(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"a": 1})
        raw = (tmp_path / "state.json").read_text()
        assert raw.endswith("\n")
        parsed = json.loads(raw)
        assert parsed == {"a": 1}

    def test_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "state.json"
        mgr = LockedStateManager(deep, serialize=lambda d: d, deserialize=lambda d: d)
        mgr.save({"deep": True})
        assert mgr.load() == {"deep": True}

    def test_lock_file_created(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"x": 1})
        mgr.load()
        assert (tmp_path / "state.json.lock").exists()


class TestUpdate:
    def test_mutator_applied(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"count": 0})

        def inc(d):
            d["count"] += 1

        result = mgr.update(inc)
        assert result["count"] == 1
        assert mgr.load()["count"] == 1

    def test_multiple_updates(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"count": 0})
        for _ in range(5):
            mgr.update(lambda d: d.__setitem__("count", d["count"] + 1))
        assert mgr.load()["count"] == 5

    def test_concurrent_updates(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"count": 0})
        barrier = threading.Barrier(4)

        def inc_many():
            barrier.wait()
            for _ in range(10):
                mgr.update(lambda d: d.__setitem__("count", d["count"] + 1))

        threads = [threading.Thread(target=inc_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert mgr.load()["count"] == 40


class TestAtomicWrite:
    def test_no_temp_files_left(self, tmp_path):
        mgr = _make_mgr(tmp_path)
        mgr.save({"x": 1})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_error_cleans_up_temp(self, tmp_path):
        def bad_serialize(d):
            raise RuntimeError("boom")

        mgr = LockedStateManager(
            tmp_path / "state.json",
            serialize=bad_serialize,
            deserialize=lambda d: d,
        )
        try:
            mgr.save({"x": 1})
        except RuntimeError:
            pass
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


class TestCustomSerializers:
    def test_custom_round_trip(self, tmp_path):
        class MyState:
            def __init__(self, name="", count=0):
                self.name = name
                self.count = count

        mgr = LockedStateManager(
            tmp_path / "state.json",
            serialize=lambda s: {"name": s.name, "count": s.count},
            deserialize=lambda d: MyState(d["name"], d["count"]),
        )
        mgr.save(MyState("test", 42))
        loaded = mgr.load()
        assert loaded.name == "test"
        assert loaded.count == 42
