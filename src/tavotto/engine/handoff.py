"""外部程序把一张刚画好的图交接给 Tavotto：`tavotto open <路径>`。

Codex 插件（`codex-plugin/`）跑完脚本后调的就是这条命令，但它不是为插件特制的
——编辑器、Makefile、别的 Agent、用户自己敲，都走同一条路。

三步，顺序不能换：

  1. **解析目标**。给一个产物（PDF/PNG…）、一个脚本（.py）或一个目录，判定出
     「项目目录 + stem」。项目 = 含 `tavotto_registry.json` 的那一层（向上找 ≤3 层），
     找不到才退回图自己所在的目录——Tavotto 的世界观是「项目 = 图库目录」而不是
     「一张图」，交接第一件事就是把这个翻译对，否则用户打开的是一个只有一张图的
     孤儿目录，旁边的图和脚本全不见了。
  2. **登记 stem**。注册表里缺这一条，图能显示但双击进不去（不可参数化，
     `registry.for_stem` 回 None）。合并走 `discover.merge`——「现有条目永远优先、
     冲突只报告不裁决」的语义在那儿，**这里绝不另写一套裁决**。
  3. **唤起**。优先桌面 App，且 **`ok` 是等出来的**（进程存在且活过稳定窗，
     或单实例转发完成；崩了报 `launch_failed` + 信号/日志）。macOS 走
     `open -na <bundle> --args …`——`-n` 让「App 已在跑」时也起一个新实例去
     转发 argv（单实例插件负责），而 spawn 本身交给 launchd：从受限上下文
     直接 exec GUI 二进制会在 AppKit `RegisterApplication` 处 SIGABRT
     （2026-08-20 实测）。Windows / 裸二进制覆盖仍直接 spawn。
     没有桌面 App 才退回浏览器模式。

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
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from . import (
    discover as engine_discover,
    figcapture as engine_figcapture,
    locate as engine_locate,
    registry as engine_registry,
    session_client,
)

#: 认得的产物后缀，与静态扫描同源
OUT_EXTS = engine_discover.OUT_EXTS
#: 向上找 tavotto_registry.json 的层数。再往上就该问用户了——静默把某个上层目录
#: 当成图库，会把一整棵源码树当素材扫一遍。
MAX_PARENTS = 3
#: 桌面 App 路径覆盖（开发态指向 dist/Tavotto，与 TAVOTTO_WORKER_PYTHON 同款惯例）
APP_ENV = "TAVOTTO_DESKTOP_APP"
DEFAULT_PORT = 5089


class HandoffError(RuntimeError):
    """交接无法继续（路径不存在、不是图、注册表损坏）。CLI 转成非零退出码。

    **`code` 是给机器读的，必须稳定**：调用方（Codex 插件、编辑器）要按它
    分诊——「注册表写不进去」该提示改目录权限，「桌面版没装」该提示去下载，
    两件事都塞进一句中文 `error` 里，对面只能做字符串匹配。文案可以随时改，
    code 不行。全部 code 见 `docs/handoff-protocol.md`。

    `extra` 是随失败一起交出去的结构化细节（`--json` 时逐键并入输出）：
    桌面启动失败要带 `app` / `exit_code` / `signal` / `log_path` / `retryable`
    ——调用方拿它们分诊「装坏了」还是「这个环境起不了 GUI」，只给一句中文
    的话对面只能猜。
    """

    def __init__(self, message: str, code: str = "handoff_failed", **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra

    def payload(self) -> dict:
        return {"ok": False, "code": self.code, "error": str(self), **self.extra}


class Target(NamedTuple):
    """交接目标：项目目录 + 要定位的 stem（目录级交接时 stem 为 None）。

    `pick` 是多 Figure 交接的选择信息（脚本的项目相对路径）：一个脚本产出
    多张图、调用方又没有 `--stem` 显式指定时，**绝不静默选第一张**——把
    「选哪张」交给界面的 Figure 选择器（前端 lib/openRequest.ts 消费）。
    stem 与 pick 互斥：定得下来一张就不需要选择器。
    """

    project: str
    stem: str | None
    pick: str | None = None


# --------------------------- 1. 解析目标 ---------------------------------
def _project_root(folder: str, *, isfile) -> str:
    """从 folder 向上找注册表所在层；找不到就是 folder 自己。

    新旧两个文件名都算数（`registry.LEGACY_REGISTRY_NAME`）——只认新名的话，
    改名前建好的图库会被判成「不是项目」，于是在它的上一层另起一个注册表。
    """
    names = (engine_registry.REGISTRY_NAME, engine_registry.LEGACY_REGISTRY_NAME)
    cur = folder
    for _ in range(MAX_PARENTS + 1):
        if any(isfile(os.path.join(cur, n)) for n in names):
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
    """一脚本多产物时优先取磁盘上真的存在的那个（刚跑完的那张图）。

    「stem 的原始产物在磁盘哪里」的判据只有 `figcapture.find_original_artifact`
    一份（捕获描述符判「有没有原件」用的也是它）——这里只是按 stem 顺序问。
    """
    for stem in stems:
        if engine_figcapture.find_original_artifact(project, stem,
                                                    isfile=isfile) is not None:
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
    # 报的是**磁盘上真正在用的那一份**：老图库还叫 mm_registry.json 时报新名，
    # 调用方就拿到一个不存在的路径，而这个字段正是用来告诉用户「去改哪个文件」的。
    on_disk = engine_registry.existing_registry_path(project)
    info: dict = {"registry": str(on_disk or engine_registry.registry_path(project)),
                  "status": "already",
                  "created": False, "added_scripts": [], "added_stems": {},
                  "conflicts": [], "dynamic_names": [], "parameterizable": None}
    if stem is not None and _registered(project, stem):
        info["parameterizable"] = True
        return info

    # 旧名那份也算「已有注册表」：算不上的话每次交接都会判成「首次起草」而无条件
    # 写盘，把用户手写的裁决按新名复制一份、旧名那份留在原地，从此两份各走各的。
    existed = on_disk is not None
    try:
        cfg, rep, changes = engine_discover.merge(project)
    except ValueError as exc:                        # 用户手写的注册表坏了
        # 语法坏（不是合法 JSON）与结构坏（scripts 不是对象、stems 不是字符串
        # 列表、stem 重复登记…）都走这条：code 稳定不变，文案覆盖两种。
        raise HandoffError(f"注册表读不懂，未做任何改动: {exc}",
                           "registry_invalid") from exc
    except OSError as exc:
        raise HandoffError(f"无法读取图库目录 {project}: {exc}",
                           "project_unreadable") from exc

    should_write = (not existed) or changes["added_scripts"] or changes["added_stems"]
    if should_write:
        try:
            # 写出永远是新名——老图库合并一次即完成搬迁。
            info["registry"] = str(engine_discover.write_config(project, cfg))
        except OSError as exc:
            # 只读目录 / 没有写权限 / 磁盘满。以前这条裸 OSError 会一路冒到
            # `tavotto open` 外面变成 traceback，插件那侧只看得到「脚本挂了」。
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


# ------------------- 2b. 脚本目标：safe probe 产品路由 --------------------
# `tavotto open script.py` 是用户的显式运行意图（总纲原则 5）：静态发现
# 解不出图时，安全地把脚本试运行一遍（safe 档：沙盒 cwd、savefig 吞掉捕获、
# 相对路径只读回退），把捕获的 Figure 登记成 RuntimeFigureAsset 再交接。
# `--no-probe` 关掉这一步；`--stem` 在多图时显式选一张。

#: 试运行委托给已运行实例时的 HTTP 超时（秒）。脚本冷启动分钟级，
#: 宁可等也不半途而废——取消语义归界面（那边有取消按钮），CLI 只等结果。
PROBE_HTTP_TIMEOUT = 1800.0

#: probe 稳定错误码 → 交接稳定错误码。缺依赖归 `native_run_required`：
#: safe 档修不了「项目要它自己的环境」，出口是换渲染环境或（PR 2 的）
#: native 运行——code 直接说出口，调用方不用再去翻 params。其余 code
#: 原样透传（probe.ERROR_* 本来就是稳定契约）。
_PROBE_CODE_MAP = {"missing_dependency": "native_run_required"}


def _http_json_status(url: str, payload: dict | None = None,
                      timeout: float = 10.0) -> tuple[int | None, dict | None]:
    """同 `_http_json`，但把 HTTP 状态码与**错误响应体**也交出来。

    probe 端点的 409（probe_in_progress）与 4xx 带着稳定 code——
    `_http_json` 会把它们吞成 None，调用方就只能瞎猜。连不上回 (None, None)。
    """
    headers: dict = {"Content-Type": "application/json"} if payload is not None else {}
    try:
        port = urllib.parse.urlsplit(url).port
        if port:
            headers.update(session_client.auth_headers(port))
    except ValueError:
        pass
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def _script_figures(project: str, script: str) -> list[dict]:
    """脚本当前登记的每张图 → 交接视角的路由描述。

    每条：{stem, artifact, asset_id, cached}。`artifact` 是**这张图自己的**
    磁盘原件（FileAsset 路由）；没有原件的是 RuntimeFigureAsset——`cached`
    表示 materialized cache 就绪（交接过去能直接显示与加画布，零执行）。
    「是不是它自己的产物」按捕获来源判，不按文件名巧合：物化描述符说
    pyplot 捕获的（结构上没有原件），同名磁盘文件是旧样本——把交接路由
    指过去，用户打开的就是陈旧文件（Codex 评审 P1）。判据各自只有一份
    权威：`figcapture.find_original_artifact` / `runtimeasset.load_metadata`
    / `runtimeasset.is_pyplot_capture`。
    """
    from . import runtimeasset as engine_runtimeasset
    try:
        reg = engine_registry.open_registry(project)
    except (FileNotFoundError, RuntimeError):
        return []
    info = reg.entries().get(script)
    if not info:
        return []
    out: list[dict] = []
    for stem in info.get("stems", ()):
        try:
            asset_id = engine_figcapture.runtime_asset_id(script, stem)
        except ValueError:
            continue                  # 注册表坏条目不该炸掉整次交接
        meta = engine_runtimeasset.load_metadata(project, asset_id)
        desc = (meta or {}).get("descriptor") or None
        artifact = engine_figcapture.find_original_artifact(project, stem)
        if artifact is not None \
                and not engine_runtimeasset.is_pyplot_capture(desc):
            out.append({"stem": stem, "artifact": artifact,
                        "asset_id": None, "cached": False})
        else:
            out.append({"stem": stem, "artifact": None,
                        "asset_id": asset_id, "cached": meta is not None})
    return out


def _probe_error(err) -> HandoffError:
    """probe 的结构化错误 → 稳定 code 的 HandoffError（映射见 _PROBE_CODE_MAP）。

    委托给已运行实例时响应形状不归我们管（老版本的 error 是一句字符串）：
    不是 dict 的一律按 script_probe_failed 包起来，绝不让交接自己炸掉。
    """
    if not isinstance(err, dict):
        err = {"code": "script_probe_failed", "message": str(err)}
    code = str(err.get("code") or "script_probe_failed")
    mapped = _PROBE_CODE_MAP.get(code, code)
    extra: dict = {}
    if mapped != code:
        extra["probe_code"] = code
    params = err.get("params") or {}
    if params.get("module"):
        extra["module"] = params["module"]
    tb = err.get("traceback")
    if tb:
        extra["traceback"] = str(tb)[-2000:]
    message = str(err.get("message") or "试运行失败")
    if mapped == "native_run_required":
        message += ("；这个项目可能依赖它自己的 Python 环境。"
                    "先在 Tavotto 设置里选择一个装了所需依赖的渲染环境，"
                    "或等待后续版本按项目原方式运行")
    return HandoffError(message, mapped, **extra)


def _remote_probe(port: int, project: str, script: str, *,
                  http_status=None) -> dict | None:
    """本机已有实例在跑时，把试运行**委托给它**。没有实例回 None。

    委托的意义：同一个 `_PROBES` 并发闸（同脚本并发吃 409）、同一次执行、
    热会话与 materialized cache 都留在那个实例手里——随后的交接零重跑。
    """
    http_status = _http_json_status if http_status is None else http_status
    st, _ = http_status(f"http://127.0.0.1:{port}/api/version", timeout=0.6)
    if st is None:
        return None
    st, opened = http_status(f"http://127.0.0.1:{port}/api/projects/open",
                             {"path": project}, timeout=10.0)
    pj = (opened or {}).get("id")
    if st != 200 or not pj:
        raise HandoffError(
            f"已在运行的 Tavotto 打不开这个项目: "
            f"{(opened or {}).get('error') or f'HTTP {st}'}",
            "remote_open_failed")
    st, resp = http_status(
        f"http://127.0.0.1:{port}/api/registry/probe?pj={quote(pj, safe='')}",
        {"script": script}, timeout=PROBE_HTTP_TIMEOUT)
    if st == 409 and (resp or {}).get("code") == "probe_in_progress":
        # 素材库/另一个调用方已经在跑同一个脚本：如实报，别再起第二次执行
        raise HandoffError(f"该脚本已有一次试运行在进行中: {script}",
                           "probe_in_progress", retryable=True)
    if resp is None or st != 200:
        raise _probe_error(resp if resp and resp.get("code") else
                           {"code": "script_probe_failed",
                            "message": (resp or {}).get("error")
                            or f"试运行请求失败（HTTP {st}）"})
    return resp


def _local_probe(project: str, script: str) -> dict:
    """没有在跑的实例时在本进程里试运行（safe 档，解释器走 pool 探测链）。

    两条纪律：① 物化 cache 只**复制**热 worker 已写好的预览 SVG，绝不为
    物化二次执行；② 无论成败，返回前关掉本进程的 worker 会话——CLI 马上
    就要退出/唤起界面，orphan worker 一个都不许留（交接后的渲染由目标进程
    自己按 lazy 语义重建，registry + cache 都在磁盘上）。
    """
    from . import pool as engine_pool, probe as engine_probe, runtimeasset as engine_runtimeasset
    try:
        result = engine_probe.probe_and_register(project, script)
        if result.get("registered"):
            try:
                worker = engine_pool.get(script, project,
                                         result.get("entry") or "main")
                for desc in result.get("descriptors") or []:
                    if isinstance(desc, dict) and desc.get("stem"):
                        engine_runtimeasset.materialize(
                            project, desc, worker.svg_path(desc["stem"]))
            except engine_pool.WorkerError:
                pass              # cache 是派生物：物化不了只影响首帧占位
        return result
    finally:
        engine_pool.invalidate(script, project)


def resolve_script_route(project: str, script: str, *,
                         stem_arg: str | None = None, no_probe: bool = False,
                         port: int = DEFAULT_PORT,
                         probe_remote=None, probe_local=None) -> tuple[Target, dict]:
    """`.py` 目标的产品路由：复用已有 Figure 路由，否则 safe probe。

    行为顺序（Session 6 契约）：现有注册表/静态发现的每张图都已有有效路由
    （磁盘原件或 materialized runtime cache）就直接复用；否则试运行一次并
    登记 RuntimeFigureAsset。单图直接定位；多图不静默选第一张——`--stem`
    显式选，或把选择信息（`pick`）交给界面的 Figure 选择器。

    返回 (Target, probe_info)。probe_info 进 `--json`：
    {performed, via: remote|local|None, entry, dropped_figures, figures}。
    """
    probe_remote = _remote_probe if probe_remote is None else probe_remote
    probe_local = _local_probe if probe_local is None else probe_local

    figures = _script_figures(project, script)
    routed = bool(figures) and all(f["artifact"] or f["cached"] for f in figures)
    info: dict = {"performed": False, "via": None, "entry": None,
                  "dropped_figures": 0}

    if not routed and not no_probe:
        result = probe_remote(port, project, script)
        via = "remote"
        if result is None:
            result = probe_local(project, script)
            via = "local"
        err = result.get("error")
        if err and not result.get("stems"):
            raise _probe_error(err)
        if result.get("stems") and not result.get("registered"):
            # stem 归属冲突有自己的码（裁决走手工路）；其余「跑出了图却
            # 登记不进去」= 产品拿不到这张图，报成功就是假成功。
            if err and err.get("code") == "multiple_stem_conflict":
                raise _probe_error(err)
            raise HandoffError(
                f"试运行捕获到了图，但没能登记成可打开的素材: {script}",
                "runtime_asset_failed", stems=list(result.get("stems") or []))
        info = {"performed": True, "via": via, "entry": result.get("entry"),
                "dropped_figures": int(result.get("dropped_figures") or 0)}
        figures = _script_figures(project, script)

    if not figures:
        if no_probe:
            raise HandoffError(
                f"静态分析解不出这个脚本会产出哪张图: {script}"
                "（去掉 --no-probe 让 Tavotto 安全地试运行一次，"
                "或在素材库的「脚本」区点「运行并发现图」）",
                "script_no_figure")
        raise HandoffError(
            f"脚本跑通了，但没有捕获到任何 Figure: {script}"
            "（确认它真的创建 matplotlib Figure；出图入口不叫 main 的话，"
            "在素材库的脚本详情里能看到试过哪些入口）",
            "script_no_figure")

    info["figures"] = [dict(f) for f in figures]
    stems = [f["stem"] for f in figures]
    if stem_arg is not None:
        if stem_arg not in stems:
            raise HandoffError(
                f"这个脚本没有产出名为 {stem_arg} 的图"
                f"（有：{', '.join(stems)}）",
                "invalid_stem", stems=stems)
        return Target(project, stem_arg), info
    if len(stems) == 1:
        return Target(project, stems[0]), info
    # 多 Figure：选择权交给界面（不静默选第一张）
    return Target(project, None, pick=script), info


# --------------------------- 3. 唤起界面 ---------------------------------
def desktop_app_candidates(*, system: str | None = None,
                           environ: dict | None = None,
                           isfile=os.path.isfile) -> list[str]:
    """桌面 App 可执行文件的候选路径（按优先级）。

    安装位置的**唯一出处是 `engine/locate.install_roots()`**——同一份清单还要
    给 CLI shim 的发现用（Codex 插件那条链），在这儿再抄一遍就是第二个权威。

    惯例位置**不是全部**：用户会把 `Tavotto.app` 从 `/Applications` 拖到别处、
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
    """桌面壳的交接契约。**与 src-tauri/src/main.rs 的 parse_open_args 严格同源。**

    `--pick-script <脚本相对路径>`：多 Figure 交接的选择信息（Session 6）。
    壳只负责把它原样送进落地 URL 的 `?pick=` / `tavotto:open` 事件，
    选择器在前端。与 `--stem` 互斥（生产侧 Target 已保证）。
    """
    argv = [app, "--open", target.project]
    if target.stem:
        argv += ["--stem", target.stem]
    elif target.pick:
        argv += ["--pick-script", target.pick]
    return argv


#: 桌面启动的就绪判据（全部可 monkeypatch；**不是 sleep，是带限期的轮询**）：
#: READY_TIMEOUT  从唤起到「进程存在且活过 SETTLE」的总限期
#: SETTLE         进程出现后还要活这么久才算「起来了」——启动即崩的进程
#:                在这窗口里就消失了
#: CRASH_WINDOW   直接 spawn 那条路上观察「起来就死」的窗口
#: POLL           轮询间隔
LAUNCH_READY_TIMEOUT = 20.0
LAUNCH_SETTLE = 1.5
LAUNCH_CRASH_WINDOW = 6.0
LAUNCH_POLL = 0.15


def _spawn_detached(argv: list[str], *, spawn=subprocess.Popen):
    """起一个不随本进程生死的界面进程：CLI 交接完就该退出，不当爹。

    返回 spawn 的进程对象（要靠它 `poll()` 出「起来就死」——以前丢掉返回值、
    起了就报成功，SIGABRT 的桌面进程照样拿到 `ok: true`）。
    """
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：控制台关了也不带走窗口
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    return spawn(argv, **kwargs)


def _bundle_root(app_exe: str) -> str | None:
    """macOS：`…/Foo.app/Contents/MacOS/<名字>` → `…/Foo.app`；不是这个形状回 None。

    唤起要交给 LaunchServices（`open`）的是 **bundle**，不是裸二进制。
    """
    parts = app_exe.split("/")
    if len(parts) >= 4 and parts[-2] == "MacOS" and parts[-3] == "Contents" \
            and parts[-4].endswith(".app"):
        return "/".join(parts[:-3]).rstrip("/")
    return None


def sidecar_log_path(*, system: str | None = None, environ: dict | None = None) -> str | None:
    """桌面壳把 sidecar 输出写到哪儿（tauri 的 app_log_dir）。

    启动失败时把它交给用户/调用方——崩溃前 sidecar 的最后几行就在里面。
    推导规则与 tauri v2 同源：macOS `~/Library/Logs/<bundle id>/sidecar.log`，
    Windows `%LOCALAPPDATA%\\<bundle id>\\logs\\sidecar.log`。拿不到就 None。
    """
    from .brand import DESKTOP_BUNDLE_ID
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    if system == "darwin":
        home = (env.get("HOME") or "").rstrip("/")
        if home:
            return f"{home}/Library/Logs/{DESKTOP_BUNDLE_ID}/sidecar.log"
    elif system.startswith("win"):
        base = (env.get("LOCALAPPDATA") or "").rstrip("\\")
        if base:
            return f"{base}\\{DESKTOP_BUNDLE_ID}\\logs\\sidecar.log"
    return None


def _pids_of(exe: str, *, run=subprocess.run) -> "list[int] | None":
    """正在跑这个可执行文件的进程号。查不了（非 POSIX、ps 失败）回 None。

    macOS 上 `ps -axo pid=,comm=` 的 comm 是完整可执行路径——按整条路径比，
    不做子串匹配（`Tavotto` 会撞上别的进程）。
    """
    if os.name != "posix":
        return None
    try:
        proc = run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True,
                   timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    pids: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, comm = line.partition(" ")
        if comm.strip() == exe:
            try:
                pids.append(int(pid_s))
            except ValueError:
                continue
    return pids


# 负 returncode 的编号是 POSIX 语义，解码表就该按 POSIX 编号写死——
# 不能用宿主的 signal 模块：Windows 上 SIGABRT 是 22，Signals(6) 要么解不出
# 要么解成别的名字，同一段代码在两个平台上给出两个答案（CI 实测）。
_POSIX_SIGNALS = {1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL",
                  6: "SIGABRT", 8: "SIGFPE", 9: "SIGKILL", 10: "SIGBUS",
                  11: "SIGSEGV", 13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM"}


def _exit_details(returncode: int) -> dict:
    """Popen 的 returncode → 结构化的 exit_code / signal。

    POSIX 上被信号杀死是负数：`-6` = SIGABRT，按 shell 惯例 exit_code 记 134。
    """
    if returncode < 0:
        name = _POSIX_SIGNALS.get(-returncode, f"signal {-returncode}")
        return {"exit_code": 128 - returncode, "signal": name}
    return {"exit_code": returncode, "signal": None}


def _launch_failed(message: str, app: str, *, code: str = "launch_failed",
                   retryable: bool = False, **extra) -> HandoffError:
    log = sidecar_log_path()
    return HandoffError(message, code, app=app, log_path=log,
                        retryable=retryable, **extra)


def _launch_desktop_via_open(app: str, bundle: str, target: Target, *,
                             run=None, pids_of=None,
                             clock=None, sleep=None) -> dict:
    """macOS：经 LaunchServices（`open -na <bundle> --args …`）唤起。

    为什么不再直接 exec 包内二进制：GUI 进程会继承调用方的执行上下文——从
    受限环境（沙箱里的 shell、没有 Aqua 会话的终端）直接 exec，AppKit 在
    `RegisterApplication` 拿不到 LaunchServices 连接就 abort()（SIGABRT，
    实测见 2026-08-20 的崩溃报告），而且**转发 argv 的第二个实例也一样崩**
    ——NSApplication 初始化在单实例检查之前。`open` 把 spawn 委托给
    launchd，App 落在用户的 GUI 会话里，与调用方的上下文无关。

    `-n` 是关键：App 已在跑时它照样起一个新实例，argv 交给单实例插件转发
    ——`open -a`（不带 `-n`）在那种情况下只会激活窗口，`--args` 根本送不到
    （这正是旧注释里「open 送不到」的那半句；另一半「直接 exec」的代价上面
    说过了）。就绪判据：App 的进程出现且活过 SETTLE——转发场景下老实例本来
    就活着，天然满足；启动即崩的场景下进程出现又消失，如实报 launch_failed。
    """
    import time as _time
    run = subprocess.run if run is None else run
    pids_of = _pids_of if pids_of is None else pids_of
    clock = _time.monotonic if clock is None else clock
    sleep = _time.sleep if sleep is None else sleep

    pre = pids_of(app)
    already = bool(pre)
    # `--args` 之后的参数形状与 desktop_argv 严格同一份（唯一生产者），
    # 在这里手拼第二份迟早漂移——多 Figure 的 --pick-script 就只需改那一处。
    argv = ["open", "-na", bundle, "--args", *desktop_argv(app, target)[1:]]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _launch_failed(f"经 LaunchServices 启动失败: {exc}", app,
                             retryable=True) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:300]
        raise _launch_failed(
            f"LaunchServices 拒绝启动 {bundle}: {err or f'open 退出码 {proc.returncode}'}",
            app, exit_code=proc.returncode)

    start = clock()
    deadline = start + LAUNCH_READY_TIMEOUT
    seen_at: float | None = None
    saw_pid = False
    while clock() < deadline:
        pids = pids_of(app)
        if pids is None:
            # 查不了进程表：只能相信 open 的成功退出码，如实说清就绪没核实
            return {"mode": "desktop", "app": app, "argv": argv, "via": "launchservices",
                    "handoff": "forwarded" if already else "launched",
                    "pid": None, "ready": "unverified",
                    "ready_ms": int((clock() - start) * 1000)}
        if pids:
            saw_pid = True
            now = clock()
            if seen_at is None:
                seen_at = now
            if now - seen_at >= LAUNCH_SETTLE:
                return {"mode": "desktop", "app": app, "argv": argv,
                        "via": "launchservices",
                        "handoff": "forwarded" if already else "launched",
                        "pid": pids[0], "ready": "process_alive",
                        "ready_ms": int((now - start) * 1000)}
        else:
            seen_at = None
        sleep(LAUNCH_POLL)
    if saw_pid:
        raise _launch_failed(
            "Tavotto 桌面进程启动后立即退出（多半是崩溃）。"
            "崩溃报告在 ~/Library/Logs/DiagnosticReports/，sidecar 日志见 log_path。",
            app, signal=None, exit_code=None)
    raise _launch_failed(
        f"Tavotto 桌面进程在 {LAUNCH_READY_TIMEOUT:g}s 内没有出现",
        app, code="launch_timeout", retryable=True)


def _launch_desktop_via_spawn(app: str, target: Target, *,
                              spawn=None, clock=None, sleep=None) -> dict:
    """Windows（以及指到裸二进制、拼不出 bundle 的 macOS 覆盖）：直接 spawn。

    就绪判据（带限期的轮询，不是 sleep）：
      * 进程在观察窗口里退出且退出码非零 → launch_failed（带 exit_code/signal）
      * 进程很快以 0 退出 → 单实例转发给了已在跑的窗口，算成功（forwarded）
      * 进程活过观察窗口 → 算起来了（process_alive）
    """
    import time as _time
    spawn = subprocess.Popen if spawn is None else spawn
    clock = _time.monotonic if clock is None else clock
    sleep = _time.sleep if sleep is None else sleep

    argv = desktop_argv(app, target)
    try:
        proc = _spawn_detached(argv, spawn=spawn)
    except OSError as exc:
        # 文件在、但起不来（权限、被杀软拦、可执行位丢了）。裸 OSError
        # 冒出去只会变成 traceback，调用方分不清「没装」和「起不来」。
        raise _launch_failed(f"Tavotto 桌面应用启动失败 {app}: {exc}", app) from exc
    if proc is None or not hasattr(proc, "poll"):
        # 注入的 spawn 没给进程对象（老测试桩）：保持旧行为，如实说没核实
        return {"mode": "desktop", "app": app, "argv": argv, "via": "spawn",
                "handoff": "launched", "pid": None, "ready": "unverified",
                "ready_ms": 0}

    start = clock()
    while clock() - start < LAUNCH_CRASH_WINDOW:
        rc = proc.poll()
        if rc is None:
            sleep(LAUNCH_POLL)
            continue
        if rc == 0:
            return {"mode": "desktop", "app": app, "argv": argv, "via": "spawn",
                    "handoff": "forwarded", "pid": getattr(proc, "pid", None),
                    "ready": "forwarder_exited",
                    "ready_ms": int((clock() - start) * 1000)}
        details = _exit_details(rc)
        raise _launch_failed(
            "Tavotto 桌面进程在就绪前退出"
            + (f"（信号 {details['signal']}）" if details["signal"]
               else f"（退出码 {details['exit_code']}）"),
            app, **details)
    return {"mode": "desktop", "app": app, "argv": argv, "via": "spawn",
            "handoff": "launched", "pid": getattr(proc, "pid", None),
            "ready": "process_alive",
            "ready_ms": int((clock() - start) * 1000)}


def _http_json(url: str, payload: dict | None = None, timeout: float = 1.0) -> dict | None:
    """本机 API 的极简调用；连不上 / 不是 JSON 一律 None（探测失败不是错误）。

    自动带上本机会话凭据头（session_client.auth_headers）：对面的实例启用了
    会话认证（ADR 0008）时，没有它连 `/api/projects/open` 都是 401；老实例 /
    --insecure-no-auth 的实例没有凭据文件，头为空，行为不变。
    """
    headers: dict = {"Content-Type": "application/json"} if payload is not None else {}
    try:
        port = urllib.parse.urlsplit(url).port
        if port:
            headers.update(session_client.auth_headers(port))
    except ValueError:
        pass
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def browser_url(port: int, target: Target, pj: str | None = None) -> str:
    """浏览器模式的落地地址：`?open=<stem>` / `?pick=<脚本>` 由前端
    lib/openRequest.ts 消费（多 Figure 的选择器走 pick，绝不静默选第一张）。"""
    qs = []
    if pj:
        qs.append("pj=" + quote(pj, safe=""))
    if target.stem:
        qs.append("open=" + quote(target.stem, safe=""))
    elif target.pick:
        qs.append("pick=" + quote(target.pick, safe=""))
    return f"http://127.0.0.1:{port}/" + ("?" + "&".join(qs) if qs else "")


def launch(target: Target, *, prefer: str = "auto", port: int = DEFAULT_PORT,
           system: str | None = None, environ: dict | None = None,
           isfile=os.path.isfile, spawn=None,
           http=_http_json, browse=webbrowser.open,
           run=None, pids_of=None,
           clock=None, sleep=None) -> dict:
    """唤起界面。返回 {"mode": ..., ...}；mode 是给插件看的机器可读值。

    桌面路径**必须等到就绪或失败才返回**：起了就报成功的话，SIGABRT 的桌面
    进程照样拿到 `ok: true`，用户对着一个没出现的窗口等（2026-08-20 实测）。
    """
    if prefer not in ("auto", "desktop", "browser"):
        raise HandoffError(f"未知的唤起方式: {prefer}", "bad_launch_mode")

    if prefer != "browser":
        app = find_desktop_app(system=system, environ=environ, isfile=isfile)
        if app:
            sysname = sys.platform if system is None else system
            bundle = _bundle_root(app) if sysname == "darwin" else None
            if bundle:
                return _launch_desktop_via_open(app, bundle, target, run=run,
                                                pids_of=pids_of, clock=clock,
                                                sleep=sleep)
            return _launch_desktop_via_spawn(app, target, spawn=spawn,
                                             clock=clock, sleep=sleep)
        if prefer == "desktop":
            raise HandoffError(
                "没找到 Tavotto 桌面应用。装一个（GitHub Releases），"
                f"或用 {APP_ENV} 指到它的可执行文件，"
                "或去掉 --desktop 走浏览器模式。", "desktop_missing")

    # 浏览器模式：先问问本机有没有已经在跑的实例——有就让它开这个项目，
    # 绝不再起第二个进程去抢同一个端口（抢不到的那个只会把用户送回旧项目）。
    if http(f"http://127.0.0.1:{port}/api/version", timeout=0.6):
        st = http(f"http://127.0.0.1:{port}/api/projects/open",
                  {"path": target.project}, timeout=10.0) or {}
        if st.get("error"):
            raise HandoffError(f"已在运行的 Tavotto 打不开这个项目: {st['error']}",
                               "remote_open_failed")
        url = browser_url(port, target, st.get("id"))
        # 安全的 token 交接（ADR 0008）：凭本机凭据换一枚一次性 nonce 拼进
        # fragment，新开的标签页才过得了会话认证。换不到（老实例 /
        # --insecure-no-auth）就开裸地址，行为与从前一致。
        nonce = session_client.relaunch_nonce(port)
        browse(url + (f"#dnonce={nonce}" if nonce else ""))
        return {"mode": "browser-existing", "url": url}

    # **冻结产物里没有 `-m tavotto` 这回事**：那时 sys.executable 就是
    # Tavotto 自己（tavotto-cli.exe / Tavotto.exe），拼成
    # `tavotto-cli -m tavotto --figures …` 会在 argparse 里报 unrecognized
    # arguments 当场退出——用户看到的是「点了没反应」。直接给主入口的 flag。
    launcher = ([sys.executable] if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "tavotto"])
    argv = [*launcher, "--figures", target.project, "--port", str(port)]
    if target.stem:
        argv += ["--open-stem", target.stem]
    elif target.pick:
        argv += ["--open-pick", target.pick]
    try:
        _spawn_detached(argv, spawn=subprocess.Popen if spawn is None else spawn)
    except OSError as exc:
        raise HandoffError(f"Tavotto 启动失败: {exc}", "launch_failed") from exc
    return {"mode": "browser-new", "argv": argv, "url": browser_url(port, target)}


# ------------------------------ 编排与 CLI --------------------------------
def _is_script_target(raw: str) -> bool:
    if not raw or not raw.strip():
        return False
    path = os.path.abspath(os.path.expanduser(raw.strip()))
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() == ".py"


def open_target(raw: str, *, prefer: str = "auto", port: int = DEFAULT_PORT,
                launch_ui: bool = True, stem: str | None = None,
                no_probe: bool = False, **kw) -> dict:
    """解析 → 登记 →（脚本目标按需 safe probe）→ 唤起。

    返回一份可直接 json.dumps 的结果。`.py` 目标带 `probe` / `figures` /
    `pick` 三个附加键（路由细节见 resolve_script_route）。
    """
    from .. import __version__  # 版本号唯一出处，别在这儿写死
    target = resolve_target(raw)
    probe_info: dict | None = None
    if _is_script_target(raw):
        path = os.path.abspath(os.path.expanduser(raw.strip()))
        script_rel = os.path.relpath(path, target.project).replace(os.sep, "/")
        # 静态发现先走一遍（草稿/合并注册表——探测路由要读它的现状）
        registry_info = ensure_registered(target.project, None)
        target, probe_info = resolve_script_route(
            target.project, script_rel, stem_arg=stem, no_probe=no_probe,
            port=port)
        if target.pick and not launch_ui:
            # 没有界面接选择器（--no-launch 的机器调用）：必须显式选
            raise HandoffError(
                "这个脚本产出多张图，机器调用必须用 --stem 显式选一张（有："
                f"{', '.join(f['stem'] for f in probe_info['figures'])}）",
                "multiple_figures_found", figures=probe_info["figures"])
        if target.stem is not None:
            registry_info["parameterizable"] = _registered(target.project,
                                                           target.stem)
    else:
        if stem is not None:
            raise HandoffError("--stem 只能与 .py 脚本目标连用", "invalid_stem")
        registry_info = ensure_registered(target.project, target.stem)
    # `version` 是**这次真正干活的那个 Tavotto** 的版本。调用方（Codex 插件）
    # 要拿它比 min_tavotto_version——插件自己的版本与它各有各的升级节奏，
    # 混为一谈会提示用户去升级一个根本没问题的东西。
    result = {"ok": True, "protocol": engine_locate.PROTOCOL_VERSION,
              "version": __version__,
              "project": target.project, "stem": target.stem,
              "registry": registry_info, "launch": None}
    if probe_info is not None:
        result["figures"] = probe_info.get("figures") or []
        result["probe"] = {k: v for k, v in probe_info.items() if k != "figures"}
        result["pick"] = target.pick
    if launch_ui:
        result["launch"] = launch(target, prefer=prefer, port=port, **kw)
    return result


def _report(result: dict) -> None:
    """人类可读输出。插件读的是 --json，这里说给人听。"""
    print(f"* 项目: {result['project']}")
    reg = result["registry"]
    if result["stem"]:
        print(f"* 面板: {result['stem']}")
    probe_info = result.get("probe")
    if probe_info and probe_info.get("performed"):
        n = len(result.get("figures") or [])
        print(f"* 已安全试运行脚本并发现 {n} 张图"
              + (f"（超上限丢弃 {probe_info['dropped_figures']} 张）"
                 if probe_info.get("dropped_figures") else ""))
    if result.get("pick"):
        stems = ", ".join(f["stem"] for f in result.get("figures") or [])
        print(f"* 这个脚本产出多张图（{stems}），已交给界面的选择器")
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
        if launch_info.get("handoff") == "forwarded":
            print("* 已转发给正在运行的 Tavotto 桌面应用")
        else:
            print(f"* Tavotto 桌面应用已启动"
                  f"（{launch_info.get('ready_ms', 0)}ms 就绪）")
    elif mode == "browser-existing":
        print(f"* 已交给正在运行的 Tavotto: {launch_info['url']}")
    else:
        print(f"* 正在启动 Tavotto: {launch_info['url']}")


def cli(argv: list[str]) -> int:
    """`tavotto open` 的入口。返回退出码。"""
    ap = argparse.ArgumentParser(
        prog="tavotto open",
        description="把一张图 / 一个脚本 / 一个图库目录交给 Tavotto 打开")
    ap.add_argument("path", help="产物（.pdf/.png…）、脚本（.py）或图库目录")
    ap.add_argument("--desktop", action="store_true",
                    help="必须用桌面应用，找不到就失败")
    ap.add_argument("--browser", action="store_true", help="强制浏览器模式")
    ap.add_argument("--no-launch", action="store_true",
                    help="只解析与登记，不唤起界面（自检用）")
    ap.add_argument("--no-probe", action="store_true",
                    help="脚本静态解不出产出时也不试运行（只按现有登记打开）")
    ap.add_argument("--stem", default=None,
                    help="脚本产出多张图时显式选哪张（只对 .py 目标有效）")
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
                             launch_ui=not args.no_launch,
                             stem=args.stem, no_probe=args.no_probe)
    except HandoffError as exc:
        # 失败也必须是**机器可解析的一行 JSON**：调用方按 `code` 分诊，
        # 拿不到 JSON 就只能去猜 stderr 里那句中文是什么意思。
        # 桌面启动失败随附 `app` / `exit_code` / `signal` / `log_path` /
        # `retryable`（HandoffError.extra），逐键并入这一行。
        if args.json:
            print(json.dumps({"protocol": engine_locate.PROTOCOL_VERSION,
                              **exc.payload()},
                             ensure_ascii=False))
        else:
            print(f"打不开: {exc}", file=sys.stderr)
            log = exc.extra.get("log_path")
            if log:
                print(f"  日志: {log}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        _report(result)
    return 0
