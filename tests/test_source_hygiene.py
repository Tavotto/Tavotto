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



def test_no_launcher_leaves_a_child_pipe_undrained():
    """开了 `stdout=PIPE` 就必须读它，否则应用会在 64 KiB 之后整个卡死。

    2026-08-22 实测（soak 第一次真正跑起来时发现）：`soak.py` 用
    `stdout=PIPE` 起应用却一次都不读，日志写满管道缓冲之后，应用**下一次
    写日志永久阻塞**——而 `logging` 的 handler 锁在它手里，于是每个请求线程
    都堵在 `acquire` 上，`/api/version` 都不再应答。

    症状极具误导性：看上去像**产品死锁**（8 个线程 futex_wait + 1 个
    pipe_write），我一度就是这么判断的。py-spy 的栈才指到 `logging.emit`。
    两次独立运行都确定性地停在第 160 轮——正是日志量填满缓冲的那一刻。

    判据要求二选一：**要么不开 PIPE**（落文件或 DEVNULL），**要么真的读**。
    这四个脚本的诊断本来就走数据目录里的 `app.log`，所以一律落文件——比
    DEVNULL 还多留一份启动期 traceback（那些进不了 app.log）。
    """
    import ast
    offenders = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # **不再接受「文件里某处读过 proc.stdout」当作排空。** Codex 在 #58 上
        # 指出：`smoke_app.py` 读它是在 `proc.wait()` **之后**，而那正是同一个
        # 死锁——应用写满缓冲 → 阻塞在写日志 → `/api/shutdown` 不应答 →
        # `wait()` 超时 → terminate/kill → 冒烟报「强制停止」，症状指向
        # 「关不干净」，与真实原因毫不相干。
        #
        # 「排空是不是与子进程并发」静态证不了，所以判据换成更简单也更硬的一条：
        # 这些启动器**根本不需要**流式读子进程输出（诊断走 app.log 与落盘的
        # server-stdout.log），那就不许开这个管道。
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "Popen"):
                continue
            kws = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
            # **两个流都要看。** 子进程有 stdout 和 stderr 两条出路，任一条是
            # 没人读的 PIPE 都会以同样的方式把它堵死——`stdout=<文件>,
            # stderr=PIPE` 照样死锁。上一版只检查 stdout，Codex 在 #58 上指出
            # 的正是这个：判据只钉了一条腿。
            # `stderr=STDOUT` 是合并进 stdout，不额外开管道，所以不算。
            for stream in ("stdout", "stderr"):
                val = kws.get(stream, "")
                if "PIPE" not in val:
                    continue      # 落文件 / DEVNULL / STDOUT / 继承，都不会填满缓冲
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} {stream}=PIPE"
                    "——启动器一律落文件或 DEVNULL；「稍后再读」不算排空")
    assert not offenders, (
        "这些子进程的输出管道开了却没人读，写满 64 KiB 之后应用会卡死在写日志上：\n  "
        + "\n  ".join(offenders))


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
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
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
            # **文本模式由 Python 的真实触发条件决定，不由关键字有无或函数名。**
            # 实测（3.13）：只给 encoding → str，只给 errors → str，
            # 裸 check_output → bytes。所以
            #   * `run(..., encoding="cp1252")` 没有 text=True 也是文本模式，
            #     照样复现那个 bug——上一版判据直接跳过它；
            #   * 裸 `check_output()` 回的是 bytes，不该被判。
            # Codex 在 #57 上指出的正是这两头。
            texty = bool({"text", "universal_newlines", "encoding", "errors"} & kw)
            if not texty:
                continue
            enc = next((k.value for k in node.keywords if k.arg == "encoding"), None)
            if enc is None:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} {name}(...) 没给 encoding")
                continue
            # **光有这个关键字不够**：`encoding=None` 就是「按系统默认」，
            # `encoding="cp1252"` 更是直接复现那个 bug。判据要问的是「解码用的
            # 是不是 UTF-8」，不是「有没有写过 encoding 这个词」——Codex 在 #57
            # 上指出的正是这个缺口。判不出的（变量、表达式）不放行，指名道姓。
            if (isinstance(enc, ast.Constant) and isinstance(enc.value, str)
                    and enc.value.lower().replace("-", "") == "utf8"):
                continue
            # **唯一的豁免：复现这个 bug 本身。** 那条用例必须用旧代码页才能
            # 证明「不给 utf-8 会丢掉诊断」。豁免要求同一行有 `# 复现用` 标记
            # ——不是按文件名放行，否则那个文件里往后写的每一处都跟着白拿。
            block = "\n".join(text.splitlines()[node.lineno - 1: node.lineno + 3])
            if "复现用" in block:
                continue
            offenders.append(
                f"{path.relative_to(ROOT)}:{node.lineno} {name}(...) "
                f"encoding={ast.unparse(enc)}——必须是 utf-8 字面量"
                "（复现这个 bug 的用例请在调用处标 `# 复现用`）")
    assert not offenders, (
        "这些 subprocess 在 Windows 上会用系统默认编码解码子进程输出，"
        "中文一出现就静默丢掉 stdout/stderr：\n  " + "\n  ".join(offenders))


def test_agent_instruction_files_do_not_diverge():
    """`AGENTS.md` 与 `CLAUDE.md` 必须逐字节相同。

    两份都是给 agent 读的指令，但**不同的 agent 读不同的那份**——Codex 读
    `AGENTS.md`，Claude 读 `CLAUDE.md`。手工并行维护的结果是它们会漂开，而
    漂开的那一份在**没人读的时候**默默错着。

    2026-08-22 实测到的漂移不是「少了一节」，是**内容被改错了**：某次对
    `AGENTS.md` 做过一遍 `claude` → `Codex` 的全文替换，于是那份指令里写着
    用 `Codex -p` 调 Claude CLI、配置在 `~/.Codex/settings.json`、以及
    「用户在别的终端里跑 Codex/codex」。**给 agent 的指令写错，比没写更坏**
    ——它会照着做。

    Codex 在 #59 上发现这件事的入口是「新加的一节只进了 CLAUDE.md」，
    而顺着查下去更严重的是那次替换。所以判据不是「都有那一节」，是
    **逐字节相同**：只要允许差异存在，就得有人去判断哪些差异是对的。
    """
    # sdist 里必须两份都在（pyproject 的 sdist include 收了它们）。少任何一份
    # 都当**失败**而不是跳过：跳过等于在最需要对拍的地方不对拍，而这两份漂开
    # 过一次就是这么没被发现的。
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert (ROOT / name).is_file(), (
            f"{name} 不在源码树里——sdist 收了 tests 却没收它，"
            "从 sdist 跑测试会在这里 FileNotFoundError")
    a = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    b = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    if a == b:
        return
    import difflib
    diff = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(),
        fromfile="CLAUDE.md", tofile="AGENTS.md", lineterm="", n=0))
    raise AssertionError(
        "两份 agent 指令漂开了——改一份必须同步另一份（内容相同，不是各自表述）：\n"
        + "\n".join(diff[:40]))


def test_sdist_ships_the_files_its_tests_read():
    """sdist 收了 `tests/`，就得收上它们要读的东西。

    2026-08-22（#59 的 review）：`CLAUDE.md` / `AGENTS.md` 的对拍用例进了
    sdist，而那两个文件没进——从发布的源码包跑测试会 `FileNotFoundError`，
    而且**失败的是一条与被测代码毫无关系的用例**，读的人要绕一大圈才明白
    「不是代码坏了，是包少收了东西」。
    """
    import re
    inc = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # 按**行首的** `[` 切下一节：直接 split("[") 会被 `include = [` 的方括号
    # 截断，段里一个条目都不剩，而断言「tests 在不在」当场就红——那不是被测
    # 对象错了，是切分判据错了。
    seg = re.split(r"\n\[", inc.split("[tool.hatch.build.targets.sdist]", 1)[1], 1)[0]
    listed = set(re.findall(r'"([^"]+)"', seg))
    assert "tests" in listed, "sdist 不再收 tests 了——这条判据的前提没了"
    for need in ("CLAUDE.md", "AGENTS.md"):
        assert need in listed, (
            f"sdist 收了 tests 却没收 {need}，而 tests 里有用例要读它")
