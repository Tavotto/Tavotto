"""native bridge 技术验证 CLI —— **不是产品，不是 `tavotto run`**。

    python -m tavotto.engine.bridge_spike run   -- python fig.py --dataset run7
    python -m tavotto.engine.bridge_spike run   -- python -m paper.figure
    python -m tavotto.engine.bridge_spike probe -- python fig.py    # 只跑一遍，出报告

`--` 之后是用户的完整 invocation，spike 自己的选项写在它前面。

刻意**没有**接进 `tavotto` 的 CLI（`cli_entry` 一个字都没改）：ADR 0020 是
架构决策的依据，不是对外承诺。命令行形状随时会变，任何人都不该照着它写脚本。
产品化（稳定 CLI 契约、桌面交接、生命周期、UI 确认文案）是 Session 9。

`run` 做的事：起用户的 Python → 等屏障 → build → 打印捕获到的图 → 放行 →
退出。加 `--patch`/`--export` 可以顺手验一遍编辑与导出走的确实是同一套引擎。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import bridge, execspec


def _split_invocation(words: list) -> tuple:
    """把 `python fig.py a b` / `python -m pkg.mod a b` 拆成 (kind, target, argv)。

    **只认这两种形态**（ADR 0014 §7）：任意 shell 命令（管道、env 前缀、
    Makefile、bash 包装）的解析与注入面是无界的，一旦支持就要对每种形态回答
    「捕获层注得进去吗、argv/env 语义还原了吗」——做不到就会变成静默半支持。
    """
    if not words:
        raise SystemExit("用法: bridge_spike run python <脚本.py|-m 模块> [参数…]")
    interp_word, rest = words[0], words[1:]
    if not rest:
        raise SystemExit("缺少目标：python 后面要有脚本或 -m 模块")
    if rest[0] == "-m":
        if len(rest) < 2:
            raise SystemExit("-m 后面要有模块名")
        return interp_word, execspec.TARGET_MODULE, rest[1], tuple(rest[2:])
    if rest[0].startswith("-"):
        raise SystemExit(
            f"不支持的解释器标志 {rest[0]!r}：v1 只认 `python 文件.py` 与 `python -m 模块`"
        )
    return interp_word, execspec.TARGET_SCRIPT, rest[0], tuple(rest[1:])


def _make_spec(words: list, explicit_interpreter: str = ""):
    interp_word, kind, target, argv = _split_invocation(words)
    interpreter = bridge.resolve_interpreter(explicit_interpreter or interp_word)
    return bridge.spec_for(target, interpreter=interpreter, argv=argv, target_kind=kind)


def cmd_probe(args) -> int:
    """跑一遍，不建控制通道，把运行小结写出来（对拍 / 冒烟用）。"""
    spec = _make_spec(args.invocation, args.python)
    out_dir = args.out_dir or tempfile.mkdtemp(prefix="tavotto-bridge-")
    report = args.report or os.path.join(out_dir, "report.json")
    argv = execspec.bridge_argv(spec, runner_py=bridge.RUNNER_PY, out_dir=out_dir, report=report)
    import subprocess  # noqa: PLC0415 — 只有这条分支要

    code = subprocess.call(argv, cwd=spec.cwd)
    print(json.dumps(json.load(open(report, encoding="utf-8")), ensure_ascii=False, indent=1))
    return code


def cmd_run(args) -> int:
    spec = _make_spec(args.invocation, args.python)
    out_dir = args.out_dir or tempfile.mkdtemp(prefix="tavotto-bridge-")
    print(f"[spike] 解释器 {spec.interpreter}", file=sys.stderr)
    print(f"[spike] cwd {spec.cwd}", file=sys.stderr)
    print(
        f"[spike] 目标 {spec.target_kind} {spec.raw_target} argv={list(spec.argv)}", file=sys.stderr
    )
    with bridge.BridgeSession(spec, out_dir=out_dir) as sess:
        # **每个屏障都要被应答**。一次运行里屏障可能出现多次：脚本中间每个
        # `plt.show()` 一次，脚本跑完再一次（那一次才是 show-only 之外的脚本
        # 唯一的编辑机会）。只应答第一个然后去等 `exit`，两边就各等各的
        # ——第一版就是这样挂死的，本机复现过。
        while True:
            ev = sess.wait_event("barrier")
            print(f"[spike] 屏障（{ev.get('reason')}）：stems={ev.get('stems')}", file=sys.stderr)
            build = sess.ensure_built()
            for stem, info in build.get("stems", {}).items():
                print(
                    f"[spike]   {stem}  {info['size_mm']} mm  来源={info['source']}",
                    file=sys.stderr,
                )
            if args.patch:
                gid, prop, value = args.patch.split("=", 2)
                stem = args.stem or next(iter(build.get("stems", {})), "")
                resp = sess.override(stem, [{"gid": gid, "prop": prop, "value": _coerce(value)}])
                print(f"[spike] override 完成，warnings={resp.get('warnings')}", file=sys.stderr)
            if args.export:
                stem = args.stem or next(iter(build.get("stems", {})), "")
                resp = sess.export(stem, [], args.export)
                print(f"[spike] 导出 {resp['path']}", file=sys.stderr)
            sess.resume()
            if ev.get("reason") == "script_end":
                break
        sess.wait_event("exit")
    print(f"[spike] 产物目录 {out_dir}", file=sys.stderr)
    return 0


def _coerce(value: str):
    try:
        return json.loads(value)
    except ValueError:
        return value


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tavotto-bridge-spike", allow_abbrev=False)
    ap.add_argument("--python", default="", help="显式解释器（默认取 invocation 里那个词）")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--stem", default="")
    ap.add_argument("--patch", default="", help="gid=prop=value（顺手验一次 override）")
    ap.add_argument("--export", default="", help="导出到这个路径")
    ap.add_argument("mode", choices=("run", "probe"))
    # invocation 走 `--` 之后，不用 `argparse.REMAINDER`：REMAINDER 会从**第一个**
    # 位置参数起吞掉一切，于是写在 mode 前面的 `--python …` 也被当成 invocation
    # 的一部分（实测第一次就踩到）。`--` 是没有歧义的那条线。
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        cut = raw.index("--")
        mine, invocation = raw[:cut], raw[cut + 1 :]
    else:
        mine, invocation = raw, []
    args = ap.parse_args(mine)
    args.invocation = invocation
    return cmd_run(args) if args.mode == "run" else cmd_probe(args)


if __name__ == "__main__":
    sys.exit(main())
