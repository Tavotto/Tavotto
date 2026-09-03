"""`tavotto run` —— Native Bridge 的**产品入口**（ADR 0021）。

    tavotto run [Tavotto 选项] -- <python> <脚本.py|-m 模块> [脚本自己的参数…]

## 这个进程拥有什么

```text
用户终端
   │
   ▼
tavotto run（本模块）
   ├── stdin / stdout / stderr   ← 原样是用户终端的，一个字节不碰
   ├── invocation / env / cwd    ← 原样是这个 shell 的
   ├── 用户 Python 子进程         ← 它是本进程的**直接子进程**
   └── 一条认证 relay（nativerelay）
           ├──── Tavotto 桌面 sidecar
           └──── 用户 Python 的 Bridge Runner
```

**桌面 sidecar 绝不 spawn 用户的 Python**（ADR 0021 §1）：它的 stdout 已经
写进 `sidecar.log`、没有终端 stdin、启动环境是桌面壳的而不是这个 shell 的。
要让它拿到这些，只能把整份环境序列化再重建，而那就是第二份环境解析实现。

## 顺序是产品语义的一部分

```text
解析 → 体检 → 起 relay → 写 descriptor → 唤起桌面 → 用户确认 → sidecar attach
                                                                      ↓
                                                        **这时才** spawn 用户 Python
```

用户确认之前**一行用户代码都没跑**。反过来做（先跑起来再找 UI）的表现是：
脚本已经写了文件、发了请求、跑了半小时，然后 Tavotto 说"找不到桌面应用"。

## 输出

**用户的 Python 起来之后**，Tavotto 自己的话全部写 stderr；用户的 stdout 一个
字节不解析、不加前缀。所以**没有 `--json`**：承诺"stdout 只有一行 JSON"就与
native 的核心语义直接冲突（用户的 `print` / `tqdm` / 二进制输出都在那条流上）。
要机器可读结果用 `--status-file`。

**`--help` 不在这条规则里，它走 stdout。** 规则守的是"stdout 归用户程序"，而
`--help` 这条路上没有用户程序——它在解析阶段就返回了，一个子进程都没起，那条
流此刻是 Tavotto 自己的。按 POSIX 惯例 `--help` 是用户**要**的输出，归 stdout；
写反了 `tavotto run --help | less` 与 `> help.txt` 在用户那儿都是空的
（issue #198）。**用法错误**（缺 `--`、不认识的选项）是另一回事：那是用户没要
的诊断，照旧写 stderr 且退 2——两个流向必须一起钉住，只钉一条就会有人把它们
改成同一个。

纯标准库 + `handoff`（唤起桌面）；**不 import Flask、不 import matplotlib**。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from . import (
    bridge,
    config,
    execspec,
    handoff,
    nativehandoff,
    nativerelay,
    runcodes,
    runspec,
    runtime,
)
from .runcodes import RunError

#: 等桌面 attach 的上限。用户要读确认文案、可能还要先把窗口找出来。
ATTACH_TIMEOUT = 300.0
#: 等用户的 Python 连回控制通道的上限。冷启动 + import 科学栈可能很慢，
#: 但 90s 还连不上就不是"慢"而是"起不来"了。
CHILD_TIMEOUT = 90.0
#: 桌面取消的轮询间隔（读一次 descriptor 的状态）。
CANCEL_POLL = 0.5


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tavotto run",
        add_help=False,  # `-h` 要能出现在 `--` 右边给用户脚本用
        allow_abbrev=False,
    )
    ap.add_argument("--project", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--status-file", default="")
    ap.add_argument("--help", "-h", action="store_true", dest="want_help")
    # ---- 内部测试用；**刻意不进正式 help**（ADR 0021 §2.2）----
    ap.add_argument("--x-no-desktop", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument(
        "--x-attach-timeout", type=float, default=ATTACH_TIMEOUT, help=argparse.SUPPRESS
    )
    ap.add_argument("--x-child-timeout", type=float, default=CHILD_TIMEOUT, help=argparse.SUPPRESS)
    return ap


def cli(argv: list[str]) -> int:
    """`tavotto run` 的入口。返回**进程退出码**（ADR 0021 §10.2）。"""
    raw = list(argv)
    mine, invocation = runspec.split_argv(raw)
    ap = build_parser()
    # `parse_known_args` 而不是 `parse_args`：没有 `--` 的时候，用户敲的
    # `tavotto run python fig.py` 会让 argparse 先喊 "unrecognized arguments"
    # ——那句话把注意力引到了"参数写错了"，而真正的问题是**缺了 `--`**。
    # 我们要先自己判这一条，再让 argparse 处理剩下的。
    args, unknown = ap.parse_known_args(mine)
    if args.want_help:
        # **stdout**：被请求的输出（见模块说明）。这条路上还没有任何用户
        # 进程，stdout 此刻不是"用户程序的"。
        print(runspec.usage_text(), end="")
        return runcodes.EXIT_OK
    if "--" not in raw or not invocation:
        _fail(RunError(runcodes.RUN_COMMAND_MISSING), quiet=False, with_usage=True)
        return runcodes.EXIT_USAGE
    if unknown:
        # `--` **左边**出现了 Tavotto 不认识的东西。静默忽略是不行的：
        # 用户很可能是把脚本的参数写错了位置，而那条命令会以一个他没要求的
        # 配置跑完并出图。
        print(f"[Tavotto Run] 不认识的选项: {' '.join(unknown)}", file=sys.stderr, flush=True)
        print(runspec.usage_text(), file=sys.stderr, end="", flush=True)
        return runcodes.EXIT_USAGE

    options = runspec.RunOptions(
        project=args.project, quiet=args.quiet, status_file=args.status_file
    )
    try:
        request = runspec.build_request(options, invocation)
    except RunError as exc:
        _fail(exc, quiet=args.quiet, with_usage=exc.code == runcodes.RUN_COMMAND_MISSING)
        _write_status(options, None, error_code=exc.code, session_result="invocation_rejected")
        return exc.exit_code()

    return _run(request, args)


# ---------------------------------------------------------------------------
def _run(request: runspec.RunRequest, args) -> int:
    quiet = request.options.quiet
    runspec.eprint(runspec.stderr_banner(request), quiet=quiet)

    app = ""
    if not args.x_no_desktop:
        app = handoff.find_desktop_app()
        if not app:
            exc = RunError(runcodes.NATIVE_DESKTOP_REQUIRED)
            _fail(exc, quiet=quiet)
            _write_status(
                request.options, request, error_code=exc.code, session_result="desktop_unavailable"
            )
            return exc.exit_code()

    native_id = nativehandoff.new_id()
    out_dir = str(config.data_path("cache", "native", native_id))
    relay = nativerelay.NativeRelay()
    proc: subprocess.Popen | None = None
    try:
        nativehandoff.create(
            native_id=native_id,
            out_dir=out_dir,
            project_root=request.project_root,
            interpreter=request.interpreter,
            cwd=request.cwd,
            target_kind=request.target_kind,
            target_display=request.target_display,
            arg_count=len(request.user_argv),
            command_fingerprint=request.command_fingerprint(),
            permission_key=request.permission_key(),
            python_version=".".join(str(v) for v in request.python_version),
            attach_host=relay.host,
            attach_port=relay.attach_port,
            attach_token=relay.attach_token,
        )
        if app:
            _wake_desktop(app, request, native_id)
        runspec.eprint("Waiting for Tavotto desktop…", quiet=quiet)
        relay.wait_for_desktop(args.x_attach_timeout, watch=_cancel_watch(native_id))

        # ---- 用户确认之后才 spawn（ADR 0021 §7）----
        proc = _spawn_user_python(request, relay, out_dir)
        relay.wait_for_child(args.x_child_timeout, watch=_child_watch(proc))
        relay.start_pump()
        code = _wait_for_child_process(proc, relay, quiet=quiet)
    except RunError as exc:
        _fail(exc, quiet=quiet)
        _write_status(request.options, request, error_code=exc.code, session_result="attach_failed")
        return exc.exit_code()
    except nativerelay.RelayClosed as exc:
        wrapped = RunError(runcodes.NATIVE_RELAY_FAILED)
        _fail(wrapped, quiet=quiet, detail=str(exc))
        _write_status(
            request.options, request, error_code=wrapped.code, session_result="relay_failed"
        )
        return wrapped.exit_code()
    finally:
        relay.close()
        nativehandoff.discard(native_id)
        if proc is not None and proc.poll() is None:
            # 走到这里说明我们**异常**离开了，而用户的进程还在。不杀它——
            # 那是他的脚本。只是不再转发字节了（runner 看到 EOF 会先恢复
            # baseline 再放开屏障，ADR 0021 §8.1）。
            pass

    figures = _figures_written(out_dir)
    result = "ok" if figures else runcodes.NO_FIGURE_CAPTURED
    if not figures:
        runspec.eprint(runcodes.message_for(runcodes.NO_FIGURE_CAPTURED), quiet=quiet)
    _write_status(
        request.options,
        request,
        script_exit_code=code,
        figures_captured=figures,
        session_result=result,
        error_code="" if figures else runcodes.NO_FIGURE_CAPTURED,
    )
    # **脚本的退出码原样透传**（ADR 0021 §10.2）。"没捕获到图"是 Tavotto 的
    # 产品结果，不是脚本的失败——把它折进退出码会让 `tavotto run … && next`
    # 这类用法与直接跑 python 的行为分叉。
    return code


# ---------------------------------------------------------------------------
def _wake_desktop(app: str, request: runspec.RunRequest, native_id: str) -> None:
    """唤起（或转发给已在跑的）桌面 App。**argv 里只有不透明 ID。**"""
    target = handoff.Target(request.project_root, None, None, native_id)
    try:
        handoff.launch(target, prefer="desktop")
    except handoff.HandoffError as exc:
        raise RunError(runcodes.NATIVE_DESKTOP_REQUIRED) from exc


def _spawn_user_python(
    request: runspec.RunRequest, relay: nativerelay.NativeRelay, out_dir: str
) -> subprocess.Popen:
    """用**当前**的 cwd / env / stdio 起用户的 Python。

    三条与 ADR 0020 spike 不同的产品决定：

    1. **`creationflags=runtime.INHERIT_CONSOLE`（= 0），不是
       `CREATE_NO_WINDOW`**。spike 那个标志是给 GUI 父进程用的（避免弹一个
       黑框）。`tavotto run` 的父进程**就是用户的终端**，在这里加它会把子进程
       从控制台上摘下来——stdin 断掉、Ctrl+C 送不到、`input()` 当场 EOF。
       CLI 拥有的子进程与 GUI 拥有的隐藏子进程是两回事，而这里显式写出
       是哪一类（`test_windows_regressions` 要求每个 spawn 都声明）。
    2. **stdin/stdout/stderr 全部 `None`**（原样继承）。协议在独立 socket 上，
       用户的三条流是他程序的语义。
    3. **env 原样 + 一个 token**，而 runner 一起来就把它摘掉。
    """
    spec = execspec.native_spec(
        request.raw_target,
        interpreter=request.interpreter,
        cwd=request.cwd,
        project_root=request.project_root,
        target_kind=request.target_kind,
        argv=request.user_argv,
    )
    argv = execspec.bridge_argv(
        spec,
        runner_py=bridge.RUNNER_PY,
        out_dir=out_dir,
        control_host=relay.host,
        control_port=relay.child_port,
    )
    env = {**os.environ, bridge.TOKEN_ENV: relay.child_token}
    os.makedirs(out_dir, exist_ok=True)
    try:
        return subprocess.Popen(  # noqa: S603 — argv 是列表，shell=False（ADR 0021 §3.3）
            argv,
            cwd=request.cwd,
            env=env,
            stdin=None,
            stdout=None,
            stderr=None,
            creationflags=runtime.INHERIT_CONSOLE,
        )
    except OSError as exc:
        raise RunError(
            runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=request.interpreter
        ) from exc


def _wait_for_child_process(
    proc: subprocess.Popen, relay: nativerelay.NativeRelay, *, quiet: bool = False
) -> int:
    """等用户脚本退出，返回**它的**退出码。

    Ctrl+C **不吞**：信号本来就送给整个前台进程组，孩子会自己收到
    `KeyboardInterrupt`。我们要做的有两件：

    1. **别抢在它前面退出**——那会留下一个孤儿，而用户的终端已经回到提示符，
       他不会知道还有个 Python 在跑；
    2. **把控制通道撤掉**。这一条是实测出来的：脚本被 Ctrl+C 打断之后仍然会
       走到"脚本结束"那个屏障（图已经画出来了），而屏障要等桌面应答——没人
       应答它就永远不退。那等于 **Tavotto 改变了 Ctrl+C 的含义**：用户按了
       中断，终端却再也回不来。撤掉通道之后 runner 在屏障处看到 EOF，会先把
       Figure 恢复成脚本原样再放开（ADR 0021 §8.1），进程正常退出。
    """
    interrupted = False
    while True:
        try:
            return _exit_code_of(proc.wait())
        except KeyboardInterrupt:
            if not interrupted:
                interrupted = True
                runspec.eprint("[Tavotto Run] 收到中断，正在收尾（不会杀你的进程）…", quiet=quiet)
                relay.cancel()
                relay.close()
            continue


def _exit_code_of(code: int) -> int:
    """`Popen.wait()` 的返回值 → 进程退出码。

    POSIX 上被信号杀掉时它是**负数**（`-SIGINT`）。按 shell 惯例折成
    `128 + signo`——直接把 `-2` 当退出码返回会变成 254，那个数字对任何人
    都没有意义。
    """
    return 128 + (-code) if code < 0 else code


def _figures_written(out_dir: str) -> int:
    """这次跑出了几张图（按会话产物目录里的预览 SVG 数）。

    **不问子进程**：它已经退出了，而这个判据要在它退出之后成立。
    """
    try:
        return sum(1 for n in os.listdir(out_dir) if n.endswith(".svg"))
    except OSError:
        return 0


def _cancel_watch(native_id: str):
    """等桌面 attach 期间盯着 descriptor——**取消**与**attach 被拒**都当场收摊。

    没有这一条的表现是：用户点了"取消"，桌面那边关掉了对话框，而他的终端
    还在"Waiting for Tavotto desktop…"上挂满 5 分钟。

    **"attach 被拒"刻意不在收摊之列。** #190 之后凭据要到 attach 成功才烧，
    所以被拒之后 descriptor 还是 `pending`，界面把那一项留在队列里让用户再点
    一次（`nativeSessionStore.approve` 的 catch），而 `environment_mutating`
    这一档本来就会自己消失。这时候 CLI 收摊 = 界面上留着一颗已经按不动的
    重试按钮——那正是 #190 的病根（可恢复的失败被做成不可恢复）换了个主语。

    等待因此由**用户的决定**来结束，不由这里猜：他点取消 → descriptor 墓碑成
    cancelled → 下面第一条分支当场收摊（退出码 3）。#190 之前这条路是断的——
    那时 attach 失败已经把 descriptor 变成 `consumed`，而 `cancel()` 对终态是
    幂等 no-op，于是取消了也还是等满 300 秒。
    """
    last = [0.0]

    def _watch() -> None:
        now = time.monotonic()
        if now - last[0] < CANCEL_POLL:
            return
        last[0] = now
        try:
            nativehandoff.peek(native_id)
        except RunError as exc:
            if exc.code in (
                runcodes.NATIVE_ATTACH_CANCELLED,
                runcodes.NATIVE_HANDOFF_EXPIRED,
                runcodes.NATIVE_HANDOFF_INVALID,
            ):
                raise
            # `consumed` 现在**只在 attach 成功之后**出现（app.py 把
            # `mark_consumed()` 放在 attach 之后）：那说明 relay 这一侧已经
            # 接受并认证了那条连接，继续等就是等自己马上返回。
            return
        # 还是 pending：用户还没点，或者点了但 attach 被拒而他可以再点一次。
        # 两种都继续等（见上面的 docstring）。

    return _watch


def _child_watch(proc: subprocess.Popen):
    """等子进程连回来期间盯着它——它要是直接死了，不该白等满整个 timeout。"""

    def _watch() -> None:
        rc = proc.poll()
        if rc is not None:
            raise RunError(runcodes.BRIDGE_CHILD_EXITED, code=_exit_code_of(rc))

    return _watch


def _fail(exc: RunError, *, quiet: bool, with_usage: bool = False, detail: str = "") -> None:
    """失败信息**写 stderr**（连 `--quiet` 也照写：静默失败比吵闹更坏）。"""
    del quiet
    line = f"[Tavotto Run] {exc}"
    if detail:
        line += f"（{detail}）"
    print(line, file=sys.stderr, flush=True)
    if with_usage:
        print(runspec.usage_text(), file=sys.stderr, end="", flush=True)


def _write_status(options: runspec.RunOptions, request, **fields) -> None:
    """只有调用方明确给了 `--status-file` 才写（ADR 0021 §11）。"""
    if not options.status_file:
        return
    try:
        runspec.write_status_file(options.status_file, runspec.status_payload(request, **fields))
    except OSError as exc:
        print(f"[Tavotto Run] 状态文件写不出来: {exc}", file=sys.stderr, flush=True)
