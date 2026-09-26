"""改图助手的事件带上发起它的项目（#589）。

SSE 是全进程一条流，后端同时端着多个项目。`ai.delta` / `ai.done` 原来不带 `pj`：A 的任务在用户切到
B 之后才说完的话、才发的收尾提示，会落进 B 的标签页。这里钉的是后端那一半——`/api/ai/run` 交给引擎的
`on_event` 给每条事件补上发起时那个项目的 id；前端那一半（按此刻认领的 pj 丢弃）在
`web/src/store/projectSwitchAi.test.ts`。
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import project_watch as engine_watch


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def test_ai_events_carry_the_project_that_started_them(client, tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}
                },
            }
        ),
        encoding="utf-8",
    )
    status = m.open_project(str(figs))
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda ev, data: published.append((ev, data)))

    def fake_run(agent, script, prompt, figures_dir, *, on_event, **_kw):
        # 引擎自己发的事件形状：只有会话 id 与内容，不知道 SSE、不知道 pj
        on_event("ai.delta", {"session": "s1", "kind": "delta", "text": "hi"})
        on_event("ai.done", {"session": "s1", "status": "done", "changed": False, "diff": ""})
        return "s1"

    monkeypatch.setattr(m.engine_ai, "run", fake_run)
    resp = client.post("/api/ai/run", json={"agent": "codex", "id": "Fig1.pdf", "prompt": "p"})
    assert resp.status_code == 200, resp.get_json()
    assert [ev for ev, _ in published] == ["ai.delta", "ai.done"]
    assert {data["pj"] for _, data in published} == {status["id"]}
    assert published[0][1]["text"] == "hi"  # 原有字段一个不丢
