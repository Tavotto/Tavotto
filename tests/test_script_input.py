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
        monkeypatch.setattr(inputbroker, "_has_answerer", lambda project: fe.present)
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
    # 口令绝不进 worker.log（它会进诊断包与错误里的日志尾巴，Codex #680 P1）；别的答案照常转录——
    # 先证明读的是这条会话的日志，「没有口令」才有意义
    log = worker.log_path.read_text(encoding="utf-8", errors="replace")
    assert "line-answer" in log
    assert "s3cret" not in log
    # 会合目录在 build 结束时删掉
    assert not (worker.out_dir / scriptinput.DIRNAME).exists()
    # 协议管道没被脚本读走：之后的请求照常
    stems = [s for s in (resp.get("stems") or [])]
    assert "Pick" in stems
    worker.override("Pick", [])


def test_a_getpass_answer_leaves_no_trace_on_disk_or_in_the_log(tmp_path, capsys):
    """口令：worker.log 只有固定标记，回复文件读完即删；普通 input 照常转录（Codex #680 P1）。"""
    ch = scriptinput.Channel(tmp_path, "s.py")
    (tmp_path / scriptinput.reply_name(1)).write_text(json.dumps({"answer": "hunter2"}), "utf-8")
    assert ch.ask("secret: ", "getpass") == "hunter2"
    (tmp_path / scriptinput.reply_name(2)).write_text(json.dumps({"answer": "plain"}), "utf-8")
    assert ch.ask("pick: ", "input") == "plain"
    err = capsys.readouterr().err
    assert "hunter2" not in err
    assert "plain" in err
    assert not (tmp_path / scriptinput.reply_name(1)).exists()
    assert not any("hunter2" in f.read_text("utf-8") for f in tmp_path.iterdir() if f.is_file())


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
    # 那一问的对话框被收回，且只收一次：脚本接住 EOF 后马上跑完，是「超时收起」还是「build 结束」
    # 先到取决于轮询时机（到点就收起的判据见 test_a_timed_out_question_is_retired_while_the_script_runs_on）
    assert [c["reason"] for c in fe.of("script.input_closed")] in (["timed_out"], ["finished"])


#: 接住 EOF 之后还要算一阵（`SI_LINGER` 秒）才画图：「超时之后、build 结束之前」那段窗口。
EOF_THEN_WORKS = """\
import json, os, time
import matplotlib.pyplot as plt

try:
    choice = input("which: ")
except EOFError:
    choice = "eof"
time.sleep(float(os.environ["SI_LINGER"]))
with open(os.environ["SI_RESULT"], "w", encoding="utf-8") as fh:
    json.dump(choice, fh)
fig, ax = plt.subplots(figsize=(2, 1.5))
fig.savefig("Late.pdf")
"""


@needs_worker
def test_a_timed_out_question_is_retired_while_the_script_runs_on(
    plane, figs, tmp_path, frontend, monkeypatch
):
    """等到超时、脚本接住 EOF 接着跑：那一问**当场**收起，迟到的答案被拒、不记住（Codex #680 P2）。
    不收起的话界面一直摆着过期的框，迟到的答案被记住——本次输出没用它，下次运行却用它。"""
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "1")
    monkeypatch.setenv("SI_LINGER", "8")
    (figs / "late.py").write_text(EOF_THEN_WORKS, encoding="utf-8")
    fe = frontend({})  # 界面在，但没人答
    out: dict = {}
    th = threading.Thread(
        target=lambda: out.setdefault("r", pool.build("late.py", str(figs), "__main__"))
    )
    th.start()
    try:
        deadline = time.monotonic() + 60
        while not fe.of("script.input_requested") and time.monotonic() < deadline:
            time.sleep(0.05)
        asked = fe.of("script.input_requested")[0]
        # 脚本还在算（build 没结束），这一问已经以「超时」收起
        while not fe.of("script.input_closed") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert th.is_alive(), "build 已经结束：量的不是「超时之后、结束之前」那段窗口"
        assert [c["reason"] for c in fe.of("script.input_closed")] == ["timed_out"]
        # 迟到的答案：不在等、不记住
        assert inputbroker.answer(asked["id"], "2") is None
        assert scriptanswers.entries(figs, "late.py") == []
    finally:
        th.join(120)
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == "eof"
    assert [c["reason"] for c in fe.of("script.input_closed")] == ["timed_out"]


def test_a_late_answer_loses_to_the_timeout_claim(tmp_path, monkeypatch):
    """worker 已经定了「超时」的案、父进程还没轮询到：迟到的答案照样被拒——不回复、不记住、收起那一问。"""
    saved: list[str] = []
    monkeypatch.setattr(scriptanswers, "remember", lambda *a, **k: saved.append(a[4]))
    monkeypatch.setattr(inputbroker, "_publish", None)
    p = inputbroker.Pending(
        id="q1",
        project_root=str(tmp_path),
        script="s.py",
        index=1,
        prompt="which: ",
        kind="input",
        directory=tmp_path,
    )
    monkeypatch.setattr(inputbroker, "_pending", {"q1": p})
    assert scriptinput.claim(tmp_path, 1, scriptinput.CLAIM_TIMEOUT)
    assert inputbroker.answer("q1", "2") is None
    assert saved == []
    assert not (tmp_path / scriptinput.reply_name(1)).exists()
    assert inputbroker.get_pending("q1") is None


def test_the_worker_waits_for_an_answer_that_claimed_first(tmp_path, monkeypatch):
    """到点时界面已经定了案（正在落盘）：worker 不按超时跑掉，等它把回复写出来再用——记住的 == 用到的。"""
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "0.3")
    ch = scriptinput.Channel(tmp_path, "s.py")
    assert scriptinput.claim(tmp_path, 1, scriptinput.CLAIM_ANSWER)
    timer = threading.Timer(
        1.2,
        lambda: (tmp_path / scriptinput.reply_name(1)).write_text(
            json.dumps({"answer": "2"}), "utf-8"
        ),
    )
    timer.start()
    try:
        assert ch.ask("which: ", "input") == "2"
    finally:
        timer.cancel()


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


# ---- 敏感会话的回复文件读后即删（Codex #812 P1）------------------------------------------------------------

_PRIVATE_KEY = "ab" * 16


def _channel(tmp_path, *, private: bool):
    ch = scriptinput.Channel(
        tmp_path / scriptinput.DIRNAME, "s.py", private_key=_PRIVATE_KEY if private else ""
    )
    ch.reset()
    return ch


def _leftover_plaintext(directory, needle: str) -> list[str]:
    return [p.name for p in directory.iterdir() if needle in p.read_text(encoding="utf-8")]


def _answer_with(channel, monkeypatch, content: str):
    def publish(index, payload):
        (channel.dir / scriptinput.reply_name(index)).write_text(content, encoding="utf-8")

    monkeypatch.setattr(channel, "_write_request", publish)


@pytest.mark.parametrize("kind", ["input", "readline", "read", "getpass"])
def test_private_reply_file_is_deleted_after_reading_for_every_kind(tmp_path, monkeypatch, kind):
    ch = _channel(tmp_path, private=True)
    _answer_with(ch, monkeypatch, '{"answer": "hunter2"}')
    assert ch.ask("q", kind) == "hunter2"
    assert not (ch.dir / scriptinput.reply_name(1)).exists()
    assert _leftover_plaintext(ch.dir, "hunter2") == []


def test_non_private_plain_input_reply_is_kept_as_before(tmp_path, monkeypatch):
    ch = _channel(tmp_path, private=False)
    _answer_with(ch, monkeypatch, '{"answer": "visible"}')
    assert ch.ask("q", "input") == "visible"
    assert (ch.dir / scriptinput.reply_name(1)).exists()


def test_non_private_getpass_reply_is_still_deleted(tmp_path, monkeypatch):
    ch = _channel(tmp_path, private=False)
    _answer_with(ch, monkeypatch, '{"answer": "pw"}')
    assert ch.ask("q", "getpass") == "pw"
    assert not (ch.dir / scriptinput.reply_name(1)).exists()


def test_private_reply_is_deleted_when_parsing_never_succeeds(tmp_path, monkeypatch):
    # 半截 / 非 dict 的回复读不出来 → 一路等到超时；文件在任何出口都不能留下
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "0.3")
    monkeypatch.setattr(scriptinput, "ANSWER_GRACE", 0.3)
    monkeypatch.setattr(scriptinput, "WORKER_POLL", 0.05)
    ch = _channel(tmp_path, private=True)
    _answer_with(ch, monkeypatch, '{"answer": "hunter2"')
    assert ch.ask("q", "input") is None
    assert not (ch.dir / scriptinput.reply_name(1)).exists()


def test_private_reply_is_deleted_when_the_read_raises(tmp_path, monkeypatch):
    ch = _channel(tmp_path, private=True)
    _answer_with(ch, monkeypatch, '{"answer": "hunter2"}')

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(scriptinput.json, "loads", boom)
    with pytest.raises(KeyboardInterrupt):
        ch.ask("q", "input")
    assert not (ch.dir / scriptinput.reply_name(1)).exists()


def test_private_no_answer_reply_raises_and_leaves_nothing(tmp_path, monkeypatch):
    ch = _channel(tmp_path, private=True)
    _answer_with(ch, monkeypatch, '{"no_answer": true, "reason": "x"}')
    with pytest.raises(scriptinput.ScriptNeedsInput):
        ch.ask("q", "input")
    assert list(p.name for p in ch.dir.iterdir() if p.name.startswith("reply-")) == []


def test_private_answer_is_not_transcribed_to_stderr_log(tmp_path, monkeypatch, capsys):
    ch = _channel(tmp_path, private=True)
    _answer_with(ch, monkeypatch, '{"answer": "hunter2"}')
    ch.ask("q", "input")
    assert "hunter2" not in capsys.readouterr().err


def test_broker_writes_private_reply_owner_only_and_leaves_no_temp(tmp_path):
    if os.name != "posix":
        pytest.skip("mode bits are POSIX-only")
    d = tmp_path / "rv"
    d.mkdir()
    seen = {}
    real = inputbroker.atomicio.write_bytes

    def spy(path, data, *, mode=None):
        seen["mode"] = mode
        real(path, data, mode=mode)
        seen["names"] = sorted(p.name for p in d.iterdir())

    inputbroker.atomicio.write_bytes, saved = spy, real
    try:
        inputbroker._reply(d, 1, {"answer": "hunter2"}, private=True)
    finally:
        inputbroker.atomicio.write_bytes = saved
    assert seen["mode"] == 0o600
    assert seen["names"] == [scriptinput.reply_name(1)]
    assert (d / scriptinput.reply_name(1)).stat().st_mode & 0o077 == 0


def test_private_channel_directory_is_owner_only(tmp_path):
    if os.name != "posix":
        pytest.skip("mode bits are POSIX-only")
    ch = _channel(tmp_path, private=True)
    assert ch.dir.stat().st_mode & 0o077 == 0
