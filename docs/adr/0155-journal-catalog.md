# ADR 0155：内置期刊标准库——期刊 = 覆盖 + 出处，未规定的项不补

日期：2026-10-31 · 状态：**Accepted（PR 1：数据结构、双侧加载与校验、中性底 `journal-base-v1`；PR 2：选择器、出处卡、画布页宽跟栏；真实数据、出处巡检、远程目录另起 PR）**。
相关：[0006 版本化出版规范](0006-codex-mcp-app-and-publication-profile.md)、[0029 Style / Spec 与规范快照](0029-style-spec-profiles.md)；
规则全文 `docs/rules/backend/profiles-and-preflight.md`「内置期刊标准库」。
**1.0 豁免已由维护者 2026-10-31 登记（`docs/1.0-release-readiness.md` §1.8）。**

## 动机

用户想在应用里自选期刊，预检按该期刊的作图要求判，并且**每一条数值都能回到原文核对**。地基已经在：`publication.json` 是规则唯一权威、
期刊覆盖（`merge_journal` / `mergeJournalInto`）早已存在并带 `derived_from` / `journal` 进 proof report、项目里存规则快照（ADR 0029）、
两个求值器靠 golden 向量对齐。缺的是三样：期刊数据本身、每条数值的出处、看出处的界面。本 ADR 定前两样的**结构**（界面后续 PR）。

## 决定

### 1. 期刊库是一份独立目录，期刊 = 对中性底的一份覆盖 + 出处表

* 新文件 `src/tavotto/profiles/journals.json`（与 `publication.json` 同目录，随 wheel 与 PyInstaller `profiles/` 一起分发）。
  **不往 `publication.json` 里塞 30 份完整规范**（ADR 0029 不做继承链）；复用现有覆盖合并，求值器一行不改。
* 形状：`publishers{id: {label, sources{sid: {url, kind: html|pdf, retrieved, archive_url?, page?}}, rules{"<字段路径>": {value, src, quote, anchor?}}}}`，
  `journals{id: {label, publisher, issn?, aliases?, sources?, rules?}}`。同出版社共用的规则写在出版社上，期刊只记差异；
  合并时**出版社 rules 先、期刊 rules 后覆盖**同一路径，期刊可用自己的 `sources`，也可引用出版社的。
* 规则键直接是 `journal-base-v1` 的字段路径（`widths_mm.double`、`min_raster_dpi`、`severity.page-width` ……），按点路径折成嵌套的 `JournalOverride`。
  键必须**merge 能精确落到**：深合并子对象写 `<子对象>.<底里存在的键>`，其余只能是顶层单段键；身份 / 展示字段（`profile_id`、`version`、`label`、`source`、`listed`）不可覆盖。
* **`quote` 只存一两句短摘录**（避免版权问题、控制包大小）；原话一字不差，数值原文是区间 / 模糊说法时，值存成 profile 已有的形状，区间由读者在摘录里自己看。
* `anchor` 用浏览器标准 text fragment（`#:~:text=`），PDF 用 `page`（`#page=N`，只能跳页）；`archive_url` 存 Wayback 快照作兜底。
  `url` 本身不许带 `#`，链接由 `source_link` 拼出。

### 2. 期刊没写的项不补：覆盖叠在 `journal-base-v1` 上，不叠在课题组规范上

叠在 `lab-publication-v1` 上会出现「Nature 要求 Times New Roman」这种凭空的结论——课题组规范是课题组的。新增中性底 profile `journal-base-v1`
（`publication.json` 内）：无宽高比限制、不限定拉丁字体（`latin` 与 `latin_accepted` 为空即不判）、不要求中日韩回退、字号下限 0（同 ADR 0029 既有的
`absolute_min_font_size_pt = 0` 用法）、线宽预设与坐标轴 / 图例策略全空，页宽 / 比例 / 字体 / 线宽等规范类检查项降为 `suggestion`（期刊写了对应项后按下面的映射抬升）；只保留安全兜底
（`element-outside-figure` error、缺字形 / 公式排不出 warn、栅格 DPI 下限 300、字号上限 72 pt）。栏宽 `widths_mm.single / double` 为 `null`（没规定就是没规定，不放占位数字冒充事实；`_validate` 与 TS `validateSpecShape` 对栏宽放宽为「正数或 null」）。`severity` 表补齐全部检查 id（默认 `warn`，与 `DEFAULT_SEVERITY` 一致，行为不变），期刊才能对每个检查写规则。
期刊写了的项才会改变判据；`journal_sources()` 里没有的路径，界面标「该期刊未规定」。

**它不是一份可选规范**：profile 加 `"listed": false`，`list_profiles()` / `listProfiles()` / `builtinCatalog()` 过滤掉，规范下拉与 `profilestore` 的内置记录里都不出现。
**「可列出」与「可解析」是两件事**：`load()` / `loadProfile()` / `profilestore.resolve_spec()`（后端用 `profiles.all_profile_ids()` 判内置）照常取得到它；
前端 `resolveDocumentSpec` 对绑到隐藏内置底的文档不报 `globalMissing`（`specBinding.isHiddenBuiltin`）。

### 3. 加载时全量校验，失败码是闭集

`engine/journals.py` 与 `web/src/lib/journals.ts` 同名同义（严格同源对），加载时校验：每条 rule 有 `value`、有 `src` 且 `src` 在可见 sources 里、`quote` 非空、
键路径合法、`value` 的 JSON 类型与底里该字段一致，折叠后整份结果过 `validate_spec`（TS 侧 `validateSpecShape` 是 `_validate` 的镜像）。
失败码闭集（19 个，见 `journals.ERROR_CODES`，含 §6 的 `journal_rule_snapshot_invalid`）：`journal_catalog_schema` / `journal_source_invalid` / `journal_source_coverage` / `journal_publisher_unknown` / `journal_alias_conflict` / `journal_rule_value_missing` /
`journal_rule_src_missing` / `journal_rule_src_unknown` / `journal_rule_quote_empty` / `journal_rule_anchor_invalid` / `journal_rule_path_unknown` / `journal_rule_value_type` /
`journal_rule_qualifier_invalid` / `journal_rule_stage_invalid` / `journal_rule_mark_invalid` / `journal_rule_unset_invalid` / `journal_merge_invalid` / `journal_unknown`。校验在加载处做，所以**一条没出处的数值进不了库**——这是对「严禁凭记忆写数值」的结构性看护。

对外：`list_journals()`、`journal_override(id)`、`journal_profile(id)`、`journal_sources(id)`（字段 → value / quote / url + anchor / link / archive_url / retrieved，
给出处卡与 proof report）。TS 侧经新增的 `@journals` 别名读同一个 JSON（`vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` / `vite.playground.config.ts` / `tsconfig.app.json` 各配一次）。

### 3b. 一次性留位的 schema 扩展（审查后补入；PR 3 的数据不必重采）

* **顶层 `catalog_rev`**（正整数）：库版本；文档绑定里存 `journalRef: {id, catalogRev, retrieved}`，「有新版可同步」才有得比（绑定由后续 PR 写，本 PR 只提供 `catalog_rev()`）。
* **展示项 `info.<id>`**：与规则共用 `{value, src, quote, …}` 外壳，键在闭集 `INFO_IDS`（`one_half_mm`、`max_height_mm`、`width_range_mm`、`width_range_single_mm`、`width_range_double_mm`、
  `width_single_mm`、`width_double_mm`、`min_width_mm`、`min_line_width_pt`、`dpi_by_kind`、`dpi_photo`、`dpi_halftone`、`dpi_combination`、`color_mode`、`formats_raw`、`formats_images_raw`、
  `font_names_raw`、`text_height_mm`、`panel_label`），值类型自由，同样强制 `src` / `quote`，**不折进 override、不过 `validate_spec`**。`journal_sources()` 每条带 `affects: "preflight" | "info"`。
  以后求值器支持某字段时，从 info 搬到 rules，不用重采。加新 id = 改两侧同名常量 + 本 ADR，向量里的 `constants` 会比。
* **每条规则的可选字段**：`qualifier`（`exact|max|min|typical|range|recommended`，缺省 exact）与 `value_alt`（如 Nature 1.5 栏 120 或 136）；`stage`（`submission|production|any`，缺省 any）；
  `mark`（quote 里要高亮的原文片段，必须是 quote 的子串，前端不猜）与 `derivation`（如「8.5 cm → 85 mm」）。
  求值器按「等于」判的路径（`widths_mm.single/double`、`allowed_aspect_ratios`、`line_widths_pt`）**只收 `exact`**；「约 / 通常 / 最大 / 区间」的宽度进 `info.width_*`，页宽判据扩成区间另起 PR 并加 preflight golden。
* **来源 `coverage`**：`publisher_general | journal_specific | single_journal_sample`（出版社 source 缺省 publisher_general，期刊 source 缺省 journal_specific）。**`single_journal_sample` 的来源不得被出版社 rules 引用**（失败码 `journal_source_coverage`），
  只能挂在对应期刊 rules 上——单刊页的数字不会被当成全家默认。
* **`unset`**：期刊规则写 `"unset": true`（不带 value，仍须 `src` / `quote`），表示「本刊不适用出版社的这一条」；只允许写在期刊上，且出版社必须有这一条。折叠时该路径不进 override；`journal_sources()` 仍列出它（`unset: true`）。
* **`journal_sources()` 的归属**：`rule_scope`（值是出版社还是期刊声明的）与 `source_scope`（出处挂在谁身上）分开——期刊自己的规则可以引用出版社的出处；期刊改写了出版社的值时 `overrides` 带出版社原条目（「出版社默认 90，该刊改为 85」）。
* **严重度随规则抬升**：期刊填了值的检查，按固定映射 `SEVERITY_BUMP`（`widths_mm.*→page-width`、`allowed_aspect_ratios→page-aspect`、`min_effective_font_size_pt→font-too-small`、`max_font_size_pt→font-too-large`、`min_raster_dpi→raster-dpi`、
  `line_widths_pt→line-width-off-preset`、`font_family.latin_accepted→font-family-substituted`、`axis_policy.* / legend_policy.* / text_weight_policy / cjk_fallback.required` 对应各检查）把 `severity.<检查>` 从 `suggestion` 提到 `warn`；
  **只抬不降，期刊显式写了 `severity.<检查>` 的以它为准**，抬升结果写进 `journal_override()`（自带、可审计）。否则「按该期刊预检」在页宽 / 比例 / 线宽上永远只是建议。
* **折叠规则（qualifier 决定进不进预检）**：预检路径上只有 `exact`，以及**方向相符**的 `min`（下限类字段：`min_effective_font_size_pt`、`absolute_min_font_size_pt`、`min_raster_dpi`、`legend_policy.min_font_size_pt`）/ `max`（上限类字段：`max_font_size_pt`、`legend_policy.max_font_size_pt`）才折进 override
  （`QUALIFIER_DIRECTION`，两侧同表）；`typical` / `recommended` / `range`、方向不符的 `min`/`max`、没有方向的路径上的 `min`/`max` 一律**不折**，只留在 `journal_sources()`（`affects: "info"`）。
  求值器不看 qualifier，所以「约 7 pt」「推荐 Helvetica」不能被钉成硬判据。不折的规则仍须 `src` / `quote`，出处卡照常显示。
* **来源 `journal` 字段**：出版社 sources 里 `single_journal_sample` 的来源必须写明所属期刊 `journal`（且该期刊属于这个出版社），只有那一本期刊的 rules 能引用，别的期刊引用报 `journal_source_coverage`；`journal` 只能写在 `single_journal_sample` 来源上。
* **类型检查补全**：底里为 null 的字段也检查类型（`KIND_OVERRIDES`：栏宽 number，`axis_policy.tick_direction / label_format / label_format_regex` string，`legend_policy.frame` boolean）。URL / anchor 用整串匹配（Python `fullmatch`，TS 无 m 标志的 `$`），尾部换行被拒。
* **期刊自有栏位 `page_columns`（用户 2026-10-31 裁决）**：profile 与期刊 rules 可写有序数组 `[{id, label:{zh,en}, width_mm, qualifier}]`（`id` 唯一，宽度在 `PAGE_MM_MIN–MAX` 即 10–1000 mm 内，`qualifier` 同规则闭集），
  用户切到 Science 时画布预设显示它自己的「2 栏 121 mm / 3 栏 184 mm」，不硬套单栏 / 双栏。下游**只读这一种形状**（`derive_page_columns` / `derivePageColumns`）：写了 `page_columns` 就用它，没写由 `widths_mm` 派生
  「单栏 / 双栏」两档（label 走现有词表）。期刊写了 `page_columns` 时以它为准，折叠把 `widths_mm.single/double` 由 **exact** 栏位派生（最窄 = single、最宽 = double，只有一档时 double 为 null），保持现有求值器与 `columnOf` 兼容；
  规则本身（`page_columns` 这条）qualifier 只收 exact，栏位各自带 qualifier，只有 exact 栏位参与判据。预检 `page-width`：页宽命中任一 exact 栏位即通过，提示列出全部栏位（带 label）。
  画布 `pageColumns` / `resolvePageColumns` 已返回这一形状（id 为 string），属性栏预设按 label 显示；**切期刊时页宽跟着换的画布联动由选择器 PR 完成**（取离当前页宽最近的一栏；期刊没规定栏宽则页宽不动；与绑定同一次提交，撤销一步全回去）。
* **建议性提示（用户 2026-10-31 裁决）**：qualifier 为 `recommended` / `typical` 的字号数值不当硬判据，但要提示。新增规则路径 `min_script_font_size_pt`（上下标最小字号）与检查 id `font-size-recommended`（**suggestion** 级，只有从期刊折叠出 `advisories` 的 profile 才会出现）。
  这两类规则（`min_script_font_size_pt` 任何 qualifier；`min_effective_font_size_pt` 的 recommended / typical）**不折进字段**，而折成 `advisories: [{check, role: body_min|script_min, value_pt, qualifier, journal, quote}]`（`journal` 是声明它的出版社 / 期刊名，`quote` 是原话）；
  `journal_sources()` 里它们的 `affects` 是 `advice`。**求值器分不出上下标**，所以只进提示不进判据：有效字号低于正文建议值（没有就按上下标值）的文字都给一条提示，文案如「Elsevier 说明 7 pt 为正文最小字号，6 pt 为上下标最小字号（原文：…）」。
  `min` / `exact` 的正文最小字号仍是硬下限，照旧折进 `min_effective_font_size_pt`。
* **科学通报「字高 7 pt」**按用户裁决按**字号**计（该条规则的 `derivation` 写「字高按字号计」，`mark` 保留原话「字高统一为7 pt」），数据进 PR 3。
* **字段映射约定**（采集脚本与人工核对遵守）：期刊「最小字号」→ `min_effective_font_size_pt`（`absolute_min_font_size_pt` 保持 0，不另设）；「最大字号」→ `max_font_size_pt`；
  `min_raster_dpi` 取各图类里**最低**的一档（照片 / 半色调档，避免对线条图位图误报），按图类的值进 `info.dpi_by_kind`；预检分不出位图的图类，暂不承诺按图类判；
  **空数组 = 明确不限**（如 `line_widths_pt: []`），与「没写」（键不出现）不同；`font_family.latin_accepted` 只收真实字体名，「sans-serif」之类泛称进 `info.font_names_raw`；
  线宽下限 / 最大高度 / 色彩模式 / 格式原文暂只进 info（底里没有对应检查）。
* **链接安全**：`url` / `archive_url` 必须 `https://` 且 `url` 不带 `#`；`anchor` 必须以 `#:~:text=` 开头；`retrieved` 必须是真实的 ISO 日期 `YYYY-MM-DD`；`page` 是正整数（`12.0` 与 `12` 同义，两侧同判据）。
  链接交给外部浏览器，`javascript:` / `file:` / `http:` 进不了库。
* **别名撞名**按 NFKC 后小写比较（全角、兼容字符折成同一个）；`publisher` 不是字符串时先报 `journal_publisher_unknown`，不抛类型异常。

### 4. 项目快照机制原样适用

文档里仍是 `{id, journal?, snapshot?, …}`（ADR 0029）：选期刊 = 绑定 `journal-base-v1` 并写入折叠出的 `journal` 覆盖（**存覆盖，不只存期刊 id**——只存 id 会让库更新悄悄改掉已定稿项目的结论，也让没有期刊库的 MCP / Python 侧无法复现），规则全文快照照旧；另存 `journalRef: {id, catalogRev, retrieved}` 与生效字段出处的精简版（value / quote / link / retrieved / 两种 scope，几 KB），用户手改了覆盖则 `journalRef` 失效、界面改说「自定义」。期刊标准更新不会悄悄改掉已定稿项目的结论，
「有新版可同步」的判据仍是内容不等、由用户确认。PR 1 对 `specBinding.ts` 只改了清单与解析（`builtinCatalog` 过滤 `listed:false`、`isHiddenBuiltin`、`resolveDocumentSpec` 对隐藏底不报 `globalMissing`）；绑定的**写入**由 PR 2 的 `actions.selectJournal` 完成（存 `journal` + `journalRef {id, catalogRev, retrieved, label}` + 精简出处 `journalSources`；`specBinding.journalCarry` 保证换成别的规范时期刊字段整套放下）。

### 4b. 数据分发：仓库只放种子，完整目录由服务端提供（用户 2026-10-31 裁决：期刊数据是公司资产，不进仓库）

* **两层数据**：仓库里的 `journals.json` 只放一小份**种子**（几十本头部期刊 + 出版社通用标准；PR 1 仍为空）；完整目录由**服务端 API** 提供，客户端本地缓存。
  取用顺序：服务端数据（验签、校验通过）→ 本地缓存 → 内置种子。没网用缓存，没缓存退回种子。
* **代码、schema、校验、两侧同源检查仍在仓库**；服务端数据客户端照样按**同一套 schema 与校验**（本 ADR §3 / §3b）过一遍，**不通过就整份弃用**，退回缓存 / 种子，不做部分采信。
* **项目快照照旧（ADR 0029）**：绑定里记期刊 id + `catalog_rev`（并按 §4 存折叠出的覆盖与精简出处），服务端目录更新**不改**已定稿项目的结论；「有新版可同步」仍由用户确认。
* **签名**：服务端目录带签名（建议 Ed25519），**公钥内置在应用里**，验签通过才使用；验签失败、签名缺失或 `catalog_rev` 倒退都当作不可用，退回缓存 / 种子。私钥不进仓库、不进客户端。
* **隐私**：客户端按**出版社**或**全量**拉取、在本地挑选，**不按单刊请求**——服务端看不出用户在作哪本期刊的图。隐私说明（`docs/privacy.md`）随远程拉取那个 PR 同步写明：请求里只有目录范围与版本号，不带项目、脚本、路径或所选期刊；遵守遥测同意与 `TAVOTTO_NO_TELEMETRY` 的既有边界。
* **版权**：`quote` 只存一两句短摘录，不存整页原文；目录里不放抓取的网页正文或截图。
* **分 PR**：PR 1 数据结构与加载（本 PR）→ PR 2 选择器与出处卡 → PR 3 种子数据（几十本头部期刊 + 出版社通用标准，按出版社分 PR，逐条人工核对）→ PR 4 出处巡检；
  **远程目录（拉取、Ed25519 验签、本地缓存、离线回退到缓存 / 种子）另开 PR**，排在选择器 PR 2 之后或与之并行；服务端复用中国站 / workerd 基础设施，目录的生成与签名发布在服务端侧的工作流里，不在本仓库。完整目录的数据不进仓库。

### 5. 不做的事

* 本 PR 的 `publishers` / `journals` **留空**，不含任何真实期刊数值或引文；golden 向量用测试夹具期刊（`fx-*`、`example.invalid`）。仓库内的种子数据按出版社分 PR，每条逐一人工核对，没核过的不进库；完整目录不进仓库（§4b）。
* 不做继承链、不做期刊版本历史（出处的 `retrieved` 即快照时间）、不自动跟随网页变化（出处巡检脚本另起 PR，变了只开 issue、不自动改数值）。

### 6. 出处快照图（PR 2 追加；用户 2026-11-01 裁决）

* **每条规则（含 `info.*`）可选 `snapshot: {file, width, height}`**：采集流水线在采集时渲染的「原网页 / PDF 页高亮快照」——把引文那一段用**黄色荧光笔**标出，并**裁成那一段的短图**。
  `file` 必须整串匹配 `^snapshots/[0-9a-f]{64}\.webp$`（内容寻址：文件名就是内容的 sha256；不许路径、不许 URL，Python `fullmatch` / TS 无 m 标志的 `$`），`width` / `height` 是正整数（`520.0` 与 `520` 同义，`true` 不算）；
  三个键齐且只有这三个。失败码 `journal_rule_snapshot_invalid`（失败码闭集 19 个，两侧同名同义）。`journal_sources()` 每条带 `snapshot`（没有 = null）。
* **只存短截图，从不存整页**（版权与包体积）；整页永远只在原网页 / 存档里。
* **分发**：快照由采集流水线产出。**种子**把它随包分发（打进前端包的目录）；**远程目录**把它当作**该目录版本的资产**提供。
* **解析**：客户端把 `file` 相对「目录资产根」解析——资产根由 `store/journalCatalogStore` 提供：种子 = 随包目录（`BUNDLED_ASSET_BASE`，`/journal-catalog/`），远程目录由拉取它的 PR 给出那一版的根（`setCatalog(raw, assetBase)`）。
* **出处卡**：有快照 = 全宽快照图（高度跟图、最高约 240px、更高在卡内滚、细边框）；**没有快照或图加载失败 = 退回摘录框**（命中的数仍用 `<mark>`），不出错误态。换算说明（`derivation`）不在卡里显示。
* 测试夹具快照图由 `scripts/gen_journal_snapshot_fixture.py` 生成（`tests/fixtures/journal_snapshots/snapshots/<sha256>.webp`，一张假的「原网页」，非任何真实页面）。

### 7. 「不按期刊」（PR 2 追加）

选期刊时绑定里记 `journalPrev {profile, pageW}`（选之前的规范绑定与页宽；从一本期刊换到另一本沿用最初那份）。选择器列表最上面的「不按期刊」一次提交退回它（撤销一步回到期刊）；没有记录就退回默认规范、页宽不动。不另设按钮。

## 后果

* 加一本期刊 = 在目录（种子 `journals.json` 或服务端目录）里加一条，求值器、预检、proof report 都不用动。
* 两个实现靠 `tests/golden/journal_vectors.json`（`scripts/gen_journal_vectors.py`）对齐；每个校验失败码至少有一条向量。
* 1.0 收敛纪律：用户 2026-10-31 裁决立项并给豁免，已登记在 `docs/1.0-release-readiness.md` §1.8；不选期刊时行为不变。
