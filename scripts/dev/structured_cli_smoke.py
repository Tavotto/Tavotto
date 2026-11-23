"""订阅登录（Codex / Claude Code CLI）无工具单轮结构化调用的冒烟（ADR 0178）。开发侧工具，不进产品。

**会消耗订阅额度——只在维护者本机手工跑，绝不进 CI / Lab / 夜跑。** 本脚本是「真跑」那一步的唯一入口：`tests/` 里所有 CLI 相关用例都是假进程。

用一张内置的小假表（小鼠体重，4 列）走真实的 `ai_structured.call_structured`（CLI 连接）+「读懂数据」的真请求与真校验，打印：是否成功、失败码、
总耗时、请求数（起的进程数）、JSON 是否过校验。不打印表格内容以外的任何用户信息；回复原文、提示词一律不打印。

数据 / 配置目录指到临时目录——不读、不写 Tavotto 的应用配置。**登录态仍来自 CLI 自己的 `CODEX_HOME` / Claude 配置目录**（那是订阅登录的本意）；
用户内容（这张假表的摘要）会发给对应厂商。

用法：
    python scripts/dev/structured_cli_smoke.py --agent codex  --model <型号> [--timeout 60]
    python scripts/dev/structured_cli_smoke.py --agent claude --model <型号> [--timeout 60]
    python scripts/dev/structured_cli_smoke.py --agent codex  --model <型号> --probe-schema   # 另跑一次，试 `--output-schema` / `--json-schema` 被接受

真跑后要看的（校准项）：总耗时是否远小于 `ai_structured.CLI_TIMEOUT_S`（60 s，待校准）；Codex 的 `model_instructions_file` 是否真的换掉预置指令、
`--ignore-user-config` 是否挡住 AGENTS.md / 技能；Claude 的 `--setting-sources ""` 空串是否被接受、带 `--json-schema` 时结果落在 `structured_output` 还是 `result`。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

#: 内置的小假表（与任何真实数据无关）
FAKE_CSV = "day,group,weight_g,mouse_id\n" + "".join(
    f"{d},{g},{20 + d * 0.3 + (1.5 if g == 'drug' else 0):.1f},{g[0]}{m}\n"
    for d in range(1, 6)
    for g in ("ctrl", "drug")
    for m in (1, 2)
)

PROBE_SYSTEM = '只回一个 JSON 对象，形如 {"answer": 1}，不要别的文字。'
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _report(title: str, res, ok_json: bool) -> None:
    print(f"[{title}]")
    print(f"  成功:        {res.ok}")
    print(f"  失败码:      {res.reason or '-'} / {res.detail or '-'}")
    print(f"  总耗时:      {res.elapsed_ms / 1000:.1f} s")
    print(f"  请求数:      {res.requests}（尝试 {res.attempts}）")
    print(f"  JSON 过校验: {ok_json}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--agent", choices=("codex", "claude"), required=True)
    p.add_argument("--model", required=True, help="显式型号（本步不读用户的 CLI 配置）")
    p.add_argument("--timeout", type=float, default=None, help="总时限秒数，默认 CLI_TIMEOUT_S")
    p.add_argument("--probe-schema", action="store_true", help="另跑一次带 JSON Schema 的最小调用")
    args = p.parse_args(argv)

    tmp = tempfile.TemporaryDirectory(prefix="tavotto-cli-smoke-")
    os.environ["TAVOTTO_DATA_DIR"] = str(Path(tmp.name) / "data")
    os.environ["TAVOTTO_NO_TELEMETRY"] = "1"
    sys.path.insert(0, str(ROOT / "src"))
    from tavotto.engine import ai_data_figure as adf, ai_data_roles as R, ai_structured as S

    table_dir = Path(tmp.name) / "table"
    table_dir.mkdir()
    (table_dir / "t.csv").write_text(FAKE_CSV, encoding="utf-8")
    entry = adf.profile_folder(table_dir).tables[0]
    values = adf.inspect_data_source(table_dir / "t.csv", v2=True)
    system, user = R.build_request(entry, values, None)

    conn = {"kind": "cli", "agent": args.agent, "model": args.model, "conn_id": f"sub:{args.agent}"}
    timeout = args.timeout if args.timeout is not None else S.CLI_TIMEOUT_S
    print(f"agent={args.agent} model={args.model} 总时限={timeout:.0f}s（会消耗订阅额度）")

    res = S.call_structured(
        system=system,
        user=user,
        validate=R.validate_roles_shape,
        limits=S.Limits(
            max_input_chars=R.MAX_INPUT_CHARS,
            max_output_tokens=R.MAX_OUTPUT_TOKENS,
            timeout_s=timeout,
        ),
        conn=conn,
        effort=R.EFFORT,
    )
    _report("读懂数据（内置假表）", res, res.ok and res.value is not None)
    rc = 0 if res.ok else 1

    if args.probe_schema:
        res2 = S.call_structured(
            system=PROBE_SYSTEM,
            user="请回答。",
            validate=lambda o: o if o.get("answer") == 1 else (_ for _ in ()).throw(ValueError()),
            limits=S.Limits(timeout_s=timeout),
            conn=conn,
            schema=PROBE_SCHEMA,
        )
        _report("带 schema 的最小调用", res2, res2.ok)
        rc = rc or (0 if res2.ok else 1)
    tmp.cleanup()
    return rc


if __name__ == "__main__":
    sys.exit(main())
