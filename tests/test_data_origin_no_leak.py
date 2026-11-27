"""`PanelObject.dataOrigin`（图是怎么从数据画出来的：表路径、列名、answers、规范）只许留在用户自己的项目文档里（ADR 0178）。

判据的主语：**每一条出机 / 出进程的通道，实际产出的字节里有没有那份来历的内容**——用一个独一无二的哨兵串放进来历的各个字段，
再到三条后端通道的产出里找：分享审阅的元素表、前端诊断快照的后端清洗、诊断包 zip。前端那几条（导出请求、诊断快照）在
`web/src/store/figureOriginPersist.test.tsx`。这些通道都是「按白名单拉取」而不是遍历输入的键，所以新字段天然进不去；
这里把「天然」钉成用例，将来有人改成透传整个面板时当场红。
"""

from __future__ import annotations

import io
import json
import zipfile

from tavotto.engine import diagnostics, diagnostics_frontend as dfe, reviewshare
from tests.test_diagnostics_bundle import _redact, snapshot

SENTINEL = "DATAORIGIN_SENTINEL_9f3a"


def origin() -> dict:
    return {
        "version": 1,
        "script": f"{SENTINEL}/fig.py",
        "file": {"path": f"{SENTINEL}/data.csv", "sheet": SENTINEL},
        "headerRow": 2,
        "answers": {"y": SENTINEL, "x": SENTINEL},
        "spec": {"name": SENTINEL},
        "fontRange": None,
        "identity": SENTINEL,
        "kind": "sd",
        "band": False,
        "fingerprint": SENTINEL,
        "dataSha256": "a" * 64,
    }


def test_review_share_element_table_does_not_carry_it():
    objects = [
        {
            "type": "panel",
            "object_id": "obj1",
            "x_mm": 0,
            "y_mm": 0,
            "w_mm": 80,
            "h_mm": 60,
            "id": "runtime:x#y",
            "dataOrigin": origin(),
            "data_origin": origin(),
        }
    ]
    out = reviewshare.layout_elements(210, 297, objects, {})
    assert out, "要有元素表才有意义"
    assert SENTINEL not in json.dumps(out, ensure_ascii=False)


def test_frontend_snapshot_sanitiser_drops_it():
    snap = snapshot()
    snap["panels"][0]["dataOrigin"] = origin()
    snap["panels"][0]["data_origin"] = SENTINEL
    snap["dataOrigin"] = origin()
    out = dfe.sanitize_snapshot(snap, _redact)
    assert out is not None and SENTINEL not in json.dumps(out, ensure_ascii=False)


def test_diagnostic_bundle_never_contains_it():
    snap = snapshot()
    snap["panels"][0]["dataOrigin"] = origin()
    snap["panels"][0]["data_origin"] = SENTINEL
    data = diagnostics.build_bundle(frontend={"frontend_state": snap, "interaction_trace": []})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    assert SENTINEL.encode() not in blob
