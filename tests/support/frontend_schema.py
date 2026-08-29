"""从 sanitize.ts 的 EVENT_SCHEMA 里逐事件抽字段名（大括号配对，不用正则啃结构）。

正则会把相邻条目的字段串到一起（实测 35 个事件只认出 20 个，还互相串味）。
配对扫描是这里唯一可靠的读法。
"""

import json
import re
import sys

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），而这个
# 探针把**带中文的 JSON** 打给父进程——第一次 print 就 UnicodeEncodeError，
# 退出码变成 1，于是所有用例只看得见「returned non-zero exit status 1」。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HELPERS = {
    "dragBegin": [
        "panel",
        "gid",
        "prop",
        "document_variant",
        "display_variant",
        "authority_variant",
        "exact_authority",
        "anchor_from_document",
    ],
    "dragCommit": [
        "panel",
        "gid",
        "prop",
        "patch_count",
        "document_variant",
        "authority_variant",
        "exact_authority",
    ],
    "previewEnd": ["session", "panel", "reason", "duration_ms"],
}
SPREADS = {
    "HISTORY_COUNTS": ["past_count", "future_count"],
    "VARIANT_TRIPLE": ["document_variant", "display_variant", "authority_variant"],
}


def block_at(text, i):
    """text[i] 必须是 '{'；返回配对的 '}' 之后的位置。"""
    depth = 0
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


def top_level_fields(block):
    """只取**本层**的 `name:`，跳过嵌套对象（FieldKind 的 {k, max, values}）。"""
    out, i, depth = [], 0, 0
    while i < len(block):
        ch = block[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            i += 1
            continue
        if depth == 1:
            m = re.match(r"([a-z][a-zA-Z0-9_]*)\s*:", block[i:])
            if m and (i == 0 or block[i - 1] in "{,\n \t"):
                out.append(m.group(1))
                i += m.end()
                continue
        i += 1
    for name, fields in SPREADS.items():
        if f"...{name}" in block:
            out.extend(fields)
    return sorted(set(out))


def extract(path):
    src = open(path, encoding="utf-8").read()
    start = src.index("export const EVENT_SCHEMA")
    body = src[start:]
    body = body[: body.index("\nfunction dragBegin")]
    table = {}
    for m in re.finditer(r"^  '([a-z_]+(?:\.[a-z_]+)+)':\s*", body, re.M):
        name = m.group(1)
        rest = body[m.end() :]
        helper = re.match(r"(dragBegin|dragCommit|previewEnd)\(\)", rest)
        if helper:
            table[name] = sorted(HELPERS[helper.group(1)])
            continue
        assert rest[0] == "{", (name, rest[:40])
        end = block_at(rest, 0)
        table[name] = top_level_fields(rest[:end])
    return table


if __name__ == "__main__":
    t = extract(sys.argv[1])
    print(len(t))
    print(json.dumps(t, indent=1, sort_keys=True))
