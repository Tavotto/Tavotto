"""TAF 的 `convert_to_editable` 适配器（ADR 0143）：脚本出的图不是 Matplotlib（Pillow / OpenCV / plotly / ROOT 画布）时，
改图助手在**候选工作区**里另写一份 Matplotlib 版**新**脚本；原脚本一个字节不动，用户对照后才采用。

权限边界（逐条有看护用例，`tests/test_ai_convert.py`）：

* 候选工作区 `data_dir()/cache/ai_convert/<sid>/`：**只复制**原脚本（与原输出图的参考副本），不建链接；Agent 的 cwd 在这里，
  它能写的只有候选目录里的**一个新文件** `<原名>_matplotlib.py`。提交前检查：除这一个新文件外候选树逐字节不变（含原脚本副本）、
  原项目指纹不变、原脚本字节不变；
* Agent **从不**写项目目录：任务的 `commit` 只是把通过检查的候选“留存待采用”（候选目录 + `meta.json`，不碰用户文件）；
  用户在对照视图里点「采用」才由后端 `adopt()` 把候选文件**新建**进项目（`scriptlock.create_script`，
  O_EXCL 语义：已存在即失败，重名自动加序号 `_matplotlib_2.py`……，永远不覆盖）；
* 校验：在用户同一解释器 / 同一路由下真的跑一遍候选脚本（`run_validation`，由 app 注入的 `runner` 走现有 probe 路径），
  必须捕获到 Matplotlib 图，否则判失败并给一句原因。跑的是紧挨着原脚本的一个**临时文件**（相对路径的数据与 cwd 判据
  与原脚本一致），由后端用 O_EXCL 建、跑完即删（几秒内的存在期里文件监视器可能看见它，删除后登记不变）——Agent 没有写它的路径。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    ai_figure_edit,
    ai_prompt_pack,
    ai_tasks,
    atomicio,
    config,
    discover,
    projectenv,
    rasterhint,
    registry,
    scriptlock,
)
from .ai_tasks import FAILED, UNKNOWN, VERIFIED, CheckResult, TaskError

TASK_TYPE = "convert_to_editable"
CONVERT_DIR = config.data_dir() / "cache" / "ai_convert"
#: 候选里原输出图参考副本的子目录与大小上限（更大的不复制，提示词里也不提）
REFERENCE_DIR = "reference"
REFERENCE_MAX_BYTES = 8 * 1024 * 1024
_REFERENCE_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"})
#: 新版预览（SVG）的大小上限
PREVIEW_MAX_BYTES = 6 * 1024 * 1024
#: 待采用候选的保留时间；超龄的在下一次新建候选时顺手清掉
RETAIN_S = 24 * 3600
NAME_SUFFIX = "_matplotlib"
#: 校验运行用的临时文件名前缀。worker 按 `importlib.import_module(stem)` 加载脚本，stem 必须是合法模块名（不能以点开头）
STAGE_PREFIX = "tavotto_convert_probe_"
MAX_NUMBERED = 99
META_NAME = "meta.json"
PREVIEW_NAME = "preview.svg"

#: 跑通了但没捕获到 Matplotlib 图的错误码（`probe.ERROR_NO_FIGURE` 与准备路径的两个同义码）
NO_FIGURE_CODES = frozenset(
    {"script_no_figure", "no_figures_captured", "no_figures_captured_silent"}
)
#: 绊线拦下候选写项目文件的稳定码（`writetripwire.CODE` 的镜像）与写给 Agent 的一句
WRITE_OUTSIDE_ROOTS_CODE = "write_outside_roots"
WRITE_OUTSIDE_HINT = (
    "校验运行里脚本想写项目目录里的文件（例如 savefig('out.png') / to_csv），被拦下了；"
    "新脚本只许读原数据，不要保存任何文件，让 Tavotto 自己导出图。"
)
_SID = re.compile(r"[0-9a-f]{12}")
_STATES = ("ready", "adopted", "discarded")


def enabled() -> bool:
    """总开关（逃生口）：**默认开**。只有显式 `ai.convert.enabled == false` 或环境变量 `TAVOTTO_NO_CONVERT_TO_EDITABLE=1` 才关。"""
    if os.environ.get("TAVOTTO_NO_CONVERT_TO_EDITABLE") == "1":
        return False
    conv = config.ai_settings().get("convert")
    return not (isinstance(conv, dict) and conv.get("enabled") is False)


class ConvertError(Exception):
    """采用 / 丢弃 / 查询的稳定码失败。`code` 一经发布不能改名（前端按它换语言）。"""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def target_name_for(script_rel: str) -> str:
    return f"{Path(script_rel).stem}{NAME_SUFFIX}.py"


# --------------------------------------------------------------------------- 原图事实（对照用）
def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """PNG / JPEG / GIF 的像素宽高（只读头部，纯标准库）；认不出回 None。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                (seg,) = struct.unpack(">H", data[i + 2 : i + 4])
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return int(w), int(h)
                i += 2 + seg
    except (struct.error, IndexError):
        return None
    return None


# --------------------------------------------------------------------------- 候选工作区
@dataclass
class ConvertCandidate:
    """一次“生成可编辑版本”的候选。属性名与 `ai_figure_edit.Candidate` 里桥会读的那几项（`dir` / `committed` /
    `refresh` / `target_name` / `extras`）一致。"""

    sid: str
    dir: Path
    project_root: Path
    script_rel: str
    script_path: Path
    base_bytes: bytes
    base_sha256: str
    base_mode: int
    library: str
    original_rel: str | None
    reference_name: str | None
    tree_before: dict[str, str]
    project_before: dict
    new_bytes: bytes | None = None
    #: 对桥而言“没写用户文件”：恒 False（`_settle_task` / 刷新据此不当成脚本被改）
    committed: bool = False
    refresh: dict | None = None
    #: 通过检查、已留存待采用
    ready: bool = False
    comparison: dict | None = None
    preview_svg: str | None = None
    extras: dict = field(default_factory=dict)

    @property
    def target_name(self) -> str:
        return target_name_for(self.script_rel)

    @property
    def target(self) -> Path:
        return self.dir / self.target_name


def _check_sid(sid: str) -> str:
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        raise ConvertError("convert_not_found", 404)
    return sid


def _under_convert_dir(path: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).parent == Path(os.path.realpath(CONVERT_DIR))
    except OSError:
        return False


def remove_dir(cdir: Path) -> None:
    """删候选目录；只删 `CONVERT_DIR` 直接子目录（防一个算错的路径把别处删掉）。"""
    try:
        if _under_convert_dir(cdir):
            shutil.rmtree(Path(os.path.realpath(cdir)), ignore_errors=True)
    except OSError:
        pass


def prune_expired(now: float | None = None) -> int:
    """清掉超龄的候选目录（按目录 mtime）。采用后的目录不再需要，也一并清。"""
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(CONVERT_DIR.iterdir())
    except OSError:
        return 0
    for d in entries:
        try:
            if d.is_dir() and not d.is_symlink() and now - d.stat().st_mtime > RETAIN_S:
                remove_dir(d)
                removed += 1
        except OSError:
            continue
    return removed


def create_candidate(
    sid: str,
    script_path: Path,
    project_root: Path,
    script_rel: str,
    *,
    library: str,
    original_path: Path | None = None,
    original_rel: str | None = None,
) -> ConvertCandidate:
    """建候选目录并**复制**原脚本（与大小合适的原输出图，当参考）；拍原项目指纹基线。失败时不留半个目录。"""
    project_root = Path(project_root)
    prune_expired()
    cdir = CONVERT_DIR / _check_sid(sid)
    reference_name = None
    try:
        cdir.mkdir(parents=True, exist_ok=False)
        base = script_path.read_bytes()
        # 候选目录在 Tavotto 数据目录里（不是项目）：这份是给 Agent 看的原脚本副本，不是用户脚本
        reference_copy = cdir / Path(script_rel).name
        reference_copy.write_bytes(base)
        if (
            original_path is not None
            and original_path.is_file()
            and not original_path.is_symlink()
            and original_path.suffix.lower() in _REFERENCE_EXT
            and original_path.stat().st_size <= REFERENCE_MAX_BYTES
        ):
            (cdir / REFERENCE_DIR).mkdir()
            reference_name = original_path.name
            (cdir / REFERENCE_DIR / reference_name).write_bytes(original_path.read_bytes())
        cand = ConvertCandidate(
            sid=sid,
            dir=cdir,
            project_root=project_root,
            script_rel=script_rel,
            script_path=script_path,
            base_bytes=base,
            base_sha256=_sha256(base),
            base_mode=script_path.stat().st_mode & 0o7777,
            library=library,
            original_rel=original_rel,
            reference_name=reference_name,
            tree_before=ai_figure_edit._tree(cdir) or {},
            project_before=ai_figure_edit.project_fingerprint(
                project_root, exclude=_own_files(script_rel)
            ),
        )
    except BaseException:
        remove_dir(cdir)
        raise
    return cand


def _own_files(script_rel: str) -> frozenset[str]:
    return frozenset(
        {Path(script_rel).as_posix(), registry.REGISTRY_NAME, registry.LEGACY_REGISTRY_NAME}
    )


def release(cand: ConvertCandidate) -> None:
    """会话收尾：通过检查、留存待采用的候选保留；其余（失败 / 取消 / 超时）删掉。"""
    if not cand.ready:
        remove_dir(cand.dir)


def prompt_for(cand: ConvertCandidate) -> str:
    return ai_prompt_pack.convert_prompt(
        script=Path(cand.script_rel).name,
        target=cand.target_name,
        library=cand.library,
        reference=f"{REFERENCE_DIR}/{cand.reference_name}" if cand.reference_name else None,
        project_root=cand.project_root,
    )


# --------------------------------------------------------------------------- 校验
def _static_uses_matplotlib(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in rasterhint.MATPLOTLIB_FAMILY for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in rasterhint.MATPLOTLIB_FAMILY:
                return True
    return False


def run_validation(cand: ConvertCandidate, runner) -> dict:
    """在用户同一解释器 / 同一路由下跑候选脚本一遍。

    `runner(figures_dir: str, script_rel: str, write_roots: tuple[str, ...]) -> {"stems": [...], "size_mm": [w, h] | None, "svg": str | None,
    "error": None | {"code": str, "message": str}}`（app 注入，走现有 probe 路径并在返回前退役它起的 worker）。
    **隔离写入（Codex #1277 P1）**：运行带写入根绊线（ADR 0141 的 `write_roots`，现有机制）——根只有候选目录（加 worker 自己的
    基础设施目录），所以候选脚本把相对路径输出（`out.png` / `data.csv`）写进项目目录时，在**写发生的那一刻**失败
    （`write_outside_roots`），用户文件根本没被打开写过；跑完的指纹复核只是兜底（绊线挡不住 C 层写）。
    候选以紧挨原脚本的**临时文件**（`tavotto_convert_probe_<sid>.py`）形式存在（O_EXCL 新建、跑完即删）：相对路径的数据读取与 cwd 判据同原脚本。
    回 `{"status": "ok" | "no_figure" | "error", ...}`。"""
    stage_path = cand.script_path.parent / f"{STAGE_PREFIX}{cand.sid}.py"
    rel = stage_path.relative_to(cand.project_root).as_posix()
    try:
        with scriptlock.script_guard(stage_path):
            scriptlock.create_script(stage_path, cand.new_bytes or b"")
            try:
                out = runner(str(cand.project_root), rel, (str(cand.dir),)) or {}
            finally:
                try:
                    stage_path.unlink()
                except OSError:
                    pass
    except OSError as exc:
        code = "exists" if isinstance(exc, FileExistsError) else "write_failed"
        return {"status": "error", "code": f"stage_{code}", "message": str(exc)}
    err = out.get("error")
    if err:
        code = str(err.get("code") or "run_failed")
        if code in NO_FIGURE_CODES:
            return {"status": "no_figure", "code": code}
        if code == WRITE_OUTSIDE_ROOTS_CODE:
            return {"status": "error", "code": code, "message": WRITE_OUTSIDE_HINT}
        return {"status": "error", "code": code, "message": str(err.get("message") or "")[:400]}
    if not out.get("stems"):
        return {"status": "no_figure", "code": "no_figures_captured"}
    return {
        "status": "ok",
        "stems": list(out["stems"]),
        "size_mm": out.get("size_mm"),
        "svg": out.get("svg"),
    }


def comparison_of(cand: ConvertCandidate, validation: dict) -> dict:
    """给对照视图的版式数据：原图（尺寸、字节、摘要）与新版（图幅 mm、图个数）；两边的宽高比与差。
    **不做像素差**：原件是位图而新版是矢量重画，逐像素差没有可辩护的含义，给人并排看。"""
    original: dict | None = None
    if cand.original_rel:
        p = cand.project_root / cand.original_rel
        try:
            data = p.read_bytes()
            dims = image_dimensions(data)
            original = {
                "path": cand.original_rel,
                "bytes": len(data),
                "sha256": _sha256(data),
                "width_px": dims[0] if dims else None,
                "height_px": dims[1] if dims else None,
            }
        except OSError:
            original = None
    size_mm = validation.get("size_mm")
    new = {
        "figures": len(validation.get("stems") or []),
        "width_mm": float(size_mm[0]) if size_mm else None,
        "height_mm": float(size_mm[1]) if size_mm else None,
    }
    aspect_delta = None
    if original and original.get("width_px") and new["width_mm"] and new["height_mm"]:
        a = original["width_px"] / original["height_px"]
        b = new["width_mm"] / new["height_mm"]
        aspect_delta = round(abs(a - b) / a, 4)
    return {
        "original": original,
        "candidate": new,
        "aspect_delta": aspect_delta,
        "pixel_diff": None,
    }


# --------------------------------------------------------------------------- 适配器
def _check(cid: str, stage: str, role: str, verdict: str, reason: str | None = None, **observed):
    return CheckResult(
        id=cid,
        layer="L2" if cid.startswith("render.") else "L1",
        stage=stage,
        role=role,
        verdict=verdict,
        reason_code=reason,
        observed=observed,
        kind="persistence" if cid.startswith("project.") else "figure",
    )


class ConvertAdapter:
    """`convert_to_editable`：读原脚本，另写一份 Matplotlib 版新脚本（只在候选工作区里）。"""

    task_type = TASK_TYPE

    def validate(self, envelope) -> list[str]:
        problems = []
        if envelope.executor == ai_tasks.DETERMINISTIC:
            problems.append("executor_must_be_agent")
        if len(envelope.allowed_writes) != 1 or "/" in envelope.allowed_writes[0]:
            problems.append("exactly_one_new_file")
        return problems

    def plan(self, envelope):
        return ai_tasks.Plan(steps=("execute",))

    def verify(self, stage: str, run, cand: ConvertCandidate) -> list[CheckResult]:
        if stage == "precommit":
            return self._precommit(run, cand)
        if not cand.ready:
            return [_check("commit.staged", stage, "integrity", FAILED, "not_staged")]
        return [_check("commit.staged", stage, "integrity", VERIFIED)]

    def _precommit(self, run, cand: ConvertCandidate) -> list[CheckResult]:
        st = "precommit"
        out: list[CheckResult] = []
        tree = ai_figure_edit._tree(cand.dir)
        if tree is None:
            out.append(_check("candidate.tree", st, "integrity", UNKNOWN, "candidate_over_budget"))
        else:
            changed = sorted(
                k
                for k in cand.tree_before.keys() | tree.keys()
                if k != cand.target_name and cand.tree_before.get(k) != tree.get(k)
            )
            out.append(
                _check(
                    "candidate.tree", st, "integrity", FAILED, "other_file_changed", files=changed
                )
                if changed
                else _check("candidate.tree", st, "integrity", VERIFIED)
            )
        try:
            cand.new_bytes = cand.target.read_bytes() if not cand.target.is_symlink() else None
        except OSError:
            cand.new_bytes = None
        if not cand.new_bytes or not cand.new_bytes.strip():
            out.append(_check("candidate.target", st, "integrity", FAILED, "target_missing"))
            out += self._project_untouched(cand, st)
            return out
        out.append(_check("candidate.target", st, "integrity", VERIFIED))
        text = cand.new_bytes.decode("utf-8", errors="replace")
        tree_, problem = discover.parse_source(text)
        if problem is not None:
            out.append(
                _check(
                    "candidate.syntax",
                    st,
                    "integrity",
                    FAILED,
                    "syntax_error",
                    lineno=problem.get("lineno"),
                    detail=problem.get("detail"),
                )
            )
        else:
            out.append(_check("candidate.syntax", st, "integrity", VERIFIED))
            out.append(
                _check("candidate.matplotlib", st, "goal", VERIFIED)
                if _static_uses_matplotlib(tree_)
                else _check("candidate.matplotlib", st, "goal", FAILED, "no_matplotlib_import")
            )
        out += self._project_untouched(cand, st)
        if any(c.verdict != VERIFIED for c in out):
            return out  # 静态检查都没过就不浪费一次真运行
        out += self._render(run, cand, st)
        # 校验运行会在原脚本旁真跑候选：它写相对路径输出（out.png 之类）就会改到项目文件，所以跑完要再核一遍
        out += [c for c in self._project_untouched(cand, st) if c.verdict != VERIFIED]
        return out

    def _project_untouched(self, cand: ConvertCandidate, st: str) -> list[CheckResult]:
        out = []
        try:
            now = cand.script_path.read_bytes()
        except OSError:
            now = None
        out.append(
            _check("project.target", st, "integrity", VERIFIED)
            if now == cand.base_bytes
            else _check("project.target", st, "integrity", FAILED, "target_changed_in_project")
        )
        changed = ai_figure_edit.fingerprint_changes(
            cand.project_before,
            ai_figure_edit.project_fingerprint(
                cand.project_root, exclude=_own_files(cand.script_rel)
            ),
        )
        if changed is None:
            out.append(_check("project.untouched", st, "integrity", UNKNOWN, "project_over_budget"))
        elif changed:
            out.append(
                _check(
                    "project.untouched",
                    st,
                    "integrity",
                    FAILED,
                    "project_files_changed",
                    files=changed[:50],
                    total=len(changed),
                )
            )
        else:
            out.append(_check("project.untouched", st, "integrity", VERIFIED))
        return out

    def _render(self, run, cand: ConvertCandidate, st: str) -> list[CheckResult]:
        runner = cand.extras.get("runner")
        if runner is None:
            return [_check("render.capture", st, "goal", UNKNOWN, "render_not_wired")]
        try:
            run.charge("renders")
        except ai_tasks.BudgetExceeded:
            return [_check("render.capture", st, "goal", UNKNOWN, "render_budget")]
        result = run_validation(cand, runner)
        cand.extras["validation"] = result
        if result["status"] == "ok":
            cand.comparison = comparison_of(cand, result)
            svg = result.get("svg")
            cand.preview_svg = (
                svg if isinstance(svg, str) and len(svg) <= PREVIEW_MAX_BYTES else None
            )
            return [
                _check("render.capture", st, "goal", VERIFIED, stems=result["stems"]),
            ]
        if result["status"] == "no_figure":
            return [_check("render.capture", st, "goal", FAILED, "no_matplotlib_figure")]
        return [
            _check(
                "render.capture",
                st,
                "goal",
                FAILED,
                "run_failed",
                code=result.get("code"),
                detail=result.get("message") or "",
            )
        ]

    def correction(self, run, failed: list[CheckResult]) -> dict | None:
        lines = []
        target = run.envelope.allowed_writes[0] if run.envelope.allowed_writes else "新文件"
        for c in failed:
            text = convert_fix_text((c.id, c.reason_code), {**c.observed, "target": target})
            if text is None:
                return None
            lines.append(text)
        if not lines:
            return None
        return {"prompt": ai_prompt_pack.correction_prompt(ai_prompt_pack.DEFAULT, lines)}

    def explain(self, outcome: str, unmet: list[dict]) -> dict:
        return {
            "message_key": f"task.{TASK_TYPE}.{outcome}",
            "params": {"unmet": [u["constraint_id"] for u in unmet]},
        }


def convert_fix_text(key: tuple[str, str | None], observed: dict) -> str | None:
    """可修正失败 → 写给 Agent 的一句。语法 / 目标缺失 / 副本被动与 `figure_edit` 同一套文字；本任务特有的在 Prompt Pack 的
    convert 一节。不在闭集里回 None（= 不可修）。"""
    own = ai_prompt_pack.convert_fix_text(key, observed)
    if own is not None:
        return own
    if key == ("candidate.syntax", "syntax_error"):
        return ai_prompt_pack.fix_text(ai_prompt_pack.DEFAULT, key, observed)  # type: ignore[arg-type]
    return None


# --------------------------------------------------------------------------- 执行器（AI 桥的那一次 CLI 运行）
class ConvertExecutor(ai_figure_edit.BridgeExecutor):
    """`commit` = 把通过检查的候选留存待采用（候选目录 + meta.json）；**不写任何用户文件**。"""

    writes_user_files = False

    def commit(self, run, cand: ConvertCandidate) -> None:
        stage(cand)

    def discard(self, run, cand: ConvertCandidate) -> None:
        remove_dir(cand.dir)


def stage(cand: ConvertCandidate) -> None:
    if cand.new_bytes is None:
        raise TaskError("candidate_missing", cand.target_name)
    meta = {
        "sid": cand.sid,
        "state": "ready",
        "project": str(cand.project_root.resolve()),
        "script_rel": cand.script_rel,
        "base_sha256": cand.base_sha256,
        "base_mode": cand.base_mode,
        "candidate_sha256": _sha256(cand.new_bytes),
        "target_name": cand.target_name,
        "library": cand.library,
        "original_rel": cand.original_rel,
        "comparison": cand.comparison,
        "has_preview": bool(cand.preview_svg),
        "created": time.time(),
    }
    try:
        if cand.preview_svg:
            (cand.dir / PREVIEW_NAME).write_text(cand.preview_svg, encoding="utf-8")
        atomicio.write_bytes(cand.dir / META_NAME, atomicio.dumps_json(meta))
    except OSError as exc:
        raise TaskError("stage_failed", type(exc).__name__) from exc
    cand.ready = True


def envelope_for(
    *, agent: str, project_key: str, cand: ConvertCandidate, prompt: str, rid: str | None
) -> ai_tasks.TaskEnvelope:
    refs = [{"kind": "script", "path_rel": cand.script_rel, "sha256": cand.base_sha256}]
    if cand.original_rel:
        refs.append({"kind": "figure", "path_rel": cand.original_rel})
    return ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": ai_tasks.SCHEMA_VERSION,
            "task_type": TASK_TYPE,
            "project_ref": {"project_key": project_key},
            "client_request_id": rid,
            "user_intent": prompt,
            "input_refs": refs,
            "output_contract": {"allowed_writes": [cand.target_name], "new_figures": True},
            "execution_policy": {"executor": agent},
            # Agent ≤ 1 + 修正 ≤ 1；每轮提交前各跑一次校验
            "budget": {"max_agent_runs": 2, "max_corrections": 1, "max_renders": 3},
        }
    )


ADAPTER = ConvertAdapter()


def ensure_registered() -> None:
    if ai_tasks.ADAPTERS.get(TASK_TYPE) is not ADAPTER:
        ai_tasks.register(ADAPTER)


# --------------------------------------------------------------------------- 状态 / 采用 / 丢弃
def _load_meta(sid: str, project: str) -> tuple[Path, dict]:
    cdir = CONVERT_DIR / _check_sid(sid)
    if not cdir.is_dir() or cdir.is_symlink() or not _under_convert_dir(cdir):
        raise ConvertError("convert_not_found", 404)
    try:
        meta = json.loads((cdir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConvertError("convert_not_ready", 409) from None
    if not isinstance(meta, dict) or meta.get("sid") != sid or meta.get("project") != project:
        raise ConvertError("convert_not_found", 404)
    return cdir, meta


def status(sid: str, project: str) -> dict:
    """待采用候选的对照数据（不含脚本全文）。"""
    cdir, meta = _load_meta(sid, project)
    return {
        "session": sid,
        "state": meta.get("state"),
        "script": meta.get("script_rel"),
        "target_name": meta.get("target_name"),
        "library": meta.get("library"),
        "comparison": meta.get("comparison"),
        "has_preview": bool(meta.get("has_preview")) and (cdir / PREVIEW_NAME).is_file(),
        "adopted_script": meta.get("adopted_script"),
    }


def preview_svg(sid: str, project: str) -> bytes:
    cdir, meta = _load_meta(sid, project)
    p = cdir / PREVIEW_NAME
    if not meta.get("has_preview") or not p.is_file() or p.is_symlink():
        raise ConvertError("convert_no_preview", 404)
    return p.read_bytes()


def _write_meta(cdir: Path, meta: dict) -> None:
    atomicio.write_bytes(cdir / META_NAME, atomicio.dumps_json(meta))


def adopt(sid: str, project: str) -> dict:
    """把待采用候选**新建**进项目（原脚本同目录；O_EXCL，不覆盖，重名加序号）。回 `{script, name}`。

    重入：已采用过的直接回上次的结果（不再写第二个文件）。原脚本此刻的字节必须仍是起跑基线、候选字节必须仍是留存时的
    摘要——对照时看到的就是写进去的。"""
    cdir, meta = _load_meta(sid, project)
    if meta.get("state") == "adopted" and meta.get("adopted_script"):
        return {"script": meta["adopted_script"], "name": Path(meta["adopted_script"]).name}
    if meta.get("state") != "ready":
        raise ConvertError("convert_not_ready", 409)
    root = Path(project)
    script_rel = str(meta["script_rel"])
    script_path = root / script_rel
    # 锁序固定：先原脚本、再目标兄弟脚本（只有本函数同时持有这两把）。「核原脚本字节仍是起跑基线 → 新建兄弟」必须在
    # 原脚本的锁里做完：否则别处 Tavotto 编辑在两步之间改了原脚本，基于旧源生成的候选照样被采用。
    with scriptlock.script_guard(script_path):
        return _adopt_locked(cdir, meta, root, script_rel, script_path)


def _adopt_locked(cdir: Path, meta: dict, root: Path, script_rel: str, script_path: Path) -> dict:
    """`adopt` 的锁内部分：调用方已持有原脚本的 `script_guard`。"""
    try:
        if _sha256(script_path.read_bytes()) != meta["base_sha256"]:
            raise ConvertError("convert_source_changed", 409)
        data = (cdir / meta["target_name"]).read_bytes()
    except OSError:
        raise ConvertError("convert_source_changed", 409) from None
    if _sha256(data) != meta["candidate_sha256"]:
        raise ConvertError("convert_candidate_changed", 409)
    parent_rel = Path(script_rel).parent
    stem = Path(meta["target_name"]).stem
    for n in range(1, MAX_NUMBERED + 1):
        name = f"{stem}.py" if n == 1 else f"{stem}_{n}.py"
        rel = (parent_rel / name).as_posix()
        dest = projectenv.contained_path(root, rel)
        if dest is None:
            raise ConvertError("convert_outside_project", 409)
        try:
            with scriptlock.script_guard(dest):
                scriptlock.create_script(Path(dest), data)
        except FileExistsError:
            continue
        except OSError as exc:
            raise ConvertError("convert_write_failed", 500) from exc
        _inherit_mode(Path(dest), meta.get("base_mode"))
        meta["state"] = "adopted"
        meta["adopted_script"] = rel
        try:
            _write_meta(cdir, meta)
        except OSError:
            pass  # 文件已建成；记不下来只影响重入判断
        return {"script": rel, "name": name}
    raise ConvertError("convert_name_exhausted", 409)


def _inherit_mode(dest: Path, base_mode) -> None:
    """尽力而为：新脚本沿用原脚本的权限位（仅 POSIX、`base_mode` 有效时）；失败吞掉，不影响采用。"""
    if (
        os.name != "posix"
        or not isinstance(base_mode, int)
        or isinstance(base_mode, bool)
        or base_mode <= 0
    ):
        return
    try:
        os.chmod(dest, base_mode & 0o777)
    except OSError:
        pass


def discard(sid: str, project: str) -> dict:
    """放弃待采用候选：删候选目录（项目里什么都没写过）。已不存在也算成功。"""
    try:
        cdir, _meta = _load_meta(sid, project)
    except ConvertError as exc:
        if exc.code == "convert_not_found":
            return {"ok": True}
        raise
    remove_dir(cdir)
    return {"ok": True}
