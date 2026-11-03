"""脚本调外部命令却找不到可执行文件：失败那一刻的闭集事实（诊断包 `project.missing_commands`，bundle schema 15）。

用户脚本 `subprocess.run(["rsvg-convert", ...])` 在 safe 下只报 `FileNotFoundError: ... 'rsvg-convert'`，任务结果
只有 `script_error`——维护者看不出：缺的是哪个命令、它在不在渲染进程的 PATH 上、常见安装位置有没有它（Homebrew 的
keg 装了但没 link 是最常见的一种）。这里在 worker 里、失败那一刻，用 worker **实际的 env** 把这几件事量出来。

**判据的主语**：「这个 worker 进程、脚本抛出的那一个 `FileNotFoundError`、此刻的 PATH 与固定目录表」。
`FileNotFoundError` 也是 `open("data.csv")` 的异常，光看 `filename` 是裸名分不出命令还是数据文件，所以**必须**
在异常的 traceback 里看到 `subprocess.py::_execute_child`（或 `os.exec*` / `os.posix_spawn*` 家族的帧）才算命令：
open 一个裸文件名不会误判。只读检查（`isfile` / `listdir` 级别），不执行任何东西。

**出门口径**：事实本身不含路径与用户文字——命令名在公开常见工具白名单（`PUBLIC_COMMANDS`）里原样，其余由
`diagnostics` 用与缺依赖现场同一个哈希函数换成 `cmd:<哈希>`；目录只写成固定枚举名（`DIR_TABLE` 的键），PATH 只有
段数与「是否含各固定目录」的布尔。敏感参数运行（命令名可由 argv 变形而来）只带白名单命令名，其余置空。

纯标准库：worker（平铺加载）、父进程与测试都能 import。
"""

from __future__ import annotations

import errno
import glob
import os
import shutil
import sys

#: 命令名的公开白名单：原样出门。其余名字是用户自己的工具，出门时哈希。
PUBLIC_COMMANDS = frozenset(
    {
        "rsvg-convert",
        "fc-match",
        "fc-list",
        "ffmpeg",
        "ffprobe",
        "gs",
        "gswin64c",
        "gswin32c",
        "pdflatex",
        "xelatex",
        "lualatex",
        "latex",
        "dvipng",
        "dvips",
        "kpsewhich",
        "pdftocairo",
        "pdftoppm",
        "pdftops",
        "inkscape",
        "convert",
        "magick",
        "identify",
        "Rscript",
        "R",
        "gnuplot",
        "dot",
        "pandoc",
        "git",
        "python",
        "python3",
        "node",
        "java",
        "make",
        "cmake",
        "gcc",
        "g++",
        "cc",
        "ls",
        "cat",
        "sh",
        "bash",
        "tar",
        "unzip",
        "curl",
        "wget",
        "optipng",
        "pngquant",
        "jpegoptim",
        "svgo",
        "mutool",
        "qpdf",
        "tesseract",
        "libreoffice",
        "soffice",
    }
)

#: 固定目录枚举名 → 通配模式（按平台）。**键是唯一会出门的目录表示**；模式里的路径只在本机量，不出门。
_DARWIN = {
    "homebrew_bin": ("/opt/homebrew/bin",),
    "usr_local_bin": ("/usr/local/bin",),
    "macports_bin": ("/opt/local/bin",),
    "texbin": ("/Library/TeX/texbin",),
    "x11_bin": ("/opt/X11/bin",),
    "usr_bin": ("/usr/bin",),
    "homebrew_opt": ("/opt/homebrew/opt/*/bin", "/usr/local/opt/*/bin"),
    "homebrew_cellar": ("/opt/homebrew/Cellar/*/*/bin", "/usr/local/Cellar/*/*/bin"),
}
_LINUX = {
    "usr_bin": ("/usr/bin",),
    "usr_local_bin": ("/usr/local/bin",),
    "snap_bin": ("/snap/bin",),
    "linuxbrew_bin": ("/home/linuxbrew/.linuxbrew/bin",),
    "texbin": ("/usr/local/texlive/*/bin/*",),
}
_WIN = {
    "chocolatey_bin": ("C:\\ProgramData\\chocolatey\\bin",),
    "scoop_shims": ("~\\scoop\\shims",),
    "inkscape_bin": ("C:\\Program Files\\Inkscape\\bin",),
    "ghostscript_bin": ("C:\\Program Files\\gs\\*\\bin",),
    "miktex_bin": (
        "C:\\Program Files\\MiKTeX\\miktex\\bin\\x64",
        "~\\AppData\\Local\\Programs\\MiKTeX\\miktex\\bin\\x64",
    ),
    "texlive_bin": ("C:\\texlive\\*\\bin\\windows", "C:\\texlive\\*\\bin\\win64"),
    "r_bin": ("C:\\Program Files\\R\\*\\bin",),
    "msys2_bin": ("C:\\msys64\\ucrt64\\bin", "C:\\msys64\\mingw64\\bin"),
}
#: 「keg」目录（Homebrew 把包装在这里、`brew link` 才把可执行文件放进 `*_bin`）
KEG_DIRS = ("homebrew_opt", "homebrew_cellar")
#: 「已 link / 通常在 PATH 上」的目录：keg 里有而这些里没有 = keg 未链接
LINKED_DIRS = ("homebrew_bin", "usr_local_bin")

_WIN_EXTS = (".exe", ".cmd", ".bat", ".com")
_GLOB_LIMIT = 64
_NAME_LIMIT = 128
_PATH_SEGMENT_LIMIT = 256

#: 测试可替换（平台名 → 目录表）；生产路径由 `dir_table()` 按 `sys.platform` 选
DIR_TABLE: dict[str, dict[str, tuple[str, ...]]] = {
    "darwin": _DARWIN,
    "linux": _LINUX,
    "win32": _WIN,
}

#: 能说明「这是一次拉起外部程序」的 traceback 帧：(文件基名, 函数名)
_SPAWN_FRAMES = frozenset(
    {
        ("subprocess.py", "_execute_child"),
        ("os.py", "execv"),
        ("os.py", "execve"),
        ("os.py", "execvp"),
        ("os.py", "execvpe"),
        ("os.py", "_execvpe"),
    }
)


def dir_table(platform: str | None = None) -> dict[str, tuple[str, ...]]:
    key = (platform or sys.platform).lower()
    for name, table in DIR_TABLE.items():
        if key.startswith(name):
            return table
    return {}


def _chain(exc: BaseException):
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 16:
        yield cur
        cur = cur.__cause__ or cur.__context__
        seen += 1


def _spawned(exc: BaseException) -> bool:
    tb = exc.__traceback__
    seen = 0
    while tb is not None and seen < 256:
        code = tb.tb_frame.f_code
        if (os.path.basename(code.co_filename), code.co_name) in _SPAWN_FRAMES:
            return True
        tb = tb.tb_next
        seen += 1
    return False


def _first_word(text: str) -> str:
    """命令行字符串的第一个词（Windows list2cmdline 规则：带引号取引号内，否则到第一个空白）。"""
    text = text.lstrip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text[1:]
    return text.split(None, 1)[0] if text else ""


def _recovered_name(exc: BaseException) -> str | None:
    """Windows 的 `_winapi.CreateProcess` 抛 FileNotFoundError 不带 `filename`：
    从 `subprocess.py` 的 Popen.__init__ / `_execute_child` 帧的局部变量恢复命令名。"""
    try:
        tb = exc.__traceback__
        seen = 0
        while tb is not None and seen < 256:
            code = tb.tb_frame.f_code
            if os.path.basename(code.co_filename) == "subprocess.py" and code.co_name in (
                "__init__",
                "_execute_child",
            ):
                loc = tb.tb_frame.f_locals
                exe = loc.get("executable")
                args = loc.get("args")
                for cand in (exe, args):
                    if cand is None:
                        continue
                    if isinstance(cand, (list, tuple)):
                        if not cand:
                            continue
                        cand = cand[0]
                    if isinstance(cand, (str, bytes, os.PathLike)):
                        text = os.fsdecode(cand)
                        if isinstance(args, (str, bytes)) and cand is args:
                            text = _first_word(text)
                        if text:
                            return text
            tb = tb.tb_next
            seen += 1
    except Exception:
        return None
    return None


def command_of(exc: BaseException) -> str | None:
    """异常链里「拉起外部程序却找不到」的那个裸命令名；不是（含 open 一个裸文件名）回 None。"""
    for cur in _chain(exc):
        if not isinstance(cur, OSError):
            continue
        if cur.errno != errno.ENOENT and getattr(cur, "winerror", None) != 2:
            continue
        name = cur.filename
        if not name and _spawned(cur):
            name = _recovered_name(cur)
        if isinstance(name, bytes):
            try:
                name = os.fsdecode(name)
            except (TypeError, ValueError):
                continue
        if not isinstance(name, str) or not name or len(name) > _NAME_LIMIT:
            continue
        if "/" in name or "\\" in name or os.sep in name or (os.altsep and os.altsep in name):
            continue
        if _spawned(cur):
            return name
    return None


def _norm(seg: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expanduser(seg))).rstrip("\\/")


def _scan(patterns: tuple[str, ...], cands: tuple[str, ...]) -> tuple[bool, bool]:
    """(有可执行的命令, 扫描被上限截断)。通配模式按「模式/候选名」直接匹配，不先枚举目录再截断。"""
    for pat in patterns:
        pat = os.path.expanduser(pat)
        wild = any(c in pat for c in "*?[")
        for c in cands:
            if not wild:
                if _runnable(os.path.join(pat, c)):
                    return True, False
                continue
            n = 0
            try:
                for hit in glob.iglob(os.path.join(pat, glob.escape(c))):
                    if _runnable(hit):
                        return True, False
                    n += 1
                    if n >= _GLOB_LIMIT:
                        return False, True
            except OSError:
                continue
    return False, False


def _candidates(name: str, platform: str) -> tuple[str, ...]:
    if platform.lower().startswith("win"):
        low = name.lower()
        return (name,) if low.endswith(_WIN_EXTS) else tuple(name + e for e in _WIN_EXTS)
    return (name,)


def _runnable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def collect(
    exc: BaseException,
    *,
    env: dict | None = None,
    platform: str | None = None,
    sensitive: bool = False,
) -> dict:
    """worker 里调用：`{"missing_command": {...}}`；不是「找不到外部命令」回 `{}`。

    只读检查，用 worker 实际的 env。`command` 在敏感运行里不在白名单就置空（argv 可以变形出命令名）。"""
    name = command_of(exc)
    if name is None:
        return {}
    plat = platform or sys.platform
    env = os.environ if env is None else env
    table = dir_table(plat)
    cands = _candidates(name, plat)

    try:
        on_path = shutil.which(name, path=env.get("PATH", env.get("Path"))) is not None
    except (OSError, ValueError, TypeError):
        on_path = False

    raw_path = env.get("PATH", env.get("Path")) or ""
    segments = [s for s in str(raw_path).split(os.pathsep) if s][:_PATH_SEGMENT_LIMIT]
    normed = {_norm(s) for s in segments}
    has: dict[str, bool] = {}
    found_in: list[str] = []
    truncated = False
    keg_cut = False
    for key, patterns in table.items():
        if key not in KEG_DIRS:
            has[key] = any(_norm(p) in normed for p in patterns if not any(c in p for c in "*?["))
        hit, cut = _scan(patterns, cands)
        if hit:
            found_in.append(key)
        elif cut:
            truncated = True
            if key in KEG_DIRS:
                keg_cut = True
    keg_present: bool | None = any(k in found_in for k in KEG_DIRS)
    linked = any(k in found_in for k in LINKED_DIRS)
    if not keg_present and keg_cut:
        keg_present = None
    safe_name = name if name in PUBLIC_COMMANDS else ("" if sensitive else name)
    return {
        "missing_command": {
            "command": safe_name,
            "on_path": on_path,
            "path_segments": len(segments),
            "path_has": has,
            "found_in": found_in,
            "keg_present": keg_present,
            "keg_unlinked": (keg_present and not linked) if keg_present is not None else None,
            **({"scan_truncated": True} if truncated else {}),
        }
    }


def dir_names() -> frozenset[str]:
    """出门允许的目录枚举名全集（各平台目录表的键）。"""
    return frozenset(k for t in (_DARWIN, _LINUX, _WIN) for k in t)
