# web/ — 前端规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`；引擎与后端在 `src/tavotto/AGENTS.md`。
本层规则的**全文按主题**在 `docs/rules/frontend/`：先在下表按路径找到主题，
读那一份细则与它点名的 ADR，再动手。看护用例在细则头部。下表是生成的：改细则头部的 `agents-route` 块，再
`python scripts/dev/gen_agents_sheets.py --write`，别手改；这里不放第二份全文。

技术栈：Vite + React 19 + TS + Tailwind v4。品牌常量唯一出处 `web/src/lib/brand.ts`
（与 `engine/brand.py` 同源），界面不得手写产品名。

## 本层不可破坏

- **一个文档，不是两套应用**：没有第二个 `documentStore`、第二个 override writer、
  第二份对象模型；一切文档改动经 `documentStore.commit / beginTxn / endTxn`，
  预览平面（`svgPreviewStore`）只改 DOM、不 commit、不进历史、不发后端。
- **旧 SVG 可以显示，旧 manifest 不得作为几何写操作的权威输入**（ADR 0017）：
  读 bbox / anchor / position / geometry 之后要写文档的一律走 `exactPanelRender`
  一族；权威缺席时命中层停摆、不清选区、入口置灰。
- **撤销防线**：`txnUpdate` 无事务时丢弃更新，绝不静默直写 doc；一切撤销入口走
  `runUndoRedo`；离散动作执行前先 `gestureCoordinator.finishActiveGesture()`，不经 keydown / 指针的
  入口（系统菜单、原生剪贴板事件）一律走 `runDiscreteAction` 闸门（先判让位、不让位才收）。
- **渲染态按「文件 + 变体」分键**（`renderKeyOf`）；SVG 与 manifest 必须同一次
  响应；只有含 `role=="image"` 的面板在连续调整期间降 dpi。
- **画法可以换，能编辑的东西一个都不许少**：`raster` ≠ 只读；二道闸只在
  `resolvePreview()` 一处；超预算只丢 `svg` 字符串、不删 `PanelRender`。
- **「这份项目有什么问题」只有一条链**（`preflight.runSpec` → `lib/validation` →
  `validationStore` → 问题面板），导出对话框不跑第二遍求值器。
- **事实在后端，前端只翻译不判断**：readiness 六态十码、`marker_current` /
  `cmap_current`、`baked_current`、`execution_profile`——界面里不许出现第二份判据
  （没有 `!!script` 的状态分支）。
- **会在项目之间存活的 store 都有项目代际**：`clear()` 换代 + 清 inflight，
  A 项目的响应绝不落进 B；请求序号挡旧响应、发请求那一刻的 pj 挡串项目、失败不清空。
- **选择器认稳定 `data-*`，不认 aria-label / 文案 / class / role**；指代单例不许
  「取第一个匹配」；活动区 role 绝不当选择器（门禁零豁免）。
- **公共 primitive 只在 `components/ui/`，同类控件出现第二套先删第二套**；图标只有
  `components/ui/icons` 一套；Tailwind 扫描面 = `web/src`（代码注释里的完整类名会进产物）。

## 按改动路径找细则

<!-- BEGIN GENERATED: agents-routes -->
| 改到 | 主题（细则在 `docs/rules/frontend/`） | 必守要点 |
| --- | --- | --- |
| 任何前端改动的验证方式、`index.css` 的扫描面、e2e 定位与溢出尺子 | 验证 → `verification.md` | `pnpm build` 才是类型检查；`web/src` 改了要重建 playground 产物；横向溢出只用 `e2e/overflow.ts` 一把尺 |
| `store/renderStore.ts`、`store/renderScheduler.ts`、`lib/bakedBaseline.ts`、`hooks/useEngineSync.ts` | 渲染态：按「文件 + 变体」分键 → `render-state-keying.md` | 键唯一出处 `renderKeyOf`；调度只住 `renderScheduler`；`latest` 退路只能看不能写 |
| 任何读 manifest 几何后写文档的动作、`panelDisplayView`、override upsert | 显示回退 ≠ 几何权威 → `display-fallback-vs-geometry-authority.md` | 显示 API 与权威 API 分开；没有视觉位移就不写 override；重复 (gid, prop) 读写都取最后一条（`effectiveOverride`） |
| `lib/previewBudget.ts`、`PanelView` 的分档、`mergeRender`、内嵌画布位图 | 预览表示法：vector / hybrid / raster → `preview-representation.md` | 加字段协议（老后端 = `VECTOR_PREVIEW`）；hybrid 不新增分支 |
| `renderStore` 的 `dropSvgPayload` / `residentSvgBytes` / pin 集合 | SVG payload 的字节预算 → `svg-byte-budget.md` | 条目数与字节两条策略并存；三条 pin 一条不能少 |
| `store/svgPreviewStore.ts`、`lib/svgStyle.ts`、`trackPointer`、`useFieldGesture` | 假实时预览：预览平面与历史平面严格分开 → `fake-realtime-preview.md` | 临时 transform 从 base 现算；`pointercancel` 与 `pointerup` 分开；样式预览与 `applyStyleEdit` 共用 `styleTargets` |
| `lib/validation.ts`、`store/validationStore.ts`、`lib/issueFocus.ts`、`lib/problemList.ts`、`lib/subplotParts.ts`、导出对话框的摘要、`uiStore` 的 status 主人 / `useServerEvents` 的 `render.failed`↔`render.done` toast | 统一检查与问题定位 → `validation-and-problem-navigation.md` | 摘要只由 `summaryFor()` 组装；定位只有 `focusObject()` 一处；失败 toast 挂主人，同项目同文件渲染成功才撤 |
| `lib/typography.ts`、`typographyAdapter.ts`、`TypographyControls.tsx`、`lib/glyphPlan.ts`、图内 pt 量的写入器 | 属性能力层与 Typography 控件 → `typography-capability-layer.md` | 不许按对象类型 switch 写属性；值四档不压扁；`propertyPathOf` 唯一；图内 pt 量换算只在 `pagePtLens` |
| `components/inspector/presentation/`、`MarkerPicker`、`ColormapPicker`、字体下拉并表 | 图内属性的展示注册表 → `inspector-presentation-registry.md` | 排版决策唯一出处；条件展开只有 `registry.fieldVisible`；低频项一律是摘要行（`ui/SummaryRow`，模板 `folds` 认领） |
| `lib/legendModel.ts`、`LegendCard`、`LegendSpacingCard`、`setLegendPlacement`、`lib/legendScale.ts` / `startLegendScale` | 图例条目与绑定 → `legend-entries-and-binding.md` | 脱开判据 = 任一 `handle_*` override 在；`LEGEND_ENTRY_STYLE_PROPS` / `LEGEND_BINDINGS` 与引擎严格同源 |
| `lib/tickSides.ts`、`ElementHitLayer` 的边框命中区、`TickAndSpineDiagram`、`applyTickSidePlan` | 坐标轴边框的语义命中区与四边刻度 → `spine-zones-and-tick-sides.md` | 命中函数纯；状态派生自 `direction` + `ticks_<side>`；一次 commit |
| `canvas/context-bar/`、`actions.beginCrop`、`store/arrangeStore`、`arrangeButtons.ts` | 多选浮动栏与共享排列参照 → `multi-selection-context-bar.md` | 进裁剪一律 `beginCrop`；浮动栏只发意图；参照只有 `arrangeStore` |
| `canvas/ObjectContextMenu.tsx`、`QuickEdit.tsx`、`quickEditStore`、`rebuildPanel`、`SyncOverridesDialog` / `syncOverridesStore`、写回窗口的「写回记录」页 `WriteBackRecords` | 画布对象的右键菜单 → `object-context-menu.md` | 五份清单只发意图；「更改为」的判据在 `lib/shapeSwitch.ts`；同步并写回只走标准 `WriteBackDialog` |
| `SettingsDialog`、`settings/*`、`store/packageStore.ts`、`store/updateStore.ts`、遥测同意控件 | 设置外壳与包管理 → `settings-shell-and-packages.md` | 外壳尺寸是合同；`run` 只在 `PackagesSettings` 里被调；查找只在点「在 PyPI 查找」时出网 |
| `lib/activity.ts`、`store/onboardingStore.ts`、`lib/onboarding/*`、`data-*` 锚点、活动区 | 交互式 Onboarding 与本地活动信号 → `onboarding-and-activity.md` | 活动信号闭集；step id 是持久化格式；`[role=status]` 绝不当选择器 |
| `store/liveSync.ts`、`useServerEvents`、`lib/activityTelemetry.ts`、命令面板 id | Codex / AI 刷新、入口整合与遥测映射 → `ai-refresh-and-telemetry-mapping.md` | 刷新入口只有 `refreshProjectNow()`；活动 → 遥测映射只在一处 |
| `web/src/diagnostics/`、`data-display-key`、`settings/DiagnosticsSendDialog.tsx`、`store/diagSendStore.ts`、`components/contact/*`、`store/contactStore.ts` | 前端诊断：状态快照与交互轨迹 → `frontend-diagnostics.md` | 只观察不当真源、吞异常；不记 mousemove；诊断与联系作者只在用户点「发送」时发出、失败只给复制 |
| `web/src/perf/`、`PerfProbeHud`、`interactionStore.begin/end`、热路径上的 `perfSpan` / `perfInput` / `perfCount` | 拖动性能探针 → `perf-probe.md` | 热路径只 import `perf/core.ts`、没录制时零成本；自动测试绝不改文档 |
| `lib/pathGeom.ts`、`lib/shapeGeometry.ts`、`pickElement` / `pickElementStack`、⌥ 轮换、`patchContents`、图内拖动吸附 `inFigureSnapCandidates`、随行元素 `companionPatchesFor`、图内平移 `InFigureMove`、方向键微调 `canvas/nudge.ts` | 命中与选择几何 → `hit-and-selection-geometry.md` | 填充按 nonzero、距离换到 mm；描边先于填充；⌥ 只换选中且必须说出口；拖动与方向键共用一份 `InFigureMove` |
| `lib/session.ts`、`types/document.ts`、`documentStore`、`lib/autosave/`、`assetStore`、`lib/projectDocs.ts`、`store/documentRestore.ts`、排版时间线（完整清单见细则） | 项目、文档模型与自动保存 → `project-document-and-autosave.md` | schema 3 只在持久化边界；外部派生唯一写入口 `applyDerivedUpdate()`；手势中不许写；文档 ready 只看恢复门（ADR 0120） |
| 画布标签图层、`CanvasThumb`、标注与 `lib/shapeSwitch.ts`、混排对齐、`writeBackAnnotations.ts`、`EmptyState`、`viewportStore.fitted`、拖页面边 / 单图拖图边 `PageEdgeResize` · `figureEdgeDrag` | 画布对象、标注与工作区视口 → `canvas-objects-and-workspace.md` | 类型切换一次 commit 不换 id；「适应画布」是模式；加图分层链 `addFigureToLayout` → `addPanelToCanvas` → `actions.addPanel` |
| `AssetBrowser`、`ScriptLibrary`、`scriptRunStore` / `runtimeAssetStore` / `scriptLibraryStore` / `assetStore` / `depRepairStore` / `envStore` / `aiStore`、`FigurePickerDialog` | 素材库普通入口 → `asset-library.md` | 七个 store 都有项目代际；同脚本防并发；多 Figure 结果绝不只显示第一张；素材卡加图走 `addFigureToLayout` |
| `lib/execTrace.ts`、`components/ai/traceWaterfall/*`、`components/ai/TraceDetails.tsx`、`fetchExecTrace` | 执行记录 Waterfall（ADR 0151） → `execution-trace-ui.md` | 转换只在 `lib/execTrace.ts`；不判定、不补 Span、不拿相邻时间算耗时；不知道就写 —；权威是 REST 快照 |
| `NativeConfirmDialog`、`nativeSessionStore`、`nativePanelState`、`?native=` / `tavotto:open` 的 `native` | `tavotto run` 的桌面面 → `tavotto-run-desktop-side.md` | 前端只提交不透明 `native_id`；确认屏是闸（`blockDismiss`）；事件按 `sequence` 判序；未知不等于 native |
| `lib/readinessText.ts`、项目接入 / 扫描 / 准备 store、左栏（偏好、样式面板、`ElementTree`）、工作目录与依赖准备对话框、`lib/importOrigin.ts`（完整清单见细则） | 接入状态与左侧外壳 → `readiness-and-left-shell.md` | 句子只有 `statusLabel()` / `reasonText()`；界面不执行动作；自动让位绝不写回偏好；来源未定的 import 前端零解析 |
| `lib/exportRequest.ts`、`store/exportStore.ts`、`lib/exportFigures.ts`、`lib/exportName.ts`、`lib/artifactInspection.ts`、`lib/imgRetry.ts`、导出对话框 | 统一导出管线 → `export-pipeline.md` | 载荷只由 `buildExportRequest()` 构造；文件名规则严格同源对；产物核验的解读只有 `inspectionState()` 一处 |
| `store/workspace.ts`、`openFastEdit` / `addFigureToLayout` / `focusLayoutPanel`、`lib/originalSpec.ts` | 两条工作流与原图规格 → `workflows-and-original-spec.md` | 模式由画布图数推出（1 张=单图编辑），`settleMode` 唯一写入点；三个稳定动作是唯一出口 |
| `lib/desktop.ts`、`hooks/menuActions.ts`、`lib/updateNotice.ts`、`UpdateNoticeDialog` | 桌面感知与更新 → `desktop-and-update.md` | 组件不得直接 import `@tauri-apps/*`；只查一条更新通道；菜单转发与键盘同一个让位判据 |
| `web/src/i18n/`、`UiMessage`、`errors.json`、`backendErrorText()`、`lib/stylePresets.ts` / `specBinding.ts` / `exportDefaults.ts` | 多语言 → `i18n.md` | 资源静态 import；code 一旦发布不能改名；`pnpm i18n:check` 是硬门禁；界面名词只有项目 / 排版 / 画布 / 项目包 |
| `web/src/playground/`、`pyodide.worker.ts`、`prewarm.ts`、`examples/*.py` | 浏览器 playground（网站 /try） → `browser-playground.md` | Pyodide 版本与包白名单钉在 `packaging/playground-runtime.json`；一个文件 = 一个 Worker |
| `components/ui/*`、`src/index.css` 的 `@theme` 与暗色值表、`lib/theme.ts`、图标、设置窗口排版、工作台结构 | UI 视觉纪律 → `ui-visual-discipline.md`（全文 `docs/ux/DESIGN_CONSTITUTION.md`） | 尺寸 / 层级 / 颜色只走语义 token（纸两套同值）；卡只经 `ui/Card`；同类控件第二套先删 |
<!-- END GENERATED: agents-routes -->

## 验证

- `cd web && pnpm test && pnpm build && pnpm i18n:check`（类型检查用 `tsc -b`）。
- `scripts/tailwind-scan-check.mjs` 的反向判据靠本文件常驻的金丝雀 `tracking-widest`（扫描面之外的散文），别删。
- 真浏览器 `pnpm e2e`：先 `python scripts/build_frontend.py`；MCP 画布还需 `python scripts/build_mcp_widget.py`。
- 改了 `web/src`：`python scripts/build_browser_playground.py --check`。构建顺序、包内前端优先级与
  真浏览器判据全文见 `docs/rules/frontend/verification.md`。
