"""代码、插件与界面里指向 README 某一节的引用，那一节必须真的在。

写法有两种：提示文字里的 `README「<标题>」`（标题可以只写前段，章节名常带 `(Beta)` 之类的尾巴），
以及链接里的 GitHub 锚点 `#using-tavotto-…`。两份 README（英文 / 中文）的标题都算。

为什么值得有：2026-09-30 用户决定 Claude Code / DSH 的安装方式在 plugin-stable promote 之前不上
README（ADR 0103 / 0104），章节撤了，配置生成器的提示却还叫用户去 README 找它——Codex 在 #694
评审里抓到的。章节会随发版加回、也会再改名，靠「记得回来改」维持不住。
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.zh-CN.md")
#: 会把字符串交到用户眼前的地方：插件（技能、生成器）、引擎与桌面后端、界面、脚本
SCOPES = ("codex-plugin", "src", "web/src", "scripts")
SUFFIXES = {".py", ".md", ".ts", ".tsx", ".json", ".yaml", ".yml", ".txt"}
TITLE_REF = re.compile(r"README「([^」]+)」")
ANCHOR_REF = re.compile(r"#(using-tavotto-[a-z0-9-]+)")


def _headings() -> list[str]:
    titles = []
    for name in READMES:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#{1,6} (.+)$", line)
            if m:
                titles.append(m.group(1).strip())
    return titles


def _slug(title: str) -> str:
    """GitHub 的标题锚点：小写、去掉标点（保留连字符）、空格换连字符。"""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def _references() -> list[tuple[str, str, str]]:
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", *SCOPES],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    refs = []
    for rel in tracked:
        path = ROOT / rel
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        refs += [(rel, "title", m.group(1)) for m in TITLE_REF.finditer(text)]
        refs += [(rel, "anchor", m.group(1)) for m in ANCHOR_REF.finditer(text)]
    return refs


def test_every_readme_section_reference_resolves():
    titles = _headings()
    slugs = {_slug(t) for t in titles}
    dangling = [
        f"{rel}: {kind} {value!r}"
        for rel, kind, value in _references()
        if (kind == "title" and not any(t.startswith(value) for t in titles))
        or (kind == "anchor" and value not in slugs)
    ]
    assert not dangling, "这些地方指向 README 里不存在的章节：\n" + "\n".join(dangling)


def test_the_scan_sees_the_known_references():
    """两种写法各有一处已知引用（技能里的首用章节名、界面里的 Codex 指南锚点），扫不到说明判据空了。"""
    refs = _references()
    assert ("web/src/lib/brand.ts", "anchor", "using-tavotto-with-codex-for-the-first-time") in refs
    assert any(
        kind == "title" and value.startswith("在 Codex 中第一次使用") for _, kind, value in refs
    )
