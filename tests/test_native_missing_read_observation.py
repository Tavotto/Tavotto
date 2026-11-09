"""Only the same uncaught, read-only open failure can support native recovery."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tavotto.engine import figcapture


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
        assert observer.uncaught_missing_read(failure.value) is None
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


def test_failure_observation_is_opt_in_and_bounded(tmp_path):
    missing = tmp_path / "missing"
    observer = figcapture.InputObserver(str(tmp_path))
    observer.install()
    try:
        with pytest.raises(FileNotFoundError) as failure:
            open(missing, encoding="utf-8")
        assert observer.uncaught_missing_read(failure.value) is None
    finally:
        observer.uninstall()
    observer = figcapture.InputObserver(str(tmp_path), observe_failed_reads=True)
    observer.install()
    try:
        failures = []
        for i in range(20):
            with pytest.raises(FileNotFoundError) as failure:
                open(tmp_path / str(i) / "data", encoding="utf-8")
            failures.append(failure.value)
        assert len(observer._failed_reads) == 16
        assert observer.uncaught_missing_read(failures[0]) is None
    finally:
        observer.uninstall()
