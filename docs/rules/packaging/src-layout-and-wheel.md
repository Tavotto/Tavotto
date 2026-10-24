# 打包与启动（src layout，2026-08-16）

> 原文出自 `packaging/AGENTS.md`「打包与启动（src layout，2026-08-16）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `packaging/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：`src/tavotto/web/` 靠 artifacts 收回否则 wheel 无界面；wheel/sdist 不含 `src-tauri/`、`workerd/`、`codex-plugin/`、`runtime/` 等（pyproject exclude 为准）；入口是纯标准库轻量入口，不再有 `python app.py`

<!-- agents-route
sheet: packaging/AGENTS.md
order: 10
改到: `pyproject.toml`、`run.sh`、`scripts/build_frontend.py`、`[tool.hatch.build]` 的 artifacts / exclude、extras
主题: 打包与启动（src layout）、wheel / sdist 卫生 → `src-layout-and-wheel.md`
必守要点: `src/tavotto/web/` 靠 artifacts 收回否则 wheel 无界面；wheel/sdist 不含 `src-tauri/`、`workerd/`、`codex-plugin/` 等
-->

- 代码在 `src/tavotto/`，`pyproject.toml`（hatchling）声明依赖与
  `tavotto = "tavotto.cli_entry:main"` 入口（**纯标准库的轻量入口**：
  `open`/`doctor` 要在 import Flask 之前分派掉，见 `src/tavotto/AGENTS.md`
  的「外部交接」）。`run.sh` = 自建 `.venv` + `pip install -e .` + 批准字体（`fetch_fonts.py --check`，缺了才取，取不到就停）+
  `exec .venv/bin/tavotto`；**不要再写 `python app.py`**，
  根目录已无该文件（旧进程内存里的老路径正是「worker 进程崩溃（无响应）」的成因）。
- extras：`worker`（matplotlib/numpy，装了就用同解释器渲染）、`dev`（pytest/build + 经自引用 extra 带进测试读取器）、`legacy-pymupdf`（退役的 PyMuPDF，只给测试读取器与维护者资产脚本，绝不进 dependencies，ADR 0072）。
- 前端产物 `src/tavotto/web/` 由 `scripts/build_frontend.py` 从 `web/dist` 拷入，
  进 .gitignore；hatchling 默认跳过 VCS 忽略的文件，**必须靠 pyproject 的
  `[tool.hatch.build] artifacts` 收回**，否则 wheel 里没有界面（首页 404）。
  开发态包内无 `web/` 时 `app.py` 自动回退到 `web/dist`。
- CI 的 package job 看护这条链路：build_frontend → wheel → 断言含
  `tavotto/web/index.html` + entry point → 干净 venv 装 wheel 跑 `tavotto --help`。
- 打包卫生：wheel/sdist 不含 `src-tauri/`、`workerd/`、`codex-plugin/`、
  `.agents/`、`services/telemetry_proxy/`、仓库根 `runtime/`（pyproject 的
  exclude 是权威，多处 pytest 看护）。
