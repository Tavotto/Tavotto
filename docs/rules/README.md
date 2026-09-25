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
src/tavotto/AGENTS.md           后端速查表：本层不可破坏 + 「改到 → 主题 → 必守要点 → 看护」
web/AGENTS.md                   前端速查表：同上
.github/AGENTS.md               CI / 发布 / 验证链速查表：同上（2026-09-18 纳入）
codex-plugin/AGENTS.md          插件 / 技能 / MCP server 速查表：同上（2026-09-25 纳入，#608）
docs/rules/repo/                跨仓库：同源对总表、判据的主语
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
   改规则改细则，再同步速查表那一行。细则头部写着它从哪份 `AGENTS.md` 的哪一节
   迁出，读的人由此知道该回哪里改。
2. **每份细则都要被速查表点名。** 加载路径是「速查表 → 细则」，没被点名的细则
   等于不存在。`tests/test_agents_rules_index.py` 钉着：细则 ↔ 速查表一一对应，
   速查表与细则里引用的仓库路径 / 用例文件 / ADR 都真的在。
3. **体积按实际加载路径量；试行预算只报告，唯一的硬线是 Codex 自动拼接 ≤ 32 KiB。**
   `python scripts/dev/agents_budget.py` 打印每份速查表、每条代表任务的加载链、以及
   Codex 从每一个有 `AGENTS.md`（git 跟踪的，含 `.agents/` 这类隐藏目录）的目录开工时拼出来的那一串是否越过 32 KiB。
   试行预算（不是行业标准）：根约 4–6 KB、单个速查表约 6–10 KB、常见任务的
   指导链约 16–24 KB——超标先报告，不为达标粗暴删规则。原始字节不是 token 数，
   也不代表任何账户的实际扣费。
   **32 KiB 那条是门禁**（2026-09-25 起，#608；此前也只报告）：它不是本项目定的预算，
   而是 Codex 默认 `project_doc_max_bytes` 的上限，越过了末尾的规则被**静默截掉**，没有
   任何信号——#608 量到后端、插件两串一直在被截，web 超了 41 B，而「只报告」的那份
   报告没人每次跑。看护 `tests/test_agents_rules_index.py`
   （`test_codex_auto_concatenation_stays_under_the_default_cap`）与
   `agents_budget.py --check`。红了的解法是把速查表里写成全文的要点迁进细则、速查表那一格
   只留索引，**不是删规则**。

## 预算例外（#608）

- **`src/tavotto/AGENTS.md`（约 18 KB）与 `web/AGENTS.md`（约 18 KB）高于 6–10 KB。** 两份
  各有 33 / 30 个主题，每行的「改到」路径与「看护」用例就是路由本身——再往细则里挪，速查表
  就没法按改动路径找到细则了；「必守要点」已收成一行索引，写成全文的原要点逐字迁进了对应
  细则的「速查表原要点」一节。硬线（拼上根之后 ≤ 32 KiB）留着约 5 KB 余量。
- **根 `AGENTS.md`（约 9 KB）高于 4–6 KB。** 它是每一串都带的那份，装的是任务路由与跨仓库
  不变量；#608 没有动它的正文（普通用户安装路由由 `tests/fixtures/codex_first_use_scenarios.json`
  逐字钉着）。要收它，另开一轮按同样的办法把「判据的主语」「最常用验证」里的全文迁进
  `docs/rules/repo/` 与 `docs/rules/ci/`。

## 加一条规则

- 已有主题：写进那份细则（保留「规则 + 为什么 + 看护用例」的写法，这是本仓库
  规则能活下来的原因），再看速查表那一行的「必守要点」「看护」要不要更新。
  「必守要点」只写一两句索引；想在那一格写全文，就是该写进细则的信号（#608）。
- 新主题：在对应层目录新建 `<slug>.md`，头部照既有文件写出处；在速查表加一行
  （改到 / 主题 / 必守要点 / 看护）。不加行的话门禁会红。
- 跨仓库的同源对：加进 `repo/same-origin-pairs.md` 那张表，并在对应层速查表的
  「看护」列点名它的对拍用例。
