"""脚本 `input()` 的作答端点、答案管理与缓存失效——真 Flask + 真 worker（ADR 0099）。

链路与界面一致：试运行 → 后端发 `script.input_requested` → 作答端点回填 → 图按答案画出来；重跑不再问、
直接用记住的答案；在答案管理里改答案 → 热会话作废（先热缓存再改答案）→ 数据绑定修订换代 → 重跑出新图。
判据的主语是**脚本实际产出的图名**（stem 由答案决定）与池里那条会话，不是我们自己的记账。
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from tavotto import app as m
from tavotto.engine import (
    databinding,
    inputbroker,
    pool as engine_pool,
    project_watch as engine_watch,
    scriptanswers,
    scriptinput,
)

try:
    engine_pool.find_worker_python()
    HAS_WORKER = True
except engine_pool.WorkerError:
    HAS_WORKER = False

needs_worker = pytest.mark.skipif(not HAS_WORKER, reason="找不到装有 matplotlib 的解释器")

PICK = """\
import matplotlib.pyplot as plt

print("1. a")
print("2. b")
choice = input("numbers: ")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(choice)
fig.savefig("sel_" + choice.replace(",", "_") + ".pdf")
"""


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture(autouse=True)
def bounded_wait(monkeypatch):
    """等人作答的上限缩到秒级：回归时用例在几十秒内红，而不是挂满 10 分钟。"""
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "20")


@pytest.fixture
def figs(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    (d / "pick.py").write_text(PICK, encoding="utf-8")
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

        def wait_for(self, kind, n=1, timeout=120):
            deadline = time.time() + timeout
            while time.time() < deadline:
                items = self.of(kind)
                if len(items) >= n:
                    return items
                time.sleep(0.05)
            raise AssertionError(f"没等到 {kind}")

    return Box()


def _listening(monkeypatch, project) -> None:
    """一条能答题的事件流正在看 `project`（等价于界面收到 hello 后报了 listen）。"""
    monkeypatch.setattr(m, "_answerer_streams", {"s": m._project_id(project.resolve())})


def _probe_async(script="pick.py"):
    out: dict = {}

    def run():
        resp = m.app.test_client().post("/api/registry/probe", json={"script": script})
        out["status"] = resp.status_code
        out["json"] = resp.get_json()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def _stems(result: dict) -> list[str]:
    return sorted(d["stem"] for d in result.get("descriptors") or [])


@needs_worker
def test_answer_remember_rerun_change_rerun(client, figs, events, monkeypatch):
    client.post("/api/projects/open", json={"path": str(figs)})
    _listening(monkeypatch, figs)  # 主界面的事件流连着，且正在看这个项目

    # 1) 第一次运行：弹问，界面看得到编号清单与提示；作答
    th, out = _probe_async()
    asked = events.wait_for("script.input_requested")[0]
    assert asked["prompt"] == "numbers: " and "1. a" in asked["stdout_tail"]
    assert asked["pj"] == m._project_id(figs.resolve())
    resp = client.post("/api/script_input/answer", json={"id": asked["id"], "answer": "1,2"})
    assert resp.status_code == 200
    th.join(120)
    assert out["status"] == 200, out
    assert _stems(out["json"]) == ["sel_1_2"]
    listed = client.get("/api/script_input/answers").get_json()
    assert listed["scripts"]["pick.py"][0]["answer"] == "1,2"
    assert listed["location"] == "tavottofile/_script_inputs.json"

    # 2) 热缓存：会话已 build；此刻的数据绑定修订
    assert engine_pool.peek("pick.py", str(figs)) is not None
    before = databinding.binding_for(figs / "pick.py", figs, "sandbox")["revision"]

    # 3) 改答案：热会话作废、界面收到重跑信号、数据绑定换代
    resp = client.post(
        "/api/script_input/answers", json={"script": "pick.py", "index": 1, "answer": "2"}
    )
    assert resp.status_code == 200, resp.get_json()
    assert engine_pool.peek("pick.py", str(figs)) is None
    assert [e["reason"] for e in events.of("panel.file_changed")] == ["script_input"]
    assert databinding.binding_for(figs / "pick.py", figs, "sandbox")["revision"] != before

    # 4) 重跑：不再问，直接用记住的答案，图变了
    th, out = _probe_async()
    th.join(120)
    assert out["status"] == 200, out
    assert _stems(out["json"]) == ["sel_2"]
    assert len(events.of("script.input_requested")) == 1
    assert [a["answer"] for a in events.of("script.input_autofilled")] == ["2"]


@needs_worker
def test_no_answerer_reports_script_needs_input_with_the_prompt(client, figs, events, monkeypatch):
    client.post("/api/projects/open", json={"path": str(figs)})
    monkeypatch.setattr(m, "_answerer_streams", {})  # 没有能答题的界面（MCP / CLI / 后台）
    t0 = time.time()
    resp = client.post("/api/registry/probe", json={"script": "pick.py"})
    body = resp.get_json()
    assert body["error"]["code"] == "script_needs_input"
    assert body["error"]["params"] == {"prompt": "numbers: "}
    assert time.time() - t0 < 60  # 绝不卡死
    assert events.of("script.input_requested") == []


@needs_worker
def test_stop_kills_the_waiting_script(client, figs, events, monkeypatch):
    client.post("/api/projects/open", json={"path": str(figs)})
    _listening(monkeypatch, figs)
    th, out = _probe_async()
    asked = events.wait_for("script.input_requested")[0]
    assert client.post("/api/script_input/stop", json={"id": asked["id"]}).status_code == 200
    th.join(60)
    assert not th.is_alive()
    assert out["json"]["error"]["code"] == "execution_cancelled"
    assert [c["reason"] for c in events.of("script.input_closed")][0] == "stopped"
    assert scriptanswers.load(figs) == {}


def test_answering_an_unknown_or_foreign_question_is_404(client, tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    client.post("/api/projects/open", json={"path": str(a)})
    foreign = inputbroker.Pending(
        id="x" * 32,
        project_root=str(b),
        script="s.py",
        index=1,
        prompt="p",
        kind="input",
        directory=tmp_path / "rv",
    )
    monkeypatch.setitem(inputbroker._pending, foreign.id, foreign)
    for pid in ("nope", foreign.id):
        resp = client.post("/api/script_input/answer", json={"id": pid, "answer": "1"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "script_input_not_pending"
    # 别的项目的问没有被答掉
    assert inputbroker.get_pending(foreign.id) is foreign


def test_answers_file_is_not_listed_as_a_canvas(client, tmp_path):
    figs = tmp_path / "figs"
    figs.mkdir()
    client.post("/api/projects/open", json={"path": str(figs)})
    scriptanswers.remember(figs, "s.py", 1, "p: ", "1")
    assert scriptanswers.answers_path(figs).is_file()
    # 对照：同一个目录里的一份真画布是列得出来的（尺子是活的）
    (scriptanswers.answers_path(figs).parent / "mine.json").write_text("{}", encoding="utf-8")
    names = client.get("/api/layouts").get_json()["layouts"]
    assert "mine" in names
    assert "_script_inputs" not in names


def test_binding_revision_is_unchanged_without_answers(tmp_path):
    """没有答案的脚本，数据绑定修订一个字节不变（不给存量计划平白换代）。"""
    root = tmp_path / "p"
    root.mkdir()
    (root / "s.py").write_text("x = 1\n", encoding="utf-8")
    plain = databinding.binding_for(root / "s.py", root, "sandbox")
    assert "script_inputs" not in plain
    assert plain["revision"] == databinding._binding_revision(
        {k: v for k, v in plain.items() if k != "revision"}
    )
    scriptanswers.remember(root, "s.py", 1, "p: ", "1")
    with_answers = databinding.binding_for(root / "s.py", root, "sandbox")
    assert with_answers["script_inputs"].startswith("sha256:")
    assert with_answers["revision"] != plain["revision"]


def _hello(resp) -> str:
    """读到 `stream.hello`，交出流 id。"""
    for chunk in resp.response:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if "stream.hello" in text:
            return json.loads(text.split("data: ", 1)[1])["stream_id"]
    raise AssertionError("没有 stream.hello")


def test_answerers_are_counted_per_project_and_only_while_the_stream_lives(client, tmp_path):
    """「能答题」= 一条带 `answers=1` 的事件流**正在看这个项目**（Codex #680 P1）。开着 A 的界面对 B 不算；
    不带标记的流不算；流断了就不算（ADR 0099 §五）。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pid_a = client.post("/api/projects/open", json={"path": str(a)}).get_json()["id"]
    pid_b = client.post("/api/projects/open", json={"path": str(b)}).get_json()["id"]
    assert m._answerer_streams == {}
    plain = client.get("/api/events", buffered=False)
    next(plain.response)
    assert m._answerer_streams == {}
    ui = client.get("/api/events?answers=1", buffered=False)
    sid = _hello(ui)
    # 还没报在看哪个项目：谁都不算
    assert not m._has_script_input_answerer(a) and not m._has_script_input_answerer(b)
    resp = client.post(f"/api/script_input/listen?pj={pid_a}", json={"stream_id": sid})
    assert resp.status_code == 200
    assert m._has_script_input_answerer(a)
    assert not m._has_script_input_answerer(b)
    # 换到 B：A 不再有人答
    client.post(f"/api/script_input/listen?pj={pid_b}", json={"stream_id": sid})
    assert m._has_script_input_answerer(b) and not m._has_script_input_answerer(a)
    ui.close()
    plain.close()
    assert m._answerer_streams == {}
    assert not m._has_script_input_answerer(b)
    gone = client.post(f"/api/script_input/listen?pj={pid_b}", json={"stream_id": sid})
    assert gone.status_code == 404


@needs_worker
def test_a_ui_on_another_project_does_not_hold_the_script(
    client, figs, tmp_path, events, monkeypatch
):
    """界面开着别的项目时，这个项目的脚本问到 input 立即 `script_needs_input`，不干等（Codex #680 P1）。"""
    other = tmp_path / "other"
    other.mkdir()
    pid_other = client.post("/api/projects/open", json={"path": str(other)}).get_json()["id"]
    pid = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
    monkeypatch.setattr(m, "_answerer_streams", {"s": pid_other})
    t0 = time.time()
    body = client.post(f"/api/registry/probe?pj={pid}", json={"script": "pick.py"}).get_json()
    assert body["error"]["code"] == "script_needs_input"
    assert time.time() - t0 < 15  # 远小于等人的上限（本文件缩到 20 秒）
    assert events.of("script.input_requested") == []


@needs_worker
def test_a_rejected_answer_keeps_the_question_waiting(client, figs, events, monkeypatch):
    """答案不合法（太长）时 400，这一问**仍在等**：改好再交一次照样答得上（Codex #680 P2）。"""
    pid = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
    monkeypatch.setattr(m, "_answerer_streams", {"s": pid})
    monkeypatch.setattr(scriptanswers, "MAX_ANSWER_CHARS", 5)
    th, out = _probe_async()
    asked = events.wait_for("script.input_requested")[0]
    bad = client.post("/api/script_input/answer", json={"id": asked["id"], "answer": "1,2,1,2,1,2"})
    assert bad.status_code == 400
    assert inputbroker.get_pending(asked["id"]) is not None
    good = client.post("/api/script_input/answer", json={"id": asked["id"], "answer": "1,2"})
    assert good.status_code == 200
    th.join(120)
    assert _stems(out["json"]) == ["sel_1_2"]


def test_render_endpoint_error_body_carries_the_prompt():
    """渲染端点那条出口（`_worker_error_payload`）也带 params.prompt——界面按 code 翻译时要它。"""
    for code in ("script_needs_input", "script_input_timeout"):
        err = engine_pool.WorkerError("脚本需要输入", "", code=code)
        err.extra = {"prompt": "numbers: "}
        body = m._worker_error_payload(err)
        assert body["code"] == code
        assert body["params"] == {"prompt": "numbers: "}
