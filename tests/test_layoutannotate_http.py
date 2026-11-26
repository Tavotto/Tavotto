"""`GET /api/layouts/<名>/agent-additions`（ADR 0208 §五「把 Agent 加的并进来」）：桌面拿基线问后端，
磁盘上多出来的是不是只有 Agent 追加的对象。判据本身在 `test_layoutannotate.py`；这里钉住 HTTP 这一层：
只认当前项目、没基线 / 没项目一律 `available: false`、回的对象与修订号就是引擎给的那份。"""

from __future__ import annotations

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documents, layoutannotate as la
from test_layoutannotate import _doc, _items, measure


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path / "data_layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / "data_layouts" / documents.AUTOSAVE_DIRNAME)
    m.app.config["TESTING"] = True
    project = tmp_path / "proj"
    (project / "tavottofile").mkdir(parents=True)
    atomicio.write_json(project / "tavottofile" / "组图.json", _doc(), indent=1)
    pj = m.open_project(str(project))["id"]
    yield m.app.test_client(), project, pj
    m.reset_projects()


def _get(client, pj, base):
    headers = {"X-Tavotto-Project": pj} if pj else {}
    q = f"?base_revision={base}" if base else ""
    return client.get(f"/api/layouts/组图/agent-additions{q}", headers=headers).get_json()


def test_desktop_learns_the_agents_additions_since_its_base(env):
    client, project, pj = env
    base = la.read_scene(project, "组图", measure)["base_revision"]
    plan = la.propose(project, "组图", _items(), measure, base_revision=base)
    out = la.commit(
        project, "组图", _items(), measure, base_revision=base, echo_digest_seen=plan["echo_digest"]
    )
    got = _get(client, pj, base)
    assert got["available"] is True and got["revision"] == out["revision"] and got["count"] == 2
    assert [o["id"] for o in got["canvases"][0]["objects"]] == out["added"]
    assert _get(client, pj, out["revision"]) == {"available": False}  # 已经跟上
    assert _get(client, pj, "") == {"available": False}  # 没基线
    m.reset_projects()
    assert _get(client, None, base) == {"available": False}  # 没打开项目
