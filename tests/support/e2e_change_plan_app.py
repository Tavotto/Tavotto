"""真浏览器 e2e 的被测进程入口（修改方案卡）：`python tests/support/e2e_change_plan_app.py --port … --figures …`。

与 `python -m tavotto` 唯一的区别：进程内把修改方案的**模型缝**换成假件（`engine/changeplan` 里本来就留给测试替换的
`_PRECHECK` / `_RESOLVE` / `_CALL`，pytest 里也是这么换的）——e2e 绝不真调模型。其余（真后端、真 worker 渲染 B0 / 候选、
真落盘、真前端）一个字不动。只在测试环境生效：生产入口 `tavotto` 不认这个文件，也不读下面的环境变量。

`TAVOTTO_E2E_PLAN_MODE`：
  * `ok`（默认）：模型回一份固定方案——把元素表里第一个带 `linewidth` 的元素的线宽改成 4（整条能改，没有「要改脚本」的部分：同一条指令不再既出卡又交脚本，ADR 0206 修订 2026-11-26）；
  * `log`：模型只把第一个带 `yscale` 的坐标轴的纵轴改成对数（不带下限）——方案卡要先问「下限一起改吗」；
  * `unsupported_sync`：同步预检就判「当前连接不支持结构化调用」（409，前端落回改脚本路径）；
  * `unsupported_thread`：预检放行，后台线程里解析连接才判出不支持（轮询得到 failed，前端同样落回改脚本路径）。

纯标准库 + tavotto；**不 import pytest**（CI 的 posix-e2e 没装）。
"""

import json
import os
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tavotto.cli_entry import main  # noqa: E402
from tavotto.engine import ai_structured, changeplan  # noqa: E402

MODE = os.environ.get("TAVOTTO_E2E_PLAN_MODE", "ok")
NEW_WIDTH = 4


def _fake_call(**kw):
    user = kw["user"]
    m = re.search(r"<elements>\n(.*)\n</elements>", user, re.S)
    elements = json.loads(m.group(1)) if m else []
    for el in elements:
        if MODE == "log":
            if any(p.get("prop") == "yscale" for p in el.get("props", [])):
                proposal = {
                    "groups": [
                        {
                            "comment_id": changeplan.ASSISTANT_COMMENT_ID,
                            "changes": [{"gid": el["gid"], "prop": "yscale", "value": "log"}],
                        }
                    ],
                    "unhandled": [],
                }
                return ai_structured.StructuredResult(True, value=kw["validate"](proposal))
            continue
        if any(p.get("prop") == "linewidth" for p in el.get("props", [])):
            proposal = {
                "groups": [
                    {
                        "comment_id": changeplan.ASSISTANT_COMMENT_ID,
                        "changes": [{"gid": el["gid"], "prop": "linewidth", "value": NEW_WIDTH}],
                    }
                ],
                "unhandled": [],
            }
            return ai_structured.StructuredResult(True, value=kw["validate"](proposal))
    return ai_structured.StructuredResult(False, reason="refused")


def apply() -> None:
    if MODE == "unsupported_sync":
        changeplan._PRECHECK = lambda conn_id=None: (False, "unsupported_connection")
    else:
        changeplan._PRECHECK = lambda conn_id=None: (True, "")
    if MODE == "unsupported_thread":
        changeplan._RESOLVE = lambda budget=None, **kw: (None, "unsupported_connection", "e2e")
    else:
        changeplan._RESOLVE = lambda budget=None, **kw: ({"kind": "e2e"}, "", "")
    changeplan._CALL = _fake_call


if __name__ == "__main__":
    apply()
    main()
