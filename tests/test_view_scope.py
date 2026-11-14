"""ADR 0184 §三：素材区的视图范围——带脚本进来只看脚本的图，明确打开文件夹回到整个文件夹，重开沿用。

范围只是显示：`/api/panels` 的清单本身一份不少，只多一个 `view_scope` 字段。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import config as engine_config, project_watch as engine_watch


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "plot.py").write_text("import matplotlib.pyplot as plt\nplt.savefig('fig1.pdf')\n")
    return root


def _open(client, root: Path, **extra):
    r = client.post("/api/projects/open", json={"path": str(root), "default": True, **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _scope(client):
    return client.get("/api/panels").get_json()["view_scope"]


def test_default_is_whole_folder(client, tmp_path):
    _open(client, _project(tmp_path))
    assert _scope(client) == {"mode": "folder", "scripts": []}


def test_script_entry_scopes_to_scripts_and_reopen_keeps_it(client, tmp_path):
    root = _project(tmp_path)
    status = _open(client, root, entry="script", script="plot.py")
    assert status["settings"]["view_scope"] == {"mode": "scripts", "scripts": ["plot.py"]}
    assert _scope(client) == {"mode": "scripts", "scripts": ["plot.py"]}
    # 最近列表重开（不带 entry）：沿用
    m.reset_projects()
    _open(client, root)
    assert _scope(client) == {"mode": "scripts", "scripts": ["plot.py"]}
    # 再带另一个脚本进来：加进表里，不替换
    _open(client, root, entry="script", script="sub/other.py")
    assert _scope(client)["scripts"] == ["plot.py", "sub/other.py"]


def test_folder_entry_resets_to_whole_folder(client, tmp_path):
    root = _project(tmp_path)
    _open(client, root, entry="script", script="plot.py")
    _open(client, root, entry="folder")
    assert _scope(client) == {"mode": "folder", "scripts": []}


def test_running_a_script_adds_it_only_in_scripts_mode(client, tmp_path):
    root = _project(tmp_path)
    _open(client, root)
    r = client.post("/api/project/view_scope", json={"add_script": "b.py"})
    assert r.get_json()["view_scope"] == {"mode": "folder", "scripts": []}
    _open(client, root, entry="script", script="plot.py")
    r = client.post("/api/project/view_scope", json={"add_script": "b.py"})
    assert r.get_json()["view_scope"] == {"mode": "scripts", "scripts": ["plot.py", "b.py"]}


@pytest.mark.parametrize("bad", ["", " plot.py", "../x.py", "/abs/x.py", "C:/x.py", "a//b.py", 3])
def test_bad_script_names_never_enter_the_scope(client, tmp_path, bad):
    root = _project(tmp_path)
    _open(client, root, entry="script", script=bad)
    assert _scope(client) == {"mode": "folder", "scripts": []}
    r = client.post("/api/project/view_scope", json={"add_script": bad})
    assert r.status_code == 400 and r.get_json()["code"] == "invalid_script_name"


def test_scope_does_not_trim_the_panel_list(client, tmp_path):
    root = _project(tmp_path)
    loose = root / "raw"
    loose.mkdir()
    (root / "fig1.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10"/>'
    )
    (loose / "x.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10"/>'
    )
    _open(client, root)
    before = sorted(p["id"] for p in client.get("/api/panels").get_json()["panels"])
    _open(client, root, entry="script", script="plot.py")
    after = sorted(p["id"] for p in client.get("/api/panels").get_json()["panels"])
    assert before == after and len(after) == 2


def test_corrupt_stored_scope_falls_back_to_folder(tmp_path):
    engine_config.set_project_settings(
        str(tmp_path), {"view_scope": {"mode": "scripts", "scripts": 7}}
    )
    assert engine_config.view_scope(str(tmp_path)) == {"mode": "scripts", "scripts": []}
    engine_config.set_project_settings(str(tmp_path), {"view_scope": "nope"})
    assert engine_config.view_scope(str(tmp_path)) == {"mode": "folder", "scripts": []}
