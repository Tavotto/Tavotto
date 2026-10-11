"""文档恢复可靠性的后端**正确行为验收**（QA，2026-10-11；任务书 9.1 的 R3/R6/A24–A26/A29/C34 等）。

审计夹具（`reproduce_backend.py`）断言的是旧缺陷（`session == {"last": None}`、`owner == "other"`）；
这里每条翻成修复后应成立的断言。**真实 Flask test client + 隔离 data_dir**，正文走生产 validator，
并能从 autosave GET 读回；故障与并发用受控注入（monkeypatch / 事件）。

只通过公共 HTTP 端点与状态文件字节驱动和观察，不依赖 `layoutsession` 的私有形状——所以同一份
用例在未修复代码上的红来自目标行为。新合同才有的字段 / 端点（`state`、`metadata`、`verified`、
`prev`、`pending`、`index`、`/candidates`、`.bak` 回退）放在 `TestPostFix*` 里，**不计红证据**。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import documents, layoutsession, project_watch as engine_watch

DOC_ID = "d_recoverable"

DOCUMENT = {
    "schema": 3,
    "project": {"id": "p_document_identity", "name": "Edited PRISMA figure"},
    "canvases": [
        {
            "id": "c1",
            "name": "Fig 1",
            "page": {"w": 360, "h": 300},
            "objects": [
                {
                    "id": "panel_1",
                    "type": "panel",
                    "fileId": "runtime:fig1.py#Fig1_PRISMA_Flowchart",
                    "fileKind": "runtime",
                    "script": "fig1.py",
                    "source": {
                        "script": "fig1.py",
                        "entry": "__main__",
                        "stem": "Fig1_PRISMA_Flowchart",
                        "captureSource": "savefig",
                        "fingerprint": "sha256:"
                        + hashlib.sha256(b"print('figure source')\n").hexdigest(),
                        "sizeMm": [330.2, 279.4],
                    },
                    "x": 5,
                    "y": 6,
                    "w": 330.2,
                    "h": 279.4,
                    "overrides": [{"gid": "figure", "prop": "facecolor", "value": "#fff"}],
                }
            ],
            "guides": [],
        }
    ],
    "activeCanvasId": "c1",
    "createdAt": 1,
    "updatedAt": 2,
}
documents.validate_document(copy.deepcopy(DOCUMENT))


def doc(*, x: float = 5, updated: int = 2, name: str | None = None) -> dict:
    d = copy.deepcopy(DOCUMENT)
    d["canvases"][0]["objects"][0]["x"] = x
    d["updatedAt"] = updated
    if name:
        d["project"]["name"] = name
    return d


# ------------------------------- 夹具（与 test_layout_session 同形） -------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setattr(m, "LAYOUT_DIR", d / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    return d


@pytest.fixture
def client(data_dir):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _project(client, tmp_path, name: str, default: bool = False) -> dict:
    root = tmp_path / name
    root.mkdir()
    (root / "fig1.py").write_bytes(b"print('figure source')\n")
    body = client.post(
        "/api/projects/open", json={"path": str(root), "default": default}
    ).get_json()
    assert body.get("id"), body
    return body


def _pj(pid: str) -> dict:
    return {"X-Tavotto-Project": pid}


def _save(client, pid, doc_id=DOC_ID, body=None, base_revision=None):
    kwargs = {"query_string": {"base_revision": base_revision}} if base_revision else {}
    r = client.put(f"/api/autosave/{doc_id}", json=body or doc(), headers=_pj(pid), **kwargs)
    assert r.status_code == 200, r.get_json()
    return r


def _put_last(client, pid, doc_id, name="n"):
    return client.put(
        "/api/layout-session/last", json={"doc_id": doc_id, "name": name}, headers=_pj(pid)
    )


def _session(client, pid) -> dict:
    r = client.get("/api/layout-session", headers=_pj(pid))
    assert r.status_code == 200
    return r.get_json()


def _owner(client, pid, doc_id=DOC_ID) -> dict:
    r = client.get(f"/api/layout-session/owner?doc_id={doc_id}", headers=_pj(pid))
    assert r.status_code == 200
    return r.get_json()


def _readable(client, pid, doc_id) -> bool:
    r = client.get(f"/api/autosave/{doc_id}", headers=_pj(pid))
    if r.status_code != 200:
        return False
    documents.validate_document(r.get_json())
    return True


def _state_dir() -> Path:
    return layoutsession.state_path().parent


# ================================ R3 / A29：last 只指向可恢复正文 ================================


def test_early_last_does_not_dangle_when_an_older_durable_last_exists(client, tmp_path):
    """旧有效 A + 新 B 的 early last（B 的槽位还没有正文）：读出来的 last 不能是读不到的 B。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    assert _put_last(client, p["id"], "d_a", "A").status_code == 200
    r = _put_last(client, p["id"], "d_b", "B")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    last = _session(client, p["id"])["last"]
    assert last is not None and last["doc_id"] == "d_a", "last 悬空到了不存在的 d_b（R3）"
    assert _readable(client, p["id"], last["doc_id"])


def test_early_last_with_no_prior_durable_last_does_not_dangle(client, tmp_path):
    p = _project(client, tmp_path, "proj", default=True)
    assert _put_last(client, p["id"], "d_b", "B").status_code == 200
    last = _session(client, p["id"])["last"]
    assert last is None or _readable(client, p["id"], last["doc_id"]), "last 指向不可读的槽位（R3）"


def test_old_frontend_early_last_then_autosave_ends_with_a_readable_last(client, tmp_path):
    """A29：旧前端顺序 = 先 PUT last、再首次 autosave。中间任何时刻读到的 last 都可读；首次保存后是 B。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    _put_last(client, p["id"], "d_a", "A")
    r = _put_last(client, p["id"], "d_b", "B")
    assert r.status_code == 200  # 旧前端只看 2xx 与 last.at
    mid = _session(client, p["id"])["last"]
    assert mid is not None and _readable(client, p["id"], mid["doc_id"]), (
        "early-last 之后到首次保存之前 last 悬空"
    )
    _save(client, p["id"], "d_b", doc(name="B"))
    end = _session(client, p["id"])["last"]
    assert end is not None and end["doc_id"] == "d_b" and _readable(client, p["id"], "d_b")


def test_normal_save_then_last_is_readable_control(client, tmp_path):
    """对照（基线应绿）：先保存、再记 last——这是最常见的正常顺序。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    assert _put_last(client, p["id"], "d_a", "A").status_code == 200
    last = _session(client, p["id"])["last"]
    assert last["doc_id"] == "d_a" and _readable(client, p["id"], "d_a")


def test_a_late_save_of_an_older_document_does_not_pull_last_back(client, tmp_path):
    """A11 后端半边：B 已经是 last；A 的迟到保存晚于 B 的提交到达，不能把 last 拉回 A。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    _put_last(client, p["id"], "d_a", "A")
    _put_last(client, p["id"], "d_b", "B")  # 打开意图
    _save(client, p["id"], "d_b", doc(name="B"))  # B 的首次正文
    assert _session(client, p["id"])["last"]["doc_id"] == "d_b"
    _save(client, p["id"], "d_a", doc(x=99, updated=9))  # A 迟到的保存
    last = _session(client, p["id"])["last"]
    assert last["doc_id"] == "d_b", "A 的迟到保存把 last 拉回了 A"


# ================================ R6 / A24：owner 与正文同序 ================================


def _hold_first_owner_commit(
    monkeypatch, pid_to_hold: str, entered: threading.Event, release: threading.Event
):
    """让 `pid_to_hold` 的那次请求停在「owner 提交」入口（旧实现 `_record_autosave_owner`，新实现
    `layoutsession.commit_slot`），最多等 1.5 s。新实现里这一步在文档锁内，对方的 PUT 会被挡住——
    等待超时后继续，这正是「新写必须等旧写的元数据提交完」的可观察形态。"""
    fired = threading.Event()

    def wrap(orig):
        def hook(*args, **kwargs):
            if not fired.is_set() and m.request.headers.get("X-Tavotto-Project") == pid_to_hold:
                fired.set()
                entered.set()
                release.wait(timeout=1.5)
            return orig(*args, **kwargs)

        return hook

    if hasattr(m, "_record_autosave_owner"):
        monkeypatch.setattr(m, "_record_autosave_owner", wrap(m._record_autosave_owner))
    if hasattr(layoutsession, "commit_slot"):
        monkeypatch.setattr(layoutsession, "commit_slot", wrap(layoutsession.commit_slot))


def test_delayed_owner_update_of_an_older_request_cannot_reassign_newer_body(
    client, tmp_path, monkeypatch
):
    """F、G 两个项目保存同一个 docId，各带合法 base_revision；F 的 owner 提交被卡到 G 的正文与 last 之后。

    最终：正文属于最新写入（G）；owner 不能被改回 F，G 的 last 不能被过滤成空——
    或者明确处于待核对（unknown），但绝不是 `other`。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _save(client, f["id"])
    baseline = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(f["id"]))
    entered, g_done = threading.Event(), threading.Event()
    _hold_first_owner_commit(monkeypatch, f["id"], entered, g_done)
    f_status: list[int] = []

    def f_request():
        with m.app.test_client() as c:
            r = c.put(
                f"/api/autosave/{DOC_ID}",
                query_string={"base_revision": baseline.headers["X-Tavotto-Revision"]},
                json=doc(x=10, updated=3),
                headers=_pj(f["id"]),
            )
            f_status.append(r.status_code)

    t = threading.Thread(target=f_request)
    t.start()
    assert entered.wait(timeout=5), "F 的 owner 提交点没有被走到（钩子没落在目标位置）"
    # G 合法地读到 F 已提交的正文与 revision，再写自己的
    g_read = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(g["id"]))
    assert g_read.status_code == 200
    g_put = client.put(
        f"/api/autosave/{DOC_ID}",
        query_string={"base_revision": g_read.headers["X-Tavotto-Revision"]},
        json=doc(x=20, updated=4, name="Edited in G"),
        headers=_pj(g["id"]),
    )
    assert g_put.status_code == 200, g_put.get_json()
    assert _put_last(client, g["id"], DOC_ID, "Edited in G").status_code == 200
    g_done.set()
    t.join(timeout=10)
    assert not t.is_alive() and f_status == [200]

    latest = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(g["id"])).get_json()
    assert latest["updatedAt"] == 4 and latest["canvases"][0]["objects"][0]["x"] == 20
    owner_g = _owner(client, g["id"])
    assert owner_g["owner"] in ("this", "unknown"), (
        f"旧请求的 owner 把最新正文改回了别的项目：{owner_g}"
    )
    last_g = _session(client, g["id"])["last"]
    assert last_g is not None and last_g["doc_id"] == DOC_ID, "G 的 last 被过滤成空（owner 倒退）"
    assert _owner(client, f["id"])["owner"] != "this", "F 仍被当成这份最新正文的主人"


# ================================ A25：正文成功、元数据失败 ================================


def test_metadata_failure_keeps_the_body_200_with_the_true_revision(client, tmp_path, monkeypatch):
    p = _project(client, tmp_path, "proj", default=True)

    def boom(*_a, **_k):
        raise OSError("simulated metadata I/O failure")

    with monkeypatch.context() as mp:
        mp.setattr(layoutsession, "_write", boom)
        r = client.put(f"/api/autosave/{DOC_ID}", json=doc(), headers=_pj(p["id"]))
    assert r.status_code == 200, "元数据写失败不能让正文保存失败"
    got = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(p["id"]))
    assert got.status_code == 200
    documents.validate_document(got.get_json())
    assert r.get_json()["revision"] == got.headers["X-Tavotto-Revision"], (
        "回给前端的 revision 不是正文的真实 revision"
    )


def test_metadata_failure_does_not_leave_the_previous_owner_trusted(client, tmp_path, monkeypatch):
    """F 保存并记了 owner；G 之后覆盖同一槽位但元数据写失败。F 的旧 owner 记录不能被盲信为「这份正文是 F 的」。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _save(client, f["id"])
    assert _owner(client, f["id"])["owner"] == "this"
    got = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(g["id"]))

    def boom(*_a, **_k):
        raise OSError("simulated metadata I/O failure")

    with monkeypatch.context() as mp:
        mp.setattr(layoutsession, "_write", boom)
        r = client.put(
            f"/api/autosave/{DOC_ID}",
            query_string={"base_revision": got.headers["X-Tavotto-Revision"]},
            json=doc(x=77, updated=8, name="G body"),
            headers=_pj(g["id"]),
        )
    assert r.status_code == 200
    verdict = _owner(client, f["id"])
    trusted = verdict["owner"] == "this" and verdict.get("verified", True)
    assert not trusted, f"正文已被 G 覆盖，F 的旧 owner 记录仍被当作已核对的归属：{verdict}"


# ================================ A26：会话文件缺失 / 损坏 / 无关字段 / I/O / 未知格式 ================================


def _seed_state(client, tmp_path):
    """两个项目、各有自己的 last 与 owner；返回 (p, q, 状态文件当前字节)。"""
    p = _project(client, tmp_path, "projP", default=True)
    q = _project(client, tmp_path, "projQ")
    for proj, doc_id in ((p, "d_p"), (q, "d_q")):
        _save(client, proj["id"], doc_id)
        assert _put_last(client, proj["id"], doc_id, doc_id).status_code == 200
    return p, q, layoutsession.state_path().read_bytes()


def _any_state_file_has_bytes(raw: bytes) -> bool:
    return any(f.is_file() and f.read_bytes() == raw for f in _state_dir().iterdir())


def test_corrupt_state_bytes_are_preserved_before_the_next_write(client, tmp_path):
    p, _q, _good = _seed_state(client, tmp_path)
    corrupt = b"{not json"
    layoutsession.state_path().write_bytes(corrupt)
    _save(client, p["id"], "d_new")  # 触发一次会话状态写入
    assert _any_state_file_has_bytes(corrupt), "坏会话文件被下一次普通写入无声抹平，原字节没有保留"


def test_unrelated_nan_does_not_wipe_other_groups_and_owners(client, tmp_path):
    p, q, _good = _seed_state(client, tmp_path)
    state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    state["unrelated_note"] = "__NAN__"
    raw = json.dumps(state).replace('"__NAN__"', "NaN").encode("utf-8")
    layoutsession.state_path().write_bytes(raw)
    # 读：无关字段的 NaN 不能让整份作废
    last = _session(client, p["id"])["last"]
    assert last is not None and last["doc_id"] == "d_p", "无关字段 NaN 让整份会话状态作废"
    _save(client, p["id"], "d_new")
    after = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    assert after["owners"].get("d_q") == layoutsession.project_key(q["figures_dir"]), (
        "其他项目的 owner 被抹掉"
    )
    assert after["projects"][layoutsession.project_key(q["figures_dir"])]["last"]["doc_id"] == "d_q"


def test_unknown_keys_survive_the_next_write(client, tmp_path):
    p, _q, _good = _seed_state(client, tmp_path)
    state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    state["future_feature"] = {"keep": "me"}
    key = layoutsession.project_key(p["figures_dir"])
    state["projects"][key]["future_group_field"] = 7
    layoutsession.state_path().write_text(json.dumps(state), encoding="utf-8")
    _save(client, p["id"], "d_new")
    after = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    assert after.get("future_feature") == {"keep": "me"}, "未知顶层键被丢"
    assert after["projects"][key].get("future_group_field") == 7, "组内未知键被丢"


def test_unreadable_state_file_is_never_overwritten_by_an_empty_state(
    client, tmp_path, monkeypatch
):
    """读失败（没拿到字节）≠ 首次空状态：不能用空状态整份覆盖一份可能完整的文件。"""
    p, _q, good = _seed_state(client, tmp_path)
    path = layoutsession.state_path()
    real = Path.read_bytes
    hits = {"n": 0}

    def flaky(self, *a, **k):
        if self == path:
            hits["n"] += 1
            raise OSError("simulated EIO")
        return real(self, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "read_bytes", flaky)
        r = client.put("/api/autosave/d_new", json=doc(), headers=_pj(p["id"]))
        assert r.status_code == 200, "状态文件读不动时正文保存仍应成功"
        _put_last(client, p["id"], "d_new", "new")  # 结果（2xx 或 5xx）不限，但不能毁掉文件
    assert hits["n"] > 0, "故障注入没有命中状态文件读取（QA 钩子需要对齐实现）"
    assert path.read_bytes() == good, "读失败后状态文件被改写了（可能用空状态覆盖了完整文件）"


def test_future_version_state_is_not_rewritten(client, tmp_path):
    p, _q, _good = _seed_state(client, tmp_path)
    state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
    state["version"] = 99
    raw = json.dumps(state).encode("utf-8")
    layoutsession.state_path().write_bytes(raw)
    r = client.put("/api/autosave/d_new", json=doc(), headers=_pj(p["id"]))
    assert r.status_code == 200
    _put_last(client, p["id"], "d_new", "new")
    assert layoutsession.state_path().read_bytes() == raw, "未来格式的状态文件被旧逻辑重写"


# ================================ C34：两个真进程共享 data_dir ================================

_WRITER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from tavotto.engine import layoutsession as ls

    role, go, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    deadline = time.time() + 20
    while not go.exists():
        if time.time() > deadline:
            sys.exit(3)
        time.sleep(0.0005)
    key = "/proj/" + role
    for i in range(n):
        doc_id = f"d_{role}{i}"
        ls.record_owner(doc_id, key)
        try:
            ls.set_last(key, doc_id, f"L{i}", slot_ok=True)
        except TypeError:
            ls.set_last(key, doc_id, f"L{i}")
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 真两进程；Windows 腿留给 CI")
def test_two_processes_sharing_a_data_dir_lose_neither_last_nor_owner(data_dir, tmp_path):
    n = 40
    go = tmp_path / "go"
    src = str(Path(layoutsession.__file__).resolve().parents[2])
    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "PYTHONPATH": src,
        "TAVOTTO_NO_TELEMETRY": "1",
    }
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, role, str(go), str(n)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for role in ("a", "b")
    ]
    go.write_text("go")
    outs = []
    for proc in procs:
        out, _ = proc.communicate(timeout=120)
        outs.append(out.decode("utf-8", "replace"))
        assert proc.returncode == 0, out.decode("utf-8", "replace")
    owners = layoutsession.owners()
    expected = {f"d_{role}{i}": f"/proj/{role}" for role in ("a", "b") for i in range(n)}
    missing = sorted(set(expected) - set(owners))
    assert not missing, f"跨进程读改写丢了 {len(missing)} 条 owner（例如 {missing[:3]}）"
    for role in ("a", "b"):
        last = layoutsession.last_for(f"/proj/{role}")
        assert last is not None and last["doc_id"] == f"d_{role}{n - 1}", (
            f"进程 {role} 的 last 被另一个进程覆盖：{last}"
        )


# ============================ 修复后新增（新合同的字段 / 端点，不计红证据） ============================


class TestPostFixContract:
    def test_pending_state_and_response_shape(self, client, tmp_path):
        p = _project(client, tmp_path, "proj", default=True)
        _save(client, p["id"], "d_a")
        _put_last(client, p["id"], "d_a", "A")
        r = _put_last(client, p["id"], "d_b", "B")
        body = r.get_json()
        assert body["state"] == "pending"
        assert body["last"]["doc_id"] == "d_a"
        sess = _session(client, p["id"])
        assert sess["last"]["doc_id"] == "d_a"
        assert sess["pending"]["doc_id"] == "d_b"
        assert sess["index"] in ("ok", "missing", "degraded", "recovered")
        _save(client, p["id"], "d_b", doc(name="B"))
        sess = _session(client, p["id"])
        assert sess["last"]["doc_id"] == "d_b" and sess["prev"]["doc_id"] == "d_a"
        assert sess["pending"] is None

    def test_autosave_put_reports_metadata_state(self, client, tmp_path, monkeypatch):
        p = _project(client, tmp_path, "proj", default=True)
        assert _save(client, p["id"]).get_json()["metadata"] == "ok"

        def boom(*_a, **_k):
            raise OSError("simulated")

        with monkeypatch.context() as mp:
            mp.setattr(layoutsession, "_write", boom)
            r = client.put("/api/autosave/d_other", json=doc(), headers=_pj(p["id"]))
        assert r.status_code == 200 and r.get_json()["metadata"] == "failed"

    def test_owner_endpoint_has_verified_flag(self, client, tmp_path):
        p = _project(client, tmp_path, "proj", default=True)
        _save(client, p["id"])
        revision = client.get(f"/api/autosave/{DOC_ID}", headers=_pj(p["id"])).headers[
            "X-Tavotto-Revision"
        ]
        assert _owner(client, p["id"]) == {
            "owner": "this",
            "evidence": "owners",
            "verified": True,
            "revision": revision,
        }
        # 没有槽位：revision 是 null
        assert _owner(client, p["id"], "d_no_such_slot")["revision"] is None

    def test_corrupt_state_falls_back_to_the_last_good_backup(self, client, tmp_path):
        p, _q, _good = _seed_state(client, tmp_path)
        layoutsession.state_path().write_bytes(b"{not json")
        sess = _session(client, p["id"])
        assert sess["index"] == "recovered"
        assert sess["last"]["doc_id"] == "d_p"

    def test_future_version_state_reads_as_degraded(self, client, tmp_path):
        p, _q, _good = _seed_state(client, tmp_path)
        state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
        state["version"] = 99
        layoutsession.state_path().write_text(json.dumps(state), encoding="utf-8")
        assert _session(client, p["id"])["index"] == "degraded"


class TestPostFixDiscovery:
    """A14 / A15 / A30：只读候选发现（新端点；基线 404，不计红证据）。"""

    def _candidates(self, client, pid, scope="project", **extra):
        r = client.get(
            "/api/layout-session/candidates",
            query_string={"scope": scope, **extra},
            headers=_pj(pid),
        )
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    def test_owners_without_last_are_discoverable_and_nothing_is_written(self, client, tmp_path):
        p = _project(client, tmp_path, "proj", default=True)
        _save(client, p["id"])
        state = json.loads(layoutsession.state_path().read_text(encoding="utf-8"))
        state["projects"] = {}
        layoutsession.state_path().write_text(json.dumps(state), encoding="utf-8")
        slot = m.AUTOSAVE_DIR / f"{DOC_ID}.json"
        before = (slot.read_bytes(), layoutsession.state_path().read_bytes())
        res = self._candidates(client, p["id"])
        item = next(i for i in res["items"] if i["doc_id"] == DOC_ID)
        assert (
            item["status"] == "ok"
            and item["owner"] == "this"
            and item["canvases"] == 1
            and item["objects"] == 1
        )
        assert (slot.read_bytes(), layoutsession.state_path().read_bytes()) == before

    def test_pure_runtime_document_without_any_metadata_is_listed_as_unknown(
        self, client, tmp_path
    ):
        p = _project(client, tmp_path, "proj", default=True)
        _save(client, p["id"])
        layoutsession.state_path().unlink()
        assert not any(
            i["doc_id"] == DOC_ID for i in self._candidates(client, p["id"], "project")["items"]
        )
        item = next(
            i
            for i in self._candidates(client, p["id"], "unknown")["items"]
            if i["doc_id"] == DOC_ID
        )
        assert item["owner"] == "unknown"
        assert {"script": "fig1.py", "figure": "Fig1_PRISMA_Flowchart"} in item["runtime"]
        assert not layoutsession.state_path().exists(), "只读发现不能写会话状态"

    def test_a_broken_slot_does_not_block_the_others(self, client, tmp_path):
        p = _project(client, tmp_path, "proj", default=True)
        _save(client, p["id"], "d_good")
        (m.AUTOSAVE_DIR / "d_broken.json").write_bytes(b"{nope")
        items = {i["doc_id"]: i for i in self._candidates(client, p["id"], "unknown")["items"]}
        assert items["d_broken"]["status"] == "invalid"
        res = self._candidates(client, p["id"], "project")
        assert any(i["doc_id"] == "d_good" and i["status"] == "ok" for i in res["items"])

    def test_other_projects_slots_are_never_returned(self, client, tmp_path):
        f = _project(client, tmp_path, "projF", default=True)
        g = _project(client, tmp_path, "projG")
        _save(client, f["id"], "d_f")
        for scope in ("project", "unknown"):
            assert not any(
                i["doc_id"] == "d_f" for i in self._candidates(client, g["id"], scope)["items"]
            )


# ============================== 独立反例审查（第二阶段） ==============================


def test_a_later_open_intent_is_not_overridden_by_an_older_pending_first_save(client, tmp_path):
    """序列：A durable → 打开新 B（pending B）→ 用户回到 A（durable A）→ B 的首次保存迟到落盘。
    最新的打开意图是 A；B 的迟到保存不能把 last 拉成 B。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    _put_last(client, p["id"], "d_a", "A")
    assert _put_last(client, p["id"], "d_b", "B").get_json()["state"] == "pending"
    _put_last(client, p["id"], "d_a", "A")  # 回到 A
    assert _session(client, p["id"])["last"]["doc_id"] == "d_a"
    _save(client, p["id"], "d_b", doc(name="B"))  # B 的首存迟到
    last = _session(client, p["id"])["last"]
    assert last["doc_id"] == "d_a", f"过期的 pending 晋升把 last 拉回了较早的打开意图 B：{last}"


def test_owner_other_is_not_forgotten_just_because_the_slot_bytes_changed_externally(
    client, tmp_path
):
    """序列：F 保存 X（owner=F）→ G 记 last X 被 409 拒 → X 的字节被外部改动（同步盘 / 编辑器）→ G 再记 last X。
    『待核对』只该是崩溃窗口的保守处理；owners 明确记着 other 的文档不该因此被别的项目认领。"""
    f = _project(client, tmp_path, "projF", default=True)
    g = _project(client, tmp_path, "projG")
    _save(client, f["id"], "d_x")
    assert _put_last(client, g["id"], "d_x", "X").status_code == 409
    slot = m.AUTOSAVE_DIR / "d_x.json"
    changed = doc(x=99, updated=9)
    slot.write_text(json.dumps(changed), encoding="utf-8")
    r = _put_last(client, g["id"], "d_x", "X")
    assert r.status_code == 409, "外部改动字节后，F 的 owner 记录被降级成未知，G 认领了 F 的排版"


def test_concurrent_autosave_last_delete_and_reads_do_not_deadlock(client, tmp_path):
    p = _project(client, tmp_path, "proj", default=True)
    errors: list[str] = []
    stop = threading.Event()

    def worker(kind: int):
        with m.app.test_client() as c:
            i = 0
            while not stop.is_set():
                i += 1
                doc_id = f"d_{kind}_{i % 5}"
                try:
                    if kind == 0:
                        r = c.put(
                            f"/api/autosave/{doc_id}",
                            json=doc(x=i, updated=i),
                            headers=_pj(p["id"]),
                        )
                    elif kind == 1:
                        r = c.put(
                            "/api/layout-session/last",
                            json={"doc_id": f"d_0_{i % 5}", "name": "n"},
                            headers=_pj(p["id"]),
                        )
                    elif kind == 2:
                        r = c.delete(f"/api/autosave/d_0_{i % 5}", headers=_pj(p["id"]))
                    else:
                        r = c.get("/api/layout-session", headers=_pj(p["id"]))
                    if r.status_code >= 500:
                        errors.append(f"kind {kind}: {r.status_code} {r.get_json()}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(k,)) for k in (0, 0, 1, 2, 3)]
    for t in threads:
        t.start()
    threading.Event().wait(3)
    stop.set()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads), "文档锁 / 会话锁交错出现了死锁"
    assert not errors, errors[:3]


def test_candidate_pages_cover_every_project_slot_exactly_once(client, tmp_path):
    p = _project(client, tmp_path, "proj", default=True)
    # 3 个本项目（owners）+ 3 个未知归属，mtime 交错
    ids = []
    for i in range(6):
        doc_id = f"d_c{i}"
        if i % 2 == 0:
            _save(client, p["id"], doc_id, doc(updated=i + 2))
        else:
            m.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
            (m.AUTOSAVE_DIR / f"{doc_id}.json").write_text(
                json.dumps(doc(updated=i + 2)), encoding="utf-8"
            )
        os.utime(m.AUTOSAVE_DIR / f"{doc_id}.json", (1_000_000 + i, 1_000_000 + i))
        ids.append(doc_id)
    seen: list[str] = []
    cursor = None
    for _ in range(12):
        qs = {"scope": "project", "limit": 2, **({"cursor": cursor} if cursor else {})}
        r = client.get("/api/layout-session/candidates", query_string=qs, headers=_pj(p["id"]))
        assert r.status_code == 200
        body = r.get_json()
        seen += [i["doc_id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    else:
        pytest.fail("分页没有终点")
    assert sorted(seen) == ["d_c0", "d_c2", "d_c4"], seen


# ============================== 预防性审查（family 3 / 4） ==============================


def test_unreadable_slot_directory_is_not_reported_as_an_empty_candidate_list(
    client, tmp_path, monkeypatch
):
    """候选目录读不出来（权限 / I/O）≠ 没有候选：不能回 200 + 空列表 + index ok。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_there")

    real = os.scandir

    def denied(path=".", *a, **k):
        if Path(path) == m.AUTOSAVE_DIR:
            raise PermissionError(13, "simulated EACCES")
        return real(path, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(os, "scandir", denied)
        r = client.get(
            "/api/layout-session/candidates",
            query_string={"scope": "project"},
            headers=_pj(p["id"]),
        )
    body = r.get_json()
    folded = r.status_code == 200 and body["items"] == [] and body.get("index") == "ok"
    assert not folded, f"目录读失败被折成空候选列表：{r.status_code} {body}"


def test_slot_removed_by_another_process_between_validation_and_record_does_not_leave_a_dangling_durable_last(
    client, tmp_path, monkeypatch
):
    """跨进程窗口：`PUT /last` 在文档锁里验槽位，但文档锁只是线程锁。另一进程在验完与记完之间删了槽位
    （用「验完立刻删文件」模拟），durable last 就指向不存在的槽位。"""
    p = _project(client, tmp_path, "proj", default=True)
    _save(client, p["id"], "d_a")
    _save(client, p["id"], "d_b", doc(name="B"))
    _put_last(client, p["id"], "d_a", "A")
    real = m._slot_recoverable

    def validate_then_vanish(path):
        out = real(path)
        if path.stem == "d_b":
            path.unlink()
        return out

    with monkeypatch.context() as mp:
        mp.setattr(m, "_slot_recoverable", validate_then_vanish)
        _put_last(client, p["id"], "d_b", "B")
    last = _session(client, p["id"])["last"]
    assert last is None or _readable(client, p["id"], last["doc_id"]), (
        f"durable last 指向了已不存在的槽位：{last}"
    )
