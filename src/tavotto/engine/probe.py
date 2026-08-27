"""试运行探测：按脚本**真实产出**的文件名建立 stem↔script 映射。

静态扫描（engine/discover.py）能解开绝大多数写法，但 stem 来自运行期数据的
脚本（遍历数据目录、读配置、命令行参数……）静态永远解不出来；show-only 脚本
（`plt.plot(...); plt.show()`）更是压根没有存图调用。那类脚本以前只能手工
登记 tavotto_registry.json——用户根本不知道要登记什么。

这里换个思路：worker 本来就在 build 阶段拦截 `Figure.savefig` / `paper_style.save`
并按**真实文件名**捕获 Figure（不写盘），所以把脚本跑一遍就能拿到权威的 stem
列表。跑得起来的脚本 = 能参数化的脚本，不再靠猜。

代价是要真的执行脚本（冷启动秒级到分钟级），所以只在用户主动触发时做，
绝不在打开项目时静默全跑。执行走 ExecutionSpec 的 safe 档（pool 的两条
spawn 路径都是 `execspec.safe_spec()` 的消费者）：cwd 在沙盒、argv 只有
脚本自身、savefig 吞掉捕获、相对路径只读回退。

两件事的唯一出处也在这里：

* `script_inventory()` —— 「项目里都有哪些 .py、各自处于什么状态」的清单
  （Compatibility Bridge Session 3）：普通 .py 不因静态分析返回 None 就从
  产品中消失，每条带稳定 reason code；
* `ERROR_*` —— 试运行失败的稳定错误码表。文案随时可改，code 不行——
  前端按 code 换成当前语言的文案（`errors:backend.*`），traceback 只进
  诊断详情。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import discover, pool, registry

LOG = logging.getLogger("tavotto.probe")

# entry 猜错的表现是 AttributeError / 脚本不执行，换一个再试即可。
# 只在脚本**解析不了**（语法错误）、静态候选给不出来时才盲试这份顺序；
# 解析得动的脚本用 `discover.probe_entry_candidates` 的精确候选——盲试
# 不存在的 entry 也要把顶层代码整个跑一遍，纯属浪费一次冷启动。
FALLBACK_ENTRIES = ("main", "render", registry.INLINE_ENTRY)

# ---------------------------------------------------------------------------
# 稳定错误码（协议契约：code 不许改，文案随便改）。
# 前端文案在 web/src/i18n/locales/*/errors.json 的 backend.* 下（中英各一份）；
# 这里的 message 是后端中文回退（约定见 app.py 顶部）。
# ---------------------------------------------------------------------------
ERROR_OUTSIDE_PROJECT = "script_path_outside_project"
ERROR_NOT_FOUND = "script_not_found"
ERROR_UNSUPPORTED_TYPE = "unsupported_script_type"
ERROR_PROBE_FAILED = "script_probe_failed"
ERROR_NO_FIGURE = "script_no_figure"
ERROR_MISSING_DEPENDENCY = "missing_dependency"
ERROR_TIMEOUT = "execution_timeout"
ERROR_CANCELLED = "execution_cancelled"
ERROR_INVALID_ENTRY = "invalid_entry"
ERROR_STEM_CONFLICT = "multiple_stem_conflict"

#: traceback 进诊断详情的截断上限（完整日志仍在 worker.log）。
_TRACEBACK_LIMIT = 4000


def _err(code: str, message: str, params: dict | None = None, traceback_text: str = "") -> dict:
    """结构化探测错误：主文案（中文回退）+ code + params；traceback 单列。"""
    out = {"code": code, "message": message, "params": params or {}}
    tb = (traceback_text or "").strip()
    if tb:
        out["traceback"] = tb[-_TRACEBACK_LIMIT:]
    return out


def _error_from_worker(exc: pool.WorkerError, entry: str) -> dict:
    """WorkerError → 稳定探测错误码。

    映射是收敛的：缺包与超时各有可执行出口（换环境 / 检查死循环），单独
    成码；会话被中途终止（脚本在探测期间被改、workerd 会话被回收）是
    execution_cancelled；其余一律 script_probe_failed——绝大多数是脚本自己
    的问题，worker 侧的细分 code（如 script_error）留在 params 里备查。
    """
    reason = _short(str(exc), exc.traceback_text)
    if exc.code == "missing_dependency":
        params = {"module": exc.module}
        # 项目环境自动接手为什么没成（ADR 0018）：找不到 venv / venv 里也没这个
        # 包 / 那个环境没有 matplotlib / Python 版本不支持。四种情况用户要做的
        # 事完全不同，只报「缺少依赖包」等于把可执行的出路藏起来。
        detail = getattr(exc, "project_env", None)
        if isinstance(detail, dict) and detail.get("code"):
            params["project_env"] = detail.get("code", "")
        return _err(
            ERROR_MISSING_DEPENDENCY,
            f"缺少依赖包：{exc.module}（当前渲染环境里没有它）",
            params=params,
            traceback_text=exc.traceback_text,
        )
    if exc.code == "worker_timeout":
        return _err(
            ERROR_TIMEOUT,
            f"脚本执行超时（入口 {entry}）",
            params={"entry": entry},
            traceback_text=exc.traceback_text,
        )
    if exc.code == "session_dead":
        return _err(
            ERROR_CANCELLED,
            "试运行被中断（会话在执行期间被终止）",
            traceback_text=exc.traceback_text,
        )
    return _err(
        ERROR_PROBE_FAILED,
        f"试运行失败（入口 {entry}）：{reason}",
        params={"entry": entry, "reason": reason},
        traceback_text=exc.traceback_text,
    )


def entry_candidates(figures_dir: str | Path, script: str) -> list[str]:
    """该脚本值得一试的 entry 列表（静态推断优先，去重保序）。

    两级静态推断：`discover.analyze_script`（存图口径，注册表草稿同款）给出
    的入口最优先；`discover.probe_entry_candidates`（绘图宽口径，show-only
    也解得出）补齐其余。脚本解析不了时退回盲试 FALLBACK_ENTRIES——运行期
    会给出真正的报错（语法错误的 traceback 比静态猜测有解释力）。
    """
    path = Path(figures_dir) / script
    info = discover.analyze_script(path, Path(figures_dir))
    static = discover.probe_entry_candidates(path)
    out: list[str] = []
    if info:
        out.append(info["entry"])
    for e in static if static is not None else FALLBACK_ENTRIES:
        if e not in out:
            out.append(e)
    if not out:
        out.append(registry.INLINE_ENTRY)
    return out


def probe(
    figures_dir: str | Path, script: str, entries: list[str] | None = None, should_cancel=None
) -> dict:
    """跑一次脚本，返回它真实产出的 stem 与每张图的结构化描述。

    返回 dict：

        script       项目相对路径（原样回显）
        entry        成功的入口；失败为 None
        stems        真实产出的 stem（排序）
        descriptors  worker build 响应里那份 CapturedFigureDescriptor payload
                     列表（figcapture 唯一实现，按捕获顺序）——调用方从这里
                     就拿得到每张图的捕获来源（savefig / pyplot）、尺寸与
                     写回能力，不必再猜
        tried        依次试过的 entry
        error        None，或 {code, message, params, traceback}（稳定码表
                     见模块头）；每个候选 entry 都失败时保留**第一个**候选
                     的报错（静态推断的那个，对用户最有解释力）
        timings      成功那次 build 的计时（worker v1 响应原样透传）
        dropped_figures  pyplot 兜底超过上限被丢掉的张数（0 = 没丢）

    **成功路径只执行一次**：build 好的热会话留在池里不 invalidate，随后的
    预览 / 渲染 / 登记拿着 (script, entry) 直接复用，不再重跑脚本。失败的
    entry 各自新建 worker（错误入口的进程绝不复用），互不污染。

    `should_cancel` 是协作取消的判据（app 层的 cancel 端点置 Event 并
    `pool.force_cancel` 硬杀在跑的 worker）：一旦为真，**不再尝试下一个
    entry**，并把本轮的失败（多半是被杀 worker 的「进程崩溃」）如实归类为
    `execution_cancelled`——被用户取消的 probe 报「脚本坏了」是撒谎。
    """
    figures_dir = str(Path(figures_dir))
    cancelled = (lambda: bool(should_cancel())) if callable(should_cancel) else (lambda: False)
    empty = {
        "script": script,
        "entry": None,
        "stems": [],
        "descriptors": [],
        "tried": [],
        "error": None,
        "timings": {},
        "dropped_figures": 0,
    }
    _cancel_err = lambda: _err(  # noqa: E731 —— 两个出口共用同一句
        ERROR_CANCELLED, "试运行已取消"
    )
    script_path = Path(figures_dir) / script
    if not script_path.is_file():
        return {
            **empty,
            "error": _err(ERROR_NOT_FOUND, f"脚本不存在: {script}", params={"script": script}),
        }
    if entries is not None:
        bad = [e for e in entries if not registry.valid_entry(e)]
        if bad:
            return {
                **empty,
                "error": _err(
                    ERROR_INVALID_ENTRY,
                    f"entry 非法: {', '.join(map(str, bad))}",
                    params={"entry": ", ".join(map(str, bad))},
                ),
            }

    tried: list[str] = []
    first_error: dict | None = None
    for entry in entries or entry_candidates(figures_dir, script):
        if cancelled():
            return {**empty, "tried": tried, "error": _cancel_err()}
        tried.append(entry)
        # 每次换 entry 都要换掉旧会话：worker 的 entry 是启动参数，
        # 复用旧进程等于一直用错的入口重试。
        pool.invalidate(script, figures_dir)
        try:
            # `pool.build` = get + ensure_built + **一次项目环境自动 fallback**
            # （内置 runtime 缺依赖 → 项目自己的 .venv 接手，ADR 0018）。
            # 探测是「跑一次用户脚本」最主要的入口，自动接手必须覆盖它——
            # 否则素材库里能打开的项目，`tavotto open` 打不开。
            worker, resp = pool.build(script, figures_dir, entry)
        except pool.WorkerError as exc:
            pool.invalidate(script, figures_dir)
            if cancelled():
                # worker 是被 cancel 硬杀的：报「进程崩溃」是把用户的取消
                # 说成脚本的错。不再试下一个 entry——取消就是取消。
                LOG.info("探测被取消 %s [entry=%s]", script, entry)
                return {**empty, "tried": tried, "error": _cancel_err()}
            LOG.info("探测失败 %s [entry=%s]: %s", script, entry, exc)
            if first_error is None:
                first_error = _error_from_worker(exc, entry)
            continue
        stems = sorted(resp.get("stems") or {})
        if stems:
            LOG.info("探测成功 %s [entry=%s] → %s", script, entry, stems)
            return {
                "script": script,
                "entry": entry,
                "stems": stems,
                "descriptors": list(resp.get("descriptors") or []),
                "tried": tried,
                "error": None,
                "timings": dict(resp.get("timings") or {}),
                "dropped_figures": int(resp.get("dropped_figures") or 0),
            }
        # 跑通了但一张图都没产出：这个 entry 大概率不是出图入口，换下一个
        if first_error is None:
            first_error = _err(
                ERROR_NO_FIGURE,
                f"脚本跑通了，但没有捕获到任何 Figure（入口 {entry} 可能不出图）",
                params={"entry": entry},
            )
        pool.invalidate(script, figures_dir)

    return {
        **empty,
        "tried": tried,
        "error": first_error
        or _err(ERROR_PROBE_FAILED, "无法确定入口", params={"entry": "", "reason": "无法确定入口"}),
    }


def _short(message: str, traceback_text: str = "", limit: int = 600) -> str:
    """给用户看的错误：优先 traceback 末尾几行（真正的异常在那儿）。"""
    tail = ""
    if traceback_text:
        lines = [ln for ln in traceback_text.strip().splitlines() if ln.strip()]
        tail = "\n".join(lines[-6:])
    text = f"{message}\n{tail}".strip() if tail else message
    return text[:limit]


def _live_stem_conflicts(figures_dir: str | Path, script: str, stems: list[str]) -> dict[str, str]:
    """本次产出里被**另一份仍在磁盘上的脚本**登记着的 stem → 归属脚本。

    脚本已经不在磁盘上的旧条目不算冲突（改名/删除后的重探测该顺畅走完，
    死条目的 stem 由 register 顺手摘掉）。用新 Registry 实例查，不碰模块级
    默认实例的状态。
    """
    reg = registry.Registry()
    try:
        reg.load(figures_dir)
    except (FileNotFoundError, RuntimeError):
        return {}  # 没有注册表 / 注册表坏了：没有冲突可言
    out: dict[str, str] = {}
    for stem in stems:
        info = reg.for_stem(stem)
        if info is None or info["script"] == script:
            continue
        if (Path(figures_dir) / info["script"]).is_file():
            out[stem] = info["script"]
    return out


def probe_and_register(
    figures_dir: str | Path, script: str, cost: str = "medium", should_cancel=None
) -> dict:
    """探测成功就写进 tavotto_registry.json 并重载注册表。

    失败原样返回（`registered: False`），**注册表零改动**——不留半写文件、
    不摘别人的 stem。产出的 stem 已被**另一份仍存在的脚本**登记时同样不写
    （`multiple_stem_conflict`）：静默把 stem 抢过来会让原脚本的登记凭空
    消失，裁决走「手工填写」（PUT /api/registry——那条路是用户显式指认的
    归属，覆盖才是语义）。归属脚本已不在磁盘上的死条目不算冲突。

    取消（`should_cancel`）输给成功：脚本在取消到达前跑完了就是跑完了，
    照常登记——「已经发生的执行」不因迟到的取消而假装没发生。
    """
    result = probe(figures_dir, script, should_cancel=should_cancel)
    if not result["stems"]:
        return {**result, "registered": False}
    conflicts = _live_stem_conflicts(figures_dir, script, result["stems"])
    if conflicts:
        detail = "；".join(f"{stem} → {owner}" for stem, owner in sorted(conflicts.items()))
        return {
            **result,
            "registered": False,
            "stem_conflicts": conflicts,
            "error": _err(
                ERROR_STEM_CONFLICT,
                f"产出的图名已被其它脚本登记：{detail}（在脚本注册表里手工裁决归属后重试）",
                params={"detail": detail},
            ),
        }
    discover.register(figures_dir, script, result["stems"], entry=result["entry"], cost=cost)
    registry.load(figures_dir)
    return {**result, "registered": True}


# ---------------------------------------------------------------------------
# 脚本清单（Session 3：所有合理项目脚本可见）
# ---------------------------------------------------------------------------
#: 清单条目的稳定 reason code（契约，改语义才改码）。
REASON_REGISTERED = "registered"  # 已登记（注册表里有这条脚本）
REASON_STATIC = "static_candidate"  # 静态解得出产物，可直接登记
REASON_DYNAMIC = "dynamic_stems"  # 有存图调用但 stem 来自运行期数据
REASON_NO_STATIC_OUTPUT = "no_static_output"  # 没有存图调用（可能创建 Figure）
REASON_INFRASTRUCTURE = "infrastructure"  # 测试/工具/样式模块（按文件名判）
REASON_UNPARSEABLE = "unparseable"  # 读不动或语法错误（试运行会给真报错）


def script_inventory(figures_dir: str | Path, registered: set[str] | None = None) -> list[dict]:
    """项目内全部合理 .py 的清单——「列给用户挑」的唯一数据源。

    walk 规则复用 `discover.iter_all_scripts`（PRUNE_DIRS / MAX_DEPTH /
    隐藏项跳过，同一个实现），路径写法复用 `discover.rel_key`。被 prune 的
    目录（.venv / build / node_modules……）里的脚本**不列**——那是环境与
    构建产物，不是用户的绘图脚本。

    每条：{script, registered, static_stems, entry_candidates, reason,
    can_probe}。reason 是稳定 code（见 REASON_*，优先级从上往下判）；
    can_probe 对列出的每个 .py 都是 True——后端 probe 本来就接受任意项目内
    脚本，清单的职责是**解释现状**，不是再设一道门。

    `registered` 不传时读图库自己的注册表（传的话用调用方的——app 层手里
    已有 ctx.registry，不必重读文件）。
    """
    figures_dir = Path(figures_dir)
    if registered is None:
        reg = registry.Registry()
        try:
            reg.load(figures_dir)
            registered = set(reg.all_scripts())
        except (FileNotFoundError, RuntimeError):
            registered = set()
    out: list[dict] = []
    for path in discover.iter_all_scripts(figures_dir):
        rel = discover.rel_key(path, figures_dir)
        info = discover.analyze_script(path, figures_dir)
        static = discover.probe_entry_candidates(path)
        candidates: list[str] = []
        if info:
            candidates.append(info["entry"])
        for e in static if static is not None else FALLBACK_ENTRIES:
            if e not in candidates:
                candidates.append(e)
        if rel in registered:
            reason = REASON_REGISTERED
        elif discover.is_infrastructure_name(path.name):
            reason = REASON_INFRASTRUCTURE
        elif info is None:
            # analyze 的 None 分不清「不出图」与「解析不了」——静态候选
            # 也给不出来的才是后者。
            reason = REASON_UNPARSEABLE if static is None else REASON_NO_STATIC_OUTPUT
        elif info["dynamic_names"]:
            reason = REASON_DYNAMIC
        else:
            reason = REASON_STATIC
        out.append(
            {
                "script": rel,
                "registered": rel in registered,
                "static_stems": list(info["stems"]) if info else [],
                "entry_candidates": candidates,
                "reason": reason,
                "can_probe": True,
            }
        )
    return out
