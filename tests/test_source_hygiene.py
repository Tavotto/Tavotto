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
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
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


def test_no_venv_or_self_referential_symlink_is_tracked() -> None:
    """版本库里不许有 `.venv`，也不许有**指向仓库内部的绝对路径符号链接**。

    真实事故（2026-08-19 ~ 20，两天内两次）：`.venv` 被误提交成一个 mode 120000
    的符号链接，内容是它自己的绝对路径。`.gitignore` 当时写的是 `.venv/`
    ——**带斜杠只匹配目录**，挡不住这个符号链接文件。

    后果不是「仓库里多个没用的文件」，而是 git 会在 `stash pop`、`checkout`、
    目录改名这些**普通操作**里忠实地把它恢复回来，**当场顶掉开发者真正的
    venv**，然后 `.venv/bin/python` 报 "too many levels of symbolic links"。
    第一次是 stash pop 触发的，第二次是把检出目录改名触发的。

    绝对路径的符号链接进版本库一律是错的：它在别人机器上必然指向不存在的地方，
    而在原作者机器上则可能自引用。
    """
    out = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout

    offenders = []
    for line in out.splitlines():
        meta, _, name = line.partition("\t")
        mode = meta.split()[0]
        if name in (".venv", "venv") or name.endswith("/.venv"):
            offenders.append(f"{name}（虚拟环境不该进版本库）")
            continue
        if mode != "120000":                 # 只有符号链接需要再查内容
            continue
        try:
            target = (ROOT / name).readlink()
        except OSError:
            continue
        if target.is_absolute():
            offenders.append(f"{name} → {target}（绝对路径符号链接）")

    assert not offenders, (
        "这些条目会在别人机器上（或改名/stash 之后）指向错误的位置：\n  "
        + "\n  ".join(offenders)
    )



def test_windows_bound_subprocesses_pin_their_decoding():
    """跑在 Windows CI 上的 subprocess 必须显式指定 encoding。

    不指定的话 `text=True` 用系统默认（Windows 上是 cp1252 / 中文机器是
    cp936）解码子进程输出。我们的 CLI 与脚本大量输出中文，于是读线程当场
    `UnicodeDecodeError` 并死掉——而 `returncode` 不经解码，照样拿得到，
    **用例继续绿**。代价是 `out.stdout` / `out.stderr` 变成空的：那条断言
    一旦真的失败，它的报错信息也是空的。**一个恰好在你需要它时失灵的诊断。**

    2026-08-22 实测：main 上 Windows 腿每次都打这段 traceback
    （`charmap codec can't decode byte 0x8d`），来自
    `test_ci_tooling.py` 跑 `lab_preflight.py --help`（帮助文本是中文）。
    而**同一个仓库的 `test_ci_qualification.py` 对同一个脚本早就写对了**
    ——修了一处没扫其余，所以这条判据按范围扫，不逐个盯。

    范围只收「会在 Windows 上跑」的：`tests/`（backend 的 windows 腿）与两个
    冒烟脚本（windows-exe-smoke）。`scripts/ci/` 的实验室脚本只跑 Linux，
    那里 `text=True` 没有这个问题，硬要求它们也写等于给噪音加噪音。
    """
    import ast
    scope = sorted(ROOT.joinpath("tests").rglob("*.py")) + [
        ROOT / "scripts" / "smoke_app.py", ROOT / "scripts" / "smoke_desktop.py"]
    offenders = []
    for path in scope:
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", "")) or ""
            if name not in ("run", "Popen", "check_output"):
                continue
            kw = {k.arg for k in node.keywords}
            # `**cfg` 展开（keyword.arg is None）静态判不出里面有没有 encoding。
            # `test_compat_runner` 就是这么传的（`**self._DECODE`），而它本来
            # 就是对的——把它判成违规，是判据问错了问题：要问的不是「有没有
            # `encoding=` 这个字面关键字」，是「这个调用最终有没有设上编码」。
            # 判不出就**不判**，并把这个盲点写在明处，别假装覆盖到了。
            if any(k.arg is None for k in node.keywords):
                continue
            texty = ("text" in kw) or ("universal_newlines" in kw) or name == "check_output"
            if texty and "encoding" not in kw:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}(...)")
    assert not offenders, (
        "这些 subprocess 在 Windows 上会用系统默认编码解码子进程输出，"
        "中文一出现就静默丢掉 stdout/stderr：\n  " + "\n  ".join(offenders))
