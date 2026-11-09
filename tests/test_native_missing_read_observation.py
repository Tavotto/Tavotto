"""Only the same uncaught, read-only open failure can support native recovery."""

from __future__ import annotations

import gc
import io
import json
import os
import weakref
from pathlib import Path

import pytest

from tavotto.engine import figcapture, nativesession


@pytest.mark.parametrize("kind", ["open", "io", "path"])
def test_uncaught_missing_read_keeps_the_actual_exception(kind, tmp_path):
    missing = tmp_path / "missing" / "data.csv"
    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            if kind == "open":
                open(str(missing), encoding="utf-8")
            elif kind == "io":
                io.open(str(missing), encoding="utf-8")
            else:
                Path(missing).read_text(encoding="utf-8")
        fact = observer.uncaught_missing_read(failure.value)
        assert fact is not None and fact["requested"] == str(missing)
        assert observer.uncaught_missing_read(failure.value) == fact
    finally:
        observer.uninstall()


def test_a_caught_read_followed_by_a_failed_write_to_the_same_path_is_not_a_read(tmp_path):
    missing = tmp_path / "missing" / "data.csv"
    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError):
            open(missing, encoding="utf-8")
        with pytest.raises(FileNotFoundError) as failure:
            open(missing, "w", encoding="utf-8")
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()


def test_nested_custom_opener_identifies_the_originating_read(tmp_path):
    data = tmp_path / "missing" / "data.csv"
    key = tmp_path / "missing" / "key.csv"
    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:

        def opener(_path, _flags):
            return open(key, encoding="utf-8")

        with pytest.raises(FileNotFoundError) as failure:
            open(data, encoding="utf-8", opener=opener)
        fact = observer.uncaught_missing_read(failure.value)
        assert fact is not None and fact["requested"] == str(key)
    finally:
        observer.uninstall()


def test_nested_opener_beyond_the_trace_limit_is_unknown(tmp_path):
    def deep_read(depth):
        if depth:
            return deep_read(depth - 1)
        return open(tmp_path / "key.csv", encoding="utf-8")

    def opener(_path, _flags):
        return deep_read(300)

    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            open(tmp_path / "data.csv", encoding="utf-8", opener=opener)
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()


@pytest.mark.parametrize("kind", ["observed_write", "unobserved_open", "no_filename"])
def test_an_opener_cannot_attribute_a_different_failure_to_its_outer_read(kind, tmp_path):
    data = tmp_path / "missing" / "data.csv"
    key = tmp_path / "missing" / "key.csv"

    def opener(_path, _flags):
        if kind == "observed_write":
            return open(key, "w", encoding="utf-8")
        if kind == "unobserved_open":
            return os.open(key, os.O_RDONLY)
        raise FileNotFoundError("custom opener")

    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            open(data, encoding="utf-8", opener=opener)
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()


def test_failure_observation_is_opt_in(tmp_path):
    missing = tmp_path / "missing"
    observer = figcapture.InputObserver(str(tmp_path))
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            open(missing, encoding="utf-8")
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()


def test_observation_of_an_exception_traceback_is_bounded(tmp_path):
    def deep_read(depth):
        if depth:
            return deep_read(depth - 1)
        return open(tmp_path / "missing.csv", encoding="utf-8")

    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            deep_read(300)
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()


def test_unreadable_custom_traceback_does_not_replace_the_original_failure(tmp_path):
    class CustomMissing(FileNotFoundError):
        def __getattribute__(self, name):
            if name == "__traceback__":
                raise RuntimeError("custom exception")
            return super().__getattribute__(name)

    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    assert observer.uncaught_missing_read(CustomMissing()) is None


def test_cwd_is_the_one_at_the_failed_read_even_after_a_caught_reraise(tmp_path, monkeypatch):
    start = tmp_path / "start"
    later = tmp_path / "later"
    start.mkdir()
    later.mkdir()
    monkeypatch.chdir(start)
    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            try:
                open(start / "missing.csv", encoding="utf-8")
            except FileNotFoundError:
                monkeypatch.chdir(later)
                raise
        fact = observer.uncaught_missing_read(failure.value)
        assert fact is not None and fact["cwd"] == str(start)
    finally:
        observer.uninstall()


def test_private_read_fact_is_not_published_with_native_state_or_events():
    session = nativesession.NativeSession(session_id="private", descriptor={})
    fact = {"requested": "/private-read-canary/data.csv", "cwd": "/private-read-canary"}
    session._on_frame(
        {
            nativesession.EVENT_KEY: "barrier",
            "script_error": {"code": "script_error", "exception_category": "file_not_found"},
            "missing_read": fact,
        }
    )
    assert "private-read-canary" not in json.dumps(session.public_state())
    assert "private-read-canary" not in json.dumps(list(session.events))
    assert session.take_missing_read() == fact
    assert session.take_missing_read() is None


def test_a_caught_missing_read_does_not_keep_the_callers_objects_alive(tmp_path):
    class Payload:
        pass

    def caught_read():
        payload = Payload()
        ref = weakref.ref(payload)
        try:
            open(tmp_path / "missing.csv", encoding="utf-8")
        except FileNotFoundError:
            pass
        return ref

    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        ref = caught_read()
        gc.collect()
        assert ref() is None
    finally:
        observer.uncaught_missing_read(None)
        observer.uninstall()
