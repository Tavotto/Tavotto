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


def test_fingerprint_round_trips_through_entries_and_bad_values_are_ignored(tmp_path):
    """`entries()` 与磁盘条目同形：刷新拿「登记时写下的条目」和此刻的 `entries()` 比相等判 worker 是否仍有效，
    少了 sha256 会让每次登记后的活 worker 被当成过期收掉；读回再写也不能丢指纹。"""
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
    assert reg.entries()["a.py"]["sha256"] == _sha("x")
    assert "sha256" not in reg.entries()["b.py"]


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


# ---------------------------------------------------------------- 参数随接回带走（维护者 2026-11-26 拍板）


def _seed_state(root: Path, script: str = "plot.py") -> str:
    """旧脚本键下：一份默认运行配置（argv）、一条带上下文的答案、一份输入转录。回配置引用。"""
    from tavotto.engine import inputtranscript, runconfig, scriptanswers

    rc = runconfig.put(root, script, ["--n", "3"])
    runconfig.set_default(root, script, rc.id)
    scriptanswers.remember(root, script, 0, "n?", "3", context="ctx", run_config=rc.id)
    inputtranscript.bind(
        root, script, rc.id, [{"index": 0, "kind": "input", "prompt": "n?", "answer": "3"}]
    )
    return rc.id


def _state_under(root: Path, script: str) -> dict:
    from tavotto.engine import inputtranscript, runconfig, scriptanswers

    with inputtranscript._LOCK:
        bindings, gens = inputtranscript._load(root)
    sep = inputtranscript._SEP
    return {
        "configs": [(c.script, c.argv) for c in runconfig.configs_of(root, script)],
        "answers": scriptanswers.entries(root, script),
        "transcripts": sorted(
            k for k in [*bindings, *gens] if k == script or k.startswith(script + sep)
        ),
    }


def test_relink_carries_argv_answers_and_transcripts_on_success(client, tmp_path):
    from tavotto.engine import runconfig, scriptrelink

    root = _project(tmp_path, fingerprint=True)
    old_rc = _seed_state(root)
    old_before = _state_under(root, "plot.py")
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    r = client.post(
        "/api/registry/relink/prepare",
        json={"from": "plot.py", "to": "scripts/plot.py"},
        headers={"X-Tavotto-Project": pj["id"]},
    )
    assert r.status_code == 200, r.get_json()
    new_rc = r.get_json()["run_config"]
    assert new_rc and new_rc != old_rc
    # 验证试跑用的就是带过来的那份 argv
    assert runconfig.get(root, new_rc, script="scripts/plot.py").argv == ("--n", "3")
    after = _state_under(root, "scripts/plot.py")
    assert after["answers"] and after["answers"][0]["answer"] == "3"
    assert after["transcripts"]
    # 试跑成功、新脚本认领了图（模拟试运行登记），收尾：状态留下；旧键一个字节不动
    discover.register(root, "scripts/plot.py", ["plot_a"])
    r = client.post(
        "/api/registry/relink/finish",
        json={"from": "plot.py", "to": "scripts/plot.py", "ok": True},
        headers={"X-Tavotto-Project": pj["id"]},
    )
    assert r.get_json()["result"] == "committed"
    assert _state_under(root, "scripts/plot.py") == after
    assert _state_under(root, "plot.py") == old_before
    from tavotto.engine import config

    assert scriptrelink.PENDING_KEY not in config.project_settings(str(root))


def test_relink_failure_leaves_nothing_under_the_new_key(client, tmp_path):
    root = _project(tmp_path, fingerprint=True)
    _seed_state(root)
    old_before = _state_under(root, "plot.py")
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    h = {"X-Tavotto-Project": pj["id"]}
    client.post(
        "/api/registry/relink/prepare", json={"from": "plot.py", "to": "scripts/plot.py"}, headers=h
    )
    r = client.post(
        "/api/registry/relink/finish",
        json={"from": "plot.py", "to": "scripts/plot.py", "ok": False},
        headers=h,
    )
    assert r.get_json()["result"] == "aborted"
    assert _state_under(root, "scripts/plot.py") == {
        "configs": [],
        "answers": [],
        "transcripts": [],
    }
    assert _state_under(root, "plot.py") == old_before


def test_relink_claimed_ok_without_ownership_is_still_undone(client, tmp_path):
    """前端说接上了，但注册表里新脚本并没有认领任何图：按事实撤销。"""
    root = _project(tmp_path, fingerprint=True)
    _seed_state(root)
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    h = {"X-Tavotto-Project": pj["id"]}
    client.post(
        "/api/registry/relink/prepare", json={"from": "plot.py", "to": "scripts/plot.py"}, headers=h
    )
    r = client.post(
        "/api/registry/relink/finish",
        json={"from": "plot.py", "to": "scripts/plot.py", "ok": True},
        headers=h,
    )
    assert r.get_json()["result"] == "aborted"
    assert _state_under(root, "scripts/plot.py")["configs"] == []


def test_stale_pending_is_settled_from_the_registry(client, tmp_path):
    """关了页面停在两步之间：下一次列候选时按注册表此刻的事实收尾（没认领 = 撤销）。"""
    root = _project(tmp_path, fingerprint=True)
    _seed_state(root)
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    h = {"X-Tavotto-Project": pj["id"]}
    client.post(
        "/api/registry/relink/prepare", json={"from": "plot.py", "to": "scripts/plot.py"}, headers=h
    )
    assert _state_under(root, "scripts/plot.py")["configs"]
    _candidates(client, pj, "plot.py")
    assert _state_under(root, "scripts/plot.py")["configs"] == []


def test_relink_does_not_touch_a_new_key_that_already_has_state(client, tmp_path):
    """用户已经在新位置配过参数：什么都不带（他新给的更新），撤销也不会误删他的。"""
    from tavotto.engine import runconfig

    root = _project(tmp_path, fingerprint=True)
    _seed_state(root)
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    mine = runconfig.put(root, "scripts/plot.py", ["--n", "9"])
    pj = _open(client, root)
    h = {"X-Tavotto-Project": pj["id"]}
    r = client.post(
        "/api/registry/relink/prepare", json={"from": "plot.py", "to": "scripts/plot.py"}, headers=h
    )
    assert r.get_json() == {"run_config": None, "carried": False}
    client.post(
        "/api/registry/relink/finish",
        json={"from": "plot.py", "to": "scripts/plot.py", "ok": False},
        headers=h,
    )
    assert [c.id for c in runconfig.configs_of(root, "scripts/plot.py")] == [mine.id]


def test_relink_never_carries_run_permissions(client, tmp_path):
    from tavotto.engine import config

    root = _project(tmp_path, fingerprint=True)
    _seed_state(root)
    config.set_project_settings(
        str(root), {"native_run_permissions": {"plot.py": {"interpreter": "/usr/bin/python3"}}}
    )
    before = json.dumps(config.project_settings(str(root)).get("native_run_permissions"))
    (root / "scripts").mkdir()
    (root / "plot.py").rename(root / "scripts" / "plot.py")
    pj = _open(client, root)
    client.post(
        "/api/registry/relink/prepare",
        json={"from": "plot.py", "to": "scripts/plot.py"},
        headers={"X-Tavotto-Project": pj["id"]},
    )
    assert json.dumps(config.project_settings(str(root)).get("native_run_permissions")) == before
