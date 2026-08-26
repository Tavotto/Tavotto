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

LOG_TAIL_LINES = 400
ERROR_TAIL = 30          # 报告里单列的最近错误条数

# 形如 sk-…、ghp_…、长十六进制串等，宁可多抹一点
_SECRET_VALUE = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{10,}|[A-Fa-f0-9]{32,})\b")
_SECRET_KEYS = ("api_key", "token", "secret", "password", "auth")

#: 假名标识：不是密钥，但也不该被顺手复制进 issue 或群聊。
#: 它把这台机器的**全部**遥测事件串在一起——诊断包里带上它，等于把
#: 「这条 issue 的作者」和后台那串匿名行为对上号，而排障一次都用不到它。
#: 开关本身（enabled / consent）不脱敏：知道遥测开没开对排障是有用的。
_PSEUDONYM_KEYS = ("install_id", "anonymous_id", "distinct_id")


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


def build_bundle(project: dict | None = None, port: int | None = None) -> bytes:
    """诊断包 zip 的字节流（全部内容已脱敏）。"""
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
        z.writestr("README.txt",
                   "Tavotto 诊断包\n\n"
                   "内容：report.json（环境与探测结果）、app.log（最近日志）、\n"
                   "config.json（用户配置）。密钥与用户主目录已自动抹掉，\n"
                   "但发出去之前仍建议自己扫一眼。\n")
    return buf.getvalue()
