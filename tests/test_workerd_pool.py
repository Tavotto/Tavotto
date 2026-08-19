"""`pool.py` 的控制面路由：找得到 workerd 就用它，否则原路走 Python 池。

这里不需要科学栈也不需要 cargo 产物——验的是**选路与规格构造**。真跑一遍
workerd 全链路的用例在 `test_worker_roundtrip.py` 末节（要 matplotlib + 二进制，
缺任一就整组跳过）。
"""
import subprocess

import pytest

from magplot.engine import pool, runtime, workerd_client


# ------------------------------ 选路 ------------------------------
def test_the_python_pool_is_the_default_when_workerd_is_absent(monkeypatch, tmp_path):
    """找不到二进制 = 一切照旧。加速件缺席绝不能让渲染整个不可用。"""
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)
    calls = []
    monkeypatch.setattr(pool, "EngineWorker",
                        lambda *a: calls.append(a) or "python-pool")
    assert pool._new_worker("fig.py", str(tmp_path), "main") == "python-pool"
    assert calls == [("fig.py", str(tmp_path), "main")]


def test_workerd_is_used_when_the_binary_is_there(monkeypatch, tmp_path):
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: "/bin/true")
    monkeypatch.setattr(pool, "WorkerdWorker", lambda *a: "workerd")
    monkeypatch.setattr(pool, "EngineWorker", lambda *a: "python-pool")
    assert pool._new_worker("fig.py", str(tmp_path), "main") == "workerd"


def test_a_failing_workerd_falls_back_to_the_python_pool(monkeypatch, tmp_path, caplog):
    """workerd 建会话失败**必须回退**并留痕——用户的图比控制面重要得多。"""
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: "/bin/true")

    def boom(*_a):
        raise pool.WorkerdUnavailable("起不来")

    monkeypatch.setattr(pool, "WorkerdWorker", boom)
    monkeypatch.setattr(pool, "EngineWorker", lambda *a: "python-pool")
    with caplog.at_level("WARNING", logger="mm.engine"):
        assert pool._new_worker("fig.py", str(tmp_path), "main") == "python-pool"
    assert any("回退" in r.getMessage() for r in caplog.records)


def test_the_conftest_default_keeps_the_suite_on_the_python_path():
    """整套既有用例必须跑在参考实现上（conftest 里把开关钉成 0）。"""
    assert pool.workerd_path() is None


def test_control_plane_reports_both_the_choice_and_what_is_actually_running(
        monkeypatch, tmp_path):
    """`selected` 与 `sessions` 缺一不可。

    workerd 建会话失败会**静默**回退（`_new_worker()` 的 except 分支），只报
    「二进制在」就会把「打进去了但一直没用上」说成一切正常——功能全在、只是慢，
    是最难被发现的一类失灵。冒烟脚本与诊断包都按这两个字段判定。
    """
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)
    assert pool.control_plane() == {"selected": "python", "path": None,
                                    "sessions": []}

    monkeypatch.setattr(workerd_client, "find_workerd", lambda: "/opt/wd")
    wd, _ = _worker(monkeypatch, tmp_path, [{"ok": True, "session_id": "s"}])
    monkeypatch.setattr(pool, "_workers", {
        ("d", "a.py"): wd,
        ("d", "b.py"): object(),   # 不是 WorkerdWorker = 这条已经回退到 Python 池
    })
    assert pool.control_plane() == {
        "selected": "workerd", "path": "/opt/wd",
        "sessions": ["workerd", "python"]}


# ------------------------------ spawn 规格 ------------------------------
def _capture_engine_worker_argv(monkeypatch, tmp_path, source):
    """真去构造一个 EngineWorker，但把 Popen 换成录音机。"""
    box = {}

    class _Rec:
        def __init__(self, argv, **kw):
            box["argv"] = argv
            box["env"] = kw.get("env")
            self.pid = 1

        def poll(self):
            return None

    monkeypatch.setattr(pool.subprocess, "Popen", _Rec)
    monkeypatch.setattr(pool, "select_worker_python", lambda: ("/usr/bin/python3", source))
    w = pool.EngineWorker("fig.py", str(tmp_path), "draw")
    return w, box


def test_the_spawn_spec_matches_what_the_python_pool_would_have_run(monkeypatch, tmp_path):
    """**同源性看护**：交给 workerd 的 argv 必须与 Python 池自己 Popen 的一致。

    两条路径各拼一次命令行的话，迟早有一条会漏掉 `--sandbox` 或 `--entry`——
    症状是「换个控制面就渲染不出来」，而错误信息在 worker 侧完全看不出原因。
    """
    w, box = _capture_engine_worker_argv(monkeypatch, tmp_path, pool.SOURCE_SYSTEM)
    spec = pool._spawn_spec("fig.py", str(tmp_path), "draw", w.out_dir, w.sandbox,
                            w.log_path, "/usr/bin/python3", pool.SOURCE_SYSTEM)
    assert spec["argv"] == box["argv"]
    assert spec["env"] == {}                     # 非内置 runtime 不注入任何 env
    assert spec["log_path"] == str(w.log_path)
    assert spec["handshake_timeout_ms"] == int(pool.HANDSHAKE_TIMEOUT * 1000)


def test_the_bundled_runtime_still_gets_its_args_and_env(monkeypatch, tmp_path):
    """内置 runtime 的 `-B` 与改道的 MPLCONFIGDIR 是「不往安装目录写东西」
    那条纪律的实体，换控制面不许把它们丢掉。

    `PYTHONPYCACHEPREFIX` **刻意不在里面**（见 `runtime.child_env` 的
    docstring：它会把读也改道，预编译字节码就白发了）。"""
    w, box = _capture_engine_worker_argv(monkeypatch, tmp_path, pool.SOURCE_BUNDLED)
    spec = pool._spawn_spec("fig.py", str(tmp_path), "draw", w.out_dir, w.sandbox,
                            w.log_path, "/usr/bin/python3", pool.SOURCE_BUNDLED)
    assert spec["argv"] == box["argv"]
    assert spec["argv"][1] == "-B"
    assert set(runtime.child_args()).issubset(spec["argv"])
    assert set(spec["env"]) == {"MPLCONFIGDIR", "PYTHONNOUSERSITE"}
    # env 只给增量：workerd 继承的本来就是 Flask 的环境
    assert "PATH" not in spec["env"]


def test_the_spec_hash_key_changes_with_entry_and_project(monkeypatch, tmp_path):
    """会话键 = spawn 规格：换 entry / 换项目就是另一条会话（argv 里都带着）。"""
    other = tmp_path / "other"
    other.mkdir()
    base = pool._spawn_spec("fig.py", str(tmp_path), "main", tmp_path / "o",
                            tmp_path / "s", tmp_path / "l", "py", pool.SOURCE_SYSTEM)
    entry = pool._spawn_spec("fig.py", str(tmp_path), "draw", tmp_path / "o",
                             tmp_path / "s", tmp_path / "l", "py", pool.SOURCE_SYSTEM)
    proj = pool._spawn_spec("fig.py", str(other), "main", tmp_path / "o",
                            tmp_path / "s", tmp_path / "l", "py", pool.SOURCE_SYSTEM)
    assert base["argv"] != entry["argv"] != proj["argv"]
    assert base["argv"] != proj["argv"]


# ------------------------------ 错误映射 ------------------------------
def test_missing_dependency_still_wins_over_the_supervisor_code():
    """缺包对用户是完全不同的一件事（有可执行出口），优先于协议 code。

    这条体验在 Python 池里是 `EngineWorker._error_of` 保的；换控制面不许回退。
    """
    tb = "Traceback…\nModuleNotFoundError: No module named 'rdkit.Chem'\n"
    err = pool._worker_error("脚本执行失败", "script_error", tb)
    assert err.code == "missing_dependency"
    assert err.module == "rdkit"
    assert "rdkit" in str(err)


def test_ordinary_errors_keep_their_code_and_traceback():
    err = pool._worker_error("stem 不存在: nope", "unknown_stem", "tb",
                            {"known": ["Fig1"]})
    assert err.code == "unknown_stem"
    assert err.traceback_text == "tb"
    assert err.extra["known"] == ["Fig1"]


class _FakeClient:
    """只实现 `WorkerdWorker` 用到的那一个方法。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def call(self, op, **kw):
        self.calls.append((op, kw))
        action = self.script.pop(0) if self.script else {"ok": True}
        if isinstance(action, Exception):
            raise action
        return action


def _worker(monkeypatch, tmp_path, script):
    monkeypatch.setattr(pool, "select_worker_python",
                        lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM))
    client = _FakeClient(script)
    return pool.WorkerdWorker("fig.py", str(tmp_path), "main", client=client), client


def test_fatal_codes_mark_the_session_dead_so_get_rebuilds(monkeypatch, tmp_path):
    """超时 / 崩溃 / 协议错乱之后 `alive()` 必须回 False。

    与 Python 池同一条纪律：状态未知的会话绝不复用，下一次 `get()` 原地重建。
    不标死的话上层会拿着一条已经被 workerd 杀掉的会话继续发请求。
    """
    for code in ("worker_timeout", "session_dead", "protocol_mismatch",
                 "spawn_failed", "handshake_timeout", "workerd_dead"):
        w, _ = _worker(monkeypatch, tmp_path, [
            {"ok": True, "session_id": "s-1"},
            workerd_client.WorkerdError("炸了", code=code, retryable=True),
        ])
        assert w.alive()
        with pytest.raises(pool.WorkerError) as e:
            w.ensure_built()
        assert e.value.code == code
        assert not w.alive(), f"{code} 之后会话必须被标死"


def test_transient_codes_do_not_kill_the_session(monkeypatch, tmp_path):
    """被顶替 / 被取消 / 队列满都是**正常的调度结果**，不是会话故障。"""
    for code in ("queue_superseded", "cancelled", "queue_full", "unknown_stem"):
        w, _ = _worker(monkeypatch, tmp_path, [
            {"ok": True, "session_id": "s-1"},
            workerd_client.WorkerdError("这条没跑", code=code),
        ])
        with pytest.raises(pool.WorkerError):
            w.ensure_built()
        assert w.alive(), f"{code} 不该把会话标死"


def test_an_expired_session_is_reopened_once_and_the_call_retried(monkeypatch, tmp_path):
    """workerd 重启过 → session_id 作废。对上层来说这只该是一次稍慢的渲染。"""
    w, client = _worker(monkeypatch, tmp_path, [
        {"ok": True, "session_id": "s-1"},
        workerd_client.WorkerdError("会话不存在", code="unknown_session"),
        {"ok": True, "session_id": "s-2"},
        {"ok": True, "stems": {"Fig1": {}}},
    ])
    assert w.ensure_built()["stems"] == {"Fig1": {}}
    assert [op for op, _ in client.calls] == [
        "open_session", "build", "open_session", "build"]
    assert w._session_id == "s-2"


def test_the_timeout_tier_travels_with_each_request(monkeypatch, tmp_path):
    """超时档位是 Flask 的策略：BUILD / REQUEST / EXPORT 各走各的，
    workerd 只负责按请求里带的那个执行。"""
    w, client = _worker(monkeypatch, tmp_path, [
        {"ok": True, "session_id": "s-1"},
        {"ok": True, "stems": {}},
        {"ok": True, "manifest": {}, "warnings": []},
        {"ok": True, "path": "/tmp/x.pdf", "warnings": []},
    ])
    w.ensure_built()
    w.override("Fig1", [])
    w.export("Fig1", [], "/tmp/x.pdf")
    tiers = {op: kw["timeout"] for op, kw in client.calls if op != "open_session"}
    assert tiers == {"build": pool.BUILD_TIMEOUT,
                     "render": pool.REQUEST_TIMEOUT,
                     "export": pool.EXPORT_TIMEOUT}


def test_the_pool_method_names_map_to_the_v1_command_names(monkeypatch, tmp_path):
    """池方法叫 override（app.py 一路这么叫），线上命令叫 render——
    映射只有一处，别在 workerd 那边再定义一次。"""
    w, client = _worker(monkeypatch, tmp_path, [
        {"ok": True, "session_id": "s-1"},
        {"ok": True, "stems": {}},
        {"ok": True, "manifest": {}, "warnings": []},
        {"ok": True, "path": "/tmp/a.png"},
        {"ok": True, "path": "/tmp/b.png"},
    ])
    w.ensure_built()
    w.override("Fig1", [{"gid": "g", "prop": "text", "value": "x"}])
    w.render_png("Fig1", 800)
    w.preview_png("Fig1", [], 400, "hist3")
    ops = [op for op, _ in client.calls]
    assert ops == ["open_session", "build", "render", "render_png", "preview_png"]
    payloads = {op: kw["payload"] for op, kw in client.calls}
    assert payloads["render_png"] == {"width": 800}
    assert payloads["preview_png"] == {"patches": [], "width": 400, "tag": "hist3"}
    assert w.rev == 1                       # render 之后前端缓存穿透用的版本 +1


def test_the_cache_layout_is_identical_to_the_python_pool(monkeypatch, tmp_path):
    """两条控制面共用同一套会话目录：`prune_engine_cache` 按 base 豁免正在用的
    会话，落点不一致的话清理会把正在写的 out/sandbox 删掉。"""
    monkeypatch.setattr(pool.subprocess, "Popen",
                        lambda *a, **k: type("P", (), {"pid": 1, "poll": lambda s: None})())
    monkeypatch.setattr(pool, "select_worker_python",
                        lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM))
    a = pool.EngineWorker("fig.py", str(tmp_path), "main")
    b, _ = _worker(monkeypatch, tmp_path, [{"ok": True, "session_id": "s-1"}])
    assert (b.base, b.out_dir, b.sandbox, b.log_path) == \
           (a.base, a.out_dir, a.sandbox, a.log_path)


def test_generation_still_increments_per_pool_key(monkeypatch, tmp_path):
    """`generation` 是**池的账本**（这个池键重建过几次），两条路径同源。"""
    w1, _ = _worker(monkeypatch, tmp_path, [{"ok": True, "session_id": "s-1"}])
    w2, _ = _worker(monkeypatch, tmp_path, [{"ok": True, "session_id": "s-2"}])
    assert w2.generation == w1.generation + 1


def test_a_hash_mismatch_is_logged_but_the_result_is_used(monkeypatch, tmp_path, caplog):
    """两侧规范化分叉只是警告：worker 照常执行了，用户什么都不该损失。"""
    w, _ = _worker(monkeypatch, tmp_path, [
        {"ok": True, "session_id": "s-1"},
        {"ok": True, "stems": {}},
        {"ok": True, "manifest": {"elements": []}, "warnings": [],
         "hash_mismatch": True, "canonical_patch_hash": "sha256:aaa",
         "worker_patch_hash": "sha256:bbb"},
    ])
    with caplog.at_level("WARNING", logger="mm.engine"):
        resp = w.override("Fig1", [{"gid": "g", "prop": "text", "value": "x"}])
    assert resp["manifest"] == {"elements": []}
    assert any("哈希不一致" in r.getMessage() for r in caplog.records)


def test_shutdown_and_force_kill_map_to_close_session(monkeypatch, tmp_path):
    """`shutdown_all(wait=True)` 的兜底硬杀在 workerd 侧是 force close——
    优雅关停要等在飞的活跑完，而死循环脚本正是等不到的那一类。"""
    w, client = _worker(monkeypatch, tmp_path, [
        {"ok": True, "session_id": "s-1"},
        {"ok": True, "closed": True},
    ])
    w.force_kill()
    op, kw = client.calls[-1]
    assert op == "close_session"
    assert kw["payload"] == {"force": True}
    assert not w.alive()


def test_subprocess_is_still_imported_for_the_python_path():
    """回退路径一行都没删——冒烟一下它还在。"""
    assert pool.subprocess is subprocess
    assert hasattr(pool.EngineWorker, "request")
    assert hasattr(pool.EngineWorker, "_readline")


def test_a_failed_open_claims_and_closes_the_ghost_session(monkeypatch, tmp_path):
    """open 失败时也要认领响应里的 session_id，并把那条会话关掉。

    workerd 在 `open_session` 的**那一刻**就把会话记进了 sessions / by_hash，
    握手或 spawn 失败时那条记录不会自己消失。失败响应里的 `session_id`
    （workerd 的 `.with_session()` 成功失败两条路都附）是唯一的线索——
    以前 `_call_on` 只拆 `resp["error"]`，把它丢了，于是那条会话谁也够不着：
    refs 停在 1，只能等超出 max_sessions 时被淘汰，而被挤掉的往往是**真正
    在用**的那条。
    """
    calls: list[dict] = []

    class FakeClient:
        def call(self, op, *, session_id=None, stem=None, payload=None,
                 timeout=None, slack=None):
            calls.append({"op": op, "session_id": session_id, "payload": payload})
            if op == "open_session":
                raise workerd_client.WorkerdError(
                    "握手超时", code="handshake_timeout", retryable=True,
                    session_id="s-7")
            return {"ok": True}

    monkeypatch.setattr(pool, "select_worker_python",
                        lambda *a, **k: ("/usr/bin/python3", pool.SOURCE_SYSTEM))
    with pytest.raises(pool.WorkerError):
        pool.WorkerdWorker("fig.py", str(tmp_path), "main",
                           client=FakeClient(), base_dir=tmp_path / "b")

    assert [c["op"] for c in calls] == ["open_session", "close_session"]
    assert calls[1]["session_id"] == "s-7"
    assert calls[1]["payload"] == {"force": True}


def test_cleanup_failure_never_hides_the_real_open_error(monkeypatch, tmp_path):
    """清理动作自己失败了，抛给调用方的仍必须是**真正的**失败原因。"""
    class FakeClient:
        def call(self, op, *, session_id=None, stem=None, payload=None,
                 timeout=None, slack=None):
            if op == "open_session":
                raise workerd_client.WorkerdError(
                    "起不来", code="spawn_failed", session_id="s-9")
            raise workerd_client.WorkerdError("workerd 也没了",
                                              code="workerd_unavailable")

    monkeypatch.setattr(pool, "select_worker_python",
                        lambda *a, **k: ("/usr/bin/python3", pool.SOURCE_SYSTEM))
    with pytest.raises(pool.WorkerError) as exc:
        pool.WorkerdWorker("fig.py", str(tmp_path), "main",
                           client=FakeClient(), base_dir=tmp_path / "b")
    assert exc.value.code == "spawn_failed"
