"""版本时间线的跨进程互斥与 id 唯一（审计 D0）。

`api_versions_*` 的「读 → 改 → 整写」原来只被进程内的 `threading.Lock()` 保护：两个后端进程共用一个数据目录时，
后写的整份盖掉先写的，两边都 200，检查点静默丢失；`_new_version_id`（毫秒 + 进程内序号）跨进程还会撞 id。
现在时间线用跨进程的槽位文档锁（锁名 `<doc_id>.versions`），id 带进程区分段，锁内再核一遍重名。

* 真两进程各追加 40 条 → 80 条、id 无重复（POSIX；Windows 的 `msvcrt` 腿留给 CI，本机没跑过）；
* 别的进程占着版本锁 → 503 `session_unavailable`，历史字节不变；
* 固定时钟 / 序号造撞 id → 不覆盖已有节点；
* 版本锁不与同一文档的自动保存共用条带（版本追加持锁期间自动保存不被堵）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import layoutsession

SRC = Path(m.__file__).resolve().parent.parent

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="用 fcntl 模拟另一个持锁者 / 真两进程；Windows 腿（msvcrt）留给 CI",
)


def _doc(tag: str = "x") -> dict:
    return {
        "schema": 2,
        "name": "fig_layout",
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": "o0",
                "type": "text",
                "text": tag,
                "x": 0,
                "y": 0,
                "w": 10,
                "h": 5,
                "sizePt": 9,
                "bold": False,
                "color": "#000",
                "align": "left",
            }
        ],
        "guides": [],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


_CHILD = r"""
import os, sys, time, json
sys.path.insert(0, sys.argv[1])
tag, n, go, root = sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
os.environ["TAVOTTO_DATA_DIR"] = root + "/data"
os.environ["TAVOTTO_NO_TELEMETRY"] = "1"
from pathlib import Path
from tavotto import app as m
m.VERSIONS_DIR = Path(root) / "_versions"
m.PROJECTS.clear()
m.DEFAULT_PROJECT = None
m.app.config["TESTING"] = True
c = m.app.test_client()
doc = json.loads(sys.argv[6])
while not os.path.exists(go):
    time.sleep(0.001)
ok = 0
for i in range(n):
    doc["objects"][0]["text"] = "%s-%d" % (tag, i)
    r = c.post("/api/versions/race", json={"doc": doc, "name": "%s-%d" % (tag, i)})
    ok += r.status_code == 200
print(json.dumps({"tag": tag, "ok": ok}))
sys.stdout.flush()
os._exit(0)
"""


@posix_only
def test_two_real_processes_appending_to_one_timeline_lose_no_version(tmp_path):
    """两个真后端进程共用数据目录，各向同一份时间线追加 40 条：80 条全在、id 不重复。"""
    go = tmp_path / "go"
    n = 40
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD,
                str(SRC),
                tag,
                str(n),
                str(go),
                str(tmp_path),
                json.dumps(_doc()),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for tag in ("A", "B")
    ]
    time.sleep(2.0)  # 两个子进程都已 import 完、在等发令
    go.write_text("go")
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()
        row = json.loads(out.decode().strip().splitlines()[-1])
        assert row["ok"] == n, row
    data = json.loads((tmp_path / "_versions" / "race.json").read_text(encoding="utf-8"))
    names = [v["name"] for v in data["versions"]]
    ids = [v["id"] for v in data["versions"]]
    assert len(names) == 2 * n, f"丢了 {2 * n - len(names)} 条"
    assert len(set(ids)) == len(ids), "出现重复的版本 id"
    assert {nm for nm in names} == {f"{t}-{i}" for t in ("A", "B") for i in range(n)}


@posix_only
def test_versions_lock_held_by_another_process_times_out_with_503_and_writes_nothing(
    client, monkeypatch, tmp_path
):
    import fcntl

    created = client.post("/api/versions/d1", json={"doc": _doc("seed"), "name": "seed"})
    assert created.status_code == 200
    vid = created.get_json()["version"]["id"]
    path = m._versions_path("d1")
    before = path.read_bytes()

    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.3)
    lock_file = layoutsession.doclock_path(m._lock_stripe(path.with_suffix(".versions")))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        for resp in (
            client.post("/api/versions/d1", json={"doc": _doc("new"), "name": "new"}),
            client.patch(f"/api/versions/d1/{vid}", json={"name": "renamed"}),
            client.post(f"/api/versions/d1/{vid}/duplicate"),
            client.delete(f"/api/versions/d1/{vid}"),
        ):
            assert resp.status_code == 503, resp.get_json()
            assert resp.get_json()["code"] == "session_unavailable"
        assert path.read_bytes() == before, "拿不到版本锁还是动了历史"
    finally:
        os.close(fd)
    assert (
        client.post("/api/versions/d1", json={"doc": _doc("new"), "name": "new"}).status_code == 200
    )


def test_ids_from_two_processes_in_the_same_millisecond_and_sequence_differ(monkeypatch):
    """两个进程恰好同一毫秒、同一序号：区分段让 id 仍不同，形状仍匹配 `_VERSION_ID_RE`。"""
    monkeypatch.setattr(m.time, "time", lambda: 1_700_000_000.0)
    ids = []
    for tag in ("aaaaaa", "bbbbbb"):
        monkeypatch.setattr(m, "_VERSION_PROC_TAG", tag)
        monkeypatch.setattr(m, "_version_seq", 0)
        ids.append(m._new_version_id())
    assert ids[0] != ids[1]
    assert all(m._VERSION_ID_RE.match(i) for i in ids)


def test_a_colliding_id_never_overwrites_an_existing_node(client, monkeypatch):
    """即使 id 生成器撞了（区分段也碰巧相同），锁内核对已有 id 会重取，已有节点原样保留。"""
    seq = iter(["v1-aa", "v1-aa", "v1-aa", "v1-bb", "v1-cc", "v1-cc", "v1-dd"])
    monkeypatch.setattr(m, "_new_version_id", lambda: next(seq))
    a = client.post("/api/versions/d1", json={"doc": _doc("first"), "name": "first"}).get_json()
    b = client.post("/api/versions/d1", json={"doc": _doc("second"), "name": "second"}).get_json()
    assert a["version"]["id"] == "v1-aa"
    assert b["version"]["id"] == "v1-bb", "撞了 id 没有重取"
    c = client.post(f"/api/versions/d1/{a['version']['id']}/duplicate").get_json()
    assert c["version"]["id"] == "v1-cc"
    data = json.loads(m._versions_path("d1").read_text(encoding="utf-8"))
    assert [v["name"] for v in data["versions"]][:2] == ["first", "second"]
    assert len({v["id"] for v in data["versions"]}) == len(data["versions"]) == 3


def test_holding_the_versions_lock_does_not_block_autosave_of_the_same_document(
    client, monkeypatch
):
    """版本锁用独立的 `<doc_id>.versions` 名字：不与同一文档自动保存（`<doc_id>.json`）共用条带。

    同线程里持着版本锁再取自动保存的槽位锁——共用条带的话线程锁拿不到、超时抛 `SessionStateUnavailable`。"""
    doc_id = "d1"
    assert m._lock_stripe(m._versions_path(doc_id).with_suffix(".versions")) != m._lock_stripe(
        m._autosave_path(doc_id)
    ), "选的 doc_id 恰好共用条带，换一个"
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.5)
    with m._versions_lock(doc_id):
        with m._document_lock(m._autosave_path(doc_id)):
            pass
