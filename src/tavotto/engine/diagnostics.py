"""一键诊断包：把排障需要的东西一次性收齐，并且**先脱敏再交出去**。

存在的理由：剩下那些没法提前覆盖的 bug，来回问十次（「你什么系统」「装的哪个
Python」「日志在哪」）才能定位一次。有了这个包，用户点一下、发过来，一次定位。

包里有什么：
    report.json   版本 / 系统 / 安装方式 / 数据目录 / 渲染解释器 / matplotlib /
                  端口 / AI CLI 探测结果 / 项目与注册表概况 / 最近错误
    app.log       最近若干行日志
    config.json   用户配置（**密钥已抹掉**）

脱敏两件事，缺一不可：
    * 密钥：api_key / token / 形如 sk-… 的串一律换成 ***；
    * 个人路径：用户主目录换成 ~，用户名换成 <user>。
用户把包发到群里或贴进 issue 时，不该顺手泄露自己的密钥和目录结构。

纯标准库，Flask 父进程 import。
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import sys
import zipfile
from pathlib import Path

from . import ai_bridge, bootstrap, config, pool, runtime, telemetry, updater
from . import diagnostics_frontend

LOG_TAIL_LINES = 400
ERROR_TAIL = 30          # 报告里单列的最近错误条数

#: 诊断包整体格式的版本。**读包的人不该靠 Tavotto 版本号去猜 schema**
#: ——manifest.json 自报这个数。1 = 只有 report/app.log/config 的那一版；
#: 2 = 增加了 frontend-state.json / interaction-trace.jsonl / manifest.json。
BUNDLE_SCHEMA_VERSION = 2
#: 两个子 schema 各自独立演进（ADR 0016 §20）。读取方**忽略不认识的字段**。
FRONTEND_SNAPSHOT_SCHEMA = 1
TRACE_SCHEMA = 1

# 形如 sk-…、ghp_…、长十六进制串等，宁可多抹一点
_SECRET_VALUE = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{10,}|[A-Fa-f0-9]{32,})\b")
_SECRET_KEYS = ("api_key", "token", "secret", "password", "auth")

#: 假名标识：不是密钥，但也不该被顺手复制进 issue 或群聊。
#: 它把这台机器的**全部**遥测事件串在一起——诊断包里带上它，等于把
#: 「这条 issue 的作者」和后台那串匿名行为对上号，而排障一次都用不到它。
#: 开关本身（enabled / consent）不脱敏：知道遥测开没开对排障是有用的。
_PSEUDONYM_KEYS = ("install_id", "anonymous_id", "distinct_id")

#: 用户自己的「东西清单」：项目名 + 路径逐条列着，对排障零帮助，
#: 对隐私却是实打实的暴露面（用户在往 issue 上贴自己所有课题的名字）。
#: 只留条数。当前打开的那个项目仍在 report.json 的 project 段里。
_USER_INVENTORY_KEYS = ("recent_projects", "projects")


def _install_id() -> str:
    """本机的匿名遥测标识（没同意过就是空串）。只用来把它从输出里抹掉。"""
    try:
        from . import telemetry
        return telemetry.install_id() or ""
    except Exception:                          # noqa: BLE001 — 脱敏不该被它拖垮
        return ""


def _redact_text(text: str) -> str:
    """文本脱敏：先抹密钥再抹个人路径。顺序无所谓，但三步都不能省。"""
    text = _SECRET_VALUE.sub("***", text)
    # 按**值**再抹一次假名标识：按键名那道只挡得住结构化的
    # `"install_id": "..."`，挡不住它偶然出现在别的字符串里。
    ident = _install_id()
    if ident:
        text = text.replace(ident, "***")
    home = os.path.expanduser("~")
    if home and home != os.sep:
        text = text.replace(home, "~")
        # Windows 上日志里可能混着两种分隔符写法
        text = text.replace(home.replace("\\", "/"), "~")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) >= 3:      # 太短的用户名replace 会误伤正常词
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text)
    return text


def _redact_obj(obj):
    """结构化数据脱敏：按键名判定的敏感字段整体换掉，其余走文本规则。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower()
            if any(s in key for s in _SECRET_KEYS) or key in _PSEUDONYM_KEYS:
                out[k] = "***" if v else v
            elif key in _USER_INVENTORY_KEYS:
                # 「用户还有哪些项目」是一份**目录清单**：每条都带项目名与路径，
                # 而排障一次都用不到它——要看的是**当前**这个项目（report.json
                # 的 project 段已经有了）。只留条数，清单本身不出门。
                out[k] = {"count": len(v)} if isinstance(v, (list, dict)) else v
            else:
                out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return _redact_text(obj)
    return obj


def _log_path() -> Path:
    return config.data_dir() / "cache" / "app.log"


def _log_tail(n: int = LOG_TAIL_LINES) -> list[str]:
    try:
        lines = _log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def install_kind() -> str:
    """怎么装的——升级指令、路径写权限、能不能自己修都由它决定。

    **这是安装方式的唯一出处**：诊断报告与遥测的 `distribution` 属性都调它。
    埋点为了拿这个值另写一份探测，就是制造第二个权威，两边迟早给出不同答案。
    """
    if pool.is_frozen():
        return "desktop"          # .app / .exe 独立应用
    return updater.install_method()


def _runtime_section() -> dict:
    """内置渲染 runtime 的现状 + **实测** import 结果。

    只在 runtime 完好时才真去 import：坏掉的时候那一轮探测必然全 None，
    白白让用户等上一分钟。
    """
    st = runtime.status()
    info = st.get("manifest") or {}
    section = {
        "present": st["present"],
        "valid": st["valid"],
        "expected": runtime.ships_bundled_runtime(),
        "root": st["root"],
        "code": st["code"],
        "python": (info.get("python") or {}).get("version"),
        "build": info.get("build") or {},
        "packages": info.get("packages") or {},
        "imports": {},
    }
    if st["valid"] and st["python"]:
        section["imports"] = runtime.probe_packages(st["python"])
    return section


def build_report(project: dict | None = None, port: int | None = None) -> dict:
    """结构化诊断报告（已脱敏）。project 由 app 层传入，避免这里反向依赖。"""
    from .. import __version__

    worker_python, worker_error = None, None
    try:
        worker_python = pool.find_worker_python()
    except pool.WorkerError as exc:
        worker_error = str(exc)

    mpl = bootstrap.matplotlib_version(worker_python) if worker_python else None
    caps = ai_bridge.capabilities()
    lines = _log_tail()
    errors = [ln for ln in lines if " ERROR " in ln or "Traceback" in ln][-ERROR_TAIL:]

    report = {
        "tavotto": {
            "version": __version__,
            "install": install_kind(),
            "frozen": pool.is_frozen(),
            "executable": sys.executable,
            "port": port,
        },
        "system": {
            "platform": platform.platform(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "os_name": os.name,
            "encoding": {
                "stdout": getattr(sys.stdout, "encoding", None),
                "filesystem": sys.getfilesystemencoding(),
                "preferred": __import__("locale").getpreferredencoding(False),
            },
        },
        "paths": {
            "data_dir": str(config.data_dir()),
            "config_dir": str(config.config_dir()),
            "log": str(_log_path()),
        },
        "render": {
            "worker_python": worker_python,
            "worker_source": pool.source_of(worker_python) if worker_python else None,
            "worker_error": worker_error,
            "matplotlib": mpl,
            # 走 pool 那份读取器：诊断包要如实反映**实际生效的**那个值，
            # 用户设的是旧名 MM_WORKER_PYTHON 时直接读新名会报 null，
            # 于是「明明设了却没生效」在诊断包里看起来像「压根没设」。
            "env_override": pool.worker_python_env(),
            # 控制面（Rust supervisor / Python 池）+ 池里每条会话实际走的哪条。
            # workerd 建会话失败是静默回退的，「装了但没用上」只有这里看得出来。
            "control_plane": pool.control_plane(),
            # 「装了但用不了」全靠这一段：内置 runtime 在不在、装的是哪些版本、
            # 实测能不能 import。只贴 manifest 不够——杀毒软件隔离掉一个 .pyd
            # 时 manifest 照样完好。
            "bundled_runtime": _runtime_section(),
        },
        # 每个已注册编码 Agent 的探测结论。**不含就绪检查的账号细节**——
        # 那条只回 ready/needs_auth/unknown，邮箱与组织名一个字都不出现。
        "ai": {
            entry["id"]: {"installed": entry["installed"],
                          "enabled": entry["enabled"],
                          "state": entry["state"],
                          "path": entry["executable_path"],
                          "source": entry["detection_source"],
                          "version": entry["version"]}
            for entry in caps.get("agents", [])
        },
        "ai_endpoints": [
            {"id": e["id"], "label": e["label"], "agent": e["agent"],
             "base_url": e["base_url"], "has_key": e["has_key"]}
            for e in caps.get("endpoints", [])
        ],
        # 遥测**开没开**对排障有用（「我关了它为什么还联网」），
        # 所以这里给状态；假名 id 由 _redact_obj / _redact_text 抹掉。
        "telemetry": {
            "consent": telemetry.settings()["consent"],
            "enabled": telemetry.enabled(),
            "hard_disabled": telemetry.hard_disabled(),
        },
        "project": project or {"open": False},
        "recent_errors": errors,
    }
    return _redact_obj(report)


def build_bundle(project: dict | None = None, port: int | None = None,
                 frontend: dict | None = None,
                 frontend_dropped: bool = False) -> bytes:
    """诊断包 zip 的字节流（全部内容已脱敏）。

    `frontend` 是前端在用户点「导出诊断包」那一刻现采的载荷
    （`{frontend_state, interaction_trace}`，见 ADR 0016）。**它是可选的**：
    老的 GET 端点不带，出的包就是 schema 2 但只有老三件 + manifest。
    """
    report = build_report(project, port)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json",
                   json.dumps(report, ensure_ascii=False, indent=1))
        z.writestr("app.log", _redact_text("\n".join(_log_tail())))
        try:
            cfg = json.loads(config.config_path().read_text(encoding="utf-8"))
            z.writestr("config.json",
                       json.dumps(_redact_obj(cfg), ensure_ascii=False, indent=1))
        except (OSError, ValueError):
            pass

        # ---- 前端状态与交互轨迹（ADR 0016）。没带就干脆不放这两个文件 ----
        snapshot, trace, truncated = _frontend_sections(frontend)
        # 请求体超限被整份丢掉时：包照出（用户要的是一个包，不是一个错误），
        # 但 manifest 必须如实说「这份 trace 不完整」
        truncated = truncated or frontend_dropped
        if snapshot is not None:
            z.writestr("frontend-state.json",
                       json.dumps(snapshot, ensure_ascii=False, indent=1))
        if trace:
            z.writestr("interaction-trace.jsonl",
                       diagnostics_frontend.trace_to_jsonl(trace))

        z.writestr("manifest.json", json.dumps({
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "tavotto_version": report.get("tavotto", {}).get("version"),
            "contains_frontend_state": snapshot is not None,
            "contains_interaction_trace": bool(trace),
            "privacy_mode": "safe-default",
            "trace_event_count": len(trace),
            "trace_truncated": truncated,
            "frontend_snapshot_schema": FRONTEND_SNAPSHOT_SCHEMA,
            "trace_schema": TRACE_SCHEMA,
        }, ensure_ascii=False, indent=1))
        z.writestr("README.txt", _readme(snapshot is not None, bool(trace)))
    return buf.getvalue()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _frontend_sections(frontend: dict | None) -> tuple[dict | None, list[dict], bool]:
    """前端载荷 → (快照, 事件列表, 有没有被截断)。**服务端的第二道校验在这里**。

    `frontend` 为 None（老的 GET 端点、或者前端没给）时三个都空，包里就没有
    那两个文件——**不放一个空壳**：空的 frontend-state.json 读起来像
    「前端当时什么状态都没有」，而真相是「这次根本没采集」。
    """
    if not isinstance(frontend, dict):
        return None, [], False
    snapshot = diagnostics_frontend.sanitize_snapshot(
        frontend.get("frontend_state"), _redact_text)
    trace, truncated = diagnostics_frontend.sanitize_trace(
        frontend.get("interaction_trace"), _redact_text)
    return snapshot, trace, truncated


def _readme(has_state: bool, has_trace: bool) -> str:
    """包里有什么、**没有什么**。双语——用户得看得懂自己在往 issue 上贴什么。

    「不含」那一段是承诺，不是免责声明：它对应的是代码里的字段 allowlist
    与服务端校验（ADR 0016 §4 / §8），不是「我们尽量不放」。
    """
    extra_zh, extra_en = "", ""
    if has_state:
        extra_zh += "- frontend-state.json：导出那一刻的前端状态摘要（匿名）\n"
        extra_en += "- frontend-state.json: anonymized snapshot of the app state\n"
    if has_trace:
        extra_zh += "- interaction-trace.jsonl：最近的编辑操作记录（匿名，一行一条）\n"
        extra_en += "- interaction-trace.jsonl: recent anonymized interaction events\n"
    return (
        "Tavotto 诊断包 / Tavotto diagnostic package\n"
        "\n"
        "包含 / This package contains:\n"
        "- report.json：系统、运行环境与探测结果\n"
        "- app.log：最近的应用日志\n"
        "- config.json：用户配置（密钥已抹掉）\n"
        + extra_zh +
        "- manifest.json：本诊断包自身的格式说明\n"
        "\n"
        "- report.json: system and runtime information\n"
        "- app.log: recent Tavotto application logs\n"
        "- config.json: user configuration (secrets removed)\n"
        + extra_en +
        "- manifest.json: describes this package's own format\n"
        "\n"
        "不包含 / It does NOT intentionally contain:\n"
        "- 图中文字（标题、坐标轴标签、图例、标注）\n"
        "- Python 脚本与源代码\n"
        "- 科研数据、数据数组\n"
        "- SVG / PDF / PNG 图像内容\n"
        "- API 密钥、令牌\n"
        "- 完整的本地文件路径、用户名、主目录\n"
        "\n"
        "- text drawn inside your figures (titles, axis labels, legends, annotations)\n"
        "- Python source code or scripts\n"
        "- raw datasets or data arrays\n"
        "- SVG / PDF / PNG image content\n"
        "- API keys or tokens\n"
        "- full local file paths, usernames, or home directories\n"
        "\n"
        "仍会包含 / Still included: 当前打开的项目**文件夹名**（report.json 的\n"
        "project 段，排障需要它判断目录权限与注册表冲突）。其余项目的清单不出门。\n"
        "The folder name of the currently open project is included; the list of\n"
        "your other projects is not.\n"
        "\n"
        "文件名、路径与图内文字在诊断包里一律换成不可逆的短哈希（doc:… / "
        "panel:… / file:… / var:…），\n"
        "只用来判断「两个状态是不是同一个」，反推不回原值。\n"
        "Identifiers are replaced with irreversible short hashes; they only tell\n"
        "whether two states are the same, and cannot be reversed.\n"
        "\n"
        "发出去之前仍建议自己扫一眼。/ You may still want to skim it before sharing.\n"
    )
