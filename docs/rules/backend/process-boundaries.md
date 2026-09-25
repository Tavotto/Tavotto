# 进程与依赖边界（重要）

> 原文出自 `src/tavotto/AGENTS.md`「进程与依赖边界（重要）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- Flask 跑在 `.venv`（flask + packaging + RenderCore 的五个包：pikepdf / fonttools / uharfbuzz / pypdfium2 / pillow，
  **没有 matplotlib**）。
  `engine/registry.py`、`engine/pool.py`、`engine/ai_bridge.py`、`engine/config.py`、
  `engine/updater.py`、`engine/runtime.py`、`engine/project_refresh.py`、
  `engine/project_watch.py`、`engine/readiness.py`、`engine/workdir.py`、
  `engine/databinding.py`、`engine/preparation.py` 被 Flask import，
  **必须保持纯标准库**。「纯标准库」挡的是科学栈（matplotlib / numpy 由 worker 解释器提供）；
  `pyproject.toml` 声明的运行时依赖（flask / packaging + RenderCore 的五个包，U10 起；ADR 0072）是父进程自己的
  ——`packaging` 自 U04（ADR 0061）起只在 `engine/depresolve.py` 的 intent 读法里延后 import，别处不许 import 它；
  pikepdf / pypdfium2 / uharfbuzz / fontTools / Pillow 只在 `rendercore/` 的 native 适配层里 import，PDFium 只在
  render child 进程里。PyMuPDF 不在闭包里（`scripts/ci/retirement_scan.py` 看护）。
- 渲染解释器由 `pool.resolve_worker_python(项目, script=…)` 决定（ADR 0018 / 0044 / 0057）：
  显式（环境变量 / 设置）> 项目记住的 > **项目自带的 venv（首开发现 + 体检，每进程每项目一次）**
  > 内置 / 自身 / 系统。**失效的显式选择不静默替换**：`explicit_python_unusable` /
  `project_python_unusable` 带 `explicit = {source, python, reason}`；只有自动记住的失效才作废并
  重新发现（`invalidated_decision()` 留记录）；用户明确选回默认链条记成 `projectenv.remember_default`。
  进程内缓存（全局选定的解释器、项目解释器的体检结论、首开发现结果）**不许替已经变了的环境作答**：
  全局那条命中前先查路径还在（删掉的设置解释器报 missing，QA ENV-03-B1）；项目那两份带
  `projectenv.interpreter_fingerprint`（解释器 lstat / stat + `pyvenv.cfg`，各取 inode / mtime / ctime / size / 权限位），
  同一路径被重建、被拿掉执行权限就重新体检（ENV-04-B1）。
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

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 三侧模块名单与各自允许的依赖
- 项目级解释器决策唯一出处 `pool.resolve_worker_python(项目, script=)`：显式 > 记住 > **项目 venv 首开发现**（ADR 0057）> 内置 / 自身 / 系统
- 失效的显式选择报 `explicit_python_unusable` / `project_python_unusable` 不静默替换
- 进程内缓存不替已经变了的环境作答（全局那条命中前查路径还在，项目体检结论与首开结果带 `projectenv.interpreter_fingerprint`，同一路径被重建 / 改权限就重验）
