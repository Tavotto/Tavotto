"""源码卫生：文本源文件里不许出现字面 NUL 字节。

为什么值得一条用例看着：git 判定二进制的依据就是「前 8000 字节里有没有
NUL」。一个字面 NUL 混进 .ts 里，编译器与测试全都照常绿灯（它在语法上
就是一个合法的字符串字符），唯一的症状是 `git diff` / `git log -p` / PR
页面从此对这个文件**只显示 `Binary files … differ`**——文件还在版本控制
里，却再也没人能审查它的改动，而且并发修改会撞出无法自动合并的冲突。

真实事故：`web/src/store/documentStore.ts` 里 `p.path.join('\\u0000')` 的
分隔符被写成了字面 NUL 而不是转义，于是这个承载撤销防线与自动保存的文件
在历次 PR 里一次都没能以 diff 形式被人看过。需要 NUL 当分隔符是完全正当
的，写成转义即可，运行时逐位相同。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 按扩展名认「文本源文件」。二进制资产（图标 / 字体 / 测试用的 PDF）不在此列。
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".rs", ".json", ".jsonc",
    ".md", ".css", ".html", ".yml", ".yaml", ".toml", ".sh", ".nsi", ".spec",
}


def _tracked_text_files() -> list[Path]:
    """git 记录在案的文本源文件。用 git ls-files 而不是 rglob——

    后者会把 node_modules / .venv / target / dist 这些体量巨大的产物一起扫进来，
    既慢又会因为别人的依赖里有二进制而误报。
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [
        ROOT / name
        for name in out.split("\0")
        if name and Path(name).suffix.lower() in TEXT_SUFFIXES
    ]


def test_no_literal_nul_in_text_sources() -> None:
    offenders = []
    for path in _tracked_text_files():
        try:
            data = path.read_bytes()
        except OSError:                      # 文件已被删除但索引还没更新
            continue
        if b"\0" in data:
            at = data.index(b"\0")
            line = data[:at].count(b"\n") + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, (
        "这些文本源文件里有字面 NUL 字节，git 会把它们当二进制、"
        "从此无法 diff 审查（需要 NUL 当值时请写成 '\\u0000' 转义）：\n  "
        + "\n  ".join(offenders)
    )
