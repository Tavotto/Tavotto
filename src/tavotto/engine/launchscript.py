"""启动脚本（ADR 0194 §二）：AI 修复写的一段 Python，Tavotto 运行脚本 S 时执行它的字节、身份仍是 S。

* 放在项目的 `tavottofile/launchers/`（Tavotto 自己的目录）：用户的代码文件夹里不多出文件；不进 `sys.path`、从不按模块名 import，
  所以不会遮蔽任何 import。
* 每个 S 至多一份，文件名由 S 的项目相对路径确定（`<stem>-<路径摘要>.py`），不需要索引。
* 执行走影子脚本那条路（ADR 0143）：safe 的 `--script-source` / native bridge 的同名参数——`__file__` / `sys.argv[0]` 是 S，
  `sys.path[0]` 是 S 所在目录，捕获到的图挂在 S 名下。
* 只认目录里的**普通文件**：符号链接 / 目录 / 读不了的一律当没有（不跟出 Tavotto 的目录）。

纯标准库，不 import app。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: 与 `config.PROJECT_STORE_DIRNAME` 同值（本模块被 execspec 引用，避免把 config 拉进它的 import 闭包）；看护用例对拍
STORE_DIRNAME = "tavottofile"
LAUNCHER_DIRNAME = "launchers"
#: 启动脚本的字节上限：它只是「怎么调用」的胶水，不是第二份画图脚本
MAX_BYTES = 64 * 1024


class LauncherError(ValueError):
    """写不了：`code` 是闭集码（`too_large` / `syntax_error` / `not_text` / `unsafe_dir`）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _rel_key(script: str) -> str:
    return Path(os.fspath(script)).as_posix().lstrip("./")


def launcher_dir(project_root) -> Path:
    return Path(os.fspath(project_root)) / STORE_DIRNAME / LAUNCHER_DIRNAME


def path_for(project_root, script: str) -> Path:
    """S 的启动脚本**应该**在的位置（不论存不存在）。"""
    rel = _rel_key(script)
    stem = Path(rel).stem or "script"
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:40] or "script"
    digest = hashlib.sha256(os.path.normcase(rel).encode("utf-8")).hexdigest()[:10]
    return launcher_dir(project_root) / f"{safe_stem}-{digest}.py"


def _contained_regular_file(project_root, path: Path) -> bool:
    """`path` 是 `tavottofile/launchers/` 里的普通文件，且这一路没有符号链接把它带出项目。"""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        store = launcher_dir(project_root)
        if store.is_symlink() or store.parent.is_symlink():
            return False
        real = os.path.realpath(path)
        base = os.path.realpath(store)
        return os.path.commonpath([os.path.normcase(real), os.path.normcase(base)]) == (
            os.path.normcase(base)
        )
    except (OSError, ValueError):
        return False


def source_for(project_root, script: str | None) -> str | None:
    """S 有启动脚本就回它的真实路径（给 `--script-source`）；没有 / 不可信回 None。"""
    if not script or project_root is None:
        return None
    path = path_for(project_root, script)
    return os.fspath(path) if _contained_regular_file(project_root, path) else None


def revision_of(project_root, script: str | None) -> str:
    """启动脚本的字节摘要（并进 S 的源码修订：改 / 删启动脚本 = 计划过期）；没有回空串。"""
    src = source_for(project_root, script)
    if src is None:
        return ""
    try:
        return hashlib.sha256(Path(src).read_bytes()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def read(project_root, script: str) -> str | None:
    src = source_for(project_root, script)
    if src is None:
        return None
    try:
        return Path(src).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def validate(code: str) -> None:
    """写之前的检查：文本、大小、能被本机 Python 解析（不执行）。"""
    if not isinstance(code, str) or "\x00" in code:
        raise LauncherError("not_text", "The launcher must be plain Python text.")
    if len(code.encode("utf-8")) > MAX_BYTES:
        raise LauncherError(
            "too_large",
            f"The launcher is larger than {MAX_BYTES // 1024} KiB; keep it to the glue.",
        )
    try:
        compile(code, "<launcher>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise LauncherError(
            "syntax_error", f"Syntax error on line {exc.lineno}: {exc.msg}"
        ) from exc


def write(project_root, script: str, code: str) -> tuple[Path, str | None]:
    """写（或替换）S 的启动脚本。回 `(路径, 原来的内容或 None)`——调用方把原内容交给快照，撤销时写回。"""
    validate(code)
    store = launcher_dir(project_root)
    for part in (store.parent, store):
        if part.is_symlink():
            raise LauncherError(
                "unsafe_dir", "Tavotto's folder in this project is a link; not writing there."
            )
    store.mkdir(parents=True, exist_ok=True)
    path = path_for(project_root, script)
    previous = read(project_root, script)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(code, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path, previous


def remove(project_root, script: str) -> bool:
    """删掉 S 的启动脚本（撤销）；本来就没有回 False。"""
    path = path_for(project_root, script)
    if not _contained_regular_file(project_root, path):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
