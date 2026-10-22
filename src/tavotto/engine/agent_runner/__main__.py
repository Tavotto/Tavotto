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
from pathlib import Path

from . import loop
from .redact import Redactor
from .tools import TOOLSETS, Limits, RunEnvelope
from .wires import (
    ANTHROPIC_EFFORTS,
    THINKING_MODES,
    WIRES,
    OpenAIChatWire,
    RunnerFailure,
    check_base_url,
)

ENV_API_KEY = "TAVOTTO_AGENT_API_KEY"
DEFAULT_LIMITS = Limits()


class _FailModel:
    """启动阶段就知道要失败时用：走同一条 `run` 路径，保证 `done` 事件形状一致。"""

    def __init__(self, failure: RunnerFailure):
        self.failure = failure

    def complete(self, **_kw):
        raise self.failure


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tavotto.engine.agent_runner", add_help=True)
    p.add_argument("--cwd", required=True, help="候选目录（工具能碰的唯一根）")
    p.add_argument("--target", required=True, help="目标脚本，相对 --cwd")
    p.add_argument("--wire", required=True, choices=[*sorted(WIRES), "demo"])
    p.add_argument("--base-url", default="", help="模型服务根地址（https，或 http://127.0.0.1）")
    p.add_argument("--model", default="")
    p.add_argument("--effort", choices=ANTHROPIC_EFFORTS, default=None)
    p.add_argument(
        "--thinking", choices=THINKING_MODES, default="none", help="思考怎么传（调用方声明）"
    )
    p.add_argument(
        "--max-tokens-field",
        choices=OpenAIChatWire.TOKEN_FIELDS,
        default="max_tokens",
        help="openai-chat 的长度上限字段（调用方声明；OpenAI o 系列要 max_completion_tokens）",
    )
    p.add_argument("--max-turns", type=int, default=DEFAULT_LIMITS.max_turns)
    p.add_argument("--max-seconds", type=float, default=DEFAULT_LIMITS.max_seconds)
    p.add_argument("--max-total-tokens", type=int, default=DEFAULT_LIMITS.max_total_tokens)
    p.add_argument("--request-timeout", type=float, default=120.0)
    p.add_argument("--prompt-file", default=None, help="提示词文件；缺省读 stdin")
    p.add_argument("--task", default="figure_edit", choices=sorted(TOOLSETS))
    p.add_argument("--demo-script", default=None, help="--wire demo 的剧本 JSON")
    p.add_argument("--retry-base", type=float, default=1.0, help="重试退避基数（秒）")
    return p


def _read_prompt(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


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
        instruction = _read_prompt(args.prompt_file)
    except (OSError, ValueError):
        instruction = ""
    bad_prompt = not instruction.strip()  # 读不到 / 空白：不调模型（不拿空指令去花钱）
    if bad_prompt:
        diag("agent_runner: bad_prompt")
    limits = Limits(
        max_turns=max(1, args.max_turns),
        max_total_tokens=max(1, args.max_total_tokens),
        max_seconds=max(1.0, args.max_seconds),
    )
    envelope = RunEnvelope(
        task_type=args.task,
        cwd=Path(args.cwd),
        target=args.target,
        instruction=instruction,
        limits=limits,
        redactor=redactor,
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
    elif args.wire == "openai-responses" and args.effort == "max":
        # Responses 没有 max 档：显式拒绝，不静默不发 reasoning；不发任何请求
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
