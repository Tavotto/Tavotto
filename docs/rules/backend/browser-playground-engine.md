# 浏览器 playground（引擎侧）

> 原文出自 `src/tavotto/AGENTS.md`「浏览器 playground（引擎侧）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

`engine/browser.py` 平铺 import `manifest/overrides/pathgeom/patchspec` 与
`figcapture`（**语义与捕获策略都只有一份实现**，与 worker.py 同一条 sys.path
纪律，不许出现 browser_manifest.py 这类分叉）。engine.zip 的模块白名单在
`scripts/build_browser_playground.py` 的 `ENGINE_FILES`：**加一个 flat import
就得同步加进去**。完整的 playground 纪律（Pyodide、完整性校验、案例库、
预热）在 `web/AGENTS.md` 与 `docs/adr/0007-browser-playground.md` /
`docs/adr/0011-playground-examples-first.md`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 平铺 import 与 worker 同一条 sys.path 纪律、不许分叉出 browser_manifest
- 加一个 flat import 必须同步 `ENGINE_FILES` 白名单
