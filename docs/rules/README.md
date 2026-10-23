# docs/rules/ — 各层规则的全文（按主题、按需读）

这里放的是 `AGENTS.md` 里**规则的全文**。2026-09-17 之前它们整段住在
`src/tavotto/AGENTS.md`（143.9 KB）与 `web/AGENTS.md`（147.8 KB）里，而 Codex
沿「仓库根 → cwd」拼接项目指令、默认 `project_doc_max_bytes` 只有 32 KiB
（[Codex 文档](https://developers.openai.com/codex/guides/agents-md/)）——两份文件
在自动加载的指令里从来没被完整读到过；Claude Code 按 `CLAUDE.md` 的指示整读，
每个后端会话为规则付出十几万字节。治理的做法是**分层 + 索引 + 按需读取**，
不是删规则。

## 结构

```text
CLAUDE.md                       入口，只指向根 AGENTS.md
AGENTS.md                       每次会话都读：任务路由 / 跨仓库不变量 / 验证入口 / 索引
src/tavotto/AGENTS.md           后端速查表：本层不可破坏 + 「改到 → 主题 → 必守要点」（看护用例在各细则头部）
web/AGENTS.md                   前端速查表：同上
.github/AGENTS.md               CI / 发布 / 验证链速查表：同上（2026-09-18 纳入）
codex-plugin/AGENTS.md          插件 / 技能 / MCP server 速查表：同上（2026-09-25 纳入，#608）
docs/rules/repo/                跨仓库：同源对总表、判据的主语、第三路径审查
docs/rules/backend/<主题>.md    后端各主题的全文（33 份，各 1–34 KB）
docs/rules/frontend/<主题>.md   前端各主题的全文（30 份，各 2–14 KB）
docs/rules/ci/<主题>.md         CI / 发布 / 验证链各主题的全文（13 份，各 1–7 KB）
docs/rules/plugin/<主题>.md     插件各主题的全文（13 份，各 1–8 KB）
docs/adr/                       架构决策（细则里点名哪份就读哪份）
```

读法：改哪一带，就在那一层速查表里按「改到」列找到主题，读那一份细则与它点名的
ADR。**不要求每次全读**——细则文件是给「正在改这一带」的人的。

## 三条纪律

1. **规则的全文只在细则文件里。** 速查表的「必守要点」是索引，不是第二份权威；
   改规则改细则（含头部 `agents-route` 块，速查表那一行由它生成）。细则头部写着它从哪份 `AGENTS.md` 的哪一节
   迁出，读的人由此知道该回哪里改。
2. **每份细则都要被速查表点名。** 加载路径是「速查表 → 细则」，没被点名的细则
   等于不存在。`tests/test_agents_rules_index.py` 钉着：细则 ↔ 速查表一一对应，
   速查表与细则里引用的仓库路径 / 用例文件 / ADR 都真的在。
3. **体积按实际加载路径量；试行预算只报告，硬线三条：Codex 自动拼接 ≤ 32 KiB、每份常驻指令
   不越过自己的棘轮上限、速查表每格 ≤ 300 B（后两条见下文「棘轮」）。**
   `python scripts/dev/agents_budget.py` 打印每份速查表、每条代表任务的加载链、以及
   Codex 从每一个有 `AGENTS.md`（git 跟踪的，含 `.agents/` 这类隐藏目录）的目录开工时拼出来的那一串是否越过 32 KiB。
   试行预算（不是行业标准）：根约 4–6 KB、单个速查表约 6–10 KB、常见任务的
   指导链约 16–24 KB——超标先报告，不为达标粗暴删规则。原始字节不是 token 数，
   也不代表任何账户的实际扣费。
   拼接体积一律按 **CRLF 口径**量（换行规范成 LF 后取「LF 字节数 + 行数」，#935）：Windows
   检出是 CRLF、每行多 1 字节，只在 Windows 重腿见红会让本地与 Linux 分片看不到。
   **32 KiB 那条是门禁**（2026-09-25 起，#608；此前也只报告）：它不是本项目定的预算，
   而是 Codex 默认 `project_doc_max_bytes` 的上限，越过了末尾的规则被**静默截掉**，没有
   任何信号——#608 量到后端、插件两串一直在被截，web 超了 41 B，而「只报告」的那份
   报告没人每次跑。看护 `tests/test_agents_rules_index.py`
   （`test_codex_auto_concatenation_stays_under_the_default_cap`）与
   `agents_budget.py --check`。红了的解法是把速查表里写成全文的要点迁进细则、速查表那一格
   只留索引，**不是删规则**。

## 棘轮（2026-10-23）

#608 之后只有 32 KiB 一条硬线，速查表又一路涨回去：两条拼接链余量一度只剩 72 B，每次都是撞墙
之后临时「腾字」；三份速查表 60 天里被 207 个提交改过，大头是每个功能 PR 往「改到」「看护」两格
追加文件名与用例名——表格占了文件八成，真正的规则（「必守要点」）只占四分之一。参照
gstack（`test/context-budget-ratchet.test.ts` + 一份上限 fixture）改成棘轮：

- **「看护」列整列迁进细则头部引用块**的「看护用例」一行（原文照搬；放头部不放末尾，是因为功能 PR 加规则多半追加在末尾，放末尾会和每个在飞 PR 冲突）。速查表只剩
  「改到 → 主题 → 必守要点」三格。
- **每格 ≤ 300 B**（`SHEET_CELL_MAX`，约 100 个汉字）。「改到」写目录与入口文件、写不下就写
  「（完整清单见细则）」；「必守要点」写一句索引。压缩前的原文逐字在细则头部引用块里。
- **每份常驻指令有自己的上限**：`tests/fixtures/agents_ceilings.json` 登记每份跟踪的
  `AGENTS.md` 与 `SKILL.md`（技能触发时整份进上下文，同样是常驻成本）。要长大就在同一个 PR 里
  `python scripts/dev/agents_budget.py --write-ceilings` 并提交那份 JSON、PR 正文写明理由；
  缩小了也重写一次，把上限降下来。上限沿每条拼接链加起来也不许越过 32 KiB——棘轮本身不能被
  「按规矩」推过硬线。
- **路由表由细则生成，不再手写**：四份速查表（`src/tavotto/` / `web/` / `.github/` /
  `codex-plugin/`）里「改到 | 主题 | 必守要点」那张表，每一行住在对应细则**头部**（出处引用块之后）的一个
  `<!-- agents-route … -->` 块里（键：`sheet` / `order` / `改到` / `主题` / `必守要点`；一份细则
  可有多个块，分别路由到不同速查表），`scripts/dev/gen_agents_sheets.py --write` 重写速查表里
  BEGIN/END GENERATED 两行标记之间的整段。功能 PR 只改自己那份细则，不再碰速查表——207 个提交
  改同一张表的冲突就此消失（参照 gstack 的 SKILL.md 由 .tmpl 生成 + 检查生成物没过期）。
  `order` 只决定行序，同一 sheet 内不得重复；插新行取个空档。
- 看护：`tests/test_agents_rules_index.py`（`test_every_resident_instruction_stays_under_its_ceiling`、
  `test_ceilings_along_every_codex_chain_fit_the_default_cap`、`test_sheet_rows_are_three_short_cells`、
  `test_generated_route_tables_are_not_stale`、`test_agents_route_blocks_are_well_formed`）
  与 `agents_budget.py --check`（含 `gen_agents_sheets.py --check`）。

仍高于试行预算的：根 `AGENTS.md`（约 10 KB，每一串都带）、`packaging/AGENTS.md`（约 19 KB）、
`src-tauri/AGENTS.md`（约 15 KB）。后两份没有路由表、主要是打包 / 签名 / 验收的操作步骤，下一步
按需加载的 skill 是更合适的载体；在那之前它们同样被上限钉住，不再长。

## 加一条规则

- 已有主题：写进那份细则（保留「规则 + 为什么 + 看护用例」的写法，这是本仓库
  规则能活下来的原因），再看细则头部 `agents-route` 块的「必守要点」要不要更新，改完跑
  `python scripts/dev/gen_agents_sheets.py --write`（**不要手改速查表里的表**）；新的看护用例写进细则头部的「看护用例」一行。
  「必守要点」只写一两句索引；想在那一格写全文，就是该写进细则的信号（#608）。
- 新主题：在对应层目录新建 `<slug>.md`，头部照既有文件写出处、写「看护用例」一行，再写一个
  `agents-route` 块（`sheet` / `order` 取同表内没用过的空档 / 改到 / 主题 / 必守要点，每格 ≤ 300 B），
  跑 `gen_agents_sheets.py --write` 生成速查表那一行。不加块的话「每份细则都被速查表点名」会红。
- 跨仓库的同源对：加进 `repo/same-origin-pairs.md` 那张表，并在对应层速查表的
  细则头部的「看护用例」里点名它的对拍用例。
