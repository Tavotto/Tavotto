# ADR 0154：内置期刊标准库——期刊 = 覆盖 + 出处，未规定的项不补

日期：2026-10-31 · 状态：**Accepted（PR 1：数据结构、双侧加载与校验、中性底 `journal-base-v1`；选择器与出处卡、真实数据、出处巡检另起 PR）**。
相关：[0006 版本化出版规范](0006-codex-mcp-app-and-publication-profile.md)、[0029 Style / Spec 与规范快照](0029-style-spec-profiles.md)；
规则全文 `docs/rules/backend/profiles-and-preflight.md`「内置期刊标准库」。

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
`absolute_min_font_size_pt = 0` 用法）、线宽预设与坐标轴 / 图例策略全空，页宽 / 比例 / 字体等检查项降为 `suggestion`；只保留安全兜底
（`element-outside-figure` error、缺字形 / 公式排不出 warn、栅格 DPI 下限 300、字号上限 72 pt）。页宽的 80 / 150 mm 只是 `widths_mm` 必须是正数的占位。
期刊写了的项才会改变判据；`journal_sources()` 里没有的路径，界面标「该期刊未规定」。

**它不是一份可选规范**：profile 加 `"listed": false`，`list_profiles()` / `listProfiles()` 过滤掉，规范下拉与 `profilestore` 的内置记录里都不出现；
`load()` / `loadProfile()` 照常取得到。

### 3. 加载时全量校验，失败码是闭集

`engine/journals.py` 与 `web/src/lib/journals.ts` 同名同义（严格同源对），加载时校验：每条 rule 有 `value`、有 `src` 且 `src` 在可见 sources 里、`quote` 非空、
键路径合法、`value` 的 JSON 类型与底里该字段一致，折叠后整份结果过 `validate_spec`（TS 侧 `validateSpecShape` 是 `_validate` 的镜像）。
失败码闭集：`journal_catalog_schema` / `journal_source_invalid` / `journal_publisher_unknown` / `journal_alias_conflict` / `journal_rule_value_missing` /
`journal_rule_src_missing` / `journal_rule_src_unknown` / `journal_rule_quote_empty` / `journal_rule_path_unknown` / `journal_rule_value_type` /
`journal_merge_invalid` / `journal_unknown`。校验在加载处做，所以**一条没出处的数值进不了库**——这是对「严禁凭记忆写数值」的结构性看护。

对外：`list_journals()`、`journal_override(id)`、`journal_profile(id)`、`journal_sources(id)`（字段 → value / quote / url + anchor / link / archive_url / retrieved，
给出处卡与 proof report）。TS 侧经新增的 `@journals` 别名读同一个 JSON（`vite.config.ts` / `vitest.config.ts` / `vite.mcp.config.ts` / `vite.playground.config.ts` / `tsconfig.app.json` 各配一次）。

### 4. 项目快照机制原样适用

文档里仍是 `{id, journal?, snapshot?, …}`（ADR 0029）：选期刊 = 绑定 `journal-base-v1` 并写入折叠出的 `journal` 覆盖，规则全文快照照旧。期刊标准更新不会悄悄改掉已定稿项目的结论，
「有新版可同步」的判据仍是内容不等、由用户确认。本 PR 不改 `specBinding.ts`。

### 5. 不做的事

* 本 PR 的 `publishers` / `journals` **留空**，不含任何真实期刊数值或引文；golden 向量用测试夹具期刊（`fx-*`、`example.invalid`）。真实数据按出版社分 PR，每条逐一人工核对，没核过的不进库。
* 不做继承链、不做期刊版本历史（出处的 `retrieved` 即快照时间）、不自动跟随网页变化（出处巡检脚本另起 PR，变了只开 issue、不自动改数值）。

## 后果

* 加一本期刊 = 在 `journals.json` 里加一条，求值器、预检、proof report 都不用动。
* 两个实现靠 `tests/golden/journal_vectors.json`（`scripts/gen_journal_vectors.py`）对齐；每个校验失败码至少有一条向量。
* 1.0 收敛纪律：这是用户 2026-10-31 提出并确认范围的新能力，是否登记 `docs/1.0-release-readiness.md` 的豁免由维护者决定（本 PR 未登记）。
