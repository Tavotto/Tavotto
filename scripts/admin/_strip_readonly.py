#!/usr/bin/env python3
"""把 ruleset 的 GET 响应剥成可以 PUT 回去的请求体。

**GET 的响应不是 PUT 的请求体。** 存档里带着一堆只读字段，原样送回去
会被 API 拒收 —— 而那发生在最需要它成功的时刻：某人刚把 ruleset 改坏，
正要回滚。`docs/admin/github-ruleset-changes.md` §2.1 写下了这条判断，
而 `apply_rulesets.sh --restore` 从前直接 PUT 存档文件，与它自相矛盾。

单独成文件而不是内联进 shell：命令替换里再套单引号的 python 代码，
bash 的解析当场就乱了（实测 `unbound variable`）。一段要用引号的逻辑
就该有自己的文件。
"""
import json
import sys

#: 服务端生成、PUT 时不接受的字段。
READ_ONLY = ("_links", "id", "node_id", "created_at", "updated_at",
             "source", "source_type", "current_user_can_bypass")


def strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in READ_ONLY}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：_strip_readonly.py <ruleset-*.json>", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as fh:
        doc = json.load(fh)
    json.dump(strip(doc), sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
