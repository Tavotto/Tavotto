"""ADR 0211 第一期 6：注册表记脚本内容指纹（sha256），脚本挪走后在项目里找字节相同的那一份接回。

* 写入时机：首次起草、统一刷新并入新脚本、试运行 / 手工登记（`discover.register`）；只读打开不写；
* 指纹不进 `entries()`（`project_refresh` 拿它比相等判 worker 过期）；非法值当没有；
* `GET /api/registry/relink-candidates`：脚本确实登记着且不在 → 列出字节相同的 `matches` 与全部项目脚本；
  老登记没有指纹 → `matches` 为空（界面让用户选）；同名但内容不同的不算匹配（不按文件名猜，ADR 0027 §5）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import discover, documents, project_watch as engine_watch, registry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "figures"
PLOT = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n\n\ndef main():\n    fig, ax = plt.subplots()\n    ax.plot([0, 1], [0, 1])\n    fig.savefig('plot_a.png')\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(m, "LAYOUT_DIR", d / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _open(client, root: Path) -> dict:
    body = client.post("/api/projects/open", json={"path": str(root)}).get_json()
    assert body.get("id"), body
    return body


def _entry(root: Path, script: str) -> dict:
    return json.loads((root / registry.REGISTRY_NAME).read_text(encoding="utf-8"))["scripts"][
        script
    ]


def _candidates(client, pj: dict, script: str) -> dict:
    r = client.get(
        "/api/registry/relink-candidates",
        query_string={"script": script},
        headers={"X-Tavotto-Project": pj["id"]},
    )
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _project(tmp_path: Path, *, fingerprint: bool) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "plot.py").write_text(PLOT, encoding="utf-8")
    entry = {"entry": "main", "cost": "light", "stems": ["plot_a"]}
    if fingerprint:
        entry["sha256"] = _sha(PLOT)
    (root / registry.REGISTRY_NAME).write_text(
        json.dumps({"version": 1, "scripts": {"plot.py": entry}}), encoding="utf-8"
    )
    return root


def test_first_open_draft_records_fingerprints(client, tmp_path):
    root = tmp_path / "figs"
    shutil.copytree(EXAMPLES, root)
    (root / registry.REGISTRY_NAME).unlink()
    _open(client, root)
    entry = _entry(root, "fig1_kinetics.py")
    assert entry["sha256"] == hashlib.sha256((root / "fig1_kinetics.py").read_bytes()).hexdigest()


def test_register_records_and_refreshes_the_fingerprint(tmp_path):
    root = _project(tmp_path, fingerprint=False)
    discover.register(root, "plot.py", ["plot_a"])
    assert _entry(root, "plot.py")["sha256"] == _sha(PLOT)
    # 用户改了脚本，之后又登记（试运行 / 手工）了一次：指纹跟着换
    edited = PLOT + "# edited\n"
    (root / "plot.py").write_text(edited, encoding="utf-8")
    discover.register(root, "plot.py", ["plot_a"])
    assert _entry(root, "plot.py")["sha256"] == _sha(edited)
    # 脚本已经不在时再登记（只是摘 / 并一张图）：沿用旧指纹——挪走之后正要靠它认出那一份
    (root / "plot.py").unlink()
    discover.register(root, "plot.py", ["plot_a", "plot_b"])
    assert _entry(root, "plot.py")["sha256"] == _sha(edited)


def test_fingerprint_stays_out_of_entries_and_bad_values_are_ignored(tmp_path):
    reg = registry.Registry()
    reg.load_data(
        {
            "scripts": {
                "a.py": {"entry": "main", "stems": ["a"], "sha256": _sha("x")},
                "b.py": {"entry": "main", "stems": ["b"], "sha256": "not-a-hash"},
            }
        }
    )
    assert reg.fingerprint_of("a.py") == _sha("x")
    assert reg.fingerprint_of("b.py") is None
    assert all("sha256" not in e for e in reg.entries().values())


def test_static_merge_does_not_write_just_to_refresh_a_fingerprint(tmp_path):
    """统一刷新的静态合并只给**新并入**的脚本记指纹；已有条目原样——用户改脚本不触发重写注册表。"""
    root = _project(tmp_path, fingerprint=True)
    (root / "plot.py").write_text(PLOT + "# edited\n", encoding="utf-8")
    cfg, _rep, _changes = discover.merge(root)
    assert cfg["scripts"]["plot.py"]["sha256"] == _sha(PLOT)


def test_moved_script_is_matched_by_bytes(client, tmp_path):
    root = _project(tmp_path, fingerprint=True)
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    # 同名但内容不同的不算；名字不同、字节相同的才算
    (root / "other").mkdir()
    (root / "other" / "plot.py").write_text(PLOT + "# different\n", encoding="utf-8")
    pj = _open(client, root)
    got = _candidates(client, pj, "plot.py")
    assert got["missing"] is True and got["fingerprint"] is True
    assert got["matches"] == ["scripts/plot.py"]
    assert set(got["scripts"]) >= {"scripts/plot.py", "other/plot.py"}
    # 两份字节相同：都列出来，不替用户挑
    shutil.copy(root / "scripts" / "plot.py", root / "copy_of_plot.py")
    assert sorted(_candidates(client, pj, "plot.py")["matches"]) == [
        "copy_of_plot.py",
        "scripts/plot.py",
    ]


def test_old_registration_without_fingerprint_offers_only_the_list(client, tmp_path):
    root = _project(tmp_path, fingerprint=False)
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    got = _candidates(client, pj, "plot.py")
    assert got["missing"] is True and got["fingerprint"] is False
    assert got["matches"] == []
    assert "scripts/plot.py" in got["scripts"]


def test_present_or_unknown_script_offers_nothing(client, tmp_path):
    root = _project(tmp_path, fingerprint=True)
    pj = _open(client, root)
    assert _candidates(client, pj, "plot.py")["missing"] is False
    assert _candidates(client, pj, "nope.py")["missing"] is False


def test_open_alone_does_not_write_the_registry(client, tmp_path):
    """只读打开（已有注册表）不为了补指纹写项目文件。"""
    root = _project(tmp_path, fingerprint=False)
    before = (root / registry.REGISTRY_NAME).read_bytes()
    _open(client, root)
    assert (root / registry.REGISTRY_NAME).read_bytes() == before
