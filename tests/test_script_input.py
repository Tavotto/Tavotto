"""脚本里的 `input()` / `sys.stdin` / `getpass` 桥接——真 worker、两条控制面（ADR 0099）。

判据的主语：**子进程里的用户脚本**实际读到了什么（脚本自己把读到的值写进项目外的结果文件），以及
父进程侧 `inputbroker` 按哪种策略答的（记下发给界面的事件）。不看 worker 的日志、不看我们自己的记账——
那是被测对象自己的说法。
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from tavotto.engine import inputbroker, pool, scriptanswers, scriptinput

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

#: 读四种入口，把读到的原样写进结果文件；最后画一张图。
ALL_KINDS = """\
import getpass, json, os, sys
import matplotlib.pyplot as plt

print("1. alpha")
print("2. beta")
got = {}
got["input"] = input("pick: ")
got["readline"] = sys.stdin.readline()
got["getpass"] = getpass.getpass("secret: ")
got["read"] = sys.stdin.read()
got["after_read"] = sys.stdin.read()
with open(os.environ["SI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(got, fh)
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(got["input"])
fig.savefig("Pick.pdf")
"""

ONE_INPUT = """\
import json, os
import matplotlib.pyplot as plt

print("1. alpha")
print("2. beta")
choice = input("which: ")
with open(os.environ["SI_RESULT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(choice) + "\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(choice)
fig.savefig("One.pdf")
"""

SWALLOWS_EXCEPTION = """\
import matplotlib.pyplot as plt

try:
    choice = input("which: ")
except Exception:
    choice = "swallowed"
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(choice)
fig.savefig("Sw.pdf")
"""

CATCHES_EOF = """\
import json, os
import matplotlib.pyplot as plt

try:
    choice = input("which: ")
except EOFError:
    choice = "eof"
with open(os.environ["SI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(choice, fh)
fig, ax = plt.subplots(figsize=(2, 1.5))
fig.savefig("Eof.pdf")
"""


class Frontend:
    """假界面：记下每条事件；`answers` 里有的问题就当场作答（按提示认）。"""

    def __init__(self, answers: dict | None = None, *, present: bool = True):
        self.answers = dict(answers or {})
        self.present = present
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def publish(self, event: str, project: str, data: dict) -> None:
        with self._lock:
            self.events.append((event, dict(data)))
        if event == "script.input_requested" and data["prompt"] in self.answers:
            value = self.answers[data["prompt"]]
            threading.Thread(
                target=inputbroker.answer,
                args=(data["id"], value),
                kwargs={"eof": value is None},
                daemon=True,
            ).start()

    def of(self, kind: str) -> list[dict]:
        with self._lock:
            return [d for e, d in self.events if e == kind]


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
def figs(tmp_path, monkeypatch):
    root = tmp_path / "figs"
    root.mkdir()
    monkeypatch.setenv("SI_RESULT", str(tmp_path / "result.json"))
    # 等人作答的上限缩到秒级：回归时用例在几十秒内红，而不是挂满 10 分钟（超时用例自己再改小）
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "20")
    yield root
    pool.shutdown_all(str(root), wait=True)


@pytest.fixture
def frontend(monkeypatch):
    holder: dict = {}

    def install(answers=None, *, present=True) -> Frontend:
        fe = Frontend(answers, present=present)
        holder["fe"] = fe
        monkeypatch.setattr(inputbroker, "_publish", fe.publish)
        monkeypatch.setattr(inputbroker, "_has_answerer", lambda: fe.present)
        return fe

    return install


def _worker_class(plane: str):
    return pool.WorkerdWorker if plane == "workerd" else pool.EngineWorker


def test_waiting_for_a_human_never_outlives_the_build_silence_watchdog():
    """等人作答先到点（回 EOF），静默看门狗永远轮不到杀一个正在等人的 worker（ADR 0099 §三）。"""
    assert scriptinput.INPUT_WAIT_TIMEOUT < pool.BUILD_IDLE_TIMEOUT


@needs_worker
def test_all_four_entry_points_are_bridged_on_both_planes(plane, figs, tmp_path, frontend):
    (figs / "all.py").write_text(ALL_KINDS, encoding="utf-8")
    fe = frontend({"pick: ": "1,2", "": "line-answer", "secret: ": "s3cret"})
    worker, resp = pool.build("all.py", str(figs), "__main__")
    assert isinstance(worker, _worker_class(plane))

    got = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert got == {
        "input": "1,2",
        "readline": "line-answer\n",
        "getpass": "s3cret",
        "read": "line-answer",
        "after_read": "",
    }
    asked = fe.of("script.input_requested")
    assert [(a["index"], a["input_kind"], a["prompt"]) for a in asked] == [
        (1, "input", "pick: "),
        (2, "readline", ""),
        (3, "getpass", "secret: "),
        (4, "read", ""),
    ]
    # 用户要看到脚本列出的编号清单才能选
    assert "1. alpha" in asked[0]["stdout_tail"] and "2. beta" in asked[0]["stdout_tail"]
    assert [r["answer"] for r in resp["script_inputs"]] == [
        "1,2",
        "line-answer",
        "s3cret",
        "line-answer",
    ]
    assert worker.last_build_script_inputs == resp["script_inputs"]
    # 按项目记住——口令除外
    kept = scriptanswers.entries(figs, "all.py")
    assert [(e["index"], e["answer"]) for e in kept] == [
        (1, "1,2"),
        (2, "line-answer"),
        (4, "line-answer"),
    ]
    # 会合目录在 build 结束时删掉
    assert not (worker.out_dir / scriptinput.DIRNAME).exists()
    # 协议管道没被脚本读走：之后的请求照常
    stems = [s for s in (resp.get("stems") or [])]
    assert "Pick" in stems
    worker.override("Pick", [])


@needs_worker
def test_remembered_answers_are_filled_in_without_asking(plane, figs, tmp_path, frontend):
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    scriptanswers.remember(figs, "one.py", 1, "which: ", "2")
    fe = frontend(present=True)
    pool.build("one.py", str(figs), "__main__")
    assert (tmp_path / "result.json").read_text(encoding="utf-8").splitlines() == ['"2"']
    assert fe.of("script.input_requested") == []
    assert [(a["index"], a["answer"]) for a in fe.of("script.input_autofilled")] == [(1, "2")]


@needs_worker
def test_a_changed_prompt_is_a_new_question(plane, figs, tmp_path, frontend):
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    scriptanswers.remember(figs, "one.py", 1, "an older prompt: ", "2")
    fe = frontend({"which: ": "1"})
    pool.build("one.py", str(figs), "__main__")
    assert (tmp_path / "result.json").read_text(encoding="utf-8").splitlines() == ['"1"']
    assert len(fe.of("script.input_requested")) == 1
    assert [(e["prompt"], e["answer"]) for e in scriptanswers.entries(figs, "one.py")] == [
        ("which: ", "1")
    ]


@needs_worker
def test_no_answerer_fails_fast_with_the_prompt(plane, figs, frontend):
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    frontend(present=False)
    t0 = time.monotonic()
    with pytest.raises(pool.WorkerError) as ei:
        pool.build("one.py", str(figs), "__main__")
    assert ei.value.code == "script_needs_input"
    assert "which:" in str(ei.value)
    assert ei.value.extra.get("prompt") == "which: "
    # 绝不卡死：一次往返，不是等人的 10 分钟
    assert time.monotonic() - t0 < 60


@needs_worker
def test_except_exception_in_the_script_cannot_swallow_needs_input(plane, figs, frontend):
    """`ScriptNeedsInput` 继承 BaseException：脚本的 `except Exception` 吞不掉它，不会拿着假值画图。"""
    (figs / "sw.py").write_text(SWALLOWS_EXCEPTION, encoding="utf-8")
    frontend(present=False)
    with pytest.raises(pool.WorkerError) as ei:
        pool.build("sw.py", str(figs), "__main__")
    assert ei.value.code == "script_needs_input"


@needs_worker
def test_unanswered_input_times_out_as_eof(plane, figs, tmp_path, frontend, monkeypatch):
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "1.5")
    (figs / "eof.py").write_text(CATCHES_EOF, encoding="utf-8")
    fe = frontend({})  # 界面在，但没人答
    worker, resp = pool.build("eof.py", str(figs), "__main__")
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == "eof"
    assert resp["script_inputs"][0]["answer"] is None
    # 那一问的对话框被收回
    assert [c["reason"] for c in fe.of("script.input_closed")] == ["finished"]


@needs_worker
def test_uncaught_timeout_reports_script_input_timeout(plane, figs, frontend, monkeypatch):
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "1.5")
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    frontend({})
    with pytest.raises(pool.WorkerError) as ei:
        pool.build("one.py", str(figs), "__main__")
    assert ei.value.code == "script_input_timeout"
    assert ei.value.extra.get("prompt") == "which: "


@needs_worker
def test_one_shot_replays_exactly_the_hot_answers_and_never_asks(plane, figs, tmp_path, frontend):
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    fe = frontend({"which: ": "2"})
    hot, _ = pool.build("one.py", str(figs), "__main__")
    # 热态之后答案被改掉了：重放必须仍用热态那一组（热态 == 重放）
    scriptanswers.update(figs, "one.py", 1, "1")
    asked_before = len(fe.of("script.input_requested"))
    fresh = pool.one_shot(
        "one.py", str(figs), "__main__", script_inputs=hot.last_build_script_inputs
    )
    try:
        resp = fresh.ensure_built()
    finally:
        pool.discard(fresh)
    assert [r["answer"] for r in resp["script_inputs"]] == ["2"]
    assert (tmp_path / "result.json").read_text(encoding="utf-8").splitlines() == ['"2"', '"2"']
    assert len(fe.of("script.input_requested")) == asked_before


@needs_worker
def test_one_shot_without_answers_fails_instead_of_asking(plane, figs, frontend):
    (figs / "one.py").write_text(ONE_INPUT, encoding="utf-8")
    fe = frontend({"which: ": "2"})
    fresh = pool.one_shot("one.py", str(figs), "__main__")
    try:
        with pytest.raises(pool.WorkerError) as ei:
            fresh.ensure_built()
    finally:
        pool.discard(fresh)
    assert ei.value.code == "script_needs_input"
    assert fe.of("script.input_requested") == []
