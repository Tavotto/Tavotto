# Tavotto 统一实施计划 · v1.0

**一份执行入口，两条实现主线，三类验证，逐级生效的门禁。**

本包正式整合 RenderCore、首次打开兼容性、FirstOpenBench CI 三份方案。它不是第四份追加任务书；从这里开始实施时，以本包为统一任务约定，旧包只作为只读来源和技术细节参考，不再把三个旧总提示词叠加执行。

**性质：重构实施规格，不是已实现代码或产品验证报告。** 用户尚未开始重构。本次只核对原方案、最新 main 指针及部分 CI/支持配置，并对本包结构进行程序校验；没有执行 Tavotto 的重构、测试、构建或发布。

详细原审计：`6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。本次采样 main：`8b95256c0d08a14bfcfc4c81358894ef01168933`。二者不同；最新提交没有被全面重新审计，U00 必须以实施时的实际 checkout 更新差异和基线。

## 前置：ci-foundation 已完成（2026-09-16）

先执行 ci-foundation 的 CI00；`ci_hosted_ready` 后进入 U00，`runner_pool_ready` 可以随后完成。CI00 与 U00 只读清点可并行。
——2026-09-16 状态：`ci_hosted_ready: pass`（基于七个 PR 的 full-ci 实测，合入 main 后用第一个 merge_group run 复核，见 [`../ci-foundation/CI_HANDOFF.md`](../ci-foundation/CI_HANDOFF.md) §12）；`runner_pool_ready: not_run`（无部署权限，管理员操作表见 [`../ci-foundation/ADMIN_HANDOFF_RUNNER_POOL.md`](../ci-foundation/ADMIN_HANDOFF_RUNNER_POOL.md)）。U00 引用 `CI_HANDOFF.md`，不重做 CI 调查，不从别的 SHA 借产品资格。

U00 已于 2026-09-20 执行（`plan.json` 里 `implementation_status: done`，产品资格仍 `not_run`）：产出见
[`U00_BASELINE.md`](U00_BASELINE.md)、[`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md)、
[`U00_CAPABILITY_INVENTORY.md`](U00_CAPABILITY_INVENTORY.md)、[`handoffs/U00_baseline.md`](handoffs/U00_baseline.md)。
每个阶段的交接放 `handoffs/`。

U01 已于 2026-09-20 执行（`implementation_status: done`，产品资格仍 `not_run`）：共同合同（ADR 0053）、
异步准备接口、case enrollment 台账（[`enrollment.json`](enrollment.json) / [`ENROLLMENT.md`](ENROLLMENT.md)）、
闭集校验器与 `invariants` job 的三步落点；唯一 enforced 的切片 `U01-S1` 经真实 HTTP 入口走完首开 → 导出
（旧后端终点）。交接见 [`handoffs/U01_contracts.md`](handoffs/U01_contracts.md)。

U02 已于 2026-09-20 执行（`implementation_status: done`，产品资格仍 `not_run`）：两个独立技术证明——
render_spike（ADR 0055：PDFium 栅格 + pikepdf/fontTools/HarfBuzz 受限 emitter，Liberation + Noto Sans SC 为默认字体，
串行 render child，最小 PyInstaller 冻结）与 runtime_spike（ADR 0056：uv + python-build-standalone 私有 Python +
离线 wheel，坏 hash / 无网负例）。证据在 [`evidence/u02/`](evidence/u02/)，spike 代码在 `scripts/dev/u02_spikes/`
（不进产品 import 图，候选包只在独立 venv）。交接见 [`handoffs/U02_spikes.md`](handoffs/U02_spikes.md)。

U06 已于 2026-09-21 执行（`implementation_status: done`，产品资格仍 `not_run`，**不切默认**）：RenderCore 的第一个
产品切片——纯模型层 `src/tavotto/rendercore/`（Render IR / RenderPlan 编译 / 排版 / 字体注册表，只许标准库，
ADR 0059）+ 字体政策与可检索文字写入（allowlist 逐字节钉住的 Liberation + Noto Sans SC，pikepdf/fontTools/HarfBuzz
适配层走 pyproject 的 `rendercore` extra，ADR 0060）。真字体 → RenderPlan → PDF → 四把独立读取器的证据在
[`evidence/u06/`](evidence/u06/)（生成器 `scripts/dev/u06_evidence.py`）；D07 会变的旧断言逐条在
[`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md) 的 `migration_evidence` 指向替代用例。交接见
[`handoffs/U06_ir_text.md`](handoffs/U06_ir_text.md)。

U07 已于 2026-09-21 执行（`implementation_status: done`，产品资格仍 `not_run`，**不切默认**）：合成（ADR 0065）——外来页作
Form XObject 整页矢量导入（页盒 / `/Rotate` / `/UserUnit` 由 qpdf 折进 /Matrix、恰好一次），crop / 翻转 / 旋转的顺序合同只在
`rendercore/placement.py`，面板 opacity 是透明组、镜像是负缩放（不再退位图），位图经 `rasterio`（Pillow，正式依赖）成
straight-alpha 的 `RasterBuffer`，pikepdf 正式裁决；栅格（ADR 0066）——应用自己的 PDFium render child（一把锁串行 + 有界
队列 + 超时 kill / reap / 重启），PNG 与 TIFF 从同一个 RasterBuffer 编码、只从 Canonical PDF 来，预览缓存键 = 内容身份 +
后端 build + 字体政策，旧新后端按 case 阈值校准，U02 spike 的 render 半边退役。证据在 [`evidence/u07/`](evidence/u07/)
（生成器 `scripts/dev/u07_evidence.py`，最小 freeze `scripts/dev/u07_freeze_child.py`）；enrollment 加 `U07-R1`（observing）。
交接见 [`handoffs/U07_compose_raster.md`](handoffs/U07_compose_raster.md)。

U08 已于 2026-09-21 执行（`implementation_status: done`，产品资格仍 `not_run`，**不切默认**）。第一切片（ADR 0067）：
契约层 `pdfbackend/__init__.py` 按 `TAVOTTO_RENDER_BACKEND` 在 PyMuPDF（默认）与 `rendercore/facade.py`（候选，19 项同签名 +
Canvas 面）之间选一个，选定即定、不静默回退；产品导出路在候选下把 `scope=canvas` 交给 `job.produce` + `ExecutionSourceResolver`
（带 override / runtime 素材由当次 worker 现画并附回执，native 会话经 `receipt.from_native_session`），`scope=original` /
预览 / 探测 / 写回标注经契约层自动切换；候选的覆盖表与字形向量各一份生成物（与默认表的差异钉成闭集）；旧契约用例在候选下
逐字重跑（`scripts/dev/u08_parity.py`，清单在 ledger `candidate_parity`，14 条实现特定断言各带替代证据，用户合同一条不删）。
第二切片（ADR 0068）：`rendercore/inspector.py` 在提交点之前重新打开封口产物量事实（完整性 / 尺寸 / 字体实际使用 / 文字层 /
有效 ppi / 位图密度，四值判据 `unknown` 不是 `verified`），D08 两档政策（standard 缺省只拦完整性与核心尺寸、strict 按出版规范
阈值连 unknown 也拦），回执 `outputs[].manifest`。第三切片（入口审计）：MCP `tavotto_export` 接同一份检查器接线、回执 `files[].manifest`
与给模型的三组点名；前端每件产出一行（全部可判项 verified 才画绿、未核验中性点名、失败红色点名、没有 manifest = 未核验）、高级选项
「严格核验产物」、`/api/render` 背压的有界重试；后端阶段名 ↔ 前端文案同源判据；Playground 审计（无导出面板、不发 `/api/*`）。
证据在 [`evidence/u08/`](evidence/u08/)；enrollment 加 `U08-R1`（observing）；facade 19 项在
[`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md) 全部有 U08 迁移证据。交接见
[`handoffs/U08_facade_parity.md`](handoffs/U08_facade_parity.md)。

## 从哪里开始

先读 [执行总提示词](00_MASTER_PROMPT.md)、[范围与修改决策](01_SCOPE_AND_DECISIONS.md)、[路线图](02_ROADMAP.md) 和 [CI 生效政策](03_CI_POLICY.md)。随后只执行 [U00](phases/U00_baseline.md)，不要在第一步删除 PyMuPDF、更换根许可证或一次启用全部兼容门禁。

以后每次会话读取总提示词、当前阶段、上一阶段交接即可。详细合同见 [职责与数据边界](04_ARCHITECTURE.md)、[测试与判据](05_TEST_STRATEGY.md)、[启用/切换/发布](06_ENABLE_AND_RELEASE.md)。

## 核心完成意味着什么

在现有已承诺能力不被悄悄削弱的前提下，完成 PyMuPDF 发行依赖替换；解决受支持 Python 的项目环境选择、正确数据上下文、额外依赖联合准备及无系统 Python 的私有运行环境路径；从正常入口完成原图、编辑、重放和最终导出，并有真实的分平台证据。

不是让任意 Python / PDF / 字体 / GPU / 网络文件系统都能无感兼容。无法推断的科学意图需要最少必要确认；未实现的范围不能称通过。

## 三类清单不能混成测试总数

`registry.json` 完整保留 **114 条 RC 原要求、74 条兼容原要求和 32 个首开场景**，共 220 个来源条目，其中 188 条是要求、32 条是场景，不等于 220 条互不重复的测试。每项注明统一负责人阶段、解释/拆分/后置决策、生效规则；原文不丢。所有产品执行状态初始仍为 `not_run`。

[生成的追踪表](generated/TRACEABILITY.md) 与 [首开场景编排](generated/FIRST_OPEN_SCHEDULE.md) 由 JSON 派生，不手工维护第二套事实。

## 可复制的开工指令

```text
请在 Tavotto 仓库实际执行本包 U00，之后按本包依赖推进。
本包是 RenderCore、compatibility、FirstOpenBench 的统一实施入口；
不要叠加三个旧包的总提示词，不要第一步执行全部最终验收。

先读当前仓库/目标目录 AGENTS 与 CLAUDE 规则，检查 HEAD 和工作区。
读取 00_MASTER_PROMPT.md、01_SCOPE_AND_DECISIONS.md、03_CI_POLICY.md，
再读 phases/U00_baseline.md。

建立真实基线、来源映射和最小 fixture；不切默认、不移除旧依赖、
不改 LICENSE、不改保护规则，不覆盖我的未提交改动。
执行到哪里就如实记录代码、命令、退出码、证据和 not_run。
每个小切片有对应测试，合并资格、默认启用资格和发行资格分开。
不推送、不合并、不发布，也不自动更新 plugin-stable。
```

## 包内检查

```bash
python tools/validate_plan.py .
python -m unittest discover -s tools -p 'test_*.py' -v
```

这两条只检查本任务书的来源、依赖图和映射，**不是 Tavotto CI，更不能证明兼容性或 PDF 渲染通过**。不要把本包固定12阶段/220来源的自检数量照搬成仓库永久required检查；实际产品runner与准入表在U01按当前代码建设。产品命令在U00确认，后续由各阶段新增。

仓库落点：`docs/implementation/tavotto-foundation/`（U00 于 2026-09-20 入库；`tools/*.py` 只做了 ruff 的 import 排序与格式化，校验逻辑一字未改）。
