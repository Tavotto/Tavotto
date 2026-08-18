"""外部程序把一张刚画好的图交接给 Magplot：`magplot open <路径>`。

Codex 插件（`codex-plugin/`）跑完脚本后调的就是这条命令，但它不是为插件特制的
——编辑器、Makefile、别的 Agent、用户自己敲，都走同一条路。

三步，顺序不能换：

  1. **解析目标**。给一个产物（PDF/PNG…）、一个脚本（.py）或一个目录，判定出
     「项目目录 + stem」。项目 = 含 `mm_registry.json` 的那一层（向上找 ≤3 层），
     找不到才退回图自己所在的目录——Magplot 的世界观是「项目 = 图库目录」而不是
     「一张图」，交接第一件事就是把这个翻译对，否则用户打开的是一个只有一张图的
     孤儿目录，旁边的图和脚本全不见了。
  2. **登记 stem**。注册表里缺这一条，图能显示但双击进不去（不可参数化，
     `registry.for_stem` 回 None）。合并走 `discover.merge`——「现有条目永远优先、
     冲突只报告不裁决」的语义在那儿，**这里绝不另写一套裁决**。
  3. **唤起**。优先桌面 App：单实例插件会把第二次启动的 argv 转发给已经在跑的
     那个窗口（所以这里**直接 exec 包内二进制**，不用 macOS 的 `open -a`——
     App 已在运行时 `open -a` 的 `--args` 根本不会送达）。没有桌面 App 才退回
     浏览器模式。

纯标准库，Flask 父进程可安全 import（不碰 matplotlib，也不 import app）。

**桌面 App 装在哪儿由 `engine/locate.py` 说了算**（同一份清单还要给 CLI shim
的发现用，见 `docs/handoff-protocol.md`），本模块只负责拿它去唤起。那边的路径
拼接全程 os.path 字符串：避免 `Path()` 在不同平台下生成不同的分隔符，从而可在
mac/CI 上单测 Windows 的安装路径（同 `engine/runtime.py`；看护用例
`tests/test_handoff.py` 与 `tests/test_install_locate.py`）。本模块仅在静态扫描
脚本时用 `pathlib.Path`（`analyze_script` 需要）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from . import discover as engine_discover
from . import locate as engine_locate
from . import registry as engine_registry

#: 认得的产物后缀，与静态扫描同源
OUT_EXTS = engine_discover.OUT_EXTS
#: 向上找 mm_registry.json 的层数。再往上就该问用户了——静默把某个上层目录
#: 当成图库，会把一整棵源码树当素材扫一遍。
MAX_PARENTS = 3
#: 桌面 App 路径覆盖（开发态指向 dist/Magplot，与 MM_WORKER_PYTHON 同款惯例）
APP_ENV = "MAGPLOT_DESKTOP_APP"
DEFAULT_PORT = 5089


class HandoffError(RuntimeError):
    """交接无法继续（路径不存在、不是图、注册表损坏）。CLI 转成非零退出码。

    **`code` 是给机器读的，必须稳定**：调用方（Codex 插件、编辑器）要按它
    分诊——「注册表写不进去」该提示改目录权限，「桌面版没装」该提示去下载，
    两件事都塞进一句中文 `error` 里，对面只能做字符串匹配。文案可以随时改，
    code 不行。全部 code 见 `docs/handoff-protocol.md`。
    """

    def __init__(self, message: str, code: str = "handoff_failed") -> None:
        super().__init__(message)
        self.code = code


class Target(NamedTuple):
    """交接目标：项目目录 + 要定位的 stem（目录级交接时 stem 为 None）。"""

    project: str
    stem: str | None


# --------------------------- 1. 解析目标 ---------------------------------
def _project_root(folder: str, *, isfile) -> str:
    """从 folder 向上找注册表所在层；找不到就是 folder 自己。"""
    cur = folder
    for _ in range(MAX_PARENTS + 1):
        if isfile(os.path.join(cur, engine_registry.REGISTRY_NAME)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return folder


def _script_stems(script: str, project: str) -> list[str]:
    """脚本 → 它产出的 stem（静态求值）。解不出返回空列表，绝不猜。"""
    try:
        info = engine_discover.analyze_script(Path(script), Path(project))
    except (OSError, ValueError, RecursionError):
        return []
    return list(info["stems"]) if info else []


def _first_on_disk(stems: list[str], project: str, *, isfile=os.path.isfile) -> str | None:
    """一脚本多产物时优先取磁盘上真的存在的那个（刚跑完的那张图）。"""
    for stem in stems:
        for ext in OUT_EXTS:
            if isfile(os.path.join(project, stem + ext)):
                return stem
    return stems[0] if stems else None


def resolve_target(raw: str, *, isdir=os.path.isdir, isfile=os.path.isfile) -> Target:
    """`<路径>` → (项目目录, stem)。路径可以是产物、脚本或目录。"""
    if not raw or not raw.strip():
        raise HandoffError("要打开的路径不能为空", "empty_path")
    path = os.path.abspath(os.path.expanduser(raw.strip()))

    if isdir(path):
        return Target(_project_root(path, isfile=isfile), None)
    if not isfile(path):
        raise HandoffError(f"路径不存在: {path}", "path_not_found")

    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    project = _project_root(folder, isfile=isfile)

    if ext.lower() == ".py":
        # 脚本：产物名由静态扫描解出（解不出就只打开项目，不假装知道 stem）
        stems = _script_stems(path, project)
        return Target(project, _first_on_disk(stems, project, isfile=isfile))
    if ext.lower() in OUT_EXTS:
        return Target(project, stem)
    raise HandoffError(
        f"不认识的文件类型: {name}"
        f"（要一张图 {'/'.join(e.lstrip('.') for e in OUT_EXTS[:3])}…、"
        f"一个 .py 脚本，或一个目录）", "unsupported_file")


# --------------------------- 2. 登记 stem --------------------------------
def _registered(project: str, stem: str) -> bool:
    try:
        reg = engine_registry.open_registry(project)
    except FileNotFoundError:
        return False
    except RuntimeError as exc:                      # 注册表损坏 / 重复 stem
        raise HandoffError(f"注册表无法加载，请先修好它: {exc}",
                           "registry_invalid") from exc
    return reg.for_stem(stem) is not None


def ensure_registered(project: str, stem: str | None) -> dict:
    """确保 stem 在注册表里；缺了就按静态扫描合并进去（现有条目原样保留）。

    返回给 CLI/插件的自检信息：是否可参数化、新增了什么、哪些脚本静态解不出
    stem（`dynamic_names`，得走试运行探测）、有没有归属冲突。

    `status` 是给机器分诊用的一个词，四种取值互斥：

      already   注册表里本来就有这条，一个字节都没动
      created   项目里原本没有注册表，这次新建了一份
      merged    注册表已存在，这次合并进了新的脚本 / stem
      unchanged 注册表已存在，扫完发现没什么可加的
    """
    info: dict = {"registry": str(engine_registry.registry_path(project)),
                  "status": "already",
                  "created": False, "added_scripts": [], "added_stems": {},
                  "conflicts": [], "dynamic_names": [], "parameterizable": None}
    if stem is not None and _registered(project, stem):
        info["parameterizable"] = True
        return info

    existed = os.path.isfile(os.path.join(project, engine_registry.REGISTRY_NAME))
    try:
        cfg, rep, changes = engine_discover.merge(project)
    except ValueError as exc:                        # 用户手写的 JSON 坏了
        raise HandoffError(f"注册表不是合法 JSON，未做任何改动: {exc}",
                           "registry_invalid") from exc
    except OSError as exc:
        raise HandoffError(f"无法读取图库目录 {project}: {exc}",
                           "project_unreadable") from exc

    should_write = (not existed) or changes["added_scripts"] or changes["added_stems"]
    if should_write:
        try:
            engine_discover.write_config(project, cfg)
        except OSError as exc:
            # 只读目录 / 没有写权限 / 磁盘满。以前这条裸 OSError 会一路冒到
            # `magplot open` 外面变成 traceback，插件那侧只看得到「脚本挂了」。
            raise HandoffError(
                f"注册表写不进去 {info['registry']}: {exc}"
                "（图库目录需要可写；换一个目录，或修好它的权限后重试）",
                "registry_write_failed") from exc
    info["created"] = not existed
    if not existed:
        info["status"] = "created"
    elif changes["added_scripts"] or changes["added_stems"]:
        info["status"] = "merged"
    else:
        info["status"] = "unchanged"
    info["added_scripts"] = list(changes["added_scripts"])
    info["added_stems"] = {k: list(v) for k, v in changes["added_stems"].items()}
    info["conflicts"] = sorted(rep["conflicts"])
    info["dynamic_names"] = sorted(s for s, i in rep["scripts"].items()
                                   if i.get("dynamic_names"))
    if stem is not None:
        info["parameterizable"] = _registered(project, stem)
    return info


# --------------------------- 3. 唤起界面 ---------------------------------
def desktop_app_candidates(*, system: str | None = None,
                           environ: dict | None = None,
                           isfile=os.path.isfile) -> list[str]:
    """桌面 App 可执行文件的候选路径（按优先级）。

    安装位置的**唯一出处是 `engine/locate.install_roots()`**——同一份清单还要
    给 CLI shim 的发现用（Codex 插件那条链），在这儿再抄一遍就是第二个权威。

    惯例位置**不是全部**：用户会把 `Magplot.app` 从 `/Applications` 拖到别处、
    会装在非默认盘。那时发现链照样找得到 CLI（清单里记着绝对路径），唤起却
    只按惯例位置找 → 交接静默退回浏览器模式，用户明明有桌面版却看不到窗口。
    所以这里在惯例位置**之前**先认两条更可靠的：

      1. 我自己旁边那个壳——冻结产物里 sidecar/CLI 与壳的相对位置是固定的，
         装到哪个盘都不用猜；
      2. 安装清单里核实过的 `desktop`。
    """
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    out: list[str] = []
    override = (env.get(APP_ENV) or "").strip()
    if override:
        out.append(override)                          # 用户显式指定的永远第一
    if getattr(sys, "frozen", False):
        # **只在冻结产物里问这一条。** 那时壳与 sidecar/CLI 的相对位置是打包
        # 时固定下来的，比任何惯例位置都准。非冻结时 describe_self 的 desktop
        # 本来就是从惯例位置推出来的，摆在这儿只会把清单挤到后面去——而清单
        # 恰恰是「装在非惯例位置」时唯一知道真相的那个。
        me = engine_locate.describe_self(system=system, environ=env, isfile=isfile)
        if me.get("desktop"):
            out.append(me["desktop"])
    manifest = engine_locate.read_manifest(system=system, environ=env, isfile=isfile)
    if manifest and manifest.get("desktop"):
        out.append(manifest["desktop"])
    # Linux 没有桌面发行形态（desktop-tauri.yml 只发 macOS/Windows）：回空表 → 浏览器
    out += [engine_locate.desktop_exe_for(root, system=system)
            for root in engine_locate.install_roots(system=system, environ=env)]
    return list(dict.fromkeys(out))                   # 去重，保序


def find_desktop_app(*, system: str | None = None, environ: dict | None = None,
                     isfile=os.path.isfile) -> str | None:
    for cand in desktop_app_candidates(system=system, environ=environ, isfile=isfile):
        if cand and isfile(cand):
            return cand
    return None


def desktop_argv(app: str, target: Target) -> list[str]:
    """桌面壳的交接契约。**与 src-tauri/src/main.rs 的 parse_open_args 严格同源。**"""
    argv = [app, "--open", target.project]
    if target.stem:
        argv += ["--stem", target.stem]
    return argv


def _spawn_detached(argv: list[str], *, spawn=subprocess.Popen) -> None:
    """起一个不随本进程生死的界面进程：CLI 交接完就该退出，不当爹。"""
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：控制台关了也不带走窗口
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    spawn(argv, **kwargs)


def _http_json(url: str, payload: dict | None = None, timeout: float = 1.0) -> dict | None:
    """本机 API 的极简调用；连不上 / 不是 JSON 一律 None（探测失败不是错误）。"""
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def browser_url(port: int, target: Target, pj: str | None = None) -> str:
    """浏览器模式的落地地址：`?open=<stem>` 由前端 lib/openRequest.ts 消费。"""
    qs = []
    if pj:
        qs.append("pj=" + quote(pj, safe=""))
    if target.stem:
        qs.append("open=" + quote(target.stem, safe=""))
    return f"http://127.0.0.1:{port}/" + ("?" + "&".join(qs) if qs else "")


def launch(target: Target, *, prefer: str = "auto", port: int = DEFAULT_PORT,
           system: str | None = None, environ: dict | None = None,
           isfile=os.path.isfile, spawn=subprocess.Popen,
           http=_http_json, browse=webbrowser.open) -> dict:
    """唤起界面。返回 {"mode": ..., ...}；mode 是给插件看的机器可读值。"""
    if prefer not in ("auto", "desktop", "browser"):
        raise HandoffError(f"未知的唤起方式: {prefer}", "bad_launch_mode")

    if prefer != "browser":
        app = find_desktop_app(system=system, environ=environ, isfile=isfile)
        if app:
            argv = desktop_argv(app, target)
            try:
                _spawn_detached(argv, spawn=spawn)
            except OSError as exc:
                # 文件在、但起不来（权限、被杀软拦、可执行位丢了）。裸 OSError
                # 冒出去只会变成 traceback，调用方分不清「没装」和「起不来」。
                raise HandoffError(f"Magplot 桌面应用启动失败 {app}: {exc}",
                                   "launch_failed") from exc
            return {"mode": "desktop", "app": app, "argv": argv}
        if prefer == "desktop":
            raise HandoffError(
                "没找到 Magplot 桌面应用。装一个（GitHub Releases），"
                f"或用 {APP_ENV} 指到它的可执行文件，"
                "或去掉 --desktop 走浏览器模式。", "desktop_missing")

    # 浏览器模式：先问问本机有没有已经在跑的实例——有就让它开这个项目，
    # 绝不再起第二个进程去抢同一个端口（抢不到的那个只会把用户送回旧项目）。
    if http(f"http://127.0.0.1:{port}/api/version", timeout=0.6):
        st = http(f"http://127.0.0.1:{port}/api/projects/open",
                  {"path": target.project}, timeout=10.0) or {}
        if st.get("error"):
            raise HandoffError(f"已在运行的 Magplot 打不开这个项目: {st['error']}",
                               "remote_open_failed")
        url = browser_url(port, target, st.get("id"))
        browse(url)
        return {"mode": "browser-existing", "url": url}

    # **冻结产物里没有 `-m magplot` 这回事**：那时 sys.executable 就是
    # Magplot 自己（magplot-cli.exe / Magplot.exe），拼成
    # `magplot-cli -m magplot --figures …` 会在 argparse 里报 unrecognized
    # arguments 当场退出——用户看到的是「点了没反应」。直接给主入口的 flag。
    launcher = ([sys.executable] if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "magplot"])
    argv = [*launcher, "--figures", target.project, "--port", str(port)]
    if target.stem:
        argv += ["--open-stem", target.stem]
    try:
        _spawn_detached(argv, spawn=spawn)
    except OSError as exc:
        raise HandoffError(f"Magplot 启动失败: {exc}", "launch_failed") from exc
    return {"mode": "browser-new", "argv": argv, "url": browser_url(port, target)}


# ------------------------------ 编排与 CLI --------------------------------
def open_target(raw: str, *, prefer: str = "auto", port: int = DEFAULT_PORT,
                launch_ui: bool = True, **kw) -> dict:
    """解析 → 登记 → 唤起。返回一份可直接 json.dumps 的结果。"""
    from .. import __version__                    # 版本号唯一出处，别在这儿写死
    target = resolve_target(raw)
    registry_info = ensure_registered(target.project, target.stem)
    # `version` 是**这次真正干活的那个 Magplot** 的版本。调用方（Codex 插件）
    # 要拿它比 min_magplot_version——插件自己的版本与它各有各的升级节奏，
    # 混为一谈会提示用户去升级一个根本没问题的东西。
    result = {"ok": True, "protocol": engine_locate.PROTOCOL_VERSION,
              "version": __version__,
              "project": target.project, "stem": target.stem,
              "registry": registry_info, "launch": None}
    if launch_ui:
        result["launch"] = launch(target, prefer=prefer, port=port, **kw)
    return result


def _report(result: dict) -> None:
    """人类可读输出。插件读的是 --json，这里说给人听。"""
    print(f"* 项目: {result['project']}")
    reg = result["registry"]
    if result["stem"]:
        print(f"* 面板: {result['stem']}")
    if reg["created"]:
        print(f"* 已生成脚本注册表 {reg['registry']}（cost 默认 medium，可按需修正）")
    elif reg["added_scripts"] or reg["added_stems"]:
        added = ", ".join(reg["added_scripts"]) or ", ".join(reg["added_stems"])
        print(f"* 注册表已合并新条目: {added}（现有条目未改动）")
    if reg["parameterizable"] is False:
        print("! 这张图没有对应脚本，打开后只能当素材排版，双击进不去图内编辑。"
              "\n  静态扫描解不出它的产出名时，"
              "用「设置 → 脚本注册表 → 试运行探测」登记。")
    for script in reg["dynamic_names"]:
        print(f"  ? {script} 的输出名来自运行期数据，"
              "静态定位不到 stem（可用试运行探测）")
    if reg["conflicts"]:
        print(f"  ⚠ stem 归属冲突未裁决: {', '.join(reg['conflicts'])}"
              f"\n    请在 {reg['registry']} 里手工指定归属")
    launch_info = result.get("launch")
    if not launch_info:
        return
    mode = launch_info["mode"]
    if mode == "desktop":
        print("* 已交给 Magplot 桌面应用")
    elif mode == "browser-existing":
        print(f"* 已交给正在运行的 Magplot: {launch_info['url']}")
    else:
        print(f"* 正在启动 Magplot: {launch_info['url']}")


def cli(argv: list[str]) -> int:
    """`magplot open` 的入口。返回退出码。"""
    ap = argparse.ArgumentParser(
        prog="magplot open",
        description="把一张图 / 一个脚本 / 一个图库目录交给 Magplot 打开")
    ap.add_argument("path", help="产物（.pdf/.png…）、脚本（.py）或图库目录")
    ap.add_argument("--desktop", action="store_true",
                    help="必须用桌面应用，找不到就失败")
    ap.add_argument("--browser", action="store_true", help="强制浏览器模式")
    ap.add_argument("--no-launch", action="store_true",
                    help="只解析与登记，不唤起界面（自检用）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="浏览器模式端口")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)
    # stdout 是管道时 Windows 退回 cp936/cp1252，输出里的中文（项目路径、
    # 错误文案）第一次 print 就 UnicodeEncodeError——调用方看到的是「命令挂了」。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.desktop and args.browser:
        msg = "--desktop 与 --browser 不能同时给"
        if args.json:
            print(json.dumps({"ok": False, "protocol": engine_locate.PROTOCOL_VERSION,
                              "code": "bad_launch_mode", "error": msg},
                             ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2
    prefer = "desktop" if args.desktop else "browser" if args.browser else "auto"

    try:
        result = open_target(args.path, prefer=prefer, port=args.port,
                             launch_ui=not args.no_launch)
    except HandoffError as exc:
        # 失败也必须是**机器可解析的一行 JSON**：调用方按 `code` 分诊，
        # 拿不到 JSON 就只能去猜 stderr 里那句中文是什么意思。
        if args.json:
            print(json.dumps({"ok": False,
                              "protocol": engine_locate.PROTOCOL_VERSION,
                              "code": exc.code, "error": str(exc)},
                             ensure_ascii=False))
        else:
            print(f"打不开: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _report(result)
    return 0
