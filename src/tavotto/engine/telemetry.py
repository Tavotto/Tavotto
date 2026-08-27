"""匿名产品遥测（纯标准库，Flask 父进程 import）。

设计取舍，每一条都有理由：

- **没有 PostHog SDK**。Flask 父进程的依赖只有 flask + pymupdf（见 CLAUDE.md
  的「进程与依赖边界」）。为了埋点往那儿加一个分析 SDK，等于让一个可选的、
  失败无所谓的功能拥有让主进程起不来的权力。`urllib.request` 够用。
- **默认不发**。同意态是**三档** unset / enabled / disabled——「没设置」不等于
  「同意」。unset 时一个字节都不往外发，连 install_id 都不生成。
- **没有落盘队列**。断网时事件就丢了，这是刻意的：一个能把用户几周前的行为
  攒起来择机上传的队列，与「本地优先」这条产品承诺是冲突的，而且它必然要在
  磁盘上留下一份行为记录。丢事件是可接受的代价，攒事件不是。
- **distinct_id 是本机随机 UUIDv4**，不从任何机器信息推导（没有 MAC、没有
  machine GUID、没有主机名、没有用户名）。它是假名，不是身份：同一个人在两台
  机器上就是两个 id，重装一次也是新的 id。指标文档里因此只说
  「opted-in anonymous install」，不说「user」。
- **绝不影响产品行为**。队列满、超时、DNS 失败、代理挂了、返回体畸形——一律
  当场丢弃。`capture()` 不抛异常、不阻塞调用方、不写用户可见的日志。
- **只发白名单里的事件与属性**，且值只能是 bool / 有界整数 / 短枚举 / 受控
  版本串。没有任意字典、没有自由文本——文件名、路径、脚本、提示词、图内文字
  在**结构上**就发不出去，而不是靠调用方自觉。

环境变量：
    TAVOTTO_NO_TELEMETRY=1     硬开关，无视任何已保存的设置（CI / 冒烟 / 管理员）
    TAVOTTO_TELEMETRY_ENDPOINT 开发/自测时改投递地址（默认是 Tavotto 自己的代理）

**与 `TAVOTTO_NO_UPDATE_CHECK` 是两个独立开关**：一个管「查不查新版本」，
一个管「发不发匿名用量」，互不代管。
"""

from __future__ import annotations

import json
import os
import platform
import queue
import sys
import threading
import urllib.error
import urllib.request
import uuid

from . import brand, config

SCHEMA_VERSION = 1
#: 同意书版本。将来**实质性扩大采集范围**时 +1：保存下来的同意会立刻失效
#: （`enabled()` 当场变 false，一个字节都不再发），界面重新征求一次。
#:
#: 「当初同意的不是这一版」是个真问题，不是形式主义——用户同意的是 v1 那张
#: 事件表，你把表加长了还按老同意接着发，等于替他做了他没做过的决定。
#:
#: **重新同意不换 install_id**：换一个等于在升版那天凭空造出一批「新安装」，
#: 留存曲线断掉、活跃数虚高一轮，而实际上一个新用户都没有。
#: 判据用 `>=` 而不是 `==`：降级回旧版本时，保存的是范围更大的那一版同意，
#: 它涵盖旧版本要采的东西，不该反过来失效。
CONSENT_VERSION = 1

#: 生产默认投递地址。**只发到 Tavotto 自己的代理**，应用里没有、也不该有
#: 任何 PostHog 项目密钥：开源桌面应用里嵌的东西一律是公开的。
DEFAULT_ENDPOINT = "https://telemetry.tavotto.com/v1/events"

#: 有界内存队列。满了就丢——阻塞调用方是绝对不允许的，而无界队列在代理长时间
#: 不可达时会把内存吃光。
QUEUE_MAX = 128
NETWORK_TIMEOUT_S = 3

CONSENT_UNSET = "unset"
CONSENT_ENABLED = "enabled"
CONSENT_DISABLED = "disabled"
_CONSENTS = (CONSENT_UNSET, CONSENT_ENABLED, CONSENT_DISABLED)

_LOCK = threading.Lock()
_QUEUE: "queue.Queue[dict] | None" = None
_SENDER: threading.Thread | None = None
#: 本进程是不是**真的**把应用服务跑起来了。`tavotto --help` / `doctor` /
#: 单测 import / 打包脚本都只是 import 了这个模块，它们不是一次产品会话。
_session_mode: str | None = None
_app_started_sent = False


# ---------------------------------------------------------------------------
# 事件契约（客户端这一份）
#
# 代理侧 services/telemetry_proxy 有**逐字对应的另一份**，两边由
# tests/test_telemetry_proxy.py::test_client_and_proxy_contracts_match 逐条比对。
# 刻意不做「共享 schema 编译器」：两份显式的表加一条对拍用例，比一个为了消除
# 十几行重复而引入的机制更容易读、也更难出错。
# ---------------------------------------------------------------------------
def _enum(*values: str) -> dict:
    return {"kind": "enum", "values": tuple(values)}


def _int(maximum: int) -> dict:
    return {"kind": "int", "max": maximum}


BOOL = {"kind": "bool"}
VERSION = {"kind": "version"}

#: 每条产品事件都自动带上的受控属性。**刻意不带** platform.platform()、
#: 内核版本、主机名、Python 可执行文件路径：那些只增加指纹面，对
#: 「哪个平台的人在用」这个问题一点帮助都没有。
AUTO_PROPS: dict[str, dict] = {
    "app_version": VERSION,
    "platform": _enum("macos", "windows", "linux", "other"),
    "arch": _enum("arm64", "x86_64", "other"),
    "distribution": _enum("desktop", "pipx", "pip", "source", "unknown"),
}

EVENTS: dict[str, dict[str, dict]] = {
    # 这个匿名标识第一次显式打开遥测。ever_enabled 已经为真时不再重发——
    # 关掉再打开不是一个新用户。
    "telemetry_enabled": {"source": _enum("first_run", "settings")},
    # 一次真实的应用会话。CLI 子命令、打包、单测 import 都不算。
    "app_started": {"app_mode": _enum("desktop", "browser")},
    # 用户真的进了某个面板的图内编辑流程（不是每次预览图请求）。
    "figure_opened": {"asset_kind": _enum("pdf", "raster"), "editable": BOOL},
    # 一次语义编辑落进历史（拖动 = 1 条，不是 120 条 pointermove）。
    "figure_edit_completed": {
        "edit_kind": _enum("text", "series", "axes", "annotation", "layout", "style", "other"),
        "patch_count": _int(1000),
    },
    "canvas_created": {"creation_kind": _enum("blank", "project", "duplicate")},
    # 只有计数，没有任何一条检查项的文字、字体名、文件名或对象 id。
    "preflight_completed": {
        "errors": _int(1000),
        "warnings": _int(1000),
        "not_verifiable": _int(1000),
        "suggestions": _int(1000),
        "passed": BOOL,
    },
    # 激活事件：**导出真的成功、文件真的写完之后**才发。
    "export_completed": {"pdf": BOOL, "png": BOOL, "with_proof": BOOL, "panel_count": _int(1000)},
    # 只有「用了哪个 agent」。提示词 / 脚本 / 目标 / 会话 id 一个都不发。
    "ai_assistant_invoked": {"agent": _enum("codex", "claude", "other")},
    "update_completed": {"update_kind": _enum("desktop", "pip", "pipx"), "target_version": VERSION},
}


class _Invalid(Exception):
    """事件或属性不在白名单里。只在进程内使用，绝不外泄给调用方。"""


def _coerce(spec: dict, value):
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise _Invalid("bool")
        return value
    if kind == "int":
        # bool 是 int 的子类，混进来会让 patch_count=True 这种值悄悄通过
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Invalid("int")
        if value < 0 or value > spec["max"]:
            raise _Invalid("range")
        return value
    if kind == "enum":
        if value not in spec["values"]:
            raise _Invalid("enum")
        return value
    if kind == "version":
        text = str(value)
        if not text or len(text) > 32:
            raise _Invalid("version")
        if any(
            c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.+-_"
            for c in text
        ):
            raise _Invalid("version")
        return text
    raise _Invalid("kind")


def validate(event: str, properties: dict | None) -> dict:
    """按白名单校验并**只留下**认识的属性。不认识的一律拒绝，不是静默透传。

    这是防线本身：调用方就算把整条文件路径塞进 `properties`，也走不出这一步。
    """
    allowed = EVENTS.get(event)
    if allowed is None:
        raise _Invalid("event")
    out: dict = {}
    for key, value in (properties or {}).items():
        spec = allowed.get(key)
        if spec is None:
            raise _Invalid("property")
        out[key] = _coerce(spec, value)
    return out


# ---------------------------------------------------------------------------
# 硬开关与投递地址
# ---------------------------------------------------------------------------
def hard_disabled() -> bool:
    """`TAVOTTO_NO_TELEMETRY=1`：无视任何已保存的同意，一个字节都不发。

    CI / 冒烟 / 打包链路与管理员统一靠它。空串与 "0" 视为没设——
    环境变量被设成空值是常见的「取消设置」写法。
    """
    raw = (os.environ.get("TAVOTTO_NO_TELEMETRY") or "").strip().lower()
    return raw not in ("", "0", "false", "no")


def endpoint() -> str:
    return os.environ.get("TAVOTTO_TELEMETRY_ENDPOINT") or DEFAULT_ENDPOINT


# ---------------------------------------------------------------------------
# 设置（跟着 config.json 走）
# ---------------------------------------------------------------------------
def settings() -> dict:
    """**内部**视图，含 install_id。绝不整体交给前端或诊断包。"""
    cfg = config.load().get("telemetry") or {}
    consent = cfg.get("consent")
    if consent not in _CONSENTS:
        consent = CONSENT_UNSET
    install_id = cfg.get("install_id")
    if not isinstance(install_id, str) or not install_id:
        install_id = None
    return {
        "consent": consent,
        "install_id": install_id,
        "consent_version": int(cfg.get("consent_version") or 0),
        "ever_enabled": bool(cfg.get("ever_enabled")),
    }


def public_settings() -> dict:
    """给界面看的那份：**没有 install_id**。

    前端只需要知道「现在发不发」；把假名 id 交出去只会让它出现在截图、
    localStorage 与前端日志里，凭空多出几个泄漏面，而界面拿它没有任何用处。
    """
    st = settings()
    return {
        "consent": st["consent"],
        "enabled": enabled(),
        "hard_disabled": hard_disabled(),
        "consent_version": CONSENT_VERSION,
        "saved_consent_version": st["consent_version"],
        # 同意过、但同意的是上一版采集范围 —— 界面据此重新问一次
        "needs_reconsent": needs_reconsent(),
    }


def _save(patch: dict) -> dict:
    with _LOCK:
        cfg = config.load()
        merged = {**(cfg.get("telemetry") or {}), **patch}
        cfg["telemetry"] = {k: v for k, v in merged.items() if v is not None}
        config.save(cfg)
    return settings()


def _consent_is_current(st: dict) -> bool:
    """这份保存下来的同意，是不是**当前这一版采集范围**的同意。

    `enabled()` 与 `capture()` 共用这一个判据——分成两份迟早分叉，而分叉的
    表现是「界面说没在发，实际还在发」。
    """
    return st["consent"] == CONSENT_ENABLED and st["consent_version"] >= CONSENT_VERSION


def needs_reconsent() -> bool:
    """同意过，但同意的是上一版采集范围 —— 界面要再问一次。

    与「从没问过」(`unset`) 分开：那两种都要弹框，但这一种**不是新用户**，
    重新同意时不发 telemetry_enabled、也不换 install_id。
    说过「不」的人不在此列——升版之后再去问一次是骚扰，不是征求同意。
    硬开关关着时也不问：那个框点了也没用。
    """
    if hard_disabled():
        return False
    st = settings()
    return st["consent"] == CONSENT_ENABLED and st["consent_version"] < CONSENT_VERSION


def enabled() -> bool:
    if hard_disabled():
        return False
    return _consent_is_current(settings())


def install_id() -> str | None:
    """当前的匿名标识；没同意时是 None（**同意之前不生成**）。

    只有 `_deliver()` 会用它。任何对外接口都不回它。
    """
    return settings()["install_id"]


def set_consent(consent: str, source: str = "settings") -> dict:
    """记录用户的选择并立即生效。

    * enabled：没有 install_id 就现生成一个 UUIDv4（**只在这一刻生成**）；
      第一次打开时补一条 telemetry_enabled，再补一条 app_started
      （本次会话已经在跑了，否则这台机器要等到下次启动才被观测到）。
    * disabled：立刻停止发送。已经在队列里的按丢弃处理。
    * 关掉再打开**不再重发** telemetry_enabled——那不是一个新用户。
    """
    if consent not in _CONSENTS:
        raise ValueError(f"unknown consent: {consent}")
    before = settings()
    patch: dict = {"consent": consent, "consent_version": CONSENT_VERSION}
    first_time = False
    if consent == CONSENT_ENABLED:
        if not before["install_id"]:
            # 随机，且只有随机：不掺任何机器信息，也不是从别的字段推出来的
            patch["install_id"] = str(uuid.uuid4())
        if not before["ever_enabled"]:
            patch["ever_enabled"] = True
            first_time = True
    after = _save(patch)
    if consent != CONSENT_ENABLED:
        _drop_pending()
        return after
    if first_time:
        capture(
            "telemetry_enabled", {"source": "first_run" if source == "first_run" else "settings"}
        )
    # 本次会话已经是一次真实的应用会话了，补记一次（不重复发）
    _maybe_send_app_started()
    return after


# ---------------------------------------------------------------------------
# 会话边界
# ---------------------------------------------------------------------------
def note_app_started(app_mode: str) -> None:
    """由 `app.main()` 在**真的要开始服务**时调用一次。

    刻意不放在 import 时：`tavotto --help`、`tavotto doctor`、打包脚本、
    单元测试都会 import 到这里，它们不是产品会话，把它们算成 DAU 会让
    「有多少人真的在用」这个数字从第一天起就是假的。
    """
    global _session_mode
    if app_mode not in ("desktop", "browser"):
        return
    _session_mode = app_mode
    _maybe_send_app_started()


def _maybe_send_app_started() -> None:
    global _app_started_sent
    if _app_started_sent or _session_mode is None or not enabled():
        return
    _app_started_sent = True
    capture("app_started", {"app_mode": _session_mode})


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def _platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return "other"


def _distribution() -> str:
    """安装方式。**复用既有的唯一出处**（diagnostics.install_kind），不另写一份。

    延迟 import：diagnostics 会拉起 pool / runtime / ai_bridge，遥测模块不该
    在被 import 的那一刻付这笔账，也不该和它们绕成环。
    """
    try:
        from . import diagnostics

        kind = diagnostics.install_kind()
    except Exception:  # noqa: BLE001 — 探测失败不该影响埋点
        return "unknown"
    return kind if kind in AUTO_PROPS["distribution"]["values"] else "unknown"


def _auto_props() -> dict:
    from .. import __version__

    return {
        "app_version": __version__,
        "platform": _platform(),
        "arch": _arch(),
        "distribution": _distribution(),
    }


def capture(event: str, properties: dict | None = None) -> bool:
    """记一条事件。回 True = 进了队列（**不代表送达**）。

    这个函数**永远不抛异常、永远不阻塞**。没同意、被硬开关关掉、事件不在
    白名单、队列满——全部安静地回 False。调用点因此可以放在导出成功、
    AI 启动成功这种关键路径上而不必包 try。
    """
    try:
        # 一次性读完设置：`enabled()` 与 `install_id()` 各读一次配置文件，
        # 而这个函数挂在编辑/导出这类调用路径上——没必要为一条埋点读两遍盘。
        st = settings()
        if hard_disabled() or not _consent_is_current(st):
            return False
        props = validate(event, properties)
        ident = st["install_id"]
        if not ident:
            return False
        payload = {
            "schema_version": SCHEMA_VERSION,
            "distinct_id": ident,
            "event": event,
            "properties": {**_auto_props(), **props},
        }
        return _enqueue(payload)
    except Exception:  # noqa: BLE001 — 埋点绝不上浮
        return False


def _enqueue(payload: dict) -> bool:
    global _QUEUE, _SENDER
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = queue.Queue(maxsize=QUEUE_MAX)
        q = _QUEUE
        if _SENDER is None or not _SENDER.is_alive():
            _SENDER = threading.Thread(
                target=_run_sender, args=(q,), daemon=True, name="tavotto-telemetry"
            )
            _SENDER.start()
    try:
        q.put_nowait(payload)
        return True
    except queue.Full:
        # 丢，不阻塞。代理长时间不可达时这是唯一正确的行为。
        return False


def _drop_pending() -> None:
    """关掉遥测时把还没送出去的清空——「关了之后还在发」是最坏的一种。"""
    with _LOCK:
        q = _QUEUE
    if q is None:
        return
    try:
        while True:
            q.get_nowait()
            q.task_done()
    except queue.Empty:
        pass


def _run_sender(q: "queue.Queue[dict | None]") -> None:
    while True:
        payload = q.get()
        if payload is None:  # 退出哨兵（reset_for_tests）
            q.task_done()
            return
        try:
            # 出队时再确认一次：用户可能在事件排队期间关掉了遥测
            if enabled():
                _post(payload)
        except Exception:  # noqa: BLE001 — 网络失败是常态
            pass
        finally:
            q.task_done()


def _post(payload: dict) -> None:
    """真正的投递。测试把这个函数整体替换掉，一个真实请求都不发。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    from .. import __version__

    req = urllib.request.Request(
        endpoint(),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{brand.PRODUCT_NAME}/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
            resp.read(1024)  # 读掉响应体好让连接能复用/关闭
    except (urllib.error.URLError, TimeoutError, OSError):
        # 离线、代理挂了、DNS 失败——都不是错误，是常态。**不记日志**：
        # 一个断网的用户不该在 app.log 里看到几十条遥测投递失败。
        return


def flush(timeout: float = 2.0) -> bool:
    """等队列排空（**只给测试与优雅退出用**）。回 False = 超时没排完。"""
    with _LOCK:
        q = _QUEUE
    if q is None:
        return True
    done = threading.Event()

    def wait():
        q.join()
        done.set()

    threading.Thread(target=wait, daemon=True).start()
    return done.wait(timeout)


def reset_for_tests() -> None:
    """清掉进程内状态（队列、发送线程、会话标记）。仅供测试调用。

    发送线程要用哨兵**收掉**，不能只是把 `_QUEUE` 置空：那样每 reset 一次就
    留下一条永远阻塞在 `q.get()` 上的守护线程，一轮测试下来能攒几十条。
    """
    global _QUEUE, _SENDER, _session_mode, _app_started_sent
    _drop_pending()
    with _LOCK:
        old, _QUEUE, _SENDER = _QUEUE, None, None
    if old is not None:
        try:
            old.put_nowait(None)
        except queue.Full:
            pass  # 满着的队列里那条自然会被丢掉
    _session_mode = None
    _app_started_sent = False
