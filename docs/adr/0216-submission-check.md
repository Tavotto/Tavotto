# ADR 0216：投稿核对——整份稿件按期刊核对、一键改、带原文截图的核对报告（功能键 `submission-pack`）

日期：2026-11-27 · 状态：**Proposed（只定契约与边界；实现分期见 §八）**
**1.0 收敛纪律：新增产品能力；维护者 2026-11-27 给豁免（「6.豁免」），登记在 `docs/1.0-release-readiness.md` §1.8。入口跟 [0193](0193-paid-edition-license.md) 的上线开关走。**
相关：[0193 高级版授权](0193-paid-edition-license.md)（功能键闭集、「付费只能拦新功能」）、[0155 期刊标准库](0155-journal-catalog.md)（事实、语气、出处截图、「需要你确认」）、
[0180 改到期刊合规](0180-fix-to-journal.md) / [0080 规范修正事务](0080-spec-fix-transaction.md)（单图修正）、[0192 Agent 工具面](0192-agent-tool-surface.md)（`tavotto_make_journal_ready`）、
[0068 产物核验](0068-artifact-inspector-and-manifest.md)、[0031 统一导出](0031-unified-export-pipeline.md)、[0199 一键排版](0199-quick-layout.md)、[0206 修改方案卡](0206-change-plan-card.md)；
`docs/rules/frontend/export-pipeline.md`、`docs/rules/frontend/validation-and-problem-navigation.md`、`docs/rules/plugin/fix-to-journal.md`。

## 问题

维护者 2026-11-27 问「哪些内置能力简单整合就能给用户 wow、做成高级版」。投一次稿，作者要翻作者指南、量栏宽、改字号、调分辨率、逐张导出、改文件名——今天 Tavotto 每一步都有，但散在问题面板、导出对话框、出处卡和 MCP 工具里，而且**一次只管一张图或一块画布**。

### 已经免费的能力（不许拦，0193 §一）

| 能力 | 出处 |
| --- | --- |
| 按期刊检查（问题面板、出处卡原文截图、「需要你确认」） | ADR 0155、`preflight.runSpec` → 问题面板 |
| 单块画布「全部修复」 | `applyIssueFixes`（只修当前画布、`safe_auto`） |
| 单张图一步改到期刊合规 | `/api/engine/specfix`、MCP `tavotto_fix_to_journal` / `tavotto_make_journal_ready`（ADR 0180 / 0192） |
| 按原图批量导出、导出初值跟着期刊走 | `exportStore.beginExportBatch`、`lib/journalExport.ts` |
| 导出产物核验 | ADR 0068，`inspectionState()` |
| 版面预览（期刊样张） | `ExportManuscriptPreview` |

**高级版只收这三样新东西**（维护者 2026-11-27「就按这个来」）：

1. **跨画布一步串起**：整份稿件的所有画布按同一本期刊检查 → 修 → 导出 → 核验，一次做完；
2. **统一编号命名，存进一个投稿文件夹**；
3. **投稿核对报告**：逐条列出期刊要求、原文（有截图就放截图）、这次量到什么、改了什么、哪些作者确认过。

上面表里的能力照旧免费、不加任何判断；投稿核对把它们当零件调用。

## 决定

### 一、单位、名字与入口（维护者 2026-11-27 拍板）

- **一块画布（排版）= 一张 Figure**。画布按项目里的顺序编号为 Figure 1…N；多子图的组合图就是一块画布。
- 功能名 **「投稿核对」**；报告叫「投稿核对报告」。界面不用「合规」「保证」这类说法。
- 入口：导出对话框页脚左侧空着的槽位（不撑大对话框）一颗 ghost 按钮「投稿核对…」，命令面板一条。没有授权时照 `PremiumLock`（一键排版的写法）置灰，悬停一句话 +「了解高级版 ›」，不弹窗。

### 二、流程（一个对话框换正文，不是几个对话框接力）

1. **起步**：期刊（默认取项目已绑定的那本；没绑定时只突出「选一本期刊」，下面的图变淡）、各画布缩略图与编号、一行「文件」摘要（命名 Figure 1 / Fig1 / 原名，格式 PDF / TIFF / PNG）。主按钮「开始」。
2. **核对中**：清单（`TaskChecklist`）五步——对照作者指南、调整图宽和字号、导出、量一遍导出的文件、写核对报告；对话框外框描边进度（`EdgeProgress`），不出百分比。
3. **结果**：左列各张图（宽度变化、要选的、出错的），右列「对照某刊作者指南里关于图的要求」：
   - 上面是**引擎判过的**（打勾 / 等你选）；
   - 下面是 **「需要你确认（N）」**：引擎不判、或原文带条件的那些。**每条能改的有一颗按钮**（如「改成 7 pt」「换成 Arial」「加宽到 80 mm」），点了照原文改、行变成勾、左列对应的图跟着变；组头「全部处理」= 能改的都改、改不了的记「看过了」；全部处理后组头变「需要你确认的都处理了」。
   - 「需要你确认」**不挡保存**；没处理的在报告里照实写「需要你确认」。
   - 每条右侧「原文」：弹出期刊原网页的高亮截图（没截图就是原话 + 来源 + 抓取日期），与出处卡同一个样子。
4. **核对报告**（对话框内预览）→ **保存到文件夹…** → **存好**（各图缩略图、文件夹路径、文件清单，报告单列一行）。

### 三、改动留在项目里（维护者 2026-11-27「留」）

修正（图宽、字号、字体……）写进对应画布的文档，一次 commit、一步撤销；导出的文件与项目一致，不出现「交出去的图和项目里的不一样」。

### 四、表达纪律（数据撑不起的不说）

- 只有原文写了 must / required 才标「必须」，写了 should / recommended 才标「建议」；Wiley 一类没有情态词的（v2 大量 `unspecified`）什么都不标。
- **「符合」只给引擎真判过的**；期刊库里只是展示项（`info.*`、v2 的 `text` / 名单 / 带条件的事实）一律进「需要你确认」。
- 一键改的按钮只在**有结构化数值**时出现；期刊库里只有原话的项（如只写了「preferably Helvetica or Arial」、没有字体名单）只能「看过了」，要先在期刊库补成结构化事实，按钮才出现。
- 按钮的目标值取期刊库里**所有**带数值的条目，不只取预检判过的那些。不折进预检的数值（`affects: info`）也算，例如 Nature 的「最大 7 pt」：它早已存成数值，只是原文写的是 should，`qualifier` 记为 `recommended`，所以预检不判（同句的 5 pt 下限是 `advice`，只给建议，不进判据）。一键改只在这个对话框里读这些值，**不改预检的折叠规则**，免费预检的结论一条不变。
- 同一份指南里互相牵制的条目不能各改各的：Nature 的 7 pt 上限原文是「all other text」，分图标签另有「8 pt bold」。「改成 7 pt」要跳过分图标签；期刊库里没有能区分它们的结构化事实时，这颗按钮不出现。
- 不承诺做不到的：没有 CMYK 导出（只出 RGB，期刊要 CMYK 时进「需要你确认」）；文件命名只有原文一句话时，按用户选的命名起名并在报告里引原文，不说「已按要求命名」；产物核验没核到的项（`unknown`）报告里写「没核到」，不写「已核验」。
- 报告页脚一句：「这份报告只说明 Tavotto 量了什么、改了什么；以期刊最新的作者指南为准。」

### 五、失败与兜底（所有终态）

| 情况 | 表现 |
| --- | --- |
| 没绑期刊 | 起步页只突出选期刊，「开始」灰着 |
| 某张图栏宽不唯一（`column_choice_required`） | 那张图下两颗按钮「单栏 89 mm」「双栏 183 mm」；没选之前「保存」灰着、悬停说先给图 N 选栏宽 |
| 某张图准备不了（脚本出错、草稿带不回） | 那张图变灰、写「脚本运行出错」、旁边「打开脚本」；说明一句「图 N 的脚本运行出错，这次先不带它，其余照常保存」；保存的文件夹里没有它，报告写「没导出：脚本运行出错」 |
| 期刊要求引擎都判不了 | 标题「文件准备好了」，说明一句「这本期刊的要求还没法自动判断，对着原文看一遍」，全部进「需要你确认」 |
| 没授权 | 入口置灰（§一） |
| 中途停止 | 「停止」回到起步页；已写进项目的改动可撤销 |

### 六、出机数据

无新增。期刊事实与截图走现有期刊库（ADR 0155）；修正与导出全在本机。授权校验走 0193 既有通道。

### 七、功能键

0193 §一 闭集新增 `submission-pack`。授权判断只在「投稿核对」这个新入口与它的编排端点上；被它调用的检查、修正、导出、核验照旧无判断。随 §八.2 进闭集（端点要判权益），不等前端。

### 八、实现分期

1. 期刊库：先 Nature / Springer Nature、Elsevier 头部刊，补「一键改」要用、而今天只有原话或整条没有的结构化事实：栏宽（`page_columns`）、字体名单（`font_family.latin_accepted`），以及分图标签与正文字号的区分。字号上下限多数已有数值（见 §四），不用补。补进期刊库的条目照原文记语气，和现有条目一样走复核与签发。
2. **引擎编排与闭源计算**（维护者 2026-11-28 定：「要求 + 量到的数值 → 状态与目标值」这一块放闭源组件，其余开源，见 §九）：
   - 开源端点 `POST /api/pro/submission-check/plan`，**只算不写**（照 `/api/engine/specfix` 与 0199）。逐画布按 B0 渲染、取 manifest、跑免费预检，再把量到的数值交给组件命令 `submission_check.plan`；按组件回的操作逐画布在 worker 里渲染候选、复用 specfix 的回滚与核对。回每块画布的最终全量覆盖与逐条要求的状态。
   - **不设 `apply` 端点**：前端拿到结果后，所有画布的改动一次 commit、一步撤销（§三）。导出走 0031，核验走 0068，报告 PDF 由开源侧按结果生成。
   - 逐画布渲染并发有上限，每个 worker 在整个事务里由 `engine_pool.hold` 护着，不让四张冷启动的图互相挤掉（#2341）。
   - 功能键 `submission-pack` **随这一期**进 `engine/license.py` 闭集、`tests/test_license_callsites.py` 登记表与前端同源处（原 §八.4 提前：端点要判权益）。
3. 前端对话框（原型在本地分支 `proto/submission-ready`，含私有期刊截图，不推）；入口与 `PremiumLock`。
4. 闭源组件（私有仓库 `Tavotto/pro`）实现 `submission_check.plan`；开源仓库的示例扩展 `tests/fixtures/pro_echo/` 加同名命令的最小实现，契约测试不需要闭源代码。

### 九、组件命令 `submission_check.plan`（0198 §三：只给数字与结构）

**开源侧先把要求合成一张表**，组件只看这张表与量到的数值：

- **一条要求一行**：同一本刊在 v1 与 v2 都有时按项合并、**v2 优先**（0155 §14，与预检一致）。v2 直接读已验签的刊包事实，不读 `fold()` 的折叠结果：`fold()` 不列 `interval`，`accepted` 阶段会显示成 `any`。
- 每行带 `source_state`：`preflight`（免费预检真判过）、`advice`（只给建议，例如 Nature 的 5 pt 下限）、`confirm`（v1 `info` 或 v2 进「需要你确认」的）。**只有 `preflight` 才能显示「判过」。**
- 引文、截图、来源链接只留在开源侧，按 `ref` 对回去；组件拿不到。
- 每行另有一个**稳定 id**（开源侧用，不发给组件）：v2 是 `v2:<fact_id>`，v1 是 `v1:<规则路径>`，栏宽再加 `#<column>`。
- **逐条一键改**：端点请求带 `accept: [稳定 id…]`。
  - 第一次核对时 `accept` 为空，开源侧只落 `source_state = preflight` 那些行的操作，对应流程里的「调整图宽和字号」。
  - 「需要你确认」里每条的按钮，以及组头「全部处理」，都是再调一次端点，把那几条的稳定 id 加进 `accept`。组件照样算，开源侧只多落这几条的操作。
  - 结果仍由前端一次 commit；每点一次就多一步撤销。

```json
{"cmd": "submission_check.plan", "payload": {
  "requirements": [{
    "ref": "r1", "check": "font_size_max", "relation": "lte", "value": 7, "unit": "pt",
    "strength": "recommended", "stage": "production", "source_state": "confirm",
    "conditions": {"text_role": "other_text"}, "has_condition_text": false
  }],
  "canvases": [{
    "ref": "c1", "width_mm": 81.3, "height_mm": 60.0, "width_adjustable": true,
    "preflight": [{"check": "page-width", "verdict": "column_choice_required", "options_mm": [89, 183]}],
    "texts": [{"ref": "t1", "role": "tick-labels", "size_pt": 6.0, "family": "DejaVu Sans", "panel_label": false}],
    "panel_labels_known": true,
    "min_data_line_pt": 0.75,
    "rasters": [{"ref": "i1", "dpi": 420}]
  }]
}}
```

- `check` 是闭集：`width_final`、`font_size_min`、`font_size_max`、`font_family`、`raster_dpi_min`、`line_width_min`（与 ADR 0218 §一 A 档同名）。不认的检查项只回 `confirm`，不给操作。
- `texts` 只给角色、字号、字体名、「是不是分图标签」与 `editable`，**不给文字内容**。
  - 画布上的文字（手加的子图标签、文字框）只在排版文档里，引擎的 manifest 里没有。所以由前端连同标签判断一起送来，`editable = false`：只量不改，操作不许指向它们。`panel_label` 来自已有的识别：画布上的子图标签（`isSubLabel`）与脚本画的子图标签（`quicklayoutfig.analyze`，只认一个字母、从 a 起连续的那种）。`panel_labels_known` = 这块画布上每个疑似子图标签都认出了归属；做不到就是 `false`。
- `ref` 都是本次请求现编的短号，映射只留在开源侧。

**组件返回**：每条要求一个状态，加上能一键改的操作。

```json
{"requirements": [{"ref": "r1", "state": "fixable", "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}]}],
 "ops": [{"ref": "o1", "op": "text.size", "canvas": "c1", "texts": ["t1"], "size_pt": 7}]}
```

- `state` 闭集：`pass`（只许落在 `source_state = preflight` 的行上）、`choose`（如栏宽要选）、`fixable`、`confirm`、`not_applicable`。
- 操作闭集：
  - `canvas.width`：`canvas, width_mm`。只许落在 `width_adjustable = true` 的画布上（画布上只有一张图、图铺满画布）：落成那张图的 `figure.size_mm` 与画布页宽，与拖边改图幅同一批键。一块画布上有几张图时改宽度是一键排版的事（0199），这条只回 `choose` 或 `confirm`；
  - `text.size`：`canvas, texts, size_pt`，落成对应元素的 `fontsize` 覆盖；
  - `text.family`：`canvas, texts, family`，`family` 必须取自那条要求的名单。
  - 字号与字体两类操作由开源侧**交给免费的 specfix 事务落地**（`specfix.transaction`，只修 `texts` 对应的元素，目标值写进这次事务用的规范）：层级、回滚、真实渲染裁决都照单图修正走，不另写一份改字号的逻辑。specfix 没修成的那条退回 `confirm`。
- **分图标签不能一起改**：这本刊有 `text_role = panel_label` 的要求时，`text.size` 不许带 `panel_label = true` 的文字；画布 `panel_labels_known = false` 时，这块画布上那条 `font_size_max` 只回 `confirm`。
- 开源侧逐条校验：`ref` 是本次发出去的、数值有限、字号在 1–200 pt、图幅不越过 2000 mm、字体在名单里、`pass` 只落在判过的行上、操作不指向 `editable = false` 的文字。
- 落地后再核一遍：specfix 实际改动的元素必须都在这条操作的 `texts` 里，并且没有分图标签。改完重新量，用量到的值对照要求判状态，不信组件给的 `size_pt`。图宽改动先落、重新量，再算字号（有效字号 = 字号 × 缩放）。**任何一条不过，整份结果作废**，按「这次没法一键改」处理，所有要求退回 `confirm`，文档不动。未知的 op 或 state 同样整份作废。
- 组件不在、版本旧、授权没过：入口按 0198 §九 置灰；端点回 `{ok: false, code}`，不改文档。

## 后果

- 用户：投一次稿从「几小时翻指南 + 逐张导出」变成一次核对；报告给作者自己留档、也可发给合作者。
- 免费用户能力一样不少；高级版只多了「整份稿件一步做完」与报告。
- 需要期刊库补结构化事实，否则一键改的按钮少；这是数据工作，不是代码判断。

## 未决

- 报告的语言：跟界面语言，还是固定英文方便发给外方合作者（实现期再问）。
- 投稿阶段 vs 终稿阶段：期刊两阶段要求不同时，按 ADR 0155 的「录用标准优先」取；是否要在起步页给一个切换，等真实用户反馈。
