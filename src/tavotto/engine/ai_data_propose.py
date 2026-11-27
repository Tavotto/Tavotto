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

**合画**（`propose(tables=[…])`，`ai_data_merge.py`）：几份列名相同的数据在内存里拼成一张逻辑表再走同一条流水；候选目录里放的是**每一份**的字节副本（布局 = 相对落点目录的路径，落点 = 这几份的最近公共父文件夹），
总大小受 `ai_data_merge.MAX_TOTAL_BYTES` 约束（不是 5 MiB），其余（`MAX_SESSIONS`、TTL、写入根绊线、用户文件夹零写入）不变。

会话只在内存里：进程重启 = 会话过期（`propose_session_expired`），前端据此重新出备选。会话属于项目：别的项目拿同一个 id 也是过期。
"""

from __future__ import annotations

import datetime
import errno
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
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from . import (
    ai_data_figure as adf,
    ai_data_merge as merge_mod,
    ai_data_progress as progress,
    ai_data_purpose as purpose_mod,
    ai_data_recipe as recipe_mod,
    ai_data_roles as roles_mod,
    ai_data_template as tpl,
    ai_figure_edit,
    config,
    discover,
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
    "propose_cancelled",  # 前端取消了这个读表类请求（概览 / 出备选；`/api/ai/data-progress/<rid>/cancel`）：什么都没留下
    "propose_thumbnail_failed",  # 懒渲染（`render="lazy"`）：这一张缩略图画不出来（`params.reason` 是 `DROP_REASONS` 里的码）
    "tables_columns_differ",  # 合画（`tables`）：这几份的列名集合或顺序不一样，拼不成一张表（`params.index` = 第一个与第一份不同的下标）
    "tables_limit_exceeded",  # 合画：份数超过上限（`params.max`）
    "tables_labels_collide",  # 合画：两个文件的标签在分组时会被并成一组（内部保险，正常不会出现；绝不静默并组）
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
#: 候选目录里**大表字节副本**的总量上限：每个会话放一份选中那张表的副本（≤ `adf.MAX_BYTES`），最多 6 个会话会占满磁盘；
#: 新会话放不下就先丢最老的（用户已经往前走了的）。小表（≤ `adf.SMALL_BYTES`）不计入。
LARGE_STAGE_BUDGET = 1024 * 1024 * 1024
#: 缩略图宽度（像素）与单张字节上限
#: 候选图的渲染宽度（像素）：主图在 2 倍屏上约 700 CSS 像素宽，640 放大了会糊（2026-11-24 改成一张主图 + 小图切换）
THUMB_WIDTH_PX = 1440
MAX_THUMB_BYTES = 4 * 1024 * 1024
#: 同时渲染几个备选（每个一条 worker；池自己还有上限）。懒渲染下是「全进程同时在画的缩略图」上限（`_RENDER_SLOTS`）
MAX_PARALLEL = 2
#: `propose(render=...)` 认的取值：`None` / `"eager"` = 一次画完再返回（旧行为）；`"lazy"` = 先返回文字，缩略图在取图时画
RENDER_MODES = ("eager", "lazy")
#: 懒模式下替用户做的默认决定最多迭代几轮（每轮消掉一个问题；问题种类有限，防御性上界）
MAX_AUTO_ROUNDS = 8
#: 幂等表：同一 `(项目, client_request_id)` 在 TTL 内回同一份响应
MAX_REQUESTS = 64
#: 有行没画出来时的稳定码（备选卡 `unplotted.code` 与提交结果；文案 `ai:dataFigureV2.reason.rows_not_plotted`）
UNPLOTTED_CODE = "rows_not_plotted"

_ID = re.compile(r"[0-9a-f]{12}")
_SESSION_ID = re.compile(r"[0-9a-f]{24}")
_HINT_MAX = 400
#: `x_measure`（G-018，读懂数据派生）：模型判「规则选作横轴的那一列是测量值、不是有序自变量」时给出的**列名**；不是用户动过的下拉（不进 `picks` 的 x / y / group）
#: `answers.purpose`：用户点选的看图目的（`ai_data_purpose.PURPOSES` 之一，闭集外的值当没给）。只重排规则已给出的备选、不新增；**不算**接管列角色
#: （不进 `_user_took_over`，与 `error` 同理），进幂等键（`_answers_digest` 带整个 `ans`）
_ANSWER_KEYS = (
    "table",
    "sheet",
    "y",
    "group",
    "x",
    "design",
    "unit",
    "x_measure",
    "error",
    "purpose",
)
#: `answers.error`：用户点选的「误差线 / 误差带画的是什么」→ 配方里的汇总方式。**只有用户点了才有**（模型的建议不写这个键）；
#: 没点 = 模板的默认（均值 ± 标准差），响应里 `error.confirmed` 为 False
ERROR_SUMMARY = {"sd": "mean_sd", "sem": "mean_sem", "ci95": "mean_ci95"}
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
    unplotted: int = 0  # 有多少行没画（`unplotted_rows` 的总数；稳定码 `rows_not_plotted` + 计数）
    cells: list | None = None  # 处理规则作用的统计（没有规则 = None）
    #: 大表：画出来的是缩减后的点 `{"rows": 整表行数, "kept": 留下的行数}`；小表 = None（响应里没有这一项）
    reduced: dict | None = None
    #: 缩略图状态：`ready`（已有 PNG；旧模式一律如此）/ `pending`（懒模式，还没画）/ `failed`（`reason` 是 `DROP_REASONS` 里的码）
    state: str = "ready"
    reason: str | None = None
    #: 相对当前看图目的，为什么这张不是推荐（`ai_data_purpose.why_not`：`{code, guidance, tier}`）；推荐的 / 没有适用理由 = None（响应里没有这一项）
    why_not: dict | None = None
    fallback: bool = False  # 画这张时项目解释器没画成、回退过（诊断标记）
    #: 合画且文件里原本有可再拆的分组列时，成对出现的两种画法各自的标记：`{"kind": "split", "column": 列名}`（按它再拆开）/
    #: `{"kind": "by_file"}`（不拆、只按文件比较）；没有成对的另一张（两种画法画出来一样）就是 None
    variant: dict | None = None
    #: 给界面「依据」那句话用的数（`candidate_facts`；大表 / 把列当分组 = None，响应里没有这一项）
    facts: dict | None = None
    #: 多个 y 的趋势图每条线各自的数（`series_facts`；单个 y / 大表 / 把列当分组 = None，响应里没有这一项）；有它时没有 `facts`
    series_facts: dict | None = None
    #: 这张图上每条拟合线的系数（`tpl.fit_report`；没有拟合线 = 空，响应里没有这一项）
    fits: list = field(default_factory=list)
    #: 误差线 / 误差带是**算出来的**（均值 ± 误差）时，哪些位置因为 n < 2 没有误差线 / 带（标签与图上一致；`error_missing_for`）；没有 = 空，响应里没有这一项
    error_missing: list = field(default_factory=list)
    #: 误差线取自现成的误差列、而且「是哪一种」是**列名自己说的**（不是用户点的、也不是 `unknown`）：界面据此写「取自列名」而不是「需要你确认」
    error_stated: bool = False
    #: 多个 y、各配各的现成误差列时的逐列事实（`error_columns_of`）；单个 y / 没有 = 空
    error_columns: list = field(default_factory=list)
    # 懒渲染的素材：候选脚本在会话目录里，这里记下渲染一张要的最少东西
    stem: str | None = None
    n: int = 0
    expected: list = field(default_factory=list)
    #: 同一张并发取图只画一次：渲染在这把锁里做，后来的在锁外等到状态落定
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


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
    #: 出备选时实际用的「每列名字」所在的物理行号（文本表格；XLSX 是 None）。提交时按它重读，不重新嗅探
    header_line: int | None = None
    root: Path | None = None  # 项目目录（懒渲染取项目解释器用）
    #: 懒渲染：画哪些备选（`items` 的插入顺序就是推荐顺序）
    lazy: bool = False
    #: 大表（`DataSummary.large`）：进度只对大表有条目
    large: bool = False
    #: 合画（几份拼一张）：各份与合成列；单份 = None（`table` / `sheet` / `data_sha256` 在合画时是第一份的 / 拼后的身份，`folder_rel` 是落点目录）
    merge: merge_mod.MergeSpec | None = None
    #: 出备选时用的样式（请求带了出版规范才有；None = 默认规范）。提交按同一份出脚本，预览与落盘的图同宽同字号
    style: dict | None = None

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


def _staged_bytes(sess: Session) -> int:
    return sess.data_size if sess.data_size > adf.SMALL_BYTES else 0


def make_room(extra: int) -> None:
    """要放进 `extra` 字节的大表副本之前：丢最老的会话，直到所有会话的大表副本加上它不超过 `LARGE_STAGE_BUDGET`。"""
    if extra <= adf.SMALL_BYTES:
        return
    with _LOCK:
        for sess in sorted(_SESSIONS.values(), key=lambda s: s.touched):
            if sum(_staged_bytes(s) for s in _SESSIONS.values()) + extra <= LARGE_STAGE_BUDGET:
                break
            if _staged_bytes(sess):
                _drop(sess)


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


#: `data_digest` 愿意读的最大文件（字节）：超过就不算（回 None = 前端当「核对不了」）
DIGEST_MAX_BYTES = 512 * 1024 * 1024


def data_digest(root: Path, table: str | None) -> str | None:
    """项目里这份数据文件**原始字节**的 sha256（与 `propose` 响应的 `data_sha256` 同一口径：CSV / TSV / 文本表 / XLSX 都是整个文件的字节）。

    给「图的来历」核对数据变没变用：路径只认项目相对路径（`normalize_rel` + `contained_path`，不跟出项目），不是普通文件 / 读不了 / 超过
    `DIGEST_MAX_BYTES` 一律回 None（不是错误：前端当「核对不了」，不出那一行）。只读、不碰缓存、不起任何后台任务。"""
    try:
        rel = normalize_rel(table)
    except ValueError:
        return None
    real = projectenv.contained_path(root, rel) if rel else None
    if real is None:
        return None
    try:
        p = Path(real)
        if not p.is_file() or p.stat().st_size > DIGEST_MAX_BYTES:
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


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
#: `header_rows` 最多给多少个文件、行号上限（与 `adf.MAX_ROWS` 同量级的物理行号）
_HEADER_ROWS_MAX = 64


def parse_header_rows(raw) -> dict[str, int]:
    """`header_rows`：`{表的路径（相对所选文件夹的 POSIX 路径，与 `table` 同一口径）: 每列名字所在的物理行号（1 起）}`，按文件指定。
    只校验形状（对象、≤ 64 项、键是 ≤ 1024 字符的字符串、值是 1 到 `adf.MAX_ROWS * 2 + 1000` 之间的整数，不收布尔）；键对不对由
    遍历结果逐字比对（对不上的静默忽略，不拿它拼路径），行号对不对由读表判（`data_header_row_invalid`）。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > _HEADER_ROWS_MAX:
        raise ProposeError("propose_input_invalid", "header_rows")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not 0 < len(k) <= 1024 or "\x00" in k:
            raise ProposeError("propose_input_invalid", "header_rows")
        out[k] = parse_header_row(v)
    return out


def parse_header_row(v) -> int:
    """一个表头物理行号：1 到 `adf.MAX_ROWS * 2 + 1000` 之间的整数，不收布尔；`parse_header_rows` 与数据概览端点共用这一份判据。"""
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 2 * adf.MAX_ROWS + 1000:
        raise ProposeError("propose_input_invalid", "header_rows")
    return v


def parse_tables(raw) -> list[tuple[str, str | None]] | None:
    """`tables`：合画的几份数据 `[{path, sheet?}]`（`path` 与 `table` 同口径：相对所选文件夹的 POSIX 路径）。不给 = None（走单份流程）；
    不是列表 / 不足 2 份 / 项的形状不对 → `propose_input_invalid`；超过 `merge_mod.MAX_TABLES` 份 → `tables_limit_exceeded`（`params.max`）。
    只校验形状：路径对不对由遍历结果逐字比对。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ProposeError("propose_input_invalid", "tables")
    if len(raw) > merge_mod.MAX_TABLES:
        raise ProposeError("tables_limit_exceeded", params={"max": merge_mod.MAX_TABLES})
    if len(raw) < 2:
        raise ProposeError("propose_input_invalid", "tables")
    out: list[tuple[str, str | None]] = []
    for item in raw:
        if not isinstance(item, dict) or not set(item) <= {"path", "sheet"}:
            raise ProposeError("propose_input_invalid", "tables")
        path = _text(item.get("path"), "tables", 1024)
        if not path:
            raise ProposeError("propose_input_invalid", "tables")
        out.append((path, _text(item.get("sheet"), "tables")))
    return out


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


#: 请求里那份出版规范的体积上限（内置最大的一份约 10 KB；期刊覆盖并入后同量级）
SPEC_MAX_BYTES = 256 * 1024
#: 期刊字号范围里一个数的合理区间（pt）
FONT_PT_MIN, FONT_PT_MAX = 1.0, 72.0


def parse_font_range(raw) -> tuple[float | None, float | None] | None:
    """请求带的期刊字号范围 `{min_pt?, max_pt?}` → `(下限, 上限)`；形状不对 / 数不合理 / 上下限写反 = 当作没给（不报错，按规范出图）。"""
    if not isinstance(raw, dict):
        return None
    out = []
    for key in ("min_pt", "max_pt"):
        v = raw.get(key)
        if v is None:
            out.append(None)
        elif (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and FONT_PT_MIN <= v <= FONT_PT_MAX
        ):
            out.append(float(v))
        else:
            return None
    lo, hi = out
    if (lo is None and hi is None) or (lo is not None and hi is not None and lo > hi):
        return None
    return lo, hi


def parse_spec(raw, font_range=None) -> tuple[dict | None, str | None, bool]:
    """请求带的出版规范 → `(模板样式 | None, 幂等摘要 | None, 是否退回了默认)`。不给 = `(None, None, False)`。
    不是对象 / 过大 / 过不了 `profiles.validate_spec` 都算「不合法」：退回默认规范并如实标出，不抛错（出图不该因为规范坏了而失败）。"""
    if raw is None:
        return None, None, False
    from . import profiles

    try:
        if not isinstance(raw, dict):
            raise profiles.ProfileError("spec")
        text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        if len(text.encode("utf-8")) > SPEC_MAX_BYTES:
            raise profiles.ProfileError("spec")
        fonts = parse_font_range(font_range)
        style = tpl.style_from_profile(profiles.validate_spec(json.loads(text)), fonts)
    except (profiles.ProfileError, TypeError, ValueError, RecursionError):
        return None, "invalid", True
    digest = hashlib.sha256((text + json.dumps(fonts)).encode("utf-8")).hexdigest()
    return style, digest, False


def with_error_summary(recipe: recipe_mod.FigureRecipe, error: str | None):
    """把配方里「均值 ± 某种误差」的层换成用户点选的那一种（`sd` / `sem` / `ci95`）；没点 / 不认识 / 配方里没有这样的层 = 原样。
    只换误差的定义，中心仍是均值（中位数 + 四分位距的层不动）。误差线取自现成的误差列（`recipe.err`）的配方：用户点选的覆盖列名说的那一种（`err_kind`），画的数不变。"""
    target = ERROR_SUMMARY.get(error or "")
    if target is not None and recipe.err is not None:
        # 多个 y 时每个 y 一个种类：用户点选的所有 y 一起换（画的数不变）
        return replace(
            recipe,
            err_kind=error if isinstance(recipe.err, str) else (error,) * len(recipe.err_cols),
        )
    if target is None or not any(
        layer.summary in ERROR_SUMMARY.values() for layer in recipe.layers
    ):
        return recipe
    layers = tuple(
        replace(layer, summary=target) if layer.summary in ERROR_SUMMARY.values() else layer
        for layer in recipe.layers
    )
    return replace(recipe, layers=layers)


def error_of(recipe: recipe_mod.FigureRecipe) -> str | None:
    """这份配方的误差线 / 误差带画的是哪一种（`sd` / `sem` / `ci95`；取自现成误差列、名字没说是哪种的是 `unknown`——绝不当成 SD）；没有这样的层 = None。"""
    if recipe.err is not None:
        kinds = set(
            recipe.err_kinds
        )  # 多个 y 的种类不一致时没有「那一种」：顶层不报，逐列看 `error_columns`
        return next(iter(kinds)) if len(kinds) == 1 else None
    back = {v: k for k, v in ERROR_SUMMARY.items()}
    for layer in recipe.layers:
        if layer.summary in back and layer.mark in ("errorbar", "band"):
            return back[layer.summary]
    return None


def error_source(recipe: recipe_mod.FigureRecipe) -> str | None:
    """误差线 / 误差带从哪来：`column` = 取自表里现成的误差列；`computed` = 用这份数据的重复值算出来的均值 ± 误差；没有误差线 = None。"""
    if recipe.err is not None:
        return "column"
    return "computed" if error_of(recipe) is not None else None


def _stated(summary: adf.DataSummary, column: str, kind: str, answers: dict) -> bool:
    return (
        kind not in (None, "unknown")
        and answers.get("error") not in ERROR_SUMMARY
        and recipe_mod.error_kind_of(summary, column) == kind
    )


def error_stated_of(
    summary: adf.DataSummary, recipe: recipe_mod.FigureRecipe, answers: dict
) -> bool:
    """（单个 y）误差线取自现成的误差列、而且是哪一种**由列名自己说明**：`err_kind` 不是 `unknown`、与列名说的一致、用户没点选过别的（`answers.error`）。
    名字没说（`unknown`）从来不算「已说明」——界面要问用户，不替他定成 SD。多个 y 的逐列判断在 `error_columns_of`，这里恒为假。"""
    return isinstance(recipe.err, str) and _stated(
        summary, recipe.err, recipe.err_kind or "", answers
    )


def error_columns_of(
    summary: adf.DataSummary, recipe: recipe_mod.FigureRecipe, answers: dict
) -> list[dict]:
    """多个 y、各配各的现成误差列时，逐列的事实：`[{y, column, kind, stated}]`（`stated` 同 `error_stated_of`：种类由列名自己说明）。单个 y / 没有现成误差列 = 空。"""
    if recipe.err is None or isinstance(recipe.err, str):
        return []
    return [
        {"y": y, "column": c, "kind": k, "stated": _stated(summary, c, k, answers)}
        for y, c, k in zip(recipe.y, recipe.err_cols, recipe.err_kinds)
    ]


#: `error_missing_for` 最多列几个标签（超出的不列；图上一个位置也没有误差线的情形不会让响应变得很大）
ERROR_MISSING_MAX = 50


def _x_text(x: float, is_date: bool) -> str:
    if is_date:
        return (adf._EPOCH + datetime.timedelta(days=round(x))).isoformat()
    return f"{x:.12g}"


def error_missing_for(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> list[str]:
    """误差线 / 误差带是算出来的（均值 ± SD / SEM / 95% CI 的 `errorbar` / `band` 层）时，**图上哪些位置没有误差线 / 带**：该位置只有 1 个值（n < 2，没有标准差）。
    用的是脚本画图的同一批函数（`summarize` / `trend_centers`），所以报的就是图上缺的那几处。标签与图上的写法一致：比较类是类别（有分组 / 分面再加上，用 ` / ` 连），
    趋势类是横轴取值（日期用 ISO 日期）；多个 y 列再加列名。没有这样的层、取自现成误差列的、大表 = 空（响应里没有这一项）。"""
    summaries = {
        layer.summary
        for layer in recipe.layers
        if layer.mark in ("errorbar", "band") and layer.summary in ERROR_SUMMARY.values()
    }
    if not summaries or recipe.err is not None or values.large is not None:
        return []
    summary = sorted(summaries)[0]
    values = recipe_mod.with_cells_applied(values, recipe)
    out: list[str] = []

    def label(*parts) -> str:
        return " / ".join(str(p) for p in parts if p)

    if recipe.family == "comparison":
        d = tpl.comparison_data(
            values.columns,
            values.rows,
            recipe.x,
            recipe.y[0],
            recipe.group,
            recipe.facet,
            recipe.order,
        )
        for p in d["panels"]:
            for g in d["groups"]:
                for ci, vals in tpl.comparison_cells(d, p, g):
                    _c, lo, hi = tpl.summarize(vals, summary)
                    if not (lo == lo and hi == hi):
                        out.append(label(d["cats"][ci], g, p if recipe.facet else ""))
    elif recipe.family == "trend" and recipe.x:
        is_date = values.per_column[recipe.x].kind == "date"
        d = tpl.trend_data(
            values.columns,
            values.rows,
            recipe.x,
            list(recipe.y),
            recipe.group,
            recipe.facet,
            recipe.unit,
            is_date,
        )
        for p in d["panels"]:
            for g in d["groups"]:
                for y in recipe.y:
                    cx, cc, lo, hi = tpl.trend_centers(tpl.trend_pool(d, p, g, y), summary)
                    for x, c, a, b in zip(cx, cc, lo, hi):
                        if c == c and not (a == a and b == b):
                            out.append(
                                label(
                                    y if len(recipe.y) > 1 else "",
                                    g,
                                    p if recipe.facet else "",
                                    _x_text(x, is_date),
                                )
                            )
    return list(dict.fromkeys(out))[:ERROR_MISSING_MAX]


def candidate_facts(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> dict | None:
    """这张图是按什么数出来的——给界面那句「依据」用的**数**（只有数与列名，文案在前端按封闭句式拼）：
    `{rows: 表里的行数, drawn: 画进去的行数, groups: 组数 | None, n_min / n_max: 每组（每个类别 × 分组 × 分面格子）的观测数范围 | None,
    x_values: 不同横轴取值的个数 | None, skipped_missing: 因为缺值（某一格是空的 / NaN）没画的行数, skipped_other: 因为那一格不是数（文字）没画的行数}`。
    两个 skipped 加起来就是 `unplotted_rows` 的总数。大表不逐行数（None）；把列当分组的也先不给。"""
    if values.large is not None or recipe.stack or _multi_y_trend(recipe):
        # 多个 y 的趋势图每条线各有各的有效值：不输出一个「第一条线的数」冒充整张图的依据，改走 `series_facts`
        return None
    # 「画进去的行」与出图用同一套判据：先套用户选的处理规则（「取数字部分」取出了数的格子算数），再去掉 `unplotted_rows` 里的行
    # （横轴不是值、分组 / 分面 / 个体空、纵轴空……），剩下的行里纵轴是数的才是一个画出来的值（折线的纵轴空格是断开，不算画进去）
    applied = recipe_mod.with_cells_applied(values, recipe) if recipe.cells else values
    idx = {c: i for i, c in enumerate(values.columns)}
    y = recipe.y[0]
    skipped = unplotted_rows(values, recipe)
    # 行的身份是（来自哪一份, 该份的行号）：合画时不同文件的同一行号是不同的行
    gone = {(item.get("file"), item["row"]) for item in skipped}
    usable = [
        r
        for n, r in enumerate(applied.rows)
        if (values.row_source(n), values.row_number(n)) not in gone
        and adf.parse_cell(r[idx[y]])[0] == "numeric"
    ]
    out: dict = {
        "rows": len(values.rows),
        "drawn": len(usable),
        "groups": None,
        "n_min": None,
        "n_max": None,
        "x_values": None,
        "skipped_missing": 0,
        "skipped_other": 0,
    }
    out["skipped_missing"], out["skipped_other"] = _skipped_split(values, skipped, idx)
    by = [c for c in (recipe.group, recipe.facet) if c]
    if recipe.family == "comparison" and recipe.x:
        by = [recipe.x, *by]
    if by:
        cells: dict[tuple, int] = {}
        for r in usable:
            key = tuple(r[idx[c]].strip() for c in by)
            cells[key] = cells.get(key, 0) + 1
        if cells:
            out["groups"] = len(cells)
            out["n_min"], out["n_max"] = min(cells.values()), max(cells.values())
    if recipe.family in ("trend", "relationship") and recipe.x:
        out["x_values"] = len(
            {r[idx[recipe.x]].strip() for r in usable if r[idx[recipe.x]].strip()}
        )
    return out


def _multi_y_trend(recipe: recipe_mod.FigureRecipe) -> bool:
    """`candidate_facts`（旧、按第一个 y）与 `series_facts`（按每个 y）的分界：只有「多个 y 的趋势图」改走新的；别的族（区间 / 构成 / 矩阵……）照旧。"""
    return recipe.family == "trend" and bool(recipe.x) and len(recipe.y) > 1


def _skipped_split(values: adf.DataSummary, skipped: list[dict], idx: dict) -> tuple[int, int]:
    """没画的行拆成 `(缺值, 不是数)` 两个数（`candidate_facts` 与 `series_facts` 共用）：缺值 = 那一格是空的或 NaN；其余（横轴 / 纵轴那一格是文字）算「不是数」。"""
    row_at = {(values.row_source(n), values.row_number(n)): n for n in range(len(values.rows))}
    missing_n = other_n = 0
    for item in skipped:
        cell = ""
        n = row_at.get((item.get("file"), item["row"]))
        if n is not None and item["column"] in idx:
            cell = values.rows[n][idx[item["column"]]]
        missing = item["reason"] != "not_a_number" and adf.parse_cell(cell)[0] != "text"
        if missing:
            missing_n += 1
        else:
            other_n += 1
    return missing_n, other_n


def series_facts(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> dict | None:
    """多个 y 的趋势图（小表、非 stack）：**每条线各自**按什么数画的（ADR 0178 修订 2026-11-27）。统计来自出图用的同一个数据准备函数
    （`tpl.trend_data`：逐行各调一次，行的身份就是它在表里的位置，合画时不同文件的同一行号不会撞），不另写一套解析规则：
    `{rows: 整表行数, eligible_rows: 公共角色（横轴 / 分组 / 分面 / 个体）都有效的行数, rows_any: 至少一个 y 有效的行数, rows_all: 所有 y 都有效的行数,
    skipped_missing / skipped_other: 同 `facts`,
    series: [{y, n: 有效值个数, x_values: 有效值所在的不同横轴取值个数（按解析后的数值 / 日期去重）,
    err_drawn / err_missing: 只有配了现成误差列才有——有中心值且有可画误差（0 也算）/ 有中心值但误差缺失（空、NaN、文字、负数）的个数}]}`，
    与 `recipe.y` 同序。有中心值才有误差线，没中心值的误差格不计。单个 y、大表、stack = None。"""
    if values.large is not None or recipe.stack or not _multi_y_trend(recipe):
        return None
    applied = recipe_mod.with_cells_applied(values, recipe)
    is_date = values.per_column[recipe.x].kind == "date"
    ys = list(recipe.y)
    err = list(recipe.err_cols) or None
    n_valid = dict.fromkeys(ys, 0)
    xs_of: dict[str, set] = {y: set() for y in ys}
    drawn = dict.fromkeys(ys, 0)
    gap = dict.fromkeys(ys, 0)
    eligible = any_ = all_ = 0
    for row in applied.rows:
        d = tpl.trend_data(
            applied.columns,
            [row],
            recipe.x,
            ys,
            recipe.group,
            recipe.facet,
            recipe.unit,
            is_date,
            err,
        )
        if not d["series"]:  # 公共角色无效：这一行没有任何点
            continue
        eligible += 1
        ok = 0
        for (_p, _g, y, _u), (xs, vals) in d["series"].items():
            if vals[0] == vals[0]:
                ok += 1
                n_valid[y] += 1
                xs_of[y].add(xs[0])
        for (_p, _g, y), (_xs, _vs, errs) in d.get(
            "errs", {}
        ).items():  # 只有中心值有数的行才进 `errs`
            if errs[0] == errs[0]:
                drawn[y] += 1
            else:
                gap[y] += 1
        any_ += ok > 0
        all_ += ok == len(ys)
    series = []
    for y in ys:
        item = {"y": y, "n": n_valid[y], "x_values": len(xs_of[y])}
        if err:
            item["err_drawn"], item["err_missing"] = drawn[y], gap[y]
        series.append(item)
    idx = {c: i for i, c in enumerate(values.columns)}
    missing_n, other_n = _skipped_split(values, unplotted_rows(values, recipe), idx)
    return {
        "rows": len(values.rows),
        "eligible_rows": eligible,
        "rows_any": any_,
        "rows_all": all_,
        "skipped_missing": missing_n,
        "skipped_other": other_n,
        "series": series,
    }


def figure_style(style: dict) -> dict:
    out = {k: style[k] for k in ("width_single_mm", "width_double_mm", "font_pt")}
    lo, hi = style.get("font_range") or (None, None)
    if lo is not None:
        out["font_min_pt"] = lo
    if hi is not None:
        out["font_max_pt"] = hi
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
            if key == "y" and isinstance(answers[key], list):
                # 多列纵轴（读懂数据 / 用户点名）：1 到 `STACK_MAX_COLUMNS` 个列名（有有序横轴时每列一条线，至多 `MULTI_Y_MAX` 个；没有横轴时是
                # 「把这几列当分组」，至多 `STACK_MAX_COLUMNS` 个）；形状不对 = 错误，取值合不合格由列角色规则判（不合格当没给）
                raw = answers[key]
                if not 1 <= len(raw) <= max(recipe_mod.MULTI_Y_MAX, recipe_mod.STACK_MAX_COLUMNS):
                    raise ProposeError("propose_input_invalid", key)
                names = [_text(c, key) for c in raw]
                if any(c is None for c in names):
                    raise ProposeError("propose_input_invalid", key)
                out[key] = names[0] if len(names) == 1 else names
                continue
            val = _text(answers[key], key)
            if key == "purpose" and val not in purpose_mod.PURPOSES:
                continue
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
    if "x_measure" in answers:
        picks["x_measure"] = answers["x_measure"]
    if design == "repeated":
        return recipe_mod.Confirmed(
            "repeated", answers.get("unit") if "unit" in answers else None
        ), picks
    if design == "independent":
        return recipe_mod.Confirmed("independent"), picks
    return recipe_mod.Confirmed(), picks


def unplotted_unmet(rows: list[dict]) -> int:
    """没画的行里「有数据却没画上」的有多少（`goal.rows_plotted` 看这个数）：纵轴空着的行（`y_missing`）没有可画的点，是缺失，不算。
    大表的清单只有前 100 个样例、不逐行分原因，这个数由缩减那一遍单独记（`Examples.unmet`）。"""
    unmet = getattr(rows, "unmet", None)
    return unmet if unmet is not None else sum(1 for r in rows if r.get("reason") != "y_missing")


class Examples(list):
    """大表上「没画的行」只给前 100 个例子；`total` 是精确的总数（来自概览扫描）。小表直接是普通列表（`total` 同长度）。"""

    total: int = 0
    #: 「有数据却没画上」的行数（见 `unplotted_unmet`）；None = 没单独记，按每行的原因数
    unmet: int | None = None


def unplotted_total(rows: list[dict]) -> int:
    return getattr(rows, "total", len(rows))


def unplotted_rows(values: adf.DataSummary, recipe) -> list[dict]:
    """配方画不出来的行（`row` 是文件里的行号，表头算第 1 行，XLSX 是表内行号）。**脚本里每一处跳过一行的地方，这里都有对应的一条**
    （sciviz G-055「没画的观测要有计数和规则」；每行只记一个原因）：

    * `x_not_a_value`：折线 / 散点的横轴没有值（空 / NaN / 不是数或日期），画不到坐标里；
    * `category_empty`：比较类的类别那一格是空的；
    * `group_empty` / `facet_empty` / `unit_empty`：分组 / 分面 / 个体那一格是空的，归不到任何一组里；
    * `not_a_number`：纵轴那一格是文字（用户还没选处理方式的）；
    * `y_missing`：比较类 / 分布类 / 散点类的纵轴是空的或 NaN——这一行没有可画的点。

    折线的纵轴空 / NaN 是缺失（线在那里断开，看得见），不算没画；夹着文字格的数值列用户已选了处理方式（`recipe.cells`）的，
    那些格子是用户明确决定过的，不在这里报（统计在 `cell_stats`）。大表只给总数与前 100 个行号，不逐行分原因（全都是横轴不是值记 `x_not_a_value`，全都是纵轴空着记 `y_missing`，混着的记 `row_not_plotted`；「有数据却没画上」的行数另记在 `Examples.unmet`）。"""
    if (
        recipe.stack
    ):  # 把列当分组：被转的列都是纯数值列，空格 / NaN 是缺失（不是「没画」），没有横轴
        return []
    if values.large is not None:
        total, numbers, counts = tpl.large_unplotted(values, recipe)
        if total == counts["x"]:
            why, col = "x_not_a_value", recipe.x
        elif total == counts["y"]:
            why, col = "y_missing", recipe.y[0]
        else:
            why, col = "row_not_plotted", recipe.x
        ex = Examples({"row": n, "column": col, "reason": why} for n in numbers)
        ex.total = total
        # 「有数据却没画上」的行数（横轴不是值、分组 / 分面 / 个体空）：纵轴空着的是缺失，不算（与小表同一口径）
        ex.unmet = counts["x"] + counts["key"]
        return ex
    out: list[dict] = []
    idx = {c: i for i, c in enumerate(values.columns)}
    x = recipe.x if recipe.family in ("trend", "relationship") else None
    category = recipe.x if recipe.family == "comparison" else None
    keys = [
        (category, "category_empty"),
        (recipe.group, "group_empty"),
        (recipe.facet, "facet_empty"),
    ]
    if recipe.family == "trend":
        keys.append((recipe.unit, "unit_empty"))
    keys = [(c, why) for c, why in keys if c and c in idx]
    # 比较 / 分布（都走 `comparison_data`）与散点：一行一个点，纵轴没有数就没有这个点；折线的缺失是断开，不在此列
    point_needs_y = recipe.family != "trend"
    covered = {c.column for c in recipe.cells}
    # 横轴列有处理规则时按**规则作用之后**的格子判：「取数字部分」取出了数的行画得出来，不算；「不画那几行」之后是空格，算没画
    applied = (
        adf.apply_cells(values.columns, values.rows, [c.args() for c in recipe.cells])[0]
        if x in covered
        else values.rows
    )
    for n, row in enumerate(values.rows):
        if x is not None:
            cell = applied[n][idx[x]]
            if values.per_column[x].kind == "date":
                ok = adf.parse_date(cell) is not None
            else:
                ok = adf.parse_cell(cell)[0] == "numeric"
            if not ok:
                out.append(_unplotted(values, n, x, "x_not_a_value"))
                continue
        empty = next(((c, why) for c, why in keys if not row[idx[c]].strip()), None)
        if empty is not None:
            out.append(_unplotted(values, n, *empty))
            continue
        for y in recipe.y:
            if y in covered:
                continue
            kind = adf.parse_cell(row[idx[y]])[0]
            if kind == "text":
                out.append(_unplotted(values, n, y, "not_a_number"))
                break  # 每行只记一条：同一行多个 y 都是文字时记第一个，条数 = 行数（不是文字格数）
            elif kind != "numeric" and point_needs_y:
                out.append(_unplotted(values, n, y, "y_missing"))
                break
    return out


def _unplotted(values: adf.DataSummary, n: int, column: str, reason: str) -> dict:
    item = {"row": values.row_number(n), "column": column, "reason": reason}
    src = values.row_source(n)
    if src is not None:  # 合画：行号是那一份自己的文件行号，要带上是哪一份
        item["file"] = src
    return item


def reduced_of(values: adf.DataSummary, recipe) -> dict | None:
    """大表：`{"rows": 整表行数, "kept": 缩减后画出的行数}`（缩减结果在 `large_reduce` 的缓存里，不再读文件）；小表 None。"""
    if values.large is None:
        return None
    _rows, info = tpl.large_reduce(values, recipe)
    return {"rows": info["rows"], "kept": info["kept"]}


def cell_stats(values: adf.DataSummary, recipe) -> list[dict]:
    """处理规则实际作用了多少格（`[{column, mode, ignored, extracted, mapped}]`），统计与脚本里套规则用的是同一个 `adf.apply_cells`。"""
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


def _stage_table(sdir: Path, source: Path, table_rel: str, tracked: bool = False) -> None:
    """把选中的那一张数据文件的**字节副本**放进候选目录（布局与将来落盘后相同）。只复制不链接；源文件只读。"""
    dest = sdir.joinpath(*PurePosixPath(normalize_rel(table_rel)).parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if tracked and progress.active():
        # 大表副本按块复制：每块记字节进度并查取消（`shutil.copyfile` 一口气拷 500 MB 不能中途停）。取消 / 出错由调用方删整个会话目录
        total = source.stat().st_size
        done = 0
        with source.open("rb") as src, dest.open("wb") as out:
            for chunk in iter(lambda: src.read(8 * 1024 * 1024), b""):
                out.write(chunk)
                done += len(chunk)
                progress.tick("stage", done, total)
        shutil.copystat(source, dest)
        return
    shutil.copyfile(source, dest)


@dataclass
class _Planned:
    """一个备选的渲染素材（脚本与公共模块已写进候选目录）；`bad` 非空 = 模板不认这个配方，不渲染。"""

    n: int
    stem: str
    recipe: recipe_mod.FigureRecipe
    bad: str | None
    expected: list


def _plan_candidates(
    sdir: Path,
    entry: adf.TableEntry,
    values: adf.DataSummary,
    recipes: list[recipe_mod.FigureRecipe],
    style: dict | None = None,
) -> list[_Planned]:
    """每个备选出脚本 + 公共模块写进候选目录、算期望指纹。**不渲染**（便宜，毫秒级）。"""
    figures = sdir / "tavotto_figures"
    figures.mkdir(parents=True, exist_ok=True)
    data_rel = script_data_rel(entry.path)
    plan: list[_Planned] = []
    if values.large is not None:
        progress.set_passes("reduce", max(1, tpl.large_reduce_pending(values, recipes)))
    for n, recipe in enumerate(recipes, start=1):
        stem = f"cand_{n}"
        try:
            rendered = tpl.render_recipe_script(
                entry,
                recipe,
                data_rel=data_rel,
                stem=stem,
                style=style,
                fits=tpl.fit_report(values, recipe),
            )
            expected = tpl.expected_fingerprints(values, recipe)
        except tpl.TemplateError:
            plan.append(_Planned(n, stem, recipe, "candidate_template_unsupported", []))
            continue
        except tpl.LargeRuleError as exc:  # 大表：整表流过才知道的资格判据没过（折线的 x 重复 / 没排序等），码与小表上同一条规则一致
            plan.append(_Planned(n, stem, recipe, exc.code, []))
            continue
        (figures / rendered.runtime_name).write_bytes(rendered.runtime)
        (figures / f"{stem}.py").write_text(rendered.script, encoding="utf-8")
        plan.append(_Planned(n, stem, recipe, None, expected))
    return plan


def _render_planned(
    sdir: Path, stem: str, n: int, expected: list, pin=None
) -> tuple[str | None, Path | None, bool]:
    """画一个备选 → `(剔除原因 | None, 缩略图, 是否回退过解释器)`：真渲染 + 数据指纹对拍（画出来的数据与文件对不上就剔除）。"""
    out = _render_one(sdir, f"tavotto_figures/{stem}.py", stem, sdir / "thumbs" / f"{n}.png", pin)
    reason = out.reason
    if reason is None and out.manifest is not None:
        checks = figureverify.data_matches_expected(out.manifest, expected, stage="precommit")
        if any(c.verdict == "failed" for c in checks):
            reason = "candidate_fingerprint_mismatch"
    return reason, out.png, out.fallback


def _render_candidates(
    sdir: Path,
    entry: adf.TableEntry,
    values: adf.DataSummary,
    recipes: list[recipe_mod.FigureRecipe],
    pin=None,
    style: dict | None = None,
) -> list[tuple[recipe_mod.FigureRecipe, dict | None, str | None, Path | None, bool]]:
    """每个备选 → `(配方, 覆盖情况, 剔除原因 | None, 缩略图, 是否回退过解释器)`。覆盖情况 = `fingerprint_coverage`；剔除原因在 `DROP_REASONS` 里。"""
    plan = _plan_candidates(sdir, entry, values, recipes, style)

    def work(p: _Planned):
        if p.bad:
            return p.recipe, None, p.bad, None, False
        reason, png, fb = _render_planned(sdir, p.stem, p.n, p.expected, pin)
        return p.recipe, tpl.fingerprint_coverage(p.recipe).to_dict(), reason, png, fb

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        return list(ex.map(work, plan))


class _PriorityGate:
    """限流的闸：同时最多 `slots` 个在里面；空位出现时，等着的里**序号最小的先进**（同序号先到先进）。
    懒渲染用它让推荐那张（序号 1）永远排在前面，不被同时发出的别的取图抢走空位。"""

    def __init__(self, slots: int):
        self._cv = threading.Condition()
        self._free = slots
        self._waiting: list[tuple[int, int]] = []
        self._seq = 0

    def __call__(self, priority: int):
        return _GateSlot(self, priority)

    def acquire(self, priority: int) -> None:
        with self._cv:
            self._seq += 1
            me = (priority, self._seq)
            self._waiting.append(me)
            while not (self._free > 0 and min(self._waiting) == me):
                self._cv.wait()
            self._waiting.remove(me)
            self._free -= 1
            self._cv.notify_all()

    def release(self) -> None:
        with self._cv:
            self._free += 1
            self._cv.notify_all()


class _GateSlot:
    def __init__(self, gate: _PriorityGate, priority: int):
        self._gate, self._priority = gate, priority

    def __enter__(self):
        self._gate.acquire(self._priority)

    def __exit__(self, *exc):
        self._gate.release()


#: 懒渲染时全进程同时在画的缩略图上限（取图请求各占一条 Flask 线程；超出的排队，推荐那张优先）
_RENDER_SLOTS = _PriorityGate(MAX_PARALLEL)


def ensure_thumbnail(project: str, session_id: str, item_id: str) -> Path:
    """取图（懒渲染的唯一画图入口）：该项还没画就在这里画并等它画完；同一项并发取图只画一次（项上的锁），后来的等到状态落定。
    画失败 → `ProposeError("propose_thumbnail_failed", params={"reason": 码})`（结果记在项上，再取不重画）。
    已画好（含旧模式出的）直接回路径。"""
    sess, item = get_item(project, session_id, item_id)
    if item.state == "pending":
        with item.lock:
            if item.state == "pending":
                if sess.large:
                    progress.enter(
                        "render"
                    )  # worker 里读 + 缩减 + 画图：没有字节进度，如实标「不确定」
                _finish_lazy_item(sess, item)
    if item.state == "failed":
        raise ProposeError("propose_thumbnail_failed", params={"reason": item.reason})
    return thumbnail_path(project, session_id, item_id)


def _finish_lazy_item(sess: Session, item: Item) -> None:
    with _RENDER_SLOTS(item.n):
        pin = _project_pin(sess.root) if sess.root is not None else None
        reason, png, fb = "candidate_render_failed", None, False
        # 失败结果会记在项上、不再重画，所以 `candidate_render_failed` 这种可能是瞬时的（池在内存吃紧时回收了刚起的 worker）失败先重试一次；
        # 超时 / 写入被拦 / 指纹对不上是确定的结论，不重试
        for _attempt in range(2):
            try:
                reason, png, fb = _render_planned(
                    sess.dir, item.stem or "", item.n, item.expected, pin
                )
            except Exception:  # noqa: BLE001 —— 任何意外都只是这一张画不出
                LOG.info("数据出图候选懒渲染意外失败", exc_info=True)
                reason, png, fb = "candidate_render_failed", None, False
            if reason != "candidate_render_failed":
                break
    item.fallback = fb
    if reason is None and png is not None:
        item.thumb = png
        item.state = "ready"
    else:
        item.reason = reason or "candidate_render_failed"
        item.state = "failed"
        LOG.info(
            "数据出图候选缩略图失败：%s（%s）", item.reason, item.code
        )  # 只记稳定码与画法码，不记列名 / 路径


# =========================================================================== 出备选
def with_explicit_table(
    profile: adf.FolderProfile,
    folder: Path,
    table: str | None,
    sheet: str | None,
    header_rows: dict[str, int] | None = None,
) -> adf.FolderProfile:
    """用户**显式选中**的表（含指定工作表）不受发现预算（`MAX_PROFILE_TABLES`）限制：不在截断后的 profile 里就单独读一份摘要并补进去。
    只认发现遍历实际列得出的文件（所选数据文件夹内、链接不跟随、隐藏项与剪枝目录不读、表格后缀）、只读。
    不满足就原样返回（随后 `pick_table` 报 `table_not_found`）；文件本身读不了抛 `DataSourceError`（稳定码）。"""
    if not table:
        return profile
    if any(t.path == table and (sheet is None or t.sheet == sheet) for t in profile.tables):
        return profile
    # 不拿请求里的字符串拼路径：用发现时的同一次遍历（`discover.walk_files`：隐藏 / `PRUNE_DIRS` 剪枝、不进链接目录、
    # 不列链接文件）把文件夹里的表列出来，只认其中**逐字相等**的那一项，路径取自遍历结果。于是规则与发现天然一致，
    # 文件夹外、链接、隐藏项、非表格后缀都落不进来。
    found, _ = discover.walk_files(folder, adf.TABLE_SUFFIXES, scan_max=adf.PROFILE_SCAN_MAX)
    rel = next(
        (f[0] for f in found if f[0] == table and not Path(f[0]).name.startswith("~$")), None
    )
    if rel is None:
        return profile
    values = adf.inspect_data_source(
        folder / PurePosixPath(rel),
        sheet,
        v2=True,
        header_row=(header_rows or {}).get(rel),
        allow_large=True,
    )
    summary = replace(
        values,
        rows=[],
        row_numbers=[],
        row_count=values.row_count if values.large is not None else len(values.rows),
    )
    entry = adf.TableEntry(rel, values.sheet, False, summary)
    return adf.FolderProfile(
        (*profile.tables, entry), profile.skipped, profile.truncated, profile.truncated_reason
    )


def _table_for_values(root_folder: Path, entry: adf.TableEntry) -> adf.DataSummary:
    # 同一读法：表头行取摘要里实际用的那一行（预览 / 资格判定 / 脚本读出来的是同一张表）
    if entry.summary.large is not None:
        return entry.summary  # 大表：概览那一遍扫描就是整表摘要（没有行数据），不再整份扫第二遍
    return adf.inspect_data_source(
        root_folder / PurePosixPath(entry.path),
        entry.sheet,
        v2=True,
        header_row=entry.summary.header_line,
        allow_large=True,
    )


def _recipe_key(rec: recipe_mod.FigureRecipe) -> str:
    return json.dumps({k: v for k, v in rec.to_dict().items() if k != "why"}, sort_keys=True)


def _with_file_only_variants(
    res, merged, values, confirmed, hint, picks, rules
) -> tuple[recipe_mod.Candidates, dict[int, dict]]:
    """合画且文件里**原本就有**在文件内取多个值的分组列（`MergeSpec.splittable`）：除了现状的画法（按文件上色、再按那一列拆开 / 分面），
    再给一份**只按文件比较**的（把那些列当不存在重新出一遍备选）。两份各自走完整的 `default_candidates`（同一套校验与排序），这里只负责并起来：
    现状的顺序不变（推荐仍是它的第一张），每张后面紧跟它只按文件比较的那一张；两种画法画出来一样的（配方除 `why` 外逐项相同）只留一张、不加标记。
    返回 `(并好的 Candidates, {id(配方): 标记})`。"""
    if any(c.recipe.err is not None for c in res.items):
        # 已汇总的表（每个位置一行 + 现成误差列）：「只按文件比较」会对各组的汇总值再算一次均值 ± 误差 / 分布（G-053），不给这一对
        return res, {}
    keep = {k: n for k, n in merged.summary.groups.items() if k not in merged.merge.splittable}
    entry_b = replace(merged, summary=replace(merged.summary, groups=keep))
    res_b = recipe_mod.default_candidates(
        adf.FolderProfile((entry_b,), (), False, None),
        table=entry_b.path,
        sheet=entry_b.sheet,
        confirmed=confirmed,
        hint=hint,
        values=replace(values, groups=keep),
        picks=picks,
        cells=rules,
    )
    if not isinstance(res_b, recipe_mod.Candidates) or not res_b.items:
        return res, {}
    seen = {_recipe_key(c.recipe) for c in res.items}
    only_b = [c for c in res_b.items if _recipe_key(c.recipe) not in seen]
    by_code = {c.code: c for c in only_b}
    column = merged.merge.splittable[0]
    out: list[recipe_mod.Candidate] = []
    marks: dict[int, dict] = {}
    used: set[str] = set()
    for cand in res.items:
        out.append(cand)
        twin = by_code.get(cand.code)
        if twin is not None:
            marks[id(cand.recipe)] = {"kind": "split", "column": column}
            marks[id(twin.recipe)] = {"kind": "by_file"}
            out.append(twin)
            used.add(cand.code)
    out += [
        c for c in only_b if c.code not in used
    ]  # 只有「只按文件」才画得出来的（现状那一边被剔除了）排在最后
    return replace(res, items=tuple(out)), marks


def _read_merged(
    root: Path,
    folder_path: Path,
    folder_rel: str,
    tables: list[tuple[str, str | None]],
    hrows: dict[str, int],
) -> tuple[merge_mod.MergedEntry, adf.DataSummary, Path, str] | str:
    """合画：逐份按单份的读法读（用户指定的表头行按文件各管各的）→ 要求列名集合与顺序一致 → 拼成一张逻辑表。
    返回 `(拼好的条目, 带行数据的摘要, 落点目录, 落点的项目相对路径)`；某份不是所选文件夹里发现遍历认得的表 → 原因码 `table_not_found`
    （与单份指名一张不存在的表同一形状）；读不了 / 没认准表头沿用单份的 `DataSourceError`；列名不一致 `tables_columns_differ`；
    合计字节超 `MAX_TOTAL_BYTES` 沿用 `data_too_large`。只读，不往任何地方写。"""
    # 与 `with_explicit_table` 同一条规则：只认发现遍历实际列得出的文件，路径取自遍历结果（隐藏 / 剪枝 / 链接 / 非表格后缀都落不进来）
    found, _ = discover.walk_files(folder_path, adf.TABLE_SUFFIXES, scan_max=adf.PROFILE_SCAN_MAX)
    known = {f[0] for f in found if not Path(f[0]).name.startswith("~$")}
    rels: list[str] = []
    summaries: list[adf.DataSummary] = []
    seen: set[tuple[str, str | None]] = set()
    total = 0
    for path, sheet in tables:
        try:
            rel = normalize_rel(path)
        except ValueError:
            raise adf.DataSourceError("data_path_outside_project") from None
        if rel not in known:
            return "table_not_found"
        values = adf.inspect_data_source(
            folder_path / PurePosixPath(rel), sheet, v2=True, header_row=hrows.get(rel)
        )
        if (rel, values.sheet) in seen:  # 同一份（同文件同工作表）给了两次
            raise ProposeError("propose_input_invalid", "tables")
        if not any(
            r == rel for r in rels
        ):  # 同一个工作簿的几张表只算一次（候选目录里也只有一份副本）
            total += values.size
        seen.add((rel, values.sheet))
        if total > merge_mod.MAX_TOTAL_BYTES:
            raise adf.DataSourceError("data_too_large", str(total))
        rels.append(rel)
        summaries.append(values)
    try:
        merge_mod.check_same_columns(summaries)
        landing = merge_mod.common_parent(rels)
        labels = merge_mod.unique_labels([(r, v.sheet) for r, v in zip(rels, summaries)])
        sources = [
            merge_mod.Source(
                path=r,
                rel=merge_mod.rebase(r, landing),
                sheet=v.sheet,
                label=lab,
                sha256=v.sha256,
                size=v.size,
                header_line=v.header_line,
            )
            for r, v, lab in zip(rels, summaries, labels, strict=True)
        ]
        entry, values = merge_mod.build(sources, summaries, landing)
    except merge_mod.MergeError as exc:
        raise ProposeError(exc.code, params=exc.params) from None
    landing_rel = "/".join(p for p in (folder_rel, landing) if p)
    real = projectenv.contained_path(root, landing_rel)
    if real is None or not Path(real).is_dir():
        raise adf.DataSourceError("data_path_outside_project")
    return entry, values, Path(real), landing_rel


def item_to_dict(sess: Session, item: Item, recommended: bool) -> dict:
    r = item.recipe
    return {
        "id": item.id,
        "title_code": item.code,
        "why_code": item.code,
        "values": {
            "table": sess.merge.sources[0].path if sess.merge is not None else r.table,
            "sheet": r.sheet,
            "family": r.family,
            # 把列当分组：`y` 是被当分组的那几列（与响应里 `roles.y` 一致），配方里转换后的值 / 类别列是内部的，不对外；多一项 `stack` 标明
            "x": None if r.stack else r.x,
            "y": list(r.stack or r.y),
            "group": None if r.stack else r.group,
            "facet": r.facet,
            "unit": r.unit,
            "layers": [layer.to_dict() for layer in r.layers],
            **({"stack": list(r.stack)} if r.stack else {}),
        },
        "recommended": recommended,
        "coverage": item.coverage,
        "cells": item.cells,
        "unplotted": {
            "count": item.unplotted,
            "code": UNPLOTTED_CODE if item.unplotted else None,
        },
        "thumbnail_url": f"/api/ai/data-figure/thumbnail/{sess.id}/{item.id}",
        "thumbnail_status": item.state,
        **({"reduced": item.reduced} if item.reduced is not None else {}),
        # 有拟合线才有：每条线的截距 / 斜率 / n（描述性的最小二乘直线；对数轴那一维是 log10）
        **({"fits": item.fits} if item.fits else {}),
        **({"facts": item.facts} if item.facts is not None else {}),
        **({"series_facts": item.series_facts} if item.series_facts is not None else {}),
        # 有误差线 / 误差带才有：画的是哪一种（`sd` / `sem` / `ci95`）
        **({"error": error_of(r)} if error_of(r) is not None else {}),
        # 误差线从哪来（`column` 现成的误差列 / `computed` 用重复值算的）；取自列的另带列名与「列名自己说明了是哪一种」
        **({"error_source": error_source(r)} if error_source(r) is not None else {}),
        **({"error_column": r.err} if isinstance(r.err, str) else {}),
        **({"error_columns": item.error_columns} if item.error_columns else {}),
        **({"error_stated": True} if item.error_stated else {}),
        # 算出来的误差线 / 带里 n < 2 因而没有误差线的位置（标签与图上一致）
        **({"error_missing_for": item.error_missing} if item.error_missing else {}),
        **({"variant": item.variant} if sess.merge is not None and sess.merge.splittable else {}),
        **({"why_not": item.why_not} if item.why_not is not None and not recommended else {}),
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
    header_rows=None,
    client_request_id: str | None = None,
    render: str | None = None,
    tables=None,
    spec=None,
    font_range=None,
    understand=None,
) -> dict:
    """出备选。返回三选一：`{"kind": "ask", question_code, options, unsure}` / `{"kind": "candidates", session, items, dropped}` /
    `{"kind": "nothing", reasons}`。数据读不了（文件夹不存在 / 不在项目内）抛 `DataSourceError`（app 转 400 + 稳定码）。

    `render`：不给 / `"eager"` = 旧行为（缩略图在这一次里画完，渲染失败的备选不进列表，可能返回 `ask`）；`"lazy"` = **文字先到**：
    不渲染，每项 `thumbnail_status: "pending"`，缩略图在取图（`ensure_thumbnail`）时画；**永不返回 `ask`**——每个问题都用它的默认答案继续
    （选表 = 第一张、列角色 = 各问题的「不确定」默认、夹着文字格的数值列 = 不画那几行、表头行 = 读表猜的那一行），响应里说清用了什么
    （`defaults` / `header_row` / `roles` / `mixed_cells`），用户在面板上改了就把改动放进 `answers`（`y` / `x` / `group` 的列名或 `none`、
    `cells`）与 `header_rows` 重来。

    `tables`（合画，`[{path, sheet?}]`，2 到 `merge_mod.MAX_TABLES` 份；与 `table` 互斥）：几份列名相同的数据拼成一张逻辑表（`ai_data_merge`），
    加一列合成的分组列（值是各份的文件标签），分组角色默认就是这一列；响应另有 `sources`（各份的路径 / 工作表 / 标签）与 `group_column`；
    单份请求的响应逐字不变。

    **不往用户文件夹写任何东西**（候选目录在 `data_dir()/cache/`，见模块说明）；幂等：同一 `(项目, client_request_id)` 在 TTL 内回同一份响应。

    `spec`（可选）：前端按画布绑定解析好的出版规范（期刊覆盖已并入；快照优先那套取舍在前端 `resolveDocumentSpec`，这里不重做）。
    过 `profiles.validate_spec` 后只取模板要的数字（`tpl.style_from_profile`）；不给 = 默认规范，响应逐字不变。规范不合法**不让出图失败**：
    退回默认规范，响应带 `spec_fallback: true`。规范有效时响应带 `figure_style`（实际用的栏宽、起始字号，以及接受了的期刊字号范围 `font_min_pt` / `font_max_pt`）。
    `font_range`（可选，`{min_pt?, max_pt?}`，随 `spec` 一起才有意义）：期刊自己规定的字号上下限，读法见 `tpl.style_from_profile`。规范进幂等键：换了期刊必须重新出图。只在本机用，不进给模型的内容。

    `understand`（可选，布尔）：给 `False` = **不起「读懂数据」的后台任务**（响应里没有 `understanding` 键，不会有一次模型出站）；候选逐字不变。
    不给 / `True` = 与从前逐字相同。给「点选误差线换类型」这类**不是用户在看数据**的重出用：那一次没人在等模型的判断，不该悄悄多发一次。
    非布尔 = `propose_input_invalid`。进幂等键（有无 `understanding` 键是响应的一部分）。"""
    sweep()
    progress.check()
    if render is not None and render not in RENDER_MODES:
        raise ProposeError("propose_input_invalid", "render")
    auto = render == "lazy"
    if understand is not None and not isinstance(understand, bool):
        raise ProposeError("propose_input_invalid", "understand")
    folder = _text(folder, "folder", 1024)
    hint = _text(hint, "hint", _HINT_MAX)
    table = _text(table, "table")
    sheet = _text(sheet, "sheet")
    ans = parse_answers(answers)
    hrows = parse_header_rows(header_rows)
    user_took_over = _user_took_over(
        ans
    )  # 懒模式的循环会往 `ans` 里补默认答案，所以在它之前记下请求原本带没带
    tabs = parse_tables(tables)
    style, spec_digest, spec_bad = parse_spec(spec, font_range)
    if tabs is not None and table is not None:  # `table` 与 `tables` 只能给一个
        raise ProposeError("propose_input_invalid", "tables")
    # 幂等键 = (项目, 请求号, 请求内容的摘要)：同一请求号带了不同的回答（换了处理方式）必须重新出图，不回旧的
    digest_parts = [folder, table, sheet, hint, _answers_digest(ans), hrows]
    if auto:
        digest_parts.append("lazy")
    if tabs is not None:
        digest_parts.append(["tables", tabs])
    if understand is False:
        digest_parts.append("no_understanding")
    if spec_digest is not None:
        digest_parts.append(["spec", spec_digest])
    digest = hashlib.sha256(
        json.dumps(digest_parts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if client_request_id is not None:
        with _LOCK:
            hit = _REQUESTS.get((project, client_request_id, digest))
        if hit is not None and (hit[2] is None or hit[2] in _SESSIONS):
            return hit[1]

    def remember(response: dict, session_id: str | None = None) -> dict:
        return _remember(project, client_request_id, response, session_id, digest)

    if tabs is None:
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
    merged: merge_mod.MergedEntry | None = None
    merged_values: adf.DataSummary | None = None
    if tabs is not None:
        got = _read_merged(root, folder_path, folder_rel, tabs, hrows)
        if isinstance(got, str):
            return remember({"kind": "nothing", "reasons": [got], "dropped": []})
        merged, merged_values, folder_path, folder_rel = got  # 落点目录成了这一次的「数据文件夹」
        profile = adf.FolderProfile((merged,), (), False, None)
        table, sheet = merged.path, merged.sheet
    else:
        folder_profile = adf.profile_folder(folder_path, hrows)
        for bad in folder_profile.skipped:
            if (
                bad.path in hrows
            ):  # 用户指定了表头行、按它读不出来：直说原因（不是悄悄「没有可画的」）
                raise adf.DataSourceError(bad.code)
        profile = with_explicit_table(folder_profile, folder_path, table, sheet, hrows)
    rules: dict[str, recipe_mod.CellRule] = dict(ans.get("cells") or {})
    defaults: list[dict] = []
    mixed_auto: list[dict] = []
    values: adf.DataSummary | None = None
    values_key: tuple | None = None
    res = None
    chosen = None
    # 合画：分组角色默认是合成的那一列（用户在右栏换了 / 选「无」就用他的；这一列成不了分组——如每份只有一行——就按原规则）
    file_group = None
    if merged is not None and merged_values is not None:
        col = merged.merge.column
        if col in merged_values.groups and col not in merged_values.id_like:
            file_group = col
            if auto and "group" not in ans:
                others = [
                    c for c in merged_values.groups if c != col and c not in merged_values.id_like
                ]
                if others:  # 原本要问「按哪一列分组」：记下替用户选了合成列
                    defaults.append({"question_code": "pick_group", "answer": {"group": col}})
    for _round in range(MAX_AUTO_ROUNDS if auto else 1):
        confirmed, picks = confirmed_from(ans)
        if file_group is not None and "group" not in picks:
            picks["group"] = file_group
        chosen = recipe_mod.pick_table(profile, table, sheet, hint)
        if isinstance(chosen, recipe_mod.NeedsChoice):
            if auto and chosen.options:  # 懒模式：不问，用第一张（发现遍历的顺序）
                ans_pick = chosen.options[0].answer
                table, sheet = ans_pick["table"], ans_pick.get("sheet")
                defaults.append({"question_code": chosen.question_code, "answer": dict(ans_pick)})
                continue
            return remember({"kind": "ask", **chosen.to_dict()})
        if isinstance(chosen, str):
            return remember({"kind": "nothing", "reasons": [chosen], "dropped": []})
        if values_key != (chosen.path, chosen.sheet):
            values = merged_values if merged is not None else _table_for_values(folder_path, chosen)
            values_key = (chosen.path, chosen.sheet)
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
        if not isinstance(res, (recipe_mod.NeedsChoice, recipe_mod.MixedCells)):
            break
        if not auto:
            return remember({"kind": "ask", **res.to_dict()})
        if isinstance(
            res, recipe_mod.MixedCells
        ):  # 默认「不画那几行」（用户点「取数字部分」等再带 `cells` 重来）
            for col in res.columns:
                rules[col["column"]] = recipe_mod.CellRule.from_dict(
                    {"column": col["column"], "mode": "skip"}
                )
                mixed_auto.append(
                    {
                        "column": col["column"],
                        "non_numeric": col["non_numeric"],
                        "rows": col["rows"],
                        "mode": "skip",
                    }
                )
            defaults.append({"question_code": res.question_code})
            continue
        opt = res.unsure or (res.options[0] if res.options else None)
        if opt is None:  # 没有任何选项可替用户选：不会发生（每个问题都有「不确定」），防御
            break
        ans.update(opt.answer)
        defaults.append({"question_code": res.question_code, "answer": dict(opt.answer)})
    if (
        res is None
        or isinstance(res, (recipe_mod.NeedsChoice, recipe_mod.MixedCells))
        or not isinstance(chosen, adf.TableEntry)
    ):
        # 懒模式迭代用尽仍没落定（防御）：不出 ask，当「没有可画的」
        return remember({"kind": "nothing", "reasons": ["no_renderable_family"], "dropped": []})
    variants: dict[int, dict] = {}
    if (
        merged is not None
        and file_group is not None
        and merged.merge.splittable
        and "group" not in ans
        and res.items
    ):
        res, variants = _with_file_only_variants(res, merged, values, confirmed, hint, picks, rules)

    if ans.get("error") in ERROR_SUMMARY and res.items:
        # 用户点选了误差线 / 误差带画哪一种：换掉每个备选里「均值 ± 某种误差」的层（其余不动）
        swapped = []
        for c in res.items:
            new = with_error_summary(c.recipe, ans["error"])
            if new is not c.recipe and id(c.recipe) in variants:
                # 合画里成对候选的标记是按配方对象记的：换了配方要把标记带过去，否则两张成对的图分不清是哪一张
                variants[id(new)] = variants.pop(id(c.recipe))
            swapped.append(replace(c, recipe=new))
        res = replace(res, items=tuple(swapped))

    # 看图目的：只重排（纯排列，不新增 / 不复活备选）；没点 `answers.purpose` 时是恒等，只多响应里的 `purpose` 一项
    purpose = purpose_mod.resolve(res.items, ans.get("purpose")) if res.items else None
    if purpose is not None:
        res = replace(res, items=purpose.items)

    def merged_info() -> dict:
        """合画：各份是哪几份（路径与请求里一致）、各自的标签、合成的分组列名；单份没有这两项。"""
        if merged is None:
            return {}
        return {
            "sources": [
                {
                    "path": src.path,
                    "sheet": src.sheet,
                    "label": src.label,
                    "header_row": src.header_line,
                    "units_row": bool(src.entry and src.entry.summary.units_row),
                }
                for src in merged.merge.sources
            ],
            "group_column": merged.merge.column,
        }

    def basics() -> dict:
        """懒模式响应里**说清这次用了什么**：表 / 表头行 / 列角色与可换的列 / 默认决定 / 自动跳过的文字格。"""
        if not auto:
            return merged_info()
        return {
            **merged_info(),
            "render": "lazy",
            "table": merged.merge.sources[0].path if merged is not None else chosen.path,
            "sheet": chosen.sheet,
            "header_row": values.header_line,
            # 这次读的那份数据文件的原始字节 sha256（单份；合画没有）：前端把它记进图的来历，之后「数据变了没有」只看它（`data_digest` 同一口径）
            **({"data_sha256": values.sha256} if merged is None else {}),
            "units_row": values.units_row,  # 表头后紧跟的单位行已被切掉（它是 `header_row + 1` 行）；没有 = False
            "roles": res.roles,
            "defaults": defaults,
            "mixed_cells": mixed_auto,
            # 用户亲手点过「误差线画的是什么」才有这一项（没有 = 模板默认的均值 ± 标准差，界面把它列进「需要你确认」）
            **({"error_confirmed": True} if ans.get("error") in ERROR_SUMMARY else {}),
        }

    if not res.items:
        return remember(
            {
                "kind": "nothing",
                "reasons": list(res.reasons),
                "dropped": [{"reason_code": p.code} for d in res.dropped for p in d.problems[:1]],
                **basics(),
            }
        )
    sid = secrets.token_hex(12)
    sdir = CANDIDATE_ROOT / sid
    sdir.mkdir(parents=True, exist_ok=False)
    dropped: list[dict] = [{"reason_code": p.code} for d in res.dropped for p in d.problems[:1]]
    fallback = False
    # 每个备选 → (配方, 剔除原因, 缩略图, 覆盖情况, 懒渲染素材)
    ready: list[
        tuple[recipe_mod.Candidate, dict | None, str | None, Path | None, _Planned | None]
    ] = []
    try:
        if merged is not None:
            for src in merged.merge.sources:
                _stage_table(sdir, folder_path / PurePosixPath(src.rel), src.rel)
        else:
            progress.check()
            make_room(values.size)
            _stage_table(
                sdir,
                folder_path / PurePosixPath(chosen.path),
                chosen.path,
                tracked=values.large is not None,
            )
        if auto:
            plan = _plan_candidates(sdir, chosen, values, [c.recipe for c in res.items], style)
            for cand, p in zip(res.items, plan, strict=True):
                cov = None if p.bad else tpl.fingerprint_coverage(p.recipe).to_dict()
                ready.append((cand, cov, p.bad, None, p))
        else:
            rendered = _render_candidates(
                sdir, chosen, values, [c.recipe for c in res.items], _project_pin(root), style
            )
            for cand, (_recipe, coverage, reason, png, fb) in zip(res.items, rendered, strict=True):
                fallback = fallback or fb
                ready.append((cand, coverage, reason, png, None))
    except OSError as exc:
        _rmtree(sdir)
        if exc.errno not in (errno.ENOSPC, errno.EDQUOT):
            raise
        # 候选目录所在的磁盘放不下大表的副本：结构化的「没有备选」，不是 500（原因码复用既有的 `candidate_render_failed`）
        return remember(
            {"kind": "nothing", "reasons": ["candidate_render_failed"], "dropped": [], **basics()}
        )
    except BaseException:
        _rmtree(sdir)
        raise
    items: dict[str, Item] = {}
    order: list[str] = []
    for cand, coverage, reason, png, planned in ready:
        if reason is not None or (png is None and planned is None):
            dropped.append({"reason_code": reason or "candidate_render_failed"})
            continue
        iid = secrets.token_hex(6)
        items[iid] = Item(
            iid,
            cand.code,
            cand.recipe,
            coverage or {},
            png,
            unplotted_total(unplotted_rows(values, cand.recipe)),
            cell_stats(values, cand.recipe) or None,
            reduced=reduced_of(values, cand.recipe),
            state="pending" if planned is not None else "ready",
            variant=variants.get(id(cand.recipe)),
            why_not=(
                None
                if purpose is None or not order
                else purpose_mod.why_not(cand.code, purpose.code, purpose.source)
            ),
            fits=tpl.fit_report(values, cand.recipe),
            error_missing=error_missing_for(values, cand.recipe),
            error_stated=error_stated_of(chosen.summary, cand.recipe, ans),
            error_columns=error_columns_of(chosen.summary, cand.recipe, ans),
            facts=candidate_facts(values, cand.recipe),
            series_facts=series_facts(values, cand.recipe),
            stem=planned.stem if planned is not None else None,
            n=planned.n if planned is not None else 0,
            expected=planned.expected if planned is not None else [],
        )
        order.append(iid)
    if not items:
        _rmtree(sdir)
        return remember(
            {
                "kind": "nothing",
                "reasons": sorted({d["reason_code"] for d in dropped}) or ["no_renderable_family"],
                "dropped": dropped,
                **basics(),
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
        header_line=values.header_line,
        merge=merged.merge if merged is not None else None,
        confirmed=res.confirmed,
        items=items,
        dir=sdir,
        touched=time.time(),
        root=Path(root),
        lazy=auto,
        large=values.large is not None,
        style=style,
    )
    with _LOCK:
        _SESSIONS[sid] = sess
    out = {
        "kind": "candidates",
        "session": sid,
        "items": [item_to_dict(sess, items[i], k == 0) for k, i in enumerate(order)],
        "dropped": dropped,
        # 这批是按什么看图目的排的、这个目的是用户点的（`user`）还是从数据推出的假设（`data`）、界面可让用户换成哪几个（不足两个 = 空）
        **(
            {
                "purpose": {
                    "code": purpose.code,
                    "source": purpose.source,
                    "options": list(purpose.options),
                }
            }
            if purpose is not None and purpose.code is not None
            else {}
        ),
        # 诊断标记：至少有一张小图是项目解释器没画成、回退到默认链条画的
        "interpreter_fallback": fallback,
        **({"spec_fallback": True} if spec_bad else {}),
        # 带了规范才有：这批图实际用的栏宽、起始字号与期刊字号范围（面板上如实显示，前端不另算一遍）
        **({"figure_style": figure_style(style)} if style is not None else {}),
        **basics(),
    }
    if auto and understand is not False and not hrows and not user_took_over:
        # 第 1 步「读懂数据」：有可用的 Key 连接才起后台任务、响应里才多这一个键；没有时响应与从前逐字相同
        und = roles_mod.start(
            project=project,
            entry=chosen,
            values=values,
            hint=hint,
            data_path=None if merged is not None else folder_path / PurePosixPath(chosen.path),
        )
        if und is not None:
            out["understanding"] = und
    return remember(out, sid)


def _user_took_over(ans: dict) -> bool:
    """请求里已有用户（或前端替用户带上的模型建议）定下的列角色 / 处理规则：不再起模型任务，也避免「采纳建议 → 再来一次 → 再起任务」的循环。"""
    return any(k in ans for k in ("y", "x", "group", "cells", "x_measure"))


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
    "RENDER_MODES",
    "ProposeError",
    "Session",
    "discard",
    "ensure_thumbnail",
    "get_item",
    "get_session",
    "normalize_rel",
    "parse_header_row",
    "parse_header_rows",
    "path_key",
    "propose",
    "script_data_rel",
    "sweep",
    "thumbnail_path",
]
