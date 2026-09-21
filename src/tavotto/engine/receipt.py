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

## U09（ADR 0070）：回执只收「这一条会话」的自报，观察到什么就记什么

* **自报必须来自跑了脚本的那个进程**：`runtime["report_origin"] == "build"` 且 `runtime["pid"]` 等于控制面
  自己起的那个子进程（`worker.child_pid`；控制面不知道 pid 时那一维不核、如实记 `pid_check=unavailable`）。
  体检（`projectenv.probe_environment`）/ 探针 / 控制面自己拼的字典**一律拒收**：`runtime=None`、
  `runtime_rejected=<原因>`、`completeness=partial`——拿预检结果冒充本次事实是 FO-059 的 must_fail。
* **已观察的输入**（`runtime["inputs"]`，`figcapture.InputObserver.report()`）：脚本经 Python `open` 只读打开的
  项目内文件（相对路径 + sha256）与 import 到的本地模块；`observation=partial` 永远如实——原生 I/O / 网络 /
  子进程看不见（D13 / FO-061）。观察到的文件身份**进公开语义身份**（数据换了就是另一次执行），机器路径不进。
* **数据绑定**（`binding`，控制面给）：准备计划在预检那一刻按静态证据记下的「打算读哪些文件、内容是什么」
  （`databinding.binding_for()`），回执把它与观察到的输入逐条对：`binding_check()` 回 `matched ∈ {True, False,
  None}`——None = 一条都没观察到（原生读），不冒充核过。

纯标准库；Flask 父进程 import 链上（与 `execspec` / `pool` 同边界）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os

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

#: 自报被拒的原因（闭集；ADR 0070）。`not_a_build_report` = 没有 `report_origin=build`（体检 / 探针 / 手拼的字典）；
#: `pid_mismatch` = 报的 pid 不是控制面自己起的那个子进程。
RUNTIME_REJECTED_NOT_BUILD = "not_a_build_report"
RUNTIME_REJECTED_PID = "pid_mismatch"
RUNTIME_REJECTIONS = (RUNTIME_REJECTED_NOT_BUILD, RUNTIME_REJECTED_PID)
#: 自报里必须是 `build` 的那个字面量（与 `figsession.REPORT_ORIGIN_BUILD` 同源；worker 侧平铺 import 不到本模块，
#: `tests/test_execution_receipt.py` 钉两边相等）。
REPORT_ORIGIN_BUILD = "build"

PID_CHECK_OK = "ok"
PID_CHECK_UNAVAILABLE = "unavailable"


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
    #: 自报被拒的原因（`RUNTIME_REJECTIONS` 之一）；None = 没拒（没报或收下了）。
    runtime_rejected: str | None = None
    #: pid 核对：`ok` / `unavailable`（控制面不知道子进程 pid，那一维没核）；拒了的回执这里是 None。
    pid_check: str | None = None
    #: 控制面在预检那一刻记下的数据绑定（`databinding.binding_for()` 的载荷）；None = 没给。
    binding: dict | None = None

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
        if self.runtime_rejected is not None and self.runtime_rejected not in RUNTIME_REJECTIONS:
            raise ValueError(f"runtime_rejected 非法: {self.runtime_rejected!r}")
        if self.runtime_rejected is not None and self.runtime is not None:
            raise ValueError("被拒的自报不能同时留在 runtime 里")
        if self.binding is not None and not isinstance(self.binding, dict):
            raise ValueError("binding 必须是 None 或对象")

    # ---------------- 完备性 ----------------
    @property
    def completeness(self) -> str:
        return COMPLETENESS_COMPLETE if self.runtime else COMPLETENESS_PARTIAL

    # ---------------- 输入观察与数据绑定（U09） ----------------
    @property
    def inputs(self) -> dict | None:
        """worker 自报的输入观察（`figcapture.InputObserver.report()`）；没报 / 老 worker 是 None。"""
        rt = self.runtime or {}
        ins = rt.get("inputs")
        return dict(ins) if isinstance(ins, dict) else None

    def observed_files(self) -> dict[str, str | None]:
        """观察到的项目内文件：相对 POSIX 路径 → sha256（大文件没算 hash 是 None）。"""
        out: dict[str, str | None] = {}
        for f in (self.inputs or {}).get("files") or []:
            if isinstance(f, dict) and isinstance(f.get("path"), str):
                out[f["path"]] = f.get("sha256") if isinstance(f.get("sha256"), str) else None
        return out

    def binding_check(self) -> dict:
        """计划记下的数据绑定 vs 观察到的输入：逐条对 sha256。

        `matched`：True = 每个预期文件里被观察到的那些全部一致；False = 至少一条不一致（数据在预检之后变了，
        或读到的是另一份）；None = 一条预期文件都没被观察到（原生读 / 没有绑定 / 没有自报）——**不冒充核过**。
        """
        expected = (self.binding or {}).get("expected") or {}
        observed = self.observed_files()
        changed, same, unobserved = [], [], []
        for rel, want in expected.items():
            if rel not in observed:
                unobserved.append(rel)
                continue
            got = observed[rel]
            if got is None:
                unobserved.append(rel)
            elif got == want:
                same.append(rel)
            else:
                changed.append(rel)
        matched: bool | None
        if changed:
            matched = False
        elif same:
            matched = True
        else:
            matched = None
        return {
            "revision": (self.binding or {}).get("revision"),
            "matched": matched,
            "same": sorted(same),
            "changed": sorted(changed),
            "unobserved": sorted(unobserved),
            "observation": (self.inputs or {}).get("observation"),
        }

    # ---------------- 身份三分 ----------------
    def public_identity(self) -> str:
        """公开语义身份：规范化意图 + 获准来源标签 + 版本号 + 观察到的输入身份（相对路径 + hash）。
        **不含任何机器路径。**"""
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
            # 数据身份：换一份数据就是另一次执行（同名干扰 / 预检后被改都在这一维上分开）
            "inputs": sorted(self.observed_files().items()),
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

    def public_facts(self) -> dict:
        """给 ArtifactManifest 的那一小段（U09）：可公开的事实，**没有路径、没有 argv、没有 stem**——
        身份 / 完备性 / generation / source revision / 解释器版本 / 关键包版本 / 数据绑定核对 / 观察的完备性。"""
        rt = self.runtime or {}
        check = self.binding_check()
        return {
            "receipt_identity": self.public_identity(),
            "receipt_id": self.receipt_id,
            "completeness": self.completeness,
            "runtime_rejected": self.runtime_rejected,
            "pid_check": self.pid_check,
            "control_plane": self.control_plane,
            "python_source": self.python_source,
            "generation": self.generation,
            "source_revision": self.source_revision,
            "python_version": rt.get("python_version"),
            "python_implementation": rt.get("python_implementation"),
            "platform": rt.get("platform"),
            "machine": rt.get("machine"),
            "packages": dict(rt.get("packages") or {}),
            "cwd_origin": self.launch_context.get("cwd_origin"),
            "observation": (self.inputs or {}).get("observation"),
            "binding": {
                "revision": check["revision"],
                "matched": check["matched"],
                "changed": len(check["changed"]),
                "unobserved": len(check["unobserved"]),
            },
        }

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
            "runtime_rejected": self.runtime_rejected,
            "pid_check": self.pid_check,
            "inputs": self.inputs,
            "binding": dict(self.binding) if self.binding is not None else None,
            "binding_check": self.binding_check(),
            "descriptors": [dict(d) for d in self.descriptors],
        }
        if include_private:
            out["private_invalidation_key"] = self.private_invalidation_key()
            out["interpreter"] = self.interpreter
            out["project_root"] = self.project_root
        return out


def accept_runtime(
    runtime, *, expected_pid: int | None
) -> tuple[dict | None, str | None, str | None]:
    """自报收不收：`(runtime, rejected, pid_check)`。

    收的条件（ADR 0070）：是对象、`report_origin == build`、报了 pid 且等于控制面自己起的那个子进程；
    控制面不知道 pid（老 workerd）时那一维不核，`pid_check=unavailable`。不是对象 / 没报 → `(None, None, None)`
    ——那是「没报」（老 worker），不是「拒了」。
    """
    if not isinstance(runtime, dict) or not runtime:
        return None, None, None
    if runtime.get("report_origin") != REPORT_ORIGIN_BUILD:
        return None, RUNTIME_REJECTED_NOT_BUILD, None
    reported = runtime.get("pid")
    if expected_pid is None:
        return dict(runtime), None, PID_CHECK_UNAVAILABLE
    if not isinstance(reported, int) or isinstance(reported, bool) or reported != expected_pid:
        return None, RUNTIME_REJECTED_PID, None
    return dict(runtime), None, PID_CHECK_OK


def from_worker(
    worker,
    build_resp: dict,
    *,
    control_plane: str,
    grant: dict | None,
    binding: dict | None = None,
) -> ExecutionReceipt:
    """`pool` 的两种 worker + 它们的 build 响应 → 回执。

    读的全是 worker 上**已经存在**的账本字段（`spec` / `generation` / `script_sha1` /
    `python_source` / `python` / `figures_dir`）；`runtime` 与 `descriptors` 来自
    build 响应。老 worker 没带 `runtime` → `partial`，不补、不猜；带了但不是这一条会话跑脚本的那个
    进程报的（体检 / 探针 / 手拼）→ 拒收（`runtime_rejected`），同样 `partial`（ADR 0070）。
    `binding` 是控制面在预检那一刻记下的数据绑定（`databinding.binding_for()`）。
    """
    spec = worker.spec
    raw = build_resp.get("runtime") if isinstance(build_resp, dict) else None
    runtime, rejected, pid_check = accept_runtime(raw, expected_pid=worker_pid(worker))
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
        runtime=runtime,
        descriptors=descriptors,
        runtime_rejected=rejected,
        pid_check=pid_check,
        binding=dict(binding) if isinstance(binding, dict) else None,
    )


def worker_pid(worker) -> int | None:
    """控制面自己起的那个子进程的 pid（`EngineWorker.child_pid` / `WorkerdWorker.child_pid`）；不知道就 None。"""
    pid = getattr(worker, "child_pid", None)
    return int(pid) if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


#: native 会话不记 argv 的**值**（ADR 0021 §4：里面可能有路径 / 样本名 / 凭据），只记数量；
#: 回执的 spec 里每个位置放这个占位串——数量进身份，值不进。
NATIVE_ARGV_PLACEHOLDER = "<argv>"


def from_native_session(session, script: str, *, grant: dict | None = None) -> ExecutionReceipt:
    """`nativesession.NativeSession` → 回执（U08，ADR 0067：执行侧源解析器对 native 面板也要回执）。

    native 会话没有 `spec` 账本，spec 由描述符元数据**重建**（`execspec.native_spec`：解释器 / cwd /
    项目根 / 目标种类 / argv 数量）；`source_revision` 是空串——会话不记 spawn 那一刻的脚本 sha1，
    此刻再去读文件量到的是「现在」，不是「当时」，不补不猜。`runtime` 是 bridge 自报的那一半
    （`last_build_runtime`），没报就是 `partial`。
    """
    from . import execspec

    kind = str(getattr(session, "target_kind", "") or execspec.TARGET_SCRIPT)
    root = str(getattr(session, "project_root", "") or "")
    raw_target = script if kind == execspec.TARGET_MODULE else os.path.join(root, script)
    spec = execspec.native_spec(
        raw_target,
        interpreter=str(getattr(session, "interpreter", "") or ""),
        cwd=str(getattr(session, "cwd", "") or ""),
        project_root=root,
        target_kind=kind,
        argv=(NATIVE_ARGV_PLACEHOLDER,) * int(getattr(session, "arg_count", 0) or 0),
    )
    # native 的 pid 是用户进程自己的（bridge 附着时记下的 `pid`，没记就不核）
    runtime, rejected, pid_check = accept_runtime(
        getattr(session, "last_build_runtime", None), expected_pid=worker_pid(session)
    )
    descriptors = tuple(
        d for d in (getattr(session, "descriptors", None) or []) if isinstance(d, dict)
    )
    return ExecutionReceipt(
        profile=execspec.PROFILE_NATIVE,
        control_plane=CONTROL_PLANE_NATIVE,
        python_source="",
        generation=int(getattr(session, "generation", 1) or 1),
        source_revision="",
        spec_stable=spec.stable_payload(),
        launch_context=execspec.launch_context(spec, grant=grant),
        interpreter=spec.interpreter,
        project_root=spec.project_root,
        runtime=runtime,
        descriptors=descriptors,
        runtime_rejected=rejected,
        pid_check=pid_check,
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
        receipt_identity=receipt.public_identity(),
    )
