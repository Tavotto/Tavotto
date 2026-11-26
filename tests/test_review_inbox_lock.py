"""收件箱的跨进程锁（ADR 0205 §四）：桌面与 MCP 两个进程同写 `review/shares.json`，谁也不能冲掉谁。

判据的主语是**另一个进程**写下的记录：用真子进程（不是线程）各加 N 条，最后盘上一条不少；
反证（2026-11-23 手工做过）：把 worker 脚本里的 `file_lock` 换成空操作（环境变量 `LOCK=off`），同样的 2 × 25 条并发写
丢记录，连跑 6 次都丢；加锁后 6 次都是 50。反证不进套件——它依赖调度，在单核 CI 上可能碰巧不丢而变成假红。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tavotto.engine import reviewinbox

ROOT = Path(__file__).resolve().parents[1]

WORKER = textwrap.dedent(
    """
    import contextlib, os, sys, time
    from pathlib import Path
    from tavotto.engine import reviewinbox
    tag, n, gate = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    if os.environ.get("LOCK") == "off":  # 手工反证用，套件里不设
        reviewinbox.file_lock = lambda *a, **k: contextlib.nullcontext()
    while not gate.exists():
        time.sleep(0.001)
    for i in range(n):
        sid = "rs_" + tag + "A" * 13 + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"[i // 32] + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"[i % 32]
        ok = reviewinbox.add_pending(
            share_id=sid, owner_token="t" * 22, review_url="https://x/r/" + "a" * 20,
            kind="figure", target_id="fig", title="T", note="", expires_ts=time.time() + 3600,
        )
        assert ok
    """
)


def _env(data_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAVOTTO_")}
    env.update(PYTHONPATH=str(ROOT / "src"), TAVOTTO_DATA_DIR=str(data_dir))
    return env


def _spawn_two(tmp_path: Path, n: int, extra: dict | None = None):
    gate = tmp_path / "go"
    env = {**_env(tmp_path / "data"), **(extra or {})}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, tag, str(n), str(gate)],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for tag in ("A", "B")
    ]
    time.sleep(0.5)  # 两个都进了等待循环
    gate.write_text("go")
    errs = [p.communicate(timeout=120)[1] for p in procs]
    return [p.returncode for p in procs], errs


def test_two_processes_each_adding_records_lose_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    codes, errs = _spawn_two(tmp_path, 25)
    assert codes == [0, 0], errs
    ids = {r["share_id"] for r in reviewinbox._load_locked()["shares"]}
    assert len(ids) == 50


HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from tavotto.engine import reviewinbox
    release = Path(sys.argv[1])
    with reviewinbox.file_lock():
        print("held", flush=True)
        deadline = time.monotonic() + 30
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    """
)


def test_a_lock_that_cannot_be_taken_fails_as_a_write_error_and_writes_nothing(
    tmp_path, monkeypatch
):
    """另一个**真进程**用产品自己的 `file_lock` 占着锁（POSIX 走 fcntl、Windows 走 msvcrt，同一条用例两边都跑）。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(reviewinbox, "LOCK_TIMEOUT_S", 0.2)
    release = tmp_path / "release"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(release)],
        env=_env(tmp_path / "data"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert holder.stdout.readline().strip() == "held", holder.stderr.read()
        t0 = time.monotonic()
        with pytest.raises(OSError):
            reviewinbox.add_pending(
                share_id="rs_AAAAAAAAAAAAAAAA",
                owner_token="t" * 22,
                review_url="https://x/r/" + "a" * 20,
                kind="figure",
                target_id="fig",
                title="T",
                note="",
                expires_ts=time.time() + 3600,
            )
        assert time.monotonic() - t0 < 3
        assert not reviewinbox.path().exists()  # 绝不跳过锁直接写
    finally:
        release.write_text("go")
        holder.communicate(timeout=60)
    assert holder.returncode == 0
    # 对方放手之后照常能写
    assert reviewinbox.add_pending(
        share_id="rs_AAAAAAAAAAAAAAAA",
        owner_token="t" * 22,
        review_url="https://x/r/" + "a" * 20,
        kind="figure",
        target_id="fig",
        title="T",
        note="",
        expires_ts=time.time() + 3600,
    )


def test_the_lock_is_reentrant_inside_one_process(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    with reviewinbox.file_lock():
        with reviewinbox.file_lock():
            assert reviewinbox.listed() == []
    with reviewinbox.file_lock():  # 释放干净，能再拿
        pass


KEY = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from tavotto.engine import reviewshare
    gate = Path(sys.argv[1])
    while not gate.exists():
        time.sleep(0.001)
    print(bytes(reviewshare._signing_key().verify_key).hex())
    """
)


def test_two_processes_first_using_the_device_key_end_up_with_the_same_key(tmp_path):
    gate = tmp_path / "go"
    env = _env(tmp_path / "data")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", KEY, str(gate)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for _ in range(4)
    ]
    time.sleep(0.5)
    gate.write_text("go")
    outs = [p.communicate(timeout=60) for p in procs]
    assert [p.returncode for p in procs] == [0] * 4, outs
    keys = {o[0].strip() for o in outs}
    assert len(keys) == 1 and len(next(iter(keys))) == 64  # 谁也没覆盖谁
