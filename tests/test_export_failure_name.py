"""导出失败按**图名**说话（T-009）：`export_render_failed` 带 `name`，`id` 仍是文件 id。

用户看到的原句是「figures/fig1.pdf 没能重新画出：…」——认的是图名，不是项目相对路径。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto import app
from tavotto.engine import exportreq

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"


class _Reg:
    """只够用的注册表：`panel.pdf` 是分开登记的 `panel-2`，界面显示名是原图名 `Panel B`。"""

    def artifact_stem(self, rel: str) -> str:
        return {"figures/panel.pdf": "panel-2"}.get(rel, Path(rel).stem)

    def display_name(self, stem: str) -> str:
        return {"panel-2": "Panel B"}.get(stem, stem)


def test_name_comes_from_the_registry(monkeypatch):
    monkeypatch.setattr(app, "current_registry", lambda: _Reg())
    assert app._export_figure_name("figures/panel.pdf") == "Panel B"
    assert app._export_figure_name("figures\\fig1.pdf") == "fig1"


def test_without_a_project_the_file_stem_is_the_name(monkeypatch):
    def boom():
        raise RuntimeError("no project")

    monkeypatch.setattr(app, "current_registry", boom)
    assert app._export_figure_name("figures/fig1.pdf") == "fig1"
    assert app._export_figure_name("") == ""
    assert app._export_figure_name(None) == ""


def test_failure_gets_a_name_and_keeps_its_id(monkeypatch):
    monkeypatch.setattr(app, "current_registry", lambda: _Reg())
    exc = exportreq.ExportRequestError(
        "export_render_failed", "x", {"id": "figures/panel.pdf", "reason": "boom"}
    )
    assert app._name_export_failure(exc) is exc
    assert exc.params == {"id": "figures/panel.pdf", "name": "Panel B", "reason": "boom"}


def test_canvas_failure_prefers_the_figure_it_points_at(monkeypatch):
    monkeypatch.setattr(app, "current_registry", lambda: _Reg())
    exc = exportreq.ExportRequestError(
        "export_render_failed",
        "x",
        {"id": "obj-1", "figure": "figures/panel.pdf", "reason": "source:source_missing: x"},
    )
    app._name_export_failure(exc)
    assert exc.params["name"] == "Panel B"
    assert exc.params["id"] == "obj-1"


def test_other_codes_and_given_names_are_left_alone(monkeypatch):
    monkeypatch.setattr(app, "current_registry", lambda: _Reg())
    other = exportreq.ExportRequestError("svg_source_unsupported", "x", {"id": "figures/panel.pdf"})
    app._name_export_failure(other)
    assert "name" not in other.params
    named = exportreq.ExportRequestError(
        "export_render_failed", "x", {"id": "a", "name": "Fig A", "reason": "r"}
    )
    app._name_export_failure(named)
    assert named.params["name"] == "Fig A"


@pytest.mark.skipif(not (LOCALES / "zh-CN").is_dir(), reason="没有 web/")
@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_copy_names_the_figure_not_the_path(locale):
    text = json.loads((LOCALES / locale / "errors.json").read_text(encoding="utf-8"))["backend"][
        "export_render_failed"
    ]
    assert set(re.findall(r"\{\{(\w+)\}\}", text)) == {"name", "reason"}
