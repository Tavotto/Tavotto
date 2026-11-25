"""ADR 0211 第一期 5：项目整夹挪走后，按路径作键的本机设置随认领迁到新路径。

真实 Flask test client + 隔离数据 / 配置目录；「挪走」= 真 `rename`，「复制」= 真 `copytree`。

* 挪走 + 认领成功：改指表（项目内的 `to` 换新根、项目外的原样）、运行配置（不含启动记录）、输入转录迁到新路径；
* 运行许可 / 执行策略 / 记住的解释器**不迁**（ADR 0021 §7.1）；
* 复制不迁（旧项目的设置留给旧项目）；
* 一项失败不挡别的、不让认领失败，留待办标记，下次打开项目重试；迁移幂等；旧键保留。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import (
    config,
    documents,
    inputremap,
    inputtranscript,
    layoutsession,
    project_watch as engine_watch,
    projectmigrate,
    runconfig,
)

TEXT = {
    "id": "t1",
    "type": "text",
    "text": "hello",
    "x": 1,
    "y": 2,
    "w": 10,
    "h": 5,
    "sizePt": 9,
    "bold": False,
    "color": "#000",
    "align": "left",
}

LAUNCH = {
    "interpreter": {"path": "/usr/bin/python3", "source": "current_process", "generation": "g1"},
    "launcher": {"kind": "direct"},
    "env_state": "ok",
    "cwd_mode": "script",
    "source": {"script_sha1": "abc", "basis": {}},
}


def _project_doc() -> dict:
    return {
        "schema": 3,
        "project": {"id": "p", "name": "排版一"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": [TEXT],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 1,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(m, "LAYOUT_DIR", d / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    monkeypatch.setattr(m, "VERSIONS_DIR", d / "layouts" / "_versions")
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _open(client, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    body = client.post("/api/projects/open", json={"path": str(root)}).get_json()
    assert body.get("id"), body
    return body


def _h(pj: dict) -> dict:
    return {"X-Tavotto-Project": pj["id"]}


def _seed_project(client, root: Path, outside: Path) -> tuple[dict, str]:
    """旧路径里：编辑一份排版（槽位 + 节点），指认过数据（项目内一条、项目外一条），存过运行参数（带启动记录）、
    输入转录，记住过运行许可 / 执行策略 / 解释器。回 (项目, 槽位 revision)。"""
    pj = _open(client, root)
    r = client.put("/api/autosave/d_mv", json=_project_doc(), headers=_h(pj))
    assert r.status_code == 200, r.get_json()
    v = client.post(
        "/api/versions/d_mv",
        json={"doc": {**_project_doc()["canvases"][0], "schema": 2}, "auto": True},
        headers=_h(pj),
    )
    assert v.get_json().get("version"), v.get_json()
    (root / "data").mkdir()
    (root / "data" / "x.csv").write_text("1,2\n", encoding="utf-8")
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "y.csv").write_text("3,4\n", encoding="utf-8")
    inputremap.add_rule(root, {"kind": "prefix", "from": "/old/data", "to": str(root / "data")})
    inputremap.add_rule(root, {"kind": "file", "from": "y.csv", "to": str(outside / "y.csv")})
    rc = runconfig.put(root, "plot.py", ["--n", "3"])
    store = runconfig.store_path(root)
    data = json.loads(store.read_text(encoding="utf-8"))
    data["launches"] = {"plot.py": LAUNCH}
    store.write_text(json.dumps(data), encoding="utf-8")
    inputtranscript.bind(
        root, "plot.py", rc.id, [{"index": 0, "kind": "input", "prompt": "n?", "answer": "3"}]
    )
    config.set_project_settings(
        str(root),
        {
            "native_run_permissions": {"k": {"interpreter": "/usr/bin/python3"}},
            "execution_policy": {"mode": "native"},
            "environment": {"python": "/usr/bin/python3"},
        },
    )
    return pj, r.get_json()["revision"], rc.id


def _claim(client, pj, rev):
    return client.post(
        "/api/layout-session/project-history/claim",
        json={"doc_id": "d_mv", "revision": rev},
        headers=_h(pj),
    )


def test_moved_project_carries_remap_runconfigs_and_transcripts(client, tmp_path):
    a_root, b_root, outside = tmp_path / "projA", tmp_path / "projB", tmp_path / "elsewhere"
    _a, rev, rc_id = _seed_project(client, a_root, outside)
    a_settings = dict(config.project_settings(str(a_root)))
    a_runconfig = runconfig.store_path(a_root).read_bytes()
    a_root.rename(b_root)  # 整夹挪走
    b = _open(client, b_root)
    assert _claim(client, b, rev).status_code == 200

    rules = {r["from"]: r for r in inputremap.rules_for(b_root)}
    # 项目内的规则换成新根；项目外的原样
    assert rules["/old/data"]["to"] == str(b_root / "data")
    assert rules["y.csv"]["to"] == str(outside / "y.csv")
    # 运行配置：引用 id 不变，argv 原样；启动记录（解释器证据）不迁
    got = runconfig.get(b_root, rc_id, script="plot.py")
    assert got.argv == ("--n", "3")
    assert "launches" not in json.loads(runconfig.store_path(b_root).read_text(encoding="utf-8"))
    # 输入转录
    assert inputtranscript.lookup(b_root, "plot.py", rc_id) is not None
    # 运行许可 / 执行策略 / 记住的解释器不迁（ADR 0021 §7.1：项目移动后重新确认）
    b_settings = config.project_settings(str(b_root))
    for key in projectmigrate.NOT_MIGRATED_SETTINGS:
        assert key not in b_settings, key
    assert projectmigrate.PENDING_KEY not in b_settings
    # 旧键原样保留
    assert config.project_settings(str(a_root)) == a_settings
    assert runconfig.store_path(a_root).read_bytes() == a_runconfig


def test_copied_project_migrates_nothing(client, tmp_path):
    a_root, c_root, outside = tmp_path / "projA", tmp_path / "projC", tmp_path / "elsewhere"
    _a, rev, rc_id = _seed_project(client, a_root, outside)
    shutil.copytree(a_root, c_root)  # 复制：旧路径还在
    c = _open(client, c_root)
    assert _claim(client, c, rev).status_code == 409
    assert inputremap.rules_for(c_root) == []
    assert not runconfig.store_path(c_root).exists()
    assert not inputtranscript.store_path(c_root).exists()


def test_failed_step_keeps_claim_and_retries_on_next_open(client, tmp_path, monkeypatch):
    a_root, b_root, outside = tmp_path / "projA", tmp_path / "projB", tmp_path / "elsewhere"
    _a, rev, rc_id = _seed_project(client, a_root, outside)
    a_root.rename(b_root)
    b = _open(client, b_root)
    real = runconfig.migrate_store

    def boom(*_a, **_k):
        raise OSError("磁盘一时写不进去")

    monkeypatch.setattr(runconfig, "migrate_store", boom)
    r = _claim(client, b, rev)
    assert r.status_code == 200, r.get_json()  # 迁移失败不让认领失败
    assert layoutsession.snapshot()["owners"]["d_mv"] == layoutsession.project_key(b_root)
    # 别的两项照迁；没迁完留待办
    assert inputremap.rules_for(b_root)
    assert inputtranscript.lookup(b_root, "plot.py", rc_id) is not None
    assert not runconfig.store_path(b_root).exists()
    pending = config.project_settings(str(b_root))[projectmigrate.PENDING_KEY]
    assert os.path.normcase(pending["from"]) == os.path.normcase(str(a_root))
    # 好了之后重新打开项目：重试一次，补上、摘掉待办；已迁过的不重复
    monkeypatch.setattr(runconfig, "migrate_store", real)
    m.reset_projects()
    _open(client, b_root)
    assert runconfig.get(b_root, rc_id, script="plot.py").argv == ("--n", "3")
    assert projectmigrate.PENDING_KEY not in config.project_settings(str(b_root))
    assert len(inputremap.rules_for(b_root)) == 2


def test_migration_is_idempotent_and_keeps_rules_made_on_the_new_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    old, new = tmp_path / "old", tmp_path / "new"
    (new / "data").mkdir(parents=True)
    (new / "data" / "x.csv").write_text("1\n", encoding="utf-8")
    (new / "z.csv").write_text("1\n", encoding="utf-8")
    config.set_project_settings(
        str(old),
        {
            inputremap.SETTINGS_KEY: {
                "rules": [
                    {"kind": "prefix", "from": "data", "to": str(old / "data")},
                    {"kind": "file", "from": "z.csv", "to": str(old / "z.csv")},
                ]
            }
        },
    )
    # 用户已经在新路径上重新指认过 z.csv：那条更新，不被旧表盖掉
    inputremap.add_rule(new, {"kind": "file", "from": "z.csv", "to": str(new / "z.csv")})
    first = projectmigrate.claim_moved(str(old), new)
    second = projectmigrate.claim_moved(str(old), new)
    assert first["input_remap"] == 1 and second["input_remap"] == 0
    rules = {r["from"]: r["to"] for r in inputremap.rules_for(new)}
    assert rules == {"data": str(new / "data"), "z.csv": str(new / "z.csv")}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("{old}/data/x.csv", "{new}/data/x.csv"),
        ("{old}", "{new}"),
        ("{old}-sibling/x.csv", "{old}-sibling/x.csv"),  # 只是名字前缀相同，不在项目里
        ("/somewhere/else/x.csv", "/somewhere/else/x.csv"),
        ("relative/x.csv", "relative/x.csv"),
    ],
)
def test_rebase_path_only_touches_paths_inside_the_old_root(tmp_path, path, expected):
    old, new = str(tmp_path / "old"), str(tmp_path / "new")
    got = projectmigrate.rebase_path(path.format(old=old, new=new), old, new)
    assert os.path.normpath(got) == os.path.normpath(expected.format(old=old, new=new))


def test_rebase_path_sees_through_symlinked_prefixes(tmp_path):
    """用户指认时的写法可能走了符号链接（macOS 的 `/var` → `/private/var`），归属记的是解析后的路径：同一处要认得出。"""
    real = tmp_path / "real"
    (real / "old" / "raw").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    got = projectmigrate.rebase_path(
        str(link / "old" / "raw"), str(real / "old"), str(tmp_path / "new")
    )
    assert os.path.normpath(got) == os.path.normpath(str(tmp_path / "new" / "raw"))


def test_absolute_from_inside_the_old_root_gets_a_rebased_twin(tmp_path, monkeypatch):
    """脚本用 `__file__` 拼出的绝对路径（`BASE_DIR / "values.txt"`）：规则的 `from` 是旧项目根内的绝对路径。
    挪走后脚本要的是新根下那一串——另加一条 `from` 换根的规则（`to` 同样换根），原规则留着。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    old, new = tmp_path / "old", tmp_path / "new"
    (new / "raw").mkdir(parents=True)
    (new / "raw" / "values.txt").write_text("1\n", encoding="utf-8")
    config.set_project_settings(
        str(old),
        {
            inputremap.SETTINGS_KEY: {
                "rules": [
                    {
                        "kind": "file",
                        "from": str(old / "values.txt"),
                        "to": str(old / "raw" / "values.txt"),
                    }
                ]
            }
        },
    )
    projectmigrate.claim_moved(str(old), new)
    rules = {r["from"]: r["to"] for r in inputremap.rules_for(new)}
    real_new = os.path.realpath(new)
    assert rules[os.path.join(real_new, "values.txt")] == str(new / "raw" / "values.txt")
    assert rules[str(old / "values.txt")] == str(new / "raw" / "values.txt")


def test_copied_project_inherits_only_the_remap_rules(client, tmp_path):
    """复制（旧路径还在）：复制品另开一份排版时拷一份改指表（项目内的换新根、项目外原样），复制源一个字节不动；
    运行配置与输入转录不跟；幂等（维护者 2026-11-26 拍板）。"""
    a_root, c_root, outside = tmp_path / "projA", tmp_path / "projC", tmp_path / "elsewhere"
    _a, rev, rc_id = _seed_project(client, a_root, outside)
    a_settings = json.dumps(config.project_settings(str(a_root)), sort_keys=True)
    shutil.copytree(a_root, c_root)
    c = _open(client, c_root)

    def copied():
        r = client.post(
            "/api/layout-session/project-history/copied", json={"doc_id": "d_mv"}, headers=_h(c)
        )
        assert r.status_code == 200, r.get_json()
        return r.get_json()["copied"]

    assert copied() == 2
    rules = {r["from"]: r["to"] for r in inputremap.rules_for(c_root)}
    assert rules == {"/old/data": str(c_root / "data"), "y.csv": str(outside / "y.csv")}
    assert copied() == 0  # 幂等
    # 复制源原样；运行配置、输入转录、运行许可都不跟
    assert json.dumps(config.project_settings(str(a_root)), sort_keys=True) == a_settings
    assert not runconfig.store_path(c_root).exists()
    assert not inputtranscript.store_path(c_root).exists()
    for key in projectmigrate.NOT_MIGRATED_SETTINGS:
        assert key not in config.project_settings(str(c_root))


def test_copied_endpoint_does_nothing_for_a_move(client, tmp_path):
    """不是复制（挪走：旧路径不在）时这个端点什么都不做——挪走走认领那条迁移。"""
    a_root, b_root, outside = tmp_path / "projA", tmp_path / "projB", tmp_path / "elsewhere"
    _seed_project(client, a_root, outside)
    a_root.rename(b_root)
    b = _open(client, b_root)
    r = client.post(
        "/api/layout-session/project-history/copied", json={"doc_id": "d_mv"}, headers=_h(b)
    )
    assert r.get_json()["copied"] == 0
    assert inputremap.rules_for(b_root) == []
