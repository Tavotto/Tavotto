"""RuntimeFigureAsset：运行时 Figure 素材的解析、materialized cache 与 stale 判定。

ADR 0013 的引擎侧落地（Compatibility Bridge Session 4）。三件事的唯一出处：

* `resolve()` —— 把一个 `runtime:` fileId 解析回 (script, stem, entry)。
  **绝不反解 id**（id 是不透明标识，脚本名里可以有 `#`）：拿注册表里现登记的
  每一对 (script, stem) 重新算一遍 `figcapture.runtime_asset_id` 与之比对，
  匹配即命中。注册表随图库走（tavotto_registry.json），项目拷贝/搬移后
  这条解析链原样成立。
* `materialize()` / `load_metadata()` / `preview_path()` —— materialized
  cache。cache 落 `config.data_dir()` 下（**绝不写进用户图库**），是
  「显示与占位用的派生物，不是用户原件」：可删除、可重建，删掉只影响
  重开时的首帧占位，文档与 override 一个字节不丢。
* `stale_status()` —— stale 判定。**只是提示，不是完备性证明**：判据是
  脚本内容 sha256 + 注册表 entry，脚本读的 CSV / 本地 import / 环境变量
  都不在里面，文案不得声称"数据未变化"。

## cache 布局与原子性

    <data_dir>/cache/runtime/<slug>/
        preview.svg      最近一次成功 build/渲染的预览（显示占位用）
        metadata.json    schema、asset_id、描述符、脚本 sha256、
                         generated_by: "Tavotto"

slug = sha256(规范化项目路径 | asset_id) 截断——只用于目录名去字符集，
身份仍是 (项目, asset_id)。**metadata.json 永远最后写**（两个文件都
tmp + os.replace）：预览写到一半失败时磁盘上没有 metadata，整个 cache
按"不存在"处理——绝不留下一份被当作成功的半成品。metadata 里的
`asset_id` / `preview` 读取时逐项校验，对不上当没有。

## 稳定错误码（app 层的 producer，契约同 probe.ERROR_*）

    runtime_asset_unknown                    fileId 解析不到（注册表里没有）
    runtime_asset_has_no_original_artifact   对 runtime 素材请求 artifact 写回
    runtime_source_writeback_unsupported     对 runtime 素材请求改写脚本源码
                                             （v1 无 producer 端点，先落表；
                                             engine 层 writeback_rejection 是
                                             它的唯一裁决出处）
    runtime_cache_missing                    请求预览但 cache 不存在/不完整

纯标准库；Flask 父进程 import（边界同 registry/pool）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from . import config, figcapture

LOG = logging.getLogger("tavotto.runtimeasset")

RUNTIME_PREFIX = "runtime:"

#: cache 的 schema 版本。布局/字段语义变了才升；读取端只认当前版本，
#: 旧版本目录按"没有 cache"处理（cache 是可重建派生物，迁移 = 重建）。
CACHE_SCHEMA = 1

# ---------------------------------------------------------------------------
# stale 状态（稳定枚举：前后端共用字面量，改语义才改名）
# ---------------------------------------------------------------------------
STALE_FRESH = "fresh"                        # 脚本与执行配置自捕获以来未变
STALE_POSSIBLY = "possibly_stale"            # 脚本或 entry 已变化（数据依赖不追踪）
STALE_MISSING_SOURCE = "missing_source"      # 脚本已不在磁盘上
STALE_MISSING_ENVIRONMENT = "missing_environment"  # 找不到可用渲染解释器
STALE_NEEDS_RERUN = "needs_rerun"            # 无 cache 或未登记：先跑一次才有图
STALE_RERUN_FAILED = "rerun_failed"          # 重新运行失败（producer 在前端：
                                             # runtime 面板渲染 error 态映射到它）

# ---------------------------------------------------------------------------
# 稳定错误码（producer 在 app.py；文案 zh/en 两侧 errors.json backend.*）
# ---------------------------------------------------------------------------
ERROR_UNKNOWN = "runtime_asset_unknown"
ERROR_NO_ARTIFACT = "runtime_asset_has_no_original_artifact"
ERROR_SOURCE_WRITEBACK = "runtime_source_writeback_unsupported"
ERROR_CACHE_MISSING = "runtime_cache_missing"


def is_runtime_id(file_id: object) -> bool:
    """这个面板 fileId 是不是 runtime 素材——**只看前缀，不解析内容**。

    前缀与磁盘相对路径在字面上不可能冲突（ADR 0013 §2：相对路径不含 `:`，
    Windows 盘符不会出现在相对路径里），所以它是合法的判别器；`#` 之后的
    结构则是不透明的，任何消费方都不得据此反解 script/stem。
    """
    return isinstance(file_id, str) and file_id.startswith(RUNTIME_PREFIX)


def resolve(asset_id: str, registry) -> dict | None:
    """asset_id → {script, stem, entry, cost}；注册表里没有 → None。

    正向重算而不是反解：对注册表里每对 (script, stem) 算
    `runtime_asset_id` 与目标比对。注册表是 stem↔script 归属的唯一权威
    （Session 3 的冲突守卫保证同一 stem 不会两属），命中即唯一。
    """
    if not is_runtime_id(asset_id):
        return None
    for script, info in registry.entries().items():
        for stem in info.get("stems", ()):
            try:
                if figcapture.runtime_asset_id(script, stem) == asset_id:
                    return {"script": script, "stem": stem,
                            "entry": info.get("entry", "main"),
                            "cost": info.get("cost", "medium")}
            except ValueError:
                continue          # 注册表里的坏条目不该让整个解析炸掉
    return None


# ---------------------------------------------------------------------------
# materialized cache
# ---------------------------------------------------------------------------
def _norm_project(project_root: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(project_root))))


def cache_dir(project_root: str | Path, asset_id: str) -> Path:
    """该 (项目, asset) 的 cache 目录。slug 只是文件名安全化，不是身份。"""
    slug = hashlib.sha256(
        f"{_norm_project(project_root)}|{asset_id}".encode("utf-8")
    ).hexdigest()[:24]
    return config.data_path("cache", "runtime", slug)


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp + os.replace：任何时刻磁盘上要么是完整旧版、要么是完整新版。"""
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def script_sha256(project_root: str | Path, script: str) -> str | None:
    """脚本当前内容的 sha256；读不到（不存在/无权限）→ None。"""
    try:
        data = (Path(project_root) / script).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def materialize(project_root: str | Path, descriptor: dict,
                svg_source: Path) -> Path | None:
    """把一张捕获 Figure 的预览物化进 cache；返回 cache 目录（失败 None）。

    `descriptor` 是 CapturedFigureDescriptor 的 payload（worker/probe 响应
    原样）；`svg_source` 是 worker out 目录里该 stem 的预览 SVG。写入顺序
    是硬约束：**先 preview 后 metadata**——metadata 落盘即整个 cache 生效，
    它绝不能指向一个还没写完的文件。物化失败只记日志（cache 是派生物，
    失败不该阻断 probe / 渲染本身）。
    """
    asset_id = descriptor.get("asset_id")
    script = descriptor.get("script")
    if not asset_id or not script:
        return None
    try:
        if not svg_source.is_file():
            return None
        target = cache_dir(project_root, asset_id)
        target.mkdir(parents=True, exist_ok=True)
        _atomic_write(target / "preview.svg", svg_source.read_bytes())
        meta = {
            "schema": CACHE_SCHEMA,
            # 这是 Tavotto 生成的派生物，不是用户原件——检视工具与人都
            # 该一眼认出来（cache 硬要求：不冒充 original artifact）
            "generated_by": "Tavotto",
            "asset_id": asset_id,
            "project": _norm_project(project_root),
            "descriptor": dict(descriptor),
            "script_sha256": script_sha256(project_root, script),
            "preview": "preview.svg",
        }
        _atomic_write(target / "metadata.json",
                      json.dumps(meta, ensure_ascii=False, indent=1,
                                 sort_keys=True).encode("utf-8"))
        return target
    except OSError:
        LOG.warning("runtime cache 物化失败: %s", asset_id, exc_info=True)
        return None


def load_metadata(project_root: str | Path, asset_id: str) -> dict | None:
    """读取并校验 cache metadata；任何不一致（schema / id / 预览文件缺失 /
    JSON 坏掉）都按"没有 cache"处理——半成品绝不当成功。"""
    target = cache_dir(project_root, asset_id)
    try:
        meta = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("schema") != CACHE_SCHEMA:
        return None
    if meta.get("asset_id") != asset_id:
        return None
    preview = meta.get("preview")
    if not isinstance(preview, str) or not (target / preview).is_file():
        return None
    return meta


def preview_path(project_root: str | Path, asset_id: str) -> Path | None:
    """cache 里可用的预览文件；没有（或 metadata 不完整）→ None。"""
    meta = load_metadata(project_root, asset_id)
    if meta is None:
        return None
    return cache_dir(project_root, asset_id) / meta["preview"]


def drop_cache(project_root: str | Path, asset_id: str) -> None:
    """删掉该 asset 的 cache（测试与显式清理用）；不存在就什么都不做。"""
    shutil.rmtree(cache_dir(project_root, asset_id), ignore_errors=True)


#: cache 总预算：与引擎会话缓存同一治理思路（超预算按最后使用时间从旧到新删）。
RUNTIME_CACHE_MAX_BYTES = 256 * 1024 * 1024
RUNTIME_CACHE_KEEP = 200


def prune_cache(max_bytes: int = RUNTIME_CACHE_MAX_BYTES,
                keep: int = RUNTIME_CACHE_KEEP) -> int:
    """runtime cache 按预算清理，返回删除的目录数。

    删除永远安全：文档持有的是描述符不是 cache 路径，被删的 asset 下一次
    lazy build / 重新运行时原地重建（`test_runtime_asset.py` 的重建用例）。
    """
    root = config.data_path("cache", "runtime")
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return 0
    items = []
    for p in entries:
        try:
            size = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
            items.append((p.stat().st_mtime, p, size))
        except OSError:
            continue
    items.sort(key=lambda it: it[0])
    total = sum(size for _, _, size in items)
    count = len(items)
    removed = 0
    for _mtime, path, size in items:
        if total <= max_bytes and count <= keep:
            break
        shutil.rmtree(path, ignore_errors=True)
        total -= size
        count -= 1
        removed += 1
    if removed:
        LOG.info("runtime cache 清理: 删除 %d 个目录", removed)
    return removed


# ---------------------------------------------------------------------------
# stale 判定
# ---------------------------------------------------------------------------
def stale_status(project_root: str | Path, asset_id: str, registry,
                 *, source: dict | None = None,
                 worker_python: object = None) -> dict:
    """一个 runtime 素材当前的 stale 状态（诚实边界见模块头）。

    `source` 是文档里持久化的描述块（{script, stem, ...}），注册表解析不到
    时用它兜底——但 **fail closed**：重算出的 asset_id 必须与请求的一致，
    对不上就是 `unknown`（绝不把 override 套到猜出来的脚本上，ADR 0013）。
    `worker_python` 可注入（测试用）；传 callable 时调用它探测。

    返回 {status, script, stem, entry, registered, cached}。
    `script` 一列可能为 None（连描述块都没有的坏请求由 app 层先挡）。
    """
    info = resolve(asset_id, registry)
    registered = info is not None
    if info is None and isinstance(source, dict):
        script = source.get("script")
        stem = source.get("stem")
        try:
            if (isinstance(script, str) and isinstance(stem, str)
                    and figcapture.runtime_asset_id(script, stem) == asset_id):
                info = {"script": script, "stem": stem, "entry": None,
                        "cost": "medium"}
        except ValueError:
            info = None
    if info is None:
        return {"status": None, "script": None, "stem": None, "entry": None,
                "registered": False, "cached": False}

    meta = load_metadata(project_root, asset_id)
    out = {"script": info["script"], "stem": info["stem"],
           "entry": info["entry"], "registered": registered,
           "cached": meta is not None}

    if not (Path(project_root) / info["script"]).is_file():
        return {**out, "status": STALE_MISSING_SOURCE}
    if callable(worker_python):
        try:
            worker_python = worker_python()
        except Exception:                       # 探测失败 = 没有可用环境
            worker_python = None
    if worker_python is None:
        return {**out, "status": STALE_MISSING_ENVIRONMENT}
    if not registered or meta is None:
        # 未登记（注册表条目被删/项目重建）或没有物化过：都要先跑一次
        return {**out, "status": STALE_NEEDS_RERUN}
    current = script_sha256(project_root, info["script"])
    cached_desc = meta.get("descriptor") or {}
    if (current is None or current != meta.get("script_sha256")
            or cached_desc.get("entry") != info["entry"]):
        return {**out, "status": STALE_POSSIBLY}
    return {**out, "status": STALE_FRESH}


# ---------------------------------------------------------------------------
# writeback 裁决（唯一出处；app 层按它回稳定错误码）
# ---------------------------------------------------------------------------
def writeback_rejection(kind: str) -> str:
    """runtime 素材的 writeback 请求 → 该回哪个稳定错误码。

    v1 全拒：artifact 写回没有目标（pyplot 捕获压根没有原件；savefig 捕获
    且原件在磁盘上的那些，写回走它的 FileAsset 身份——那条路的事务防线一条
    不少），source 写回（改写用户脚本）整个不做（ADR 0013 §7 / Pylustrator
    研究"保存即改源码：不吸收"）。后端**硬拒绝**，不是藏按钮。
    """
    if kind == "source":
        return ERROR_SOURCE_WRITEBACK
    return ERROR_NO_ARTIFACT
