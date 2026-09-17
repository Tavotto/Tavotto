# 进程与依赖边界（重要）

> 原文出自 `src/tavotto/AGENTS.md`「进程与依赖边界（重要）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- Flask 跑在 `.venv`（只有 flask + pymupdf，**没有 matplotlib**）。
  `engine/registry.py`、`engine/pool.py`、`engine/ai_bridge.py`、`engine/config.py`、
  `engine/updater.py`、`engine/runtime.py`、`engine/project_refresh.py`、
  `engine/project_watch.py`、`engine/readiness.py` 被 Flask import，
  **必须保持纯标准库**。
- `engine/worker.py`、`engine/manifest.py`、`engine/overrides.py`、
  `engine/figsession.py`、`engine/wireproto.py`、`engine/preview_complexity.py`
  只在执行侧子进程里跑，解释器由 `pool.find_worker_python()` 探测
  （需科学栈；可用 `TAVOTTO_WORKER_PYTHON` 覆盖）。
- `engine/bridge_runner.py` 与 `engine/bridgeboot.py` 跑在**用户自己的解释器**里
  （native bridge，ADR 0020）：**纯标准库、必须在 3.10 上跑得起来、启动阶段
  绝不 import matplotlib**——用户环境的版本我们说了不算，而提前 import
  matplotlib 会抢走用户脚本对 backend 的决定权。
- 运行时可写数据一律走 `engine/config.data_dir()`（`TAVOTTO_DATA_DIR` 可覆盖，
  conftest 已全局隔离）：cache / layouts / exports / baked_overrides/&lt;项目id&gt;.json /
  ai_history.sqlite3 / ai_snapshots 全在那儿。**不要再往包目录或仓库根写东西**
  ——site-packages 不可写，装成 wheel 后会直接崩。
