"""导入即扫描（T02）：项目被认领 / 恢复之后自动开始的**有界、只读、零执行**的结构检查。

产品位置：用户导入文件夹 → 静态素材立即可浏览排版；同时后台读一遍项目结构，给轻量准备面板一份**真实**
报告（找到了什么脚本、哪个像绘图目标、环境有哪些**未核验**的线索、依赖声明在哪、哪里没看全）。
它**不是**准备会话（`prepsession`，T01）：会话的「检查」要问环境决策（`preparation.plan_for` →
`decide_environment`，有解释器体检与写配置的副作用），是用户选定目标之后的明确动作；这里的扫描发生在
选目标**之前**，所以只许读。两者的关系：

    扫描（本模块，自动、零执行）──候选脚本 / 目标 / 环境线索──► 用户选定目标
        └─ 报告里每个目标带 `session_target`（= 创建准备会话的请求体）
    准备会话（T01，明确动作）──plan_for 等──► 检查 / 采用 / 运行

扫描**不另造**素材扫描器与关系判定器：脚本走 `discover.iter_all_scripts(budget=)`（同一份剪枝 /
`MAX_DEPTH`），素材走 `project_refresh.iter_assets(budget=)`（同一份素材边界），分类走
`probe.inventory_entry`（与「脚本清单」同一份 reason 判据）。本模块只新增：预算与 partial 账本
（`scanbudget`）、目标与作用域的选择、环境**线索**的只读枚举、依赖声明的位置、证据修订号、会话式服务
（单飞 / 取消 / 迟到作废）。

### 零执行（可证明，不是约定）

* 从不 import `pool` / `preparation` / `deprepair` / `subprocess` 等（AST 门禁 `tests/test_project_scan_zero_exec.py`）；
* 解析永远不给目标解释器（`discover.inspect_script(target_python=None)`）：宿主判语法错误就是
  `syntax_error` 且 `parser=None`（**未核验**，不是「确认不是脚本」）；
* 环境线索只读磁盘记录，不问登录 shell（`userenvs.discover(ask_login_shell=False)`），不体检、不
  `import matplotlib`；记住过的解释器只 `stat`；
* 扫描期间 `Popen` / `os.exec*` / `posix_spawn*` / `socket.connect` 一次都不会被调用（同一份测试里
  换成桩，调用即失败）。

### 不写用户项目

扫描**只读**；不写 `tavotto_registry.json`、不写 `tavottofile/`。（`open_project` 缺注册表时起草并写
注册表是**既有**打开行为，编排者裁决为 deliberate-boundary，不在本模块。）

### 「没测量」不是「测量结果是零」

任何读不动的目录 / 预算用完 / 占位文件 / 符号链接 / 单文件过大都进账本（`scanbudget`），`state` 变
`partial`；`partial` + 没找到脚本绝不报「这是静态项目」。未核验的脚本照样列出、可以手动选为目标。

纯标准库：Flask 父进程 import 它。
"""

from __future__ import annotations

import hashlib
import json
import logging
import stat
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import (
    depresolve,
    discover,
    figcapture,
    probe,
    project_refresh,
    projectenv,
    registry,
    scanbudget,
    userenvs,
)

LOG = logging.getLogger("tavotto.projscan")

SCAN_VERSION = 1

# ---------------------------------------------------------------- 词汇（闭集）
STATE_RUNNING = "running"
STATE_COMPLETE = "complete"
STATE_PARTIAL = "partial"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
STATES = (STATE_RUNNING, STATE_COMPLETE, STATE_PARTIAL, STATE_CANCELLED, STATE_FAILED)

#: phase 词汇是准备会话（`prepsession.PHASES`）的子集——同一份闭集，前端只面对一份词汇；
#: 这里不 import `prepsession`（它拖着 `preparation` → `pool`），子集关系由
#: `tests/test_project_scan.py` 钉着。
PHASE_SCANNING = "scanning"
PHASE_AWAITING_CONFIRMATION = "awaiting_confirmation"
PHASE_AWAITING_CONFIGURATION = "awaiting_configuration"
PHASE_COMPLETED = "completed"
PHASE_ACTION_REQUIRED = "action_required"
PHASE_CANCELLED = "cancelled"
PHASES = (
    PHASE_SCANNING,
    PHASE_AWAITING_CONFIRMATION,
    PHASE_AWAITING_CONFIGURATION,
    PHASE_COMPLETED,
    PHASE_ACTION_REQUIRED,
    PHASE_CANCELLED,
)

OUTCOME_SCANNING = "scanning"
OUTCOME_TARGET_FOUND = "target_found"  # 找到唯一的、尚未连接的绘图目标
OUTCOME_CONNECTED = "already_connected"  # 绘图脚本都已登记：素材已可编辑，没有要准备的目标
OUTCOME_CHOOSE_TARGET = "choose_target"  # 有候选但不唯一 / 只有未核验的脚本：让用户选
OUTCOME_STATIC = "static_source"  # 完整扫描、没有脚本：静态素材，直接可排版
OUTCOME_EMPTY = "nothing_found"  # 完整扫描、没有脚本也没有素材
OUTCOME_UNCHECKED = "unchecked"  # 扫描不完整且没有可选目标：不是「没有脚本」
OUTCOME_CANCELLED = "cancelled"
OUTCOME_FAILED = "failed"
OUTCOMES = (
    OUTCOME_SCANNING,
    OUTCOME_TARGET_FOUND,
    OUTCOME_CONNECTED,
    OUTCOME_CHOOSE_TARGET,
    OUTCOME_STATIC,
    OUTCOME_EMPTY,
    OUTCOME_UNCHECKED,
    OUTCOME_CANCELLED,
    OUTCOME_FAILED,
)

ROLE_PLOT = "plot"  # 有绘图证据（已登记 / 静态产图 / 动态图名）
ROLE_AUXILIARY = "auxiliary"  # 工具 / 测试 / 样式模块：不默认当目标，也不强制修复
ROLE_UNKNOWN = "unknown"  # 读不动 / 没解析成 / 没核验：不当空项目，可手动选

CHECK_OK = "ok"
CHECK_UNKNOWN = "unknown"
CHECK_PARTIAL = "partial"

ACTION_RESCAN = "rescan"
ACTION_CANCEL_SCAN = "cancel_scan"
ACTION_PREPARE = "prepare"
ACTION_CHOOSE_TARGET = "choose_target"

#: 被拒读的脚本在条目里的 `unchecked_reason`（= `scanbudget` 账本 code 的子集）
UNCHECKED_REASONS = (
    scanbudget.ISSUE_UNREADABLE_FILE,
    scanbudget.ISSUE_PLACEHOLDER,
    scanbudget.ISSUE_TOO_LARGE,
    scanbudget.ISSUE_SOURCE_BYTES,
    scanbudget.ISSUE_PARSE_FAILED,
    scanbudget.ISSUE_CANCELLED,
    scanbudget.ISSUE_TIME,
)

#: 环境线索候选最多列多少条（超出的只标 `truncated`）
MAX_ENV_CANDIDATES = 24
#: 一份仍然新鲜的完成报告在多少秒内被重复认领复用（A→B→A、两个标签页）
FRESH_S = 30.0

#: `environment.candidates[*].source`：项目 venv 与「记住的」是 projscan 自己的名字，其余沿用 `userenvs.SOURCES`
SOURCE_PROJECT_VENV = "project_venv"
SOURCE_REMEMBERED = "remembered"
SCOPE_PROJECT = "project"
SCOPE_MACHINE = "machine"
_PROJECT_SCOPED = frozenset(
    {
        userenvs.SOURCE_VSCODE,
        userenvs.SOURCE_PYTHON_VERSION,
        userenvs.SOURCE_ENVIRONMENT_YML,
        userenvs.SOURCE_SHEBANG,
        SOURCE_PROJECT_VENV,
        SOURCE_REMEMBERED,
    }
)
STATUS_UNCHECKED = "unchecked"
STATUS_REMEMBERED = "remembered_unverified"
STATUS_MISSING = "missing"

#: 依赖声明的「作用域」标志文件（最近的祖先目录里有任一个，它就是这个目标的作用域）
_SCOPE_MARKERS = ("requirements.txt", "pyproject.toml", "environment.yml", "environment.yaml")


# ---------------------------------------------------------------- 小工具
def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _public_problem(problem: dict | None) -> dict | None:
    """条目里的 `problem` 的公开投影：只留 kind / 行号 / 解析器版本。`detail` 是异常原文，
    OSError 的原文里带绝对路径——扫描报告不出绝对路径。"""
    if not problem:
        return None
    out = {"kind": problem.get("kind")}
    for key in ("lineno", "parser_version", "encoding"):
        if problem.get(key) is not None:
            out[key] = problem[key]
    return out


def _registered_scripts(root: Path) -> set[str]:
    return set(_registered_stems(root))


def _registered_stems(root: Path) -> dict[str, list[str]]:
    reg = registry.Registry()
    try:
        reg.load(root)
    except (FileNotFoundError, RuntimeError, OSError):
        return {}
    return {script: list(reg.stems_of(script)) for script in reg.all_scripts()}


def _linked_scripts(root: Path) -> set[str]:
    """登记了、**而且**至少一张登记的图此刻真有东西可编辑的脚本：项目里有同名的图文件，或这张图被某次执行捕获过
    （`probe.was_captured`：runtime cache 里有物化记录）。「有同名的图文件」只认 `figcapture.find_original_artifact`
    ——项目根一层、`ARTIFACT_EXTS`——与 handoff / probe 找原件是同一份判据，不另立「素材在哪」的第二条规则
    （递归素材清单里的 `archive/fig.pdf` 不是这个脚本的原件，不算连接）。

    打开项目时的静态扫描会先把字面量 savefig 的图名写进注册表（T00 deliberate-boundary）——那只是猜测，脚本一次都没
    跑过、什么都打不开。只按「注册表里有」就报 `already_connected`（「素材已可编辑」）是假话，而且会把只有脚本的项目的
    「准备并运行」入口藏起来（T11 真首跑发现）。只读：文件名比对 + 数据目录里的 cache 元数据，不执行、不起解释器。"""
    return {
        script
        for script, stems in _registered_stems(root).items()
        if any(
            figcapture.find_original_artifact(str(root), stem) is not None
            or probe.was_captured(root, script, stem)
            for stem in stems
        )
    }


def _rel_dir_posix(root: Path, directory: Path) -> str:
    rel = PurePosixPath(directory.relative_to(root).as_posix()).as_posix()
    return rel or "."


# ---------------------------------------------------------------- 脚本
def _unchecked_item(path: Path, root: Path, registered: set[str], why: str) -> dict:
    """读不了的脚本：照样列出（可手动选为目标），分类按名字 / 登记可得的那部分，`checked=False`。"""
    seen = {"info": None, "problem": None, "parser": None, "entry_candidates": None}
    item = probe.inventory_entry(path, root, registered, seen)
    if item["reason"] == probe.REASON_NO_STATIC_OUTPUT:
        # 「确认不产图」需要读过它；没读就不能下这个结论
        item["reason"] = probe.REASON_UNPARSEABLE
    item["checked"] = False
    item["unchecked_reason"] = why
    item["problem"] = None
    return item


def _script_item(
    path: Path,
    root: Path,
    registered: set[str],
    budget: scanbudget.Budget,
    cache: dict,
) -> dict:
    rel = discover.rel_key(path, root)
    try:
        st = path.stat()
    except OSError:
        budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="file", path=rel)
        return _unchecked_item(path, root, registered, scanbudget.ISSUE_UNREADABLE_FILE)
    if scanbudget.is_placeholder(st):
        # 读它 = 强制下载：不读，记账
        budget.note(scanbudget.ISSUE_PLACEHOLDER, scope="file", path=rel)
        return _unchecked_item(path, root, registered, scanbudget.ISSUE_PLACEHOLDER)
    sig = (st.st_size, st.st_mtime_ns)
    hit = cache.get(rel)
    if hit is not None and hit[0] == sig:
        seen = hit[1]  # 内容证据没变：不重新解析
        budget.source_bytes += st.st_size
    else:
        refused = budget.charge_source(st.st_size)
        if refused is not None:
            budget.note(refused, scope="file", path=rel)
            return _unchecked_item(path, root, registered, refused)
        try:
            seen = discover.inspect_script(path, root, target_python=None, glob_disk=False)
        except (RecursionError, MemoryError, ValueError):
            budget.note(scanbudget.ISSUE_PARSE_FAILED, scope="file", path=rel)
            return _unchecked_item(path, root, registered, scanbudget.ISSUE_PARSE_FAILED)
        cache[rel] = (sig, seen)
    problem = seen.get("problem")
    if problem is not None and problem.get("kind") == discover.PROBLEM_IO:
        budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="file", path=rel)
    item = probe.inventory_entry(path, root, registered, seen)
    item["checked"] = True
    item["problem"] = _public_problem(item["problem"])
    return item


def _role_of(item: dict) -> str:
    reason = item["reason"]
    if reason == probe.REASON_REGISTERED:
        return ROLE_PLOT  # 注册表说它产图：登记本身就是绘图证据，读没读成都不改
    if not item.get("checked", True):
        return ROLE_UNKNOWN
    if reason in (probe.REASON_STATIC, probe.REASON_DYNAMIC):
        return ROLE_PLOT
    if reason in (probe.REASON_NO_STATIC_OUTPUT, probe.REASON_INFRASTRUCTURE):
        return ROLE_AUXILIARY
    return ROLE_UNKNOWN  # unparseable


def _marker_present(path: Path, root: Path, budget: scanbudget.Budget | None) -> bool:
    """作用域标记文件在不在。导入即扫描（有 `budget`）按元数据判：只 `lstat`、不跟随——符号链接 / 路径
    替身不探目标（UNC 会触发 SMB 访问），记一条 `unreadable_file`（partial）而不是当「没有标记」。
    没有 `budget`（准备 / 依赖门）保持跟随用户自己的链接。"""
    if budget is None:
        return path.is_file()
    try:
        st = path.lstat()
    except OSError:
        return False
    if scanbudget.is_redirect(st):
        budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="file", path=_rel_dir_posix(root, path))
        return False
    return stat.S_ISREG(st.st_mode)


def _scope_of(
    root: Path,
    script: str,
    memo: dict[str, str | None],
    budget: scanbudget.Budget | None = None,
) -> str | None:
    """脚本的依赖作用域：从脚本所在目录往上，第一个放着依赖声明文件的目录（项目相对 POSIX；`.` = 根）。
    没有就是 None。只 `is_file`，不读文件；不同作用域的 requirements **不**在这里合并。"""
    directory = (root / script).parent
    chain: list[Path] = []
    cur = directory
    while True:
        chain.append(cur)
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent
    found: str | None = None
    for d in chain:
        if budget is not None and budget.stop_reason() is not None:
            return None
        key = str(d)
        if key not in memo:
            try:
                has = False
                for name in _SCOPE_MARKERS:
                    if budget is not None and budget.stop_reason() is not None:
                        return None
                    if _marker_present(d / name, root, budget):
                        has = True
                        break
            except OSError:
                has = False
            memo[key] = _rel_dir_posix(root, d) if has else None
        if memo[key] is not None:
            found = memo[key]
            break
    return found


def _targets_of(
    root: Path,
    items: list[dict],
    linked: set[str] | None = None,
    budget: scanbudget.Budget | None = None,
) -> tuple[list[dict], str | None, str]:
    """条目 → 目标列表、默认目标、选择状态。

    * 绘图证据的脚本与「读不了 / 没解析成」的脚本可以当目标；工具 / 测试 / 样式模块**不默认**当目标
      （它们缺包或缺参不阻塞别的绘图脚本，也不会被强制修复），但仍在 `scripts` 里、可手动选；
    * 默认目标只在**恰好一个尚未连接的绘图脚本**时给——第一版只支持单个优先绘图作用域，多个就让用户选，
      不替他挑，更不把不同作用域的 requirements 混成一份。
    """
    memo: dict[str, str | None] = {}
    targets: list[dict] = []
    for it in items:
        role = _role_of(it)
        if role == ROLE_AUXILIARY:
            continue
        target = {
            "script": it["script"],
            "role": role,
            "evidence": it["reason"],
            "registered": it["registered"],
            "entry": it["entry_candidates"][0] if it["entry_candidates"] else None,
            "scope": _scope_of(root, it["script"], memo, budget),
            "scope_checked": budget is None or budget.stop_reason() is None,
            "checked": it.get("checked", True),
        }
        body = {"script": it["script"]}
        if target["entry"] and role == ROLE_PLOT and it["reason"] != probe.REASON_REGISTERED:
            body["entry"] = target["entry"]
        target["session_target"] = body
        targets.append(target)
    # 「已连接」= 登记了且真有可编辑的图（`_linked_scripts`）；只在注册表里、什么都打不开的仍是待准备的目标
    linked = {t["script"] for t in targets if t["registered"]} if linked is None else linked
    for t in targets:
        t["linked"] = t["script"] in linked
    pending = [t for t in targets if t["role"] == ROLE_PLOT and not t["linked"]]
    unknown = any(t["role"] == ROLE_UNKNOWN for t in targets)
    if len(pending) == 1 and not unknown:
        # 还有未核验的 unknown 目标时不替用户挑默认（它可能才是要跑的那个）：落到下面的 ambiguous
        return targets, pending[0]["script"], "single"
    if len(pending) > 1:
        return targets, None, "ambiguous"
    if unknown:
        # 还有读不了 / 没解析成的脚本是可选目标：它们**未核验**，不能因为别处有已连接的绘图脚本
        # 就当成「全连着了」收起提示——交给用户选（unknown 不当通过）
        return targets, None, "ambiguous"
    if any(t["role"] == ROLE_PLOT for t in targets):
        return targets, None, "connected"  # 绘图脚本都已连着素材
    return targets, None, "ambiguous" if targets else "none"


# ---------------------------------------------------------------- 依赖声明
def dependency_evidence(
    root: Path, script: str | None, budget: scanbudget.Budget | None = None
) -> dict:
    """依赖**声明**在哪、有多少条、哪几类读不懂——只读文件、不求值、不联网、不问解释器。

    `declared_intents` 自带文件数 / 字节上限，读不了的记 `unsupported`（不是「没有依赖」）。输出只有
    声明文件的项目相对路径、条数与闭集 reason，**不带原文行**（原文可能含带凭据的 index URL）。
    结论永远是 `evaluated: False`：能不能装、装没装，要等环境被核验（T05 / T06）。

    `budget`（导入即扫描传）：声明文件按**不跟随链接、只读有上限普通文件**读（符号链接 / UNC / FIFO / 超大
    文件被拒、不被探），被拒的条目进账本（`unreadable_file`，partial）——不是「没有依赖」。"""
    try:
        intents = depresolve.declared_intents(root, script, no_follow=budget is not None)
    except (OSError, ValueError, RuntimeError):
        return {
            "script": script,
            "files": [],
            "requirements": 0,
            "unsupported": [],
            "readable": False,
            "evaluated": False,
        }
    files = sorted({i.source for i in intents if i.source})
    if budget is not None:
        for rel in sorted(
            {
                i.source
                for i in intents
                if i.reason == depresolve.UNSUPPORTED_UNREADABLE and i.source
            }
        ):
            budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="file", path=rel)
    return {
        "script": script,
        "files": files,
        "requirements": sum(1 for i in intents if i.kind == depresolve.INTENT_KIND_REQUIREMENT),
        "unsupported": sorted({i.reason for i in intents if i.reason}),
        "readable": True,
        "evaluated": False,
    }


# ---------------------------------------------------------------- 环境线索
def environment_evidence(
    root: Path,
    script: str | None,
    budget: scanbudget.Budget | None = None,
    *,
    private: bool = False,
) -> dict:
    """环境**候选线索**（只读磁盘记录）：项目 venv、记住的决策、`.vscode` / `.python-version` /
    `environment.yml` / shebang、Conda / pyenv 的落盘记录。

    * 每条 `status` 都是 `unchecked`（记住的是 `remembered_unverified`，文件没了是 `missing`）——**没有
      任何一条被体检过**，`verified` 恒为 False；"推荐"与"采用"是 T05 的事，这里不给推荐；
    * 项目内的解释器给项目相对路径，项目外的只给不透明 `id`（`userenvs.env_id`）与来源 / 标签，不出机器路径；
    * 不问登录 shell、不 `import`、不 `stat` 以外的东西。登录 shell 的答案只在**已经被明确问过**时才并进来
      （`userenvs.discover(ask_login_shell=None)` 只读缓存，T05 的检查动作才会去问）；
    * `budget`：Conda / pyenv 的枚举带着墙钟预算与取消回调，到期 / 取消停在已枚举到的部分（账本 → partial）；
    * `private=True`（T05 的 `envadvice` 用）：每行多带一个 `_python`（解释器绝对路径）。公开形态永远不带。"""
    root = Path(root)
    by_key: dict[str, dict] = {}
    order: list[str] = []

    def add(python: str, source: str, label: str, extra: dict | None = None) -> dict | None:
        # 最后一道：项目派生路径经过重定向就不算指纹 / 不入表（指纹的 stat 会跟随）；上游已各自拦过，这里兜底
        bad = scanbudget.redirected_component(root, python, allow_final_link=True)
        if bad is not None:
            if budget is not None:
                budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="file", path=bad)
            return None
        key = projectenv._executable_key(python)
        row = by_key.get(key)
        if row is None:
            rel = projectenv.project_relative(root, python)
            row = {
                "id": userenvs.env_id(python),
                "source": source,
                "sources": [source],
                "scope": SCOPE_PROJECT if source in _PROJECT_SCOPED else SCOPE_MACHINE,
                "label": label,
                "python_relative": rel or None,
                "status": STATUS_UNCHECKED,
                "fingerprint": _digest(projectenv.interpreter_fingerprint(python)),
                "_python": python,
            }
            by_key[key] = row
            order.append(key)
        elif source not in row["sources"]:
            row["sources"].append(source)
            if source in _PROJECT_SCOPED:
                row["scope"] = SCOPE_PROJECT
        if extra:
            row.update(extra)
        return row

    for venv in projectenv.discover(root, script, no_follow=True, budget=budget):
        python = projectenv.interpreter_of(venv, root=root)
        if python:
            add(python, SOURCE_PROJECT_VENV, Path(venv).name)

    record = projectenv.remembered_record(root, no_follow=True)
    if record is not None and record.get("redirected") and budget is not None:
        budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="file", path=record["redirected"])
    remembered = None
    if record is not None:
        remembered = {
            "mode": record.get("mode") or "pinned",
            "automatic": bool(record.get("automatic", False)),
            "trigger": record.get("trigger") or "",
            "exists": bool(record.get("exists")),
        }
        for key in ("python_version", "matplotlib_version", "support"):
            if record.get(key):
                remembered[key] = str(record[key])
        if record.get("path"):
            python = record["path"]
            row = add(
                python,
                SOURCE_REMEMBERED,
                "",
                {
                    "status": STATUS_REMEMBERED if record.get("exists") else STATUS_MISSING,
                    "chosen_by": "automatic" if record.get("automatic") else "user",
                },
            )
            if row is not None:
                remembered["id"] = row["id"]

    for entry in userenvs.discover(
        root, script, ask_login_shell=None, no_follow=True, budget=budget
    ):
        add(entry["python"], entry["source"], entry.get("label") or "")

    candidates = [by_key[k] for k in order]
    truncated = len(candidates) > MAX_ENV_CANDIDATES
    candidates = candidates[:MAX_ENV_CANDIDATES]
    if not private:
        candidates = [{k: v for k, v in c.items() if k != "_python"} for c in candidates]
    return {
        "verified": False,
        "remembered": remembered,
        "candidates": candidates,
        "truncated": truncated,
    }


# ---------------------------------------------------------------- 报告
def phase_of(state: str, targets: list[dict], choice: str, assets: int, partial: bool) -> dict:
    """扫描状态 + 已发现的事实 → `{phase, outcome}`。纯函数；`unknown` 不当通过，partial 不当「没有」。"""
    if state == STATE_RUNNING:
        return {"phase": PHASE_SCANNING, "outcome": {"kind": OUTCOME_SCANNING}}
    if state == STATE_CANCELLED:
        return {"phase": PHASE_CANCELLED, "outcome": {"kind": OUTCOME_CANCELLED}}
    if state == STATE_FAILED:
        return {
            "phase": PHASE_ACTION_REQUIRED,
            "outcome": {"kind": OUTCOME_FAILED, "code": "project_scan_failed"},
        }
    if choice == "single":
        return {"phase": PHASE_AWAITING_CONFIRMATION, "outcome": {"kind": OUTCOME_TARGET_FOUND}}
    if choice == "ambiguous":
        return {"phase": PHASE_AWAITING_CONFIGURATION, "outcome": {"kind": OUTCOME_CHOOSE_TARGET}}
    if choice == "connected":
        return {"phase": PHASE_COMPLETED, "outcome": {"kind": OUTCOME_CONNECTED}}
    if partial:
        # 没有可选目标，但这一轮没看全：不能说「是静态项目」
        return {
            "phase": PHASE_ACTION_REQUIRED,
            "outcome": {"kind": OUTCOME_UNCHECKED, "code": "scan_incomplete"},
        }
    return {
        "phase": PHASE_COMPLETED,
        "outcome": {"kind": OUTCOME_STATIC if assets else OUTCOME_EMPTY},
    }


def _actions_of(
    state: str, choice: str, default_target: str | None, targets: list[dict]
) -> list[dict]:
    """报告里的动作：**都是后端生成的、不执行用户代码的**。`prepare` 只是「创建准备会话」的请求体
    （T01 端点），真正的环境检查 / 采用 / 运行是那条端点里用户再确认的事。"""
    if state == STATE_RUNNING:
        return [{"id": ACTION_CANCEL_SCAN, "kind": ACTION_CANCEL_SCAN}]
    actions: list[dict] = [{"id": ACTION_RESCAN, "kind": ACTION_RESCAN}]
    if default_target:
        body = next(t["session_target"] for t in targets if t["script"] == default_target)
        actions.append({"id": ACTION_PREPARE, "kind": ACTION_PREPARE, "target": body})
    elif targets:
        actions.append({"id": ACTION_CHOOSE_TARGET, "kind": ACTION_CHOOSE_TARGET})
    return actions


def _checks_of(state: str, partial: bool, items: list[dict], env: dict, deps: dict) -> list[dict]:
    """与准备会话同一套状态词（ok / unknown / ...），再加 `partial`。每一项都是**已读到的事实**的投影：
    环境与依赖要等被核验才是 ok，扫描阶段一律 unknown——不虚构已满足。"""
    scanning = state == STATE_RUNNING
    return [
        {
            "id": "scripts",
            "status": CHECK_UNKNOWN if scanning else (CHECK_PARTIAL if partial else CHECK_OK),
            "detail": {"count": len(items), "unchecked": sum(1 for i in items if not i["checked"])},
        },
        {
            "id": "environment",
            "status": CHECK_UNKNOWN,
            "code": (
                "scan_incomplete"
                if env.get("checked") is False
                else ("candidates_unverified" if env["candidates"] else "no_candidates")
            ),
            "detail": {"candidates": len(env["candidates"])},
        },
        {
            "id": "dependencies",
            "status": CHECK_UNKNOWN,
            "code": (
                "scan_incomplete"
                if deps.get("checked") is False
                else ("declared_not_evaluated" if deps["files"] else "no_declarations")
            ),
            "detail": {"files": len(deps["files"]), "requirements": deps["requirements"]},
        },
    ]


def scan(
    root: str | Path,
    *,
    cancel: Callable[[], bool] | None = None,
    limits: scanbudget.Limits | None = None,
    cache: dict | None = None,
    budget: scanbudget.Budget | None = None,
) -> dict:
    """对一个项目做一次有界、只读、零执行的结构扫描，回完整报告（见模块文档）。

    不抛：目录读不动 / 预算用完 / 取消都变成 `state` 与账本。`cache` 是调用方持有的
    `{脚本相对路径: ((size, mtime_ns), 解析结果)}`——内容证据没变的脚本不重新解析（增量更新）；环境线索
    不缓存体检结论，因为根本没有体检。`budget` 让调用方（服务）在扫描进行中读用量。"""
    root = Path(root)
    budget = budget or scanbudget.Budget(limits=limits or scanbudget.Limits(), cancel=cancel)
    cache = cache if cache is not None else {}
    registered = _registered_scripts(root) if budget.stop_reason() is None else set()

    paths = discover.iter_all_scripts(root, budget=budget) if budget.stop_reason() is None else []
    items: list[dict] = []
    live = set()
    for path in paths:
        # A stopped walk must not begin a new parsing stage, even for paths it
        # collected before stopping. The incomplete scan remains explicit below.
        if budget.stop_reason() is not None:
            break
        item = _script_item(path, root, registered, budget, cache)
        live.add(item["script"])
        items.append(item)
    for gone in [k for k in cache if k not in live and budget.stopped is None]:
        cache.pop(gone, None)  # 脚本没了：缓存里不留（没被预算打断的完整一轮才清）

    assets = (
        project_refresh.iter_assets(root, budget=budget) if budget.stop_reason() is None else []
    )
    asset_kinds = {"pdf": 0, "raster": 0}
    for _path, kind in assets:
        asset_kinds[kind] = asset_kinds.get(kind, 0) + 1

    # 已停止的遍历不再起新的文件系统发现：停了就退回「注册表里有」的粗判（不碰磁盘）
    linked = (
        _linked_scripts(root)
        if budget.stop_reason() is None
        else {i["script"] for i in items if i["registered"]}
    )
    targets, default_target, choice = _targets_of(root, items, linked, budget)
    script_for_env = default_target or (targets[0]["script"] if len(targets) == 1 else None)
    # A stopped traversal must not start fresh filesystem discovery. Keep these
    # stages separately guarded: cancellation/timeout can occur in either one.
    env = {
        "verified": False,
        "remembered": None,
        "candidates": [],
        "truncated": True,
        "checked": False,
    }
    deps = {
        "script": script_for_env,
        "files": [],
        "requirements": 0,
        "unsupported": [],
        "readable": False,
        "evaluated": False,
        "checked": False,
    }
    if budget.stop_reason() is None:
        env = environment_evidence(root, script_for_env, budget)
    if budget.stop_reason() is None:
        deps = dependency_evidence(root, script_for_env, budget)
    budget.stop_reason()  # Include expiry during the last evidence stage in the report.

    issues = budget.issues()
    if budget.stopped == scanbudget.ISSUE_CANCELLED:
        state = STATE_CANCELLED
    else:
        has_partial = any(i["severity"] == scanbudget.SEVERITY_PARTIAL for i in issues)
        # 设计内的静默剪枝（层级太深）只有在「一个脚本都没找到」时才升级成 partial
        notes_matter = (not items) and any(
            i["severity"] == scanbudget.SEVERITY_NOTE for i in issues
        )
        state = STATE_PARTIAL if (has_partial or notes_matter) else STATE_COMPLETE
    partial = state in (STATE_PARTIAL, STATE_CANCELLED)
    derived = phase_of(state, targets, choice, len(assets), partial)

    evidence = {
        "scripts": [(i["script"], i["reason"], i["checked"], i["registered"]) for i in items],
        "sigs": sorted((rel, sig) for rel, (sig, _seen) in cache.items()),
        "assets": sorted((p.relative_to(root).as_posix(), k) for p, k in assets),
        "linked": sorted(linked),
        "env": [(c["id"], c["status"], c["fingerprint"]) for c in env["candidates"]],
        "deps": deps["files"],
        "issues": [(i["code"], i["severity"], i.get("path", "")) for i in issues],
    }
    return {
        "scan_version": SCAN_VERSION,
        "state": state,
        **derived,
        "budget": budget.snapshot(),
        "issues": issues,
        "assets": {
            "count": len(assets),
            "pdf": asset_kinds["pdf"],
            "raster": asset_kinds["raster"],
            "browsable": len(assets) > 0,
        },
        "scripts": items,
        "targets": targets,
        "default_target": default_target,
        "target_choice": choice,
        "checks": _checks_of(state, partial, items, env, deps),
        "environment": env,
        "dependencies": deps,
        "actions": _actions_of(state, choice, default_target, targets),
        "evidence_revision": _digest(evidence),
    }


# ---------------------------------------------------------------- 服务（单飞 / 取消 / 迟到作废）
class _Entry:
    """一个项目当前的扫描账：运行中的、或最近一次终局的。"""

    def __init__(self, project_id: str, root: Path, epoch: int) -> None:
        self.project_id = project_id
        self.root = root
        self.epoch = epoch
        self.scan_id = uuid.uuid4().hex[:12]
        self.cancel_event = threading.Event()
        self.budget = scanbudget.Budget(cancel=self.cancel_event.is_set)
        self.state = STATE_RUNNING
        self.report: dict | None = None
        self.finished_at: float | None = None
        self.cache: dict = {}
        self.seq = 1
        self._last_shape: tuple = ()
        self.thread: threading.Thread | None = None
        self.reason = ""


class ScanService:
    """每项目一份扫描账。线程模型：`ensure()` 起一个守护线程跑 `scan()`；HTTP 线程只读快照。

    * **单飞**：已有一次在跑就返回它，不叠加；
    * **去重**：一份终局报告在 `FRESH_S` 内被重复认领（A→B→A、两个标签页）直接复用，不重扫；超过则
      重扫，但脚本解析走 `cache`（内容证据没变就不重解析）；
    * **迟到作废**：`epoch` 单调；线程结束时发现自己已不是当前 epoch（被 `drop` / 重启取代）就丢弃结果；
    * **取消只取消扫描**：不碰任何执行 / 安装 / worker（扫描根本没有这些 owner）。"""

    def __init__(self, *, runner: Callable[..., dict] | None = None, clock=time.monotonic) -> None:
        self._runner = runner or scan
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._epochs: dict[str, int] = {}

    # ------------------------------------------------------------------ 读
    def get(self, project_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(project_id)
            return self._snapshot(entry) if entry is not None else None

    def _snapshot(self, entry: _Entry) -> dict:
        """当前账的公开快照：运行中是实时计数（已发现 / 仍检查，没有百分比），终局是完整报告。"""
        budget = entry.budget.snapshot()
        shape = (entry.state, budget["scripts"], budget["assets"], budget["entries"] // 256)
        if shape != entry._last_shape:
            entry._last_shape = shape
            entry.seq += 1
        base = {
            "scan_version": SCAN_VERSION,
            "project_id": entry.project_id,
            "scan_id": entry.scan_id,
            "epoch": entry.epoch,
            "observation_seq": entry.seq,
            "reason": entry.reason,
        }
        if entry.report is not None:
            return {**base, **entry.report}
        derived = phase_of(entry.state, [], "none", 0, False)
        return {
            **base,
            "state": entry.state,
            **derived,
            "budget": budget,
            "issues": entry.budget.issues(),
            "found": {"scripts": budget["scripts"], "assets": budget["assets"]},
            "checks": _checks_of(
                entry.state, False, [], {"candidates": []}, {"files": [], "requirements": 0}
            ),
            "actions": _actions_of(entry.state, "none", None, []),
        }

    # ------------------------------------------------------------------ 写
    def ensure(
        self,
        project_id: str,
        root: str | Path,
        *,
        reason: str = "claim",
        force: bool = False,
        publish: Callable[[str, dict], None] | None = None,
    ) -> dict:
        """开始（或复用）这个项目的扫描，回快照。不阻塞：扫描在后台线程里。"""
        root = Path(root)
        with self._lock:
            entry = self._entries.get(project_id)
            if entry is not None and entry.root == root:
                if entry.state == STATE_RUNNING:
                    return self._snapshot(entry)
                fresh = (
                    entry.finished_at is not None
                    and self._clock() - entry.finished_at < FRESH_S
                    and entry.state in (STATE_COMPLETE, STATE_PARTIAL)
                )
                if fresh and not force:
                    return self._snapshot(entry)
            carry = entry.cache if entry is not None and entry.root == root else {}
            epoch = self._epochs.get(project_id, 0) + 1
            self._epochs[project_id] = epoch
            new = _Entry(project_id, root, epoch)
            new.cache = carry
            new.reason = reason if reason in ("claim", "restore", "manual", "refresh") else "manual"
            self._entries[project_id] = new
            thread = threading.Thread(
                target=self._run, args=(new, publish), name="tavotto-project-scan", daemon=True
            )
            new.thread = thread
            snap = self._snapshot(new)
        self._hint(publish, new, "started")
        thread.start()
        return snap

    def _run(self, entry: _Entry, publish) -> None:
        try:
            report = self._runner(
                entry.root, cache=entry.cache, budget=entry.budget, cancel=entry.cancel_event.is_set
            )
            state = report["state"]
        except Exception:  # noqa: BLE001 — 扫描线程不许把异常漏给解释器；失败是一种终局
            LOG.exception("项目扫描失败")
            report, state = None, STATE_FAILED
        with self._lock:
            if self._entries.get(entry.project_id) is not entry:
                return  # 已被取代 / 项目已关闭：迟到的结果作废
            entry.state = state
            if report is None:
                derived = phase_of(STATE_FAILED, [], "none", 0, False)
                report = {
                    "state": STATE_FAILED,
                    **derived,
                    "budget": entry.budget.snapshot(),
                    "issues": entry.budget.issues(),
                    "checks": [],
                    "actions": [{"id": ACTION_RESCAN, "kind": ACTION_RESCAN}],
                    "scripts": [],
                    "targets": [],
                    "default_target": None,
                    "target_choice": "none",
                    "assets": {"count": 0, "pdf": 0, "raster": 0, "browsable": False},
                    "evidence_revision": "",
                }
            entry.report = report
            entry.finished_at = self._clock()
        self._hint(publish, entry, "finished")

    def cancel(self, project_id: str) -> dict | None:
        """取消**这个项目的扫描**（只此一件事）。已经终局就什么都不做。"""
        with self._lock:
            entry = self._entries.get(project_id)
            if entry is None:
                return None
            if entry.state == STATE_RUNNING:
                entry.cancel_event.set()
            return self._snapshot(entry)

    def drop(self, project_id: str) -> None:
        """项目关闭：取消在跑的扫描并忘掉这笔账（线程醒来发现自己不是当前条目，结果作废）。"""
        with self._lock:
            # epoch 账**不**随之清零：同一进程里它单调，前端靠 (scan_id, epoch) 丢旧响应
            entry = self._entries.pop(project_id, None)
        if entry is not None:
            entry.cancel_event.set()

    def wait(self, project_id: str, timeout: float = 30.0) -> dict | None:
        """测试 / 同步调用方用：等这个项目当前的扫描结束。"""
        with self._lock:
            entry = self._entries.get(project_id)
            thread = entry.thread if entry is not None else None
        if thread is not None:
            thread.join(timeout)
        return self.get(project_id)

    def _hint(self, publish, entry: _Entry, phase: str) -> None:
        if publish is None:
            return
        try:
            # 只是「重读」提示：不带 phase / 路径 / 计数（与 `preparation.session` 同一纪律）
            publish(
                "project.scan",
                {"pj": entry.project_id, "scan_id": entry.scan_id, "epoch": entry.epoch},
            )
        except Exception:  # noqa: BLE001 — 提示发不出去不影响扫描本身
            LOG.debug("project.scan 提示发布失败", exc_info=True)


SCANS = ScanService()
