"""e2e 用：替插件往项目排版里加一批标注（`web/e2e/agent-merge.spec.ts`，ADR 0208 §五）。

`python tests/support/e2e_agent_annotate.py <项目目录> <排版名>`：读场景 → 出方案 → 写入，与插件同一条引擎路
（`engine/layoutannotate`），只是子图不量（e2e 里没有起 worker）——锚点都锚在子图框上，不需要量。
数据目录取环境变量 `TAVOTTO_DATA_DIR`（与被测后端同一个：撤销留存要落在后端看得到的地方）。stdout 最后一行是 JSON。
"""

from __future__ import annotations

import json
import sys

from tavotto.engine import layoutannotate as la


def _no_measure(panel, requests):
    raise la.PanelUnavailable("no_script")


def main(project: str, name: str) -> None:
    scene = la.read_scene(project, name, _no_measure)
    items = [
        {
            "kind": "arrow",
            "name": "callout",
            "from": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [12, -4]},
            "to": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [-6, 8]},
        },
        {
            "kind": "text",
            "name": "note",
            "text": "Agent note",
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
    main(sys.argv[1], sys.argv[2])
