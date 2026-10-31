#!/usr/bin/env python3
"""发布编排的诊断包条目门禁：客户端会写的每个顶层条目，线上诊断服务都认识。

背景：诊断包的顶层文件名在主仓库登记于 `engine/diagsend.py: ENTRY_KINDS`，infra 诊断服务另有一张
`contract.KNOWN_BUNDLE_FILES`——两个仓库各一份、谁也测不到谁。主仓库加了条目、infra 没跟上（或改了没部署），
线上每个反馈包都被打 `unknown_entry`：TVD-MYV0 漏 `detail-*` / `optional-*`，TVD-E03P 漏 `exec-traces.json`。
主仓库这一侧「源码写得出的条目都登记了」由 `tests/test_diag_send.py` 的 AST 看护钉住；本门禁钉跨仓库那一段。

判据的主语：**要发布的那个 SHA** 的 `ENTRY_KINDS` 键，对 **此刻线上部署的** 诊断服务
（`GET <PRODUCTION_ENDPOINT>/v1/reports/contract` 的 `known_bundle_files`）。不读 infra 仓库的源码——
改了没部署一样会漏。服务端多认几个不算错（旧客户端的条目、将来的条目）。

* 两侧一致：放行；
* 客户端有、服务端没有，或取不到 / 读不懂线上的表：不放行，除非 `workflow_dispatch` 的
  `ack_diag_contract` 写了原因（显式签字，进 job summary）。tag 触发带不了输入：先部署 infra 再发，
  或改用 dispatch 签字。

客户端源码只按 AST 读（不 import 包：trust 阶段没装依赖，也不该执行待发布的代码）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

# 与 release_blockers.py 同源：CI 捕获的 stdout 在 Windows 上会退回系统区域编码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CONTRACT_PATH = "/v1/reports/contract"


def _module_constant(tree: ast.Module, name: str):
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"客户端源码里找不到模块级常量 {name}")


def client_facts(source: str) -> tuple[set[str], str]:
    """(`ENTRY_KINDS` 的键, 契约 URL)。"""
    tree = ast.parse(source)
    kinds = _module_constant(tree, "ENTRY_KINDS")
    endpoint = _module_constant(tree, "PRODUCTION_ENDPOINT")
    if not isinstance(kinds, dict) or not kinds or not isinstance(endpoint, str):
        raise SystemExit("ENTRY_KINDS / PRODUCTION_ENDPOINT 的形状不对")
    return set(kinds), endpoint.rstrip("/") + CONTRACT_PATH


def server_files(raw: str) -> set[str] | None:
    """线上契约里的已知文件名；空 / 不是 JSON / 形状不对 → None（当作取不到）。"""
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    files = doc.get("known_bundle_files") if isinstance(doc, dict) else None
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        return None
    return set(files)


def check(client: set[str], raw_contract: str, ack: str, event: str) -> tuple[bool, list[str]]:
    """返回 (放行?, 给人看的行)。"""
    ack = ack.strip()
    server = server_files(raw_contract)
    lines: list[str] = []
    if server is None:
        lines.append("❌ 取不到或读不懂线上诊断服务的条目表（`known_bundle_files`）。")
    else:
        missing = sorted(client - server)
        if not missing:
            if ack:
                lines.append(
                    "两侧一致，但 ack_diag_contract 填了内容——陈旧的签字会在下次真漂移时被误当成放行，请清空后重跑。"
                )
                return False, lines
            lines.append(f"✅ 客户端的 {len(client)} 个诊断包条目线上诊断服务都认识。")
            return True, lines
        lines.append("❌ 这些条目客户端会写、线上诊断服务不认识（每个包都会被打 unknown_entry）：")
        lines.extend(f"  - `{name}`" for name in missing)
        lines.append(
            "先在 infra 的 `contract.KNOWN_BUNDLE_FILES` 加上并部署（`ops/deploy.sh tavotto-cn`），再发版。"
        )
    if ack:
        lines.append(f"⚠️ 已签字放行（ack_diag_contract = {ack!r}）：明知不一致仍发布。")
        return True, lines
    if event == "push":
        lines.append(
            "tag 触发带不了输入：先修好上面的问题，或用 workflow_dispatch(ref=<本 tag>, publish=true, "
            'ack_diag_contract="<原因>") 签字后发布。'
        )
    else:
        lines.append("确要发布：在 workflow_dispatch 的 ack_diag_contract 里写明原因。")
    return False, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True, type=Path, help="待发布 SHA 的 engine/diagsend.py")
    ap.add_argument("--print-url", action="store_true", help="只打印线上契约的 URL")
    ap.add_argument("--contract", type=Path, help="curl 取回的线上契约（取不到就是空文件）")
    ap.add_argument("--ack", default="", help="workflow_dispatch 的 ack_diag_contract 输入")
    ap.add_argument("--event", default="workflow_dispatch", help="github.event_name")
    args = ap.parse_args(argv)

    client, url = client_facts(args.client.read_text(encoding="utf-8"))
    if args.print_url:
        print(url)
        return 0
    if args.contract is None:
        ap.error("--contract 必填（除非 --print-url）")
    raw = args.contract.read_text(encoding="utf-8") if args.contract.exists() else ""
    ok, lines = check(client, raw, args.ack, args.event)
    text = "\n".join([f"线上契约：{url}", *lines])
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### 诊断包条目门禁\n\n" + text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
