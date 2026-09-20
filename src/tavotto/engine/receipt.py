"""ExecutionReceipt —— 「这次到底是谁、在哪、用什么跑的」（统一实施包 U01，ADR 0053）。

04_ARCHITECTURE §2 那一行：*worker 自报 + 控制面关联*。两半来源不同，缺一半就
是半张回执：

* **worker 自报**（`figsession.runtime_report()`，随 v1 build 响应的 `runtime`
  字段回来）：`sys.executable` / `sys.prefix` / `sys.base_prefix` / 关键包版本 /
  脚本真正看到的 `cwd`。这是执行侧**此刻量到的**，父进程从路径字符串上猜不出
  （`.venv/bin/python` 是软链，realpath 会把 venv 与基础解释器判成同一个）。
* **控制面关联**（`pool.EngineWorker` / `WorkerdWorker` 上早就有的账本）：
  `generation`（哪一代会话）、`script_sha1`（spawn 那一刻的脚本内容 = source
  revision）、`python_source`（解释器来源标签）、ExecutionSpec 的稳定字段与
  LaunchContext。

## 身份三分（04 §3）——三个方法，三个问题

| 方法 | 回答 | 含机器路径？ |
|---|---|---|
| `private_invalidation_key()` | 「还是不是同一个环境」——缓存 / 会话失效用 | **含**（executable / prefix / 项目根 / cwd）：区分两个 venv 正靠它们 |
| `public_identity()` | 「这是哪种执行」——可写进文档 / 报告 / 跨机器比对 | 不含；只有规范化意图 + 获准来源标签 + 版本号 |
| （不在这里）最终文件 hash | 「文件长什么样」 | 在 `figcapture.SourceArtifact.bytes_sha256`，**不回写进回执** |

`receipt_id` 是这一次执行实例的不透明 id（由公开身份 + 私有键 + generation 派生）：
同一环境同一脚本重建一代就换一个 id；两个项目里的同名脚本永远不同 id（FO-008）。

`completeness` 只有两档：worker 自报到了是 `complete`，老 worker / native 早期版本
没带 `runtime` 是 `partial`——**partial 不是错误**，但它必须显式写出来，读回执的人
才知道 prefix 那一栏是「没量」而不是「量到了空」。

纯标准库；Flask 父进程 import 链上（与 `execspec` / `pool` 同边界）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from . import execspec, figcapture

#: 回执形态的版本。加可选字段不升；改语义 / 删字段才升。
RECEIPT_VERSION = 1

COMPLETENESS_COMPLETE = "complete"
COMPLETENESS_PARTIAL = "partial"
COMPLETENESS = (COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL)

CONTROL_PLANE_PYTHON = "python_pool"
CONTROL_PLANE_WORKERD = "workerd"
CONTROL_PLANE_NATIVE = "native"
CONTROL_PLANES = (CONTROL_PLANE_PYTHON, CONTROL_PLANE_WORKERD, CONTROL_PLANE_NATIVE)


def _canon(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class ExecutionReceipt:
    """一次执行的回执（不可变、可 JSON 化）。字段分三组，见模块头。"""

    profile: str  # PROFILE_SAFE | PROFILE_NATIVE
    control_plane: str  # CONTROL_PLANES 之一
    python_source: str  # pool 的来源标签（native 恒空串：解释器是用户的）
    generation: int
    source_revision: str  # spawn 那一刻的脚本 sha1（读不到是空串）
    spec_stable: dict  # ExecutionSpec.stable_payload()
    launch_context: dict  # execspec.launch_context(spec, grant=…)
    interpreter: str  # 机器路径（只进私有键）
    project_root: str  # 机器路径（只进私有键）
    runtime: dict | None  # worker 自报；None = 没报（completeness=partial）
    descriptors: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if self.profile not in execspec.PROFILES:
            raise ValueError(f"profile 非法: {self.profile!r}")
        if self.control_plane not in CONTROL_PLANES:
            raise ValueError(f"control_plane 非法: {self.control_plane!r}（可选 {CONTROL_PLANES}）")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool):
            raise ValueError("generation 必须是整数")
        if self.generation < 1:
            raise ValueError("generation 从 1 起：0 或负数说明没起过会话")
        if not isinstance(self.spec_stable, dict) or not isinstance(self.launch_context, dict):
            raise ValueError("spec_stable / launch_context 必须是对象")
        if self.runtime is not None and not isinstance(self.runtime, dict):
            raise ValueError("runtime 必须是 None 或对象")
        if not isinstance(self.descriptors, tuple):
            raise ValueError("descriptors 必须是元组")

    # ---------------- 完备性 ----------------
    @property
    def completeness(self) -> str:
        return COMPLETENESS_COMPLETE if self.runtime else COMPLETENESS_PARTIAL

    # ---------------- 身份三分 ----------------
    def public_identity(self) -> str:
        """公开语义身份：规范化意图 + 获准来源标签 + 版本号。**不含任何路径。**"""
        rt = self.runtime or {}
        payload = {
            "receipt_version": RECEIPT_VERSION,
            "profile": self.profile,
            "control_plane": self.control_plane,
            "python_source": self.python_source,
            "spec": self.spec_stable,
            "launch_context": {
                k: v for k, v in self.launch_context.items() if k != "grant"
            },  # grant 记的是授予时刻，不是意图
            "source_revision": self.source_revision,
            "python_version": rt.get("python_version"),
            "python_implementation": rt.get("python_implementation"),
            "platform": rt.get("platform"),
            "machine": rt.get("machine"),
            "packages": rt.get("packages") or {},
            "completeness": self.completeness,
        }
        return _sha(payload)

    def private_invalidation_key(self) -> str:
        """私有失效键：**含**机器路径与 prefix——区分两个 venv 靠的就是它们。"""
        rt = self.runtime or {}
        payload = {
            "public": self.public_identity(),
            "interpreter": self.interpreter,
            "project_root": self.project_root,
            "executable": rt.get("executable"),
            "prefix": rt.get("prefix"),
            "base_prefix": rt.get("base_prefix"),
            "cwd": rt.get("cwd"),
        }
        return _sha(payload)

    @property
    def receipt_id(self) -> str:
        """这一次执行实例的不透明 id（公开身份 + 私有键 + generation）。"""
        return _sha(
            {
                "public": self.public_identity(),
                "private": self.private_invalidation_key(),
                "generation": self.generation,
            }
        )

    # ---------------- 序列化 ----------------
    def to_payload(self, *, include_private: bool = False) -> dict:
        """默认**不带**机器路径（给 HTTP / MCP 投影）；`include_private=True` 给诊断包。

        `runtime` 自报里的 executable / prefix / cwd 也是机器路径：默认投影只留
        版本类字段，私有那几项归 `include_private`。
        """
        rt = self.runtime
        if rt is not None and not include_private:
            rt = {
                k: v
                for k, v in rt.items()
                if k not in ("executable", "prefix", "base_prefix", "cwd", "argv0")
            }
        out = {
            "receipt_version": RECEIPT_VERSION,
            "receipt_id": self.receipt_id,
            "public_identity": self.public_identity(),
            "completeness": self.completeness,
            "profile": self.profile,
            "control_plane": self.control_plane,
            "python_source": self.python_source,
            "generation": self.generation,
            "source_revision": self.source_revision,
            "spec": dict(self.spec_stable),
            "launch_context": dict(self.launch_context),
            "runtime": rt,
            "descriptors": [dict(d) for d in self.descriptors],
        }
        if include_private:
            out["private_invalidation_key"] = self.private_invalidation_key()
            out["interpreter"] = self.interpreter
            out["project_root"] = self.project_root
        return out


def from_worker(
    worker, build_resp: dict, *, control_plane: str, grant: dict | None
) -> ExecutionReceipt:
    """`pool` 的两种 worker + 它们的 build 响应 → 回执。

    读的全是 worker 上**已经存在**的账本字段（`spec` / `generation` / `script_sha1` /
    `python_source` / `python` / `figures_dir`）；`runtime` 与 `descriptors` 来自
    build 响应。老 worker 没带 `runtime` → `partial`，不补、不猜。
    """
    spec = worker.spec
    runtime = build_resp.get("runtime") if isinstance(build_resp, dict) else None
    if runtime is not None and not isinstance(runtime, dict):
        runtime = None
    descriptors = (
        tuple(d for d in (build_resp.get("descriptors") or []) if isinstance(d, dict))
        if isinstance(build_resp, dict)
        else ()
    )
    return ExecutionReceipt(
        profile=spec.profile,
        control_plane=control_plane,
        python_source=str(getattr(worker, "python_source", "") or ""),
        generation=int(worker.generation),
        source_revision=str(getattr(worker, "script_sha1", "") or ""),
        spec_stable=spec.stable_payload(),
        launch_context=execspec.launch_context(spec, grant=grant),
        interpreter=spec.interpreter,
        project_root=spec.project_root,
        runtime=dict(runtime) if runtime else None,
        descriptors=descriptors,
    )


def source_artifact_for(
    receipt: ExecutionReceipt, path, *, source_id: str, patch_hash: str | None
) -> figcapture.SourceArtifact:
    """回执 + 它写出来的文件 → SourceArtifact（execution 来源）。"""
    return figcapture.source_artifact_from_file(
        path,
        source_id=source_id,
        origin=figcapture.ORIGIN_EXECUTION,
        receipt_id=receipt.receipt_id,
        generation=receipt.generation,
        patch_hash=patch_hash,
    )
