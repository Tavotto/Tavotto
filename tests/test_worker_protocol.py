"""pool 侧的 worker 协议 v1（信封构造 + 响应校验），不需要科学栈。

真 worker 的往返在 `test_worker_roundtrip.py`（要 matplotlib，没有就整档跳过）。
这里用**假子进程**盯住父进程自己的那一半：发出去的信封长什么样、响应对不上
号时会不会继续用这个会话。后者是数据损坏级的——管道串行，回显对不上意味着
之后每一条响应都错位，A 图的 manifest 会落到 B 图上。
"""
import json
import threading

import pytest

from tavotto.engine import patchspec, pool


class _FakePipe:
    """worker 的 stdin：把父进程写进来的信封解析出来，顺手排好回应。"""

    def __init__(self, proc):
        self.proc = proc
        self.sent: list[dict] = []

    def write(self, text: str) -> None:
        env = json.loads(text)
        self.sent.append(env)
        self.proc.queue(self.proc.responder(env))

    def flush(self) -> None:
        pass


class _FakeStdout:
    def __init__(self, proc):
        self.proc = proc

    def readline(self) -> str:
        return self.proc.pending.pop(0) if self.proc.pending else ""


class _FakeProc:
    """只实现 `EngineWorker.request()` 真正用到的那几个口子。"""

    def __init__(self, responder):
        self.responder = responder
        self.stdin = _FakePipe(self)
        self.stdout = _FakeStdout(self)
        self.pending: list[str] = []
        self.returncode = None
        self.killed = False

    def queue(self, resp) -> None:
        if resp == "":          # 模拟「进程死了」：readline 直接读到 EOF
            return
        line = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
        self.pending.append(line + "\n")

    def poll(self):
        return self.returncode

    #: 真实的 `Popen.kill()` **只发信号**，不等进程退出——紧接着的 `poll()`
    #: 完全可能还回 None。假进程默认照着这个现实来（`reap_on_kill=False`）：
    #: 假的比真的更配合，用例就测不到真实的竞态（这条正是 review 抓到的：
    #: 第一版假进程在 kill 里同步设了 returncode，于是「kill 完 alive() 未必
    #: 是假」这个窗口在用例里根本不存在）。
    reap_on_kill = False

    def kill(self) -> None:
        self.killed = True
        if self.reap_on_kill:
            self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode if self.returncode is not None else 0


def _worker(responder, tmp_path) -> pool.EngineWorker:
    """不 spawn 任何进程地捏一个 EngineWorker（`__init__` 会真的起子进程）。"""
    w = object.__new__(pool.EngineWorker)
    w.script_name = "fig_fake.py"
    w.figures_dir = str(tmp_path)
    w.entry = "main"
    w.base = tmp_path
    w.out_dir = tmp_path / "out"
    w.log_path = tmp_path / "worker.log"     # 不存在 → _log_tail 回空串
    w.lock = threading.Lock()
    w.rev = 0
    w.generation = 4
    w.built = True
    w.last_patch_hash = ""
    w.last_patch_hash_by_stem = {}           # 写回自检按 stem 问的那份账本
    w.last_used = 0.0
    w._touched = float("inf")                # 关掉 _touch 的落盘（无 base 目录也不炸）
    w.proc = _FakeProc(responder)
    return w


def _echo(env, **overrides) -> dict:
    """一个「规矩的」v1 成功响应。"""
    resp = {"ok": True, "protocol_version": 1,
            "request_id": env["request_id"],
            "worker_generation": env.get("worker_generation"),
            "render_revision": env.get("render_revision")}
    if "canonical_patch_hash" in env:
        resp["canonical_patch_hash"] = env["canonical_patch_hash"]
    resp.update(overrides)
    return resp


# ------------------------------ 信封构造 ------------------------------
def test_envelope_carries_version_generation_revision_and_hash(tmp_path):
    w = _worker(lambda env: _echo(env, manifest={}, warnings=[]), tmp_path)
    w.rev = 12
    patches = [{"gid": "b", "prop": "text", "value": "后写的"},
               {"gid": "a", "prop": "text", "value": "先写的"}]
    w.override("Fig1", patches)

    env = w.proc.stdin.sent[-1]
    assert env["protocol_version"] == pool.PROTOCOL_VERSION == 1
    assert env["request_id"].startswith("r-") and len(env["request_id"]) > 8
    assert env["worker_generation"] == 4
    assert env["render_revision"] == 12
    # 池方法叫 override（app.py 一路这么叫），线上命令叫 render
    assert env["cmd"] == "render"
    assert env["stem"] == "Fig1"             # stem 走顶层，不在 payload 里
    assert env["payload"] == {"patches": patches}
    assert env["canonical_patch_hash"] == patchspec.patch_hash(patches)


def test_request_ids_are_unique_per_request(tmp_path):
    w = _worker(lambda env: _echo(env), tmp_path)
    for _ in range(5):
        w.request({"cmd": "ping"})
    ids = [e["request_id"] for e in w.proc.stdin.sent]
    assert len(set(ids)) == 5


def test_commands_map_to_the_v1_names_and_payloads(tmp_path):
    w = _worker(lambda env: _echo(env, path="/tmp/x.png", stems={},
                                  manifest={}, warnings=[]), tmp_path)
    w.ensure_built()
    w.render_png("Fig1", 800)
    w.preview_png("Fig1", [], 400, "hist3")
    w.export("Fig1", [], "/tmp/x.pdf", "pdf", 300)
    sent = {e["cmd"]: e for e in w.proc.stdin.sent}
    assert set(sent) == {"build", "render_png", "preview_png", "export"}
    assert sent["build"]["payload"] == {}
    assert "canonical_patch_hash" not in sent["build"]     # 不带 patches 就不算
    assert sent["render_png"]["payload"] == {"width": 800}
    assert sent["preview_png"]["payload"] == {"patches": [], "width": 400,
                                              "tag": "hist3"}
    assert sent["export"]["payload"] == {"patches": [], "path": "/tmp/x.pdf",
                                         "format": "pdf", "dpi": 300}


def test_generation_increments_per_pool_key():
    """同一 (项目, 脚本) 每重建一次 +1；不同池键各算各的。"""
    a = pool._next_generation(("/proj/gen-a", "fig1.py"))
    assert a == 1                                   # 从 1 开始
    assert pool._next_generation(("/proj/gen-a", "fig1.py")) == 2
    # 另一个池键独立计数（同名脚本在两个项目里是两个会话）
    assert pool._next_generation(("/proj/gen-b", "fig1.py")) == 1
    assert pool._next_generation(("/proj/gen-a", "fig1.py")) == 3


# ------------------------------ 响应校验 ------------------------------
def test_request_id_mismatch_kills_the_session(tmp_path):
    """回显对不上 = 会话已错位，必须杀掉不复用（与超时同纪律）。

    不杀的话后面每一条响应都对错请求：用户看到 A 面板的修改出现在 B 面板上，
    而且没有任何报错——比直接失败糟得多。
    """
    w = _worker(lambda env: _echo(env, request_id="r-别人的"), tmp_path)
    with pytest.raises(pool.WorkerError) as e:
        w.request({"cmd": "ping"})
    assert e.value.code == "protocol_mismatch"
    assert "重试" in str(e.value)
    assert w.proc.killed, "协议错乱的 worker 必须被 kill，不许留在池里复用"


def test_pipe_eof_kills_the_session_so_get_cannot_reuse_it(tmp_path):
    """管道 EOF 之后 `alive()` 必须立刻是假——否则 `get()` 会复用一条死 worker。

    「这个 worker 还在不在」只能有**一个**判据。读到 EOF 的那一刻我们已经知道
    答案了，可 `alive()` 问的是 `poll()`：子进程关掉 stdout 到被回收之间有一个
    窗口，`poll()` 在那期间仍回 None。不就地杀掉的话，`pool.get()` 会认为这条
    worker 还能用、直接复用，下一次请求写进死管道、等满整个超时才失败。

    **workerd 那侧是同一个坑，同一天修的**（`session.rs` 的 EOF 分支就地摘掉
    进程）。pool 是 workerd 的参考实现，判据分叉就等于有两套语义——所以两边
    各有一条用例。那边的窗口是靠假 worker「关了 stdout 先赖 1.5 秒」拉长成必然
    的；这边用假进程直接把 `poll()` 钉成 None。
    """
    w = _worker(lambda env: "", tmp_path)        # responder 回 "" = 读到 EOF
    assert w.proc.poll() is None, "前提：进程对象此刻还『活着』"
    with pytest.raises(pool.WorkerError) as e:
        w.request({"cmd": "ping"})
    assert "崩溃" in str(e.value)
    assert w.proc.killed, "读到 EOF 的 worker 必须就地杀掉，不许留给 get() 复用"
    # **关键的一句**：假进程的 `kill()` 刻意**不**同步回收（真的 `Popen.kill()`
    # 也不会），所以 `poll()` 此刻**仍然回 None**。`alive()` 还敢说「活着」的话，
    # `get()` 就会把这条死 worker 复用掉。
    assert w.proc.poll() is None, "前提：kill 之后进程还没被回收（真实情形）"
    assert not w.alive(), (
        "kill 完 alive() 必须立刻是假。只问 `poll()` 的话这里会回 True——"
        "而 `Popen.kill()` 只发信号、不等退出")


def test_protocol_version_mismatch_kills_the_session(tmp_path):
    w = _worker(lambda env: _echo(env, protocol_version=2), tmp_path)
    with pytest.raises(pool.WorkerError) as e:
        w.request({"cmd": "ping"})
    assert e.value.code == "protocol_mismatch"
    assert w.proc.killed


def test_v1_error_envelope_becomes_a_workererror(tmp_path):
    w = _worker(lambda env: {
        "ok": False, "protocol_version": 1, "request_id": env["request_id"],
        "error": {"code": "unknown_stem", "retryable": False,
                  "message": "stem 不存在: nope", "traceback": ""},
    }, tmp_path)
    with pytest.raises(pool.WorkerError) as e:
        w.request({"cmd": "ping"})
    assert e.value.code == "unknown_stem"
    assert "nope" in str(e.value)
    assert not w.proc.killed, "普通业务错误不该把会话杀掉"


def test_missing_dependency_still_wins_over_the_protocol_code(tmp_path):
    """缺包对用户是完全不同的一件事（有可执行出口），优先于协议 code。"""
    tb = 'Traceback…\nModuleNotFoundError: No module named \'rdkit.Chem\'\n'
    w = _worker(lambda env: {
        "ok": False, "protocol_version": 1, "request_id": env["request_id"],
        "error": {"code": "script_error", "retryable": False,
                  "message": "脚本执行失败", "traceback": tb},
    }, tmp_path)
    with pytest.raises(pool.WorkerError) as e:
        w.request({"cmd": "ping"})
    assert e.value.code == "missing_dependency"
    assert e.value.module == "rdkit"


def test_hash_mismatch_is_logged_but_the_result_is_used(tmp_path, caplog):
    """哈希分歧只是警告：worker 照常执行了，结果照常用。"""
    w = _worker(lambda env: _echo(env, manifest={"elements": []}, warnings=[],
                                  hash_mismatch=True,
                                  worker_patch_hash="sha256:" + "1" * 64),
                tmp_path)
    with caplog.at_level("WARNING", logger="tavotto.engine"):
        resp = w.override("Fig1", [{"gid": "g", "prop": "text", "value": "x"}])
    assert resp["manifest"] == {"elements": []}
    assert any("哈希不一致" in r.getMessage() for r in caplog.records)


def test_dead_worker_and_empty_response_keep_their_old_errors(tmp_path):
    """老的两条兜底不变：进程已退出 / 无响应。"""
    w = _worker(lambda env: "", tmp_path)          # 空行 = EOF = 崩了
    w.proc.pending.clear()
    with pytest.raises(pool.WorkerError, match="崩溃"):
        w.request({"cmd": "ping"})

    w2 = _worker(lambda env: _echo(env), tmp_path)
    w2.proc.returncode = 0
    with pytest.raises(pool.WorkerError, match="已退出"):
        w2.request({"cmd": "ping"})


# ------------------------------ 计时管道 ------------------------------
def test_control_plane_adds_queue_wait_and_total(tmp_path):
    """父进程补的两个数一定在：worker 只说自己那一段，排队与往返归控制面。"""
    w = _worker(lambda env: _echo(env, manifest={}, warnings=[],
                                  timings={"patch_apply_ms": 1.5,
                                           "canvas_draw_ms": 9.0,
                                           "manifest_ms": 4.0}),
                tmp_path)
    resp = w.override("Fig1", [])
    t = resp["timings"]
    # worker 自报的一个都不许被改
    assert t["patch_apply_ms"] == 1.5 and t["canvas_draw_ms"] == 9.0
    assert t["manifest_ms"] == 4.0
    assert isinstance(t["queue_wait_ms"], float) and t["queue_wait_ms"] >= 0
    assert isinstance(t["total_ms"], float) and t["total_ms"] >= t["queue_wait_ms"]


def test_timings_survive_a_worker_that_reports_none(tmp_path):
    """worker 一个 timings 都不给（老 worker / legacy 分支）也不能炸。"""
    w = _worker(lambda env: _echo(env, manifest={}, warnings=[]), tmp_path)
    resp = w.override("Fig1", [])
    assert set(resp["timings"]) == {"queue_wait_ms", "total_ms"}


def test_cold_render_folds_in_the_build_timings(tmp_path):
    """冷启动那一次 render 必须带上 build 的耗时——用户等的是同一件事。

    不并过来的话响应里只剩几毫秒的 apply/draw，而用户刚等了半分钟。
    """
    def responder(env):
        if env["cmd"] == "build":
            return _echo(env, stems={}, timings={"script_build_ms": 4200.0,
                                                 "script_exec_ms": 4100.0})
        return _echo(env, manifest={}, warnings=[],
                     timings={"patch_apply_ms": 0.4, "canvas_draw_ms": 8.0})

    w = _worker(responder, tmp_path)
    w.built = False
    t = w.override("Fig1", [])["timings"]
    assert t["script_build_ms"] == 4200.0 and t["script_exec_ms"] == 4100.0
    assert t["build_total_ms"] >= 0          # build 那次往返，与 render 的分开
    assert t["canvas_draw_ms"] == 8.0        # render 自己那份没被盖掉
    # 已经 built 的会话不再折叠（第二次渲染不该凭空冒出 script_build_ms）
    assert "script_build_ms" not in w.override("Fig1", [])["timings"]


def test_preview_dpi_is_only_sent_when_asked(tmp_path):
    """不给 preview_dpi 时信封形状一字不变（老调用方与 golden 断言都靠这条）。"""
    w = _worker(lambda env: _echo(env, manifest={}, warnings=[]), tmp_path)
    w.override("Fig1", [])
    assert w.proc.stdin.sent[-1]["payload"] == {"patches": []}
    w.override("Fig1", [], preview_dpi=96)
    assert w.proc.stdin.sent[-1]["payload"] == {"patches": [], "preview_dpi": 96}


def test_inline_svg_is_only_sent_when_asked(tmp_path):
    """同上：`inline_svg` 也是「不给就一个字段都不加」的可选项。"""
    w = _worker(lambda env: _echo(env, manifest={}, warnings=[], svg="<svg/>"), tmp_path)
    w.override("Fig1", [])
    assert w.proc.stdin.sent[-1]["payload"] == {"patches": []}
    resp = w.override("Fig1", [], inline_svg=True)
    assert w.proc.stdin.sent[-1]["payload"] == {"patches": [], "inline_svg": True}
    # 结果字段整体透传（控制面不解释 svg，只是把它带上来）
    assert resp["svg"] == "<svg/>"
