"""`tavotto codex install / doctor / uninstall`（ADR 0012）。**纯标准库。**

## 为什么是一条命令，而不是一份文档

首次使用体验重构把普通用户的安装收敛到了「两条 Codex 命令 + 一条引擎命令 + 新开
会话」。那仍是四个手工步骤，且失败分诊靠用户读输出。桌面设置页也想要一个「安装
Codex 集成」按钮——**如果按钮另写一套安装器，它就会与 README 的命令漂移**（本仓库
最忌讳的第二权威）。所以先有这条命令，按钮以后 spawn 它。

## 三条纪律

* **幂等，缺什么补什么。** 每一步都带 `skipped`：重跑一次必须能看出「什么都没做」。
  健康状态下不重装任何组件（与 SKILL 会话入口同一契约）。
* **只报告，不代劳。** 不自动装/升级 Codex CLI 本身；找不到就给安装指引。
* **`--json` 时失败也必须是一行 JSON**，带稳定 `error_code`（与 `tavotto open`
  同一条纪律）。只往 stderr 写一句中文，调用方就只能去匹配字符串。

安装参数（marketplace 源、sparse 路径、插件引用）全部从 `brand.py` 派生——README
与这条命令共用同一份，看护在 `tests/test_codex_install_cli.py`。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import brand

#: 每一步的稳定 code。message 随时可改，code 不许改（调用方按它分诊）。
ERR_CODEX_MISSING = "codex_cli_missing"
ERR_MARKETPLACE = "marketplace_add_failed"
ERR_PLUGIN = "plugin_add_failed"
ERR_PROVISION = "provision_failed"
ERR_HEALTH = "health_failed"
ERR_UNINSTALL = "uninstall_failed"

#: 单条 Codex 命令的上限。marketplace add 要拉一次稀疏检出，给宽一点；
#: 但必须有上限——没有网络时它会一直挂着，而调用方在等那行 JSON。
_TIMEOUT = 180


# ------------------------------ 探测 ------------------------------
def codex_home() -> Path:
    """Codex 自己的配置目录（`CODEX_HOME` 是官方覆盖变量，测试也用它重定向）。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _search_dirs() -> list[Path]:
    """PATH 之外还值得找的地方（与 `ai_agents` 的探测思路同源，独立实现在纯标准库层）。"""
    home = Path.home()
    out = [home / ".codex" / "bin", home / ".local" / "bin", home / "bin"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "npm")
    else:
        out += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return out


def find_codex() -> tuple[str | None, list[str]]:
    """找 `codex` 可执行文件。返回 (路径 or None, 找过哪些位置)。

    **找过哪些位置要如实报出来**：用户装在别处时，「找不到」这三个字什么忙都帮不上，
    而一串路径他一眼就能看出该往哪儿指（与 AI 桥的 `diagnostics.searched` 同一纪律）。
    """
    searched: list[str] = ["PATH"]
    hit = shutil.which("codex")
    if hit:
        return hit, searched
    for d in _search_dirs():
        exe = d / ("codex.cmd" if os.name == "nt" else "codex")
        searched.append(str(d))
        if exe.is_file() and os.access(exe, os.X_OK):
            return str(exe), searched
    return None, searched


def _run(argv: list[str], timeout: int = _TIMEOUT) -> tuple[int, str]:
    """跑一条命令，回 (退出码, 合并输出)。绝不抛——失败也是一种结论。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, f"找不到可执行文件：{argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"超过 {timeout}s 没有返回：{' '.join(argv)}"
    except OSError as exc:
        return 126, f"{type(exc).__name__}: {exc}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def installed_plugin_dir() -> Path | None:
    """已装插件在 Codex 那边的落点（`$CODEX_HOME/plugins/**/.codex-plugin/plugin.json`）。

    按**清单里的 name** 认，不按目录名：目录名带缓存哈希，会随版本变。
    """
    root = codex_home() / "plugins"
    if not root.is_dir():
        return None
    for manifest in sorted(root.rglob(".codex-plugin/plugin.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("name") == brand.CODEX_PLUGIN_NAME:
            return manifest.parent.parent
    return None


def engine_importable() -> bool:
    """这个解释器能不能 `import tavotto.engine`——pip/pipx 形态天然满足。"""
    try:
        import tavotto.engine  # noqa: F401
    except Exception:
        return False
    return True


# ------------------------------ 步骤 ------------------------------
def _step(name: str, *, ok: bool, skipped: bool = False, detail: str = "",
          code: str = "") -> dict:
    out = {"step": name, "ok": ok, "skipped": skipped}
    if detail:
        out["detail"] = detail
    if code:
        out["error_code"] = code
    return out


def _marketplace_step(codex: str, *, apply: bool) -> dict:
    rc, out = _run([codex, "plugin", "marketplace", "list"])
    if rc == 0 and brand.CODEX_PLUGIN_NAME in out:
        return _step("marketplace", ok=True, skipped=True, detail="已登记")
    if not apply:
        return _step("marketplace", ok=False, detail="未登记", code=ERR_MARKETPLACE)
    argv = [codex, "plugin", "marketplace", "add", brand.CODEX_MARKETPLACE]
    for sparse in brand.CODEX_SPARSE_PATHS:
        argv += ["--sparse", sparse]
    rc, out = _run(argv)
    if rc != 0:
        return _step("marketplace", ok=False, detail=out[-400:], code=ERR_MARKETPLACE)
    return _step("marketplace", ok=True, detail="已登记")


def _plugin_step(codex: str, *, apply: bool) -> dict:
    rc, out = _run([codex, "plugin", "list"])
    if rc == 0 and brand.CODEX_PLUGIN_NAME in out:
        # **健康状态下不重装。** 升级归 `codex plugin marketplace upgrade`，
        # 由用户自己决定什么时候做；这条命令的职责是「缺什么补什么」。
        return _step("plugin", ok=True, skipped=True, detail="已安装")
    if not apply:
        return _step("plugin", ok=False, detail="未安装", code=ERR_PLUGIN)
    rc, out = _run([codex, "plugin", "add", brand.CODEX_PLUGIN_REF])
    if rc != 0:
        return _step("plugin", ok=False, detail=out[-400:], code=ERR_PLUGIN)
    return _step("plugin", ok=True, detail="已安装")


def _engine_step(plugin_dir: Path | None, *, apply: bool) -> dict:
    if engine_importable():
        return _step("engine", ok=True, skipped=True,
                     detail="当前解释器已能 import tavotto.engine")
    if plugin_dir is None:
        return _step("engine", ok=False, detail="插件还没装好，无从 provision",
                     code=ERR_PROVISION)
    server = plugin_dir / "mcp" / "server.py"
    if not server.is_file():
        return _step("engine", ok=False, detail=f"插件里没有 {server}", code=ERR_PROVISION)
    if not apply:
        return _step("engine", ok=False, detail="需要 provision", code=ERR_PROVISION)
    # **复用插件自己的 --provision**，不抄第二份：那份实现知道该建在哪、装什么版本
    rc, out = _run([sys.executable, str(server), "--provision"])
    if rc != 0:
        return _step("engine", ok=False, detail=out[-400:], code=ERR_PROVISION)
    return _step("engine", ok=True, detail="已准备匹配版本的引擎")


def _health_step(plugin_dir: Path | None) -> dict:
    if plugin_dir is None:
        return _step("health", ok=False, detail="找不到已装的插件", code=ERR_HEALTH)
    server = plugin_dir / "mcp" / "server.py"
    rc, out = _run([sys.executable, str(server), "--health"], timeout=90)
    if rc != 0:
        return _step("health", ok=False, detail=out[-400:], code=ERR_HEALTH)
    return _step("health", ok=True, detail=out[-400:])


# ------------------------------ 三个子命令 ------------------------------
def _codex_or_fail(steps: list[dict]) -> str | None:
    codex, searched = find_codex()
    if codex is None:
        steps.append(_step(
            "codex_cli", ok=False, code=ERR_CODEX_MISSING,
            detail="找不到 codex 命令。找过：" + "、".join(searched)
            + "。请先安装 Codex CLI（本命令不代装），装好后重跑。"))
        return None
    steps.append(_step("codex_cli", ok=True, detail=codex))
    return codex


def _run_pipeline(*, apply: bool) -> tuple[bool, list[dict]]:
    steps: list[dict] = []
    codex = _codex_or_fail(steps)
    if codex is None:
        return False, steps
    steps.append(_marketplace_step(codex, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    steps.append(_plugin_step(codex, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    plugin_dir = installed_plugin_dir()
    steps.append(_engine_step(plugin_dir, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    steps.append(_health_step(plugin_dir))
    return steps[-1]["ok"], steps


def uninstall_steps() -> tuple[bool, list[dict]]:
    """移除插件与 marketplace 项。**不碰引擎**——它可能还有别的用处。"""
    steps: list[dict] = []
    codex = _codex_or_fail(steps)
    if codex is None:
        return False, steps
    rc, out = _run([codex, "plugin", "list"])
    if rc == 0 and brand.CODEX_PLUGIN_NAME in out:
        rc, out = _run([codex, "plugin", "remove", brand.CODEX_PLUGIN_REF])
        steps.append(_step("plugin", ok=rc == 0, detail=out[-400:] or "已移除",
                           code="" if rc == 0 else ERR_UNINSTALL))
    else:
        steps.append(_step("plugin", ok=True, skipped=True, detail="本来就没装"))
    rc, out = _run([codex, "plugin", "marketplace", "list"])
    if rc == 0 and brand.CODEX_PLUGIN_NAME in out:
        rc, out = _run([codex, "plugin", "marketplace", "remove",
                        brand.CODEX_MARKETPLACE])
        steps.append(_step("marketplace", ok=rc == 0, detail=out[-400:] or "已移除",
                           code="" if rc == 0 else ERR_UNINSTALL))
    else:
        steps.append(_step("marketplace", ok=True, skipped=True, detail="本来就没登记"))
    return all(s["ok"] for s in steps), steps


def _emit(ok: bool, action: str, steps: list[dict], *, as_json: bool) -> int:
    failed = next((s for s in steps if not s["ok"]), None)
    if as_json:
        payload = {"ok": ok, "action": action, "steps": steps}
        if failed and failed.get("error_code"):
            payload["error_code"] = failed["error_code"]
            payload["error"] = failed.get("detail", "")
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if ok else 1
    for s in steps:
        mark = "跳过" if s.get("skipped") else ("✓" if s["ok"] else "✗")
        line = f"{mark} {s['step']}"
        if s.get("detail"):
            line += f"：{s['detail']}"
        print(line, file=sys.stdout if s["ok"] else sys.stderr)
    if ok and action == "install":
        # 刻意**只说这一句**：旧会话里验不出工具来，试图验证只会给出误导性的结论
        print("\n装好了。请新开一个 Codex 会话。")
    return 0 if ok else 1


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="tavotto codex",
        description="安装 / 诊断 / 移除 Tavotto 的 Codex 集成（ADR 0012）")
    ap.add_argument("action", choices=("install", "doctor", "uninstall"))
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    if args.action == "uninstall":
        ok, steps = uninstall_steps()
    else:
        ok, steps = _run_pipeline(apply=args.action == "install")
    return _emit(ok, args.action, steps, as_json=args.json)
