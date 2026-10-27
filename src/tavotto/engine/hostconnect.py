"""`tavotto hosts list|connect|disconnect|check`：「接入 AI 编辑器」的唯一安装器（用户 2026-10-27）。**纯标准库。**

## 为什么是一条命令

与 `tavotto codex install` 同一个思路（ADR 0012）：桌面设置里的「接入」按钮 spawn 这条命令、只解析它那一行
JSON；按钮背后不另写一套安装器，README / 命令行 / 按钮 / Windows Lab 的验收读的都是这里。

## 写进宿主的是什么

一条 stdio MCP 服务：`command` 是**这一份 Tavotto** 能起服务端的命令（桌面版 = 同目录的 `tavotto-cli mcp`，
见 `mcpserve`；源码树 = `python -m tavotto mcp`），`env` 只有 `TAVOTTO_MCP_ROOTS`——用户在第一次接入时选的
那个项目文件夹（所有宿主共用，存在 Tavotto 配置目录的 `hosts.json`）。主目录、文件系统根一律不收，
与 `RootAuthority` / 配置生成器的授权边界相同。

## 三条纪律

* **写之前先备份，只动 tavotto 那一条。** 文件读不懂（比如带注释的 JSONC）就不写、如实报
  `config_unreadable`——绝不为了写进去把用户的配置整份重写。「断开」只删 tavotto 那一条。
* **幂等。** 已经是这条配置就 `skipped`，重跑一次看得出「什么都没做」。
* **`--json` 时失败也是一行 JSON**，带稳定 `error_code`（界面翻译成一句话，英文 code 不进界面）。

各宿主的格式与位置：JSON 文件类在 `_FILE_HOSTS`；Claude Code 走它自己的 `claude mcp add`（配置文件归它管）；
Codex 委托 `codexinstall`（插件 + 技能，插件的启动器在只装桌面版时会交给 `tavotto-cli mcp`）。
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import config
from .runtime import CREATE_NO_WINDOW

#: 第一版的八个宿主（顺序 = 界面上的顺序）
HOST_IDS = (
    "codex",
    "claude-code",
    "cursor",
    "vscode",
    "trae",
    "zcode",
    "workbuddy",
    "claude-desktop",
)
SERVER_NAME = "tavotto"
ROOTS_ENV = "TAVOTTO_MCP_ROOTS"
HOSTS_FILE = "hosts.json"

ERR_UNKNOWN_HOST = "unknown_host"
ERR_NOT_INSTALLED = "host_not_installed"
ERR_NO_SERVER = "mcp_server_not_bundled"
ERR_NO_ROOT = "project_root_missing"
ERR_BAD_ROOT = "project_root_rejected"
ERR_UNREADABLE = "config_unreadable"
ERR_WRITE = "config_write_failed"
ERR_CLI = "host_cli_failed"
ERR_HEALTH = "server_health_failed"
ERR_CODEX = "codex_install_failed"


# ----------------------------------------------------------------------------- 起服务端的命令
def mcp_launch() -> dict | None:
    """这一份 Tavotto 起 MCP 服务端的命令：{"command", "args"}；起不了（pip 装的没有服务端）就是 None。"""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli = exe.with_name("tavotto-cli" + exe.suffix)
        return {"command": str(cli), "args": ["mcp"]} if cli.is_file() else None
    from . import mcpserve  # noqa: PLC0415

    if mcpserve._source_tree_mcp_dir() is None:
        return None
    return {"command": sys.executable, "args": ["-m", "tavotto", "mcp"]}


# ----------------------------------------------------------------------------- 项目文件夹
def _hosts_state_path() -> Path:
    return config.config_dir() / HOSTS_FILE


def load_state() -> dict:
    try:
        data = json.loads(_hosts_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    p = _hosts_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def check_project_root(path: str) -> tuple[str | None, str | None]:
    """用户选的项目文件夹 → (规范化的绝对路径, None) 或 (None, 拒绝原因)。
    主目录、主目录的上级、文件系统根一律不收：授权给宿主的范围必须是一个具体的项目（与 RootAuthority 同一边界）。"""
    if not path:
        return None, "没有选文件夹"
    p = Path(path).expanduser()
    if not p.is_absolute():
        return None, "要绝对路径"
    try:
        p = p.resolve(strict=True)
    except OSError:
        return None, "文件夹不存在"
    if not p.is_dir():
        return None, "不是文件夹"
    if os.pathsep in str(p):
        # TAVOTTO_MCP_ROOTS 按 os.pathsep 切成多个根：`/tmp/a:b` 会被读成 `/tmp/a` 与 `b` 两个根，放行用户没选的目录
        # （Codex #1455 P1；与 configure.py 的 validate_project_root 同一条规则）
        return (
            None,
            f"文件夹路径里不能有「{os.pathsep}」（它在授权里是多个目录的分隔符），请换一个文件夹或改名",
        )
    home = Path.home().resolve()
    if p == Path(p.anchor) or p == home or p in home.parents:
        return None, "不能是整个主目录或磁盘根目录，请选一个具体的项目文件夹"
    return str(p), None


# ----------------------------------------------------------------------------- 结果
def _step(name: str, *, ok: bool, skipped: bool = False, detail: str = "", code: str = "") -> dict:
    s = {"step": name, "ok": ok, "skipped": skipped}
    if detail:
        s["detail"] = detail
    if code:
        s["error_code"] = code
    return s


def _result(host: str, action: str, steps: list[dict], **extra) -> dict:
    ok = all(s["ok"] for s in steps)
    code = next((s.get("error_code") for s in steps if not s["ok"]), None)
    out = {"ok": ok, "host": host, "action": action, "steps": steps, **extra}
    if not ok:
        out["state"] = out.get("state") or "failed"
        out["error_code"] = code or "failed"
    return out


# ----------------------------------------------------------------------------- JSON 配置文件
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tavotto-tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def merge_json_file(path: Path, mutate: Callable[[dict], None], *, what: str) -> dict:
    """读 → 改一份副本 → 不同才备份并原子写回。读不懂就不写。返回一步结论（detail 里有备份文件名）。"""
    existed = path.is_file()
    if existed:
        try:
            raw = path.read_text(encoding="utf-8-sig")
            old = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError) as exc:
            return _step(
                what,
                ok=False,
                code=ERR_UNREADABLE,
                detail=f"{path}：读不懂（{exc.__class__.__name__}），没有改它",
            )
        if not isinstance(old, dict):
            return _step(
                what, ok=False, code=ERR_UNREADABLE, detail=f"{path}：顶层不是对象，没有改它"
            )
    else:
        old = {}
    new = copy.deepcopy(old)
    mutate(new)
    if new == old:
        return _step(what, ok=True, skipped=True, detail=str(path))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = ""
        if existed:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            b = path.with_name(path.name + f".tavotto-backup-{stamp}")
            b.write_bytes(path.read_bytes())
            backup = str(b)
        _atomic_write(path, json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        return _step(what, ok=False, code=ERR_WRITE, detail=f"{path}：{exc}")
    return _step(what, ok=True, detail=str(path) + (f"（备份：{backup}）" if backup else ""))


def _set_in(top_key: str, entry: dict) -> Callable[[dict], None]:
    def mutate(d: dict) -> None:
        box = d.get(top_key)
        if not isinstance(box, dict):
            box = d[top_key] = {}
        box[SERVER_NAME] = entry

    return mutate


def _drop_from(top_key: str) -> Callable[[dict], None]:
    def mutate(d: dict) -> None:
        box = d.get(top_key)
        if isinstance(box, dict):
            box.pop(SERVER_NAME, None)

    return mutate


# ----------------------------------------------------------------------------- 各宿主的位置
def _appdata() -> Path:
    """VS Code 系 / Claude 桌面版的「用户数据」根：Windows `%APPDATA%`、macOS Application Support、Linux ~/.config。"""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


@dataclass(frozen=True)
class FileTarget:
    """一个要写的配置文件：位置、tavotto 那一条放在哪个顶层键下、条目长什么样。"""

    path: Path
    top_key: str
    entry: Callable[[dict, dict], dict]  # (launch, env) -> 条目


@dataclass(frozen=True)
class FileHost:
    id: str
    label: str
    #: 装了没有：任一目录存在即算（宿主自己的用户数据 / 配置目录）
    detect: Callable[[], list[Path]]
    targets: Callable[[], list[FileTarget]]
    #: 写完之后的宿主特有一步（WorkBuddy 的信任）；断开时不撤（信任只对这一条配置的指纹有效）
    after_connect: Callable[[dict, dict], dict | None] | None = None


def _plain(launch: dict, env: dict) -> dict:
    return {"command": launch["command"], "args": list(launch["args"]), "env": dict(env)}


def _stdio(launch: dict, env: dict) -> dict:
    return {"type": "stdio", **_plain(launch, env)}


def _zcode_entry(launch: dict, env: dict) -> dict:
    # ZCode 的运行配置是 `mcp.servers`（与 Windows Lab 验收 zcode-native 用的同一形状）；超时只认 timeoutMs
    return {**_stdio(launch, env), "enabled": True, "timeoutMs": 120000}


def _vscode_user_dirs(names: tuple[str, ...]) -> list[Path]:
    return [_appdata() / n / "User" for n in names]


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.is_dir()]


def _cursor_dir() -> Path:
    return Path.home() / ".cursor"


def _zcode_dir() -> Path:
    return Path.home() / ".zcode"


def _workbuddy_dir() -> Path:
    return Path.home() / ".workbuddy"


def _claude_desktop_dir() -> Path:
    return _appdata() / "Claude"


TRAE_APPS = ("Trae", "Trae CN")


def _zcode_mutate(entry: dict) -> Callable[[dict], None]:
    def mutate(d: dict) -> None:
        feats = d.get("features")
        if not isinstance(feats, dict):
            feats = d["features"] = {}
        feats["mcp"] = True
        mcp = d.get("mcp")
        if not isinstance(mcp, dict):
            mcp = d["mcp"] = {}
        _set_in("servers", entry)(mcp)

    return mutate


def _zcode_drop(d: dict) -> None:
    mcp = d.get("mcp")
    if isinstance(mcp, dict):
        _drop_from("servers")(mcp)


def workbuddy_trust_hash(launch: dict, env: dict) -> str:
    """WorkBuddy 5.8.3 `computeTrustConfigHash` 对 stdio 服务的算法（没有 cwd 时）：
    sha256(command | 排序后的 args 逗号连接 | 排序后的 env 键名逗号连接)。用例里的期望值由那段原样 JS 在 node 里算出。"""
    material = "|".join(
        [
            launch["command"],
            ",".join(sorted(str(a) for a in launch["args"])),
            ",".join(sorted(env)),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _workbuddy_trust(launch: dict, env: dict) -> dict:
    """用户点了「接入」= 同意：替他在 WorkBuddy 的 mcp-approvals.json 里只信任 tavotto 这一条（方案决策，2026-10-27）。
    不信任这一条，WorkBuddy 不加载用户自加的 MCP（Windows Lab run 20261027T071832Z-65d729）。"""
    key = f"{workbuddy_trust_hash(launch, env)}::{SERVER_NAME}"
    return merge_json_file(
        _workbuddy_dir() / "mcp-approvals.json",
        lambda d: d.setdefault(key, int(time.time() * 1000)),
        what="trust",
    )


_FILE_HOSTS: dict[str, FileHost] = {
    "cursor": FileHost(
        "cursor",
        "Cursor",
        detect=lambda: _existing([_cursor_dir()]),
        targets=lambda: [FileTarget(_cursor_dir() / "mcp.json", "mcpServers", _plain)],
    ),
    "vscode": FileHost(
        "vscode",
        "VS Code",
        detect=lambda: _existing(_vscode_user_dirs(("Code",))),
        # 用户级 mcp.json：顶层是 servers，不是 mcpServers（与配置生成器的 HOSTS["vscode"] 同一 schema）
        targets=lambda: [
            FileTarget(d / "mcp.json", "servers", _stdio)
            for d in _existing(_vscode_user_dirs(("Code",)))
        ],
    ),
    "trae": FileHost(
        "trae",
        "TRAE",
        detect=lambda: _existing(_vscode_user_dirs(TRAE_APPS)),
        targets=lambda: [
            FileTarget(d / "mcp.json", "mcpServers", _plain)
            for d in _existing(_vscode_user_dirs(TRAE_APPS))
        ],
    ),
    "zcode": FileHost(
        "zcode",
        "ZCode",
        detect=lambda: _existing([_zcode_dir()]),
        # 用户级 cli/ 与「非项目对话」的默认工作目录两处：只写一处时桌面版读不到（Windows Lab 实测）
        targets=lambda: [
            FileTarget(_zcode_dir() / "cli" / "config.json", "mcp", _zcode_entry),
            FileTarget(
                _zcode_dir() / "workspace" / "default" / ".zcode" / "config.json",
                "mcp",
                _zcode_entry,
            ),
        ],
    ),
    "workbuddy": FileHost(
        "workbuddy",
        "WorkBuddy",
        detect=lambda: _existing([_workbuddy_dir()]),
        targets=lambda: [FileTarget(_workbuddy_dir() / "mcp.json", "mcpServers", _plain)],
        after_connect=_workbuddy_trust,
    ),
    "claude-desktop": FileHost(
        "claude-desktop",
        "Claude",
        detect=lambda: _existing([_claude_desktop_dir()]),
        targets=lambda: [
            FileTarget(_claude_desktop_dir() / "claude_desktop_config.json", "mcpServers", _plain)
        ],
    ),
}

LABELS = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    **{h.id: h.label for h in _FILE_HOSTS.values()},
}


def _target_mutate(t: FileTarget, entry: dict) -> Callable[[dict], None]:
    return _zcode_mutate(entry) if t.top_key == "mcp" else _set_in(t.top_key, entry)


def _target_drop(t: FileTarget) -> Callable[[dict], None]:
    return _zcode_drop if t.top_key == "mcp" else _drop_from(t.top_key)


def _target_entry_now(t: FileTarget) -> dict | None:
    try:
        d = json.loads(t.path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    box = (d.get("mcp") or {}).get("servers") if t.top_key == "mcp" else d.get(t.top_key)
    got = box.get(SERVER_NAME) if isinstance(box, dict) else None
    return got if isinstance(got, dict) else None


# ----------------------------------------------------------------------------- Claude Code / Codex
def _claude_argv() -> list[str] | None:
    from . import ai_agents  # noqa: PLC0415

    agent = ai_agents.get_agent("claude")
    res = ai_agents.resolve(agent, probe_readiness=False) if agent else None
    return list(res.argv) if res and res.argv else None


def _run(argv: list[str], timeout: float = 120) -> tuple[int, str]:
    from . import ai_agents  # noqa: PLC0415

    try:
        p = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=ai_agents.spawn_env(argv[0]),
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _claude_code_connect(launch: dict, env: dict) -> list[dict]:
    claude = _claude_argv()
    if not claude:
        return [_step("detect", ok=False, code=ERR_NOT_INSTALLED, detail="没找到 claude 命令")]
    steps = [_step("detect", ok=True, detail=" ".join(claude))]
    rc, out = _run([*claude, "mcp", "list"])
    if rc == 0 and f"plugin:{SERVER_NAME}:{SERVER_NAME}" in out:
        # 已经装了 Tavotto 的 Claude Code 插件：再登记一条同名 user 服务会出现两个 Tavotto（配置生成器同一提醒）
        steps.append(_step("register", ok=True, skipped=True, detail="已通过 Tavotto 插件接入"))
        return steps
    # 重跑幂等：先删掉旧的那一条（没有就当成功），再按现在的命令与文件夹登记
    _run([*claude, "mcp", "remove", "--scope", "user", SERVER_NAME])
    add = [*claude, "mcp", "add", "--scope", "user", SERVER_NAME]
    for k, v in env.items():
        add += ["-e", f"{k}={v}"]
    rc, out = _run([*add, "--", launch["command"], *launch["args"]])
    steps.append(
        _step("register", ok=rc == 0, detail=out.strip()[-300:], code="" if rc == 0 else ERR_CLI)
    )
    return steps


def _claude_code_disconnect() -> list[dict]:
    claude = _claude_argv()
    if not claude:
        return [_step("detect", ok=True, skipped=True, detail="没找到 claude 命令，无可断开")]
    rc, out = _run([*claude, "mcp", "remove", "--scope", "user", SERVER_NAME])
    return [_step("unregister", ok=True, skipped=rc != 0, detail=out.strip()[-300:])]


def _codex_connect() -> list[dict]:
    from . import codexinstall  # noqa: PLC0415

    ok, steps, _summary = codexinstall._run_pipeline(apply=True, upgrade=False)
    if not ok and not any(s.get("error_code") for s in steps if not s["ok"]):
        steps.append(_step("codex", ok=False, code=ERR_CODEX))
    return steps


def _codex_disconnect() -> list[dict]:
    from . import codexinstall  # noqa: PLC0415

    _ok, steps = codexinstall.uninstall_steps()
    return steps


# ----------------------------------------------------------------------------- 动作
def installed(host: str) -> bool:
    if host in _FILE_HOSTS:
        return bool(_FILE_HOSTS[host].detect())
    if host == "claude-code":
        return _claude_argv() is not None
    if host == "codex":
        from . import codexinstall  # noqa: PLC0415

        return codexinstall.find_codex()[0] is not None
    return False


def _codex_connected() -> bool:
    """Codex：插件装上了（`codexinstall.installed_plugin_dir()`，问的是 Codex 自己的安装记录）。"""
    from . import codexinstall  # noqa: PLC0415

    try:
        return codexinstall.installed_plugin_dir() is not None
    except Exception:  # noqa: BLE001 — 列表只是状态，问不到就当没接
        return False


def _claude_code_connected() -> bool:
    """Claude Code：`claude mcp get tavotto` 认得它（user 级登记或插件带来的同名 server 都算）。"""
    claude = _claude_argv()
    if not claude:
        return False
    rc, out = _run([*claude, "mcp", "list"], timeout=60)
    # 每行「名字: 命令 - 状态」；插件那条的名字自己带冒号（plugin:tavotto:tavotto），所以按行首前缀判
    prefixes = (f"{SERVER_NAME}:", f"plugin:{SERVER_NAME}:{SERVER_NAME}:")
    return rc == 0 and any(line.strip().startswith(prefixes) for line in out.splitlines())


def connected(host: str, launch: dict | None, env: dict) -> bool:
    """文件类宿主：每个目标文件里 tavotto 那一条与「现在会写的」逐字相同。Codex / Claude Code 问各自的 CLI
    （Codex #1455 P2：之前这两家永远报「未接入」）。"""
    if host == "codex":
        return _codex_connected()
    if host == "claude-code":
        return _claude_code_connected()
    if host not in _FILE_HOSTS or launch is None:
        return False
    targets = _FILE_HOSTS[host].targets()
    return bool(targets) and all(_target_entry_now(t) == t.entry(launch, env) for t in targets)


def _env_for(root: str) -> dict:
    return {ROOTS_ENV: root}


def health(launch: dict, env: dict) -> dict:
    """按要写进宿主的那条命令真起一次：握手 + 列工具。只证明「这条命令起得来、是完整的服务端」。"""
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tavotto-hosts", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        p = subprocess.run(
            [launch["command"], *launch["args"]],
            input="".join(json.dumps(m) + "\n" for m in msgs),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **env},
            timeout=180,
            creationflags=CREATE_NO_WINDOW,
        )
        by_id = {
            m.get("id"): m for m in (json.loads(x) for x in p.stdout.splitlines() if x.strip())
        }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _step("health", ok=False, code=ERR_HEALTH, detail=str(exc)[:300])
    tools = ((by_id.get(2) or {}).get("result") or {}).get("tools") or []
    names = {t.get("name") for t in tools}
    if "tavotto_open_figure" not in names:
        return _step("health", ok=False, code=ERR_HEALTH, detail=(p.stderr or "")[-300:])
    return _step("health", ok=True, detail=f"{len(names)} 个工具")


def connect(host: str, project_root: str | None = None) -> dict:
    if host not in HOST_IDS:
        return _result(host, "connect", [_step("host", ok=False, code=ERR_UNKNOWN_HOST)])
    if host == "codex":
        # Codex 走插件（自带启动器与技能），不用这一份的 MCP 命令、也不用文件夹：pip / pipx 装的同样能接（Codex #1455 P2）
        steps = _codex_connect()
        out = _result(host, "connect", steps)
        if out["ok"]:
            changed = any(s["ok"] and not s["skipped"] for s in steps)
            out["state"] = "needs_new_session" if changed else "connected"
        return out
    launch = mcp_launch()
    if launch is None:
        return _result(
            host,
            "connect",
            [
                _step(
                    "server", ok=False, code=ERR_NO_SERVER, detail="这份 Tavotto 里没有 MCP 服务端"
                )
            ],
        )
    state = load_state()
    root = project_root or state.get("project_root")
    if not root:
        return _result(host, "connect", [_step("project_root", ok=False, code=ERR_NO_ROOT)])
    root, why = check_project_root(root)
    if root is None:
        return _result(
            host, "connect", [_step("project_root", ok=False, code=ERR_BAD_ROOT, detail=why or "")]
        )
    if state.get("project_root") != root:
        _save_state({**state, "project_root": root})
    env = _env_for(root)
    steps: list[dict] = [_step("project_root", ok=True, skipped=True, detail=root)]
    if host == "claude-code":
        steps += _claude_code_connect(launch, env)
    else:
        fh = _FILE_HOSTS[host]
        if not fh.detect():
            return _result(
                host,
                "connect",
                [*steps, _step("detect", ok=False, code=ERR_NOT_INSTALLED)],
                state="not_installed",
            )
        for t in fh.targets():
            steps.append(
                merge_json_file(t.path, _target_mutate(t, t.entry(launch, env)), what="config")
            )
        if fh.after_connect and all(s["ok"] for s in steps):
            extra = fh.after_connect(launch, env)
            if extra:
                steps.append(extra)
    if all(s["ok"] for s in steps):
        steps.append(health(launch, env))
    changed = any(s["ok"] and not s["skipped"] for s in steps if s["step"] != "health")
    out = _result(host, "connect", steps, project_root=root)
    if out["ok"]:
        out["state"] = "needs_new_session" if changed else "connected"
    return out


def disconnect(host: str) -> dict:
    if host not in HOST_IDS:
        return _result(host, "disconnect", [_step("host", ok=False, code=ERR_UNKNOWN_HOST)])
    if host == "codex":
        steps = _codex_disconnect()
    elif host == "claude-code":
        steps = _claude_code_disconnect()
    else:
        steps = [
            merge_json_file(t.path, _target_drop(t), what="config")
            for t in _FILE_HOSTS[host].targets()
            if t.path.is_file()
        ] or [_step("config", ok=True, skipped=True, detail="本来就没接入")]
    out = _result(host, "disconnect", steps)
    if out["ok"]:
        out["state"] = "disconnected"
    return out


def list_hosts() -> dict:
    launch = mcp_launch()
    root = load_state().get("project_root")
    env = _env_for(root) if root else {}
    hosts = []
    for h in HOST_IDS:
        inst = installed(h)
        hosts.append(
            {
                "id": h,
                "label": LABELS[h],
                "installed": inst,
                # Codex 不经文件夹（插件自带授权逻辑），其余要先有文件夹才谈得上「接入」
                "connected": inst and (h == "codex" or bool(root)) and connected(h, launch, env),
            }
        )
    return {
        "ok": True,
        "action": "list",
        "server": launch is not None,
        "project_root": root,
        "hosts": hosts,
    }


# ----------------------------------------------------------------------------- CLI
def _emit(result: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for s in result.get("steps", []):
            mark = "跳过" if s.get("skipped") else ("通过" if s["ok"] else "失败")
            print(f"[{mark}] {s['step']}: {s.get('detail', '')}")
        for h in result.get("hosts", []):
            mark = "已接入" if h["connected"] else ("可接入" if h["installed"] else "未检测到")
            print(f"{h['label']}: {mark}")
    return 0 if result.get("ok") else 1


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="tavotto hosts", description="把 Tavotto 接入本机的 AI 编辑器（写配置前先备份）"
    )
    ap.add_argument("action", choices=("list", "connect", "disconnect"))
    ap.add_argument("host", nargs="?", choices=HOST_IDS)
    ap.add_argument(
        "--project-root", help="授权给宿主的项目文件夹（第一次接入必填，之后所有宿主共用）"
    )
    ap.add_argument("--json", action="store_true", help="输出一行机器可读结果")
    args = ap.parse_args(argv)
    if args.action == "list":
        return _emit(list_hosts(), args.json)
    if not args.host:
        ap.error("connect / disconnect 要指定宿主")
    if args.action == "connect":
        return _emit(connect(args.host, args.project_root), args.json)
    return _emit(disconnect(args.host), args.json)
