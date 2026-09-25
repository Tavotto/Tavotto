"""worker 协议 v1 的**信封语义**——两条执行入口共用的一份实现。

完整契约在 `docs/adr/0003-worker-protocol-v1.md`；本模块是它在 Python 侧的
唯一实现，`workerd/`（Rust supervisor）是另一侧的镜像。

## 为什么把它从 worker.py 拆出来

native bridge（ADR 0020）换掉的是**传输**（stdin/stdout → loopback socket）
和**执行方式**（Tavotto 挑解释器跑 → 用户自己的进程跑），**协议语义一个字节
都不换**：同一个请求信封、同一个响应信封、同一套 generation / render_revision
/ canonical_patch_hash 回显纪律、同一张错误码表。

抄一份进 bridge 就是造第二套协议语义——它一开始逐字相同，然后在某次「只给
bridge 加一个字段」之后分叉，而分叉的表现是 supervisor 的账本对不上号、
或者某个错误码只在一条入口上出现。所以信封收在这里，传输各写各的。

## 谁负责什么

* 本模块：信封解析 / 校验 / 分派 / 回显 / 错误信封；
* `figsession.LiveFigureSession`：Figure 到手之后的编辑语义；
* `worker.py` / `bridge_runner.py`：怎么把脚本跑起来 + 字节怎么进出。

纯标准库 + 兄弟模块 `patchspec`。
"""

from __future__ import annotations

import collections
import sys
import traceback

import patchspec

__all__ = [
    "PROTOCOL_VERSION",
    "V1_COMMANDS",
    "ProtocolError",
    "V1Handler",
    "echo",
    "hash_check",
    "int_arg",
    "v1_error",
]

#: 本实现的协议版本。升版规则见 ADR 0003：加字段不升版（两侧都必须容忍未知
#: 字段），改语义 / 删字段才升。
PROTOCOL_VERSION = 1

#: v1 命令集——不在表里的一律 unknown_cmd，绝不「猜一个最像的」。
V1_COMMANDS = frozenset(
    {
        "ping",
        "build",
        "render",
        "render_png",
        "preview_png",
        "export",
        "cancel",
        "shutdown",
    }
)

#: 带 patches 的命令（要算 canonical hash 做序列化自检）
PATCH_COMMANDS = frozenset({"render", "preview_png", "export"})

#: 需要 stem 的命令
STEM_COMMANDS = frozenset({"render", "render_png", "preview_png", "export"})

#: 会在响应里带 `timings` 的命令（v1 **only**——legacy 信封的形状一字不改）。
#: 加字段不升协议版本（ADR 0003 §1：两侧都必须容忍未知字段）。
TIMED_COMMANDS = frozenset({"build", "render", "export"})


class ProtocolError(Exception):
    """v1 的结构化错误。`retryable` 是给 supervisor 看的：只有 internal
    （我们也不知道为什么）才值得重启后重试一次；bad_request / unknown_cmd /
    unknown_stem / script_error 重试多少次都是同一个结果。"""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        traceback_text: str = "",
        extra: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.traceback_text = traceback_text
        self.extra = extra or {}


def int_arg(payload: dict, key: str, default: int) -> int:
    """payload 里的整数参数（width / dpi）；写错类型报 bad_request。"""
    raw = payload.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ProtocolError("bad_request", f"payload.{key} 必须是整数")
    try:
        return int(raw)
    except ValueError as exc:
        raise ProtocolError("bad_request", f"payload.{key} 必须是整数: {raw!r}") from exc


def echo(req: dict) -> dict:
    """v1 响应里原样回显的信封字段。

    generation / revision / hash **执行侧一律不解释**：它们是 supervisor 的
    账本（哪一代 worker、渲染到第几版、这组 patch 的身份），执行侧插手只会
    多出一个可能与账本不一致的地方。回显让上层能把响应对回请求。
    """
    out = {"protocol_version": PROTOCOL_VERSION, "request_id": req.get("request_id")}
    for key in ("worker_generation", "render_revision", "canonical_patch_hash"):
        if key in req:
            out[key] = req[key]
    return out


def hash_check(req: dict) -> dict:
    """自己也算一遍 canonical hash，与请求里带的对不上就标记出来。

    **不拒绝执行**：分歧只可能来自「另一种语言的序列化实现」（Rust
    supervisor），当场拒绝会把一个可观测的对齐问题变成一次用户可见的渲染
    失败。标记 + stderr 警告，让上层发现并去修序列化。
    """
    claimed = req.get("canonical_patch_hash")
    if not isinstance(claimed, str) or not claimed:
        return {}
    payload = req.get("payload")
    patches = payload.get("patches", []) if isinstance(payload, dict) else []
    mine = patchspec.patch_hash(patches)
    if mine == claimed:
        return {}
    print(
        f"[protocol] canonical_patch_hash 不一致: 请求 {claimed} / "
        f"本地 {mine}（照常执行，请检查两侧的规范化实现）",
        file=sys.stderr,
    )
    return {"hash_mismatch": True, "worker_patch_hash": mine}


def v1_error(req: dict, exc: ProtocolError) -> dict:
    err = {
        "code": exc.code,
        "retryable": exc.retryable,
        "message": exc.message,
        "traceback": exc.traceback_text,
    }
    err.update(exc.extra)
    return {"ok": False, **echo(req), "error": err}


class V1Handler:
    """v1 信封 → v1 响应。**两条入口共用的分派逻辑。**

    子类只需回答两个执行侧才知道的问题：

    * `ensure_built(timings)` —— 「脚本跑了吗」。safe worker 在这里第一次跑
      用户脚本；native bridge 的脚本早就在用户自己的进程里跑过了，是空操作。
    * `build_result(timings)` —— build 响应的 body（stems + descriptors）。

    需要额外命令的入口重写 `commands()` 与 `handle_extra()`（native bridge
    的 `continue` 走这条），**绝不新造一套信封**。
    """

    def __init__(self, session):
        self.session = session
        # 见过的 request_id（v1 的 cancel 用来分辨「那条已经跑完了」和
        # 「根本没见过这个 id」）。执行侧串行读请求，能读到 cancel 就说明
        # 目标请求早已结束——只留最近一小段，不做无上限的账本。
        self._seen: collections.deque = collections.deque(maxlen=64)

    # ---------------- 子类接口 ----------------
    def commands(self) -> frozenset:
        return V1_COMMANDS

    def ensure_built(self, timings: dict | None = None) -> None:
        return None

    def build_result(self, timings: dict) -> dict:
        raise NotImplementedError

    def handle_extra(self, cmd: str, req: dict, payload: dict) -> dict:
        raise ProtocolError("unknown_cmd", f"未知指令: {cmd}")

    # ---------------- 分派 ----------------
    def handle_v1(self, req: dict) -> dict:
        """v1 信封 → v1 响应。抛 `ProtocolError` 由 `v1_error()` 转成错误信封。

        **名字不叫 `handle`**：safe worker 还带着一套 legacy 扁平信封，它的
        入口历来就叫 `handle`。两者同名的话子类的 legacy 方法会静默顶掉本
        方法，表现是每条 v1 请求都被 legacy 分支答成「未知指令」——第一次
        重构时就是这样红的。
        """
        rid = req.get("request_id")
        if req.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(
                "bad_request",
                f"不支持的 protocol_version: {req.get('protocol_version')!r}"
                f"（本实现说 v{PROTOCOL_VERSION}）",
            )
        if not isinstance(rid, str) or not rid:
            raise ProtocolError("bad_request", "request_id 必须是非空字符串")
        cmd = req.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            raise ProtocolError("bad_request", "cmd 必须是非空字符串")
        payload = req.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ProtocolError("bad_request", "payload 必须是对象")
        for key in ("worker_generation", "render_revision"):
            val = req.get(key)
            if val is not None and not isinstance(val, int):
                raise ProtocolError("bad_request", f"{key} 必须是整数")
        known = self.commands()
        if cmd not in known:
            raise ProtocolError("unknown_cmd", f"未知指令: {cmd}", extra={"known": sorted(known)})

        self._seen.append(rid)

        if cmd == "shutdown":
            raise SystemExit(0)
        if cmd == "ping":
            return {}
        if cmd == "cancel":
            return self.cancel(payload)

        patches = payload.get("patches", [])
        if cmd in PATCH_COMMANDS and not isinstance(patches, list):
            raise ProtocolError("bad_request", "payload.patches 必须是数组")

        # 阶段计时（毫秒）。**只在 v1 出现**，legacy 信封的形状一字不改。
        timings: dict[str, float] = {}
        #: 这一版的预览表示法（ADR 0022）。与 `timings` 同一条纪律：**只在 v1
        #: 出现**，出参形态传下去，legacy 的 `{ok, manifest, warnings}` 一字不动。
        preview: dict = {}
        self.ensure_built(timings)
        if cmd == "build":
            return {**self.build_result(timings), "timings": timings}
        if cmd not in V1_COMMANDS:
            return self.handle_extra(cmd, req, payload)

        result: dict = {}
        stem = req.get("stem")
        fig = self.session
        if cmd in STEM_COMMANDS:
            if not isinstance(stem, str) or not stem:
                raise ProtocolError("bad_request", "stem 必须是非空字符串")
            if stem not in fig.states:
                raise ProtocolError(
                    "unknown_stem", f"stem 不存在: {stem}", extra={"known": sorted(fig.states)}
                )

        # 参数校验全部先做完再进渲染：混在下面那个 try 里的话，matplotlib 自己
        # 抛的 ValueError（画的时候什么都可能抛）会被当成「调用方参数写错了」，
        # 报出 retryable=false 的 bad_request——排障时指向完全错误的方向。
        if cmd == "export":
            path = payload.get("path")
            if not isinstance(path, str) or not path:
                raise ProtocolError("bad_request", "payload.path 必须是非空字符串")
        width = int_arg(payload, "width", 800 if cmd == "render_png" else 400)
        dpi = int_arg(payload, "dpi", 600)
        # 可选：这一次预览 SVG 用的 dpi（缺省 = 会话的 preview_dpi）。
        # 非正数一律 bad_request——0 会让 matplotlib 抛在渲染里，报出来的
        # 是 internal + 一段 traceback，指向完全错误的方向。
        preview_dpi = None
        if "preview_dpi" in payload and payload["preview_dpi"] is not None:
            preview_dpi = int_arg(payload, "preview_dpi", fig.preview_dpi)
            if preview_dpi <= 0:
                raise ProtocolError("bad_request", f"payload.preview_dpi 必须为正: {preview_dpi}")
        # 可选：把这次的预览 SVG 一并放进响应（与 manifest 原子配对）。
        # 只认真正的布尔值——"false"/0 这类写法在真值判断下会静默地做反，
        # 而这个字段决定的是「调用方能不能拿到配对的 SVG」，静默出错代价太大。
        inline_svg = payload.get("inline_svg", False)
        if not isinstance(inline_svg, bool):
            raise ProtocolError("bad_request", f"payload.inline_svg 必须是布尔值: {inline_svg!r}")

        try:
            if cmd == "render":
                result = fig.do_render(stem, patches, timings, preview_dpi, inline_svg, preview)
            elif cmd == "render_png":
                result = fig.do_render_png(stem, width)
            elif cmd == "preview_png":
                result = fig.do_preview_png(stem, patches, width, str(payload.get("tag", "p")))
            elif cmd == "export":
                result = fig.do_export(
                    stem, patches, payload["path"], str(payload.get("format", "pdf")), dpi, timings
                )
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 我们也不知道为什么——supervisor 重启后重试一次是合理的
            raise ProtocolError(
                "internal",
                str(exc) or exc.__class__.__name__,
                retryable=True,
                traceback_text=traceback.format_exc(),
            ) from exc
        if cmd in TIMED_COMMANDS:
            result["timings"] = timings
        if preview:
            result["preview"] = preview
        return result

    def cancel(self, payload: dict) -> dict:
        """**尽力而为的幂等 no-op**——这是协议里最容易被误解的一条。

        执行侧单线程串行读请求：正在跑 build/export 的时候根本读不到 cancel，
        等读到了那条请求早就结束了。所以这里不假装能中断 matplotlib，
        只回 ok + 一句诚实的 note。真正的硬取消 = supervisor kill 掉进程重启
        （`pool` 的超时路径就是这个语义）。
        """
        target = payload.get("request_id")
        if not isinstance(target, str) or not target:
            raise ProtocolError("bad_request", "cancel 需要 payload.request_id")
        seen = target in self._seen
        note = (
            "该请求已执行完毕，取消无事可做（执行侧串行执行，读到 cancel 时它必然已结束）"
            if seen
            else "没见过这个 request_id（可能已被淘汰出最近记录，或从未到达）"
        )
        return {"note": note, "cancelled": False, "seen": seen}


def respond(handler: V1Handler, req: object, legacy=None) -> dict:
    """按信封分派。带 `protocol_version` 走 v1，否则交给 `legacy`（可选）。"""
    if not isinstance(req, dict):
        return v1_error({}, ProtocolError("bad_request", "请求必须是 JSON 对象"))
    if "protocol_version" not in req:
        if legacy is None:
            return v1_error(
                {}, ProtocolError("bad_request", "请求缺少 protocol_version（本入口只说 v1）")
            )
        return legacy(req)
    try:
        result = handler.handle_v1(req)
    except ProtocolError as exc:
        return v1_error(req, exc)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 兜底，绝不让执行侧静默退出
        return v1_error(
            req,
            ProtocolError(
                "internal",
                str(exc) or exc.__class__.__name__,
                retryable=True,
                traceback_text=traceback.format_exc(),
            ),
        )
    return {"ok": True, **echo(req), **hash_check(req), **result}
