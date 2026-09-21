# ADR 0068：ArtifactInspector、ArtifactManifest 与 D08 检查政策

日期：2026-09-21 · 状态：**Accepted（U08 第二切片；两个后端都接，默认后端不切）**
相关：[0067 候选切换开关与执行侧源](0067-render-backend-switch-and-execution-sources.md)、[0031 导出](0031-unified-export-pipeline.md)、
[0029 出版规范](0029-style-spec-profiles.md)、[0030 预检](0030-validation-and-problem-navigation.md)、[0046 EPS / TIFF](0046-eps-and-tiff-export-formats.md)、
[0051 保留式规范化](0051-preserving-normalization.md)；实施包 `docs/implementation/tavotto-foundation/`（`01_SCOPE_AND_DECISIONS.md`
D08、`04_ARCHITECTURE.md` §1 / §2 ArtifactManifest 合同、`05_TEST_STRATEGY.md` §5、`phases/U08_facade_validation.md`、registry
RC-063 ~ RC-074、RC-083 ~ RC-085）。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 检查什么 | **重新打开封口的 staging 文件**量事实（RC-063：不回传计划里的宽高）：PDF 的页盒 / 旋转 / UserUnit → 可见尺寸、内容流普查（路径 / 文字 / 位图 / Form / 着色）、字体**声明 vs 实际用到**（Tf 真的引用过的）+ 嵌入 / 子集 / ToUnicode、抽回来的文字层、位图有效 ppi（像素 ÷ 累计 CTM）；PNG 的签名 / 逐块 CRC / IHDR / pHYs / IEND；TIFF 的 IFD / 条带越界 / 分辨率标签 | `rendercore/inspector.py`：`observe_pdf` / `observe_png` / `observe_tiff` |
| 计划 / 观测 / 政策分开（04 §2） | manifest 三半张各自独立：`plan` 由生产者填（候选路 `job.plan_facts()` 从 RenderPlan 取期望文字行 / 对象框 / 源与回执公开身份；旧后端只有请求级的页面 / 像素 / ppi / vector），检查器**不改**它；`observed` 只来自字节；`policy` 只由 mode + profile 决定；`artifact.sha256` 是文件字节，发布只是 `os.replace`——发布后的字节就是核过的字节 | `test_every_published_output_carries_a_manifest_whose_sha_is_the_published_bytes` |
| 四值判据 | `verified / failed / unknown / not_applicable`（`VERDICTS` 闭集）。**unknown 不是 verified**（RC-070 must_fail：None 按 truthy 通过），也不是 failed | `test_unknown_is_never_reported_as_verified_and_strict_blocks_on_it` |
| 两档政策（D08） | `standard`（缺省，老客户端 = standard）：必需 = 完整性 + 核心尺寸（PDF 页面 ±0.2 pt；位图 ±1 px）；可选项 unknown / failed 只写进 manifest 说明，**不拒绝合法成果**。`strict`（用户在请求 `inspection.mode` 里选）：PDF 必需 + carrier / fonts_embedded / text_layer / image_ppi，位图必需 + raster_density；必需项失败**或 unknown** 都阻断 | `test_strict_inspection_blocks_on_required_unknown_or_failure_but_standard_only_notes`、`test_raster_policy_size_is_required_and_density_only_under_strict` |
| 阈值从哪来（RC-071） | 严格政策的 `min_raster_dpi` 只从 `profilestore.resolve_spec(profile_id)` 解析出的出版规范来（同一个 id 两入口同一份），检查器自己不写数；规范 id 不认识 → `bad_inspection`，不退默认 | `test_strict_inspection_takes_its_thresholds_from_the_publication_profile` |
| 在哪一步（RC-073） | `exportjob.run(..., inspect=)` 钩子：`produce` 之后、**提交点之前**（`_committed` 置上之前）；拒绝的 `Produced` 换成 `artifact_rejected`（带 manifest 投影），不发布——不合格的文件不出现在用户的导出目录；合格的附 manifest 发布。partial 语义不变（一项拒、另一项照常） | `test_the_inspect_hook_runs_before_the_commit_point_and_its_verdict_enters_the_outputs`、`test_a_partial_rejection_publishes_the_good_format_and_withholds_the_bad_one` |
| 客户端 proof（RC-074） | 客户端随请求发来的样式检查报告**从不**进检查器；坏文件搭配 `errors=0` 的报告照样拒 | `test_a_client_proof_cannot_override_the_server_verdict` |
| 有效 ppi（RC-068） | PDF 内位图：`px / (‖(a,b)‖/72)`，CTM 沿 q/Q 栈与 Form /Matrix 累计；`raster_density` = 文件像素 ÷ 计划页面尺寸（观测像素、计划物理尺寸），**不是**请求的 ppi 也不是文件声明的标签（那一维归可选项 `dpi_tag`） | `test_image_effective_ppi_comes_from_pixels_over_the_accumulated_ctm` |
| 预算（RC-072） | 内容流遍历有 `Budget`（深度 8 / Form 256 / 指令 500k）；耗尽 → 普查 / 字体 / 文字 / 位图全部 unknown（strict 因此阻断、standard 只说明），**不假装看完了** | `test_a_self_referencing_form_exhausts_the_budget_and_everything_downstream_is_unknown` |
| 字体（RC-065 / RC-066） | 声明 ≠ 使用（顶层 F1 没用、Form 里的 F2 才是用到的）；嵌入 / 子集 / ToUnicode 三个事实分开报；Type 3 的嵌入记 unknown；文字层的判据是**抽回来的字符串**含期望的每一行（ToUnicode 存在 ≠ 映射正确：把 `<0048>` 改成 `<0058>` 就 failed） | `test_fonts_are_reported_as_used_not_merely_declared_and_forms_are_walked`、`test_the_candidate_writer_output_verifies_fonts_text_and_carrier_under_strict` |
| 载体（RC-067） | `vector / mixed / raster / empty / unknown` 按顶层对象种类分；计划说矢量、文件全位图 → failed；mixed（面板里的位图源）在矢量计划下 verified 并注明 | `test_image_effective_ppi_…`（carrier failed）、候选 app 用例（mixed） |
| 裁切（RC-069） | 只对**计划里知道包围盒的对象**判（面板框 vs 页面），外来页内部 unknown、注明观察范围；没有对象框就是 unknown | manifest `notes` |
| 没有 pikepdf 的机器 | PDF 走 `observe_pdf_basic`（契约层的 `probe_asset`：打得开、有尺寸）→ 完整性与尺寸照核，其余 unknown 并写明；两者都没有 → integrity **unknown**（不是 verified） | `observe_pdf_any` |
| 检查器自己炸 | `uninspected()`：所有项 unknown、standard 交付并说明、strict 阻断——异常不是通过 | `app._export_inspect` |
| 写回 | staging 完整性 / 尺寸的既有合同不动（`_post_check_size` 落盘后如实报告、不回滚）；候选下 `annotate_asset` 对坏 / 加密的 staging PDF 结构化失败 → 409、原件零改动（RC-059 / RC-060） | `test_writeback_with_annotations_fails_closed_when_the_staged_pdf_is_unusable` |
| 用户可见 | 新码 `artifact_rejected`（params `failed` / `policy`）与 `bad_inspection`（`value`）进两种语言的 `errors.json`；`Output.manifest` 是 `inspector.summary()` 的投影（verdict / 每项四值 / 说明 / sha256 / 载体 / 尺寸 / 字体），**旧字段一个不动**（RC-090）；前端「未核验不显示绿」归第三切片 | `tests/test_error_codes.py` |

## 1. 为什么 standard 下 unknown 不拒

05 §5 / D08：任意外部 PDF（用户脚本产出的原图、外来 PDF 面板）里字体 / 裁切 / 位图的真相不总能观察到——Type 3 字体没有
FontFile、Form 套 Form 有预算、外来页内部不重画就不知道被裁了什么。把这些 unknown 当失败会封死所有正常导出；当通过会把
「没看」说成「看过了」。所以 standard 只对**能可靠量到、且失败就是坏成果**的两项（打得开 / 尺寸对）阻断，其余写进 manifest
让读的人知道哪些没核验；strict 是用户明确选择「按规范来」，那时 unknown 就是「不能证明合规」，按规范阻断。

## 2. 旧后端下的观测事实（不是缺陷修复，只是如实记录）

* 旧后端 `save_png` / `original_png` 写出的 PNG 的 pHYs 是 96 dpi（PyMuPDF Pixmap 默认），不是本次请求的 ppi：`dpi_tag` 在
  默认路上是 `failed`（可选项）。像素数是对的（`size` verified），有效密度按像素 ÷ 页面算也对（`raster_density` verified）。
  改与不改归用户拍板（一行 `pix.set_dpi`），本切片不动默认路径。
* 旧后端的 PDF 用 base-14（不嵌入）：`fonts_embedded` 在默认路上是 `failed`（可选项）；候选写入的是子集嵌入的批准字体
  （verified）。这正是 D07 迁移的意义之一。
* 旧后端路上没有计划里的期望文字行：`text_layer` 恒 unknown（不显示成绿）。

## 3. 反证（每条变异一次就红，`scratchpad/u08/mutate_b.py`）

| 变异 | 红在 |
|---|---|
| 钩子挪到提交点之后 | `test_the_inspect_hook_runs_before_the_commit_point…` |
| 拒绝的照样发布 | `test_a_broken_artifact_is_rejected_before_publish_and_the_export_dir_stays_clean` |
| 检查器读客户端报告 / 尺寸回传计划值 | `test_a_client_proof_cannot_override_the_server_verdict`、`test_a_page_of_the_wrong_actual_size…` |
| unknown 当 verified（strict 不看 unknown） | `test_unknown_is_never_reported_as_verified_and_strict_blocks_on_it` |
| 阈值写死 300 不读规范 | `test_strict_inspection_takes_its_thresholds_from_the_publication_profile` |
| 字体只扫顶层 /Font | `test_fonts_are_reported_as_used_not_merely_declared_and_forms_are_walked` |
| 文字层只看 ToUnicode 存在 | `test_the_candidate_writer_output_verifies_fonts_text_and_carrier_under_strict`（错映射） |
| 预算耗尽当 pass | `test_a_self_referencing_form_exhausts_the_budget…` |
| 有效 ppi 用请求值 | `test_image_effective_ppi_comes_from_pixels_over_the_accumulated_ctm` |
| PNG 不验 CRC / TIFF 不验条带 | `test_a_damaged_png_is_integrity_failed_not_unknown`、`test_tiff_observation_…` |
| 检查器异常当通过 | `test_export_inspection.py`（uninspected 用例） |

## 4. 没做 / 边界

* 不解析着色、透明组内部、复杂裁剪路径；不判外来页内部的裁切；不做 PDF/X；不比像素（像素归写回像素门与校准）。
* 前端展示（未核验不显示绿、strict 的开关、错误文案落位）归第三切片；MCP 的 `export` 回执带 manifest 也归那里。
* `scope=original` 的计划半张只有请求级事实（没有回执）：U09 若要把回执关联到原图导出，接线点在 `_export_produce_original`。
