"""运行配置（T03）：用户手动给的**精确 argv**，与「产物绑定它的原配置」所需的本机登记。

safe 档以前 argv 恒空（ADR 0014 §2），要参数的脚本只能走 `tavotto run --` native。T03 让用户在界面里给
一串**原样 token**：空串、空格、中文、重复、负数、粘连选项、`--`、子命令都按数组原样交给脚本的
`sys.argv[1:]`——Tavotto 不拆、不合并、不重排。本模块只做三件事：

* **校验**（`validate_argv`）：字符串数组、有界、无 NUL；坏的当场拒绝，不截断、不"修好"。
* **登记**（`put` / `select`）：每个 (项目, 脚本, argv) 一个本机不透明引用 `rc_…`。引用进资产 id、
  描述符与回执；**argv 原文只在本机登记里**，不进公开身份、诊断、遥测。登记不可变：用户"编辑"
  参数 = 新的 token 列表 = 新的引用（新修订），在途的执行与已有产物仍指着它们当时的那一条，
  所以编辑不会改变在途 spec，也不会让旧产物冷重放时读到"最新配置"。
* **降级**（异常码）：本机没有这条引用（别的机器保存的文档）、敏感值没落盘（重启后）、登记来自更新
  的版本——一律**显式拒绝**，绝不回落成空 argv 去跑（那会用错参数产出一张看似正常的图）。

存放：`<data_dir>/runconfigs/<项目摘要>.json`，**Tavotto 自己的数据目录**，不写用户项目，所以只读项目
照样能用、也不需要登记保留文件名。`sensitive=True` 的配置只活在进程内存里（文件里只留
"有过一条敏感配置"的占位）：重启后选它会得到 `run_config_secret_missing`，让用户重新提供。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import atomicio, config, execspec

ID_PREFIX = "rc_"
FORMAT_VERSION = 1
#: 单个项目最多记多少条配置（超出丢最老的；被丢的引用之后得到 `run_config_missing`，不会静默换成别的）
MAX_CONFIGS = 400

ERROR_INVALID_ARGV = "invalid_argv"
ERROR_MISSING = "run_config_missing"
ERROR_SECRET_MISSING = "run_config_secret_missing"
ERROR_UNSUPPORTED = "run_config_unsupported"
ERROR_CODES = (ERROR_INVALID_ARGV, ERROR_MISSING, ERROR_SECRET_MISSING, ERROR_UNSUPPORTED)


class RunConfigError(Exception):
    """运行配置不能用。`code` 是稳定码（`ERROR_CODES`），`reason` 是不含参数值的短理由。"""

    code = ERROR_MISSING

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason

    def to_payload(self) -> dict:
        # `reason` 是给程序 / 诊断看的短码（不含参数值），不进文案占位符：界面文案只按 `code` 翻
        out = {"error": str(self), "code": self.code, "params": {}}
        if self.reason:
            out["reason"] = self.reason
        return out


class InvalidArgv(RunConfigError):
    code = ERROR_INVALID_ARGV


class RunConfigMissing(RunConfigError):
    code = ERROR_MISSING


class RunConfigSecretMissing(RunConfigError):
    code = ERROR_SECRET_MISSING


class RunConfigUnsupported(RunConfigError):
    code = ERROR_UNSUPPORTED


@dataclasses.dataclass(frozen=True)
class RunConfig:
    """一条已登记的运行配置。`argv is None` = 敏感配置在本进程里已经不在了。"""

    id: str
    script: str
    argv: tuple[str, ...] | None
    sensitive: bool
    source: str
    created_at: float

    def public(self) -> dict:
        """公开投影：**没有参数值**。给前端 / 计划 / 诊断用。"""
        return {
            "id": self.id,
            "script": self.script,
            "argv_count": None if self.argv is None else len(self.argv),
            "sensitive": self.sensitive,
            "source": self.source,
        }


def validate_argv(raw) -> tuple[str, ...]:
    """用户给的 token 列表 → 元组。必须是字符串数组；空数组合法（= 没有参数）。

    不接受一整串 shell 命令（`"--freq 2"`）：那需要猜 token 边界，猜错就静默改了参数。"""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise InvalidArgv("argv 必须是字符串数组（每个 token 一项）", reason="not_a_list")
    if not all(isinstance(a, str) for a in raw):
        raise InvalidArgv("argv 的每一项都必须是字符串", reason="not_a_string")
    if len(raw) > execspec.MAX_ARGV_TOKENS:
        raise InvalidArgv("argv 的 token 太多", reason="too_many_tokens")
    if sum(len(a) for a in raw) > execspec.MAX_ARGV_CHARS:
        raise InvalidArgv("argv 总长度超出上限", reason="too_long")
    if any("\x00" in a for a in raw):
        raise InvalidArgv("argv 的 token 不能含 NUL 字符", reason="nul_character")
    if len(execspec.argv_wire(raw)) > execspec.MAX_ARGV_WIRE_CHARS:
        raise InvalidArgv("argv 序列化后超出命令行长度上限", reason="too_long")
    return tuple(raw)


# ---------------------------------------------------------------------------
# 登记
# ---------------------------------------------------------------------------
_LOCK = threading.RLock()
#: 敏感配置的 argv：只在进程内存里。键 (项目, 配置 id)。
_SECRETS: dict[tuple[str, str], tuple[str, ...]] = {}


def _norm_project(project_root: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(project_root))))


def store_path(project_root: str | Path) -> Path:
    digest = hashlib.sha256(_norm_project(project_root).encode("utf-8")).hexdigest()[:24]
    return config.data_path("runconfigs", f"{digest}.json")


def _read(project_root: str | Path) -> dict:
    """读登记（整份）；文件坏了 / 不存在 = 空（配置丢了得到明确的 `run_config_missing`，不是静默空 argv）。
    **更新版本写的文件拒绝**（`RunConfigUnsupported`）——旧引擎在这里读不懂，不能当它是空的。"""
    try:
        raw = store_path(project_root).read_text(encoding="utf-8")
    except OSError:
        return {"configs": {}, "defaults": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"configs": {}, "defaults": {}}
    if not isinstance(data, dict):
        return {"configs": {}, "defaults": {}}
    version = data.get("version")
    if isinstance(version, int) and version > FORMAT_VERSION:
        raise RunConfigUnsupported(
            "这份运行配置是更新版本的 Tavotto 写的，当前版本读不懂", reason="newer_format"
        )
    configs, defaults = data.get("configs"), data.get("defaults")
    return {
        "configs": configs if isinstance(configs, dict) else {},
        "defaults": defaults if isinstance(defaults, dict) else {},
    }


def _write(project_root: str | Path, data: dict) -> None:
    path = store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_json(path, {"version": FORMAT_VERSION, **data})


def _load(project_root: str | Path) -> dict[str, dict]:
    return _read(project_root)["configs"]


def _record_to_config(project_root: str | Path, cid: str, rec: dict) -> RunConfig | None:
    if not isinstance(rec, dict) or not isinstance(rec.get("script"), str):
        return None
    sensitive = bool(rec.get("sensitive"))
    if sensitive:
        argv = _SECRETS.get((_norm_project(project_root), cid))
    else:
        raw = rec.get("argv")
        if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
            return None
        argv = tuple(raw)
    return RunConfig(
        id=cid,
        script=rec["script"],
        argv=argv,
        sensitive=sensitive,
        source=str(rec.get("source") or "user"),
        created_at=float(rec.get("created_at") or 0.0),
    )


def put(
    project_root: str | Path,
    script: str,
    argv,
    *,
    sensitive: bool = False,
    source: str = "user",
) -> RunConfig | None:
    """登记 (项目, 脚本, argv)，返回它的配置；**空 argv 回 None**（没有配置，旧行为）。

    同一 (脚本, argv, 敏感标记) 复用已有引用——"没改"不生出新修订；改了任何一个 token 就是新引用。"""
    tokens = validate_argv(argv)
    if not tokens:
        return None
    root = _norm_project(project_root)
    with _LOCK:
        data = _read(project_root)
        configs = data["configs"]
        for cid, rec in configs.items():
            cfg = _record_to_config(project_root, cid, rec)
            if (
                cfg is not None
                and cfg.script == script
                and cfg.sensitive == bool(sensitive)
                and cfg.argv == tokens
            ):
                return cfg
        cid = ID_PREFIX + secrets.token_hex(6)
        while cid in configs:  # pragma: no cover — 48 位随机撞上的概率可以忽略，但别靠概率
            cid = ID_PREFIX + secrets.token_hex(6)
        now = time.time()
        configs[cid] = {
            "script": script,
            "argv": None if sensitive else list(tokens),
            "sensitive": bool(sensitive),
            "source": source,
            "created_at": now,
        }
        if sensitive:
            _SECRETS[(root, cid)] = tokens
        if len(configs) > MAX_CONFIGS:
            for old in sorted(configs, key=lambda k: float(configs[k].get("created_at") or 0))[
                : len(configs) - MAX_CONFIGS
            ]:
                configs.pop(old, None)
                _SECRETS.pop((root, old), None)
                data["defaults"] = {k: v for k, v in data["defaults"].items() if v != old}
        _write(project_root, data)
        return _record_to_config(project_root, cid, configs[cid])


def get(project_root: str | Path, config_id: str, *, script: str | None = None) -> RunConfig:
    """按引用取配置。本机没有 / 脚本对不上 → `RunConfigMissing`；敏感值已不在 → `RunConfigSecretMissing`。"""
    if not isinstance(config_id, str) or not config_id.startswith(ID_PREFIX):
        raise RunConfigMissing("没有这条运行配置", reason="malformed_reference")
    with _LOCK:
        rec = _load(project_root).get(config_id)
        cfg = _record_to_config(project_root, config_id, rec) if rec is not None else None
    if cfg is None:
        raise RunConfigMissing(
            "这台机器上没有这张图当初用的运行参数（可能来自另一台机器，或登记已清理）；请重新输入参数后再运行",
            reason="not_on_this_machine",
        )
    if script is not None and cfg.script != script:
        raise RunConfigMissing("这条运行配置不属于这个脚本", reason="script_mismatch")
    if cfg.argv is None:
        raise RunConfigSecretMissing(
            "这次运行含敏感参数，没有保存；请重新输入后再运行", reason="secret_not_kept"
        )
    return cfg


def selection(project_root: str | Path, config_id: str, *, script: str | None = None):
    """`get()` + 转成 pool 吃的 `execspec.RunSelection`。"""
    cfg = get(project_root, config_id, script=script)
    return execspec.RunSelection(config_id=cfg.id, argv=cfg.argv or ())


def selection_for(
    project_root: str | Path, script: str, argv, *, sensitive: bool = False, source: str = "user"
):
    """用户这次给的 token → `RunSelection`（空 argv → None，旧行为）。登记 + 校验一次做完。

    `source` 只记「这份配置最初从哪个入口来」（`user` = 界面、`mcp` = Agent 经插件提交）：同一 (脚本, argv) 两个入口
    得到**同一个**引用（T10：GUI 与 MCP 同一份执行意图），它不进身份、不进池键。"""
    cfg = put(project_root, script, argv, sensitive=sensitive, source=source)
    return None if cfg is None else execspec.RunSelection(config_id=cfg.id, argv=cfg.argv or ())


def configs_of(project_root: str | Path, script: str) -> list[RunConfig]:
    """这个脚本登记过的配置（新→旧）；坏记录跳过。给素材清单列"同一脚本的几份配置"用。"""
    try:
        with _LOCK:
            configs = _load(project_root)
    except RunConfigError:
        return []
    out = []
    for cid, rec in configs.items():
        cfg = _record_to_config(project_root, cid, rec)
        if cfg is not None and cfg.script == script:
            out.append(cfg)
    return sorted(out, key=lambda c: -c.created_at)


def forget_secrets() -> None:
    """测试与"清除敏感值"用：模拟进程重启后敏感配置不在了。"""
    with _LOCK:
        _SECRETS.clear()


# ---------------------------------------------------------------------------
# 磁盘面板的默认配置
# ---------------------------------------------------------------------------
def set_default(project_root: str | Path, script: str, config_id: str | None) -> None:
    """记下"这个脚本最近一次**用户明确运行**用的是哪份配置"（`None` = 无参数运行，清掉）。

    只服务**磁盘面板**（用户脚本自己写出来的 `fig.pdf` 的素材）：它们没有 Tavotto 的执行产物可绑，重跑时
    只能按脚本找配置。`runtime:` 素材**从不**读它——那些产物的资产 id 里自带冻结的配置引用。
    热会话 / 写回重放用的是会话自己冻结的 `worker.run`，与这里之后的变化无关。"""
    with _LOCK:
        data = _read(project_root)
        if config_id is None:
            if script not in data["defaults"]:
                return
            data["defaults"].pop(script)
        else:
            if data["defaults"].get(script) == config_id:
                return
            data["defaults"][script] = config_id
        _write(project_root, data)


def default_selection(project_root: str | Path, script: str):
    """磁盘面板重跑该用的配置（`RunSelection`；没有默认 = None，旧行为）。敏感值丢了 → `RunConfigSecretMissing`。"""
    with _LOCK:
        cid = _read(project_root)["defaults"].get(script)
    if not isinstance(cid, str):
        return None
    return selection(project_root, cid, script=script)
