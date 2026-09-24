"""`config.json` 的读-改-写只有一个入口：`config.transaction()`（Codex #550）。

以前 updater / telemetry 各持各的模块锁再 `load()` → `save()`，与 config 模块里的
收藏、最近列表写入互不排斥。交错时后写的一方用自己读到的旧快照整份覆盖，另一方的
改动丢了、却已经对调用方报了成功；两边还共用同一个 `config.json.tmp`，会互相删掉。

判据的主语：**另一个模块的写入方**（updater，跑在另一个线程）在「config 事务进行中」
这一刻做写入——它必须等事务结束，最后两边的改动都在盘上。
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from tavotto.engine import config as engine_config, updater

SRC = Path(__file__).resolve().parent.parent / "src" / "tavotto"


def test_other_module_writer_waits_for_the_transaction(tmp_path):
    keep = str(tmp_path / "keep")
    t = None
    with engine_config.transaction() as cfg:
        # 事务里读到的是旧快照；此时另一个模块在另一个线程里写配置
        t = threading.Thread(target=lambda: updater.set_settings({"auto_check": False}))
        t.start()
        # 共用一把锁时它在这里等（join 超时返回、线程仍活着）；各持各的锁时它已经写完了，
        # 而下面这次 save 会用旧快照把它盖掉——那正是要抓的丢更新
        t.join(0.5)
        cfg["pinned_projects"] = [{"path": keep, "name": "keep"}]
    t.join(10)
    assert not t.is_alive()
    on_disk = json.loads(engine_config.config_path().read_text(encoding="utf-8"))
    assert on_disk["pinned_projects"] == [{"path": keep, "name": "keep"}]
    assert on_disk["updates"]["auto_check"] is False


def test_transaction_writes_nothing_when_the_block_raises(tmp_path):
    engine_config.edit_pinned("add", str(tmp_path / "a"))
    with pytest.raises(RuntimeError):
        with engine_config.transaction() as cfg:
            cfg["pinned_projects"] = []
            raise RuntimeError("boom")
    assert [e["path"] for e in engine_config.pinned_projects()] == [str(tmp_path / "a")]


def test_save_leaves_no_temp_files(monkeypatch):
    d = engine_config.config_dir()
    # 这条用例会写盘：先确认写的是 conftest 隔离出来的临时目录，不是真实用户配置
    assert "tavotto-config" in str(d), d
    engine_config.save({"recent_projects": []})
    assert not list(d.glob("*.tmp"))

    # 写到一半失败：临时文件也收拾掉，原文件不动
    before = engine_config.config_path().read_text(encoding="utf-8")

    def boom(self, target):
        raise OSError("disk full")

    # 只在这个块里换掉 Path.replace。**不要** `monkeypatch.undo()`：它会连 conftest
    # 自动生效的配置目录隔离一起撤掉，之后的 config_path() 就指向开发者真实的配置
    # （2026-09-25 实测踩到：读到了本机 ~/Library/Application Support/Tavotto/config.json）
    with monkeypatch.context() as m:
        m.setattr(Path, "replace", boom)
        with pytest.raises(OSError):
            engine_config.save({"recent_projects": [{"path": "/x"}]})
    assert engine_config.config_dir() == d  # 隔离还在
    assert not list(d.glob("*.tmp"))
    assert engine_config.config_path().read_text(encoding="utf-8") == before


def _direct_save_calls(tree: ast.AST) -> list[int]:
    """`<config 模块>.save(...)` 的调用行号：`config.save` / `engine_config.save` /
    `from .config import save` 之后的裸 `save(...)`。"""
    aliases = {"config", "engine_config"}
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("config"):
            for a in node.names:
                if a.name == "save":
                    bare.add(a.asname or a.name)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith(".config") or a.name == "config":
                    aliases.add(a.asname or a.name.rsplit(".", 1)[-1])
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "save"
            and isinstance(f.value, ast.Name)
            and f.value.id in aliases
        ) or (isinstance(f, ast.Name) and f.id in bare):
            lines.append(node.lineno)
    return lines


def test_no_module_outside_config_calls_save_directly():
    """结构门禁：config.py 之外不许 `config.save(...)`——写配置一律 `with config.transaction()`。"""
    offenders = []
    scanned = 0
    for path in SRC.rglob("*.py"):
        if path.name == "config.py" and path.parent.name == "engine":
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(SRC)}:{n}" for n in _direct_save_calls(tree)]
    assert scanned > 50, "扫到的文件太少，判据多半量在空集合上"
    assert offenders == [], f"这些地方绕过了 config.transaction()：{offenders}"


def test_the_gate_sees_a_direct_save():
    """反证判据自己：三种写法都认得出来（判据不会因为换个 import 方式就恒绿）。"""
    for src in (
        "from tavotto.engine import config\nconfig.save({})",
        "from . import config as engine_config\nengine_config.save({})",
        "from .config import save\nsave({})",
    ):
        assert _direct_save_calls(ast.parse(src)) == [2], src
