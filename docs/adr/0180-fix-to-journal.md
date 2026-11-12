# ADR 0180：Agent 一步改到期刊合规——`tavotto_fix_to_journal`，复用桌面那份修复事务，不新写判据

- 状态：**已接受**（2026-11-11，产品负责人裁决：「四个都按推荐走」，含 1.0 豁免；§1.8 登记行随第一个实现 PR 提交，行位接在 #1862「wheel 自带 MCP 服务端」之后）
- 相关：[0080 「问题」面板的修复事务](0080-spec-fix-transaction.md)（本 ADR 的核心：把它接给 Agent）、
  [0051 保留式规范化](0051-preserving-normalization.md)（改图幅用它）、[0155 内置期刊库](0155-journal-catalog.md)（`journal_id` 的解析）、
  [0006 Codex MCP](0006-codex-mcp-app-and-publication-profile.md)、[0078 会话跨进程恢复](0078-mcp-session-survives-server-process.md)、
  `docs/rules/plugin/normalize-figure.md`、`docs/rules/plugin/result-budget-and-session-state.md`、`docs/rules/plugin/export-preflight-and-inspection.md`

## 背景

产品负责人 2026-11-11 要求「Agent 化」：Agent 自己把图收尾到能投稿，人只做最后的审美确认；先写这份 ADR 再动手。

**现在 Agent 想把一张图改到「符合 Nature」，要走这条路：**

1. `tavotto_list_journals` 查 id → `tavotto_preflight(journal_id)` 拿到问题列表；
2. 读问题里的 gid，自己算新值，拼 `tavotto_apply_overrides`；
3. 再 preflight，看有没有修好、有没有改坏别处；循环；
4. `tavotto_export`。

第 2 步就是 ADR 0080 在桌面端已经废掉的「逐条算、盲写」：Agent 只看得到一条规则，算不出「刻度字大了以后轴标题还在不在图幅里」、「改完层级倒没倒」、「这台机器有没有这个字体」。桌面端当年复现到的就是这几种：刻度被提得比轴标题还大、改字号把文字挤出图外、字体一条都没修（ADR 0080 §问题）；Agent 拿着同样的信息，只会犯同样的错。

**而该有的部件仓库里都已经有了**，只是没有接给 Agent：

| 部件 | 在哪 | 现在谁能用 |
| --- | --- | --- |
| 判据（哪里违规） | `engine/preflight.run()` | 桌面 / HTTP / MCP 都能用 |
| 面板内部的修复计划 + 真实渲染裁决 + 外边距重排 | `engine/specfix.py` + `app._specfix_transaction()` | **只有桌面**（`POST /api/engine/specfix`） |
| 改图幅 / 最小字号，保留原图 | `engine/normalize.py` + `bridge.normalize_figure` | MCP（`tavotto_normalize_figure`） |
| 期刊规则 | `journal_id` → 中性底规范 + 期刊覆盖 | MCP 各工具都认 |

## 决定

### 一、只加一个工具：`tavotto_fix_to_journal`

**不另做 `check_journal`。** 只读的检查就是 `tavotto_preflight(journal_id=…)`，再做一个只读检查工具等于给同一个判据开第二个入口（违反单一权威）。缺的是「每条问题能不能自动修、怎么修」，这一点改在 preflight 的**结果形状**上（见 §三），不新增工具。

`tavotto_fix_to_journal` 的入参：

| 参数 | 说明 |
| --- | --- |
| `session_id` | 必填 |
| `journal_id` / `profile_id` / `journal` | 三选一，语义同其它工具（ADR 0155 §9） |
| `column` | 目标栏位 id（如 `single` / `double` / 期刊自有栏位 id），或直接给 `width_mm`；**都不给时见 §二第 1 步** |
| `only` | `[{rule, gid?}]`，只修点名的问题；不给 = 「全部处理」的集合（不含建议档，与桌面 `batchable()` 同一口径） |
| `include_suggestions` | 默认 false；true 时建议档也修（与桌面组头「全部修复」同义） |
| `formats` / `dpi` / `stem` / `out_dir` | 给了就在通过后导出（走 `tavotto_export` 同一条预检门 + 产物核验）；不给只改会话、不出文件 |
| `previews` | 默认 true：结果里附改前 / 改后两张预览图（§四） |
| `dry_run` | 默认 false；true 时完整跑一遍事务、返回 diff 与预览，最后回到 B0、不出文件 |

### 二、事务：两段，各用一个现成的算子，整体要么提交要么回到 B0

```
B0（会话此刻的全量 override 列表）
 │
 ├─ 1. 图幅：页宽不是期刊栏宽时
 │     · 给了 column / width_mm → 用它
 │     · 没给、且只有一个栏位与现宽相差 ≤ 15%  → 取那个栏位（结果里写明「按现宽取了单栏」）
 │     · 没给、且有两个以上候选                → 不改任何东西，exit=column_choice_required，
 │                                               列出候选栏位与各自宽度，让 Agent 问用户
 │     → normalize 事务，只给 width_mm（高度按原长宽比；page-aspect 超限时再给 height_mm 夹到上限；
       不给 min_font_pt，字号留给第 2 段）
 │
 ├─ 2. 面板内部：specfix 事务（ADR 0080 原样），基准是第 1 段的结果
 │     字号按角色层级抬、字体换规范字体、线宽吸档、刻度朝向 / 图例边框 / 封闭轴、出界用外边距重排
 │
 ├─ 3. 复检：preflight 再跑一次，问题分 fixed / remaining / introduced 三档
 │     introduced 里有 error 或 warn → 整体不通过（与 0080 「修复引入的 warn 也挡」同一条）
 │
 └─ 4. 给了 formats 就导出；导出被预检挡或产物核验不过 → 整体不通过
通过：会话停在最终列表，落盘一次（ADR 0078）。不通过：render(B0)，会话与文件零改动。
```

- **每个量只有一个算子**：图幅只由 normalize 改，字号 / 字体 / 线宽只由 specfix 改。第 1 段刻意不给 `min_font_pt`，免得两个算子各算一次字号。
- **回滚是否干净**照 ADR 0080：事务里任何一次渲染带 warning 或抛了，这个会话的 worker 作废、下次调用按全量列表重放，结果带 `worker_retired: true`。MCP 的 worker 是会话自己起的，作废没有 native 那层顾虑；`tavotto run` 的 native 会话不经 MCP，不涉及。
- **只改会话，不改用户的 .py**（与 normalize / apply 相同）。

### 三、`tavotto_preflight` 的结果里，每条问题多一个 `fix` 字段

只在 MCP 的结果整形层加（`server.py` / `bridge.py`），**不改 `preflight.run()` 的输出**——那份输出与前端求值器靠 golden vectors 对齐，加字段会动到同源对。

| `fix.kind` | 判据（唯一出处） | 给 Agent 的意思 |
| --- | --- | --- |
| `auto` | 规则在 `specfix.FIXABLE_RULES` 里 | `tavotto_fix_to_journal` 能修 |
| `size` | `page-width` / `page-aspect` | `tavotto_fix_to_journal` 能修；`fix.columns` 列出可选栏位 |
| `manual` | 其余（配色、数据、子图结构……） | 只能人或 Agent 改脚本；`fix.hint` 一句话说改什么 |
| `verify` | `not_verifiable` 档 | 需要人工确认，工具无法判断 |

**不给原始 override 补丁。** 外部分析建议「每条问题附一个可直接 apply 的补丁」，这里不采纳：没经过渲染裁决的补丁，就是 ADR 0080 废掉的盲写。想看会改什么，用 `tavotto_fix_to_journal(dry_run=true)`：完整跑一遍事务，返回 diff，最后回到 B0。

### 四、结果形状（给 Agent 看，也给评测用）

```jsonc
{
  "ok": true,
  "exit": "done",               // done / nothing_to_do / column_choice_required / font_unavailable /
                                // constraint_conflict / not_resolved / acceptance_failed / unsupported
  "journal": {"id": "nature", "catalog_rev": "…"},
  "size": {"from_mm": [120, 90], "to_mm": [89, 66.8], "column": "single"},
  "changes": [{"gid": "ax0.xtick", "prop": "fontsize", "from_pt": 6, "to_pt": 7, "rule": "font-too-small"}],
  "adjustments": [{"kind": "margins", "detail": "…"}],
  "issues": {"fixed": 7, "remaining": [...], "introduced": []},   // remaining 每条带 §三 的 fix
  "skipped": [{"rule": "font-family-substituted", "gid": "…", "reason": "font_unavailable", "want": "Arial"}],
  "exported": [{"format": "pdf", "path": "…", "inspection": {...}}],
  "worker_retired": false
}
```

- 预览图走 MCP 的 `image` 内容块，长边 1000 px、各一张。不放进 `structuredContent`，以免撑爆内嵌画布的事件上限（ADR 0069）。`previews:false` 时不渲染、不附图。
- `changes` 用页面上读者量到的 pt / mm（与问题面板同一口径），不用脚本坐标系的数字。
- `issues` 前后计数这一块，就是「Agent 单独做 vs Agent + Tavotto」评测要读的数字，不用另做插桩。

### 五、代码怎么挪

1. `app._specfix_transaction(render, base, scale, profile, only)` 原样搬进 `engine/specfix.transaction()`：它本来就只依赖一个 `render` 回调，纯标准库。Flask 端点和 bridge 都调这一份。这一步是**逐字搬家**，单独一个 PR、`risk:low`，分发表与现有 `tests/test_specfix_real.py` 全部不改即过。
2. `bridge.fix_to_journal()`：拼 §二 的两段。`render` 回调用会话热 worker 的 `_render`（与 normalize 同一个）。
3. `server.py`：注册工具；preflight 结果加 `fix`。
4. 技能 `tavotto-figure/SKILL.md` 和 `tavotto_guide`：用户点名期刊时，先 `fix_to_journal`，`column_choice_required` 就问用户要单栏还是双栏，剩下的 `manual` 再改脚本。

### 六、不做的

- **不加只读的 `check_journal`**（理由见 §一）。
- **不放宽任何判据**，也不按期刊临时加特例：期刊之间的差异全部来自 `journal_id` 解析出的规范。
- **不缩字号**来塞进栏宽。这和 0080 一样：放不下就报 `no_fit`，让用户决定。
- **画布标注（canvas 层）与多图排版不在范围内**。这个工具只管单张图的会话；排版页的合规检查以后另写 ADR。
- **不做远程 / 托管版**：与「渲染、排版、导出都在你的机器上」的承诺冲突，要做另写 ADR。

## 裁决记录（2026-11-11，四项均按推荐）

1. **不另做 `check_journal`**：检查仍是 `tavotto_preflight(journal_id=…)`，结果每条问题加 `fix`（§三）。
2. **栏位自动选择**：只有一个栏位与现宽相差 ≤ 15% 时自动取它；否则 `column_choice_required`，由 Agent 问用户。
3. **预览图默认附带**：改前 / 改后各一张，长边 1000 px，走 `image` 内容块；`previews:false` 关掉。
4. **1.0 豁免已给**。§1.8 登记行：「Agent 一步改到期刊合规：`tavotto_fix_to_journal` 复用桌面修复事务 + 保留式规范化改图幅；preflight 结果带 `fix` 分类」；开关跟期刊库总开关走，关着时工具不出现、preflight 结果不带 `fix`。

## 实现顺序

1. 逐字搬家：`app._specfix_transaction` → `engine/specfix.transaction()`（`risk:low`，现有用例不改即过）。
2. `bridge.fix_to_journal()` + 工具注册 + preflight 结果的 `fix` 字段 + §1.8 登记（`risk:high`：插件 / MCP）。
3. 技能 `tavotto-figure/SKILL.md` 与 `tavotto_guide` 的使用说明（首次使用契约，`risk:normal`）。

## 看护（实现 PR 里加）

- `tests/test_specfix_real.py` 原有用例在搬家后逐字不改即过（证明 §五第 1 步是纯搬家）。
- `tests/test_mcp_fix_to_journal.py`（真 matplotlib）：
  - 宽 120 mm 的图 → 单栏合规且层级不倒挂；
  - 两个候选栏位 → `column_choice_required`，会话零改动；
  - 缺字体 → 其余照修，`skipped` 里写明要的字体；
  - 引入 warn → 整体回到 B0；
  - 导出被挡 → 回到 B0、不出文件；
  - `dry_run` → diff 与真跑一致，会话回到 B0；
  - 回滚带 warning → `worker_retired`。
- 同源对：`fix.kind == "auto"` 的规则集合 == `specfix.FIXABLE_RULES`（按结构读，不写第二份清单）。
- preflight 加 `fix` 后，`preflight.run()` 的 golden vectors 逐字节不变。
