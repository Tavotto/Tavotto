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
  **唯一的口子（2026-11-11，用户「同意打进 wheel」，1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）**：
  `codex-plugin/mcp/tavotto_mcp/` 这**一个** Python 包，经 wheel 的
  `[tool.hatch.build.targets.wheel.force-include]` 映射成 wheel 顶层包 `tavotto_mcp`，让 pip / pipx / uvx 装的
  `tavotto mcp` 直接起服务（`engine/mcpserve.py` 先源码树、后已安装）。用 `force-include` 而不是
  `packages` / 放宽 exclude：全局 exclude 必须继续挡着 `codex-plugin/**`，`force-include` 只点名一个目录、
  不会把技能 / 启动器 / `.codex-plugin` / 画布产物 `widget/canvas.html` 带进来。**sdist 必须带同一个目录**
  （`[tool.hatch.build.targets.sdist.force-include]`）：`python -m build` 先 sdist 再从 sdist 构建 wheel，
  源目录缺席 wheel 构建直接失败。wheel 里没有画布产物，服务端按 `tavotto_mcp/widget.py` 的缺席语义降级
  （不挂 `_meta.ui`，工具照常）。看护：`tests/test_desktop_mcp_serve.py`（配置、wheel / sdist 成员、临时 venv 装 wheel 后
  `tavotto mcp` 握手 + 列工具且 stdout 首帧前零字节；CI `package` job 对刚打的 wheel 跑）。
