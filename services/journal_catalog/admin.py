"""限流规则的管理命令行：`python -m services.journal_catalog.admin <命令>`（只读写 sqlite，不需要重启服务）。

  rules list                                  列出当前规则（默认值覆盖、网段例外、紧急开关）与最近的审计
  rules set <名> <整数>                       改全局默认值；名 ∈ ip_tokens_per_day / reads_per_hour / reads_per_day / ip_reads_per_day
  rules exempt-cidr <CIDR> [--tokens N] [--reads N] [--note 文字]
                                              该网段放宽到指定值；两个数都不给 = 完全豁免 IP 维度（设备维度照常）
  rules clear <名 | CIDR>                     清除一条默认值覆盖或一条网段例外（回到环境变量 / 内置默认）
  kill-switch on|off                          紧急开关：on = 所有 IP 维度限额一律不拦
  top [--n N]                                 当天不同设备数最多的 N 个出口（只显示 ip_key 前 8 位）与被拒次数

每次改动写 `rule_audit`（操作者取 `--by`，缺省 `$USER`；时间；改了什么）。审计里没有客户端 IP——
CIDR 是管理员写的网段规则，可以记。服务最多 `RULES_TTL_S`（10 秒）后读到改动。
库路径取 `--db` 或环境变量 `TAVOTTO_JC_DB`。
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time
from collections.abc import Callable, Mapping

from .core import RULES_TTL_S  # noqa: F401 - 文档里引用
from .store import RULE_NAMES, Store

# 太宽的网段等于关掉 IP 限额——那有紧急开关，不靠一条 /0
MIN_PREFIX = {4: 8, 6: 32}


class AdminError(Exception):
    pass


def parse_cidr(text: str) -> str:
    try:
        net = ipaddress.ip_network(text.strip(), strict=False)
    except ValueError as exc:
        raise AdminError(f"不是合法的 CIDR：{text}") from exc
    if net.prefixlen < MIN_PREFIX[net.version]:
        raise AdminError(
            f"网段太宽（IPv{net.version} 前缀至少 /{MIN_PREFIX[net.version]}）；要整体放开请用 kill-switch on"
        )
    return str(net)


def _positive(text: str) -> int:
    try:
        v = int(text)
    except ValueError as exc:
        raise AdminError(f"必须是整数：{text}") from exc
    if v < 1:
        raise AdminError("必须 >= 1")
    return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m services.journal_catalog.admin")
    ap.add_argument("--db", help="sqlite 路径（缺省取 TAVOTTO_JC_DB）")
    ap.add_argument("--by", help="操作者（缺省取 $USER）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rules = sub.add_parser("rules").add_subparsers(dest="sub", required=True)
    rules.add_parser("list")
    p = rules.add_parser("set")
    p.add_argument("name", choices=RULE_NAMES)
    p.add_argument("value")
    p = rules.add_parser("exempt-cidr")
    p.add_argument("cidr")
    p.add_argument("--tokens")
    p.add_argument("--reads")
    p.add_argument("--note", default="")
    p = rules.add_parser("clear")
    p.add_argument("target")
    p = sub.add_parser("kill-switch")
    p.add_argument("state", choices=("on", "off"))
    p = sub.add_parser("top")
    p.add_argument("--n", type=int, default=10)
    return ap


def run(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    out=None,
) -> int:
    env = os.environ if env is None else env
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    db = args.db or env.get("TAVOTTO_JC_DB")
    if not db:
        print("缺少 --db / TAVOTTO_JC_DB", file=sys.stderr)
        return 2
    actor = (args.by or env.get("USER") or "unknown").strip()[:64] or "unknown"
    store = Store(db)
    now = int(clock())
    try:
        if args.cmd == "rules" and args.sub == "list":
            r = store.load_rules()
            print(
                f"紧急开关: {'on（所有 IP 维度限额不拦）' if r['kill_switch'] else 'off'}", file=out
            )
            print("默认值覆盖:", file=out)
            for name in RULE_NAMES:
                print(
                    f"  {name} = {r['defaults'].get(name, '(未设，用环境变量 / 内置默认)')}",
                    file=out,
                )
            print("网段例外:", file=out)
            for cidr, exempt, tokens, reads in sorted(r["cidrs"]):
                what = "完全豁免 IP 维度" if exempt else f"tokens={tokens} reads={reads}"
                print(f"  {cidr}  {what}", file=out)
            print("最近审计:", file=out)
            for ts, who, action, detail in store.audit_log(10):
                print(
                    f"  {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts))}Z {who} {action} {detail}",
                    file=out,
                )
        elif args.cmd == "rules" and args.sub == "set":
            print(store.set_default(args.name, _positive(args.value), actor, now), file=out)
        elif args.cmd == "rules" and args.sub == "exempt-cidr":
            tokens = _positive(args.tokens) if args.tokens is not None else None
            reads = _positive(args.reads) if args.reads is not None else None
            exempt = tokens is None and reads is None
            print(
                store.set_cidr(parse_cidr(args.cidr), exempt, tokens, reads, args.note, actor, now),
                file=out,
            )
        elif args.cmd == "rules" and args.sub == "clear":
            if args.target in RULE_NAMES:
                print(store.clear_default(args.target, actor, now), file=out)
            else:
                print(store.clear_cidr(parse_cidr(args.target), actor, now), file=out)
        elif args.cmd == "kill-switch":
            print(store.set_kill_switch(args.state == "on", actor, now), file=out)
        elif args.cmd == "top":
            for key, devices, denied in store.top_ips(now, max(1, args.n)):
                den = " ".join(f"{c}={n}" for c, n in sorted(denied.items())) or "-"
                print(f"{key[:8]}  设备 {devices}  被拒 {den}", file=out)
    except AdminError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


def main() -> None:  # pragma: no cover
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    main()
