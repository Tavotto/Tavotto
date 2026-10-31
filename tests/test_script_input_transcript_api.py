"""运行时 input 经**真 Flask + 真 worker** 的公共入口（T08）：依赖前一问的第二问、准备会话里的待答输入、
以及口令哨兵在每个 Tavotto 自己写出的载荷里都不出现。

判据的主语：脚本实际读到的值（项目外的结果文件）、发给界面的事件、HTTP 响应体与 Tavotto 写下的文件。
「不出现」的断言都先证明观测有效（同一次运行的普通答案在同一份载荷里**出现**了）。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import zipfile

import pytest

from tavotto import app as m
from tavotto.engine import (
    config,
    deprepair,
    figcapture,
    inputbroker,
    pool as engine_pool,
    prepsession,
    project_watch as engine_watch,
    scriptinput,
)

try:
    engine_pool.find_worker_python()
    HAS_WORKER = True
except engine_pool.WorkerError:
    HAS_WORKER = False

needs_worker = pytest.mark.skipif(not HAS_WORKER, reason="找不到装有 matplotlib 的解释器")

#: 第二问的答案依赖第一问：两问前面的输出一字不差，只有「前一问答了什么」不同。
TWO_STEP = """\
import json, os
import matplotlib.pyplot as plt

print("1. raw  2. smooth")
first = input("mode: ")
print("1. red  2. blue")
second = input("color: ")
with open(os.environ["SI_RESULT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps([first, second]) + "\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(first + second)
fig.savefig("two_" + first + "_" + second + ".pdf")
"""

SECRET = """\
import getpass, json, os
import matplotlib.pyplot as plt

token = getpass.getpass("token: ")
label = input("label: ")
with open(os.environ["SI_RESULT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps([len(token), label]) + "\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(label)
fig.savefig("sec_" + label + ".pdf")
"""

SENTINEL = "S3NT1NEL-t08-http"


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture(autouse=True)
def bounded_wait(monkeypatch, tmp_path):
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "20")
    monkeypatch.setenv("SI_RESULT", str(tmp_path / "result.jsonl"))


@pytest.fixture
def figs(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    yield d
    engine_pool.shutdown_all(str(d), wait=True)


@pytest.fixture
def events(monkeypatch):
    got: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def publish(event, data):
        with lock:
            got.append((event, dict(data)))

    monkeypatch.setattr(m, "sse_publish", publish)

    class Box:
        def of(self, kind):
            with lock:
                return [d for e, d in got if e == kind]

        def all(self):
            with lock:
                return list(got)

        def wait_for(self, kind, n=1, timeout=120):
            deadline = time.time() + timeout
            while time.time() < deadline:
                items = self.of(kind)
                if len(items) >= n:
                    return items
                time.sleep(0.05)
            raise AssertionError(f"没等到 {kind}")

    return Box()


def _open_listening(client, monkeypatch, figs) -> str:
    pid = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
    monkeypatch.setattr(m, "_answerer_streams", {"s": pid})
    return pid


def _probe_async(script):
    out: dict = {}

    def run():
        resp = m.app.test_client().post("/api/registry/probe", json={"script": script})
        out["status"] = resp.status_code
        out["json"] = resp.get_json()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def _answer(client, asked, value):
    resp = client.post("/api/script_input/answer", json={"id": asked["id"], "answer": value})
    assert resp.status_code == 200, resp.get_json()


def _runs(tmp_path) -> list:
    path = tmp_path / "result.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@needs_worker
def test_the_second_question_is_asked_again_when_the_first_answer_changes(
    client, figs, events, monkeypatch, tmp_path
):
    """第二问的上下文含已确认的前一问：在答案管理里把第一问改掉，第二问不许静默沿用旧答案。"""
    (figs / "two.py").write_text(TWO_STEP, encoding="utf-8")
    _open_listening(client, monkeypatch, figs)
    th, out = _probe_async("two.py")
    _answer(client, events.wait_for("script.input_requested", 1)[0], "1")
    _answer(client, events.wait_for("script.input_requested", 2)[1], "2")
    th.join(120)
    assert out["status"] == 200 and _runs(tmp_path) == [["1", "2"]], out

    resp = client.post(
        "/api/script_input/answers", json={"script": "two.py", "index": 1, "answer": "2"}
    )
    assert resp.status_code == 200, resp.get_json()
    th, out = _probe_async("two.py")
    third = events.wait_for("script.input_requested", 3)[2]
    assert third["prompt"] == "color: ", "第一问的答案变了，第二问却被自动回填"
    assert third.get("suggestion") == "2"  # 旧答案只是建议
    _answer(client, third, "1")
    th.join(120)
    assert _runs(tmp_path) == [["1", "2"], ["2", "1"]]
    assert [a["prompt"] for a in events.of("script.input_autofilled")] == ["mode: "]


@needs_worker
def test_a_preparation_session_presents_the_waiting_question_and_continues_the_same_run(
    client, figs, events, monkeypatch, tmp_path
):
    """准备会话：脚本在等输入时报告 `awaiting_runtime_input`，并给出那一问的公开投影（不含提示与答案）；
    经既有答题端点回答后**同一次**执行接着跑完，脚本入口只执行一次。"""
    monkeypatch.setattr(deprepair, "gate", lambda root, script: None)
    monkeypatch.setattr(
        deprepair, "preparation_offer", lambda root, script: {"plan": {"status": "nothing_needed"}}
    )
    prepsession.SESSIONS.reset_for_tests()
    (figs / "two.py").write_text(TWO_STEP, encoding="utf-8")
    pid = _open_listening(client, monkeypatch, figs)
    sessions = "/api/engine/preparation-sessions"
    report = client.post(sessions, json={"script": "two.py"}, query_string={"pj": pid}).get_json()
    run = next(a for a in report["actions"] if a["kind"] == "run")
    resp = client.post(
        f"{sessions}/{report['session_id']}/actions",
        json={"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        query_string={"pj": pid},
    )
    assert resp.status_code == 202, resp.get_json()
    asked = events.wait_for("script.input_requested", 1)[0]
    deadline = time.time() + 60
    while True:
        now = client.get(f"{sessions}/{report['session_id']}", query_string={"pj": pid}).get_json()
        if now["phase"] == "awaiting_runtime_input":
            break
        assert time.time() < deadline, now
        time.sleep(0.05)
    assert now["runtime_input"] == {
        "id": asked["id"],
        "index": 1,
        "input_kind": "input",
        "secret": False,
    }
    assert "mode: " not in json.dumps(now["runtime_input"])
    _answer(client, asked, "1")
    _answer(client, events.wait_for("script.input_requested", 2)[1], "1")
    deadline = time.time() + 120
    while True:
        now = client.get(f"{sessions}/{report['session_id']}", query_string={"pj": pid}).get_json()
        if now["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline, now
        time.sleep(0.1)
    assert now["phase"] == "completed", now
    assert now.get("runtime_input") is None
    assert _runs(tmp_path) == [["1", "1"]]  # 一次执行，答完接着跑
    prepsession.SESSIONS.reset_for_tests()


def _texts_under(root) -> str:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                raw = open(path, "rb").read()
            except OSError:
                continue
            if name.endswith((".zip", ".tavotto")):
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    out.extend(z.read(n).decode("utf-8", "replace") for n in z.namelist())
            out.append(raw.decode("utf-8", "replace"))
    return "\n".join(out)


@needs_worker
def test_the_getpass_sentinel_is_absent_from_every_payload_tavotto_writes(
    client, figs, events, monkeypatch, tmp_path, caplog
):
    """C17 / X04：SSE、HTTP 响应、任务诊断、项目包、Tavotto 的数据目录 / 会话缓存 / 项目目录、日志里都没有口令，
    也没有它的 sha256。只证明 Tavotto 自己不记录——脚本自己 print 口令不在这个保证里。"""
    caplog.set_level("DEBUG")
    (figs / "sec.py").write_text(SECRET, encoding="utf-8")
    _open_listening(client, monkeypatch, figs)
    th, out = _probe_async("sec.py")
    asked = events.wait_for("script.input_requested", 1)[0]
    assert asked["input_kind"] == "getpass" and asked.get("secret") is True
    _answer(client, asked, SENTINEL)
    _answer(client, events.wait_for("script.input_requested", 2)[1], "visible")
    th.join(120)
    assert out["status"] == 200, out
    assert _runs(tmp_path) == [[len(SENTINEL), "visible"]]
    diag = client.get(
        "/api/diagnostics/task",
        query_string={
            "kind": out["json"]["diagnostic"]["kind"],
            "ref": out["json"]["diagnostic"]["ref"],
        },
    )
    assert diag.status_code == 200, diag.get_json()
    asset = figcapture.runtime_asset_id("sec.py", "sec_visible")
    layout = {
        "schema": 2,
        "name": "pk",
        "page": {"w": 180, "h": 120},
        "panels": [],
        "objects": [
            {"id": "o1", "type": "panel", "fileId": asset, "x": 0, "y": 0, "w": 30, "h": 20}
        ],
        "guides": [],
    }
    pkg = client.post("/api/package", json={"stem": "pk", "doc": layout})
    answers = client.get("/api/script_input/answers").get_json()
    payloads = "\n".join(
        [
            json.dumps(events.all(), ensure_ascii=False),
            json.dumps(out["json"], ensure_ascii=False),
            json.dumps(diag.get_json(), ensure_ascii=False),
            json.dumps(pkg.get_json(), ensure_ascii=False),
            json.dumps(answers, ensure_ascii=False),
            caplog.text,
            _texts_under(figs),
            _texts_under(config.data_dir()),
            _texts_under(engine_pool.ENGINE_CACHE),
        ]
    )
    assert "visible" in payloads  # 观测有效：同一次运行的普通答案看得到
    assert SENTINEL not in payloads
    assert hashlib.sha256(SENTINEL.encode()).hexdigest() not in payloads
    assert inputbroker.pending() == []


ONE = """\
import matplotlib.pyplot as plt

print("1. a  2. b")
choice = input("which: ")
fig, ax = plt.subplots(figsize=(2, 1.5))
fig.savefig("one_" + choice + ".pdf")
"""


@needs_worker
@pytest.mark.parametrize("ending", ["timed_out", "stopped"])
def test_an_unanswered_question_lands_in_the_task_diagnostic_without_its_text(
    client, figs, events, monkeypatch, ending
):
    """input 的超时 / 停止终局进任务诊断（T04 白名单）：只有计数与闭集，提示与输出片段不进快照。"""
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "1.5" if ending == "timed_out" else "20")
    (figs / "one.py").write_text(ONE, encoding="utf-8")
    _open_listening(client, monkeypatch, figs)
    th, out = _probe_async("one.py")
    asked = events.wait_for("script.input_requested", 1)[0]
    if ending == "stopped":
        assert client.post("/api/script_input/stop", json={"id": asked["id"]}).status_code == 200
    th.join(120)
    assert out["json"]["error"]["code"] in ("script_input_timeout", "execution_cancelled"), out
    assert "input_facts" not in out["json"]  # 响应形状不变
    diag = out["json"]["diagnostic"]
    doc = client.get(
        "/api/diagnostics/task", query_string={"kind": diag["kind"], "ref": diag["ref"]}
    ).get_json()
    snap = json.dumps(doc, ensure_ascii=False)
    assert "which" not in snap and "1. a" not in snap
    found = _find_key(doc, "input")
    assert found is not None, snap
    assert found["asked"] == 1 and found["shown"] == 1 and found[ending] == 1, found


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], dict) and "asked" in obj[key]:
            return obj[key]
        for v in obj.values():
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    return None
