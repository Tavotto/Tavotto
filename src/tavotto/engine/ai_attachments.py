"""改图助手的附件（ADR 0175）：用户在输入框里附的文件（图片或数据 / 其他文件），只给用户自己连的那个 AI 用。

流程：前端把每个文件（图片已缩到 ≤ 4 MiB）逐个 `POST /api/ai/attachments?name=…` 暂存到 `data_dir()/cache/ai_attachments_staged/`，
拿回不透明 id；发送时 `POST /api/ai/run` 只带 id。`ai_bridge.run` 起会话时 `materialize` 把暂存的文件**移**进这次任务的工作目录：

* 候选工作区（TAF / 内置执行器）：`<候选目录>/_attachments/`——与候选同生同灭，候选清理时一起删；提交前检查（`ai_figure_edit._tree`）
  把这个目录整个排除在「候选树」之外，所以它既不会被当成「改了别的文件」，也绝不会被当成要写回的东西；
* 旧路径（外部 CLI 原地改）：`data_dir()/cache/ai_attachments/<sid>/`——**绝不写进用户项目目录**；会话收尾时删。

图片（魔数认 PNG / JPEG / GIF / WebP、≤ 4 MiB）给模型「看」；其余文件原样放进去，由 AI 自己读（CSV / TXT / JSON 等文本最常见）。
不附文件时本模块一行都不执行，请求、提示词、工作区与原来逐字节相同。附件不进遥测、不进诊断包（诊断包只取白名单里的日志与报告，
不读 `cache/` 下的这两个目录；`tests/test_ai_attachments.py` 钉住）。纯标准库。
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config
from .agent_runner import media
from .imageheader import image_dimensions

#: 一次最多附几个（前端同一个数；超出的后端也只取前 MAX_FILES 个）
MAX_FILES = 6
#: 图片单张字节上限 = 内置执行器 `read_image` 的上限（`agent_runner.media.MAX_IMAGE_BYTES`），同一个数
MAX_IMAGE_BYTES = media.MAX_IMAGE_BYTES
#: 图片单张像素上限（宽 × 高）：4000 万像素约等于 8000 × 5000；再大的图模型端也会自行缩小，没有意义
MAX_PIXELS = 40_000_000
#: 非图片文件的单个上限与一次发送的合计上限（合计口径同「联系作者」附件的 50 MiB）
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
#: 候选工作区 / 旧路径目录里的子目录名；提交前检查按这个名字把它排除在候选树之外
ATTACHMENTS_DIRNAME = "_attachments"
#: 暂存的文件最多留这么久（秒）：用户附了文件却没发送也不会一直留在磁盘上
STAGED_TTL_S = 3600
#: 旧路径会话目录的兜底寿命（秒）：正常在会话收尾时删，进程被杀留下的按它清
SESSION_TTL_S = 24 * 3600

_ID = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)


def staged_dir() -> Path:
    return config.data_dir() / "cache" / "ai_attachments_staged"


def session_root() -> Path:
    return config.data_dir() / "cache" / "ai_attachments"


class AttachmentError(ValueError):
    """暂存被拒（稳定码，前端据此换一句人话；前端本来就会先缩小 / 先判大小，走到这里是兜底）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def safe_name(name: object, fallback: str = "file") -> str:
    """用户给的文件名 → 能安全落盘的一段（去路径、控制字符、Windows 保留字符与保留名；保留扩展名与中文）。≤ 80 字符。"""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE.sub("_", base).strip(" .")
    if not base or _WIN_RESERVED.match(base):
        base = f"{fallback}_{base}" if base else fallback
    if len(base) > 80:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 80 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else base[:80]
    return base


def _sniff(raw: bytes) -> str | None:
    img = media.from_bytes(raw)
    return img.mime_type if img is not None else None


def _prune(root: Path, ttl: float) -> None:
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    cutoff = time.time() - ttl
    for p in entries:
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        except OSError:
            continue


def stage(raw: bytes, name: str = "") -> dict:
    """暂存一个文件 → `{id, kind, mime, bytes, width, height, name}`；`kind` 是 `image`（魔数认得、≤ 4 MiB、像素不超）或 `file`。
    魔数是图片但超了图片上限 → 当图片拒绝（前端本该先缩小）；不是图片的按 `MAX_FILE_BYTES` 判。"""
    if not raw:
        raise AttachmentError("attachment_empty")
    mime = _sniff(raw) if len(raw) <= MAX_IMAGE_BYTES else None
    looks_image = mime is not None or raw.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    )
    dims = image_dimensions(raw) if mime else None
    if mime:
        if dims and dims[0] * dims[1] > MAX_PIXELS:
            raise AttachmentError("attachment_too_large")
        kind = "image"
    elif looks_image and len(raw) > MAX_IMAGE_BYTES:
        raise AttachmentError("attachment_too_large")
    else:
        kind = "file"
        if len(raw) > MAX_FILE_BYTES:
            raise AttachmentError("attachment_too_large")
    clean = safe_name(name, "attachment")
    root = staged_dir()
    root.mkdir(parents=True, exist_ok=True)
    _prune(root, STAGED_TTL_S)
    rid = uuid.uuid4().hex
    (root / f"{rid}__{clean}").write_bytes(raw)
    return {
        "id": rid,
        "kind": kind,
        "mime": mime,
        "bytes": len(raw),
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
        "name": clean,
    }


def discard(rid: str) -> bool:
    """用户在输入框里点 × 去掉一个：把暂存的那份删掉。不存在 = 已经没了，不算错。"""
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        return False
    gone = False
    for p in staged_dir().glob(f"{rid}__*"):
        try:
            p.unlink()
            gone = True
        except OSError:
            pass
    return gone


def _find(rid: str) -> Path | None:
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        return None
    try:
        for p in staged_dir().glob(f"{rid}__*"):
            if p.is_file() and not p.is_symlink():
                return p
    except OSError:
        pass
    return None


def valid_ids(ids: object) -> list[str]:
    """请求里的 `attachments`：只留格式合法、去重、至多 `MAX_FILES` 个；不是列表就当没有（不报错）。"""
    if not isinstance(ids, list):
        return []
    out: list[str] = []
    for x in ids:
        if isinstance(x, str) and _ID.fullmatch(x) and x not in out:
            out.append(x)
        if len(out) >= MAX_FILES:
            break
    return out


def materialize(ids: list[str], dest: Path) -> list[dict]:
    """把暂存的文件移进 `dest`（重名自动加序号）；→ `[{name, kind}]`，按给定顺序，`name` 相对 `dest`。
    暂存里找不到的 id（过期 / 已被删）、超过合计上限之后的，静默跳过：用户的任务照常发，只是少几个文件。"""
    out: list[dict] = []
    total = 0
    for rid in valid_ids(ids):
        src = _find(rid)
        if src is None:
            continue
        try:
            size = src.stat().st_size
            if total + size > MAX_TOTAL_BYTES:
                continue
            raw = src.read_bytes()
            kind = "image" if (len(raw) <= MAX_IMAGE_BYTES and _sniff(raw)) else "file"
            name = src.name.split("__", 1)[1] if "__" in src.name else src.name
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / name
            n = 1
            while target.exists():
                n += 1
                stem, dot, ext = name.rpartition(".")
                target = dest / (f"{stem}_{n}.{ext}" if dot else f"{name}_{n}")
            target.write_bytes(raw)
            src.unlink(missing_ok=True)
            total += size
            out.append({"name": target.name, "kind": kind})
        except OSError:
            continue
    return out


def session_dir(sid: str) -> Path:
    """旧路径（外部 CLI 在原项目里改）这次任务的附件目录：在数据目录里，不在项目里。"""
    return session_root() / sid


def remove_session(sid: str) -> None:
    """会话收尾 / 被拒时删旧路径的附件目录；只删 `session_root()` 之内的一层。"""
    try:
        root = Path(os.path.realpath(session_root()))
        target = Path(os.path.realpath(session_dir(sid)))
        if target.parent == root:
            shutil.rmtree(target, ignore_errors=True)
    except OSError:
        pass


def prune_sessions() -> None:
    """兜底：进程被杀时留下的旧路径目录，超过 `SESSION_TTL_S` 的清掉。"""
    root = session_root()
    if root.is_dir():
        _prune(root, SESSION_TTL_S)


# --------------------------------------------------------------------------- 模型能不能看图
#: 已知**只吃文字**的型号（按型号名的前缀判，小写，去掉 `厂商/` 前缀）。只列有把握的：判不出的一律当「不知道」，
#: 照常发送、不拦。名字里带 `vl` / `vision` / `omni` / `image` 的一律视为能看图（`qwen-vl`、`glm-4.5v` 等）。
_TEXT_ONLY_PREFIXES = (
    "deepseek",
    "minimax-m",
    "codestral",
    "gpt-3.5",
    "o1-mini",
    "o3-mini",
    "kimi-k2-",  # 带日期 / 变体后缀的 K2 文本版；`kimi-k2.5` 这类多模态新版不在此列
    "qwen-coder",
    "qwen2.5-coder",
    "qwen3-coder",
    "glm-4.5",
    "glm-4.6",
    "glm-4.7",
)
_VISION_WORDS = ("vl", "vision", "omni", "image", "multimodal")


def known_text_only(model: str | None) -> bool:
    """这个型号**确定**看不了图 → True；能看图或判不出 → False（判不出时不拦、照常发送）。"""
    name = str(model or "").strip().lower().rsplit("/", 1)[-1]
    if not name:
        return False
    if any(w in name for w in _VISION_WORDS) or re.search(r"glm-[0-9.]+v", name):
        return False
    return name == "kimi-k2" or name.startswith(_TEXT_ONLY_PREFIXES)


def text_only_models(models: list[str] | None) -> list[str]:
    """一组型号里确定看不了图的那些（给能力清单的 `no_vision`）。"""
    return [m for m in (models or []) if known_text_only(m)]
