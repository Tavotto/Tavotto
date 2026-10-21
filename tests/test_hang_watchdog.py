"""用例级挂死看门狗（issue #1226）：真起一个内层会话，让一条用例故意睡过很小的阈值。

不依赖真实的长时间等待：阈值给 2 秒，睡 120 秒，期望内层在几秒内以退出码 1 结束并把 nodeid、
线程栈、子进程名单打到 stderr。内层用例和一条「正常很快」的对照一起跑，证明没有误杀。
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import textwrap
import time

import pytest

from support import hang_watchdog as hw

REPO = pathlib.Path(__file__).resolve().parents[1]


CONFTEST = """
import importlib.util, sys
sys.path.insert(0, {tests!r})
spec = importlib.util.spec_from_file_location("outer_tests_conftest", {conftest!r})
mod = importlib.util.module_from_spec(spec)
sys.modules["outer_tests_conftest"] = mod
spec.loader.exec_module(mod)
# 只借真 conftest 里的这一个钩子，其余（兜底数据目录、分片选项、遥测门禁……）与本探针无关。
pytest_runtest_protocol = mod.pytest_runtest_protocol
"""


def _inner(tmp_path, body: str, **env_extra):
    """在 tmp_path 里造一个只借用真 conftest 看门狗钩子的内层会话。"""
    (tmp_path / "conftest.py").write_text(
        CONFTEST.format(tests=str(REPO / "tests"), conftest=str(REPO / "tests" / "conftest.py")),
        encoding="utf-8",
    )
    inner = tmp_path / "test_inner_probe.py"
    inner.write_text(textwrap.dedent(body), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAVOTTO_TEST_")}
    env.update(env_extra)
    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=0",
            "-q",
            str(inner),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc, time.monotonic() - started


HANG_BODY = """
    import time

    def test_fast_control():
        assert True

    def test_sleeps_forever():
        time.sleep(120)
"""


def test_hung_case_is_named_dumped_and_ends_the_process(tmp_path):
    proc, elapsed = _inner(tmp_path, HANG_BODY, TAVOTTO_TEST_HANG_SECONDS="2")
    err = proc.stdout + proc.stderr
    assert proc.returncode == hw.HANG_EXIT_CODE, err
    assert elapsed < 60, elapsed  # 没有等 120 秒的 sleep
    assert "test_inner_probe.py::test_sleeps_forever" in err, err
    after = err.split("用例挂死", 1)[1]
    assert "Current thread" in after or "Thread 0x" in after, err
    assert "in test_sleeps_forever" in after, err  # 栈里指到了卡住的那一行
    assert "子进程：" in err, err
    assert "test_fast_control" not in err.split("用例挂死", 1)[1], err  # 对照用例没被误杀


def test_watchdog_is_off_by_default(tmp_path):
    proc, _ = _inner(tmp_path, "def test_ok():\n    assert True\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "用例挂死" not in proc.stdout + proc.stderr
    assert "[slow-test" not in proc.stdout + proc.stderr


def test_slow_case_leaves_a_live_note_without_failing(tmp_path):
    proc, _ = _inner(
        tmp_path,
        "import time\n\ndef test_a_bit_slow():\n    time.sleep(1.2)\n",
        TAVOTTO_TEST_HANG_SECONDS="60",
        TAVOTTO_TEST_SLOW_NOTE_SECONDS="1",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "test_inner_probe.py::test_a_bit_slow" in out and "[slow-test" in out, out


@pytest.mark.parametrize("raw", [None, "", "0", "-5", "abc"])
def test_bad_or_missing_limit_means_disabled(raw):
    env = {} if raw is None else {hw.HANG_ENV: raw}
    assert hw.hang_seconds(env) is None
    assert hw.slow_note_seconds(env) is None


def test_slow_note_threshold_defaults_and_overrides():
    assert hw.slow_note_seconds({hw.HANG_ENV: "900"}) == hw.DEFAULT_SLOW_NOTE_SECONDS
    assert hw.slow_note_seconds({hw.HANG_ENV: "900", hw.SLOW_NOTE_ENV: "5"}) == 5
    assert (
        hw.slow_note_seconds({hw.HANG_ENV: "900", hw.SLOW_NOTE_ENV: "junk"})
        == hw.DEFAULT_SLOW_NOTE_SECONDS
    )


def test_report_and_exit_names_the_case_and_calls_exit():
    out, codes = [], []
    hw.report_and_exit("t.py::x", 7, write=out.append, exit_fn=codes.append)
    text = "".join(out)
    assert "t.py::x" in text
    assert "子进程：" in text
    assert codes == [hw.HANG_EXIT_CODE]


def test_ci_arms_the_watchdog_on_both_sharded_backend_lanes():
    """没有这一条，机制写得再对，CI 上也是一次都不会触发的空门禁。"""
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = re.compile(
        r"      - name: pytest\n        env:\n          " + hw.HANG_ENV + r': "(\d+)"\n'
        r"        run: >-\n          python -m pytest --shard="
    )
    for job_id in ("backend-fast", "backend-platforms"):
        start = text.index(f"\n  {job_id}:\n")
        end = text.find("\n  # ───", start)
        match = step.search(text, start, end if end > 0 else len(text))
        assert match, f"{job_id} 的分片 pytest 步骤没有设置 {hw.HANG_ENV}"
        # 必须大于 pytest.ini 里 300 秒的 faulthandler 打栈线（先看到栈、再退出），且远小于 job 上限 90 分钟。
        assert 300 < int(match.group(1)) <= 1800, (job_id, match.group(1))


def test_report_survives_a_cp1252_stderr_and_still_exits(monkeypatch):
    """Windows runner 的 stderr 可能是 cp1252：中文表头不许让计时线程死在 os._exit 之前。"""
    import io

    raw = io.BytesIO()
    fake = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "__stderr__", fake)
    codes = []
    hw.report_and_exit("t.py::中文", 7, exit_fn=codes.append)
    fake.flush()
    assert codes == [hw.HANG_EXIT_CODE]
    assert "t.py::".encode() in raw.getvalue()


def test_exit_is_reached_even_when_the_writer_raises():
    def boom(_text):
        raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")

    codes = []
    hw.report_and_exit("t.py::x", 7, write=boom, exit_fn=codes.append)
    assert codes == [hw.HANG_EXIT_CODE]


@pytest.mark.skipif(os.name == "nt", reason="ps 只在 POSIX")
def test_no_children_is_reported_as_none_not_as_the_ps_probe():
    out = hw.describe_children()
    assert "没有子进程" in out, out
    assert "ps -axo" not in out, out


@pytest.mark.skipif(os.name == "nt", reason="ps 只在 POSIX")
def test_real_child_is_listed():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        out = hw.describe_children()
        assert f"pid={child.pid} " in out, out
    finally:
        child.kill()
        child.wait()
