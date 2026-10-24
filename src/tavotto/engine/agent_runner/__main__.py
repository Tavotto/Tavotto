"""进程入口：`python -m tavotto.engine.agent_runner --cwd <候选目录> --target <相对路径> --wire <名> …`

约定（父进程只靠这些和我们说话）：

* 提示词从 `--prompt-file` 或 stdin 读（UTF-8），**不放 argv**（Windows 命令行转义与进程列表泄漏）。
* 密钥只读环境变量 `TAVOTTO_AGENT_API_KEY`（读后立即从本进程环境里删掉）；argv、stdout、stderr 里都没有它：
  事件写出函数 `emit` 与诊断函数 `diag` 是本进程写 stdout / stderr 的唯一出口，所有字符串过 `Redactor`（模型的成功回复也不可信）。
* stdout 每行一条 JSON 事件（`thinking | tool | text | usage | done`，ASCII 转义，Windows 控制台编码无关）；
  最后一条一定是 `done`。stderr 只写诊断级错误码，不含内容。
* 退出码：`done.ok` 为真 0，否则 1；命令行参数错 2。
* SIGTERM / SIGINT / Windows 的 Ctrl-Break：尽快收尾，发 `done` 的 `cancelled`。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from dataclasses import replace
from pathlib import Path

from . import loop
from .redact import Redactor
from .tools import TOOLSETS, RunEnvelope
from .wires import (
    ANTHROPIC_EFFORTS,
    RESPONSES_EFFORTS,
    THINKING_MODES,
    WIRES,
    AnthropicWire,
    OpenAIChatWire,
    RunnerFailure,
    check_base_url,
)

ENV_API_KEY = "TAVOTTO_AGENT_API_KEY"


class _FailModel:
    """启动阶段就知道要失败时用：走同一条 `run` 路径，保证 `done` 事件形状一致。"""

    def __init__(self, failure: RunnerFailure):
        self.failure = failure

    def complete(self, **_kw):
        raise self.failure


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tavotto.engine.agent_runner", add_help=True)
    p.add_argument("--cwd", required=True, help="候选目录（工具能碰的唯一根）")
    p.add_argument(
        "--target",
        required=True,
        help="目标脚本，相对 --cwd（convert_to_editable：原脚本，只读）",
    )
    p.add_argument(
        "--output",
        default="",
        help="convert_to_editable：要新建的 <原名>_matplotlib.py（候选目录顶层、尚不存在）",
    )
    p.add_argument("--wire", required=True, choices=[*sorted(WIRES), "demo"])
    p.add_argument("--base-url", default="", help="模型服务根地址（https，或 http://127.0.0.1）")
    p.add_argument("--model", default="")
    p.add_argument(
        "--effort",
        choices=(*dict.fromkeys((*RESPONSES_EFFORTS, *ANTHROPIC_EFFORTS)),),
        default=None,
    )
    p.add_argument(
        "--thinking", choices=THINKING_MODES, default="none", help="思考怎么传（调用方声明）"
    )
    p.add_argument(
        "--max-tokens-field",
        choices=OpenAIChatWire.TOKEN_FIELDS,
        default="max_tokens",
        help="openai-chat 的长度上限字段（调用方声明；OpenAI o 系列要 max_completion_tokens）",
    )
    # 默认值取所选任务工具集的 default_limits（convert 与 figure_edit 不同），所以这里缺省 None
    p.add_argument("--max-turns", type=int, default=None)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--max-total-tokens", type=int, default=None)
    p.add_argument("--request-timeout", type=float, default=120.0)
    p.add_argument("--prompt-file", default=None, help="提示词文件；缺省读 stdin")
    p.add_argument("--task", default="figure_edit", choices=sorted(TOOLSETS))
    p.add_argument("--demo-script", default=None, help="--wire demo 的剧本 JSON")
    p.add_argument(
        "--auth",
        choices=("key", "bearer"),
        default="key",
        help="鉴权形态（调用方声明）：key = x-api-key + Bearer 都给；bearer = 只发 Authorization: Bearer（账号登录令牌）",
    )
    p.add_argument(
        "--check-ipc",
        action="store_true",
        help='convert_to_editable：`check` 经 stdout/stdin 回调父进程。stdin 第一行是 {"prompt": …}，之后每行一个 {"ok": bool, "report": str}',
    )
    p.add_argument("--retry-base", type=float, default=1.0, help="重试退避基数（秒）")
    return p


def _read_prompt(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def _ipc_check(emit):
    """`check` 回调：向父进程要一次校验运行（stdout 一条 `check_request`，stdin 等一行回复）。
    父进程跑的是 Tavotto 自己的校验流水（ADR 0143 `run_validation`），本进程不碰候选以外的任何东西。
    stdin 关了 / 回复畸形 → 不通过，报告只有稳定短码。"""

    def check() -> tuple[bool, str]:
        emit({"type": "check_request"})
        try:
            line = sys.stdin.buffer.readline().decode("utf-8", errors="replace")
            msg = json.loads(line)
        except (OSError, ValueError):
            return False, "check_unavailable"
        if not isinstance(msg, dict):
            return False, "check_unavailable"
        return bool(msg.get("ok")), str(msg.get("report") or "")[:4000]

    return check


def _read_first_line_prompt() -> str:
    """`--check-ipc`：stdin 第一行是 `{"prompt": …}`（stdin 之后还要留给 check 的回复，所以不能读到 EOF）。"""
    line = sys.stdin.buffer.readline().decode("utf-8", errors="replace")
    try:
        msg = json.loads(line)
    except ValueError:
        return ""
    return (
        msg.get("prompt", "")
        if isinstance(msg, dict) and isinstance(msg.get("prompt"), str)
        else ""
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = os.environ.pop(ENV_API_KEY, "")
    redactor = Redactor(api_key)
    cancel = threading.Event()

    def on_signal(_signum, _frame):
        cancel.set()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, on_signal)
            except (ValueError, OSError):  # 非主线程 / 平台不支持
                pass

    lock = threading.Lock()

    def diag(message: str) -> None:
        try:
            sys.stderr.write(redactor.redact(message) + "\n")
        except OSError:
            pass

    class _DiagHandler(logging.Handler):
        """产品模块（如 tlstrust 的退回告警）的日志也只经 diag 出口：只发「模块名: 级别」短码，不带异常文本。"""

        def emit(self, record: logging.LogRecord) -> None:
            diag(f"agent_runner: log {record.name} {record.levelname.lower()}")

    product_log = logging.getLogger("tavotto")
    product_log.handlers = [_DiagHandler()]
    product_log.propagate = False

    def emit(event: dict) -> None:
        line = json.dumps(redactor.redact_obj(event), ensure_ascii=True, separators=(",", ":"))
        with lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                cancel.set()  # 父进程没了：别再跑

    try:
        instruction = (
            _read_first_line_prompt() if args.check_ipc else _read_prompt(args.prompt_file)
        )
    except (OSError, ValueError):
        instruction = ""
    bad_prompt = not instruction.strip()  # 读不到 / 空白：不调模型（不拿空指令去花钱）
    if bad_prompt:
        diag("agent_runner: bad_prompt")
    base = TOOLSETS[args.task].default_limits
    limits = replace(
        base,
        max_turns=max(1, args.max_turns if args.max_turns is not None else base.max_turns),
        max_total_tokens=max(
            1,
            args.max_total_tokens if args.max_total_tokens is not None else base.max_total_tokens,
        ),
        max_seconds=max(
            1.0, args.max_seconds if args.max_seconds is not None else base.max_seconds
        ),
    )
    envelope = RunEnvelope(
        task_type=args.task,
        cwd=Path(args.cwd),
        target=args.target,
        instruction=instruction,
        limits=limits,
        redactor=redactor,
        output=args.output,
        check=_ipc_check(emit) if args.check_ipc else None,
    )

    model: object
    if bad_prompt:
        model = _FailModel(RunnerFailure("internal", "bad_prompt"))
    elif args.effort is not None and args.wire == "openai-chat":
        # Chat 协议没有可移植的推理强度字段：声明了就拒绝，不静默丢掉；不发任何请求
        model = _FailModel(RunnerFailure("internal", "bad_args"))
    elif args.thinking != "none" and args.wire not in ("anthropic", "demo"):
        # 思考模式只有 anthropic 协议认；别的协议上声明了就拒绝，不静默丢掉；不发任何请求
        model = _FailModel(RunnerFailure("internal", "bad_args"))
    elif args.thinking == "budget" and args.effort is None and args.wire != "demo":
        # budget 要有档位：显式优于静默（缺省时旧实现悄悄不发 thinking）；不发任何请求
        model = _FailModel(RunnerFailure("internal", "bad_args"))
    elif (args.wire == "openai-responses" and args.effort not in (None, *RESPONSES_EFFORTS)) or (
        args.wire == "anthropic" and args.effort not in (None, *ANTHROPIC_EFFORTS)
    ):
        # 这个协议不认的档位（Responses 没有 max；anthropic 没有 minimal）：显式拒绝，不静默不发；不发任何请求
        model = _FailModel(RunnerFailure("internal", "bad_args"))
    elif args.wire == "demo":
        try:
            model = loop.DemoModel.from_file(args.demo_script or "")
        except (OSError, ValueError):
            model = _FailModel(RunnerFailure("internal", "bad_demo_script"))
    elif not api_key:
        model = _FailModel(RunnerFailure("auth", "no_key"))
    elif not args.model:
        model = _FailModel(RunnerFailure("internal", "no_model"))
    elif (bad := check_base_url(args.base_url)) is not None:
        model = _FailModel(RunnerFailure("network", bad))
    else:
        wire = (
            OpenAIChatWire(token_field=args.max_tokens_field)
            if args.wire == OpenAIChatWire.name
            else AnthropicWire(bearer_only=args.auth == "bearer")
            if args.wire == AnthropicWire.name
            else WIRES[args.wire]
        )
        model = loop.HttpModel(
            wire,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            effort=args.effort,
            thinking=args.thinking,
            request_timeout=args.request_timeout,
            retry_base=args.retry_base,
        )
    del api_key

    res = loop.run(model, TOOLSETS[args.task], envelope, emit, cancel)
    if res.reason == "internal":
        diag(f"agent_runner: internal {res.detail}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
