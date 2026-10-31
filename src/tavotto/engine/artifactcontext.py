"""Parent-side, zero-edit admission of a selected disk artifact. Never writes originals."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import tempfile
import threading
import zlib
from pathlib import Path

from tavotto import pdfbackend

from . import atomicio, config, figcapture, originalspec
from .figcapture import ArtifactContextError, artifact_request as artifact_request

# Two RGBA buffers + scratch: 1/4 of RenderCore's 64M cap; PDF keeps 60s/2GiB child bounds.
MAX_BYTES = figcapture.ARTIFACT_VALIDATION_MAX_BYTES
MAX_PIXELS = figcapture.ARTIFACT_VALIDATION_MAX_PIXELS
PDF_DPI = 300


#: 应急开关（`config.experiments.script_backed_figure`）：10-21 起**默认开**（Win11 真机 + macOS WebKit/Chromium 验过，
#: 用户裁决）；只有显式写 `false` 才关，关着时来源驱动策略一律 409 `policy_disabled`。
EXPERIMENT = "script_backed_figure"
#: 来源驱动采用的准入结论（ADR 0139 §二）。`needs_confirmation` 永不升级为 `exact`。
STATUS_EXACT = "exact"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"


def script_backed_enabled() -> bool:
    """默认开；只有布尔 `False` 关（字符串 "false"、0 不算——开关值要么是布尔，要么当没写）。"""
    experiments = config.load().get("experiments")
    return not (isinstance(experiments, dict) and experiments.get(EXPERIMENT) is False)


def _require(ok, reason, message):
    if not ok:
        raise ArtifactContextError(reason, message)


def create_context(path: Path, source_id: str) -> dict:
    """Called only after the controller resolves/authorizes this exact file ID."""
    path = Path(path)
    try:
        _require(path.is_file(), "source_unreadable", "Source unavailable.")
        _require(
            path.suffix.lower() in (".png", ".pdf"), "source_unreadable", "Unsupported format."
        )
        _require(
            path.stat().st_size <= MAX_BYTES, "validation_budget_exceeded", "Source byte limit."
        )
        sha, size = figcapture.hash_file(path, max_bytes=MAX_BYTES)
        return figcapture.SourceArtifact(
            source_id, figcapture.ORIGIN_STATIC, path.suffix[1:].lower(), sha, size
        ).to_payload()
    except figcapture.FileHashBudgetExceeded as exc:
        raise ArtifactContextError("validation_budget_exceeded", "Source byte limit.") from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, ArtifactContextError):
            raise
        raise ArtifactContextError("source_unreadable", "Cannot read source.") from exc


def assert_current(path: Path, context: dict) -> None:
    """Also usable as the final publication gate after a cached render/export."""
    current = create_context(path, context["source_id"])
    _require(
        current == {k: v for k, v in context.items() if k != "render_policy"},
        "source_changed",
        "The selected source changed; reopen it.",
    )


def _snapshot(path, out):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    _require(len(data) <= MAX_BYTES, "validation_budget_exceeded", "Input byte limit.")
    out.write_bytes(data)
    return data


def _png_density(data):
    # Pillow can accept a missing IEND or damaged ancillary CRC. Admission requires
    # a complete PNG; 16-bit normalization and animated PNG would lose source pixels.
    _require(data.startswith(b"\x89PNG\r\n\x1a\n"), "source_unreadable", "Invalid PNG signature.")
    pos, ended, ppm = 8, False, None
    while pos + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        end = pos + 12 + size
        _require(end <= len(data), "source_unreadable", "Truncated PNG chunk.")
        crc = struct.unpack_from(">I", data, end - 4)[0]
        _require(
            zlib.crc32(data[pos + 4 : end - 4]) == crc, "source_unreadable", "Invalid PNG CRC."
        )
        _require(kind != b"acTL", "source_unreadable", "Animated PNG is unsupported.")
        # Raw RGBA equality cannot establish equality of color-managed appearance.
        _require(
            kind not in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"),
            "source_unreadable",
            "PNG color metadata is unsupported.",
        )
        if kind == b"IHDR":
            _require(
                size == 13 and data[pos + 16] <= 8, "source_unreadable", "Unsupported PNG depth."
            )
        if kind == b"pHYs" and size == 9:
            x, y, unit = struct.unpack_from(">IIB", data, pos + 8)
            ppm = (x, y) if unit == 1 and x > 0 and y > 0 else None
        pos = end
        if kind == b"IEND":
            ended = size == 0 and pos == len(data)
            break
    _require(ended, "source_unreadable", "Incomplete PNG.")
    return originalspec._png_dpi(data), ppm


def _frame(path, kind, data):
    probe = pdfbackend.probe_asset(path, "pdf" if kind == "pdf" else "raster", include_pages=True)
    if kind == "pdf":
        _require(data.rstrip().endswith(b"%%EOF"), "source_unreadable", "Incomplete PDF.")
        _require(probe["pages"] == 1, "validation_budget_exceeded", "Single-page PDFs only.")
        size = [probe["w_pt"], probe["h_pt"]]
        _require(
            all(math.isfinite(v) and v > 0 for v in size), "source_unreadable", "Invalid PDF frame."
        )
        width = max(1, round(size[0] * PDF_DPI / 72))
        pixels = width * max(1, round(size[1] * width / size[0]))
        frame = {"size_pt": size, "validation_width_px": width}
    else:
        size = [probe["px_w"], probe["px_h"]]
        dpi, ppm = _png_density(data)
        frame = {"size_px": size, "dpi": dpi, "ppm": ppm}
        pixels = size[0] * size[1]
    _require(0 < pixels <= MAX_PIXELS, "validation_budget_exceeded", "Decoded pixel limit.")
    return frame


def _same_frame(a, b, kind):
    if kind == "pdf":
        return a["size_pt"] == b["size_pt"]
    if a["size_px"] != b["size_px"] or (a["ppm"] is None) != (b["ppm"] is None):
        return False
    # pHYs stores integer pixels/metre: at most one quantization step, never a
    # broad mm threshold. Effective DPI facts still come from originalspec.
    return a["ppm"] is None or all(abs(x - y) <= 1 for x, y in zip(a["ppm"], b["ppm"]))


def validate_candidate(source_path: Path, candidate_path: Path, context: dict) -> dict:
    """Compare a private zero-edit save in the source format, before applying edits."""
    return _compare_candidate(source_path, candidate_path, context, adopt=False)


def admit_reconstruction(source_path: Path, candidate_path: Path, context: dict) -> dict:
    """来源驱动采用（ADR 0139）的准入：身份 / 格式 / 图幅检查与精确策略**逐项相同**，
    只有「像素逐点相等」这一步改为产出结论——零差 `exact`，非零 `needs_confirmation` 并带差异度量。
    **没有任何自动接受的容差**；结论只看逐字节相等，度量只用来帮用户看。"""
    return _compare_candidate(source_path, candidate_path, context, adopt=True)


def _compare_candidate(source_path, candidate_path, context, *, adopt):
    assert_current(source_path, context)
    kind = context["kind"]
    same_kind = Path(candidate_path).suffix.lower() == "." + kind
    _require(same_kind, "source_mismatch", "Format differs.")
    root = config.data_dir() / "artifact-validation"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="check-", dir=root) as directory:
            a, b = (Path(directory) / f"{name}.{kind}" for name in ("source", "candidate"))
            data_a, data_b = _snapshot(source_path, a), _snapshot(candidate_path, b)
            _require(
                figcapture.hash_file(a) == (context["bytes_sha256"], context["size_bytes"]),
                "source_changed",
                "Source changed during validation.",
            )
            fa, fb = _frame(a, kind, data_a), _frame(b, kind, data_b)
            _require(_same_frame(fa, fb, kind), "source_mismatch", "Source frame differs.")
            if adopt:
                _require(kind == "png", "unsupported_render_state", "PNG sources only.")
                return _adoption_verdict(a, b, kind, fa, data_b)
            if kind == "pdf":
                pa, pb = a.with_suffix(".png"), b.with_suffix(".png")
                pdfbackend.render_preview_png(a, fa["validation_width_px"], pa)
                pdfbackend.render_preview_png(b, fa["validation_width_px"], pb)
                a, b = pa, pb
            metrics = pdfbackend.compare_png(a, b)
            _require(
                metrics.get("ok") and metrics.get("max_abs_diff") == 0,
                "source_mismatch",
                "Zero-edit source pixels differ.",
            )
            return dict(
                format=kind, frame=fa, pixels_equal=True, total_pixels=metrics["total_pixels"]
            )
    except Exception as exc:
        if isinstance(exc, ArtifactContextError):
            raise
        budget = getattr(exc, "code", "") in (
            "pixel_budget_exceeded",
            "raster_too_large",
            "render_child_timeout",
        ) or isinstance(exc, (MemoryError, TimeoutError))
        reason = "validation_budget_exceeded" if budget else "source_unreadable"
        raise ArtifactContextError(reason, "Unable to validate the selected source.") from exc
    finally:
        assert_current(source_path, context)


def _adoption_verdict(source, candidate, kind, frame, candidate_bytes):
    """逐字节比较的结论 + 差异度量；`reconstruction_sha256` 是零编辑候选的字节摘要（确认绑定的就是它）。"""
    extent = pdfbackend.compare_png_extent(source, candidate)
    # 图幅已逐项核对过；这里仍不一致只能是解码层面的矛盾，一律拒绝，绝不降成 needs_confirmation。
    _require(extent.get("ok"), "source_mismatch", "Source frame differs.")
    equal = extent["differing_pixels"] == 0
    return dict(
        format=kind,
        frame=frame,
        pixels_equal=equal,
        total_pixels=extent["total_pixels"],
        status=STATUS_EXACT if equal else STATUS_NEEDS_CONFIRMATION,
        reconstruction_sha256=hashlib.sha256(candidate_bytes).hexdigest(),
        metrics=dict(
            differing_pixels=extent["differing_pixels"],
            differing_ratio=extent["differing_ratio"],
            total_pixels=extent["total_pixels"],
            bbox=extent["bbox"],
        ),
    )


# ---------------------------------------------------------------------------
# 服务端写回闸（ADR 0139 §五.3）：采用成功时记一条「项目 × 源文件 × 字节身份」，写回类入口命中即拒，
# 不依赖请求自带的 policy。记录在 data_dir()，原子写；磁盘原件字节变了（hash 不同）记录不再命中。
# ---------------------------------------------------------------------------
LOG = logging.getLogger(__name__)
ADOPTION_MAX_ENTRIES = 4096
_ADOPTION_LOCK = threading.RLock()
#: 写回类事务（整段）与采用记录（身份复核 + 落记录）共用的锁：先过闸的写回不会被随后的采用抢跑，
#: 先记的采用会让随后的写回在闸上被拒。可重入。
ADOPTION_GATE = threading.RLock()


def _adoption_path() -> Path:
    return config.data_dir() / "source-backed" / "adoptions.json"


class AdoptionStoreUnknown(Exception):
    """采用记录文件存在，但读不了 / 结构不对：状态未知（不是「没有采用」）。"""


def _adoption_entries() -> list:
    """文件不存在 = 没有采用（`[]`）；存在但读不了 / JSON 畸形 / version 不认识 / entries 不是 list
    = 状态未知，抛 `AdoptionStoreUnknown`。个别畸形条目照旧丢弃。"""
    path = _adoption_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise AdoptionStoreUnknown(str(exc)) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AdoptionStoreUnknown(str(exc)) from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(entries, list):
        raise AdoptionStoreUnknown("unrecognized structure")
    return [
        e
        for e in entries
        if isinstance(e, dict)
        and all(isinstance(e.get(k), str) for k in ("project", "source_id", "bytes_sha256"))
    ]


def _store_unknown_error(exc) -> ArtifactContextError:
    LOG.error(
        "来源驱动采用记录损坏或读不了（%s）：%s。写回类入口对 PNG 一律拒绝、新的采用被拒；"
        "确认没有人在用后删除该文件即可恢复（它只是个安全闸，删后已采用的原件需重新采用才会再受保护）。",
        _adoption_path(),
        exc,
    )
    return ArtifactContextError(
        "source_unreadable",
        "The source-backed adoption record in the Tavotto data folder is damaged; see the log.",
        retryable=False,
    )


def adoption_store_exists() -> bool:
    """记录文件在不在（存在但坏的也算在：交给 `is_adopted` 按未知处理）。不读内容，供热路径早退。"""
    return _adoption_path().exists()


def _project_key(project_root) -> str:
    # 与项目系统（app._project_id / pool._norm_dir）同一个规范化身份：大小写不敏感的卷上换个大小写重开
    # 同一项目，不能因此绕过写回闸。
    return config.normalize_path_identity(str(Path(project_root).resolve()))


def record_adoption(project_root, context: dict) -> None:
    """采用成功（exact 或 needs_confirmation 都算）时调用；幂等。写不进去就整个采用失败（fail closed）。"""
    entry = {
        "project": _project_key(project_root),
        "source_id": context["source_id"],
        "bytes_sha256": context["bytes_sha256"],
    }
    with _ADOPTION_LOCK:
        try:
            entries = _adoption_entries()
        except AdoptionStoreUnknown as exc:
            raise _store_unknown_error(exc) from exc  # 绝不覆盖损坏文件
        if entry in entries:
            return
        if len(entries) >= ADOPTION_MAX_ENTRIES:
            # 先清掉已经不会命中的（文件没了 / 字节变了），仍满就拒绝——绝不挤掉一条仍有效的记录。
            entries = [e for e in entries if _adoption_live(e)]
            _require(
                len(entries) < ADOPTION_MAX_ENTRIES,
                "adoption_store_full",
                "Too many source-backed adoptions are recorded.",
            )
        try:
            atomicio.write_json(_adoption_path(), {"version": 1, "entries": [*entries, entry]})
        except (OSError, ValueError, atomicio.AtomicWriteError) as exc:
            raise ArtifactContextError(
                "source_unreadable", "Cannot record the source-backed adoption."
            ) from exc


def _adoption_live(entry) -> bool:
    path = Path(entry["project"]) / entry["source_id"]
    try:
        return figcapture.hash_file(path, max_bytes=MAX_BYTES)[0] == entry["bytes_sha256"]
    except (OSError, ValueError):
        return False


def is_recorded(project_root, context: dict) -> bool:
    """这条（项目 × source_id × 字节身份）已经记过。记录读不了按「没记」（交给 record_adoption 去拒）。"""
    try:
        entries = _adoption_entries()
    except AdoptionStoreUnknown:
        return False
    key = _project_key(project_root)
    return any(
        e["project"] == key
        and e["source_id"] == context["source_id"]
        and e["bytes_sha256"] == context["bytes_sha256"]
        for e in entries
    )


def is_adopted(project_root, path: Path) -> bool:
    """这个项目里的这个文件，此刻的字节是否已被来源驱动策略采用过。读不了文件按「已采用」（拒绝更安全）。"""
    project = _project_key(project_root)
    try:
        entries = _adoption_entries()
    except AdoptionStoreUnknown as exc:
        _store_unknown_error(exc)  # 只记日志；PNG 一律当作已采用（只有 PNG 会被采用）
        return Path(path).suffix.lower() == ".png"
    mine = [e for e in entries if e["project"] == project]
    if not mine:
        return False
    path = Path(path)
    same = []
    for entry in mine:
        candidate = Path(project) / entry["source_id"]
        try:
            if candidate == path or candidate.samefile(path):
                same.append(entry)
        except OSError:
            continue
    if not same:
        return False
    try:
        digest = figcapture.hash_file(path, max_bytes=MAX_BYTES)[0]
    except (OSError, ValueError):
        return True
    return any(e["bytes_sha256"] == digest for e in same)
