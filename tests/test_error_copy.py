"""导出与运行里最常见的几条中文报错不该漏出工程术语。

审计 T-009：用户看到的是「脚本执行超时（入口 main）」「没有捕获到任何 Figure」
「渲染失败（HTTP 500）」「作业仍可能完成」「已通过干净重放校验」「工作进程已重启」。
这里只钉这批 key，不对整份语言包下禁令（别处的「入口」「会话」可能是正当用法）。
插值参数一个不能少：前后端按参数名传值，改名要另行确认。
`export_render_failed` 的 `{{id}}` 换成 `{{name}}`（图名）是确认过的（后端同时补发 `name`）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZH = ROOT / "web" / "src" / "i18n" / "locales" / "zh-CN"

BANNED = re.compile(r"作业|HTTP|入口|Figure|重放|工作进程|会话期间|重渲染")

#: (文件, 点路径) → 必须保留的插值参数
KEYS: dict[tuple[str, str], set[str]] = {
    ("errors.json", "code.execution_timeout"): {"entry"},
    ("errors.json", "code.script_no_figure"): {"entry"},
    ("errors.json", "render.failed"): {"status"},
    ("errors.json", "code.export_render_failed"): {"name", "reason"},
    ("errors.json", "code.script_changed"): set(),
    ("errors.json", "code.worker_build_timeout"): set(),
    ("errors.json", "code.worker_timeout"): set(),
    ("dialogs.json", "exportJob.statusFailed"): set(),
    ("inspector.json", "writeBack.verified_other"): {"count"},
}


def _find(tree: dict, key: str) -> list[str]:
    """按末段 key 在整棵树里找（只认唯一命中），点路径的前缀只用来消歧。"""
    *prefix, leaf = key.split(".")
    hits: list[tuple[str, str]] = []

    def walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*path, k))
        elif isinstance(node, str) and path[-1] == leaf:
            hits.append((".".join(path), node))

    walk(tree, ())
    if len(hits) > 1 and prefix:
        hits = [h for h in hits if prefix[-1] in h[0].split(".")]
    return [v for _, v in hits]


def test_error_copy_has_no_engineering_terms_and_keeps_params() -> None:
    for (fn, key), params in KEYS.items():
        values = _find(json.loads((ZH / fn).read_text(encoding="utf-8")), key)
        assert len(values) == 1, f"{fn}:{key} 应唯一命中，实际 {len(values)}"
        text = values[0]
        assert not BANNED.search(text), f"{fn}:{key} 里有工程术语：{text}"
        assert set(re.findall(r"\{\{(\w+)\}\}", text)) == params, f"{fn}:{key} 插值参数变了：{text}"
