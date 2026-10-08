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

from . import (
    discover,
    figcapture,
    inputbroker,
    inputremap,
    pool,
    projectenv,
    registry,
    runconfig,
    runtimeasset,
    taskdiag,
)

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
#: 两条从 worker 原样透传的码（`worker.SCRIPT_NEEDS_ARGUMENTS` / `SCRIPT_EXITED`）：
#: 脚本要命令行参数而 Tavotto 不带参数运行 / 脚本自己 `sys.exit` 了。归成通用的
#: `script_probe_failed` 会把出路（给默认值或 `tavotto run` / 去掉那句 exit）说丢。
ERROR_NEEDS_ARGUMENTS = "script_needs_arguments"
ERROR_SCRIPT_EXITED = "script_exited"
#: 脚本 `input()`（ADR 0099）：没人能答 / 等到超时脚本没接住 EOF。worker 原样透传。
ERROR_NEEDS_INPUT = "script_needs_input"
ERROR_INPUT_TIMEOUT = "script_input_timeout"
#: 脚本要读的数据找不到（ADR 0106）：worker 说得出缺的是哪一串。载荷 `missing_input` 原样带出，
#: 素材库这条入口弹的是与画布同一个「指认数据位置」对话框。
ERROR_MISSING_INPUT = pool.MISSING_INPUT_CODE

#: 与「入口猜没猜对」无关的失败码：换一个入口只是把顶层代码再跑一遍，得到同一个失败（T01，F7：缺参脚本的
#: 顶层代码曾跑满 3 个入口）。缺参 / 要输入 / 读不到数据 / 缺包 / 超时 / 取消都在里面。
_ENTRY_INDEPENDENT_CODES = frozenset(
    {
        ERROR_NEEDS_ARGUMENTS,
        ERROR_NEEDS_INPUT,
        ERROR_INPUT_TIMEOUT,
        ERROR_MISSING_INPUT,
        ERROR_MISSING_DEPENDENCY,
        ERROR_CANCELLED,
        "worker_timeout",
        pool.BUILD_TIMEOUT_CODE,
    }
)


def entry_retry_allowed(exc: pool.WorkerError) -> bool:
    """这次失败之后值不值得换下一个入口再试一次。只有「入口猜错」一类（找不到函数 / 脚本没出图）才换；
    起会话之前的两道门（运行目录 / 依赖准备）是「要用户先答」，同样不换。判据只此一份，探测的入口循环用它。"""
    if getattr(exc, "code", "") in _ENTRY_INDEPENDENT_CODES:
        return False
    return not (
        isinstance(getattr(exc, "confirmation", None), dict)
        or isinstance(getattr(exc, "dependency_preparation", None), dict)
    )


#: traceback 进诊断详情的截断上限（完整日志仍在 worker.log）。
_TRACEBACK_LIMIT = 4000


def _err(code: str, message: str, params: dict | None = None, traceback_text: str = "") -> dict:
    """结构化探测错误：主文案（中文回退）+ code + params；traceback 单列。"""
    out = {"code": code, "message": message, "params": params or {}}
    tb = (traceback_text or "").strip()
    if tb:
        out["traceback"] = tb[-_TRACEBACK_LIMIT:]
    return out


def _error_from_worker(
    exc: pool.WorkerError, entry: str, *, figures_dir: str = "", script: str = ""
) -> dict:
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
        out = _err(
            ERROR_MISSING_DEPENDENCY,
            (
                f"缺少依赖包：{exc.module}（当前渲染环境里没有它）"
                if exc.module
                else "缺少依赖包（含敏感参数的运行不显示包名）"
            ),
            params=params,
            traceback_text=exc.traceback_text,
        )
        # 「能不能一键装上」（ADR 0019）。**素材库这条路必须也带上它**：
        # 用户打开旧项目走的就是这里，只在渲染端点上给恢复引导的话，
        # 「素材库里打不开、面板里能修」又是一次两个入口两个答案。
        if figures_dir and exc.module:
            from . import deprepair

            out["dependency_repair"] = deprepair.offer(figures_dir, script, exc.module, detail)
        return out
    if exc.code in (ERROR_NEEDS_INPUT, ERROR_INPUT_TIMEOUT):
        # 脚本要输入而没人能答 / 等到超时（ADR 0099）：提示原文走 params，界面按 code 翻
        extra = getattr(exc, "extra", None) or {}
        return _err(
            exc.code,
            str(exc),
            params={"prompt": str(extra.get("prompt") or "")},
            traceback_text=exc.traceback_text,
        )
    if exc.code in (ERROR_NEEDS_ARGUMENTS, ERROR_SCRIPT_EXITED):
        # 文案由前端按 code 翻；`error` 给 `script_exited` 的占位符（`SystemExit: 2`
        # 那一行），`script_needs_arguments` 的 usage 在 traceback 里（pool 已接上）。
        lines = [ln for ln in (exc.traceback_text or "").splitlines() if ln.strip()]
        params = {"error": (lines[-1].strip() if lines else str(exc))[:200]}
        if exc.code == ERROR_NEEDS_ARGUMENTS:
            # T03：用户给了精确 token 时（`argv_count` > 0）脚本自己的解析器仍拒绝；`parse_kind` 只在 worker
            # 有 argparse 的实际证据时才有（missing_required / invalid_value / unknown）。
            # 只带类别与个数，不带参数值。
            extra = getattr(exc, "extra", None) or {}
            if extra.get("argv_count"):
                params["argv_count"] = str(int(extra["argv_count"]))
            if extra.get("parse_kind"):
                params["parse_kind"] = str(extra["parse_kind"])
        out = _err(
            exc.code,
            str(exc),
            params=params,
            traceback_text=exc.traceback_text,
        )
        # 先 `exists()` 判空再 exit（ADR 0106）：脚本里写着、此刻哪儿都找不到的路径随错误带出
        offer = getattr(exc, "missing_input", None)
        if isinstance(offer, dict):
            out["missing_input"] = offer
        return out
    if exc.code == ERROR_MISSING_INPUT:
        offer = getattr(exc, "missing_input", None)
        # 占位符与渲染入口同一个：`{{error}}` 是 traceback 的最后一行（`FileNotFoundError: …`）
        lines = [ln for ln in (exc.traceback_text or "").splitlines() if ln.strip()]
        out = _err(
            ERROR_MISSING_INPUT,
            str(exc),
            params={"error": (lines[-1].strip() if lines else str(exc))[:200]},
            traceback_text=exc.traceback_text,
        )
        if isinstance(offer, dict):
            out["missing_input"] = offer
        return out
    # 起会话之前的两道门（U03 的运行目录 / U04 的依赖准备）：它们是「需要输入」，不是失败——
    # code 原样带出、载荷原样带出（与渲染端点 `_worker_error_payload` 同一形状），素材库这条
    # 入口才能弹同一个确认框，而不是一句「试运行失败」。
    confirmation = getattr(exc, "confirmation", None)
    if isinstance(confirmation, dict):
        out = _err(exc.code, str(exc))
        out["confirmation"] = confirmation
        return out
    dependency = getattr(exc, "dependency_preparation", None)
    if isinstance(dependency, dict):
        out = _err(exc.code, str(exc))
        out["dependency_preparation"] = dependency
        return out
    # build 超时有自己的码（ADR 0048）；试运行走的正是 build，两个都要认——
    # 漏掉的话「脚本执行超时」会退化成一句通用的「试运行失败」。
    if exc.code in ("worker_timeout", pool.BUILD_TIMEOUT_CODE):
        return _err(
            ERROR_TIMEOUT,
            f"脚本执行超时（入口 {entry}）",
            params={"entry": entry},
            traceback_text=exc.traceback_text,
        )
    if exc.code == "session_dead":
        # 退出状态在手（#435）：进程**自己**死的（脚本把它带崩、access violation、
        # sys.exit）不是「被中断」——报成取消等于把一次真崩溃藏起来。只有被杀 /
        # 状态未知的才归 execution_cancelled（用户点的取消在调用方就已经分出去了）。
        if pool.exited_on_its_own((getattr(exc, "extra", None) or {}).get("exit")):
            return _err(
                ERROR_PROBE_FAILED,
                f"试运行失败（入口 {entry}）：{exc}",
                params={"entry": entry, "reason": str(exc)},
                traceback_text=exc.traceback_text,
            )
        return _err(
            ERROR_CANCELLED,
            "试运行被中断（会话在执行期间被终止）",
            traceback_text=exc.traceback_text,
        )
    out = _err(
        ERROR_PROBE_FAILED,
        f"试运行失败（入口 {entry}）：{reason}",
        params={"entry": entry, "reason": reason},
        traceback_text=exc.traceback_text,
    )
    # C++ 读取器找不到数据（ADR 0110 §一）：码照旧是「试运行失败」，载荷原样带出——素材库弹同一个
    # 「指认数据位置」对话框（`pool._offer_missing_input` 只在对上脚本里一串常量时挂）
    offer = getattr(exc, "missing_input", None)
    if isinstance(offer, dict):
        out["missing_input"] = offer
    return out


def entry_candidates(figures_dir: str | Path, script: str) -> list[str]:
    """该脚本值得一试的 entry 列表（静态推断优先，去重保序）。

    两级静态推断：`discover.analyze_script`（存图口径，注册表草稿同款）给出
    的入口最优先；`discover.probe_entry_candidates`（绘图宽口径，show-only
    也解得出）补齐其余。脚本解析不了时退回盲试 FALLBACK_ENTRIES——运行期
    会给出真正的报错（语法错误的 traceback 比静态猜测有解释力）。
    """
    # 脚本钉在项目根之内的 realpath 才读；在外面就是「解析不了」那一档，退回盲试列表
    real = projectenv.contained_path(figures_dir, script)
    if real is None:
        return list(FALLBACK_ENTRIES)
    path = Path(real)
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


#: 结果里这次运行每一问去向的键（T08，`inputbroker.InputFacts.payload()`）：只有计数与闭集理由。
#: HTTP 端点在落任务诊断之后把它从响应体里摘掉（响应形状不变）。
INPUT_FACTS_KEY = "input_facts"


def probe(
    figures_dir: str | Path,
    script: str,
    entries: list[str] | None = None,
    should_cancel=None,
    run=None,
    on_acquired=None,
) -> dict:
    """`_probe()` + 这次运行的输入去向（T08）：成功取 worker 的 `last_input_facts`，失败取第一处错误那次
    build 挂在异常上的 `input_facts`——与 `error` 说的是同一次尝试。"""
    box: dict = {}
    result = _probe(
        figures_dir, script, entries, should_cancel, run, facts=box, on_acquired=on_acquired
    )
    if box.get("facts") is not None:
        result[INPUT_FACTS_KEY] = box["facts"]
    return result


def _probe(
    figures_dir: str | Path,
    script: str,
    entries: list[str] | None = None,
    should_cancel=None,
    run=None,
    *,
    facts: dict,
    on_acquired=None,
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

    `run`（T03）：用户给的精确 argv 的运行配置（`execspec.RunSelection`），None = 不带参数（旧行为）。
    它同时是池键的一段：这次执行的热会话、作废、取消都只作用于这份配置，不碰同脚本别的参数。

    `should_cancel` 是协作取消的判据（app 层的 cancel 端点置 Event 并
    `pool.force_cancel` 硬杀在跑的 worker）：一旦为真，**不再尝试下一个
    entry**，并把本轮的失败（多半是被杀 worker 的「进程崩溃」）如实归类为
    `execution_cancelled`——被用户取消的 probe 报「脚本坏了」是撒谎。

    `on_acquired(worker, owned)`（T09b）：每次取到会话、执行之前报一次——`owned` 是 `pool.acquired_here`：True =
    这次试运行新建的、False = 别人（渲染路 / 准备会话）正在用的同键会话、None = 说不清。取消端点据此**按 owner**
    只关自己建的那条；共享会话只停自己的等待意图，一根手指不碰（ADR 0116 §三，与准备会话的取消同一条规则）。
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

    run_ctx = {"run": run} if run is not None else {}

    def before_build(worker):
        owned = pool.acquired_here(worker)
        if on_acquired is not None:
            on_acquired(worker, owned)
        # Cancellation may arrive while get() discovers an interpreter, before the
        # worker exists in the pool. Check again after every acquisition, including fallback.
        if cancelled():
            if owned is False:
                # 别人的会话（渲染路正在用的同键会话）：本次试运行只是放弃等待，不杀它
                raise pool.WorkerError("试运行已取消", code=ERROR_CANCELLED)
            if not pool.force_cancel(script, figures_dir, expected_worker=worker, **run_ctx):
                worker.force_kill()  # Already detached: never retire its replacement.
            raise pool.WorkerError("试运行已取消", code=ERROR_CANCELLED)

    tried: list[str] = []
    first_error: dict | None = None
    for entry in entries or entry_candidates(figures_dir, script):
        if cancelled():
            return {**empty, "tried": tried, "error": _cancel_err()}
        tried.append(entry)
        # 每次换 entry 都要换掉旧会话：worker 的 entry 是启动参数，
        # 复用旧进程等于一直用错的入口重试。
        pool.invalidate(script, figures_dir, run, only_run=True)
        try:
            # `pool.build` = get + ensure_built + **一次项目环境自动 fallback**
            # （内置 runtime 缺依赖 → 项目自己的 .venv 接手，ADR 0018）。
            # 探测是「跑一次用户脚本」最主要的入口，自动接手必须覆盖它——
            # 否则素材库里能打开的项目，`tavotto open` 打不开。
            _worker, resp = pool.build(
                script, figures_dir, entry, before_build=before_build, **run_ctx
            )
        except pool.WorkerError as exc:
            if cancelled():
                # worker 是被 cancel 硬杀的：报「进程崩溃」是把用户的取消
                # 说成脚本的错。不再试下一个 entry——取消就是取消。
                LOG.info("探测被取消 %s [entry=%s]", script, entry)
                facts["facts"] = getattr(exc, "input_facts", None)  # 在等输入时被停：诊断里看得到
                return {**empty, "tried": tried, "error": _cancel_err()}
            pool.invalidate(script, figures_dir, run, only_run=True)
            LOG.info("探测失败 %s [entry=%s]: %s", script, entry, exc)
            if first_error is None:
                first_error = _error_from_worker(exc, entry, figures_dir=figures_dir, script=script)
                facts["facts"] = getattr(exc, "input_facts", None)
            if not entry_retry_allowed(exc):
                break  # 与入口无关的失败：再换入口只会把顶层代码重跑一遍
            continue
        if cancelled():
            # 共享会话的等待者被取消：别人的 build 照常跑完（不杀它），但本次试运行不得再当成功返回——
            # 否则 probe_and_register 会替换注册表 stem，可能摘掉别的参数建出来的图。
            LOG.info("探测完成但已被取消 %s [entry=%s]", script, entry)
            facts["facts"] = getattr(_worker, "last_input_facts", None)
            return {**empty, "tried": tried, "error": _cancel_err()}
        stems = sorted(resp.get("stems") or {})
        facts["facts"] = getattr(_worker, "last_input_facts", None)
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
                # 这次试运行按哪一代改指表跑的（ADR 0106 §五）：登记 / 物化落地前核对
                "remap_generation": getattr(_worker, "remap_generation", None),
                # T03：这次执行绑定的运行配置引用（不透明 id；argv 原文不出现在结果里）
                **({"run_config": run.config_id} if run is not None else {}),
            }
        # 跑通了但一张图都没产出：这个 entry 大概率不是出图入口，换下一个
        if first_error is None:
            first_error = _err(
                ERROR_NO_FIGURE,
                f"脚本跑通了，但没有捕获到任何 Figure（入口 {entry} 可能不出图）",
                params={"entry": entry},
            )
            # 多半是先 `exists()` 判空再自己退出（ADR 0106）：脚本里写着、此刻哪儿都找不到的路径
            # 一并带上，界面据此弹「指认数据位置」——与渲染入口的「没出图」同一份载荷
            offer = pool.missing_input_offer(script, figures_dir)
            if offer is not None:
                first_error["missing_input"] = offer
        pool.invalidate(script, figures_dir, run, only_run=True)

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
    figures_dir: str | Path,
    script: str,
    cost: str = "medium",
    should_cancel=None,
    run=None,
    on_acquired=None,
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
    result = probe(
        figures_dir,
        script,
        should_cancel=should_cancel,
        **({"run": run} if run is not None else {}),
        **({"on_acquired": on_acquired} if on_acquired is not None else {}),
    )
    if not result["stems"]:
        return {**result, "registered": False}
    return register_probed(figures_dir, script, result, cost=cost)


def register_probed(
    figures_dir: str | Path, script: str, result: dict, cost: str = "medium"
) -> dict:
    """把一次**已经成功**的执行（`stems` 非空）按真实产出登记进注册表：冲突 / 改指代次的判据只此一份。

    试运行（`probe_and_register`）与准备会话（`prepsession`，T01）的执行结束后都走这里——登记不是
    探测的私事，是「捕获到的图要能被编辑请求按 stem 找到」的那一步。`result` 至少带 `stems` /
    `entry` / `remap_generation`；原样展开回去，加 `registered`（与可能的 `error` / `stem_conflicts`）。
    """
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
    # 登记在改指表的锁里、核对过代次才落地（ADR 0106 §五）：试运行途中改了指认，产出的图名 / 描述
    # 是按旧位置的数据来的——丢弃、可重试，注册表零改动
    append = bool(result.get("run_config"))
    try:
        with inputremap.landing(figures_dir, result.get("remap_generation")):
            # 无参数运行整条替换（T03 已知缺口）：替换之前这个脚本名下、这次没产出的图名会就此失去关联——多半是此前**带参数**
            # 产出的。记下来如实告诉用户（`unlinked_stems`），用原参数再运行一次即可并回来；不改注册表格式（T09b）
            before = [] if append else discover.registered_stems(figures_dir, script)
            # 「替换掉了哪些此前真捕获过的图名」的证据在**注册表提交之前**算完：任何环节在这里失败，整次登记失败、
            # 注册表字节不变——绝不能出现「端点报失败、注册表却已丢了旧 stem」（r4220769153）
            unlinked = sorted(
                s
                for s in set(before) - set(result["stems"])
                if was_captured(figures_dir, script, s)
            )
            discover.register(
                figures_dir,
                script,
                result["stems"],
                entry=result["entry"],
                cost=cost,
                # T03：带运行配置的执行只是这个脚本的**另一份配置**——并进去，不换掉。整条替换会让
                # 无参数 / 别的参数登记过的图当场失去编辑入口（注册表是 stem 归属的唯一权威）
                append=append,
            )
            registry.load(figures_dir)
            # stems 可能由数据决定：记下是在哪张改指表下登记的，表变了渲染时据此重新登记
            inputremap.record_registration(figures_dir, script)
            if result.get("run_config"):
                inputremap.record_registration(figures_dir, script, result["run_config"])
    except inputremap.RemapChanged as exc:
        LOG.info("试运行结果作废（%s）: %s", exc, script)
        return {
            **result,
            "registered": False,
            "error": _err(inputremap.ERROR_CHANGED, str(exc)),
        }
    return {**result, "registered": True, **({"unlinked_stems": unlinked} if unlinked else {})}


def was_captured(figures_dir: str | Path, script: str, stem: str) -> bool:
    """这个图名此前真被某次执行捕获过：runtime cache 里有它的物化记录（无参数或这个脚本登记过的任一份运行配置）。

    注册表里的图名不全是执行结果——打开项目时的静态扫描会把字面量 `savefig` 的名字先登记上（T00 deliberate-boundary），
    条件分支里的那张从没产出过。对它说「此前带其他参数生成的……已不再关联」是假话（T11：第一次无参数运行就报）。
    cache 被按体积回收过的旧图会漏报——宁可少说，不说假话。导入即扫描判「这个脚本已经连着可编辑的图」也用它
    （`projscan._linked_scripts`），一份判据两处用。

    运行配置登记读不出 / 来自新版本（`RunConfigError`）时，带配置那几份变体当**没有证据**：这是「是否捕获过」的
    证据判断，宁可少说，不能因此炸掉扫描或登记。敏感配置的秘密值已不在（重启后只剩 ID 占位）同样当没有证据：cache 在，但打开会得到
    `run_config_secret_missing`，不是「可直接编辑」（r4221248582；两种情形共用 `runconfig.executable_configs_of`）。它**不**放行任何执行——读不出配置却要按空 argv 运行，由执行侧的
    `run_config_unreadable` 显式拒绝（那条不在这里）。"""
    configs = runconfig.executable_configs_of(figures_dir, script)
    ids = [figcapture.runtime_asset_id(script, stem)] + [
        figcapture.runtime_asset_id(script, stem, cfg.id) for cfg in configs
    ]
    return any(runtimeasset.load_metadata(figures_dir, asset_id) is not None for asset_id in ids)


# ---------------------------------------------------------------------------
# 任务绑定诊断（T04）
# ---------------------------------------------------------------------------
#: 试运行的终局词汇（与准备的 `ready` / `error` / `cancelled` 同名，界面一套词）。
OUTCOME_READY = "ready"
OUTCOME_ERROR = "error"
OUTCOME_CANCELLED = "cancelled"


def outcome_of(result: dict) -> str:
    # 已登记后物化 / 默认配置 / 刷新仍可能失败，成功登记不能盖掉这次终局错误。
    error = result.get("error")
    if error:
        return OUTCOME_CANCELLED if error.get("code") == ERROR_CANCELLED else OUTCOME_ERROR
    return OUTCOME_READY if result.get("registered") else OUTCOME_ERROR


def diagnostic_projection(
    result: dict,
    *,
    attempt_id: str,
    argv_count: int,
    run_config: str | None,
    elapsed_ms: int | None,
    input_facts: dict | None = None,
) -> dict:
    """一次试运行的**白名单**投影。逐字段挑，不读 `result` 的其余部分：`error.message` / `params` /
    `traceback` 是脚本自己的异常文字，`descriptors` / `stems` 有图名与路径，`entry` 是用户的函数名，
    `stem_conflicts` 是别的脚本的名字——都不进。"""
    err = result.get("error") or {}
    return taskdiag.clean(
        {
            "snapshot_version": taskdiag.SNAPSHOT_VERSION,
            "kind": taskdiag.KIND_SCRIPT_RUN,
            "attempt_id": taskdiag.ident(attempt_id),
            "outcome": outcome_of(result),
            "target": {"category": "script", "has_entry": bool(result.get("entry"))},
            "config": {"argv_count": argv_count, "run_config": taskdiag.ident(run_config)},
            "execution": {
                "captured_count": taskdiag.count(len(result.get("stems") or [])),
                "registered": taskdiag.flag(bool(result.get("registered"))),
                "stem_conflict_count": len(result.get("stem_conflicts") or {}),
            },
            "error": {"code": taskdiag.code(err.get("code"))} if err else None,
            "input": inputbroker.facts_projection(input_facts),
            "timing": {"elapsed_ms": taskdiag.count(elapsed_ms)},
        }
    )


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
    # 目标解析器（U03 / FO12）：宿主 AST 不认识的合法语法交给项目自己的解释器再解析一遍。
    # 只读此刻定下来的路径（`pool.peek_worker_python`，T02：不复检、不起进程）；没有就没有目标解析器。
    found = pool.peek_worker_python(figures_dir)
    target_python = found[0] if found else None
    return [
        inventory_entry(
            path,
            figures_dir,
            registered,
            discover.inspect_script(path, figures_dir, target_python=target_python),
        )
        for path in discover.iter_all_scripts(figures_dir)
    ]


def inventory_entry(path: Path, figures_dir: Path, registered: set[str], seen: dict) -> dict:
    """一个脚本的清单条目：`seen` 是 `discover.inspect_script()` 的结果——**分类只有这一份**。

    `script_inventory`（用户主动打开清单）与导入即扫描（`projscan`，T02，带预算、不传目标解析器）
    各自负责「读不读、怎么读」，读出来之后的 reason / entry 候选判据共用这里，不分叉成两套脚本分类。
    纯函数：不碰文件系统、不起进程。"""
    rel = discover.rel_key(path, figures_dir)
    info, problem, static = seen["info"], seen["problem"], seen["entry_candidates"]
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
    elif problem is not None:
        # 读不动 / 解码不了 / 两边都判语法错误：`problem` 说清是哪一种（U03）。
        # 文件**照样在清单里**，可以试运行——运行期会给出真正的报错。
        reason = REASON_UNPARSEABLE
    elif info is None:
        reason = REASON_NO_STATIC_OUTPUT  # 确认不产图（工具 / 样式模块）
    elif info["dynamic_names"]:
        reason = REASON_DYNAMIC
    else:
        reason = REASON_STATIC
    return {
        "script": rel,
        "registered": rel in registered,
        "static_stems": list(info["stems"]) if info else [],
        "entry_candidates": candidates,
        "reason": reason,
        "can_probe": True,
        # 加字段（老前端忽略）：解析不了时是哪一种问题；由目标解释器解析的标 parser
        "problem": problem,
        "parser": seen.get("parser"),
    }
