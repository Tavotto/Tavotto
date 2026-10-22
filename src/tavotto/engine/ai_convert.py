"""TAF 的 `convert_to_editable` 适配器（ADR 0143）：脚本出的图不是 Matplotlib（Pillow / OpenCV / plotly / ROOT 画布）时，
改图助手在**候选工作区**里另写一份 Matplotlib 版**新**脚本；原脚本一个字节不动，用户对照后才采用。

权限边界（逐条有看护用例，`tests/test_ai_convert.py`）：

* 候选工作区 `data_dir()/cache/ai_convert/<sid>/`：**只复制**原脚本（与原输出图的参考副本），不建链接；Agent 的 cwd 在这里，
  它能写的只有候选目录里的**一个新文件** `<原名>_matplotlib.py`。提交前检查：除这一个新文件外候选树逐字节不变（含原脚本副本）、
  原项目指纹不变、原脚本字节不变；
* Agent **从不**写项目目录：任务的 `commit` 只是把通过检查的候选“留存待采用”（候选目录 + `meta.json`，不碰用户文件）；
  用户在对照视图里点「采用」才由后端 `adopt()` 把候选文件**新建**进项目（`scriptlock.create_script`，
  O_EXCL 语义：已存在即失败，重名自动加序号 `_matplotlib_2.py`……，永远不覆盖）；
* 来源语言（ADR 0143 修订，2026-10-23）：原脚本可以是 `.py`（Python，位图库出的图）或 `.R` / `.r`（R）、`.m`（MATLAB）纯文本脚本。
  R / MATLAB **从不被运行**：对照只用脚本旁已有的原图，没有原图就如实标“未对照验证”（`verification: "unknown"`）；
  Python 里没有逐一对应的统计方法时 Agent 停下说明（结局 `needs_input`，不生成新脚本）；新脚本要的包缺失时按
  `convertdeps` 的规则自动装 / 停住等用户在依赖卡片上决定（候选不删，点主按钮后 `run_resume` 接着走同一个候选，不重跑 Agent）；
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
import threading
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
    sourcelang,
)
from .ai_tasks import FAILED, UNKNOWN, VERIFIED, CheckResult, NeedsInput, TaskError

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

#: Agent 判定“Python 里没有可靠对应”时的固定标记句式：首行 `TAVOTTO_NEEDS_INPUT: <原因>`，并且不写新文件
NEEDS_INPUT_MARKER = "TAVOTTO_NEEDS_INPUT:"
NEEDS_INPUT_REASON_MAX = 200
_NEEDS_INPUT_RE = re.compile(r"^[ \t>*`_-]*TAVOTTO_NEEDS_INPUT:[ \t]*(.*)$", re.M)
#: 对照数据里 `verification` 的取值：没有原图 = `unknown`（Tavotto 没运行 R / MATLAB，也没有原图可比）；有原图 = 并排给人看
VERIFICATION_UNKNOWN = "unknown"
VERIFICATION_SIDE_BY_SIDE = "side_by_side"

#: 校验运行因缺 Python 包失败的错误码（`probe.ERROR_MISSING_DEPENDENCY`）：不可修正（让 Agent 改代码解决不了缺包），走缺依赖卡
MISSING_DEPENDENCY_CODE = "missing_dependency"
#: 自动安装失败的稳定码（结果里 `render.capture` 的 `code`；`errors` 文案按它换语言）
DEPENDENCY_INSTALL_FAILED = "convert_dependency_install_failed"
#: 依赖钩子结果的 `status`（与 `convertdeps.STATUS_*` 同值；这里不 import 它：convertdeps 经 deprepair 通向既有 import 环，引擎侧只认 app 注入的钩子）
DEPS_INSTALLED = "installed"
DEPS_CANCELLED = "cancelled"
DEPS_INSTALLED_STOPPED = (
    "installed_stopped"  # 内部：装好后发现取消已置位（晚到的停止；首跑与续跑同一语义）
)
DEPS_HOLD = "hold"
DEPS_FAILED = "failed"
#: 校验运行前发现要用户先决定一件事（装不装缺的包 / 换环境）的内部码：任务以 `needs_input` 终局、候选不删（`ConvertAdapter.hold`）
DEPENDENCY_DECISION = "convert_dependency_decision"
#: 任务结局 `unmet[].reason_code`：界面据此出依赖卡片；卡片事实在同一条 `unmet[].data`
DEPENDENCY_DECISION_REASON = "dependency_decision"
#: 首跑里停止晚于安装提交点：依赖已装好、候选保留，任务结局 needs_input 的原因码（界面出「依赖已经装好了…」+「继续 / 放弃」）
DEPENDENCY_INSTALLED_LATE_REASON = "dependency_installed_late"
#: 候选的待决状态：`awaiting_dependencies`（等用户点卡片）→ `resuming`（装 + 继续校验中）→ `ready` / 回 `awaiting_dependencies` / `failed`
STATE_AWAITING = "awaiting_dependencies"
STATE_RESUMING = "resuming"
STATE_FAILED = "failed"
#: 续跑里依赖已装好、但用户的停止来得太晚（过了 deprepair 提交点，环境已换代，修复层拒绝了取消）：候选保留，停在这里等用户点「继续」
STATE_INSTALLED = "deps_installed"
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
_STATES = (
    "ready",
    "adopted",
    "discarded",
    STATE_AWAITING,
    STATE_RESUMING,
    STATE_FAILED,
    STATE_INSTALLED,
)


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
    """原脚本 → 新脚本名。只取主干，所以 `fig.R` / `fig.m` / `fig.py` 都是 `fig_matplotlib.py`。"""
    return f"{Path(script_rel).stem}{NAME_SUFFIX}.py"


#: R / MATLAB 脚本旁“同名原图”的后缀优先级。**只收浏览器（Chromium / WebView2）能直接显示的格式**：对照视图经 `/api/file`
#: 原样取字节显示，TIFF / BMP 之类显示不了，选它当参照会得到一张破图还标成并排对照。仓库里没有现成的“可直接显示”名单
#: （`project_refresh.IMG_EXT` 是素材盘点集，含 TIFF），所以这是唯一的一份；TIFF 旁图 → 不当参照，如实标“未对照验证”。
_SIBLING_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def sibling_original(script_path: Path) -> Path | None:
    """脚本同目录下与它**同主干名**的位图（`fig.R` → `fig.png`）。R / MATLAB 从不被运行，没有“脚本跑出来的图”可指认，
    请求没给 `path` 时就用脚本旁已有的同名原图当对照；没有就 None（结果里如实标“未对照验证”）。不看符号链接。"""
    try:
        names = {p.name.lower(): p for p in script_path.parent.iterdir()}
    except OSError:
        return None
    for ext in _SIBLING_EXTS:
        hit = names.get(f"{script_path.stem}{ext}".lower())
        if hit is not None and hit.is_file() and not hit.is_symlink():
            return hit
    return None


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
    #: 位图库名（`rasterhint.LIBRARIES`）；R / MATLAB 来源没有（None）
    library: str | None
    original_rel: str | None
    reference_name: str | None
    tree_before: dict[str, str]
    project_before: dict
    #: 来源语言（`sourcelang.LANGUAGES`）
    source_language: str = sourcelang.PYTHON
    #: 校验运行前自动装上的发行包（`convertdeps.ensure`）
    installed: list = field(default_factory=list)
    #: 校验运行前停住等用户决定时的卡片事实（`convertdeps` 的 `card`）；非 None = 候选要留着等 `resume`
    hold: dict | None = None
    #: 校验运行前依赖步骤返回了 installed 而取消已置位（停止晚于安装提交点）：候选要留着，停在 `deps_installed` 等用户点「继续」
    late_installed: bool = False
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
    library: str | None,
    source_language: str = sourcelang.PYTHON,
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
            library=library or None,
            source_language=source_language,
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
    """会话收尾：通过检查、留存待采用的候选（含停住等用户决定 / 已装好等继续的）保留；其余（失败 / 取消 / 超时）删掉。"""
    if not cand.ready and cand.hold is None and not cand.late_installed:
        remove_dir(cand.dir)


def prompt_for(cand: ConvertCandidate) -> str:
    return ai_prompt_pack.convert_prompt(
        script=Path(cand.script_rel).name,
        target=cand.target_name,
        library=cand.library,
        source_language=cand.source_language,
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
                deps = _ensure_dependencies(cand, rel)
                if deps is not None:
                    return deps
                cancel = cand.extras.get("cancel")
                if cancel is not None and cancel.is_set():
                    # 依赖旁路没报取消（调研 none / skipped / 不缺包）而取消已置位：不启动校验运行
                    return {"status": "cancelled"}
                out, killed = _run_with_cancel(runner, cand, rel, cancel)
                if killed:
                    return {"status": "cancelled"}
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


def _run_with_cancel(runner, cand: ConvertCandidate, rel: str, cancel) -> tuple[dict, bool]:
    """跑校验运行；运行中取消到来时，若 runner 带 `force_cancel(figures_dir, rel, write_roots)`（app 注入，= `pool.force_cancel`
    的同键硬杀）就当场结束在跑的探测。回 `(输出, 是否因取消被硬杀)`。runner 没有该入口（纯引擎单测）则等同直接调用。"""
    figures_dir, roots = str(cand.project_root), (str(cand.dir),)
    killer = getattr(runner, "force_cancel", None)
    if cancel is None or killer is None:
        return runner(figures_dir, rel, roots) or {}, False
    done = threading.Event()
    fired = threading.Event()

    def watch() -> None:
        while not done.wait(0.05):
            if cancel.is_set():
                fired.set()
                try:
                    killer(figures_dir, rel, roots)
                except Exception:  # noqa: BLE001 —— 硬杀失败不改变结论：探测自己会跑完 / 超时
                    pass
                return

    t = threading.Thread(target=watch, name="convert-cancel-watch", daemon=True)
    t.start()
    try:
        out = runner(figures_dir, rel, roots) or {}
    finally:
        done.set()
        t.join(5)
    return out, fired.is_set()


def _ensure_dependencies(cand: ConvertCandidate, rel: str) -> dict | None:
    """校验运行前：新脚本要的包缺了就按 `convertdeps` 的规则处理（自动装 / 停住等用户决定 / 什么都不做）。回 None = 继续跑校验；
    回 dict = 这就是校验结论：安装失败（不可修正：让 Agent 改代码解决不了装包失败）、被取消、或停住等用户决定。
    钩子由 app 注入（`cand.extras["deps"]`）；没接钩子（纯引擎调用方 / 多数单测）不做任何依赖动作。
    `cand.extras["cancel"]`（任务的取消标记 / 续跑作业的取消标记）原样传给钩子，装依赖的动作据此停手。"""
    hook = cand.extras.get("deps")
    if hook is None:
        return None
    try:
        got = hook(str(cand.project_root), rel, cand.extras.get("cancel")) or {}
    except Exception:  # noqa: BLE001 —— 依赖旁路的任何异常都不该让校验崩：当作没装，让运行自己如实失败
        return None
    status = got.get("status")
    if status == DEPS_INSTALLED:
        # 并入（不覆盖）：「继续」从 `deps_installed` 重建的候选已带着先前装的包，这一轮没装新的也不能把记录抹掉
        cand.installed = list(dict.fromkeys([*cand.installed, *(got.get("installed") or [])]))
        cancel = cand.extras.get("cancel")
        if cancel is not None and cancel.is_set():
            # 装好了、而取消已置位（首跑与续跑同一语义）：修复层拒绝了晚到的取消（环境已换代）。不再往下跑校验（违背用户的停止意图），
            # 也不删候选——续跑由 `run_resume`、首跑由适配器的 `hold` 停在「已装好、等你继续」
            return {"status": DEPS_INSTALLED_STOPPED}
        return None
    if status == DEPS_CANCELLED:
        return {"status": "cancelled"}
    if status == DEPS_HOLD and isinstance(got.get("card"), dict):
        cand.hold = {**got["card"], **({"notice": got["code"]} if got.get("code") else {})}
        return {"status": "error", "code": DEPENDENCY_DECISION, "message": ""}
    if status == DEPS_FAILED:
        return {
            "status": "error",
            "code": DEPENDENCY_INSTALL_FAILED,
            "message": "",
            "installer_code": str(got.get("code") or ""),
            "packages": list(got.get("missing") or []),
        }
    return None


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
        # 没有原图可比 = 未对照验证（四态 unknown）：R / MATLAB 从不被运行，界面据此说一句“请并排看一眼再采用”
        "reference": original["path"] if original else None,
        "verification": VERIFICATION_SIDE_BY_SIDE if original else VERIFICATION_UNKNOWN,
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


def _failure_check(result: dict, st: str) -> CheckResult:
    """校验运行没通过 → 一条 `render.capture` 检查（任务的提交前验证与续跑共用，原因码闭集只写这一处）。"""
    if result["status"] == "cancelled":
        return _check("render.capture", st, "goal", UNKNOWN, "cancelled")
    if result["status"] == "no_figure":
        return _check("render.capture", st, "goal", FAILED, "no_matplotlib_figure")
    code = result.get("code")
    if code == DEPENDENCY_DECISION:
        return _check("render.capture", st, "goal", FAILED, "dependency_decision")
    if code == MISSING_DEPENDENCY_CODE:
        return _check("render.capture", st, "goal", FAILED, "missing_dependency")
    if code == DEPENDENCY_INSTALL_FAILED:
        return _check(
            "render.capture",
            st,
            "goal",
            FAILED,
            "dependency_install_failed",
            installer_code=result.get("installer_code"),
            packages=result.get("packages"),
        )
    return _check(
        "render.capture",
        st,
        "goal",
        FAILED,
        "run_failed",
        code=code,
        detail=result.get("message") or "",
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
        rendered = self._render(run, cand, st)
        # 校验运行会在原脚本旁真跑候选：它写相对路径输出（out.png 之类）就会改到项目文件，所以跑完要再核一遍
        after = [c for c in self._project_untouched(cand, st) if c.verdict != VERIFIED]
        if (cand.hold is not None or cand.late_installed) and after:
            # 依赖调查期间项目被动了：完整性闸门先于“停住等用户决定”。不进依赖等待、不留存候选，
            # 结局同不缺依赖时的完整性失败（“停住”那条伪失败不算证据，丢掉）
            cand.hold = None
            cand.late_installed = False
            return out + after
        return out + rendered + after

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
        cancel = getattr(run, "cancel_event", None)
        if cancel is not None:
            cand.extras["cancel"] = cancel
        result = run_validation(cand, runner)
        cand.extras["validation"] = result
        if result["status"] == DEPS_INSTALLED_STOPPED:
            cand.late_installed = True  # 候选要留（`hold` / `discard` 据此）；这条检查只是占位，`hold` 紧接着以 needs_input 终局
            return [_check("render.capture", st, "goal", UNKNOWN, DEPENDENCY_INSTALLED_LATE_REASON)]
        if result["status"] == "ok":
            cand.comparison = comparison_of(cand, result)
            svg = result.get("svg")
            cand.preview_svg = (
                svg if isinstance(svg, str) and len(svg) <= PREVIEW_MAX_BYTES else None
            )
            return [
                _check("render.capture", st, "goal", VERIFIED, stems=result["stems"]),
            ]
        return [_failure_check(result, st)]

    def hold(self, cand: ConvertCandidate):
        """提交前验证之后的“停住”口（`ai_tasks._verify_precommit`）：校验运行前发现要用户先决定装包 / 换环境时，
        把候选留存成 `awaiting_dependencies`（候选目录 + meta.json，不碰用户文件），任务以 `needs_input` 终局，
        卡片事实随结局的 `unmet[].data` 交给界面；用户点了主按钮由 `resume` 接着走**同一个候选**（不重跑 Agent）。"""
        if cand.late_installed:
            # 首跑里停止来得太晚（依赖步骤已过安装提交点）：环境已换代，候选留存成 `deps_installed`，任务以 needs_input 终局；
            # 取消路径（`halted`）不会再看到它——NeedsInput 在循环前抛出。不自动校验，等用户点「继续」或「放弃」
            stage_installed(cand)
            return ai_tasks.NeedsInput(
                "",
                reason_code=DEPENDENCY_INSTALLED_LATE_REASON,
                data={"installed": list(cand.installed)},
            )
        if cand.hold is None:
            return None
        stage_hold(cand)
        return ai_tasks.NeedsInput("", reason_code=DEPENDENCY_DECISION_REASON, data=dict(cand.hold))

    def settle_hold_on_cancel(self, cand: ConvertCandidate) -> bool:
        """取消已置位时问适配器：这次停住还要不要保留？环境已被改（`late_installed`，依赖装好后才收到停止）必须留成
        `deps_installed`；普通等待（system / uncommon / ask_once 卡）环境没动，取消赢：放掉停住态，执行器 `discard` 会删候选。"""
        if cand.late_installed:
            return True
        cand.hold = None
        return False

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
        params: dict = {"unmet": [u["constraint_id"] for u in unmet]}
        if outcome == "needs_input":
            # Agent 说“哪一步换不了”的那一句（已清洗、有长度上限）；只随任务结局给界面，不进日志 / 遥测
            reasons = [str(u.get("detail") or "") for u in unmet if u.get("detail")]
            params["reason"] = reasons[0] if reasons else ""
        return {"message_key": f"task.{TASK_TYPE}.{outcome}", "params": params}


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
def clean_needs_input_reason(text: str) -> str:
    """Agent 给的“换不了”原因 → 一行干净的短句：去控制字符 / 换行折叠 / 去首尾标点装饰，截到 `NEEDS_INPUT_REASON_MAX`。"""
    one = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    one = re.sub(r"\s+", " ", one).strip(" \t*_`>-")
    return one[:NEEDS_INPUT_REASON_MAX].rstrip()


def needs_input_reason(transcript) -> str | None:
    """Agent 的回答里有没有 `TAVOTTO_NEEDS_INPUT: <原因>`（行首；只看完整回答 `message`，不看工具输出 / 思考）。
    有 → 清洗后的原因（可能为空串，表示只给了标记）；没有 → None。多条取最后一条（最终说法）。"""
    found: str | None = None
    for item in transcript or []:
        if not isinstance(item, dict) or item.get("kind") != "message":
            continue
        for m in _NEEDS_INPUT_RE.finditer(str(item.get("text") or "")):
            found = clean_needs_input_reason(m.group(1))
    return found


class ConvertExecutor(ai_figure_edit.BridgeExecutor):
    """`commit` = 把通过检查的候选留存待采用（候选目录 + meta.json）；**不写任何用户文件**。

    Agent 运行结束后若**没有写出新文件**而回答里带着 `TAVOTTO_NEEDS_INPUT:` 标记 → `NeedsInput`（任务结局 `needs_input`，
    不生成新脚本）；没有标记照旧交给提交前检查（`target_missing`，可修正一次）。"""

    writes_user_files = False

    def __init__(self, *args, transcript=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._transcript = transcript

    def _stop_if_needs_input(self, cand: ConvertCandidate) -> None:
        if self._transcript is None:
            return
        try:
            if cand.target.exists() or cand.target.is_symlink():
                return  # 写了新文件就按新文件走：标记只在“没写文件”时算数
            reason = needs_input_reason(self._transcript())
        except Exception:  # noqa: BLE001 —— 读转录失败当作没有标记
            return
        if reason is not None:
            raise NeedsInput(reason)

    def execute(self, run) -> ConvertCandidate:
        cand = super().execute(run)
        self._stop_if_needs_input(cand)
        return cand

    def correct(self, run, cand: ConvertCandidate, feedback: dict) -> ConvertCandidate:
        cand = super().correct(run, cand, feedback)
        self._stop_if_needs_input(cand)
        return cand

    def commit(self, run, cand: ConvertCandidate) -> None:
        stage(cand)

    def discard(self, run, cand: ConvertCandidate) -> None:
        if cand.hold is not None or cand.late_installed:
            return  # 停住等用户决定的候选要留着（`resume` 接着走它）；取消 / 超时等其余结局不会设 hold
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
        "source_language": cand.source_language,
        "installed": list(cand.installed),
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


def _hold_meta(cand: ConvertCandidate, state: str) -> dict:
    """停住类留存（等依赖决定 / 已装好等继续）共用的 meta：候选脚本字节的摘要一并记下，`resume` 接着走的必须就是停住时的那份。"""
    return {
        "sid": cand.sid,
        "state": state,
        "project": str(cand.project_root.resolve()),
        "script_rel": cand.script_rel,
        "base_sha256": cand.base_sha256,
        "base_mode": cand.base_mode,
        "candidate_sha256": _sha256(cand.new_bytes or b""),
        "target_name": cand.target_name,
        "library": cand.library,
        "source_language": cand.source_language,
        "installed": list(cand.installed),
        "original_rel": cand.original_rel,
        "reference_name": cand.reference_name,
        # 起跑时拍下的项目指纹：续跑的复核基线只能是它（不能用“此刻”的项目重建，否则停住期间的改动被洗白）
        "project_before": cand.project_before,
        "created": time.time(),
    }


def _write_hold_meta(cand: ConvertCandidate, meta: dict) -> None:
    try:
        atomicio.write_bytes(cand.dir / META_NAME, atomicio.dumps_json(meta))
    except OSError as exc:
        raise TaskError("stage_failed", type(exc).__name__) from exc


def stage_hold(cand: ConvertCandidate) -> None:
    """校验运行前停住：把候选留存成 `awaiting_dependencies`（meta.json 里带卡片事实）。**不碰用户文件。**"""
    if cand.new_bytes is None or cand.hold is None:
        raise TaskError("candidate_missing", cand.target_name)
    _write_hold_meta(cand, {**_hold_meta(cand, STATE_AWAITING), "dependency_card": cand.hold})


def stage_installed(cand: ConvertCandidate) -> None:
    """首跑里依赖已装好而停止来得太晚：候选留存成 `deps_installed`（与续跑里同一情形同一状态）。**不碰用户文件。**"""
    if cand.new_bytes is None:
        raise TaskError("candidate_missing", cand.target_name)
    _write_hold_meta(cand, _hold_meta(cand, STATE_INSTALLED))


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


def _reconcile_orphan_resume(sid: str, cdir: Path, meta: dict) -> dict:
    """孤儿续跑的唯一判据与唯一处理：meta 说 `resuming`，本进程 `_RESUMES` 里却没有这个 sid（后端重启 / 续跑线程意外死掉）。
    **持久化回滚**（原子写 meta，卡片等结构化字段原样保留），之后 `prepare_resume` 才能接受 install / replan：
    有 installed 记录（已过安装提交点）→ `deps_installed`（等「继续」）；否则回 `awaiting_dependencies`（环境若已被装到一半改动，
    下一次 install 的摘要校验 `dependency_impact_changed` / `repair_plan_stale` 兜住）；两样都没有（没卡可回）→ `failed`。
    判断与回滚全程持 `_RESUMES_LOCK` 并**重读** meta：`prepare_resume` 登记与 `run_resume` 的摘牌都在这把锁里，
    所以刚登记的续跑不会被回滚，也不会拿旧快照盖掉续跑刚写的终态。返回当前（可能已回滚的）meta。"""
    if meta.get("state") != STATE_RESUMING:
        return meta
    with _RESUMES_LOCK:
        if sid in _RESUMES:
            return meta
        try:
            fresh = json.loads((cdir / META_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return meta
        if not isinstance(fresh, dict) or fresh.get("state") != STATE_RESUMING:
            return fresh if isinstance(fresh, dict) else meta
        if fresh.get("installed"):
            _set_state(cdir, fresh, STATE_INSTALLED)
        elif isinstance(fresh.get("dependency_card"), dict):
            _set_state(cdir, fresh, STATE_AWAITING)
        else:
            _set_state(cdir, fresh, STATE_FAILED, failure={"reason": "resume_interrupted"})
        try:
            return json.loads((cdir / META_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return fresh


def status(sid: str, project: str) -> dict:
    """待采用候选的对照数据（不含脚本全文）。"""
    cdir, meta = _load_meta(sid, project)
    meta = _reconcile_orphan_resume(sid, cdir, meta)
    state = meta.get("state")
    return {
        "session": sid,
        "state": state,
        "script": meta.get("script_rel"),
        "target_name": meta.get("target_name"),
        "library": meta.get("library"),
        "source_language": meta.get("source_language") or sourcelang.PYTHON,
        "installed": list(meta.get("installed") or []),
        "comparison": meta.get("comparison"),
        "has_preview": bool(meta.get("has_preview")) and (cdir / PREVIEW_NAME).is_file(),
        "adopted_script": meta.get("adopted_script"),
        # 等用户决定装包 / 换环境时的卡片事实（`convertdeps` 的 card）；续跑失败时的原因（`failure`）
        "dependency_card": meta.get("dependency_card") if state in (STATE_AWAITING,) else None,
        "failure": meta.get("failure") if state == STATE_FAILED else None,
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
    if _cancel_resume(sid):
        return {
            "ok": True
        }  # 续跑作业在跑：它收到取消后自己停手并删候选目录（装依赖的动作也被取消）
    remove_dir(cdir)
    return {"ok": True}


# --------------------------------------------------------------------------- 续跑（用户在依赖卡片上点了主按钮之后）
#: 正在跑的续跑作业：sid → 取消标记。`discard` / 关闭卡片据此把取消传到正在进行的安装
_RESUMES: dict[str, threading.Event] = {}
_RESUMES_LOCK = threading.Lock()


def resuming(sid: str) -> bool:
    with _RESUMES_LOCK:
        return sid in _RESUMES


def _cancel_resume(sid: str) -> bool:
    """有续跑作业就置位它的取消标记（回 True）；没有回 False。"""
    # 置位必须在锁内：续跑线程「查取消 → 落 ready → 摘登记」也在这把锁里，二者只能一先一后——
    # 要么续跑看见标记（取消赢、删候选），要么登记已摘（回 False，discard 按已 ready 的候选照常删）
    with _RESUMES_LOCK:
        ev = _RESUMES.get(sid)
        if ev is None:
            return False
        ev.set()
    return True


def _before_ready_stage(sid: str) -> None:
    """续跑落 ready 之前的空钩子（测试用它把续跑停在“校验通过、落 ready 之前”）。"""


def _rebuild(cdir: Path, meta: dict, project: Path) -> ConvertCandidate:
    """从留存的候选目录 + meta.json 还原一个能再跑校验的候选。原脚本字节必须仍是起跑基线、候选脚本字节必须仍是停住时的摘要
    ——续跑校验的就是对照时看到的那份。项目指纹基线是**起跑时**存进 meta 的那份（Codex #1330 P1：不许用此刻的项目重建，
    否则停住期间别的进程改了项目文件会被洗白）；此刻的项目与它对不上、或 meta 里没有基线（旧留存）→ 一律失败。"""
    script_rel = str(meta["script_rel"])
    script_path = project / script_rel
    try:
        base = script_path.read_bytes()
        new = (cdir / meta["target_name"]).read_bytes()
    except OSError:
        raise ConvertError("convert_source_changed", 409) from None
    if _sha256(base) != meta["base_sha256"]:
        raise ConvertError("convert_source_changed", 409)
    if _sha256(new) != meta["candidate_sha256"]:
        raise ConvertError("convert_candidate_changed", 409)
    baseline = meta.get("project_before")
    if not isinstance(baseline, dict):
        raise ConvertError("convert_project_changed", 409)
    changed = ai_figure_edit.fingerprint_changes(
        baseline, ai_figure_edit.project_fingerprint(project, exclude=_own_files(script_rel))
    )
    # None = 测不出（任一侧超限 / 读不了，例如停住后项目长过文件数上限）：fail-closed，按「已变化」拒，不当成没变
    if changed is None or changed:
        raise ConvertError("convert_project_changed", 409)
    return ConvertCandidate(
        sid=str(meta["sid"]),
        dir=cdir,
        project_root=project,
        script_rel=script_rel,
        script_path=script_path,
        base_bytes=base,
        base_sha256=str(meta["base_sha256"]),
        base_mode=int(meta.get("base_mode") or 0),
        library=meta.get("library"),
        original_rel=meta.get("original_rel"),
        reference_name=meta.get("reference_name"),
        tree_before=ai_figure_edit._tree(cdir) or {},
        project_before=baseline,
        source_language=str(meta.get("source_language") or sourcelang.PYTHON),
        installed=list(meta.get("installed") or []),
        new_bytes=new,
    )


def prepare_resume(sid: str, project: str) -> tuple[Path, dict, threading.Event]:
    """点卡片主按钮的同步部分：核对状态、登记取消标记（同一个候选同时只有一个续跑作业）。"""
    cdir, meta = _load_meta(sid, project)
    meta = _reconcile_orphan_resume(sid, cdir, meta)
    awaiting = meta.get("state") == STATE_AWAITING and isinstance(meta.get("dependency_card"), dict)
    if not awaiting and meta.get("state") != STATE_INSTALLED:
        raise ConvertError("convert_not_awaiting_dependencies", 409)
    with _RESUMES_LOCK:
        if sid in _RESUMES:
            raise ConvertError("convert_resume_busy", 409)
        ev = threading.Event()
        _RESUMES[sid] = ev
    return cdir, meta, ev


def _drop_candidate_files(cdir: Path, meta: dict) -> None:
    """续跑的完整性失败：删掉候选脚本与预览（meta 留着，只为让轮询读到失败原因；`failed` 本就不可采用）。"""
    for name in (str(meta.get("target_name") or ""), PREVIEW_NAME):
        if name and "/" not in name:
            try:
                (cdir / name).unlink()
            except OSError:
                pass


def _set_state(cdir: Path, meta: dict, state: str, **extra) -> None:
    meta = {k: v for k, v in meta.items() if k not in ("failure",)}
    meta.update({"state": state, **extra})
    try:
        _write_meta(cdir, meta)
    except OSError:
        pass


def run_resume(
    sid: str,
    project: str,
    cdir: Path,
    meta: dict,
    cancel: threading.Event,
    *,
    runner,
    resolve=None,
    confirmed_impact: str = "",
    ensure=None,
) -> dict:
    """接着走**同一个候选**（不重跑 Agent）：装依赖（注入的 `resolve` = `convertdeps.resolve`，用户点击回显的摘要）→ 校验运行 → 对照 → 留存待采用。
    `ensure`（= `convertdeps.ensure`）给「按当前环境重新判定」：不带点击摘要，按**此刻**的项目解释器走自动路径的同一棵决定树
    （能直接装的装 / 要问一次或名单外或仍是系统 Python 的回新卡 / 已不缺的直接续跑）。二者给一个。
    结局 `{"state": ready | awaiting_dependencies | failed | cancelled | deps_installed}`；`cancelled` 时候选目录已删。
    `deps_installed`：取消来得太晚（依赖步骤已过提交点、返回 installed）——候选保留，不再自动校验，等用户点「继续」。
    只在 `prepare_resume` 之后调用（它登记了 `cancel`），返回前一定摘掉登记。"""
    root = Path(project)
    try:
        _set_state(cdir, meta, STATE_RESUMING)
        try:
            cand = _rebuild(cdir, meta, root)
        except ConvertError as exc:
            if exc.code in ("convert_project_changed", "convert_source_changed"):
                _drop_candidate_files(cdir, meta)  # 完整性失败：候选不可再被采用
            _set_state(cdir, meta, STATE_FAILED, failure={"reason": exc.code})
            return {"state": STATE_FAILED, "reason": exc.code}
        cand.extras["runner"] = runner
        cand.extras["cancel"] = cancel
        cand.extras["resume"] = True
        if ensure is not None:
            cand.extras["deps"] = lambda proj, rel, cancel_ev: ensure(proj, rel, cancel_ev)
        else:
            cand.extras["deps"] = lambda proj, rel, cancel_ev: resolve(
                proj, rel, confirmed_impact=confirmed_impact, cancel=cancel_ev
            )
        installed_before = list(
            cand.installed
        )  # 先前（首跑 / 上一轮）装的；「这一轮装了东西」只看新增
        result = run_validation(cand, runner)
        if result["status"] == "cancelled" or (
            cancel.is_set() and cand.installed == installed_before
        ):
            # 依赖步骤真的被取消（没过提交点），或根本没装过东西：用户的停止照旧生效，候选删掉
            remove_dir(cdir)
            return {"state": "cancelled"}
        if cancel.is_set():
            # 依赖步骤返回了 installed（修复层拒绝了晚到的取消，环境已换代）：以安装结果为准，不删候选，
            # 也不再自动跑校验——停在「已装好、等你继续」，由用户点继续（走 replan 入口，已不缺包，直接校验）
            _set_state(cdir, meta, STATE_INSTALLED, installed=list(cand.installed))
            return {"state": STATE_INSTALLED, "installed": list(cand.installed)}
        if cand.hold is not None:
            # 摘要对不上 / 又遇到系统 Python：回到等用户，卡片换成新的（带 notice）
            _set_state(cdir, meta, STATE_AWAITING, dependency_card=cand.hold)
            return {"state": STATE_AWAITING}
        if result["status"] == "ok":
            untouched = [
                c for c in ADAPTER._project_untouched(cand, "precommit") if c.verdict != VERIFIED
            ]
            if not untouched:
                cand.comparison = comparison_of(cand, result)
                svg = result.get("svg")
                cand.preview_svg = (
                    svg if isinstance(svg, str) and len(svg) <= PREVIEW_MAX_BYTES else None
                )
                _before_ready_stage(sid)  # 测试钩子：停在「校验通过、落 ready 之前」
                # 「查取消标记 → 落 ready → 摘登记」在 _RESUMES_LOCK 里一气做完，与 `_cancel_resume` 同一把锁：
                # 其后到达的 discard 看到的是已 ready 的候选（照常删），之前到达的取消在这里被看见（删候选、不 ready）
                with _RESUMES_LOCK:
                    if cancel.is_set():
                        cancelled = True
                    else:
                        cancelled = False
                        stage(cand)
                        _RESUMES.pop(sid, None)
                if cancelled:
                    remove_dir(cdir)
                    return {"state": "cancelled"}
                return {"state": "ready"}
            failure = {"reason": "project_files_changed"}
            _drop_candidate_files(cdir, meta)
        else:
            check = _failure_check(result, "precommit")
            failure = {
                "reason": check.reason_code,
                **{
                    k: v
                    for k, v in (check.observed or {}).items()
                    if k in ("installer_code", "packages", "code")
                },
            }
        _set_state(cdir, meta, STATE_FAILED, failure=failure)
        return {"state": STATE_FAILED, **failure}
    except Exception:  # noqa: BLE001 —— 续跑作业的未预期异常：落成 failed，不让轮询永远等
        _set_state(cdir, meta, STATE_FAILED, failure={"reason": "resume_error"})
        return {"state": STATE_FAILED, "reason": "resume_error"}
    finally:
        with _RESUMES_LOCK:
            _RESUMES.pop(sid, None)


def cancel_registration(sid: str) -> None:
    """起续跑线程失败时摘掉 `prepare_resume` 的登记（否则这个候选永远判成“有作业在跑”）。"""
    with _RESUMES_LOCK:
        _RESUMES.pop(sid, None)
