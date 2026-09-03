"""Session 22 的遥测接线：刷新与包操作在**成功边界**上记一条粗粒度事件。

守三件事：只收白名单来由；桶名闭集、条数本身不出网；中间态一条都不记。
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import deprepair, project_watch as engine_watch, telemetry


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    telemetry.set_consent(telemetry.CONSENT_ENABLED, source="settings")
    box.clear()
    yield box
    telemetry.reset_for_tests()


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _project(tmp_path):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    return figs


def _events(sent, name):
    telemetry.flush(5.0)
    return [p for p in sent if p["event"] == name]


class TestRefreshCompleted:
    def test_manual_refresh_with_no_change_is_bucket_none(self, client, tmp_path, sent):
        figs = _project(tmp_path)
        pj = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
        client.post(f"/api/project/refresh?pj={pj}", json={"reason": "manual"})
        (ev,) = _events(sent, "project_refresh_completed")
        assert ev["properties"]["source"] == "manual"
        assert ev["properties"]["changed_bucket"] == "none"
        # 没有任何一个脚本名 / 路径进 payload
        assert "fig1" not in json.dumps(ev) and str(figs) not in json.dumps(ev)

    def test_one_new_script_is_bucket_one(self, client, tmp_path, sent):
        figs = _project(tmp_path)
        pj = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
        # 静态发现要看得见 stem：只有 `pass` 的脚本不会被登记
        (figs / "fig2.py").write_text(
            "def main():\n    fig.savefig('Fig2.pdf')\n", encoding="utf-8"
        )
        client.post(f"/api/project/refresh?pj={pj}", json={"reason": "codex"})
        (ev,) = _events(sent, "project_refresh_completed")
        assert ev["properties"]["source"] == "codex"
        assert ev["properties"]["changed_bucket"] == "one"

    def test_unknown_reason_is_normalized_to_manual(self, client, tmp_path, sent):
        figs = _project(tmp_path)
        pj = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
        client.post(f"/api/project/refresh?pj={pj}", json={"reason": "../../etc"})
        (ev,) = _events(sent, "project_refresh_completed")
        assert ev["properties"]["source"] == "manual"

    def test_probe_and_registry_reasons_are_not_captured(self, client, tmp_path, sent):
        figs = _project(tmp_path)
        pj = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
        ctx = m.PROJECTS[pj]
        m.refresh_project(ctx, reason="probe", allow_static_merge=False)
        m.refresh_project(ctx, reason="registry", allow_static_merge=False)
        assert _events(sent, "project_refresh_completed") == []

    def test_failed_refresh_sends_nothing(self, client, tmp_path, sent):
        figs = _project(tmp_path)
        pj = client.post("/api/projects/open", json={"path": str(figs)}).get_json()["id"]
        (figs / "tavotto_registry.json").write_text("{broken", encoding="utf-8")
        res = client.post(f"/api/project/refresh?pj={pj}", json={"reason": "manual"})
        assert res.status_code == 400
        assert _events(sent, "project_refresh_completed") == []

    @pytest.mark.parametrize(
        "n,bucket", [(0, "none"), (1, "one"), (2, "few"), (5, "few"), (6, "many"), (40, "many")]
    )
    def test_bucket_boundaries(self, n, bucket):
        result = {
            "registry": {
                "added_scripts": [f"s{i}.py" for i in range(n)],
                "removed_scripts": [],
                "changed_scripts": [],
            },
            "assets": {"added": [], "removed": [], "changed": []},
        }
        assert m._refresh_changed_bucket(result) == bucket


class TestPackageAction:
    def test_terminal_states_only(self, sent):
        m._capture_package_action(deprepair.OP_INSTALL, {"state": deprepair.STATE_INSTALLING})
        m._capture_package_action(deprepair.OP_INSTALL, {"state": deprepair.STATE_VERIFYING})
        assert _events(sent, "package_action") == []
        m._capture_package_action(
            deprepair.OP_INSTALL, {"state": deprepair.STATE_DONE, "distribution": "秘密包"}
        )
        m._capture_package_action(deprepair.OP_UPDATE, {"state": deprepair.STATE_FAILED})
        m._capture_package_action(deprepair.OP_UNINSTALL, {"state": deprepair.STATE_CANCELLED})
        evs = _events(sent, "package_action")
        assert [(e["properties"]["action"], e["properties"]["outcome"]) for e in evs] == [
            ("install", "ok"),
            ("update", "failed"),
            ("remove", "cancelled"),
        ]
        assert "秘密包" not in json.dumps(evs), "包名可能泄露私有依赖，不发"


def test_consent_version_was_bumped_for_the_new_events():
    """采集范围实质性扩大 → CONSENT_VERSION 升版，旧同意失效、重新征求。"""
    assert telemetry.CONSENT_VERSION >= 2
    for name in (
        "project_refresh_completed",
        "project_readiness_opened",
        "tutorial_started",
        "tutorial_step_completed",
        "tutorial_completed",
        "context_bar_multi_used",
        "document_saved",
        "recovery_action",
        "package_action",
    ):
        assert name in telemetry.EVENTS


def test_tutorial_step_ids_match_the_frontend_closed_set():
    """`step_id` 的枚举与 `web/src/lib/onboarding/stepIds.ts` 逐字同源。"""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "web/src/lib/onboarding/stepIds.ts").read_text(
        encoding="utf-8"
    )
    block = re.search(r"STEP_IDS = \[(.*?)\] as const", src, re.S).group(1)
    ids = re.findall(r"'([a-z_]+)'", block)
    assert tuple(ids) == telemetry.EVENTS["tutorial_step_completed"]["step_id"]["values"]
