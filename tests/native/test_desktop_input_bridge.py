"""桌面拥有的 native 进程里脚本的 `input / readline / read / getpass`（P07，AC-15，ADR 0126）。

链条与 `test_desktop_owned_process.py` 相同：`desktopproc.start` 在本测试进程里真 spawn 用户 Python
（真 Bridge Runner），唯一被替掉的是窗口与 Flask。「答题的人」由用例扮演——读
`session.pending_input`、调 `session.answer_input()`，与 `POST /api/native/sessions/<id>/input`
走的是同一个方法。

被测的合同（桌面拥有的会话才有；终端拥有的继续用真 stdin）：

* 脚本停在 `input()` 等人答，**同一个进程、同一批对象、同一个调用栈**续跑——答完不是重跑；
* 昂贵的科学计算恰好一次（用外部计数文件观测，不靠 AST 判）；
* 口令（getpass）的值只经控制通道一次，不进公开状态 / 事件 / 诊断 / 日志 / 任何落盘文件；
* 取消 / EOF 有确定的结果；多个窗口看到同一个 Pending，先到先得，迟到与重复答复被拒。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from support import desktopkit as dk, nativekit
from tavotto.engine import config, execspec, nativesession, runcodes
from tavotto.engine.runcodes import RunError

pytestmark = nativekit.needs_user_python

SECRET = "hunter2-NOT-FOR-DISK-7f3a"


@pytest.fixture(autouse=True)
def _reap_owned():
    yield
    for s in nativesession.REGISTRY.list():
        if s.owned is not None and not s.owned.finished:
            try:
                s.cancel_owned()
            except Exception:  # noqa: BLE001
                pass


def start(tmp_path, script_text, **kw):
    """桌面「打开并运行」用的 stdin 通道：输入桥（OS stdin 是 null，脚本的提问由界面作答）。"""
    return dk.start(tmp_path, script_text, stdin=execspec.STDIN_BRIDGE, **kw)


def wait_pending(session, kind: str | None = None, timeout: float = 60.0) -> dict:
    """等脚本停在一次提问上，返回那份 Pending 的公开形状。"""
    box: dict = {}

    def _ready() -> bool:
        p = session.pending_input
        if p is not None and (kind is None or p["kind"] == kind):
            box.update(p)
            return True
        return False

    dk.wait_until(_ready, timeout=timeout, what=f"pending input {kind or ''}")
    return box


FOUR_FORMS = (
    dk.FIGURE_HEADER
    + """\
import getpass, json, os, sys

# 昂贵的科学计算：外部计数文件观测它跑了几次（不是 AST 判）
with open("compute.log", "a") as f:
    f.write("computed\\n")
state = {"obj": object(), "pid": os.getpid()}
obj_id = id(state["obj"])

def where():
    return {"pid": os.getpid(), "same_obj": id(state["obj"]) == obj_id}

seen = {}
seen["input"] = input("Your name? ")
seen["input_where"] = where()
seen["readline"] = sys.stdin.readline()
seen["readline_where"] = where()
seen["read"] = sys.stdin.read()
seen["read_where"] = where()
tok = getpass.getpass("Token: ")
seen["getpass_len"] = len(tok)
seen["getpass_where"] = where()
seen["start_pid"] = state["pid"]
with open("seen.json", "w") as f:
    json.dump(seen, f)
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
"""
)


def test_four_input_forms_continue_in_the_same_process(tmp_path, caplog):
    """input / readline / read / getpass 回答后同 PID、同对象，计算恰好一次（AC-15）。"""
    caplog.set_level(logging.DEBUG)
    session = start(tmp_path, FOUR_FORMS)
    work = tmp_path / "work"

    p1 = wait_pending(session, "input")
    assert p1["prompt"] == "Your name? " and p1["masked"] is False
    assert session.process_pid == session.owned.pid
    session.answer_input(p1["request_id"], text="Ada")

    p2 = wait_pending(session, "readline")
    assert p2["request_id"] != p1["request_id"]
    session.answer_input(p2["request_id"], text="second line")

    p3 = wait_pending(session, "read")
    session.answer_input(p3["request_id"], text="body one\nbody two")

    p4 = wait_pending(session, "getpass")
    assert p4["masked"] is True
    session.answer_input(p4["request_id"], text=SECRET)

    nativekit.wait_state(session, [nativesession.BARRIER])
    seen = dk.read_json(work / "seen.json")
    assert seen["input"] == "Ada"
    assert seen["readline"] == "second line\n"
    assert seen["read"] == "body one\nbody two"
    assert seen["getpass_len"] == len(SECRET)
    pid = session.owned.pid
    for key in ("input_where", "readline_where", "read_where", "getpass_where"):
        assert seen[key] == {"pid": pid, "same_obj": True}, f"{key} 不是原地续跑"
    assert seen["start_pid"] == pid
    assert (work / "compute.log").read_text().splitlines() == ["computed"], "科学计算不止一次"
    assert session.pending_input is None

    # 口令只经控制通道一次：公开状态 / 事件 / 诊断 / 日志 / 落盘文件 / 进程输出里都没有
    haystacks = {
        "public_state": json.dumps(session.public_state(), ensure_ascii=False),
        "events": json.dumps(list(session.events), ensure_ascii=False, default=str),
        "diagnostics": json.dumps(session.diagnostics(), ensure_ascii=False),
        "log": "\n".join(r.getMessage() for r in caplog.records),
        "stdout": dk.tail(session, "stdout"),
        "stderr": dk.tail(session, "stderr"),
    }
    for name, text in haystacks.items():
        assert SECRET not in text, f"口令泄漏到 {name}"
    roots = [Path(config.data_dir()), tmp_path, session.out_dir]
    for root in roots:
        for path in Path(root).rglob("*"):
            if path.is_file() and path.stat().st_size < 5_000_000:
                assert SECRET.encode() not in path.read_bytes(), f"口令落盘: {path}"
    dk.finish(session)


def test_every_input_form_answers_from_the_gui_only(tmp_path):
    """桌面拥有的进程 stdin 是 /dev/null：没有答复，脚本绝不会从别处拿到答案（负对照）。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import sys
line = sys.stdin.readline()
open("raw.txt", "w").write(repr(line))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "readline")
    # 脚本此刻停在提问上：没有被 EOF 的 stdin 放走
    time.sleep(0.5)
    assert (
        session.pending_input is not None and session.pending_input["request_id"] == p["request_id"]
    )
    assert not (tmp_path / "work" / "raw.txt").exists()
    session.answer_input(p["request_id"], text="typed")
    dk.finish(session)
    assert (tmp_path / "work" / "raw.txt").read_text() == repr("typed\n")


EOF_SCRIPT = (
    dk.FIGURE_HEADER
    + """\
import json, sys
out = {}
try:
    input("anything? ")
except EOFError:
    out["input"] = "EOFError"
out["readline"] = sys.stdin.readline()
out["read"] = sys.stdin.read()
json.dump(out, open("eof.json", "w"))
"""
)


def test_eof_answer_is_the_same_thing_a_closed_stdin_would_say(tmp_path):
    """用户选「没有输入」= EOF：`input` 抛 EOFError、readline 回空串、read 回空串。EOF 之后不再提问。"""
    session = start(tmp_path, EOF_SCRIPT)
    p = wait_pending(session, "input")
    session.answer_input(p["request_id"], eof=True)
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "eof.json") == {
        "input": "EOFError",
        "readline": "",
        "read": "",
    }
    assert session.pending_input is None


def test_second_window_sees_the_same_pending_and_late_answers_are_refused(tmp_path):
    """两个窗口：看到同一个 request_id；先到先得；迟到 / 重复 / 错 id 的答复被拒且不喂给脚本。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import json
a = input("first? ")
b = input("second? ")
json.dump([a, b], open("ab.json", "w"))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "input")
    # 窗口 B 重连：从注册表重新拿会话，看到的是同一个 Pending（事实在后端，不在某个窗口里）
    other = nativesession.REGISTRY.get(session.session_id)
    assert other.public_state()["pending_input"]["request_id"] == p["request_id"]

    session.answer_input(p["request_id"], text="A")
    with pytest.raises(RunError) as dup:
        other.answer_input(p["request_id"], text="DUP")  # 窗口 B 慢了一步
    assert dup.value.code == runcodes.NATIVE_INPUT_NOT_PENDING

    p2 = wait_pending(session, "input")
    assert p2["request_id"] != p["request_id"]
    with pytest.raises(RunError) as stale:
        other.answer_input(p["request_id"], text="STALE")  # 旧问题的答案不能答新问题
    assert stale.value.code == runcodes.NATIVE_INPUT_NOT_PENDING
    other.answer_input(p2["request_id"], text="B")
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "ab.json") == ["A", "B"]


def test_no_pending_means_nothing_to_answer(tmp_path):
    """没有在等的提问：答复当场被拒，不排队、不留着等下一次提问。"""
    session = start(tmp_path, dk.FIGURE_HEADER + "import time; time.sleep(30)\n")
    dk.wait_until(lambda: session.state == nativesession.WAITING_FOR_FIGURE, what="脚本在跑")
    with pytest.raises(RunError) as exc:
        session.answer_input("in-1", text="early")
    assert exc.value.code == runcodes.NATIVE_INPUT_NOT_PENDING
    session.cancel_owned()


def test_cancel_while_waiting_for_input_really_ends_the_process(tmp_path):
    """脚本停在提问上时取消：进程真退出、租约放掉、Pending 清掉（AC-16 的 input 场景）。"""
    script = dk.FIGURE_HEADER + "input('blocked? ')\nopen('after.txt','w').write('ran')\n"
    session = start(tmp_path, script)
    wait_pending(session, "input")
    pid = session.owned.pid
    result = session.cancel_owned()
    assert result.confirmed
    dk.wait_until(lambda: session.owned.finished, what="所有者确认结束")
    assert not dk.pid_alive(pid)
    assert not dk.holds_lease(session)
    assert session.pending_input is None
    assert session.state == nativesession.ENDED and session.cancelled
    assert not (tmp_path / "work" / "after.txt").exists(), "取消后脚本不应继续往下跑"


def test_pending_input_is_not_a_barrier(tmp_path):
    """提问期间脚本还没到屏障：编辑 / 继续被拒（不排队），答完再到屏障才能编辑。"""
    script = dk.FIGURE_HEADER + "x = input('go? ')\nfig, ax = plt.subplots()\nplt.show()\n"
    session = start(tmp_path, script)
    p = wait_pending(session, "input")
    with pytest.raises(RunError) as exc:
        session.resume()
    assert exc.value.code == runcodes.NATIVE_SESSION_NOT_AT_BARRIER
    session.answer_input(p["request_id"], text="y")
    nativekit.wait_state(session, [nativesession.BARRIER])
    dk.finish(session)


def test_answer_publishes_session_change_for_other_windows(tmp_path):
    """Pending 出现 / 消失都推一次会话事件（SSE 的来源），序号单调；事件里没有提示词与答案。"""
    seen = []
    nativesession.REGISTRY.on_change = lambda s, entry: seen.append((s.sequence, dict(entry)))
    try:
        script = dk.FIGURE_HEADER + "getpass_value = __import__('getpass').getpass('PW: ')\n"
        session = start(tmp_path, script)
        p = wait_pending(session, "getpass")
        session.answer_input(p["request_id"], text=SECRET)
        dk.finish(session)
    finally:
        nativesession.REGISTRY.on_change = None
    input_events = [e for _, e in seen if e.get("input")]
    assert [e["input"] for e in input_events][:2] == ["requested", "answered"]
    text = json.dumps(input_events, ensure_ascii=False)
    assert SECRET not in text and "PW: " not in text
    seqs = [s for s, _ in seen]
    assert seqs == sorted(seqs)


def test_terminal_owned_sessions_keep_the_real_stdin(tmp_path):
    """终端拥有的 native 不装这套钩子：答复入口当场拒绝（负对照；真 stdin 的行为由 CLI 用例钉着）。"""
    sess = nativesession.NativeSession(
        session_id="t-1",
        descriptor={"native_id": "n", "out_dir": str(tmp_path), "metadata": {}},
    )
    with pytest.raises(RunError) as exc:
        sess.answer_input("in-1", text="x")
    assert exc.value.code == runcodes.NATIVE_INPUT_NOT_PENDING


def test_threads_not_started_by_runner_for_input(tmp_path):
    """native 侧不起后台线程（本模块的承诺）：提问等待用的是主线程上的阻塞读。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import json, threading
before = threading.active_count()
input("q? ")
json.dump({"before": before, "after": threading.active_count()}, open("threads.json", "w"))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "input")
    session.answer_input(p["request_id"], text="x")
    dk.finish(session)
    seen = dk.read_json(tmp_path / "work" / "threads.json")
    assert seen["after"] == seen["before"]


def test_input_bridge_flag_belongs_to_the_bridge_stdin_channel_only(tmp_path):
    """`--input-bridge` 只在 `stdin=bridge` 的桌面启动命令行里；terminal / pipe / null 逐字节不变。"""
    from tavotto.engine import bridge

    def argv_for(stdin):
        launch = dk.launch_for(tmp_path, "pass\n", stdin=stdin)
        return execspec.bridge_argv(
            launch.spec, runner_py=bridge.RUNNER_PY, out_dir=tmp_path / "out"
        )

    assert "--input-bridge" in argv_for(execspec.STDIN_BRIDGE)
    assert "--input-bridge" not in argv_for(execspec.STDIN_PIPE)
    assert "--input-bridge" not in argv_for(execspec.STDIN_NULL)
    terminal = execspec.native_spec(
        str(tmp_path / "a.py"),
        interpreter=nativekit.USER_PYTHON,
        cwd=str(tmp_path),
        project_root=str(tmp_path),
    )
    assert "--input-bridge" not in execspec.bridge_argv(
        terminal, runner_py=bridge.RUNNER_PY, out_dir=tmp_path / "out"
    )


def test_a_question_asked_before_registration_is_still_announced(tmp_path, monkeypatch):
    """脚本一起来就 `input()`:传输 reader 先于 `registry.adopt` 处理提问时,`_on_change` 还没装上——
    登记完成后必须补发当前状态,否则界面永远收不到那一问的事件(Codex #1022 P1)。"""
    seen = []
    nativesession.REGISTRY.on_change = lambda s, entry: seen.append(dict(entry))
    real_adopt = nativesession.REGISTRY.adopt

    def slow_adopt(session):
        # 让提问在登记前到达:等 pending 出现(此时回调还是 None)再登记
        dk.wait_until(lambda: session.pending_input is not None, what="登记前的提问")
        real_adopt(session)

    monkeypatch.setattr(
        nativesession.REGISTRY, "adopt", slow_adopt
    )  # 打在实例上,不被别的用例遗留的实例属性遮住
    try:
        session = start(tmp_path, "input('immediately? ')\n")
        dk.wait_until(
            lambda: any(e.get("input") == "requested" for e in seen), what="补发的提问事件"
        )
    finally:
        nativesession.REGISTRY.on_change = None
    p = session.pending_input
    assert p is not None
    session.answer_input(p["request_id"], text="x")
    dk.finish(session)


def test_an_unencodable_answer_is_rejected_without_claiming_the_pending(tmp_path):
    """答复里有孤立的 UTF-16 代理项(JS 字符串可以有):编码失败不能已经把 pending 认领掉,
    否则重试被拒、脚本永远等下去(Codex #1022 r4267606099)。"""
    script = dk.FIGURE_HEADER + "import json\njson.dump(input('q? '), open('a.json','w'))\n"
    session = start(tmp_path, script)
    p = wait_pending(session, "input")
    with pytest.raises(RunError) as bad:
        session.answer_input(p["request_id"], text="ok\ud800bad")
    assert bad.value.code == runcodes.NATIVE_INPUT_INVALID
    assert (
        session.pending_input is not None and session.pending_input["request_id"] == p["request_id"]
    )
    session.answer_input(p["request_id"], text="fine")  # 同一问还能答
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "a.json") == "fine"


def test_a_question_arriving_during_the_terminal_transition_is_dropped(tmp_path, monkeypatch):
    """提问的「状态检查」与「装 pending」是一步:终态转换夹在它们之间时,提问必须被丢弃,不能在已结束的会话上
    复活一个答不了的框(Codex #1022 r4267749679)。时序用 `time.time` 钩子在检查与装载之间同步触发终态——不靠 sleep。"""
    session = nativesession.NativeSession(
        session_id="race-1", descriptor={"native_id": "n", "out_dir": str(tmp_path), "metadata": {}}
    )
    session.owned = object()  # 桌面拥有(鸭子类型,本用例不碰它)
    session._set_state(nativesession.WAITING_FOR_FIGURE)  # noqa: SLF001
    events = []
    session._on_change = lambda s, entry: events.append(dict(entry))  # noqa: SLF001
    real_time = nativesession.time.time
    fired = []

    def hooked():
        if not fired:  # 只在提问处理里触发一次:进终态(取消 / 断线都走这里)
            fired.append(1)
            session._set_state(nativesession.ENDED)  # noqa: SLF001
        return real_time()

    monkeypatch.setattr(nativesession.time, "time", hooked)
    session._on_input_request({"request_id": "in-1-1", "kind": "input", "prompt": "q? "})  # noqa: SLF001
    monkeypatch.setattr(nativesession.time, "time", real_time)
    assert fired, "钩子没触发,用例没有量到想量的窗口"
    assert session.state == nativesession.ENDED
    assert session.pending_input is None
    assert not [e for e in events if e.get("input") == "requested"]


class _CountingBridge:
    """只数「问了几次」的假桥：`_BridgeStdin` 的缓冲语义单测用。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = 0
        self.buffer = ""
        self.eof = False

    def ask(self, kind, prompt=""):
        self.asked += 1
        return self.answers.pop(0) if self.answers else None


def test_zero_length_readline_returns_immediately_without_asking():
    from tavotto.engine.bridge_runner import _BridgeStdin

    b = _CountingBridge(["never\n"])
    stdin = _BridgeStdin(b)
    assert stdin.readline(0) == ""
    assert stdin.read(0) == ""
    assert b.asked == 0  # 0 长度读不是一次提问：脚本不会因此停在界面上


def test_sized_readline_stops_at_size_without_waiting_for_newline():
    from tavotto.engine.bridge_runner import _BridgeStdin

    b = _CountingBridge(["abcdefgh\n"])
    stdin = _BridgeStdin(b)
    assert stdin.readline(3) == "abc"
    assert stdin.readline(-1) == "defgh\n"
    assert b.asked == 1


def test_readlines_honours_the_size_hint_like_a_real_text_stream(tmp_path):
    """`readlines(hint)`:累计长度达到 hint 就停,不继续发起提问(Codex #1022 第九轮);`read(0)` / `readline(0)`
    同样不提问。真文件:`StringIO('aa\\nbb\\n').readlines(3)` 只回第一行。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import json, sys
bounded = sys.stdin.readlines(3)           # 第一行 'aa\\n' 已经 3 个字符:读够即止
zero = (sys.stdin.read(0), sys.stdin.readline(0))
tail = input("next? ")                     # 真正的下一问应当是这条,而不是又一个 readline
json.dump([bounded, list(zero), tail], open("hint.json", "w"))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "readline")
    session.answer_input(p["request_id"], text="aa")
    nxt = wait_pending(session, None)
    assert nxt["kind"] == "input", f"readlines(3) 读够之后还在提问: {nxt['kind']}"
    session.answer_input(nxt["request_id"], text="done")
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "hint.json") == [["aa\n"], ["", ""], "done"]


def test_input_consumes_buffered_stdin_before_asking(tmp_path):
    """有界读剩下的缓冲先给 `input()`:`readline(1)` 收到 "abc" 后缓冲里有 "bc\\n",随后的 `input()` 直接返回 "bc",
    不再开新提问(Codex #1022 第十一轮)。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import json, sys
a = sys.stdin.readline(1)
b = input()
c = input("next? ")
json.dump([a, b, c], open("buf.json", "w"))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "readline")
    session.answer_input(p["request_id"], text="abc")
    nxt = wait_pending(session, "input")
    assert nxt["prompt"] == "next? ", f"缓冲里还有 'bc'，却又问了: {nxt}"
    session.answer_input(nxt["request_id"], text="last")
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "buf.json") == ["a", "bc", "last"]


def test_partial_read_leftovers_are_input_lines_not_eof(tmp_path):
    """`read(n)` 的整次回答多出来的部分：后续 `input()` 逐行取完（含没有换行的最后一段），取尽之后才是 EOFError；
    全程只有一次提问。"""
    script = (
        dk.FIGURE_HEADER
        + """\
import json, sys
head = sys.stdin.read(2)
lines = [input(), input()]
try:
    input()
    tail = "no-eof"
except EOFError:
    tail = "EOFError"
json.dump([head, lines, tail], open("leftover.json", "w"))
"""
    )
    session = start(tmp_path, script)
    p = wait_pending(session, "read")
    session.answer_input(p["request_id"], text="abcd\nzz")
    dk.finish(session)
    assert dk.read_json(tmp_path / "work" / "leftover.json") == ["ab", ["cd", "zz"], "EOFError"]
