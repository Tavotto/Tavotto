"""ADR 0094「覆盖率路线」2(b)：图例整体字号走源码层精修——就地改 / 新增 legend(...) 的 fontsize=。

对每个带图例的用例：热态 = 一次性 worker 应用 `legend.fontsize`；新脚本 = 把脚本里**唯一**那处
`.legend(...)` 调用的 `fontsize=` 改掉（没有就加上），不插钩子块、不带 override；用 verify 同一把尺比。

可改的前提（判不了就不改）：文件里恰好一处 `.legend(` 调用、不在循环 / 推导式里、没有 `prop=`
（`prop` 与 `fontsize` 同给时 matplotlib 以 prop 为准）。改动只动那几个字节，其余原样。

用法：PYTHONPATH=<源码> python legend_ast.py [case ...]   （写 results_legend_ast.json）
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_spike  # noqa: E402

from tavotto.engine import pool  # noqa: E402

_LOOPS = (
    ast.For,
    ast.While,
    ast.AsyncFor,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class NotEditable(Exception):
    pass


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for kid in ast.iter_child_nodes(node):
            out[kid] = node
    return out


def _offset(lines: list[bytes], lineno: int, col: int) -> int:
    """ast 的 (行, 列) → 字节偏移。ast 的列是 UTF-8 字节列。"""
    return sum(len(ln) for ln in lines[: lineno - 1]) + col


def edit_legend_fontsize(src: bytes, value: float) -> tuple[bytes, str]:
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "legend"
    ]
    if len(calls) != 1:
        raise NotEditable(f"脚本里有 {len(calls)} 处 .legend( 调用，认不出是哪一处")
    call = calls[0]
    parents = _parents(tree)
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, _LOOPS):
            raise NotEditable(f"{call.lineno} 行的 legend() 在循环里，改了会连带别的图例")
    if any(k.arg == "prop" for k in call.keywords):
        raise NotEditable("legend() 带了 prop=，fontsize 不生效")
    if any(k.arg is None for k in call.keywords):
        raise NotEditable("legend() 带了 **kwargs，fontsize 可能来自那里")
    lines = src.splitlines(keepends=True)
    kw = next((k for k in call.keywords if k.arg == "fontsize"), None)
    if kw is not None:
        a = _offset(lines, kw.value.lineno, kw.value.col_offset)
        b = _offset(lines, kw.value.end_lineno, kw.value.end_col_offset)
        how = f"{call.lineno} 行改 fontsize={src[a:b].decode()} → {value!r}"
        return src[:a] + repr(value).encode() + src[b:], how
    last = (call.args + call.keywords)[-1] if (call.args or call.keywords) else None
    if last is None:
        at = _offset(lines, call.end_lineno, call.end_col_offset) - 1
        ins = f"fontsize={value!r}"
    else:
        at = _offset(lines, last.end_lineno, last.end_col_offset)
        ins = f", fontsize={value!r}"
    return src[:at] + ins.encode() + src[at:], f"{call.lineno} 行新增 fontsize={value!r}"


def run_case(name: str) -> dict:
    d, script, entry, stem = run_spike.CASES[name]
    figs = HERE / d
    src = (figs / script).read_bytes()
    out = {"case": name, "results": []}
    hot = pool.one_shot(script, str(figs), entry)
    try:
        hot.override(stem, [])
        base = run_spike._manifest(hot, stem)
        picks = [
            p
            for c, p in run_spike.pick_patches(base)
            if p["prop"] == "fontsize" and p["gid"].endswith(".legend")
        ]
        for p in picks:
            row = {"patch": f"{p['gid']}.{p['prop']}={p['value']}"}
            try:
                new_src, how = edit_legend_fontsize(src, p["value"])
            except NotEditable as exc:
                row.update(verdict="not_editable", reason=str(exc))
                out["results"].append(row)
                continue
            row["edit"] = how
            hot.override(stem, [p])
            m_hot = run_spike._manifest(hot, stem)
            spike_name = f"__tvspike_ast_{script}"
            (figs / spike_name).write_bytes(new_src)
            fresh = pool.one_shot(spike_name, str(figs), entry)
            try:
                fresh.override(stem, [])
                m_fresh = run_spike._manifest(fresh, stem)
                cmp = run_spike.compare(hot, fresh, stem, m_hot, m_fresh)
            finally:
                pool.discard(fresh)
                (figs / spike_name).unlink()
            row.update(cmp)
            row["verdict"] = "pass" if cmp["geometry_ok"] and cmp["pixels_ok"] else "reject"
            out["results"].append(row)
    finally:
        pool.discard(hot)
    return out


def main(argv):
    names = argv or [
        "fig1_kinetics",
        "fig2_correlation",
        "pg_kinetics",
        "pg_calibration",
        "user_a",
        "user_b",
    ]
    res = []
    for n in names:
        r = run_case(n)
        for row in r["results"]:
            print(f"{n:18} {row['verdict']:12} {row.get('edit') or row.get('reason')}", flush=True)
        res.append(r)
    (HERE / "results_legend_ast.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
