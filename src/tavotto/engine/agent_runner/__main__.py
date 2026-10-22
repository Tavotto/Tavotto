"""进程入口：`python -m tavotto.engine.agent_runner --cwd <候选目录> --target <相对路径> --wire <名> …`

约定（父进程只靠这些和我们说话）：

* 提示词从 `--prompt-file` 或 stdin 读（UTF-8），**不放 argv**（Windows 命令行转义与进程列表泄漏）。
* 密钥只读环境变量 `TAVOTTO_AGENT_API_KEY`（读后立即从本进程环境里删掉）；argv、stdout、stderr 里都没有它。
* stdout 每行一条 JSON 事件（`thinking | tool | text | usage | done`，ASCII 转义，Windows 控制台编码无关）；
  最后一条一定是 `done`。stderr 只写诊断级错误码，不含内容。
* 退出码：`done.ok` 为真 0，否则 1；命令行参数错 2。
* SIGTERM / SIGINT / Windows 的 Ctrl-Break：尽快收尾，发 `done` 的 `cancelled`。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from . import loop
from .tools import TOOLSETS, Limits, RunEnvelope
from .wires import ANTHROPIC_EFFORTS, THINKING_MODES, WIRES, RunnerFailure, check_base_url

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

    def emit(event: dict) -> None:
        line = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
        with lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                cancel.set()  # 父进程没了：别再跑

    try:
        instruction = _read_prompt(args.prompt_file)
    except OSError:
        instruction = ""
        sys.stderr.write("agent_runner: prompt_unreadable\n")
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
    )

    model: object
    if args.wire == "demo":
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
        model = loop.HttpModel(
            WIRES[args.wire],
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
        sys.stderr.write(f"agent_runner: internal {res.detail}\n")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
