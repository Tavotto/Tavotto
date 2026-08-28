"""native 会话注册表与**单 reader** 传输（ADR 0021 §5）。

对面用一个 socketpair 假装 Bridge Runner：本批判据全在**传输与状态机**上，
真起一个 Python 只会让每条用例慢几秒而证明不了更多（真进程链在
`test_run_cli_integration.py` 与 `test_native_barrier_semantics.py`）。

单 reader 那条是本批的重点。ADR 0020 的 spike 侧是"每条请求开一个读线程 +
`join(timeout)`"——超时之后那个线程**还卡在 socket 上**，下一条请求再开一个，
两个 reader 抢同一条流。此后没有任何东西能证明"这条响应是那条请求的"，而
错配的表现是 A 图的 manifest 落在 B 图上。
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from tavotto.engine import envlease, nativesession, pool, runcodes
from tavotto.engine.runcodes import RunError

META = {
    "project_root": "/p",
    "interpreter": "/p/.venv/bin/python",
    "cwd": "/p",
    "target_kind": "script",
    "target_display": "figure.py",
    "arg_count": 0,
    "command_fingerprint": "f" * 32,
    "permission_key": "k" * 32,
    "python_version": "3.13.1",
}


def descriptor(native_id: str = "a" * 32, **meta) -> dict:
    return {
        "schema": 1,
        "native_id": native_id,
        "relay": {"host": "127.0.0.1", "attach_port": 1},
        "attach_token": "t",
        "out_dir": "/tmp/native-out",
        "metadata": {**META, **meta},
    }


class FakeRunner:
    """socketpair 的另一端：像 Bridge Runner 那样说 worker v1。"""

    def __init__(self):
        self.mine, self.theirs = socket.socketpair()
        self.rfile = self.mine.makefile("rb")
        self.requests: list[dict] = []

    def connect(self, *_a, **_kw):
        return self.theirs

    def read_request(self, timeout: float = 10.0) -> dict:
        self.mine.settimeout(timeout)
        line = self.rfile.readline()
        assert line, "对端关掉了"
        req = json.loads(line)
        self.requests.append(req)
        return req

    def send(self, obj: dict) -> None:
        self.mine.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def send_raw(self, data: bytes) -> None:
        self.mine.sendall(data)

    def reply(self, req: dict, **body) -> None:
        self.send(
            {
                "ok": True,
                "protocol_version": pool.PROTOCOL_VERSION,
                "request_id": req["request_id"],
                **body,
            }
        )

    def event(self, name: str, **fields) -> None:
        self.send({nativesession.EVENT_KEY: name, **fields})

    def close(self) -> None:
        for obj in (self.rfile, self.mine, self.theirs):
            try:
                obj.close()
            except OSError:
                pass


@pytest.fixture
def runner():
    r = FakeRunner()
    yield r
    r.close()


@pytest.fixture
def session(runner):
    s = nativesession.REGISTRY.attach(descriptor(), connect=runner.connect)
    yield s
    s.shutdown()
    nativesession.REGISTRY.forget(s.session_id)


def at_barrier(runner, session, stems=("Fig1",)):
    runner.event("barrier", reason="show", stems=list(stems))
    assert session.wait_for_state([nativesession.BARRIER], 10) == nativesession.BARRIER
    return session


# --------------------------------------------------------------------------
def test_native_register_and_state_machine(runner, session):
    assert session.state == nativesession.STARTING_PYTHON
    session.note_hello({"pid": 4242})
    assert session.state == nativesession.WAITING_FOR_FIGURE
    assert session.process_pid == 4242

    runner.event("barrier", reason="show", stems=["Fig1"])
    assert session.wait_for_state([nativesession.BARRIER], 10) == nativesession.BARRIER
    assert session.barrier_reason == "show"
    assert session.stems == ["Fig1"]
    assert session.public_state()["editable"] is True

    runner.event("released", reason="show")
    assert (
        session.wait_for_state([nativesession.RUNNING_SCRIPT], 10) == nativesession.RUNNING_SCRIPT
    )
    assert session.public_state()["editable"] is False

    runner.event("exit", code=0, figures=1)
    assert session.wait_for_state([nativesession.ENDED], 10) == nativesession.ENDED
    assert session.exit_code == 0 and session.figures_captured == 1


def test_the_registry_is_not_the_worker_pool():
    """**独立一张表**（ADR 0021 §5）：池的 LRU 会 `shutdown()` 掉最久没用的
    那个，而 native 会话的"那个"是**用户正在跑的脚本**。"""
    assert nativesession.REGISTRY is not getattr(pool, "_workers", None)
    body = _module_body(nativesession)  # 模块 docstring 里会解释这些名字
    assert "pool.get(" not in body, "native 会话不该经过 pool.get"
    assert "MAX_ALIVE" not in body, "native 会话不该受 LRU 上限管"


def test_native_duplicate_attach_is_refused(runner):
    s = nativesession.REGISTRY.attach(descriptor(), connect=runner.connect)
    try:
        other = FakeRunner()
        with pytest.raises(RunError) as exc:
            nativesession.REGISTRY.attach(descriptor(), connect=other.connect)
        assert exc.value.code == runcodes.NATIVE_SESSION_CONFLICT
        other.close()
    finally:
        s.shutdown()
        nativesession.REGISTRY.forget(s.session_id)


def test_native_not_at_barrier(runner, session):
    """脚本在跑的时候**当场拒绝，不排队**。

    排队的表现是：用户几分钟前点的那次改动，在他早就忘了的时候突然生效。
    """
    session.note_hello({"pid": 1})
    with pytest.raises(RunError) as exc:
        session.ensure_built()
    assert exc.value.code == runcodes.NATIVE_SESSION_NOT_AT_BARRIER
    assert runner.requests == [], "被拒绝的请求不该发出去"


def test_native_single_reader(runner, session):
    """**只有一个线程读 socket**——不管发过多少条请求。"""
    at_barrier(runner, session)

    def _serve():
        for _ in range(5):
            req = runner.read_request()
            runner.reply(req, stems={}, descriptors=[])

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    for _ in range(5):
        session.ensure_built()
    t.join(20)
    readers = [th for th in threading.enumerate() if th.name.startswith("tavotto-native-reader")]
    assert len(readers) == 1, f"跑了 5 条请求之后有 {len(readers)} 个 reader 线程"


def test_native_timeout_does_not_leave_a_second_reader(runner, session, monkeypatch):
    """超时之后**不再开第二个 reader**，而且会话被标成 poisoned。

    "再试一次"在这里是错的：我们不知道那条请求有没有被执行，而下一条请求的
    响应可能是它的。
    """
    at_barrier(runner, session)
    monkeypatch.setattr(nativesession, "BUILD_TIMEOUT", 0.3)
    reader = threading.Thread(target=runner.read_request, daemon=True)
    reader.start()  # 请求确实发出去了，只是没人回
    with pytest.raises(RunError) as exc:
        session.ensure_built()
    reader.join(10)
    assert exc.value.code == runcodes.NATIVE_RELAY_FAILED
    assert session.transport.poison is not None
    readers = [th for th in threading.enumerate() if th.name.startswith("tavotto-native-reader")]
    assert len(readers) == 1

    # 迟到的响应到了：**丢掉**（framing 仍然是对的），但会话不会假装可用
    req = runner.requests[-1]
    runner.reply(req, stems={}, descriptors=[])
    time.sleep(0.2)
    assert session.transport.orphan_responses == 1
    with pytest.raises(RunError) as exc2:
        session.ensure_built()
    assert exc2.value.code == runcodes.NATIVE_RELAY_FAILED


def test_a_malformed_frame_fails_the_session(runner, session):
    """坏帧 = framing 没有证明了。**不跳过、不猜**——跳过一帧之后，下一条
    响应会被配到上一条请求上。"""
    at_barrier(runner, session)
    runner.send_raw(b"{not json at all\n")
    assert session.wait_for_state([nativesession.FAILED], 10) == nativesession.FAILED
    assert session.terminal_error["code"] == runcodes.NATIVE_RELAY_FAILED


def test_native_disconnect_wakes_waiters(runner, session):
    """EOF **唤醒所有等待者**——绝不让谁永久挂着。"""
    at_barrier(runner, session)
    box: dict = {}

    def _ask():
        try:
            session.ensure_built()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_ask, daemon=True)
    t.start()
    runner.read_request()
    runner.close()
    t.join(15)
    assert not t.is_alive(), "对端断开之后请求还挂着"
    assert isinstance(box.get("error"), RunError)
    assert box["error"].code == runcodes.NATIVE_SESSION_DISCONNECTED


def test_a_mismatched_echo_poisons_the_session(runner, session):
    """回显对不上 = 会话错位了。继续用下去，用户会看到 A 图的 manifest
    落在 B 图上。

    **对不上的两种形状要分开看**：`request_id` 对不上在单 reader 下根本配不到
    等待者（那是"孤儿响应"，由超时那条用例覆盖）；这里量的是配到了、但
    `protocol_version` 不是我们说的那一版——那意味着对面换了一套语义。
    """
    at_barrier(runner, session)

    def _serve():
        req = runner.read_request()
        runner.send({"ok": True, "protocol_version": 99, "request_id": req["request_id"]})

    threading.Thread(target=_serve, daemon=True).start()
    with pytest.raises(RunError) as exc:
        session.ensure_built()
    assert exc.value.code == runcodes.NATIVE_RELAY_FAILED
    assert session.transport.poison is not None


def test_the_envelope_comes_from_the_one_place(runner, session):
    """信封由 `pool.build_envelope()` 产出——**与 stdin/stdout 那条控制面是
    同一个函数**（ADR 0020 §6）。没有第二套协议语义。"""
    at_barrier(runner, session)

    def _serve():
        req = runner.read_request()
        runner.reply(req, stems={}, descriptors=[])

    threading.Thread(target=_serve, daemon=True).start()
    session.ensure_built()
    sent = runner.requests[-1]
    assert sent["protocol_version"] == pool.PROTOCOL_VERSION
    assert sent["cmd"] == "build"
    assert isinstance(sent["request_id"], str) and sent["request_id"]
    assert set(pool.build_envelope({"cmd": "build"})) <= set(sent)


# --------------------------------------------------------------------------
# logical asset → live route
# --------------------------------------------------------------------------
def _built(runner, session, stems=("Fig1",), script="figure.py"):
    at_barrier(runner, session, stems)

    def _serve():
        req = runner.read_request()
        runner.reply(
            req,
            stems={s: {"size_mm": [80, 60], "source": "savefig"} for s in stems},
            descriptors=[{"script": script, "stem": s} for s in stems],
        )

    threading.Thread(target=_serve, daemon=True).start()
    return session.ensure_built()


def test_route_binds_and_resolves(runner, session):
    _built(runner, session)
    nativesession.REGISTRY.bind_assets(session)
    assert nativesession.REGISTRY.route_for("/p", "figure.py", "Fig1") is session
    assert nativesession.REGISTRY.route_for("/p", "figure.py", "Fig2") is None


def test_native_asset_conflict_is_reported_not_silently_taken(runner):
    """两个终端跑同一个脚本：**后来的不抢现有面板**（ADR 0021 §9.2）。

    静默抢过来的表现是用户在界面上看到的图突然换成了另一次运行的，
    而界面什么都没说。
    """
    first = nativesession.REGISTRY.attach(descriptor("a" * 32), connect=runner.connect)
    other = FakeRunner()
    second = nativesession.REGISTRY.attach(descriptor("b" * 32), connect=other.connect)
    try:
        _built(runner, first)
        assert nativesession.REGISTRY.bind_assets(first) == []
        _built(other, second)
        rejected = nativesession.REGISTRY.bind_assets(second)
        assert rejected == ["Fig1"]
        assert nativesession.REGISTRY.route_for("/p", "figure.py", "Fig1") is first
    finally:
        first.shutdown()
        second.shutdown()
        nativesession.REGISTRY.forget(first.session_id)
        nativesession.REGISTRY.forget(second.session_id)
        other.close()


def test_a_route_dies_with_its_session(runner, session):
    """会话结束 → live route 立刻失效。**cache 里还有预览不等于会话还在。**"""
    _built(runner, session)
    nativesession.REGISTRY.bind_assets(session)
    runner.event("exit", code=0, figures=1)
    session.wait_for_state([nativesession.ENDED], 10)
    assert nativesession.REGISTRY.route_for("/p", "figure.py", "Fig1") is None


def test_requests_after_the_session_ended_have_a_stable_code(runner, session):
    runner.event("exit", code=0, figures=0)
    session.wait_for_state([nativesession.ENDED], 10)
    with pytest.raises(RunError) as exc:
        session.ensure_built()
    assert exc.value.code == runcodes.NATIVE_SESSION_ENDED


def test_terminal_state_never_goes_back(runner, session):
    """终态不回头：脚本已经退出了，UI 不该因为一条迟到的事件又显示成
    "正在运行"。"""
    runner.event("exit", code=3, figures=0)
    session.wait_for_state([nativesession.ENDED], 10)
    runner.event("barrier", reason="show", stems=["Fig1"])
    time.sleep(0.2)
    assert session.state == nativesession.ENDED
    assert session.exit_code == 3


# --------------------------------------------------------------------------
def test_attach_takes_an_environment_lease_and_gives_it_back(runner):
    """attach = 这个环境上多了一条 native 会话（装依赖要被挡住）。"""
    s = nativesession.REGISTRY.attach(descriptor(), connect=runner.connect)
    try:
        assert envlease.native_sessions_on(META["interpreter"]) == [s.session_id]
        runner.event("exit", code=0, figures=0)
        s.wait_for_state([nativesession.ENDED], 10)
        assert envlease.native_sessions_on(META["interpreter"]) == []
    finally:
        s.shutdown()
        nativesession.REGISTRY.forget(s.session_id)


def test_attach_is_refused_while_the_environment_is_mutating(runner):
    """装包期间**不许**起 native 会话——半装完的 site-packages 上 import 到
    一半的失败会落在用户自己的脚本上。"""
    with envlease.mutating(envlease.env_key_of(META["interpreter"]), META["interpreter"]):
        with pytest.raises(envlease.EnvironmentBusy):
            nativesession.REGISTRY.attach(descriptor(), connect=runner.connect)
    assert envlease.native_sessions_on(META["interpreter"]) == [], "被拒之后不该留下租约"


def test_public_state_and_diagnostics_carry_no_secrets(runner, session):
    session.note_hello({"pid": 7})
    blob = json.dumps(session.public_state(), ensure_ascii=False, default=str)
    assert "attach_token" not in blob and '"t"' not in blob
    diag = session.diagnostics()
    text = json.dumps(diag, ensure_ascii=False, default=str)
    assert META["cwd"] not in text, "诊断里出现了 cwd 原文（应该只有哈希）"
    assert diag["cwd_hash"] and diag["execution_profile"] == "native"
    assert "interpreter" not in diag, "诊断里不该有解释器路径原文"


def test_shutdown_all_does_not_kill_the_user_script(runner, session):
    """sidecar 收摊 = **只放手**。runner 看到 EOF 会先恢复 baseline 再放开
    屏障，脚本照常跑完——这就是"关掉 App 默认 detach and continue"。"""
    nativesession.REGISTRY.shutdown_all()
    assert session.state == nativesession.DETACHED
    tail = _module_body(nativesession).split("def shutdown_all")[-1]
    for forbidden in ("os.kill", "proc.kill", "SIGKILL", "SIGTERM"):
        assert forbidden not in tail, f"收摊路径里出现了 {forbidden!r}——那是用户的进程"


def _module_body(module) -> str:
    """去掉模块 docstring 的源码。

    结构性守卫要判的是**代码**，而这些模块的 docstring 里恰恰会解释"为什么
    不用 MAX_ALIVE / 为什么不 os.kill"——把说明文字当成违规是这类判据最常见的
    假红。
    """
    src = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
    return src.split('"""', 2)[-1]
