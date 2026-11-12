# ADR 0155：内置期刊标准库——期刊 = 覆盖 + 出处，未规定的项不补

日期：2026-10-31 · 状态：**Accepted（PR 3 带着种子数据把入口打开，见 §11；此前 PR 2 合入但入口默认关闭（2026-11-01 用户决定）；PR 1：数据结构、双侧加载与校验、中性底 `journal-base-v1`；PR 2：选择器、出处卡、画布页宽跟栏；真实数据、出处巡检、远程目录另起 PR）**。
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
* **隐私**（**已被 §10 取代**：2026-11-04 用户改为按需下载、服务端留读取记录）：~~客户端按**出版社**或**全量**拉取、在本地挑选，**不按单刊请求**——服务端看不出用户在作哪本期刊的图。~~隐私说明（`docs/privacy.md`）随远程拉取那个 PR 同步写明：请求里只有目录范围与版本号，不带项目、脚本、路径或所选期刊；遵守遥测同意与 `TAVOTTO_NO_TELEMETRY` 的既有边界。
* **版权**：`quote` 只存一两句短摘录，不存整页原文；目录里不放抓取的网页正文或截图。
* **分 PR**：PR 1 数据结构与加载（本 PR）→ PR 2 选择器与出处卡 → PR 3 种子数据（几十本头部期刊 + 出版社通用标准，按出版社分 PR，逐条人工核对）→ PR 4 出处巡检；
  **远程目录（拉取、Ed25519 验签、本地缓存、离线回退到缓存 / 种子；协议见 §10）另开 PR**，排在选择器 PR 2 之后或与之并行；服务端复用中国站 / workerd 基础设施，目录的生成与签名发布在服务端侧的工作流里，不在本仓库。完整目录的数据不进仓库。

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

### 8. 入口开关（PR 2 追加）

**PR 2 合入但入口默认关闭，数据就绪后再开（2026-11-01 用户决定）**：`web/src/lib/journalUi.ts` 的常量 `JOURNAL_CATALOG_UI = false`（前端没有功能开关机制，功能登记表只有 active / removed，所以只加这一个常量）。关着时设置 › 期刊规范里的「期刊」行与「期刊要求」、问题面板行尾的「出处」图标、导出对话框里的期刊名、设置搜索里的 `spec.journal` 都不出现，界面与此 PR 之前相同；
带期刊绑定的旧文档照旧按绑定预检，画布页预设的期刊栏名也照旧跟着绑定走（它们读文档里的绑定，不是入口）。打开 = 把常量改成 `true`；测试 / e2e 用开发期 localStorage 键 `tavotto.dev.journalCatalogUi = '1'`，生产界面没有任何入口能设它。
关着期间不写发行说明（用户在这个版本里看不到它），打开那个 PR 补。

### 7. 「不按期刊」（PR 2 追加）

选期刊时绑定里记 `journalPrev {profile, pageW}`（选之前的规范绑定与页宽；从一本期刊换到另一本沿用最初那份）。选择器列表最上面的「不按期刊」一次提交退回它（撤销一步回到期刊）；没有记录就退回默认规范、页宽不动。不另设按钮。

### 9. MCP 侧（开关同源；用户 2026-11-04 拍板）

* **同一个开关**：Python 常量 `engine/journals.py` 的 `JOURNAL_CATALOG_AGENTS` 与 `web/src/lib/journalUi.ts` 的 `JOURNAL_CATALOG_UI` 字面值必须相同（严格同源对，`tests/test_journal_agents.py::test_agent_switch_is_the_desktop_switch_literally`
  从 TS 源文件读字面值对拍，读不出就失败）。生产里没有任何用户入口能设它（不读环境变量）；测试 monkeypatch 常量。数据就绪那个 PR 把两边一起改成 true。
* **关着时 Agent 看不到期刊库**：`tavotto_list_journals` 不在 tools/list，各工具 schema 里没有 `journal_id`；带 `journal_id` 调用（含直接调 `tavotto_list_journals`）回结构化错误 `journal_catalog_disabled`，不静默忽略。
  旧引擎没有 `journals` 模块等同于关（桥只经 `_optional_engine("journals")` 取它，不抬高插件最低引擎版本）。
* **开着时**：新只读工具 `tavotto_list_journals({query?})` → `{catalog_rev, journals[{id, label, publisher, aliases}]}`（`query` 按 id / 名称 / 出版社 / 别名做 NFKC + 小写的子串过滤；空库如实返回空列表）；
  `tavotto_open_figure` / `tavotto_normalize_figure` / `tavotto_preflight` / `tavotto_export` 加 `journal_id`：解析为 `profile_id = journal-base-v1` + `journal = journal_override(id)`（语义同 §4 桌面 `selectJournal`，存覆盖不存 id）。
  与手写 `profile_id` / `journal` 同时给 → `journal_id_conflict`（不猜谁赢）；未知 id → `journal_unknown`；批量 open 的 `journal_id` 错是整次调用的错，不逐张重复。
  解析逻辑全在引擎（`journals.select_for_agent` / `journal_ref` / `search_journals`），插件只翻译。`journal_catalog_disabled` / `journal_id_conflict` 是 Agent 入口自己的码，不进库校验的闭集 `ERROR_CODES`（TS 侧没有对应物）。
* **`journal_ref`**：`{id, catalog_rev, retrieved}`（口径同桌面 `journalRef`：`retrieved` 取该期刊全部出处里最晚的取回日期；snake_case）。进 `profile` 的印章（`profiles.stamp`）与会话里的 profile，
  所以 open / preflight / export 的结果顶层各有一份 `journal_ref`，proof report 顶层与 `profile` 里也有；之后不带参数的调用沿用会话里的期刊与 `journal_ref`。
* 技能文本（SKILL.md / references）此时不写；开关打开那个 PR 再加。开关关着期间不写发行说明（同 §8）。

### 10. 远程目录协议（用户 2026-11-04 拍板：按需下载 + 设备令牌 + 限额；取代 §4b 的「不按单刊请求」）

> **隐私口径变更**：§4b 原写「不按单刊请求、服务端看不出用户在作哪本期刊」。用户 2026-11-04 决定改为**按需下载**——选中某本刊时才向服务端取它的标准，
> 服务端**会记录「哪个设备令牌在什么时候首次读了哪本刊」，保留 1 年，到期删除，用途仅是统计热门刊与防刷**。§4b 其余（验签、`catalog_rev` 单调、整份校验失败弃用、项目快照不受服务端更新影响）不变。
> 实现：`engine/journalremote.py`（客户端；Ed25519 验签用 PyNaCl，见「签名与完整性」）、`services/journal_catalog/`（服务端，不进 wheel）、
> 发布与签名工具在私有数据仓库 `tools/catalog/publish_catalog.py`。

**两层数据。**

| 层 | 内容 | 获取 | 计数 |
|---|---|---|---|
| 索引 | 出版社名；每本刊的 id / 名称 / ISSN / 别名 + 刊包的 `sha256` 与字节数；`catalog_rev` | 启动时整份下载，**签名** | 不计数；搜索与浏览离线可用 |
| 刊包 | 该刊完整对象 + 所属出版社完整对象（含 `sources` / `rules`） | 选中该刊时才下载 | 服务端按设备令牌对该刊的**首次**读取计 1 |
| 快照 | 高亮短图 `snapshots/<sha256>.webp`（内容寻址） | 出处卡要看时才拉 | 不计数（需有效令牌） |

**签名与完整性。** 索引是信封 `{alg:"ed25519", key_id, payload:<base64>, sig:<base64>}`，签名覆盖 `payload` 解码后的原始字节（不做规范化）。
每本刊的标准**不单独签名**，由签名索引给出的 `sha256` + 字节数担保：哈希或大小对不上，刊包整个作废。选这条的理由：服务端对象存储 / 缓存不可信也无妨；
发布时只签一次；换版本时只有变了的刊哈希变。公钥内置在 `journalremote.TRUSTED_KEYS`（`key_id → 公钥`，可同时放多把，轮换时新旧并存一个版本）。
验签失败、签名缺失、`key_id` 未知、索引畸形、`catalog_rev` 低于「历史最高与内置种子」，一律整次作废，缓存原样；拼出的完整目录（种子 + 索引骨架 + 已下载刊包）过 `journals.validate_catalog`，
不过整份弃用（前端 `setCatalog` 再过一道 `loadCatalog`）。种子里有的刊以种子为准，不下载、不计数。

**验签用成熟库，不手写加密（用户 2026-11-05 拍板）。** 验签调 PyNaCl（libsodium）的 `VerifyKey(pk).verify(msg, sig)`（先判公钥 32 / 签名 64 字节；任何异常，含库缺失，一律当验签失败，目录保持种子）。早先一版是 `engine/ed25519.py` 里手写的纯标准库验签，已删除：为省一个原生依赖而自己实现签名算法，不如用被广泛审计的库。代价是 Flask 父进程的依赖闭包多了 `pynacl`（及其 `cffi`）；缓解是**延后 import**——只在拉索引验签的函数里 import，Flask 启动不加载（`tests/test_journal_remote.py` 看护 `import tavotto.app` 后 `nacl` 不在 `sys.modules`），冻结产物由 `packaging/tavotto.spec` 点名收进 hiddenimports（`nacl` 全部子模块 + `nacl._sodium` 扩展）。

**取舍：先用 pyca `cryptography`，后换 PyNaCl（用户 2026-11-05）。** `cryptography` 49 起 PyPI 不再发 macOS x86_64 / universal2 与 win32 轮子，而 macos-x86_64 是桌面版与 runtime-lock 的正式目标，Intel Mac 会退到 Rust 源码构建；钉在 48.x 则 dependabot 的告警（都在 X.509 / PKCS#7，我们用不到）一直挂着。PyNaCl 1.6.2（libsodium）有 cp38-abi3 的 macOS universal2、win32 / win_amd64 / win_arm64、manylinux / musllinux 轮子，macOS 包约 0.4 MB（cryptography 约 7.8 MB）。libsodium 的验签拒 S >= L 的可塑性签名与小阶公钥点，`tests/test_journal_remote.py` 钉死这两条与 RFC 8032 官方向量。

**限流可调（用户 2026-11-05 拍板）。** 理由：学校 / 公司 / 宿舍整片人共用一个出口 IP，原先「每 IP 每天 5 个新令牌、100 本」会让第 6 个同学起拿不到令牌，而这个 IP 维度本来只是防「一个出口大规模刷」，不是防个人（个人由设备维度的 10 本 / 小时、20 本 / 天管）。所以：
1. **默认放宽 IP 维度**：新令牌 5 → 200 / 天；首次读取 100 → 2000 / 天（约 200 台设备 × 日均约 10 本，设备自己的上限是 20 本 / 天，日均远低于上限）。设备维度不变。
2. **规则运行时可调，不用重新部署**：规则存 sqlite 表（`rules` 全局默认值与紧急开关、`ip_cidr_rules` 网段例外、`rule_audit` 审计，只用 SQLite / D1 共有语法），服务每约 10 秒读一次（短 TTL 缓存）。环境变量 `TAVOTTO_JC_*` 保留，作为规则表没写时的缺省。
3. **网段例外**：管理员写 CIDR（IPv4 段、IPv6 前缀）→ 放宽到指定值，或完全豁免 IP 维度（设备维度照常）；多条命中取最长前缀。判断在 HMAC **之前**、用请求里的明文 IP 在内存里做，明文 IP 不落库、不进日志、不进审计；比 `/8`（IPv6 `/32`）更宽的网段命令行拒收（要整体放开用紧急开关）。
4. **紧急开关**：一键关掉所有 IP 维度限额，设备维度照常。
5. **管理命令** `python -m services.journal_catalog.admin rules list|set|exempt-cidr|clear`、`kill-switch on|off`、`top --n N`；每次改动写审计表（操作者 `--by` 或 `$USER`、时间、改了什么；只有规则键、旧值 → 新值、管理员写的 CIDR）。
6. **可观测**：按 `ip_key` 记当天不同设备数（设备只存 HMAC 派生的 16 位标签，不能回连 `devices` / `reads`）与被拒次数（按 code），`top` 只显示 `ip_key` 前 8 位；与 `ip_events` 同样只留 48 小时。

**设备令牌与共享位置（app 与 Agent 共用）。** 令牌是服务端签发的匿名随机串（`POST device`），与遥测的 install_id 无关。
令牌、索引、已下载刊包、快照都放在用户数据目录 `config.data_dir()/journal-catalog/`
（macOS `~/Library/Application Support/Tavotto`，Windows `%LOCALAPPDATA%\Tavotto`，Linux `$XDG_DATA_HOME/tavotto`，`TAVOTTO_DATA_DIR` 可改）：
`device.json`（0600，`{"token": ...}`）、`index.signed.json`、`bundles/<sha256>.json`、`snapshots/<sha256>.webp`、`state.json`、`lock`。
**app 与 Agent 宿主同机共用这一份**：谁先需要谁领，后来的一方直接读盘复用；缓存命中不发请求，因此不计数。写入全部是原子替换；领令牌与改 `state.json` 在目录锁（`lock`，`O_EXCL`，30 秒视为遗弃）里「先重读再签发」，两个进程同时需要时只有一个去签发。
文件损坏或丢失 → 重新领一枚（**占用当天该 IP 的一个新令牌名额**（默认每 IP 每天 200 个），且该机器的当日额度从头算起）；服务端回 `device_unknown` 时客户端换新令牌重试一次（若盘上已是对方刚换的新令牌则直接用）。

**限额（服务端）。** 只数「令牌 × 刊」的首次读取；重复读、因 `catalog_rev` 更新重拉都不计。三道，**全部是滚动窗口**（不用日历日，所以没有 UTC / 服务器时区问题；回复里的 `retry_after_s` 就是最早一条过期的秒数）：
令牌 10 本 / 小时、20 本 / 天；同一 IP 合计 2000 本 / 天；同一 IP 24 小时内最多签发 200 个新令牌（IP 维度默认值 2026-11-05 放宽，理由与可调规则见下面「限流可调」）。**IP 级限额是 app 与 Agent 合计，不按客户端类型分桶**（请求头 `X-Tavotto-Client` 只用于统计，改头绕不过）。
IP 不存明文，存 HMAC（IPv6 取 /64），只留 48 小时；读取记录（令牌、刊 id、时间、客户端类型）保留 1 年后删除。

**超限的表现。** 客户端回 `quota`；界面不弹错，用一句话胶囊提示（文案待用户过目），这本刊暂时按「不按期刊」处理。断网又没缓存的刊在选择器里可见，选中时提示需要联网（同为胶囊）。
**拉取时机**：启动时同步一次索引，之后每 24 小时至多一次，失败 1 小时后再试；刊包只在选中时下载。**开关**：只认 `TAVOTTO_NO_JOURNAL_SYNC=1`，设了一个字节都不发（有缓存仍可用）；`TAVOTTO_NO_TELEMETRY` 只管遥测，与 `updater` 同一先例，不代管（用户 2026-11-04 定）；
不要求遥测同意（请求不带 install_id）。入口常量 `JOURNAL_CATALOG_UI` 关着时前端不发任何请求。**公钥**：生产代码里只有 `prod-1`（私钥在维护者本机 `~/.config/tavotto-journal-catalog.key`，不进仓库），测试钥只在测试里注入（`test_production_code_does_not_trust_the_test_key` 看护）。**两个入口、一个后端、计数只有一份**：`https://tavotto.cn/v1/journal-catalog/`（中国站，后端本体，sqlite 计数在这里）与 `https://tavotto.com/v1/journal-catalog/`（海外，tavotto.com 的 Vercel 函数薄反代到中国站）。反代把真实客户端 IP 放进 `X-Tavotto-Client-IP` 并附共享密钥头 `X-Tavotto-Proxy-Secret`；**后端只在密钥对得上时才采信这个 IP**，否则按连接 IP 算（所以伪造头绕不过 IP 限额；密钥走环境变量，不进仓库）。边缘只缓存签名索引（`s-maxage=300`）；令牌签发、刊包、快照每次回源（首次读取的计数与令牌校验在源站，刊包被边缘缓存就绕过了计数）。客户端先后顺序与更新检查、联系作者**同一个**「大陆环境」判据（`contactsend.region_is_cn`：壳的 `TAVOTTO_NETWORK_REGION=cn` 或界面语言 zh-CN）：大陆先 .cn 后 .com，其余先 .com 后 .cn；传输失败换另一个入口，额度用完（429）不换（两个入口是同一份计数）。读取记录因此不区分入口，同一份、保留 1 年。限额窗口用滚动 24 小时 / 60 分钟。

**线协议 v1**（基址 `https://<host>/v1/journal-catalog/`；服务端静态内容由发布工具生成）：

| 接口 | 说明 |
|---|---|
| `GET index.signed.json` | 签名索引。无需令牌 |
| `POST device` | 签发令牌，回 `{"token": ...}` |
| `GET journals/<id>?rev=<catalog_rev>` | 刊包字节；头 `X-Tavotto-Device`；`rev` 必须是索引里的版本 |
| `GET snapshots/<sha256>.webp` | 快照；头 `X-Tavotto-Device`；哈希必须等于文件名 |

请求头：`X-Tavotto-Device: <令牌>`，`X-Tavotto-Client: app | agent-<宿主名>`（`^(app|agent-[a-z0-9][a-z0-9-]{0,31})$`），`User-Agent: Tavotto/<版本>`。**请求里没有项目、路径、脚本，也没有所选期刊之外的任何内容。**
错误：400 `bad_request`；401 `device_unknown`；404 `stale_rev`（索引过期，重取索引再试）/ `no_such_journal`；429 `quota_hour` / `quota_day` / `ip_day` / `ip_tokens`，体里带 `retry_after_s`，并有 `Retry-After` 头。

#### 10.1 Agent 接入说明

Agent 宿主（Codex / Claude Code 插件、MCP 等）与 app 走**同一套接口、同一份共享目录、同一个令牌**，没有第二条路：

1. **读索引与缓存**：先读共享目录里的 `index.signed.json`（用内置公钥验签；没有就 `GET index.signed.json` 取）。已有 `bundles/<索引里该刊的 sha256>.json`（校验哈希）就直接用，不发请求、不计数。
2. **令牌**：读 `device.json`；没有就在目录锁里先重读、仍没有再 `POST device`，用原子替换写回（权限 0600）。app 与 Agent 谁先到谁领，另一方复用；两方都没有才签发。服务端回 `device_unknown` 时重领一次。**不要为绕过限额反复换令牌**——IP 级限额会拦住，并占用当天的新令牌名额（默认每 IP 每天 200 个）。
3. **下载一本刊**：`GET journals/<id>?rev=<索引的 catalog_rev>`，头带 `X-Tavotto-Device` 与 `X-Tavotto-Client: agent-<宿主名>`（用 `TAVOTTO_JOURNAL_CLIENT=agent-<宿主名>` 环境变量时 `journalremote` 会自己带）。校验 `sha256` / 字节数等于索引条目，再写入 `bundles/<sha256>.json`（原子替换）。
4. **优先复用实现**：宿主环境里装了 `tavotto` 包时，直接调 `tavotto.engine.journalremote.sync()` / `ensure_journal(id)` / `snapshot_path(file)`（永不抛，结果见各自文档字符串），不要自己再写一遍；没有就按上面三步实现。
5. **计数**：只数该令牌对该刊的首次读取；重复读、`catalog_rev` 更新重拉、种子里的刊都不计。每令牌 10 本 / 小时、20 本 / 天，同 IP 合计 2000 本 / 天、新令牌 200 个 / 天（app 与 Agent 合计；数字是服务端默认，可在不重新部署的情况下调，见 §10 的「限流可调」）。
6. **超限**：收到 429 就停——读 `retry_after_s`（秒），期间不要重试同一类请求，也不要换令牌；对用户一句话说明额度用完、稍后再试即可。离线且没有缓存同理，说明需要联网。
7. **禁止**：不要把项目内容、路径、脚本、文件名带进任何请求；`TAVOTTO_NO_JOURNAL_SYNC=1` 时一个字节都不发。
8. **不要信任未验签的东西**：索引必须验签，刊包必须对哈希，快照文件名必须等于内容的 sha256。

**MCP 桥怎么接（实现，引用 §9 的 Agent 入口）**：`tavotto_list_journals` 读「种子 + 缓存索引」（`journalremote.agent_catalog()`：先静默同步索引、不计数，离线用缓存，没有缓存用内置种子；
远程目录里的刊多一个 `downloaded`，标出标准是否已在本机）。`tavotto_open_figure` / `tavotto_normalize_figure` / `tavotto_preflight` / `tavotto_export` 的 `journal_id` 走 `bridge.journal_selection`：
先看与手写 `profile_id` / `journal` 是否冲突（冲突直接 `journal_id_conflict`，**不下载、不占额度**），再 `journalremote.agent_resolve(id)`（本地没有才下载并计 1 次），最后交给 §9 的 `select_for_agent`（带上拼好的目录）。
客户端类型自报 `agent-<MCP clientInfo.name 的 slug>`（没有则 `agent-mcp`），用户 / 宿主自己设了 `TAVOTTO_JOURNAL_CLIENT` 就尊重。

**Agent 错误码**（并进 `journals.AGENT_ERROR_CODES`，与 `journal_catalog_disabled` / `journal_id_conflict` 同一个元组；不在库校验的闭集 `ERROR_CODES` 里；失败一律明确报给 Agent，**不静默退回别的数据**）：

| code | 什么时候 | 话术要点（给 Agent 的 `error` 文本） | 额外字段 |
|---|---|---|---|
| `journal_quota_exceeded` | 服务端 429（令牌 / IP 限额） | 这本期刊今天的下载额度用完了，约 N 分钟后再试；不要换设备令牌重试，也不要凭记忆编这本期刊的规范 | `retry_after_s` |
| `journal_offline` | 这本刊本地没有、联不上网，或 `TAVOTTO_NO_JOURNAL_SYNC=1` | 这本期刊的标准还没下载到本机，需要联网；联网后再试，或改用内置种子里已有的期刊 | — |
| `journal_verification_failed` | 验签 / 哈希 / 目录校验没过 | 数据没有通过验签 / 校验，已整份弃用；不要采用任何来自它的数值 | — |

已有的 `journal_unknown`（索引里没有这个 id）、`journal_catalog_disabled`、`journal_id_conflict` 口径不变。

**部署**（不在本仓库部署；详见 `services/journal_catalog/DEPLOYMENT.md`）：索引、刊包、快照由发布工具生成到服务端 DATA 目录，随中国站现有服务器上的 WSGI 服务（sqlite 计数）提供；
选 sqlite 单机而不是 KV / D1，因为用户主要在大陆、计数是强一致的单行事务、规模小；`schema.sql` 只用 SQLite / D1 共有语法，将来要搬 D1 不用改表。
（`workerd/` 在本仓库是渲染 worker 的 Rust supervisor，与服务端无关。）

## 后果

* 加一本期刊 = 在目录（种子 `journals.json` 或服务端目录）里加一条，求值器、预检、proof report 都不用动。
* 两个实现靠 `tests/golden/journal_vectors.json`（`scripts/gen_journal_vectors.py`）对齐；每个校验失败码至少有一条向量。
* 1.0 收敛纪律：用户 2026-10-31 裁决立项并给豁免，已登记在 `docs/1.0-release-readiness.md` §1.8；不选期刊时行为不变。

## 11. 修订记录：PR 3 种子数据 + 打开入口（2026-11-04）

* **种子**：`journals.json` 现在是 56 本头部期刊 + 9 个出版社的通用标准（156 条规则、43 张快照图），每一条都带原文出处与短摘录。把关方式（用户 2026-11-04 选定）：Codex 逐条对照原文复核 → 出一张核对页给用户抽看、拍板 → 通过的才进种子；**Codex 判「驳回」的与改不动的一律不进**，没核过的不进；**唯一例外**：用户 2026-11-04 看过核对页后裁决恢复两条被 Codex 驳回的栏位表——Science（自家栏名 2 栏 121 mm / 3 栏 184 mm，栏位限定词 typical）与 Elsevier（单栏 90 / 1.5 栏 140 / 双栏 190 mm，exact；摘录换成原文表格里从「Single column 90 mm」到「190 mm」的连续一段），核对页上标「用户裁决恢复，覆盖 Codex 驳回」。JACS / ES&T 栏位、Springer Nature 出版社级栏宽、Nature 7 pt 上限保持剔除。种子由私有仓库的 `tools/seed/build_seed.py` 从整理结果生成、可重跑；完整目录与采集产物仍不进本仓库（§4b）。PR 1 复审遗留的 7 本 `-sample` 假期刊已不在库里（测试看护）。
* **id = 整理结果的 key**：期刊 id、出版社 id 直接用私有整理结果里的 key（即远程完整目录将来用的 id），项目绑在种子 id 上，远程目录一到才对得上。整理结果里没有 slug 形 publisher 的几本（科学通报、化学学报、中国激光）用稳定的 slug，将来远程目录若换 id 要在那一侧加别名。
* **映射约定的补充**（细化 §3b「字段映射约定」）：① 同一来源页的栏位合成**一条** `page_columns` 规则（摘录是各栏记录的原话按页内顺序连接，每栏自带 `qualifier`）；没有栏名的「宽度上限」与区间进 `info.width_*`，不冒充栏位；Springer 的 174 mm 栏位按 `typical` 只展示。② `min_raster_dpi` 取各图类里最低的一档（原文写「达到 / 必须」的 `exact` 记为 `min`），按图类的值进 `info.dpi_by_kind`；其余图类的档不单独成规则。③ `font_family.latin_accepted` 只收一眼能认的真实字体名，泛称、带说明的句子进 `info.font_names_raw`。④ 字号 `range`（如「5–7 pt」）两端各记一条、限定词 `range`（不进判据，只展示）；Elsevier 7 pt / 6 pt 走建议级、Science 5 pt 是 `min`、Nature 7 pt 是 `max_font_size_pt`、科学通报「字高 7 pt」按字号计并保留原话（`derivation`「字高按字号计」）。AIP 不在本批种子里；将来收入时它的 264 dpi 只能是兜底（`recommended`），不进 `min_raster_dpi` 的判据。
* **入口打开**：`web/src/lib/journalUi.ts` 的 `JOURNAL_CATALOG_UI = true`（§8 的开关到此为止；开发期 localStorage 键保留但不再起作用）。「关着时」的测试改成「开着时」的，关着的情形不再单测。
* **快照资产根改为 `/assets/journal-catalog/`**（§6 原写 `/journal-catalog/`）：后端只服务 `/assets/<path>`（公开前缀、不可变缓存），种子快照放在 `web/public/assets/journal-catalog/snapshots/`，由既有路由送出，不另开路由、不动 `security._PUBLIC_PREFIXES`。随包测试看护：引用到的每张快照都在、是 webp、文件名即内容 sha256、没有无人引用的快照。
* **一键修复读栏位表**：`issueFix.fixOptions / planFix` 改读 `derivePageColumns`——选项是判据认的（`exact`）栏位，单栏 / 双栏沿用原措辞，期刊自有栏（如 Elsevier 的 1.5 栏）用它自己的栏名。
* **`pageColumnsOf` 的回退（登记）**：规范里没有任何可用栏位（期刊底没规定栏宽，或栏宽都不在 `PAGE_MM_MIN`–`PAGE_MM_MAX` 内）时，画布页预设与拖边吸附退回**内置默认规范**的单栏 / 双栏（`FALLBACK_WIDTHS`）。这只是显示 / 吸附的回退，**不是判据**：预检 `page-width` 只读 `exact` 栏位，期刊没有 `exact` 栏位就不报 `page-width`；`resolvePageColumns().journal` 只在期刊自己写了可用栏位时为真。
* **展示细节**：要求清单里展示项的对象值不露 JSON（文件格式说成「推荐 … ；可接受 …」，按图类 dpi 逐项列）；规范页四个关键数里 `null`（该规范没规定）显示「未规定」，不显示 `null`；最小字号 ≤ 0（底规范没有硬性下限，期刊只给建议值或没写）也显示「未规定」，有建议字号时在名字后小字「· 建议 N pt」；要求清单里只有原句、没有可标的数的行（色彩模式、字体名、1.5 栏等）值这一栏统一写「有要求」，原话留给出处卡。
* **MCP 与遥测随开关一起打开（§9 的「数据就绪那个 PR」）**：`journals.JOURNAL_CATALOG_AGENTS = True`（与 `JOURNAL_CATALOG_UI` 同源对）；插件 `tavotto_list_journals` 处理器由 `JOURNAL_HANDLERS` 并进 `HANDLERS`，遥测枚举 `usage.TOOLS` / 引擎 `EVENTS["mcp_tool_call"].tool` / 代理 `contract.py` 同步加上这个工具名；工具名闭集扩了，**`CONSENT_VERSION` 3 → 4**（用户 2026-11-04 拍板这版升；`reconsentDelta` 只写「AI 编辑器查期刊列表的次数」一项，`docs/privacy.md` 同步版本记录）。技能文本由别的 PR 加。
* **发行说明**：入口打开，补 `docs/release-notes/unreleased/journal-catalog.md`（§8 的约定）。

## 12. 修订记录：期刊贯通到其他设置（2026-11-11，用户拍板）

用户 2026-11-11：期刊规范「还没有融会贯通到其他设置」——设置页同屏摆着默认规范的数，导出、样式、改图助手都不知道项目选了哪本期刊。
一律只读文档绑定里存的精简出处（§4，`DocumentProfile.journalSources`），不查目录：已定稿项目离线也一样、库更新不改它。规范本身仍只检查、不改图（ADR 0029）；
下面会改文档的只有「按期刊调整样式」，而且是用户点了才改、一次 commit、撤销一步。

* **设置 › 期刊规范只有一份「本项目的规则」**（#1849）：期刊底不列出（§2），规范库下拉此前退回库里第一条（默认规范），同屏打架。现在多一条虚拟条目
  「<期刊> · 期刊 · 本项目在用」并默认选中，下面各项是合并后实际在用的值：期刊写了（`affects === 'preflight'`，或栏宽由 `page_columns` 派生）给数，
  没写的说「未规定 · 默认 X」；宽度容差与推荐导出分辨率是工具参数 / 固定推荐 600，只写数（用户裁决）。期刊有自有栏位时单 / 双栏并成一行「栏宽」。
  「本项目实际检查的规则」折叠行在按期刊时不再重复。「复制一份再修改」复制期刊折好的全文（去掉 `listed:false`），成为普通自定义规范；
  带 `page_columns` 的规范在编辑区逐栏可改，`widths_mm.single / double` 随 exact 栏位重算（§3b 同一口径）。从期刊换到库里别的规范、或在导出对话框的规范下拉里换走，先确认一句。
* **导出初值跟期刊**（#1850，`lib/journalExport`）：分辨率取期刊最高一档（`info.dpi_by_kind` 与 `min_raster_dpi` 的最大值，夹到 1200），只升不降；
  期刊列了格式（`info.formats_raw`）时，默认勾着、期刊没列出的那种换成同类里列出的（位图换位图、矢量换矢量，EPS 不当候选），同类一个都没列就留着。
  没列出的格式照样能勾，名字右上角一个小圆点 + 气泡。每个「文档 + 期刊」只套一次。导出对话框「检查 › 规范」下拉在期刊绑定下显示期刊名（此前空白）。
* **按期刊调整样式**（#1851，`lib/journalStyle` + `store/journalStyle`，功能 `style.journal-adjust`）：左栏样式面板「跟随样式」节里一行「N 项不合 X」+「按期刊调整」。
  字号抬下限 / 压上限（期刊写的推荐 / 区间也算——这是用户主动要的调整，不是判据）、画布标注只抬下限、线宽抬下限（0 宽不算）、拉丁字体换成期刊列表里第一个本机画得出的。
  按角色统一：样式里设了以样式为准，没设取图上最常见的值再夹进范围；已在范围内的角色不动。画布跟随样式 → 改这套样式（`editBoundStyleAll`）；没跟随 → 新建一份名为期刊名的样式并跟随。
* **改图助手带期刊上下文**（#1852，`lib/journalAi` → `ai_prompt_pack.journal_context`）：请求体 `journal` 是结构化摘要（字号上下限、字体、线宽下限、exact 栏宽、预检分辨率下限），
  后端严格校验形状、不对就整段不写，写成提示词界面上下文里的一句（Prompt Pack 新增 7 个模板键；没有期刊时 V1 输出逐字节不变）。`docs/privacy.md` 补一句：按期刊检查时请求带期刊名与作图要求。
* **顶栏期刊胶囊**：文档名后一枚中性胶囊显示期刊名，点开设置 › 期刊规范；窄于 900 只留图标。
* **不做（登记）**：图高上限（`info.max_height_mm`，种子里 5 本）仍只展示——要进判据得新增检查 id、两侧求值器与 golden 向量，并把数据从 `info` 搬进 `rules`（§3b 留的路），另起 PR；
  不在画布属性栏另起一处「超高」提示（「这份项目有什么问题」只有一条链）。
