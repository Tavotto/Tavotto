"""改图助手的参考图（ADR 0175）：用户在输入框里附的图片，只给用户自己连的那个 AI 看。

流程：前端先把图（已缩到 ≤ 4 MiB）逐张 `POST /api/ai/ref-images` 暂存到 `data_dir()/cache/ai_refs_staged/`，拿回不透明 id；
发送时 `POST /api/ai/run` 只带 id。`ai_bridge.run` 起会话时 `materialize` 把暂存的图**移**进这次任务的工作目录：

* 候选工作区（TAF / 内置执行器）：`<候选目录>/_reference/`——与候选同生同灭，候选清理时一起删；提交前检查（`ai_figure_edit._tree`）
  把这个目录整个排除在「候选树」之外，所以它既不会被当成「改了别的文件」，也绝不会被当成要写回的东西；
* 旧路径（外部 CLI 原地改）：`data_dir()/cache/ai_refs/<sid>/`——**绝不写进用户项目目录**；会话收尾时删。

不附图时本模块一行都不执行，请求、提示词、工作区与原来逐字节相同。图片不进遥测、不进诊断包（诊断包只取白名单里的日志与报告，
不读 `cache/` 下的这两个目录；`tests/test_ai_refimages.py` 钉住）。纯标准库。
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

#: 一次最多附几张（前端同一个数；超出的后端也只取前 MAX_IMAGES 张）
MAX_IMAGES = 4
#: 单张字节上限 = 内置执行器 `read_image` 的上限（`agent_runner.media.MAX_IMAGE_BYTES`），同一个数
MAX_BYTES = media.MAX_IMAGE_BYTES
#: 单张像素上限（宽 × 高）：4000 万像素约等于 8000 × 5000；再大的图模型端也会自行缩小，没有意义
MAX_PIXELS = 40_000_000
#: 候选工作区 / 旧路径目录里的子目录名；提交前检查按这个名字把它排除在候选树之外
REFERENCE_DIRNAME = "_reference"
#: 暂存的图最多留这么久（秒）：用户附了图却没发送也不会一直留在磁盘上
STAGED_TTL_S = 3600
#: 旧路径会话目录的兜底寿命（秒）：正常在会话收尾时删，进程被杀留下的按它清
SESSION_TTL_S = 24 * 3600

_ID = re.compile(r"^[0-9a-f]{32}$")
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def staged_dir() -> Path:
    return config.data_dir() / "cache" / "ai_refs_staged"


def session_root() -> Path:
    return config.data_dir() / "cache" / "ai_refs"


class RefImageError(ValueError):
    """暂存被拒（稳定码，前端据此换一句人话；前端本来就会先缩小，走到这里是兜底）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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


def stage(raw: bytes) -> dict:
    """暂存一张图 → `{id, mime, bytes, width, height}`。只认 PNG / JPEG / GIF / WebP（按魔数，不信扩展名与 Content-Type）。"""
    if not raw:
        raise RefImageError("ref_image_empty")
    if len(raw) > MAX_BYTES:
        raise RefImageError("ref_image_too_large")
    mime = _sniff(raw)
    if mime is None:
        raise RefImageError("ref_image_format")
    dims = image_dimensions(raw)
    if dims and dims[0] * dims[1] > MAX_PIXELS:
        raise RefImageError("ref_image_too_large")
    root = staged_dir()
    root.mkdir(parents=True, exist_ok=True)
    _prune(root, STAGED_TTL_S)
    rid = uuid.uuid4().hex
    (root / f"{rid}.{_EXT[mime]}").write_bytes(raw)
    return {
        "id": rid,
        "mime": mime,
        "bytes": len(raw),
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
    }


def discard(rid: str) -> bool:
    """用户在输入框里点 × 去掉一张：把暂存的那份删掉。不存在 = 已经没了，不算错。"""
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        return False
    gone = False
    for p in staged_dir().glob(f"{rid}.*"):
        try:
            p.unlink()
            gone = True
        except OSError:
            pass
    return gone


def _find(rid: str) -> Path | None:
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        return None
    root = staged_dir()
    for ext in _EXT.values():
        p = root / f"{rid}.{ext}"
        try:
            if p.is_file() and not p.is_symlink():
                return p
        except OSError:
            continue
    return None


def valid_ids(ids: object) -> list[str]:
    """请求里的 `ref_images`：只留格式合法、去重、至多 `MAX_IMAGES` 个；不是列表就当没有（不报错）。"""
    if not isinstance(ids, list):
        return []
    out: list[str] = []
    for x in ids:
        if isinstance(x, str) and _ID.fullmatch(x) and x not in out:
            out.append(x)
        if len(out) >= MAX_IMAGES:
            break
    return out


def materialize(ids: list[str], dest: Path) -> list[str]:
    """把暂存的图移进 `dest`，文件名 `ref-1.png` …；→ 相对 `dest` 的文件名列表（按给定顺序）。
    暂存里找不到的 id（过期 / 已被删）静默跳过：用户的任务照常发，只是少一张图。"""
    names: list[str] = []
    for rid in valid_ids(ids):
        src = _find(rid)
        if src is None:
            continue
        try:
            raw = src.read_bytes()
            if len(raw) > MAX_BYTES or _sniff(raw) is None:
                src.unlink(missing_ok=True)
                continue
            dest.mkdir(parents=True, exist_ok=True)
            name = f"ref-{len(names) + 1}.{src.suffix.lstrip('.')}"
            (dest / name).write_bytes(raw)
            src.unlink(missing_ok=True)
            names.append(name)
        except OSError:
            continue
    return names


def session_dir(sid: str) -> Path:
    """旧路径（外部 CLI 在原项目里改）这次任务的参考图目录：在数据目录里，不在项目里。"""
    return session_root() / sid


def remove_session(sid: str) -> None:
    """会话收尾 / 被拒时删旧路径的参考图目录；只删 `session_root()` 之内的一层。"""
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
