"""native 控制面的 HTTP 端点与**路由隔离**（ADR 0021 §4.1 / §9）。

两组判据：

* **可信 descriptor 边界**：网页只能提交 `native_id`；host / port / token /
  interpreter / 完整命令一律来自那份 0600 的文件。界面确认的是哪条
  invocation，执行端就只能执行那条；
* **safe 与 native 不串路由**：路由由 `execution_profile` 决定，不由"现在
  哪条路通"决定。静默 fallback 的表现是用户看到另一个环境生成的图，
  而界面什么都没说。
"""

from __future__ import annotations

import inspect
import json
import socket

import pytest

from tavotto import app as appmod
from tavotto.engine import (
    enginesession,
    nativehandoff,
    nativeperm,
    nativesession,
    pool as engine_pool,
    runcodes,
    runspec,
)
from tavotto.engine.runcodes import RunError

FIELDS = {
    "project_root": "/p",
    "interpreter": "/p/.venv/bin/python",
    "cwd": "/p",
    "target_kind": "script",
    "target_display": "figure.py",
    "arg_count": 2,
    "command_fingerprint": "f" * 32,
    "permission_key": "k" * 32,
    "python_version": "3.13.1",
    "attach_host": "127.0.0.1",
    "attach_port": 51234,
    "attach_token": "TOKEN-SHOULD-NEVER-LEAK",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def make_descriptor(**kw) -> str:
    native_id, _ = nativehandoff.create(**{**FIELDS, **kw})
    return native_id


# --------------------------------------------------------------------------
# 可信 descriptor 边界
# --------------------------------------------------------------------------
def test_pending_never_returns_the_token_or_the_port(client):
    native_id = make_descriptor()
    resp = client.get(f"/api/native/pending/{native_id}")
    assert resp.status_code == 200
    blob = resp.get_data(as_text=True)
    assert FIELDS["attach_token"] not in blob
    assert "51234" not in blob and "attach_port" not in blob
    body = json.loads(blob)["pending"]
    assert body["interpreter"] == FIELDS["interpreter"]  # 该有的确实有
    assert body["remembered"] is False  # 默认不勾"记住"


def test_a_bad_native_id_is_a_404_with_a_stable_code(client):
    """格式不合法的 ID **在最外层**被判掉——不是 500，也不是一句"未知错误"。"""
    for bad in ("not-hex", "0" * 31, "0" * 33, "0123456789ABCDEF0123456789abcdef"):
        resp = client.get(f"/api/native/pending/{bad}")
        assert resp.status_code == 404, bad
        assert json.loads(resp.get_data(as_text=True))["code"] == (
            runcodes.NATIVE_HANDOFF_INVALID
        ), bad


def test_a_traversal_shaped_id_never_reaches_a_file(client, tmp_path):
    """路径穿越形状的 ID：不管是被路由挡掉还是被格式判据挡掉，**都不能是 200**。

    判据刻意不钉"由谁挡的"：Flask 的路由与我们的 `_ID_RE` 都会挡，而哪一层
    先起作用是实现细节；钉死它只会在某次路由重构里变成假红。
    """
    for bad in ("..%2f..%2fetc%2fpasswd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "a/../../etc"):
        resp = client.get(f"/api/native/pending/{bad}")
        assert resp.status_code != 200, bad


def test_a_consumed_descriptor_is_409_with_its_own_code(client, monkeypatch):
    native_id = make_descriptor()
    monkeypatch.setattr(
        nativesession.REGISTRY, "attach", lambda *_a, **_k: _fake_session(native_id)
    )
    assert client.post(f"/api/native/pending/{native_id}/approve").status_code == 200
    again = client.post(f"/api/native/pending/{native_id}/approve")
    assert again.status_code == 409
    assert json.loads(again.get_data(as_text=True))["code"] == runcodes.NATIVE_HANDOFF_CONSUMED


def test_cancel_makes_the_descriptor_unusable(client):
    native_id = make_descriptor()
    assert client.post(f"/api/native/pending/{native_id}/cancel").status_code == 200
    resp = client.post(f"/api/native/pending/{native_id}/approve")
    assert resp.status_code == 409
    assert json.loads(resp.get_data(as_text=True))["code"] == runcodes.NATIVE_ATTACH_CANCELLED


def test_the_request_body_cannot_replace_the_invocation(client, monkeypatch):
    """前端**不得**在确认之后换掉解释器 / 目标 / 端点（Session 7B 的
    plan-binding 同款纪律）。

    判据是：请求体里塞进那几个字段，attach 拿到的 descriptor 一个字节没变。
    """
    native_id = make_descriptor()
    seen: dict = {}

    def _attach(descriptor, **_kw):
        seen["descriptor"] = descriptor
        return _fake_session(native_id)

    monkeypatch.setattr(nativesession.REGISTRY, "attach", _attach)
    client.post(
        f"/api/native/pending/{native_id}/approve",
        json={
            "interpreter": "/evil/python",
            "relay": {"host": "10.0.0.1", "attach_port": 9},
            "attach_token": "attacker",
            "metadata": {"interpreter": "/evil/python"},
            "target_display": "evil.py",
        },
    )
    got = seen["descriptor"]
    assert got["metadata"]["interpreter"] == FIELDS["interpreter"]
    assert got["relay"] == {"host": "127.0.0.1", "attach_port": 51234}
    assert got["attach_token"] == FIELDS["attach_token"]
    assert got["metadata"]["target_display"] == "figure.py"


def test_remember_is_opt_in_and_scoped(client, monkeypatch, tmp_path):
    native_id = make_descriptor(project_root=str(tmp_path))
    monkeypatch.setattr(
        nativesession.REGISTRY, "attach", lambda *_a, **_k: _fake_session(native_id)
    )
    client.post(f"/api/native/pending/{native_id}/approve")  # 不带 remember
    assert nativeperm.listing(str(tmp_path)) == []

    other = make_descriptor(project_root=str(tmp_path))
    client.post(f"/api/native/pending/{other}/approve", json={"remember": True})
    listed = nativeperm.listing(str(tmp_path))
    assert len(listed) == 1
    assert listed[0]["permission_key"] == FIELDS["permission_key"]
    assert listed[0]["interpreter"] == FIELDS["interpreter"]
    assert nativeperm.is_remembered(str(tmp_path), FIELDS["permission_key"]) is True
    # 另一个项目不受影响
    assert nativeperm.is_remembered(str(tmp_path / "elsewhere"), FIELDS["permission_key"]) is False


def test_a_permission_can_be_revoked(tmp_path):
    """一次"记住"如果没有对称的"忘掉"，它就不是一个可以放心点的选项。"""
    nativeperm.remember(str(tmp_path), "kk", interpreter="/p/python")
    assert nativeperm.is_remembered(str(tmp_path), "kk")
    assert nativeperm.forget(str(tmp_path), "kk") == 1
    assert not nativeperm.is_remembered(str(tmp_path), "kk")


def test_a_schema_bump_invalidates_old_permissions(tmp_path, monkeypatch):
    """许可绑定的维度变了 → 旧的那次点击不是对新含义的授权。"""
    nativeperm.remember(str(tmp_path), "kk", interpreter="/p/python")
    monkeypatch.setattr(runspec, "PERMISSION_SCHEMA", runspec.PERMISSION_SCHEMA + 1)
    assert not nativeperm.is_remembered(str(tmp_path), "kk")


def test_unknown_session_is_404(client):
    resp = client.get("/api/native/sessions/native-nope")
    assert resp.status_code == 404
    assert json.loads(resp.get_data(as_text=True))["code"] == runcodes.NATIVE_SESSION_UNKNOWN


# --------------------------------------------------------------------------
# safe ↔ native 不串路由
# --------------------------------------------------------------------------
def test_a_native_panel_never_falls_back_to_a_safe_worker(monkeypatch):
    """**没有 live 会话 = offline，不是"换个 worker 跑一遍"**（ADR 0021 §9.1）。

    退回 safe 的表现是：用户的 conda 环境里 matplotlib 是 3.7、Tavotto 内置的
    是 3.10，同一个脚本出来的图肉眼可见地不同——而界面上什么都没说。
    """
    called: list = []
    monkeypatch.setattr(enginesession.pool, "get", lambda *a, **k: called.append(a) or object())
    with pytest.raises(RunError) as exc:
        enginesession.resolve(
            project_root="/p",
            script="figure.py",
            entry="__main__",
            stem="Fig1",
            execution_profile=enginesession.PROFILE_NATIVE,
        )
    assert exc.value.code == runcodes.NATIVE_SESSION_OFFLINE
    assert called == [], "native 面板悄悄用了 safe worker"


def test_a_safe_panel_never_switches_to_a_live_native_session(monkeypatch):
    """反方向同理：profile 是**面板的属性**，不是"现在哪条路通"。"""
    sentinel = object()
    monkeypatch.setattr(enginesession.pool, "get", lambda *a, **k: sentinel)
    route = []
    monkeypatch.setattr(
        enginesession.nativesession.REGISTRY,
        "route_for",
        lambda *a, **k: route.append(a) or object(),
    )
    got = enginesession.resolve(
        project_root="/p",
        script="figure.py",
        entry="__main__",
        stem="Fig1",
        execution_profile=enginesession.PROFILE_SAFE,
    )
    assert got is sentinel
    assert route == [], "safe 面板去问了 native 路由表"


def test_profile_defaults_to_safe_when_the_cache_says_nothing(tmp_path):
    """**未知不等于 native**：把未知当 native 会让一个普通面板在没有会话时
    直接报 offline，而它本来 safe 就能渲染。"""
    assert enginesession.profile_of(tmp_path, "runtime:figure.py#Fig1") == (
        enginesession.PROFILE_SAFE
    )


def test_the_resolver_is_the_only_place_that_branches():
    """**结构性守卫**：`app.py` 里不许再出现第二处 native/safe 分支。

    第一处漏掉的形状是仓库里出现过三次的那个：共享判据修了一处、第二个
    消费点还是老样子——表现是"预览是 native 的、导出是 safe 的"。
    """
    src = __import__("pathlib").Path(appmod.__file__).read_text(encoding="utf-8")
    hits = [
        line.strip()
        for line in src.splitlines()
        if "engine_pool.get(" in line and "engine_enginesession" not in line
    ]
    assert hits == [], f"app.py 里还有绕过 resolve() 的 pool.get: {hits}"


# --------------------------------------------------------------------------
# worker-like 面：两种对象必须长成一样
# --------------------------------------------------------------------------
def _provides(cls, name: str) -> bool:
    """类上有（方法 / property），或者 `__init__` 给实例装了这个属性。

    只查类是不够的：`pool.EngineWorker` 的 `built` / `export_dir` /
    `last_build_descriptors` 全是 `__init__` 里赋的实例属性，`hasattr(cls, …)`
    一律 False。构造一个真 worker 来查又要 mkdir、开日志文件、算 generation
    ——判据不该带这些副作用，所以查 `__init__` 字节码里的属性名。
    """
    if hasattr(cls, name):
        return True
    code = getattr(getattr(cls, "__init__", None), "__code__", None)
    return code is not None and name in code.co_names


@pytest.mark.parametrize("cls", [engine_pool.EngineWorker, nativesession.NativeSession])
def test_both_worker_kinds_provide_the_whole_worker_like_surface(cls):
    """**结构性守卫**：`resolve()` 回的两种对象都必须盖住 `WORKER_LIKE`。

    这条用例存在是因为它真的漏过一次：契约原来只列**方法名**
    （`ensure_built` / `override` / …），而 `app.py` 同时还读三个**属性**
    ——`built`（冷启动判据）、`export_dir`（画布导出落点）、
    `last_build_descriptors`（runtime cache 物化）——`NativeSession` 一个
    都没有。表现是 native 面板一进 `/api/engine/render` 就 AttributeError，
    而"两种对象共享同一批成员"这句话就明明白白写在 `resolve()` 的 docstring
    里。散在文档里的清单只约束读到它的人；这一条让它每次都被跑一遍。
    """
    missing = [n for n in enginesession.WORKER_LIKE if not _provides(cls, n)]
    assert missing == [], f"{cls.__name__} 缺 worker-like 成员: {missing}"


def test_the_render_call_shape_fits_both_worker_kinds():
    """成员名对上**还不够**：`app.py:2530` 发的是
    `wk.override(st, patches, preview_dpi, inline_svg=…)`——第三个是**位置**
    参数。`NativeSession.override(self, stem, patches, **kw)` 名字在、签名不在，
    照样每次渲染 TypeError。名字与调用形状是两件事，分别钉。
    """
    for cls in (engine_pool.EngineWorker, nativesession.NativeSession):
        inspect.signature(cls.override).bind(
            object(),  # self
            "Fig1",
            [{"gid": "g", "prop": "text", "value": "x"}],
            200,
            inline_svg=True,
        )


def test_a_native_session_answers_the_attributes_the_routes_read(tmp_path):
    """在**真对象**上跑一遍那三个属性——签名对了不等于值对了。"""
    out = tmp_path / "out"
    session = nativesession.NativeSession(
        session_id="native-x",
        descriptor={"native_id": "x" * 32, "metadata": FIELDS, "out_dir": str(out)},
    )
    assert session.built is False, "还没 build 就说 build 过了，冷启动提示会消失"
    assert session.last_build_descriptors == []

    # 导出临时件单独一层：`out_dir` 是会话自己的产出面（runner 往里写
    # `{stem}.svg`，`runcli._figures_written()` 还要扫它数图）。
    assert session.export_dir.is_dir()
    assert session.export_dir.parent == out

    session.descriptors = [{"stem": "Fig1"}]
    assert session.last_build_descriptors == [{"stem": "Fig1"}], "别名与存储分叉了"


# --------------------------------------------------------------------------
def _fake_session(native_id: str):
    """一条不连真 socket 的会话（端点判据不需要真进程）。"""
    session = nativesession.NativeSession(
        session_id=f"native-{native_id}",
        descriptor={"native_id": native_id, "metadata": FIELDS, "out_dir": "/tmp/x"},
    )
    return session


def test_the_registry_attach_really_authenticates(tmp_path, monkeypatch):
    """端点用的是真 `attach`——这条证明那条路径确实会认证。

    上面几条端点判据把 `attach` 换成了假的（它们量的是 HTTP 层）；如果没有
    这一条，"attach 会不会认证"就变成了没人问过的事。
    """
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    import threading

    def _serve():
        conn, _ = listener.accept()
        conn.recv(4096)
        conn.sendall(b'{"ok":false,"code":"native_auth_failed"}\n')
        conn.close()

    threading.Thread(target=_serve, daemon=True).start()
    native_id = make_descriptor(attach_port=port)
    with pytest.raises(RunError) as exc:
        nativesession.REGISTRY.attach(nativehandoff.consume(native_id))
    assert exc.value.code == runcodes.NATIVE_AUTH_FAILED
    listener.close()
