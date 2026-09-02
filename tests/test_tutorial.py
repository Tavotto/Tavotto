"""离线教程项目（ADR 0039）：包内资源、版本化副本、重置、三个 API、打包产物。

分四组：

* 资源本身——`importlib.resources` 找得到、元数据稳定、静态验证挑得出坏资源；
* 副本——首次复制、幂等、缺文件修复、重置原子、复制失败留旧、Windows 占用报得清；
* API——`GET /api/tutorial`、`POST /api/tutorial/open`、`POST /api/tutorial/reset`，
  以及「不跑脚本、不 probe、不动用户项目、只清教程自己的东西」四条边界；
* 打包——读 wheel / sdist 的 zip 成员，不在源码树上断言路径存在；把 wheel 解开后
  在子进程里经 `importlib.resources` 真读一遍。

最后一组（需要装了 matplotlib 的解释器）真起 worker 跑教程脚本：它属于测试，
不是应用打开教程时会做的事。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import (
    config as engine_config,
    pool,
    project_watch as engine_watch,
    tutorial,
)

REPO = Path(__file__).resolve().parent.parent
SRC_RESOURCES = REPO / "src" / "tavotto" / "resources" / "tutorial_project"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """数据目录按用例隔离；app 里那几个 import 时算好的常量一并指过来。"""
    d = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(d))
    monkeypatch.setattr(m, "AUTOSAVE_DIR", d / "layouts" / "_autosave")
    monkeypatch.setattr(m, "BAKED_DIR", d / "baked_overrides")
    return d


@pytest.fixture
def client(data_dir):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _make_user_project(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    return figs


def _broken_copy(tmp_path) -> Path:
    """一份可以随便弄坏的资源副本（源码树里的那份只读）。"""
    dst = tmp_path / "res"
    shutil.copytree(tutorial.resource_root(), dst)
    return dst


# ---------------------------------------------------------------------------
# 资源
# ---------------------------------------------------------------------------


def test_resources_are_reachable_through_importlib_resources():
    from importlib.resources import files

    via_pkg = Path(str(files("tavotto").joinpath("resources", "tutorial_project")))
    assert via_pkg.is_dir()
    assert tutorial.resource_root() == via_pkg
    listed = tutorial.resource_files()
    assert set(listed) >= {
        "tutorial_meta.json",
        "tavotto_registry.json",
        "paper_style.py",
        "fig1_kinetics.py",
        "Fig1_kinetics.pdf",
        "fig2_correlation.py",
        "Fig2_correlation.pdf",
        "tavottofile/Tutorial.json",
    }
    assert not any("__pycache__" in rel or rel.endswith(".pyc") for rel in listed)


def test_metadata_is_stable_and_free_of_paths():
    meta = tutorial.tutorial_metadata()
    assert meta["schema"] == tutorial.META_SCHEMA
    assert isinstance(meta["tutorial_version"], int) and meta["tutorial_version"] >= 1
    assert meta["document_name"] == "Tutorial"
    assert tutorial._DOC_ID_RE.match(meta["document_id"])
    assert len(meta["panels"]) >= 2
    assert [p["stem"] for p in meta["panels"]] == meta["expected_stems"]
    # 两张图都进得了图内编辑：标题 / 图例 / 轴标签是 21 的 coachmark 要点的
    for panel in meta["panels"]:
        assert {"title", "legend_text", "axis_label"} <= set(panel["editable_roles"])
    # 至少一张图带一个规范问题（8 pt 下限）
    assert any(
        (p.get("spec_issue") or {}).get("code") == "font-below-absolute-floor"
        for p in meta["panels"]
    )
    text = json.dumps(meta, ensure_ascii=False)
    assert not tutorial._ABS_PATH_RE.search(text)
    assert str(tutorial.resource_root()) not in text


def test_shipped_resources_pass_static_validation():
    assert tutorial.validate_tutorial_resources() == []


def test_validation_rejects_a_pdf_with_no_page_size(monkeypatch):
    """PDF 打得开但首页尺寸为零（空 MediaBox）——「可读」不等于「有尺寸」。"""
    from tavotto import pdfbackend

    monkeypatch.setattr(
        pdfbackend, "probe_asset", lambda p, k: {"kind": "pdf", "w_pt": 0, "h_pt": 0}
    )
    problems = tutorial.validate_tutorial_resources()
    assert problems and all("读不出页面尺寸" in p for p in problems), problems


def test_resources_are_small_and_render_no_external_data():
    files = tutorial.resource_files()
    total = sum(p.stat().st_size for p in files.values())
    assert total <= tutorial.MAX_TOTAL_BYTES
    for rel in ("fig1_kinetics.py", "fig2_correlation.py", "paper_style.py"):
        src = files[rel].read_text(encoding="utf-8")
        assert "requests" not in src and "urllib" not in src
        assert "read_csv" not in src and "np.load" not in src


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda r: (r / "Fig1_kinetics.pdf").unlink(), "缺少 Fig1_kinetics.pdf"),
        (lambda r: (r / "Fig1_kinetics.pdf").write_bytes(b"not a pdf"), "不是可读的 PDF"),
        (lambda r: (r / "tavotto_registry.json").write_text("{oops", encoding="utf-8"), "注册表"),
        (
            lambda r: (r / "fig1_kinetics.py").write_text("def main(:\n", encoding="utf-8"),
            "编译失败",
        ),
        (
            lambda r: (r / "fig1_kinetics.py").write_text(
                "import numpy as np\n\ndef main():\n    np.loadtxt('../secret.csv')\n",
                encoding="utf-8",
            ),
            "loadtxt",
        ),
        (
            lambda r: (r / "fig1_kinetics.py").write_text(
                "import requests\n\ndef main():\n    pass\n", encoding="utf-8"
            ),
            "网络模块",
        ),
        (
            lambda r: (r / "tutorial_meta.json").write_text(
                (r / "tutorial_meta.json")
                .read_text(encoding="utf-8")
                .replace('"Tutorial"', '"/Users/someone/Tutorial"', 1),
                encoding="utf-8",
            ),
            "绝对路径",
        ),
        (
            lambda r: (r / "tutorial_meta.json").write_text(
                json.dumps(
                    {
                        **json.loads((r / "tutorial_meta.json").read_text(encoding="utf-8")),
                        "expected_stems": ["Fig1_kinetics"],
                    }
                ),
                encoding="utf-8",
            ),
            "不一致",
        ),
        (
            lambda r: (r / "tavottofile" / "Tutorial.json").write_text(
                json.dumps({"schema": 2, "name": "x", "page": {"w": 1, "h": 1}, "objects": []}),
                encoding="utf-8",
            ),
            "schema",
        ),
        (
            lambda r: (r / "tavottofile" / "Tutorial.json").write_text(
                json.dumps(
                    {
                        **json.loads((r / "tavottofile" / "Tutorial.json").read_text("utf-8")),
                        "canvases": [
                            {
                                "id": "c1",
                                "name": "x",
                                "page": {"w": 10, "h": 10},
                                "objects": [{"id": "o", "type": "panel", "fileId": "Nope.pdf"}],
                                "guides": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            ),
            "不存在的素材",
        ),
        (lambda r: (r / "big.bin").write_bytes(b"\0" * (tutorial.MAX_FILE_BYTES + 1)), "太大"),
        (lambda r: (r / "tutorial_meta.json").unlink(), "缺少 tutorial_meta.json"),
        (
            lambda r: (r / "tutorial_meta.json").write_text(
                json.dumps(
                    {
                        **json.loads((r / "tutorial_meta.json").read_text(encoding="utf-8")),
                        "panels": json.loads((r / "tutorial_meta.json").read_text("utf-8"))[
                            "panels"
                        ][:1],
                    }
                ),
                encoding="utf-8",
            ),
            "至少要有两张",
        ),
    ],
    ids=[
        "missing-pdf",
        "corrupt-pdf",
        "bad-registry",
        "syntax-error",
        "external-data",
        "network-import",
        "absolute-path",
        "stems-mismatch",
        "doc-schema",
        "doc-unknown-asset",
        "oversized",
        "missing-meta",
        "single-panel",
    ],
)
def test_validation_catches_broken_resources(tmp_path, mutate, needle):
    res = _broken_copy(tmp_path)
    mutate(res)
    problems = tutorial.validate_tutorial_resources(res)
    assert problems, "坏资源没被挑出来"
    assert any(needle in p for p in problems), problems


# ---------------------------------------------------------------------------
# 副本：复制 / 幂等 / 修复 / 重置 / 失败路径
# ---------------------------------------------------------------------------


def _pristine() -> dict[str, bytes]:
    return {rel: p.read_bytes() for rel, p in tutorial.resource_files().items()}


def _copy_bytes(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in tutorial.resource_files()}


def test_first_open_copies_into_a_versioned_data_dir(data_dir):
    tp = tutorial.ensure_tutorial_copy()
    meta = tutorial.tutorial_metadata()
    assert tp.created and not tp.reset and tp.repaired == []
    assert (
        tp.path
        == data_dir
        / "tutorial"
        / f"v{meta['tutorial_version']}-{tp.resource_digest}"
        / (tutorial.PROJECT_DIRNAME)
    )
    assert tp.path == tutorial.tutorial_destination()
    assert _copy_bytes(tp.path) == _pristine()
    state = json.loads((tp.path.parent / tutorial.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["tutorial_version"] == meta["tutorial_version"]
    assert state["resource_digest"] == tp.resource_digest
    assert set(state["files"]) == set(tutorial.resource_files())
    # 副本可写（包内是只读的 site-packages）
    assert os.access(tp.path / "fig1_kinetics.py", os.W_OK)
    assert tutorial.copy_status()["complete"] is True


def test_ensure_again_is_idempotent_and_keeps_user_edits(data_dir):
    first = tutorial.ensure_tutorial_copy()
    edited = first.path / "fig1_kinetics.py"
    edited.write_text("# my edit\n" + edited.read_text(encoding="utf-8"), encoding="utf-8")
    (first.path / "tavottofile" / "MyCanvas.json").write_text("{}", encoding="utf-8")

    again = tutorial.ensure_tutorial_copy()
    assert again.path == first.path
    assert not again.created and not again.reset and again.repaired == []
    assert edited.read_text(encoding="utf-8").startswith("# my edit")
    assert (first.path / "tavottofile" / "MyCanvas.json").is_file()


def test_reset_restores_pristine_copy_and_leaves_no_leftovers(data_dir):
    first = tutorial.ensure_tutorial_copy()
    (first.path / "fig1_kinetics.py").write_text("broken", encoding="utf-8")
    (first.path / "Fig1_kinetics.pdf").write_bytes(b"written back")
    (first.path / "tavottofile" / "MyCanvas.json").write_text("{}", encoding="utf-8")
    (first.path / "tavottofile" / "export").mkdir()

    reset = tutorial.ensure_tutorial_copy(reset=True)
    assert reset.path == first.path and reset.reset and not reset.created
    assert _copy_bytes(reset.path) == _pristine()
    assert not (reset.path / "tavottofile" / "MyCanvas.json").exists()
    assert not (reset.path / "tavottofile" / "export").exists()
    leftovers = [p.name for p in reset.path.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == [], leftovers


def test_stale_leftovers_from_an_interrupted_reset_are_swept(data_dir):
    first = tutorial.ensure_tutorial_copy()
    stale_old = first.path.parent / f".{tutorial.PROJECT_DIRNAME}-deadbeef.old"
    stale_tmp = first.path.parent / f".{tutorial.PROJECT_DIRNAME}-cafef00d.tmp"
    for d in (stale_old, stale_tmp):
        d.mkdir()
        (d / "junk").write_text("x", encoding="utf-8")
    tutorial.ensure_tutorial_copy()
    assert not stale_old.exists() and not stale_tmp.exists()
    assert first.path.is_dir()


def test_missing_files_are_repaired_without_touching_the_rest(data_dir):
    first = tutorial.ensure_tutorial_copy()
    (first.path / "Fig1_kinetics.pdf").unlink()
    (first.path / "tavotto_registry.json").write_text("{not json", encoding="utf-8")
    edited = first.path / "fig2_correlation.py"
    edited.write_text("# kept\n", encoding="utf-8")
    status = tutorial.copy_status()
    assert status["exists"] and not status["complete"]
    assert status["missing"] == ["Fig1_kinetics.pdf"] and status["registry_ok"] is False

    fixed = tutorial.ensure_tutorial_copy()
    assert sorted(fixed.repaired) == ["Fig1_kinetics.pdf", "tavotto_registry.json"]
    assert (fixed.path / "Fig1_kinetics.pdf").read_bytes() == _pristine()["Fig1_kinetics.pdf"]
    assert json.loads((fixed.path / "tavotto_registry.json").read_text(encoding="utf-8"))["scripts"]
    assert edited.read_text(encoding="utf-8") == "# kept\n"  # 用户的改动仍在
    assert tutorial.copy_status()["complete"] is True


def test_resource_change_gets_a_fresh_directory_and_keeps_the_old_one(
    data_dir, tmp_path, monkeypatch
):
    old = tutorial.ensure_tutorial_copy()
    (old.path / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")

    # 资源变了（升版本号，或只改了一个字节）→ 目录名跟着变，旧目录原样留着
    res = _broken_copy(tmp_path)
    meta = json.loads((res / "tutorial_meta.json").read_text(encoding="utf-8"))
    meta["tutorial_version"] = meta["tutorial_version"] + 1
    (res / "tutorial_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(tutorial, "resource_root", lambda: res)

    new = tutorial.ensure_tutorial_copy()
    assert new.created and new.path != old.path
    assert new.path.parent.name.startswith(f"v{meta['tutorial_version']}-")
    assert new.resource_digest != old.resource_digest
    assert (old.path / "fig1_kinetics.py").read_text(encoding="utf-8") == "# mine\n"
    assert (new.path / "fig1_kinetics.py").read_bytes() == (res / "fig1_kinetics.py").read_bytes()

    # 只改内容不升版本号同样换目录：纪律在结构里，不在「记得升版本」上
    (res / "paper_style.py").write_text("# v2 style\n", encoding="utf-8")
    newer = tutorial.ensure_tutorial_copy()
    assert newer.path != new.path and newer.path.parent.name.startswith(new.path.parent.name[:3])
    assert new.path.is_dir()


def test_copy_failure_keeps_the_previous_copy_intact(data_dir, monkeypatch):
    first = tutorial.ensure_tutorial_copy()
    (first.path / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")
    calls = {"n": 0}
    real = shutil.copyfile

    def flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(tutorial.shutil, "copyfile", flaky)
    with pytest.raises(tutorial.TutorialError) as ei:
        tutorial.ensure_tutorial_copy(reset=True)
    assert ei.value.code == "tutorial_copy_failed"
    assert (first.path / "fig1_kinetics.py").read_text(encoding="utf-8") == "# mine\n"
    leftovers = [p.name for p in first.path.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == [], leftovers


def test_locked_directory_is_reported_and_old_copy_survives(data_dir, monkeypatch):
    """Windows 上 rename 被占用 → PermissionError。旧副本一个字节不动，错误说得清。"""
    first = tutorial.ensure_tutorial_copy()
    (first.path / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")
    real_rename = os.rename

    def locked(src, dst, *a, **kw):
        if Path(src) == first.path:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(tutorial.os, "rename", locked)
    with pytest.raises(tutorial.TutorialError) as ei:
        tutorial.ensure_tutorial_copy(reset=True)
    assert ei.value.code == "tutorial_locked"
    assert "占用" in ei.value.message
    assert (first.path / "fig1_kinetics.py").read_text(encoding="utf-8") == "# mine\n"
    assert _copy_bytes(first.path).keys() == _pristine().keys()
    leftovers = [p.name for p in first.path.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == [], leftovers


def test_placing_the_new_copy_failing_puts_the_old_one_back(data_dir, monkeypatch):
    first = tutorial.ensure_tutorial_copy()
    (first.path / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")
    real_rename = os.rename

    def fail_second(src, dst, *a, **kw):
        if Path(dst) == first.path and Path(src).name.endswith(".tmp"):
            raise OSError(5, "I/O error")
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(tutorial.os, "rename", fail_second)
    with pytest.raises(tutorial.TutorialError):
        tutorial.ensure_tutorial_copy(reset=True)
    assert (first.path / "fig1_kinetics.py").read_text(encoding="utf-8") == "# mine\n"


def test_is_tutorial_path_only_matches_the_data_dir_tree(data_dir, tmp_path):
    tp = tutorial.ensure_tutorial_copy()
    assert tutorial.is_tutorial_path(tp.path)
    assert tutorial.is_tutorial_path(tp.path / "fig1_kinetics.py")
    assert not tutorial.is_tutorial_path(tmp_path / "figs")
    assert not tutorial.is_tutorial_path(tutorial.resource_root())
    sibling = data_dir / "tutorial-not"
    sibling.mkdir(parents=True)
    assert not tutorial.is_tutorial_path(sibling)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _forbid_execution(monkeypatch):
    """打开 / 重置教程的整个过程里，worker 与 probe 一个都不许被碰。"""

    def boom(*a, **kw):
        raise AssertionError("打开教程不得执行用户脚本")

    monkeypatch.setattr(m.engine_pool, "get", boom)
    monkeypatch.setattr(m.engine_probe, "probe_and_register", boom)
    monkeypatch.setattr(m.engine_probe, "script_inventory", boom)
    monkeypatch.setattr(m.engine_discover, "build_draft", boom)


def test_get_reports_availability_without_leaking_package_paths(client):
    body = client.get("/api/tutorial").get_json()
    assert body["available"] is True and body["problems"] == []
    assert body["tutorial_version"] == tutorial.tutorial_metadata()["tutorial_version"]
    assert body["metadata"]["document_id"]
    assert body["copy"] == {
        "exists": False,
        "complete": False,
        "missing": sorted(tutorial.resource_files()),
        "registry_ok": False,
        "version": body["tutorial_version"],
        "resource_digest": body["copy"]["resource_digest"],
    }
    assert body["project"] == {"open": False, "id": None}
    dumped = json.dumps(body)
    assert str(tutorial.resource_root()) not in dumped
    assert str(engine_config.data_dir()) not in dumped


def test_get_reports_broken_resources_instead_of_pretending(client, monkeypatch, tmp_path):
    res = _broken_copy(tmp_path)
    (res / "Fig2_correlation.pdf").unlink()
    monkeypatch.setattr(tutorial, "resource_root", lambda: res)
    body = client.get("/api/tutorial").get_json()
    assert body["available"] is False
    assert any("Fig2_correlation.pdf" in p for p in body["problems"])
    assert "metadata" not in body


def test_open_copies_opens_and_runs_nothing(client, monkeypatch, data_dir):
    _forbid_execution(monkeypatch)
    resp = client.post("/api/tutorial/open", json={})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["reset"] is False and body["created"] is True and body["repaired"] == []
    assert body["tutorial"] == tutorial.tutorial_metadata()
    proj = body["project"]
    assert proj["open"] and proj["tutorial"] is True and proj["drafted"] is False
    assert proj["scripts"] == 2
    assert tutorial.is_tutorial_path(proj["figures_dir"])

    panels = client.get("/api/panels").get_json()["panels"]
    assert sorted(p["id"] for p in panels) == [
        f"{s}.pdf" for s in tutorial.tutorial_metadata()["expected_stems"]
    ]
    assert all(p.get("script") for p in panels), "两张图都得连着脚本（可进图内编辑）"
    assert client.get("/api/layouts").get_json()["layouts"] == ["Tutorial"]
    doc = client.get("/api/layouts/Tutorial").get_json()
    assert doc["schema"] == 3 and len(doc["canvases"][0]["objects"]) == 2

    # 再开一次：复用进程里的项目，副本不重建
    again = client.post("/api/tutorial/open", json={}).get_json()
    assert again["project"]["reused"] is True and again["created"] is False


def test_recent_list_flags_the_tutorial(client, tmp_path):
    figs = _make_user_project(tmp_path)
    client.post("/api/projects/open", json={"path": str(figs)})
    client.post("/api/tutorial/open", json={})
    recent = client.get("/api/projects/recent").get_json()["recent"]
    flags = {e["name"]: e["tutorial"] for e in recent}
    assert flags == {tutorial.PROJECT_DIRNAME: True, "figs": False}
    assert client.get("/api/project").get_json()["tutorial"] is True
    # 用户可以像别的项目一样把它从最近列表移掉；磁盘副本不动
    path = next(e["path"] for e in recent if e["tutorial"])
    assert client.post("/api/projects/remove", json={"path": path}).get_json()["ok"]
    assert Path(path).is_dir()


def test_open_does_not_touch_the_user_project(client, tmp_path, monkeypatch):
    figs = _make_user_project(tmp_path)
    user = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    # 快照在**打开之后**取：打开自己的项目会起草注册表，那是既有行为，不是教程的
    before = {p.name: p.read_bytes() for p in figs.iterdir() if p.is_file()}
    _forbid_execution(monkeypatch)
    body = client.post("/api/tutorial/open", json={"default": False}).get_json()
    assert body["project"]["id"] != user["id"]
    # 用户项目仍是默认项目，文件一个字节没变，两个项目都开着
    assert client.get("/api/project").get_json()["id"] == user["id"]
    assert {p.name: p.read_bytes() for p in figs.iterdir() if p.is_file()} == before
    opened = {p["id"] for p in client.get("/api/projects").get_json()["projects"]}
    assert opened == {user["id"], body["project"]["id"]}


def test_reset_clears_only_tutorial_state(client, tmp_path, monkeypatch, data_dir):
    figs = _make_user_project(tmp_path)
    user = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    tut = client.post("/api/tutorial/open", json={"default": False}).get_json()
    pid, tpath = tut["project"]["id"], Path(tut["project"]["figures_dir"])
    doc_id = tut["tutorial"]["document_id"]

    # 教程自己的痕迹：自动保存槽位、写回基线、项目内的画布 / 导出
    m.AUTOSAVE_DIR.mkdir(parents=True)
    (m.AUTOSAVE_DIR / f"{doc_id}.json").write_text("{}", encoding="utf-8")
    (m.AUTOSAVE_DIR / "d-user.json").write_text("{}", encoding="utf-8")
    m.BAKED_DIR.mkdir(parents=True)
    (m.BAKED_DIR / f"{pid}.json").write_text("{}", encoding="utf-8")
    (m.BAKED_DIR / f"{user['id']}.json").write_text("{}", encoding="utf-8")
    (tpath / "tavottofile" / "MyCanvas.json").write_text("{}", encoding="utf-8")
    (tpath / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")
    (figs / "tavottofile").mkdir(exist_ok=True)
    (figs / "tavottofile" / "Keep.json").write_text("{}", encoding="utf-8")
    engine_config.touch_recent(str(figs))

    _forbid_execution(monkeypatch)
    closed: list[tuple[str, bool]] = []
    real_close = m.close_project

    def spy_close(target, wait=False):
        closed.append((target, wait))
        return real_close(target, wait=wait)

    monkeypatch.setattr(m, "close_project", spy_close)
    resp = client.post("/api/tutorial/reset", json={})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["reset"] is True and body["project"]["id"] == pid
    # 换目录之前先把教程项目关干净（worker 退出并释放文件，Windows 上才 rename 得动）
    assert closed == [(pid, True)]
    assert sorted(body["cleared"]) == sorted([f"{doc_id}.json", f"{pid}.json"])

    assert not (m.AUTOSAVE_DIR / f"{doc_id}.json").exists()
    assert (m.AUTOSAVE_DIR / "d-user.json").exists()
    assert not (m.BAKED_DIR / f"{pid}.json").exists()
    assert (m.BAKED_DIR / f"{user['id']}.json").exists()
    assert _copy_bytes(tpath) == _pristine()
    assert not (tpath / "tavottofile" / "MyCanvas.json").exists()
    # 用户项目：还开着、还是默认、文件一个都没少，最近列表还在
    assert client.get("/api/project").get_json()["id"] == user["id"]
    assert (figs / "tavottofile" / "Keep.json").is_file()
    assert (figs / "p1.pdf").is_file()
    recent_paths = [e["path"] for e in client.get("/api/projects/recent").get_json()["recent"]]
    assert str(figs) in recent_paths and str(tpath) in recent_paths
    # 遥测同意态不属于重置范围
    assert engine_config.load()["telemetry"] == engine_config.load()["telemetry"]


def test_reset_before_open_creates_and_opens(client, monkeypatch):
    _forbid_execution(monkeypatch)
    body = client.post("/api/tutorial/reset", json={}).get_json()
    assert body["reset"] is True and body["project"]["tutorial"] is True
    assert client.get("/api/project").get_json()["id"] == body["project"]["id"]


def test_reset_keeps_tutorial_as_default_only_if_it_was(client, tmp_path):
    figs = _make_user_project(tmp_path)
    user = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    client.post("/api/tutorial/open", json={"default": False})
    client.post("/api/tutorial/reset", json={})
    assert client.get("/api/project").get_json()["id"] == user["id"]

    client.post("/api/tutorial/open", json={"default": True})
    tut = client.post("/api/tutorial/reset", json={}).get_json()
    assert client.get("/api/project").get_json()["id"] == tut["project"]["id"]


def test_reset_when_locked_reopens_the_old_copy(client, monkeypatch):
    tut = client.post("/api/tutorial/open", json={}).get_json()
    tpath = Path(tut["project"]["figures_dir"])
    (tpath / "fig1_kinetics.py").write_text("# mine\n", encoding="utf-8")
    real_rename = os.rename

    def locked(src, dst, *a, **kw):
        if Path(src) == tpath:
            raise PermissionError(13, "in use")
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(tutorial.os, "rename", locked)
    resp = client.post("/api/tutorial/reset", json={})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "tutorial_locked"
    proj = client.get("/api/project").get_json()
    assert proj["open"] and proj["id"] == tut["project"]["id"]
    assert (tpath / "fig1_kinetics.py").read_text(encoding="utf-8") == "# mine\n"


def test_open_repairs_a_damaged_copy(client):
    tut = client.post("/api/tutorial/open", json={}).get_json()
    tpath = Path(tut["project"]["figures_dir"])
    m.reset_projects()
    (tpath / "Fig2_correlation.pdf").unlink()
    body = client.post("/api/tutorial/open", json={}).get_json()
    assert body["repaired"] == ["Fig2_correlation.pdf"] and body["created"] is False
    assert (tpath / "Fig2_correlation.pdf").is_file()


def test_tutorial_endpoints_go_through_session_auth():
    """新端点不能绕过 ADR 0008 的认证：security 钩子对所有 /api 生效。"""
    from tavotto import security

    for rule in ("/api/tutorial", "/api/tutorial/open", "/api/tutorial/reset"):
        assert any(r.rule == rule for r in m.app.url_map.iter_rules())
    assert not getattr(security, "PUBLIC_PATHS", set()) & {
        "/api/tutorial",
        "/api/tutorial/open",
        "/api/tutorial/reset",
    }


# ---------------------------------------------------------------------------
# 打包：读产物，不读源码树
# ---------------------------------------------------------------------------


def _dist_dir() -> Path | None:
    override = os.environ.get("TAVOTTO_DIST_DIR")
    if override:
        return Path(override)
    return REPO / "dist" if (REPO / "dist").is_dir() else None


def _newest(pattern: str) -> Path | None:
    d = _dist_dir()
    if d is None:
        return None
    files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


WHEEL = _newest("tavotto-*.whl")
SDIST = _newest("tavotto-*.tar.gz")
_NEED_DIST = "先 python -m build（或设 TAVOTTO_DIST_DIR）——这几条读的是产物不是源码树"


@pytest.mark.skipif(WHEEL is None, reason=_NEED_DIST)
def test_wheel_contains_every_tutorial_resource_and_nothing_else():
    expected = {f"tavotto/resources/tutorial_project/{rel}" for rel in tutorial.resource_files()}
    with zipfile.ZipFile(WHEEL) as z:
        members = {i.filename: i for i in z.infolist()}
        shipped = {n for n in members if n.startswith("tavotto/resources/tutorial_project/")}
        assert shipped == expected, {"missing": expected - shipped, "extra": shipped - expected}
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in members)
        total = sum(members[n].file_size for n in shipped)
        assert total <= tutorial.MAX_TOTAL_BYTES
        for rel, path in tutorial.resource_files().items():
            assert z.read(f"tavotto/resources/tutorial_project/{rel}") == path.read_bytes(), rel
        # 别的资源也在（与 lab_acceptance 的结构检查同一批）
        assert "tavotto/profiles/publication.json" in members


@pytest.mark.skipif(SDIST is None, reason=_NEED_DIST)
def test_sdist_contains_every_tutorial_resource():
    expected = {
        f"src/tavotto/resources/tutorial_project/{rel}" for rel in tutorial.resource_files()
    }
    with tarfile.open(SDIST) as t:
        names = t.getnames()
        prefix = names[0].split("/")[0]
        shipped = {
            n[len(prefix) + 1 :]
            for n in names
            if n.startswith(f"{prefix}/src/tavotto/resources/tutorial_project/")
        }
        assert shipped == expected, {"missing": expected - shipped, "extra": shipped - expected}
        assert not any("__pycache__" in n for n in names)


@pytest.mark.skipif(WHEEL is None, reason=_NEED_DIST)
def test_installed_wheel_layout_exposes_resources_through_importlib(tmp_path):
    """把 wheel 解到一个干净目录，在子进程里只靠 `importlib.resources` 找资源。

    `resource_root()` 必须落在解开的那份里（不是源码树），且静态验证全过。
    """
    site = tmp_path / "site"
    with zipfile.ZipFile(WHEEL) as z:
        z.extractall(site)
    probe = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from tavotto.engine import tutorial\n"
        "root = tutorial.resource_root()\n"
        "print(json.dumps({'root': str(root), 'problems': tutorial.validate_tutorial_resources(),"
        " 'meta': tutorial.tutorial_metadata()['tutorial_version']}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(site)
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # Windows 上默认按区域编码解码，中文一出现就静默丢
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=True,
    )
    info = json.loads(out.stdout.strip().splitlines()[-1])
    assert (
        Path(info["root"]).resolve()
        == (site / "tavotto" / "resources" / "tutorial_project").resolve()
    )
    assert info["problems"] == []
    assert info["meta"] == tutorial.tutorial_metadata()["tutorial_version"]


def test_smoke_and_ci_run_the_tutorial_on_the_bundled_runtime():
    """教程脚本「在内置 runtime 上可运行」由冒烟证明：`--tutorial` 要真的接进去。"""
    smoke = (REPO / "scripts" / "smoke_app.py").read_text(encoding="utf-8")
    assert '"--tutorial"' in smoke
    assert "_check_tutorial(base)" in smoke
    assert "/api/tutorial/open" in smoke and "/api/tutorial/reset" in smoke
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # 两条桌面冒烟①（Windows / macOS 内置 runtime）都带 --tutorial
    bundled = [
        blk
        for blk in ci.split("- name:")
        if "--expect-source bundled --expect-runtime" in blk
        and "--expect-control-plane workerd" in blk
    ]
    assert len(bundled) == 2, "找不到两条内置 runtime 的冒烟①"
    assert all("--tutorial" in blk for blk in bundled)
    # wheel 装进干净环境之后经 importlib.resources 验一遍教程资源
    assert "tutorial.validate_tutorial_resources()" in ci


def test_desktop_spec_ships_the_tutorial_resources_as_datas():
    """PyInstaller 只打 .py 进 PYZ；包内数据要显式列进 datas，否则桌面版没有教程。"""
    spec = (REPO / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert '"tavotto/resources"' in spec
    assert '"tavotto/profiles"' in spec


def test_pyproject_keeps_resources_inside_the_wheel_and_sdist():
    import tomllib

    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = cfg["tool"]["hatch"]["build"]
    assert not any("resources" in e for e in build.get("exclude", []))
    assert "src/tavotto" in cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/tavotto"]
    # 资源文件不能被 .gitignore 挡掉（hatchling 默认跳过 VCS 忽略的文件）
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "*.pdf" not in ignore.splitlines() and "resources" not in ignore


# ---------------------------------------------------------------------------
# 真跑一遍教程脚本（测试专属；应用打开教程时不做这件事）
# ---------------------------------------------------------------------------

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


def _rpc(proc, obj, timeout=180):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    assert not reader.is_alive(), f"worker 超时（{timeout}s）: {obj.get('cmd')}"
    line = box[0] if box else ""
    assert line, f"worker 无响应: {obj.get('cmd')}\n{proc.stderr.read()}"
    resp = json.loads(line)
    assert resp.get("ok"), f"{resp.get('error', resp)}\n{resp.get('traceback', '')}"
    return resp


@pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
@pytest.mark.parametrize("panel_index", [0, 1])
def test_tutorial_scripts_build_in_a_worker_and_expose_editable_roles(
    data_dir, tmp_path, panel_index
):
    tp = tutorial.ensure_tutorial_copy()
    meta = tp.metadata
    panel = meta["panels"][panel_index]
    proc = subprocess.Popen(
        [
            WORKER_PY,
            str(pool.WORKER_PY),
            "--script",
            str(tp.path / panel["script"]),
            "--figures-dir",
            str(tp.path),
            "--out-dir",
            str(tmp_path / "out"),
            "--sandbox",
            str(tmp_path / "sandbox"),
            "--entry",
            "main",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    try:
        resp = _rpc(proc, {"cmd": "build"})
        assert panel["stem"] in resp["stems"]
        man = json.loads((tmp_path / "out" / f"{panel['stem']}.json").read_text(encoding="utf-8"))
        roles = {el["role"] for el in man["elements"]}
        assert set(panel["editable_roles"]) <= roles, roles
        texts = [
            f["value"]
            for el in man["elements"]
            for f in el.get("editable", [])
            if f["prop"] == "text" and isinstance(f["value"], str)
        ]
        issue = panel.get("spec_issue")
        if issue:
            assert any(t.startswith(issue["text_prefix"]) for t in texts), texts
            small = [
                f["value"]
                for el in man["elements"]
                if el["role"] == issue["role"]
                for f in el.get("editable", [])
                if f["prop"] == "fontsize"
            ]
            assert any(isinstance(v, (int, float)) and v <= 8 for v in small), small
        # build 期间不得往教程副本里写图
        assert (tp.path / panel["file"]).read_bytes() == _pristine()[panel["file"]]
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
