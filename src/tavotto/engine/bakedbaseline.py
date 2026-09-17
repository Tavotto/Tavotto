"""「更新原图」时烙进文件的 override 基线：每个项目一份文件的读取、追加与有效性判定。

2026-09-17 审计任务书 PR E 从 `app.py` 提出来的第一条业务链。这里**不知道 Flask、不知道
「当前项目」是谁**：根目录、项目 id、旧文件迁移时「哪些 stem 是本项目的」判据（注册表）
都由调用方显式递进来。`app.py` 里的 `load_baked` / `append_baked` 是它按默认项目取值的
薄包装（迁移期保留，好让老调用方式与测试不动）。纯标准库，Flask 父进程 import 链上。

数据形状：`{stem: {"versions": [{"ts", "patches", "patch_hash"?, "files"?}, …]}}`，末位 =
当前基线。历史上有过两种旧形状——全局一个文件按 stem 索引（两个图库里都有的 Fig1 会互相
覆盖基线，新拖入的面板继承了另一个项目的 override），与单版本 `{stem: {"patches", …}}`
——两种都只在读取端兼容，写出永远是新形状。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import atomicio, patchspec

LOG = logging.getLogger("tavotto")

#: 每个 stem 保留的版本条数（末位是当前基线，前面是「更新原图」的足迹）。
KEEP_VERSIONS = 50

#: 旧 baked 条目（没记文件身份）退回「mtime 是否晚于写回时刻」判基线失效时的
#: 宽限：commit 与 append 在同一次请求里、间隔秒级，120s 足够宽；
#: 更宽只会把「写回后马上被外部重写」误判成仍有效。
TS_GRACE_S = 120

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def sha1_of(path: Path) -> str:
    """文件内容的 sha1（分块读，不整份进内存）。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class BakedBaselineStore:
    """`<root>/<项目 id>.json` 的读-改-写；`legacy_path` 是旧的全局文件，只读迁移源。

    `lock` 可由调用方给（app 里一把模块级 RLock 同时护着迁移与读-改-写）；不给就自建。
    可重入：`append` 持锁后还会调 `load`。
    """

    def __init__(
        self,
        root: Path,
        legacy_path: Path | None = None,
        *,
        lock: threading.RLock | None = None,
    ) -> None:
        self.root = Path(root)
        self.legacy_path = Path(legacy_path) if legacy_path is not None else None
        self._lock = lock if lock is not None else threading.RLock()

    def path_for(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def _write(self, path: Path, data: dict) -> None:
        """原子落盘（唯一实现见 engine/atomicio.py）。"""
        atomicio.write_json(path, data, indent=1)

    def _migrate_legacy(self, path: Path, stem_known: Callable[[str], bool]) -> None:
        """旧的全局文件 → 本项目的分键文件（只读迁移，一次性）。

        按 `stem_known` 过滤：只有本项目认得的 stem 才搬过来，别的项目的同名 Fig1 留在
        旧文件里等它自己迁移（所以**不删旧文件**）。迁完就写盘——哪怕一条都没搬
        也要写出空 dict，否则每次读都要再翻一遍旧文件，而且「本项目确实没有基线」
        与「还没迁移」这两种状态分不开。
        """
        legacy: dict = {}
        if self.legacy_path is not None:
            try:
                legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                legacy = {}
        mine = {stem: v for stem, v in legacy.items() if isinstance(v, dict) and stem_known(stem)}
        try:
            self._write(path, mine)
        except OSError:  # 写不进去（只读介质）：这次照旧读旧文件，不拦渲染
            LOG.warning("baked 基线迁移写盘失败: %s", path, exc_info=True)
            return
        if mine:
            LOG.info("baked 基线迁移: %d 个 stem → %s", len(mine), path.name)

    def load(self, project_id: str, *, stem_known: Callable[[str], bool]) -> dict:
        """本项目的 `{stem: {"versions": [...]}}`；读不出来回空 dict。"""
        with self._lock:
            path = self.path_for(project_id)
            if not path.exists():
                self._migrate_legacy(path, stem_known)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        for stem, v in list(data.items()):  # 迁移单版本旧格式
            if "patches" in v:
                data[stem] = {
                    "versions": [{"ts": v.get("updated_at", ""), "patches": v["patches"]}]
                }
        return data

    def append(
        self,
        project_id: str,
        stem: str,
        patches: list,
        *,
        stem_known: Callable[[str], bool],
        files: dict | None = None,
    ) -> None:
        """读-改-写全程持锁（同一项目内），落盘走原子替换。

        每条版本都带 `patch_hash`（`patchspec` 的权威实现）：写回响应里回的是同一个
        值，用户/排障时能把「磁盘上这张图」与「哪一版 patches」对上。旧条目没有这个
        键，读取端一律按缺失兼容。

        `files` 是写回 commit 之后各目标文件的身份（`{名字: {sha1, mtime_ns, size}}`）：
        「磁盘文件已经是这个基线的样子」这句话只在文件身份没变时成立，
        `baseline_matches_file` 靠它判基线失效。
        """
        with self._lock:
            data = self.load(project_id, stem_known=stem_known)
            entry = data.setdefault(stem, {"versions": []})
            version: dict = {
                "ts": time.strftime(TS_FORMAT),
                "patches": patches,
                "patch_hash": patchspec.patch_hash(patches),
            }
            if files:
                version["files"] = files
            entry["versions"].append(version)
            entry["versions"] = entry["versions"][-KEEP_VERSIONS:]
            self._write(self.path_for(project_id), data)


def baseline_version(stem: str, baked: dict) -> dict | None:
    """baked 表由调用方传进来——它曾经是个模块级缓存，多项目下那就是
    「A 项目扫一遍素材，B 项目的基线全被换掉」。"""
    versions = (baked.get(stem) or {}).get("versions") or []
    return versions[-1] if versions else None


def baseline_patches(stem: str, baked: dict) -> list:
    version = baseline_version(stem, baked)
    return version["patches"] if version else []


def baseline_matches_file(version: dict, path: Path) -> bool:
    """写回基线是否**仍烙在这份磁盘文件上**——基线有效性判据的唯一出处。

    用户在 Tavotto 之外重跑自己的构建脚本会把产物刷回脚本原值，此后
    「文件已是基线那个样子」静默失效：预览挂磁盘原图（脚本原值）、编辑态
    显示 script+overrides，两者永久分叉且互不报错。所以基线必须绑定文件身份：

    - `size` 不同 → 失效（不读内容）；
    - `mtime_ns` 相同 → 有效（常态路径，零额外 IO）；
    - mtime 变了但 size 相同 → 读一次 sha1 定夺——touch / 网盘同步会换 mtime
      不换内容，误判失效的代价是 heavy 脚本白跑几分钟。

    旧条目没有 `files`：退回「文件 mtime 是否晚于写回时刻 + 宽限」的保守判据；
    `ts` 解析不动就维持旧行为（当作有效）——不拿猜出来的结论触发 heavy 重渲染。
    """
    try:
        st = path.stat()
    except OSError:
        return False
    files = version.get("files")
    if isinstance(files, dict) and isinstance(files.get(path.name), dict):
        ident = files[path.name]
        if ident.get("size") != st.st_size:
            return False
        if ident.get("mtime_ns") == st.st_mtime_ns:
            return True
        try:
            return sha1_of(path) == ident.get("sha1")
        except OSError:
            return False
    ts = str(version.get("ts") or "")
    try:
        baked_at = time.mktime(time.strptime(ts, TS_FORMAT))
    except (ValueError, OverflowError):
        return True
    return st.st_mtime <= baked_at + TS_GRACE_S
