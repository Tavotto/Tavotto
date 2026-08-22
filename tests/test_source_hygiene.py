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
        cwd=ROOT, capture_output=True, text=True, check=True,
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
