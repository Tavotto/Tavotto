"""技能接入（`engine/hostskills.py`）与 MCP 工具 `tavotto_guide`（2026-11-06）。

主语：用户机器上各宿主的技能目录（这里全在临时 HOME 里造）——写得完整、重跑幂等、用户改过的不覆盖、断开只删自己写的、
不是 Tavotto 的同名目录不碰；以及 guide 只读白名单内的说明、路径穿越被拒、冻结布局也读得到。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from tavotto.engine import hostskills as K

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "codex-plugin" / "skills" / "tavotto-figure"
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))
from tavotto_mcp import guide  # noqa: E402

FILE_HOSTS = ("claude-code", "cursor", "vscode", "zcode", "antigravity", "workbuddy")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "家 目录"  # 中文 + 空格
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def _dest(home, host="cursor"):
    return K._skill_dirs(host)[0] / K.SKILL_NAME


def _all_files(root: Path):
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


# ------------------------------------------------------------------ 来源与元数据
def test_skill_name_field_equals_folder_name_and_description_fits_zcode():
    text = (SRC / "SKILL.md").read_text(encoding="utf-8")
    fm = text.split("---", 2)[1]
    assert re.search(r"^name:\s*(\S+)\s*$", fm, re.M).group(1) == SRC.name == K.SKILL_NAME
    desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip()
    assert len(desc) <= 1024, len(desc)  # ZCode 的上限


def test_source_resolves_in_the_source_tree_and_frozen_layout(tmp_path, monkeypatch):
    assert K.skill_source() == SRC.resolve() or K.skill_source() == SRC
    fake = tmp_path / "meipass"
    shutil.copytree(SRC, fake / "skills" / "tavotto-figure")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake), raising=False)
    assert K.skill_source() == fake / "skills" / "tavotto-figure"
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)
    assert K.skill_source() is None  # pip 安装 / 漏收：如实 None


# ------------------------------------------------------------------ 写入
@pytest.mark.parametrize("host", FILE_HOSTS)
def test_install_copies_the_whole_skill_with_a_record(home, host):
    res = K.install_skill(host)
    assert res["ok"] and res["state"] == "written", res
    dest = _dest(home, host)
    assert _all_files(dest) - {K.RECORD} == _all_files(SRC)
    rec = json.loads((dest / K.RECORD).read_text(encoding="utf-8"))
    assert rec["schema"] == 1 and rec["written_by"] == K.WRITTEN_BY
    assert rec["sha256"] == K.tree_sha256(SRC) == K.tree_sha256(dest)
    assert K.skill_state(host) == "current"
    assert not [p for p in dest.parent.iterdir() if p.name != K.SKILL_NAME]  # 没留临时目录


def test_second_install_is_a_noop_and_does_not_touch_files(home):
    K.install_skill("cursor")
    f = _dest(home) / "SKILL.md"
    before = f.stat().st_mtime_ns
    res = K.install_skill("cursor")
    assert res["skipped"] and res["state"] == "skipped"
    assert f.stat().st_mtime_ns == before


def test_checksum_is_stable_and_ignores_pycache_and_the_record(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(SRC, a)
    shutil.copytree(SRC, b)
    (b / "scripts" / "__pycache__").mkdir(exist_ok=True)
    (b / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (b / K.RECORD).write_text("{}", encoding="utf-8")
    assert K.tree_sha256(a) == K.tree_sha256(b) == K.tree_sha256(a)
    (b / "SKILL.md").write_bytes((b / "SKILL.md").read_bytes() + b" ")
    assert K.tree_sha256(a) != K.tree_sha256(b)


def test_a_stale_untouched_copy_is_replaced(home, monkeypatch, tmp_path):
    older = tmp_path / "older"
    shutil.copytree(SRC, older)
    (older / "SKILL.md").write_text("旧版\n", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(K, "skill_source", lambda: older)
        K.install_skill("cursor")
    assert K.skill_state("cursor") == "stale"
    res = K.install_skill("cursor")
    assert res["state"] == "updated"
    assert K.skill_state("cursor") == "current"
    assert "旧版" not in (_dest(home) / "SKILL.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 不覆盖
def test_a_user_edited_copy_is_never_overwritten_or_removed(home):
    K.install_skill("cursor")
    f = _dest(home) / "SKILL.md"
    f.write_text(f.read_text(encoding="utf-8") + "\n我的笔记\n", encoding="utf-8")
    assert K.skill_state("cursor") == "modified"
    res = K.install_skill("cursor")
    assert res["ok"] and res["state"] == "modified"
    assert "我的笔记" in f.read_text(encoding="utf-8")
    rm = K.remove_skill("cursor")
    assert rm["ok"] and rm["state"] == "modified" and f.is_file()


def test_a_user_added_file_also_counts_as_modified(home):
    K.install_skill("cursor")
    (_dest(home) / "my.txt").write_text("x", encoding="utf-8")
    assert K.skill_state("cursor") == "modified"
    assert K.install_skill("cursor")["state"] == "modified"


def test_a_same_named_dir_that_is_not_ours_is_left_alone(home):
    other = _dest(home)
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("别人的技能", encoding="utf-8")
    assert K.skill_state("cursor") == "modified"
    assert K.install_skill("cursor")["state"] == "modified"
    assert K.remove_skill("cursor")["state"] == "modified"
    assert (other / "SKILL.md").read_text(encoding="utf-8") == "别人的技能"


def test_a_symlinked_skill_dir_is_not_touched(home, tmp_path):
    real = tmp_path / "dev-checkout"
    real.mkdir()
    (real / "SKILL.md").write_text("dev", encoding="utf-8")
    dest = _dest(home)
    dest.parent.mkdir(parents=True)
    try:
        dest.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("不能建符号链接")
    assert K.install_skill("cursor")["state"] == "modified"
    assert K.remove_skill("cursor")["state"] == "modified"
    assert (real / "SKILL.md").is_file()


# ------------------------------------------------------------------ 断开
def test_remove_deletes_only_our_directory_and_keeps_siblings(home):
    K.install_skill("cursor")
    sibling = _dest(home).parent / "别的技能"
    sibling.mkdir()
    (sibling / "SKILL.md").write_text("keep", encoding="utf-8")
    res = K.remove_skill("cursor")
    assert res["ok"] and res["state"] == "removed"
    assert not _dest(home).exists() and sibling.is_dir()
    assert K.remove_skill("cursor")["state"] == "none"  # 重复断开


def test_claude_desktop_is_builtin_and_unknown_hosts_do_nothing(home):
    assert K.skill_state("claude-desktop") == "builtin"
    assert K.install_skill("claude-desktop")["state"] == "builtin"
    assert K.install_skill("codex")["state"] == "none"
    assert K.remove_skill("claude-desktop")["state"] == "none"


def test_trae_only_writes_into_editor_dirs_that_exist(home):
    assert K.install_skill("trae")["state"] == "none"
    assert K.skill_state("trae") == "missing"
    (home / ".trae-cn").mkdir()
    res = K.install_skill("trae")
    assert res["state"] == "written"
    assert (home / ".trae-cn" / "skills" / "tavotto-figure" / "SKILL.md").is_file()
    assert not (home / ".trae").exists()


def test_host_directories_match_the_verified_table(home):
    got = {h: [str(p.relative_to(home).as_posix()) for p in K._skill_dirs(h)] for h in FILE_HOSTS}
    assert got == {
        "claude-code": [".claude/skills"],
        "cursor": [".cursor/skills"],
        "vscode": [".copilot/skills"],
        "zcode": [".zcode/skills"],
        "antigravity": [".gemini/config/skills"],
        "workbuddy": [".workbuddy/skills"],
    }


# ------------------------------------------------------------------ 失败如实报
def test_missing_source_is_reported(home, monkeypatch):
    monkeypatch.setattr(K, "skill_source", lambda: None)
    res = K.install_skill("cursor")
    assert not res["ok"] and res["error_code"] == K.ERR_SOURCE
    assert not _dest(home).exists()


def test_write_failure_is_reported_and_leaves_no_half_written_dir(home, monkeypatch):
    def boom(*a, **k):
        raise OSError("磁盘满了")

    monkeypatch.setattr(K.shutil, "copytree", boom)
    res = K.install_skill("cursor")
    assert not res["ok"] and res["error_code"] == K.ERR_WRITE and "磁盘满了" in res["detail"]
    assert not _dest(home).exists()
    assert not [p for p in K._skill_dirs("cursor")[0].iterdir()]


def test_failed_swap_keeps_the_previous_copy(home, monkeypatch, tmp_path):
    older = tmp_path / "older"
    shutil.copytree(SRC, older)
    (older / "SKILL.md").write_text("旧版\n", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(K, "skill_source", lambda: older)
        K.install_skill("cursor")
    real = K.os.replace
    calls = []

    def flaky(a, b):
        calls.append(a)
        if str(b).endswith("tavotto-figure") and ".tmp-" in str(a):
            raise OSError("占用")
        return real(a, b)

    monkeypatch.setattr(K.os, "replace", flaky)
    res = K.install_skill("cursor")
    assert not res["ok"]
    assert (_dest(home) / "SKILL.md").read_text(encoding="utf-8") == "旧版\n"


# ------------------------------------------------------------------ tavotto_guide
def test_guide_returns_the_skill_and_lists_sections():
    out = guide.read_guide()
    assert out["ok"] and out["text"] == (SRC / "SKILL.md").read_text(encoding="utf-8")
    assert "other-hosts" in out["sections"]
    got = guide.read_guide("other-hosts")
    assert got["text"] == (SRC / "references" / "other-hosts.md").read_text(encoding="utf-8")
    assert guide.read_guide("other-hosts.md")["ok"]


@pytest.mark.parametrize(
    "bad",
    [
        "../SKILL",
        "..",
        "../../AGENTS",
        "references/other-hosts",
        "/etc/passwd",
        "a\0b",
        "C:\\x",
        5,
        ["x"],
    ],
)
def test_guide_rejects_anything_outside_the_whitelist(bad):
    out = guide.read_guide(bad)
    assert not out["ok"] and out["code"] == guide.ERR_SECTION


def test_guide_works_from_a_frozen_layout_and_fails_stably_without_it(tmp_path, monkeypatch):
    fake = tmp_path / "mei"
    shutil.copytree(SRC, fake / "skills" / "tavotto-figure")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake), raising=False)
    assert guide.read_guide("other-hosts")["ok"]
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "none"), raising=False)
    out = guide.read_guide()
    assert not out["ok"] and out["code"] == guide.ERR_UNAVAILABLE


def test_guide_tool_is_wired_through_the_server():
    from tavotto_mcp import server

    res = server.call_tool("tavotto_guide", {"section": "other-hosts"})
    assert "isError" not in res or not res["isError"]
    assert res["structuredContent"]["section"] == "other-hosts"
    bad = server.call_tool("tavotto_guide", {"section": "../x"})
    assert bad["isError"] and bad["structuredContent"]["code"] == guide.ERR_SECTION


def test_initialize_instructions_point_to_the_guide():
    import io

    from tavotto_mcp import rpc, server

    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    init = s.dispatch("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert "tavotto_guide" in init["instructions"]
