"""脚本眼里的 `sys.stdout`：safe worker 换成了 `scriptinput.StdoutTail`，真 stdout 上的常用写法都得照常跑。

TVD-E03P：Windows 用户脚本第一行 `sys.stdout.reconfigure(encoding="utf-8")`，在 safe 下报
`'StdoutTail' object has no attribute 'reconfigure'`，一张图都没出。

判据的主语：**子进程里的用户脚本**能不能跑完、出图（build 回的 stems），以及经字节层写的输出是否仍进了
界面看的那段尾巴（`StdoutTail.tail()`）。
"""

from __future__ import annotations

import io
import os
import sys

import pytest

from tavotto.engine import pool, scriptinput

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _workerd_binary() -> str | None:
    saved = os.environ.pop("TAVOTTO_WORKERD", None)
    try:
        from tavotto.engine import workerd_client

        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["TAVOTTO_WORKERD"] = saved


WORKERD_EXE = _workerd_binary()

_PLOT = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot([0, 1], [0, 1])
fig.savefig("{stem}.pdf")
"""

#: 三种 Windows 脚本里常见的「让 stdout 输出 UTF-8」写法，各自打印中文后出一张图
SCRIPTS = {
    "reconfigure": 'import sys\nsys.stdout.reconfigure(encoding="utf-8")\nprint("中文 ok")\n',
    "wrap_buffer": (
        "import io, sys\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")\n'
        'print("中文 ok")\nsys.stdout.flush()\n'
    ),
    "wrap_detach": (
        "import io, sys\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding="utf-8")\n'
        'print("中文 ok")\nsys.stdout.flush()\n'
    ),
}


@pytest.fixture(params=["python", "workerd"])
def plane(request, monkeypatch):
    """两条控制面：Python 池（conftest 默认）/ 真 workerd（没有产物就 skip——skip 不是绿）。"""
    from tavotto.engine import workerd_client

    if request.param == "workerd":
        if WORKERD_EXE is None:
            pytest.skip("没有 tavotto-workerd 产物（先在 workerd/ 里 cargo build）")
        monkeypatch.setenv("TAVOTTO_WORKERD", WORKERD_EXE)
    workerd_client.reset_client()
    yield request.param
    workerd_client.reset_client()


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    yield root
    pool.shutdown_all(str(root), wait=True)


@needs_worker
@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_common_windows_stdout_idioms_still_produce_the_figure(plane, figs, name):
    stem = "Fig_" + name
    (figs / f"{name}.py").write_text(SCRIPTS[name] + _PLOT.format(stem=stem), encoding="utf-8")
    worker, resp = pool.build(f"{name}.py", str(figs), "__main__")
    assert stem in (resp.get("stems") or [])
    log = worker.log_path.read_text(encoding="utf-8", errors="replace")
    assert "中文 ok" in log


def test_reconfigure_accepts_the_real_keywords_and_rejects_a_bad_encoding():
    tail = scriptinput.StdoutTail(io.StringIO())
    assert tail.reconfigure(encoding="utf-8", errors="replace", line_buffering=True) is None
    with pytest.raises(LookupError):
        tail.reconfigure(encoding="no-such-codec")
    with pytest.raises(TypeError):
        tail.reconfigure("utf-8")  # 真 stdout 只收关键字参数


def test_bytes_written_through_the_buffer_reach_the_tail_the_ui_reads():
    """编号清单经 `sys.stdout.buffer` 写出也得进尾巴，用户才看得到选项；汉字拆在两次 write 里不乱。"""
    target = io.StringIO()
    tail = scriptinput.StdoutTail(target)
    data = "1. 甲\n2. 乙\n".encode()
    tail.buffer.write(data[:4])  # 「甲」的三个字节被拆开
    tail.buffer.write(data[4:])
    assert tail.tail() == "1. 甲\n2. 乙"
    assert target.getvalue() == "1. 甲\n2. 乙\n"


def test_a_wrapper_around_the_buffer_does_not_close_it_when_collected():
    tail = scriptinput.StdoutTail(io.StringIO())
    wrapper = io.TextIOWrapper(tail.buffer, encoding="utf-8")
    wrapper.write("a\n")
    wrapper.close()
    again = io.TextIOWrapper(tail.buffer, encoding="utf-8")  # 第二次包不报 closed file
    again.write("b\n")
    again.flush()
    assert tail.detach() is tail.buffer
    tail.write("c\n")
    assert tail.tail().splitlines()[-1] == "c"


def test_a_menu_buffered_in_a_script_wrapper_reaches_the_question(tmp_path, monkeypatch):
    """`sys.stdout = TextIOWrapper(sys.stdout.buffer)` 后 print 菜单、`input()` 不带提示：菜单还在包装层缓冲里，
    发问那一刻的尾巴与上下文摘要里也得有它（Codex #1541 P2）。"""
    import json

    tail = scriptinput.StdoutTail(io.StringIO())
    wrapper = io.TextIOWrapper(tail.buffer, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", wrapper)
    wrapper.write("1. alpha\n2. beta\n")  # 没 flush
    ch = scriptinput.Channel(tmp_path, "s.py", tail=tail)
    (tmp_path / scriptinput.reply_name(1)).write_text(json.dumps({"answer": "1"}), "utf-8")
    seen = {}
    real = ch._write_request

    def spy(index, payload):
        seen.update(payload)
        real(index, payload)

    monkeypatch.setattr(ch, "_write_request", spy)
    assert ch.ask("", "input") == "1"
    assert "1. alpha" in seen["stdout_tail"] and "2. beta" in seen["stdout_tail"]
