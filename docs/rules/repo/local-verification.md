# 本地验证：按改动补跑的那几步

> 原文出自根 `AGENTS.md`「最常用验证」（2026-10-24 根文件瘦身时迁出，正文逐字未改）。
> 这里是这一主题的**唯一全文**；根 `AGENTS.md` 只留一句索引。改规则改这里，并同步那一句。

- **改完 Python 先过 Ruff，再跑针对性 pytest，最后才是完整验证。** 提交前那条
  与 CI 那一格逐字相同；`--unsafe-fixes` 会动语义，要逐条看过再用。规则集在
  `pyproject.toml` 的 `[tool.ruff]`，细节与取舍在 `docs/ci/ruff.md`。示例图库 /
  playground 示例 / CompatBench 语料**不参与格式化**。**Ruff 不替代任何语义门禁。**
- **新增 `sys.path` 源码根要同步审查 `[tool.ruff]` 的 `src`**，否则平铺 import 被判成第三方
  （`docs/rules/ci/ruff-lint-lane.md`）。
- 改了 `src/tavotto/rendercore/` 里字体 / 字形归属相关的东西、或换了批准字体集合
  （`rendercore/fonts_allowlist.json`，先改 ADR 0060）：`python scripts/gen_canvas_coverage.py --write`
  与 `python scripts/gen_glyph_plan_vectors.py --write`——那张覆盖表是前端「这个字
  导出后是不是方框」的唯一依据。字体文件不进 git：跑用例 / 打包前先 `python scripts/fetch_fonts.py`。
- 改了 `web/src` 或引擎四模块（manifest/overrides/pathgeom/patchspec）：
  playground 产物 `python scripts/build_browser_playground.py`（网站仓库提交它，
  `--check` 防漂移）；Codex 画布 `python scripts/build_mcp_widget.py` **只为本地
  试用**——它不进 git（ADR 0043），CI 从每次 checkout 现建并验证。
- 引擎改动后重启服务：`lsof -ti:5089 -sTCP:LISTEN | xargs kill; ./run.sh --no-browser`。
- 完整验证链（CompatBench / 等价性矩阵 / 不变式 / nightly / E2E / 性能基线）
  见 `docs/rules/ci/verification-chain.md`（`.github/AGENTS.md` 是速查表）。指导文档自身的门禁：`tests/test_agents_rules_index.py`
  （速查表 ↔ 细则一一对应、引用都在、每份指令的棘轮上限、速查表每格 ≤ 300 B），
  体积：`python scripts/dev/agents_budget.py --check`。
