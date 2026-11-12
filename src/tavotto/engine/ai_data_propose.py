"""TAF 数据出图 v2 的「出备选」流水（ADR 0178）：数据文件夹 → 确定性备选配方 → 每个备选真渲染一张缩略图 → 候选会话。纯标准库，
Flask 父进程 import；**不调模型**（模型工具集是下一个 PR，复用这条流水）。

流水（`propose`）：选表 / 列角色（`ai_data_recipe.default_candidates`，有歧义就返回一道选择题）→ 对选中的那张表读一次带行数据的摘要 →
每个备选 `render_recipe_script` 出脚本 + 公共模块 → 写进**候选目录**（`CANDIDATE_ROOT/<session>/`，在 `data_dir()/cache/` 下）→ 用 safe worker
真渲染 PNG 缩略图与 manifest → 候选脚本算出的数据指纹与 `expected_fingerprints` 对拍，对不上的备选剔除并记原因。

**候选怎么读到数据（与 `ai_convert.run_validation` 的先例对照）**：先例是「影子脚本」——候选字节留在候选目录、worker 以项目里的显示路径
为 `__file__`、cwd 仍是原脚本所在目录、写入根绊线只开候选目录。这里**不能**照搬影子脚本：模板脚本 `import tavotto_plot_xxxxxxxx`（同目录的公共
模块），而影子脚本的显示目录（`<数据文件夹>/tavotto_figures/`）此刻在用户的磁盘上并不存在，公共模块没有地方可被 import。所以本流水取先例的
另外两半——**候选目录是一个独立的小项目**（脚本、公共模块、被选中那一张数据文件的**字节副本**，都在 `CANDIDATE_ROOT/<session>/` 里，布局与将来
落盘后相同：`tavotto_figures/<stem>.py` 读 `../<数据相对路径>`），safe worker 以这个小项目为 `figures_dir` 跑，**写入根绊线只开候选目录**
（`write_roots`）——脚本往任何别处写（含用户文件夹）在写发生的那一刻失败。用户文件夹里**一个字节都不写、不建目录、不改 mtime**；数据文件
只被读（`profile_folder` / `inspect_data_source`）与复制进候选目录，用户的文件不被改写、移动。解释器：先用项目渲染本来会用的那个（`pool.peek_worker_python` 纯读、`pool.pinned_interpreter` 钉在候选目录上，safe 档），这样选中的小图与落盘后的图同一环境、同一字体；它起不来 / 缺 matplotlib / 画不出就**静默**回退到默认链条再画一次（不报错、不装依赖，结果里 `interpreter_fallback` 留诊断标记），两条都不行才算这个备选失败。落盘后的核对是对**原件**渲染的（见 `ai_data_task`），不沿用候选的结论。

**候选目录的生命周期**：会话在内存里登记（`_SESSIONS`），目录在 `CANDIDATE_ROOT/<session>/`；存活 `SESSION_TTL_S`（最后一次读 / 提交后重新计时），
同时至多 `MAX_SESSIONS` 个（超了先删最老的）；提交后**不**删（用户可以接着出同一批里的另一个备选），放弃（`discard`）立刻删；同一会话里数据文件只有**一份**副本（所有备选共用，≤ 5 MiB，`data_too_large` 上限），整个会话目录随会话删，磁盘上界 ≈ `MAX_SESSIONS` × （5 MiB + 每张缩略图 ≤ 4 MiB × 4）、常态每会话只有「数据文件 + 几十 KB 的小图」；每次出备选 / 提交前 `sweep()` 把过期会话与上一个进程留下的
孤儿目录（`mtime` 超过 TTL）一并删。删除一律不用 `ignore_errors=True`：删不掉记日志并留给下一次 sweep，不假装删了。

会话只在内存里：进程重启 = 会话过期（`propose_session_expired`），前端据此重新出备选。会话属于项目：别的项目拿同一个 id 也是过期。
"""

from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import (
    ai_data_figure as adf,
    ai_data_recipe as recipe_mod,
    ai_data_template as tpl,
    ai_figure_edit,
    config,
    figcapture,
    figureverify,
    pool,
    probe,
    projectenv,
)

LOG = logging.getLogger(__name__)

#: 稳定码（HTTP 错误，前端 `errors:backend.<码>` 有中英文文案，`tests/test_error_codes.py` 读这张表）
ERROR_CODES = (
    "propose_session_expired",  # 会话不在了（过期 / 进程重启 / 属于别的项目 / 已提交或放弃）
    "propose_candidate_unknown",  # 会话里没有这个备选
    "propose_data_changed",  # 数据文件自出备选后变了（sha256 不同）：前端重新出备选
    "propose_input_invalid",  # 请求体的字段类型不对 / 超长
    "propose_cells_invalid",  # 数值列里夹着的文字格的处理规则不合格（`params.reason` 是拒绝码，`params.key` 点名哪一项）
    "propose_cells_need_rule",  # 用户只用文字说明了怎么处理（`describe`）：要先由助手整理成规则，不猜
)
#: 一个备选没能出现在结果里的原因（`dropped[].reason_code` / `nothing.reasons`；文案在前端 `dataFigure.reason.<码>`）
DROP_REASONS = (
    "candidate_template_unsupported",  # 模板不认这个配方（内部不一致，不是用户的错）
    "candidate_render_failed",  # 脚本在 safe worker 里跑不出图
    "candidate_timeout",  # 渲染超时
    "candidate_thumbnail_failed",  # 图出来了但缩略图没取到
    "candidate_fingerprint_mismatch",  # 画出来的数据与 CSV / XLSX 对不上（剔除，不展示）
    "candidate_write_blocked",  # 候选脚本想写候选目录以外的地方（被绊线拦下）
)
CANDIDATE_ROOT = config.data_dir() / "cache" / "ai_data_candidates"
SESSION_TTL_S = 30 * 60
MAX_SESSIONS = 6
#: 缩略图宽度（像素）与单张字节上限
THUMB_WIDTH_PX = 640
MAX_THUMB_BYTES = 4 * 1024 * 1024
#: 同时渲染几个备选（每个一条 worker；池自己还有上限）
MAX_PARALLEL = 2
#: 幂等表：同一 `(项目, client_request_id)` 在 TTL 内回同一份响应
MAX_REQUESTS = 64
#: 有行没画出来时的稳定码（备选卡 `unplotted.code` 与提交结果；文案 `ai:dataFigureV2.reason.rows_not_plotted`）
UNPLOTTED_CODE = "rows_not_plotted"

_ID = re.compile(r"[0-9a-f]{12}")
_SESSION_ID = re.compile(r"[0-9a-f]{24}")
_HINT_MAX = 400
_ANSWER_KEYS = ("table", "sheet", "y", "group", "x", "design", "unit")
_ANSWER_MAX = 400


class ProposeError(ValueError):
    """流水的结构化失败；`code` 在 `ERROR_CODES` 里（构造时校验）。"""

    def __init__(self, code: str, detail: str = "", params: dict | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"未登记的 ProposeError 码：{code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.params = dict(params or {})


# =========================================================================== 路径纯函数（不碰磁盘，POSIX 上可测 Windows 口径）
_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_rel(raw: str | None) -> str:
    """请求里的「项目相对路径」→ 规范 POSIX 相对路径（空串 = 项目根）。

    反斜杠当分隔符（Windows 前端 / 用户手输）；去掉空段与 `.`；绝对路径、盘符（`C:`、`c:\\`）、UNC、含 `..` 的一律拒绝
    （`ValueError("path_outside")`，调用方转成 `data_path_outside_project`）。这里只做**字符串**层的挡板，碰文件系统前仍经
    `projectenv.contained_path`。"""
    text = "" if raw is None else str(raw)
    if "\x00" in text:
        raise ValueError("path_outside")
    t = text.replace("\\", "/")
    if t.startswith("/") or _DRIVE.match(t):
        raise ValueError("path_outside")
    parts = [p for p in t.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("path_outside")
    return "/".join(parts)


def script_data_rel(table_rel: str) -> str:
    """数据文件（相对数据文件夹的 POSIX 路径）→ 脚本里的数据路径：`../<相对路径>`（脚本在 `<数据文件夹>/tavotto_figures/`）。
    数据在子目录时是 `../sub/a.csv`；永远 POSIX 分隔符（脚本里 `Path(__file__).parent / '../…'` 在 Windows 上同样成立）。"""
    rel = normalize_rel(table_rel)
    if not rel:
        raise ValueError("path_outside")
    return "../" + rel


def path_key(p: str, *, windows: bool | None = None) -> str:
    """比较路径用的规范键。Windows（盘符大小写、反斜杠、大小写不敏感）：`ntpath.normcase` 后统一成 `/`；其它：`posixpath.normpath`。
    `windows=None` 取当前平台。纯函数：用例在 POSIX 上传 `windows=True` 钉 Windows 口径。"""
    win = (os.name == "nt") if windows is None else windows
    if win:
        return ntpath.normcase(ntpath.normpath(p)).replace("\\", "/")
    return posixpath.normpath(p)


# =========================================================================== 会话
@dataclass
class Item:
    id: str
    code: str  # `recipe_mod.CANDIDATE_CODES` 之一
    recipe: recipe_mod.FigureRecipe
    coverage: dict
    thumb: Path | None = None
    unplotted: int = 0  # 有多少行因为横轴没有值而没画（稳定码 `rows_not_plotted` + 计数）
    cells: list | None = None  # 处理规则作用的统计（没有规则 = None）


@dataclass
class Session:
    id: str
    project: str
    folder_rel: str
    table: str
    sheet: str | None
    data_sha256: str
    data_size: int
    confirmed: recipe_mod.Confirmed
    items: dict[str, Item]
    dir: Path
    touched: float = 0.0

    def item(self, item_id: str) -> Item | None:
        return self.items.get(item_id)


_LOCK = threading.RLock()
_SESSIONS: dict[str, Session] = {}
_REQUESTS: dict[tuple[str, str, str], tuple[float, dict, str | None]] = {}


def _rmtree(path: Path) -> bool:
    """删一个一次性目录；删不掉记日志返回 False（下一次 `sweep` 重试），**不**用 `ignore_errors`。"""
    try:
        shutil.rmtree(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        LOG.warning("候选目录没删掉，留给下一次清理：%s", path.name, exc_info=True)
        return False


def _inside_root(path: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).parent == Path(os.path.realpath(CANDIDATE_ROOT))
    except OSError:
        return False


def _drop(sess: Session) -> None:
    with _LOCK:
        _SESSIONS.pop(sess.id, None)
        for key in [k for k, v in _REQUESTS.items() if v[2] == sess.id]:
            _REQUESTS.pop(key, None)
    if _inside_root(sess.dir):
        _rmtree(sess.dir)


def sweep(now: float | None = None) -> None:
    """删过期会话、超出上限的最老会话与孤儿目录（不在 `_SESSIONS` 里、`mtime` 超过 TTL，多半是上一个进程留下的）。"""
    now = time.time() if now is None else now
    with _LOCK:
        for sess in [s for s in _SESSIONS.values() if now - s.touched > SESSION_TTL_S]:
            _drop(sess)
        live = sorted(_SESSIONS.values(), key=lambda s: s.touched)
        for sess in live[: max(0, len(live) - MAX_SESSIONS)]:
            _drop(sess)
        for key in [k for k, v in _REQUESTS.items() if now - v[0] > SESSION_TTL_S]:
            _REQUESTS.pop(key, None)
        while len(_REQUESTS) > MAX_REQUESTS:
            _REQUESTS.pop(min(_REQUESTS, key=lambda k: _REQUESTS[k][0]), None)
        known = {s.dir.name for s in _SESSIONS.values()}
    try:
        children = list(CANDIDATE_ROOT.iterdir())
    except OSError:
        return
    for child in children:
        if child.name in known or child.is_symlink():
            continue
        try:
            if now - child.stat().st_mtime > SESSION_TTL_S and child.is_dir():
                _rmtree(child)
        except OSError:
            continue


def get_session(project: str, session_id: str, *, touch: bool = True) -> Session:
    """会话取用：不存在 / 过期 / 属于别的项目 → `propose_session_expired`。id 是不透明的 24 位十六进制，格式不对一律当过期。"""
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ProposeError("propose_session_expired")
    now = time.time()
    with _LOCK:
        sess = _SESSIONS.get(session_id)
        if sess is None or sess.project != project or now - sess.touched > SESSION_TTL_S:
            raise ProposeError("propose_session_expired")
        if touch:
            sess.touched = now
        return sess


def get_item(project: str, session_id: str, item_id: str) -> tuple[Session, Item]:
    sess = get_session(project, session_id)
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id) or item_id not in sess.items:
        raise ProposeError("propose_candidate_unknown")
    return sess, sess.items[item_id]


def thumbnail_path(project: str, session_id: str, item_id: str) -> Path:
    """缩略图 PNG 的路径：只回缓存里**我们自己写的**那份（id 与会话都是服务端登记的，不拼用户给的路径）。"""
    sess, item = get_item(project, session_id, item_id)
    thumb = item.thumb
    if thumb is None or thumb.is_symlink() or not thumb.is_file():
        raise ProposeError("propose_candidate_unknown")
    if Path(os.path.realpath(thumb)).parent != Path(os.path.realpath(sess.dir / "thumbs")):
        raise ProposeError("propose_candidate_unknown")
    return thumb


def discard(project: str, session_id: str) -> bool:
    """放弃（或提交后清理）一个会话：删目录。不存在也算成功（幂等）。"""
    try:
        sess = get_session(project, session_id, touch=False)
    except ProposeError:
        return False
    _drop(sess)
    return True


def reset_for_tests() -> None:
    """测试用：删掉全部会话与幂等表。"""
    with _LOCK:
        for sess in list(_SESSIONS.values()):
            _drop(sess)
        _REQUESTS.clear()


# =========================================================================== 请求解析
def _text(value, what: str, limit: int = _ANSWER_MAX) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ProposeError("propose_input_invalid", what)
    return value


def parse_cells(raw) -> dict[str, recipe_mod.CellRule]:
    """`answers.cells`：`{列名: {"mode": "skip"} | {"mode": "extract"} | {"mode": "map", "map": {原文: 数 | null}, "rest": "skip" | "extract"}}`，
    声明式、不含代码。`describe`（用户只写了文字说明）**不是可执行方式**：没有 `map` 一并给就 `propose_cells_need_rule`（要先由助手整理成规则，
    不猜）；带了 `map` 的 `describe` 当 `map` 用。未知方式 / 形状不对 → `propose_cells_invalid`。键值的真假（键是不是真在该列文字格里、
    值是不是有限数）等读到数据后由 `recipe.cell_rule_problems` 判。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > recipe_mod.MAX_Y:
        raise ProposeError("propose_cells_invalid", params={"reason": "cell_rule_invalid"})
    out: dict[str, recipe_mod.CellRule] = {}
    for col, rule in raw.items():
        if not isinstance(col, str) or not isinstance(rule, dict):
            raise ProposeError("propose_cells_invalid", params={"reason": "cell_rule_invalid"})
        body = {k: v for k, v in rule.items() if k != "text"}
        if body.get("mode") == "describe":
            if "map" not in body:
                raise ProposeError("propose_cells_need_rule")
            body["mode"] = "map"
        try:
            out[col] = recipe_mod.CellRule.from_dict({"column": col, **body})
        except recipe_mod.RecipeError as exc:
            raise ProposeError("propose_cells_invalid", params={"reason": exc.code}) from None
    return out


def parse_answers(answers) -> dict:
    """对上一轮选择题的回答：只认 `_ANSWER_KEYS`，值是字符串（列名 / 文件名 / `unsure` …）；别的键丢弃（不报错）。"""
    if answers is None:
        return {}
    if not isinstance(answers, dict):
        raise ProposeError("propose_input_invalid", "answers")
    out: dict = {}
    for key in _ANSWER_KEYS:
        if key in answers:
            val = _text(answers[key], key)
            if val is not None:
                out[key] = val
    if "cells" in answers:
        out["cells"] = parse_cells(answers["cells"])
    return out


def confirmed_from(answers: dict[str, str]) -> tuple[recipe_mod.Confirmed, dict]:
    """回答 → 用户确认过的事实 + 列角色的指定。`design` 只有 `independent` / `repeated` 才是确认；`unsure` 与别的值不是确认
    （走 `picks`，由 `default_candidates` 落到保守默认）。`unit` 列名只在 `repeated` 时才生效（且要是真的 id 列，那里再核）。"""
    design = answers.get("design")
    picks = {k: answers[k] for k in ("y", "group", "x") if k in answers}
    if design == "unsure":
        picks["design"] = "unsure"
    if "unit" in answers:
        picks["unit"] = answers["unit"]
    if design == "repeated":
        return recipe_mod.Confirmed(
            "repeated", answers.get("unit") if "unit" in answers else None
        ), picks
    if design == "independent":
        return recipe_mod.Confirmed("independent"), picks
    return recipe_mod.Confirmed(), picks


def unplotted_rows(values: adf.DataSummary, recipe) -> list[dict]:
    """配方画不出来的行（`row` 是文件里的行号，表头算第 1 行，XLSX 是表内行号）：横轴（trend / relationship）没有值（空 / NaN / 不是数或日期）的行
    画不到坐标里。y 列的空 / NaN 是缺失，折线在那里断开，不算没画；夹着文字格的数值列用户已选了处理方式（`recipe.cells`），
    那些格子是用户明确决定过的，不在这里报（统计在 `cell_stats`）。"""
    out: list[dict] = []
    idx = {c: i for i, c in enumerate(values.columns)}
    x = recipe.x if recipe.family in ("trend", "relationship") else None
    covered = {c.column for c in recipe.cells}
    for n, row in enumerate(values.rows):
        if x is not None:
            cell = row[idx[x]]
            if values.per_column[x].kind == "date":
                ok = adf.parse_date(cell) is not None
            else:
                ok = adf.parse_cell(cell)[0] == "numeric"
            if not ok:
                out.append({"row": values.row_number(n), "column": x, "reason": "x_not_a_value"})
                continue
        for y in recipe.y:
            if y not in covered and adf.parse_cell(row[idx[y]])[0] == "text":
                out.append({"row": values.row_number(n), "column": y, "reason": "not_a_number"})
    return out


def cell_stats(values: adf.DataSummary, recipe) -> list[dict]:
    """处理规则实际作用了多少格（`[{column, mode, ignored, extracted, mapped}]`），与图下方说明同一份计数（`adf.apply_cells`）。"""
    if not recipe.cells:
        return []
    return adf.apply_cells(values.columns, values.rows, [c.args() for c in recipe.cells])[2]


# =========================================================================== 候选渲染
@dataclass
class _Rendered:
    png: Path | None = None
    manifest: dict | None = None
    reason: str | None = None
    fallback: bool = False  # 项目自己的解释器没画成、回退到默认链条才画成的（诊断用，不给界面文案）


def _reason_for_error(err: dict) -> str:
    code = str(err.get("code") or "")
    cause = str((err.get("params") or {}).get("worker_code") or "")
    if "timeout" in code or "timeout" in cause:
        return "candidate_timeout"
    if ai_figure_edit.tripped_write_roots(code, cause):
        return "candidate_write_blocked"
    return "candidate_render_failed"


def _project_pin(root: Path):
    """项目**此刻已经定下来**的解释器（`pool.peek_worker_python`：纯读，不起进程、不体检、不触发依赖流程）；没有 → None（走默认链条）。"""
    try:
        got = pool.peek_worker_python(root)
    except Exception:  # noqa: BLE001 —— 读不出来就当没有
        return None
    return pool.EnvironmentDecision(python=got[0], source=got[1]) if got else None


def _render_one(sdir: Path, rel: str, stem: str, thumb: Path, pin=None) -> _Rendered:
    """先用项目渲染本来会用的解释器（`pin`，safe 档）画；起不来 / 缺 matplotlib / 画不出就**静默**回退到默认链条再画一次
    （不报错、不装依赖）；超时与写入被拦不回退（换解释器救不了）。两条都不行才是这个备选的失败原因。"""
    if pin is not None:
        with pool.pinned_interpreter(str(sdir), pin):
            out = _render_attempt(sdir, rel, stem, thumb)
        if out.png is not None or out.reason in ("candidate_timeout", "candidate_write_blocked"):
            return out
        retry = _render_attempt(sdir, rel, stem, thumb)
        retry.fallback = retry.png is not None
        return retry
    return _render_attempt(sdir, rel, stem, thumb)


def _render_attempt(sdir: Path, rel: str, stem: str, thumb: Path) -> _Rendered:
    """在候选目录这个小项目里用 safe worker 真跑一遍 `rel`，取 manifest 与 PNG 缩略图。**必有超时**（探测与 `render_png` 走 worker 池
    的请求超时）；返回前一定退役这条 worker（状态未知的不复用、kill 后 reap 由池负责）。"""
    roots = (str(sdir),)
    worker_key_args = (rel, str(sdir))
    try:
        result = probe.probe(str(sdir), rel, entries=["main"], write_roots=roots)
        err = result.get("error")
        if err:
            return _Rendered(reason=_reason_for_error(err))
        if list(result.get("stems") or []) != [stem]:
            return _Rendered(reason="candidate_render_failed")
        worker = pool.peek(*worker_key_args, write_roots=roots)
        if worker is None:
            return _Rendered(reason="candidate_render_failed")
        try:
            man = json.loads(
                Path(figcapture.usable_preview_path(worker.out_dir, stem, ".json")).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            return _Rendered(reason="candidate_render_failed")
        try:
            src = Path(worker.render_png(stem, THUMB_WIDTH_PX))
            if src.is_symlink() or not src.is_file() or src.stat().st_size > MAX_THUMB_BYTES:
                return _Rendered(manifest=man, reason="candidate_thumbnail_failed")
            thumb.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, thumb)
        except Exception:  # noqa: BLE001 —— 取不到缩略图只少一张卡
            return _Rendered(manifest=man, reason="candidate_thumbnail_failed")
        return _Rendered(png=thumb, manifest=man)
    except Exception as exc:  # noqa: BLE001 —— 单个备选的任何意外只少一张卡
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        LOG.info("数据出图候选渲染失败：%s", code)
        return _Rendered(
            reason="candidate_timeout" if "timeout" in code.lower() else "candidate_render_failed"
        )
    finally:
        try:
            pool.invalidate(rel, str(sdir), None, only_run=True, force=True, write_roots=roots)
        except Exception:  # noqa: BLE001 —— 退役失败不改变结论；池按 LRU 淘汰
            LOG.warning("数据出图候选渲染后 worker 退役失败", exc_info=True)


def _stage_table(sdir: Path, source: Path, table_rel: str) -> None:
    """把选中的那一张数据文件的**字节副本**放进候选目录（布局与将来落盘后相同）。只复制不链接；源文件只读。"""
    dest = sdir.joinpath(*PurePosixPath(normalize_rel(table_rel)).parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)


def _render_candidates(
    sdir: Path,
    entry: adf.TableEntry,
    values: adf.DataSummary,
    recipes: list[recipe_mod.FigureRecipe],
    pin=None,
) -> list[tuple[recipe_mod.FigureRecipe, dict | None, str | None, Path | None, bool]]:
    """每个备选 → `(配方, 覆盖情况, 剔除原因 | None, 缩略图, 是否回退过解释器)`。覆盖情况 = `fingerprint_coverage`；剔除原因在 `DROP_REASONS` 里。"""
    figures = sdir / "tavotto_figures"
    figures.mkdir(parents=True, exist_ok=True)
    data_rel = script_data_rel(entry.path)
    plan: list[tuple[int, str, recipe_mod.FigureRecipe, str | None, list]] = []
    for n, recipe in enumerate(recipes, start=1):
        stem = f"cand_{n}"
        try:
            rendered = tpl.render_recipe_script(entry, recipe, data_rel=data_rel, stem=stem)
            expected = tpl.expected_fingerprints(values, recipe)
        except tpl.TemplateError:
            plan.append((n, stem, recipe, "candidate_template_unsupported", []))
            continue
        (figures / rendered.runtime_name).write_bytes(rendered.runtime)
        (figures / f"{stem}.py").write_text(rendered.script, encoding="utf-8")
        plan.append((n, stem, recipe, None, expected))

    def work(item):
        n, stem, recipe, bad, expected = item
        if bad:
            return recipe, None, bad, None, False
        out = _render_one(
            sdir, f"tavotto_figures/{stem}.py", stem, sdir / "thumbs" / f"{n}.png", pin
        )
        reason = out.reason
        if reason is None and out.manifest is not None:
            checks = figureverify.data_matches_expected(out.manifest, expected, stage="precommit")
            if any(c.verdict == "failed" for c in checks):
                reason = "candidate_fingerprint_mismatch"
        return recipe, tpl.fingerprint_coverage(recipe).to_dict(), reason, out.png, out.fallback

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        return list(ex.map(work, plan))


# =========================================================================== 出备选
def _table_for_values(root_folder: Path, entry: adf.TableEntry) -> adf.DataSummary:
    return adf.inspect_data_source(root_folder / PurePosixPath(entry.path), entry.sheet)


def item_to_dict(sess: Session, item: Item, recommended: bool) -> dict:
    r = item.recipe
    return {
        "id": item.id,
        "title_code": item.code,
        "why_code": item.code,
        "values": {
            "table": r.table,
            "sheet": r.sheet,
            "family": r.family,
            "x": r.x,
            "y": list(r.y),
            "group": r.group,
            "facet": r.facet,
            "unit": r.unit,
            "layers": [layer.to_dict() for layer in r.layers],
        },
        "recommended": recommended,
        "coverage": item.coverage,
        "cells": item.cells,
        "unplotted": {
            "count": item.unplotted,
            "code": UNPLOTTED_CODE if item.unplotted else None,
        },
        "thumbnail_url": f"/api/ai/data-figure/thumbnail/{sess.id}/{item.id}",
    }


def propose(
    root: Path,
    *,
    project: str,
    folder: str | None,
    table: str | None = None,
    sheet: str | None = None,
    hint: str | None = None,
    answers=None,
    client_request_id: str | None = None,
) -> dict:
    """出备选。返回三选一：`{"kind": "ask", question_code, options, unsure}` / `{"kind": "candidates", session, items, dropped}` /
    `{"kind": "nothing", reasons}`。数据读不了（文件夹不存在 / 不在项目内）抛 `DataSourceError`（app 转 400 + 稳定码）。

    **不往用户文件夹写任何东西**（候选目录在 `data_dir()/cache/`，见模块说明）；幂等：同一 `(项目, client_request_id)` 在 TTL 内回同一份响应。"""
    sweep()
    folder = _text(folder, "folder", 1024)
    hint = _text(hint, "hint", _HINT_MAX)
    table = _text(table, "table")
    sheet = _text(sheet, "sheet")
    ans = parse_answers(answers)
    # 幂等键 = (项目, 请求号, 请求内容的摘要)：同一请求号带了不同的回答（换了处理方式）必须重新出图，不回旧的
    digest = hashlib.sha256(
        json.dumps(
            [folder, table, sheet, hint, _answers_digest(ans)], ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    if client_request_id is not None:
        with _LOCK:
            hit = _REQUESTS.get((project, client_request_id, digest))
        if hit is not None and (hit[2] is None or hit[2] in _SESSIONS):
            return hit[1]

    def remember(response: dict, session_id: str | None = None) -> dict:
        return _remember(project, client_request_id, response, session_id, digest)

    table = table or ans.get("table")
    sheet = sheet or ans.get("sheet")
    try:
        folder_rel = normalize_rel(folder)
    except ValueError:
        raise adf.DataSourceError("data_path_outside_project") from None
    real = projectenv.contained_path(root, folder_rel)
    if real is None:
        raise adf.DataSourceError("data_path_outside_project")
    folder_path = Path(real)
    if not folder_path.is_dir():
        raise adf.DataSourceError("data_not_found")
    root_real = Path(os.path.realpath(root))
    folder_rel = folder_path.relative_to(root_real).as_posix() if folder_path != root_real else ""
    profile = adf.profile_folder(folder_path)
    confirmed, picks = confirmed_from(ans)
    chosen = recipe_mod.pick_table(profile, table, sheet, hint)
    if isinstance(chosen, recipe_mod.NeedsChoice):
        return remember({"kind": "ask", **chosen.to_dict()})
    if isinstance(chosen, str):
        return remember({"kind": "nothing", "reasons": [chosen], "dropped": []})
    values = _table_for_values(folder_path, chosen)
    rules = ans.get("cells") or {}
    if rules:
        bad = recipe_mod.check_cell_rules(list(rules.values()), chosen.summary, values)
        if bad:
            raise ProposeError(
                "propose_cells_invalid", params={"reason": bad[0].code, "key": bad[0].detail}
            )
    res = recipe_mod.default_candidates(
        profile,
        table=chosen.path,
        sheet=chosen.sheet,
        confirmed=confirmed,
        hint=hint,
        values=values,
        picks=picks,
        cells=rules,
    )
    if isinstance(res, (recipe_mod.NeedsChoice, recipe_mod.MixedCells)):
        return remember({"kind": "ask", **res.to_dict()})
    if not res.items:
        return remember(
            {
                "kind": "nothing",
                "reasons": list(res.reasons),
                "dropped": [{"reason_code": p.code} for d in res.dropped for p in d.problems[:1]],
            }
        )
    sid = secrets.token_hex(12)
    sdir = CANDIDATE_ROOT / sid
    sdir.mkdir(parents=True, exist_ok=False)
    try:
        _stage_table(sdir, folder_path / PurePosixPath(chosen.path), chosen.path)
        rendered = _render_candidates(
            sdir, chosen, values, [c.recipe for c in res.items], _project_pin(root)
        )
    except BaseException:
        _rmtree(sdir)
        raise
    items: dict[str, Item] = {}
    order: list[str] = []
    dropped: list[dict] = [{"reason_code": p.code} for d in res.dropped for p in d.problems[:1]]
    fallback = False
    for cand, (recipe, coverage, reason, png, fb) in zip(res.items, rendered, strict=True):
        fallback = fallback or fb
        if reason is not None or png is None:
            dropped.append({"reason_code": reason or "candidate_render_failed"})
            continue
        iid = secrets.token_hex(6)
        items[iid] = Item(
            iid,
            cand.code,
            recipe,
            coverage or {},
            png,
            len(unplotted_rows(values, recipe)),
            cell_stats(values, recipe) or None,
        )
        order.append(iid)
    if not items:
        _rmtree(sdir)
        return remember(
            {
                "kind": "nothing",
                "reasons": sorted({d["reason_code"] for d in dropped}) or ["no_renderable_family"],
                "dropped": dropped,
            }
        )
    sess = Session(
        id=sid,
        project=project,
        folder_rel=folder_rel,
        table=chosen.path,
        sheet=chosen.sheet,
        data_sha256=values.sha256,
        data_size=values.size,
        confirmed=res.confirmed,
        items=items,
        dir=sdir,
        touched=time.time(),
    )
    with _LOCK:
        _SESSIONS[sid] = sess
    out = {
        "kind": "candidates",
        "session": sid,
        "items": [item_to_dict(sess, items[i], k == 0) for k, i in enumerate(order)],
        "dropped": dropped,
        # 诊断标记：至少有一张小图是项目解释器没画成、回退到默认链条画的
        "interpreter_fallback": fallback,
    }
    return remember(out, sid)


def _answers_digest(ans: dict) -> dict:
    return {
        k: ({c: r.to_dict() for c, r in sorted(v.items())} if k == "cells" else v)
        for k, v in ans.items()
    }


def _remember(
    project: str, rid: str | None, response: dict, session_id: str | None, digest: str
) -> dict:
    if rid is not None:
        with _LOCK:
            _REQUESTS[(project, rid, digest)] = (time.time(), response, session_id)
    return response


__all__ = [
    "CANDIDATE_ROOT",
    "DROP_REASONS",
    "ERROR_CODES",
    "Item",
    "ProposeError",
    "Session",
    "discard",
    "get_item",
    "get_session",
    "normalize_rel",
    "path_key",
    "propose",
    "script_data_rel",
    "sweep",
    "thumbnail_path",
]
