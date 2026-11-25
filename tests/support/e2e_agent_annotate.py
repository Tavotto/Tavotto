"""e2e 用：替插件往项目排版里加一批标注（`web/e2e/agent-merge.spec.ts`，ADR 0208 §五）。

`python tests/support/e2e_agent_annotate.py <项目目录> <排版名> [批次号]`（批次号把这一批往下挪，文字改成「Agent note <批次号>」）：读场景 → 出方案 → 写入，与插件同一条引擎路
（`engine/layoutannotate`），只是子图不量（e2e 里没有起 worker）——锚点都锚在子图框上，不需要量。
数据目录取环境变量 `TAVOTTO_DATA_DIR`（与被测后端同一个：撤销留存要落在后端看得到的地方）。stdout 最后一行是 JSON。
"""

from __future__ import annotations

import json
import sys

from tavotto.engine import layoutannotate as la


def _no_measure(panel, requests):
    raise la.PanelUnavailable("no_script")


def main(project: str, name: str, batch: int = 1) -> None:
    dy = (batch - 1) * 12.0
    scene = la.read_scene(project, name, _no_measure)
    items = [
        {
            "kind": "arrow",
            "name": "callout",
            "from": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [12, -4 + dy]},
            "to": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [-6, 8 + dy]},
        },
        {
            "kind": "text",
            "name": "note",
            "text": "Agent note" if batch == 1 else f"Agent note {batch}",
            "sizePt": 8,
            "at": {"anchor": {"item": {"name": "callout", "point": "ne"}}, "offset_mm": [1, 0]},
            "attach": "w",
        },
    ]
    plan = la.propose(project, name, items, _no_measure, base_revision=scene["base_revision"])
    if not plan["ok"]:
        raise SystemExit(json.dumps({"plan": plan}, ensure_ascii=False, default=str))
    out = la.commit(
        project,
        name,
        items,
        _no_measure,
        base_revision=scene["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    print(json.dumps({"revision": out["revision"], "added": out["added"]}))


if __name__ == "__main__":
    # 被 spawn 时 stdout 是管道，Windows 上会退回系统区域编码：钉 UTF-8（`tests/test_*` 的探针门禁）
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
