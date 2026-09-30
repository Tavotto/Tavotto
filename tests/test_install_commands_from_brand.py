"""文档 / 技能里出现的 Claude Code 插件与 DSH bundle 安装命令，全部由品牌常量拼出（ADR 0103 / 0104）。

同一条命令会被抄进 README、技能的 other-hosts 参考、实现文档、发行说明……只看 README 的话，
`brand.CLAUDE_*` / `brand.DSH_*` 一改，别处的旧命令照样绿着发出去，照着技能装的 agent 拿到的
是过期的规格。所以这里不按文件点名，而是扫**所有**进版本库的 Markdown（ADR 与已发行的发行说明
是历史记录，不跟着改），凡是长得像这几条命令的地方都要与常量整段相等。

另有非空约束：命令此刻对外登的地方（矩阵 beta 时是 README，此前是待发说明，2026-09-30 用户决定）
与技能参考里必须真的扫到这几条，免得命令换了写法、正则一条都抓不到时本文件恒绿。
"""

import json
import re
import subprocess
from pathlib import Path

from tavotto.engine import brand

ROOT = Path(__file__).resolve().parent.parent
README = "README.md"
SKILL_REFERENCE = f"{brand.CODEX_PLUGIN_SUBDIR}/skills/tavotto-figure/references/other-hosts.md"
PENDING = "docs/release-notes/UNRELEASED.md"

#: 命令参数里可能紧跟的 Markdown / 引号：不算参数的一部分
_ARG = r"[^\s`\"']+"
PATTERNS = {
    "claude_marketplace": re.compile(
        rf"claude plugin marketplace add ({_ARG})((?: --sparse {_ARG})*)"
    ),
    "claude_ref": re.compile(rf"claude plugin (?:install|update|uninstall) ({_ARG})"),
    "dsh_spec": re.compile(rf"(git\+{_ARG}&path:/{_ARG})"),
    "dsh_name": re.compile(rf"dsh plugin --profile {_ARG} (?:update|remove) ({_ARG})"),
}


def _is_current_doc(rel: str) -> bool:
    if rel.startswith("docs/adr/"):
        return False
    if rel.startswith("docs/release-notes/"):
        return rel == "docs/release-notes/UNRELEASED.md"
    return True


def _docs() -> dict[str, str]:
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "*.md"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    # 换行 + 缩进折成一个空格：Markdown 折行会把一条命令断在两行
    return {
        rel: re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8"))
        for rel in tracked
        if _is_current_doc(rel)
    }


def _found() -> dict[str, dict[str, list[tuple[str, ...]]]]:
    return {
        rel: {kind: [m.groups() for m in pat.finditer(text)] for kind, pat in PATTERNS.items()}
        for rel, text in _docs().items()
    }


def test_every_install_command_in_the_docs_comes_from_brand():
    sparse = "".join(f" --sparse {p}" for p in brand.CLAUDE_SPARSE_PATHS)
    expected = {
        "claude_marketplace": (brand.CLAUDE_MARKETPLACE, sparse),
        "claude_ref": (brand.CLAUDE_PLUGIN_REF,),
        "dsh_spec": (brand.DSH_PLUGIN_SPEC,),
        "dsh_name": (brand.DSH_BUNDLE_NAME,),
    }
    stale = [
        f"{rel}: {kind} = {groups}"
        for rel, kinds in _found().items()
        for kind, hits in kinds.items()
        for groups in hits
        if groups != expected[kind]
    ]
    assert not stale, "这些安装命令与品牌常量对不上：\n" + "\n".join(stale)


def _published_home(host_id: str) -> str:
    """面向用户的安装命令此刻登在哪：矩阵 beta 时是 README，此前在待发说明里等发版（2026-09-30）。"""
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    (host,) = [h for h in matrix["mcp_hosts"]["hosts"] if h["id"] == host_id]
    return README if host["status"] == "beta" else PENDING


def test_the_scan_really_sees_the_user_facing_commands():
    found = _found()
    wanted = {
        "claude-code": ("claude_marketplace", "claude_ref"),
        "dsh": ("dsh_spec",),
    }
    for host_id, kinds in wanted.items():
        for rel in (_published_home(host_id), SKILL_REFERENCE):
            for kind in kinds:
                assert found[rel][kind], f"{rel} 里没扫到 {kind}：命令换了写法，正则要跟着改"
    if _published_home("dsh") == README:
        assert found[README]["dsh_name"], "README 里没扫到 dsh 的 update 命令"
