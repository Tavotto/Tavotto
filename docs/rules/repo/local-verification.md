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
- **验证结论取自命令本身的退出码（错题本 L-002 升级，2026-11-03）。** 不靠管道末端、`grep -c`、
  `cmd || echo 0` 或分号串联得出「过了」；要串联就 `cmd; echo rc=$?` 单独看，管道里开 `set -o pipefail`；
  提交 / 推送只接在退出码判过的那一步后面。为什么：ruff 改了输出格式后 `grep -c` 恒为 0，六个分支报绿；
  `cmd || echo 0` 是追加不是替换；`测试 | grep … && git commit` 在测试红时照样提交推送
  （#454、#528、#539、#820、#1255、#1325）。循环里先 `rc=$?` 存下再打印：`echo "… $(cmd2) … $?"`
  的命令替换先执行、把 `$?` 重置成 0，五个变异全显示绿（2026-11-03，#1652）。
- **子进程与假脚本显式钉 UTF-8，判据量字节而非父进程解码结果（错题本 L-010 升级，2026-11-06）。**
  捕获文本用 `encoding="utf-8"`，不用仅有 `text=True` 的平台默认编码；假脚本读写中文材料也显式给编码。
  为什么：Windows 默认 cp1252 把破折号写成 0x97，父进程解码失败使 stderr 变 None；假脚本按默认编码读中文
  JSON，10 条用例在合并组连红五轮（#253、#725）。11-06 导出脚本用例再漏显式编码，被源码规范扫描拦下（#1790）。
- **变基或合并零冲突不等于对（错题本 L-004 升级，2026-11-05）。** 重放后先数提交数、diff 关键文件，再扫顶层
  重名、读双方 hunk 查语义交互。为什么：`rebase --onto` 起点误写成 HEAD，重放范围为空，10 个提交全部脱落而 git
  报成功（#177、#186）。
- **文档与发行说明的每句断言，指得出兑现它的那行代码（错题本 L-008 升级，2026-11-05）。** 写进文档的命令在目标
  存在与否两个分支各跑一次；发行说明在同版最后一个相关改动合入后再核。为什么：「每个附件都可溯源」而 provenance
  只覆盖两类；文档里的 `mv` 在目标已存在时静默嵌套；先写好的发行说明被同版后落地的改动推翻（三个不同日子，
  2026-08-22 至 09-04，迁入时未留 PR 号）。
- **worktree 里 `PYTHONPATH` 用绝对路径并探针确认来源；main 对照用独立干净树（错题本 L-040 升级，2026-11-05）。**
  为什么：相对路径在起子进程换 cwd 后失效，退回主工作区旧 main；进程内用例正常、起应用的用例是空对照，本机绿
  CI 红，几个子代理的「通过」全得重跑（#599、#595、#1162）。
- **入队前本地跑全量，别只跑相关测试子集（错题本 L-044 升级，2026-11-05）。** 子代理也一样：它只跑自列目录时，
  由主会话补全量。为什么：子集约 1100 条全绿，CI 全量加多平台一跑红九条，源码规范、既有保证被误伤、Windows
  布局都在子集外；子代理还报过确定性失败的「已过」；2026-11-03 子代理只跑自列文件，三处登记到 full-ci 才红
  （#820、#1639、#1655、#1671、#1733、#1757）。
- **合 main、改 base 或并行开 PR 时，先核 ADR 号再推（错题本 L-006 升级，2026-11-05）。** 取号按目录最大号加 1，
  并发 PR 会拿到同一个号，`tests/test_docs_references.py::test_adr_numbers_are_unique` 报重复。改号只替换本 PR
  新增的行。为什么：三个不同日子撞过号；10-31 三个 PR 都用了 0154，合入后 main 红，修复 PR 排在队尾，7.5 小时
  连踢 12 次（#294、#1034、#1548、#1564、#1580）。
