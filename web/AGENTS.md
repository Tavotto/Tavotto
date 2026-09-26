# web/ — 前端规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`；引擎与后端在 `src/tavotto/AGENTS.md`。
本层规则的**全文按主题**在 `docs/rules/frontend/`：先在下表按改动路径找到主题，
读那一份细则（各 2–13 KB）与它点名的 ADR，再动手。规则改在细则文件里，并同步
这里那一行；这里不放第二份全文。

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
  `runUndoRedo`；离散动作执行前先 `gestureCoordinator.finishActiveGesture()`。
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

| 改到 | 主题（细则在 `docs/rules/frontend/`） | 必守要点 | 看护 |
| --- | --- | --- | --- |
| 任何前端改动的验证方式、`index.css` 的扫描面、e2e 定位与溢出尺子 | 验证 → `verification.md` | `pnpm build` 才是类型检查；`web/src` 改了要重建 playground 产物；横向溢出只用 `e2e/overflow.ts` 一把尺 | `scripts/tailwind-scan-check.mjs`（反向判据的金丝雀 `tracking-widest` 常驻在本文件这一行，删了它门禁红）、`e2e/a11y.spec.ts`、`e2e/overflow.ts` |
| `store/renderStore.ts`、`store/renderScheduler.ts`、`lib/bakedBaseline.ts`、`hooks/useEngineSync.ts` | 渲染态：按「文件 + 变体」分键 → `render-state-keying.md` | 键唯一出处 `renderKeyOf`；调度只住 `renderScheduler`；`latest` 退路只能看不能写 | `store/renderStore.test.ts`、`store/renderScheduler.test.ts`、`lib/bakedBaseline.test.ts`、`hooks/useEngineSync.test.ts`、`canvas/panelPreviewMode.test.tsx`、`components/left/elementTreeRerender.test.tsx`、`tests/test_engine_variants.py`、`tests/test_paths_and_baked.py` |
| 任何读 manifest 几何后写文档的动作、`panelDisplayView`、override upsert | 显示回退 ≠ 几何权威 → `display-fallback-vs-geometry-authority.md` | 显示 API 与权威 API 分开；没有视觉位移就不写 override；重复 (gid, prop) 读写都取最后一条（`effectiveOverride`） | `store/geometryAuthority.test.ts`、`store/alignAction.test.ts`、`canvas/alignUndoConvergence.test.tsx`、`test/renderFixtures.ts` 的 `seedExactRender()` |
| `lib/previewBudget.ts`、`PanelView` 的分档、`mergeRender`、内嵌画布位图 | 预览表示法：vector / hybrid / raster → `preview-representation.md` | 加字段协议（老后端 = `VECTOR_PREVIEW`）；hybrid 不新增分支 | `canvas/panelPreviewMode.test.tsx`、`lib/previewBudget.test.ts`、`store/renderStore.test.ts`、`mcp/session.test.ts`、`tests/test_preview_budget.py` |
| `renderStore` 的 `dropSvgPayload` / `residentSvgBytes` / pin 集合 | SVG payload 的字节预算 → `svg-byte-budget.md` | 条目数与字节两条策略并存；三条 pin 一条不能少 | `store/svgMemoryBudget.test.ts`、`hooks/useEngineSync.test.ts`、`embedded/session.test.ts` |
| `store/svgPreviewStore.ts`、`lib/svgStyle.ts`、`trackPointer`、`useFieldGesture` | 假实时预览：预览平面与历史平面严格分开 → `fake-realtime-preview.md` | 临时 transform 从 base 现算；`pointercancel` 与 `pointerup` 分开；样式预览与 `applyStyleEdit` 共用 `styleTargets`；手势不许比它的依据活得更久（拖动中 Esc 先 `cancelActivePointerGesture()`、等图时依据不在文档里就 `settleUnbackedCommit` 还原、改倍率重建基准） | `lib/svgStyle.test.ts`、`store/svgPreviewStore.test.ts`、`canvas/fakeRealtimeDrag.test.tsx`、`e2e/fake-realtime.spec.ts`、`canvas/renderSwapFeel.test.tsx`、`canvas/dragGestureLifecycle.test.tsx`、`e2e/drag-gesture-lifecycle.spec.ts` |
| `lib/validation.ts`、`store/validationStore.ts`、`lib/issueFocus.ts`、`lib/problemList.ts`、导出对话框的摘要 | 统一检查与问题定位 → `validation-and-problem-navigation.md` | 摘要只由 `summaryFor()` 组装；定位只有 `focusObject()` 一处 | `lib/validation.test.ts`、`lib/issueFocus.test.ts`、`lib/issueFix.test.ts`、`lib/problemList.test.ts`、`store/validationStore.test.ts`、`tests/test_preflight.py` |
| `lib/typography.ts`、`typographyAdapter.ts`、`TypographyControls.tsx`、`lib/glyphPlan.ts` | 属性能力层与 Typography 控件 → `typography-capability-layer.md` | 不许按对象类型 switch 写属性；值四档不压扁；`propertyPathOf` 唯一 | `lib/typography.test.ts`、`typographyAdapter.test.tsx`、`lib/canvasTextFont.test.ts`、`tests/test_typography_families.py` |
| `components/inspector/presentation/`、`MarkerPicker`、`ColormapPicker`、字体下拉并表 | 图内属性的展示注册表 → `inspector-presentation-registry.md` | 排版决策唯一出处；条件展开只有 `registry.fieldVisible` | `presentation/registry.test.ts`、`controls/pickers.test.tsx`、`colorScalePanels.test.tsx`、`figureFontFamilies.test.tsx`、`tickTaskCard.test.tsx` |
| `lib/legendModel.ts`、`LegendCard`、`LegendSpacingCard`、`setLegendPlacement`、`lib/legendScale.ts` / `startLegendScale` | 图例条目与绑定 → `legend-entries-and-binding.md` | 脱开判据 = 任一 `handle_*` override 在；`LEGEND_ENTRY_STYLE_PROPS` / `LEGEND_BINDINGS` 与引擎严格同源 | `inspector/legendCard.test.tsx`、`legendSpacingCard.test.tsx`、`controls/pickers.test.tsx`、`tests/test_legend_binding.py`、`canvas/inFigureDrag.test.tsx` |
| `lib/tickSides.ts`、`ElementHitLayer` 的边框命中区、`TickAndSpineDiagram`、`applyTickSidePlan` | 坐标轴边框的语义命中区与四边刻度 → `spine-zones-and-tick-sides.md` | 命中函数纯；状态派生自 `direction` + `ticks_<side>`；一次 commit | `lib/tickSides.test.ts`、`canvas/spineZones.test.tsx`、`inspector/tickTaskCard.test.tsx` |
| `canvas/context-bar/`、`actions.beginCrop`、`store/arrangeStore`、`arrangeButtons.ts` | 多选浮动栏与共享排列参照 → `multi-selection-context-bar.md` | 进裁剪一律 `beginCrop`；浮动栏只发意图；参照只有 `arrangeStore` | `context-bar/position.test.ts`、`multiSelectionBar.test.tsx`、`store/alignSelectedTo.test.ts`、`store/arrangeStore.test.ts`、`canvas/contextBar.test.tsx` |
| `canvas/ObjectContextMenu.tsx`、`QuickEdit.tsx`、`quickEditStore`、`rebuildPanel` | 画布对象的右键菜单 → `object-context-menu.md` | 五份清单只发意图；「更改为」的判据在 `lib/shapeSwitch.ts` | `canvas/objectContextMenu.test.tsx`、`store/quickEditActions.test.ts`、`e2e/quick-menu.spec.ts`、`tests/test_engine_invalidate.py` |
| `SettingsDialog`、`settings/*`、`store/packageStore.ts`、`store/updateStore.ts`、遥测同意控件 | 设置外壳与包管理 → `settings-shell-and-packages.md` | 外壳尺寸是合同；`run` 只在 `PackagesSettings` 里被调；查找只在点「在 PyPI 查找」时出网 | `SettingsDialog.test.tsx`、`settings/PackagesSettings.test.tsx`、`settings/PackagesSearch.test.tsx`、`store/projectSwitchPackages.test.ts`、`updateStates.test.tsx`、`SettingsTelemetry.test.tsx`、`e2e/settings-shell.spec.ts` |
| `lib/activity.ts`、`store/onboardingStore.ts`、`lib/onboarding/*`、`data-*` 锚点、活动区 | 交互式 Onboarding 与本地活动信号 → `onboarding-and-activity.md` | 活动信号闭集；step id 是持久化格式；`[role=status]` 绝不当选择器 | `onboardingStore.test.ts`、`activity.test.ts`、`lib/onboarding/*.test.ts`、`onboardingLayer.test.tsx`、`lib/liveRegionSelector.test.ts`、`e2e/tutorial.spec.ts` |
| `store/liveSync.ts`、`useServerEvents`、`lib/activityTelemetry.ts`、命令面板 id | Codex / AI 刷新、入口整合与遥测映射 → `ai-refresh-and-telemetry-mapping.md` | 刷新入口只有 `refreshProjectNow()`；活动 → 遥测映射只在一处 | `lib/activityTelemetry.test.ts`、`CommandPalette.test.tsx`、`store/projectReadinessStore.test.ts`、`hooks/useServerEvents.test.ts` |
| `web/src/diagnostics/`、`data-display-key` | 前端诊断：状态快照与交互轨迹 → `frontend-diagnostics.md` | 只观察不当真源、吞异常；定长 240 纯内存；不记 mousemove | `diagnostics/*.test.ts`（含 `privacy.test.ts`）、`tests/test_diagnostics_bundle.py` |
| `web/src/perf/`、`PerfProbeHud`、`interactionStore.begin/end`、热路径上的 `perfSpan` / `perfInput` / `perfCount` | 拖动性能探针 → `perf-probe.md` | 热路径只 import `perf/core.ts`、没录制时零成本；自动测试绝不改文档 | `perf/core.test.ts`、`perf/synthetic.test.ts`、`perf/probeStore.test.tsx`、`e2e/perf-probe.spec.ts`、`tests/test_perf_probe.py` |
| `lib/pathGeom.ts`、`lib/shapeGeometry.ts`、`pickElement` / `pickElementStack`、⌥ 轮换、`patchContents`、图内拖动吸附 `inFigureSnapCandidates`、随行元素 `companionPatchesFor` | 命中与选择几何 → `hit-and-selection-geometry.md` | 填充按 nonzero、距离换到 mm；⌥ 只换选中且必须说出口 | `canvas/patchCarryDrag.test.tsx`、`lib/pathGeom.test.ts`、`elementPathSelection.test.tsx`、`shapeOutline.test.tsx`、`canvas/twinAxesPick.test.tsx`、`e2e/twin-axes-pick.spec.ts`、`canvas/inFigureDrag.test.tsx`、`canvas/axesCompanionDrag.test.tsx` |
| `lib/session.ts`、`types/document.ts`、`documentStore` 的 `loadSeq` / `derivedSeq` / `registerTxnFinalizer`、`lib/autosave/diskWriter.ts`、`panelSourceSync.ts`、`assetStore`、`lib/projectDocs.ts` | 项目、文档模型与自动保存 → `project-document-and-autosave.md` | schema 3 只在持久化边界；外部派生唯一写入口 `applyDerivedUpdate()`；手势中不许写 | `store/documentStore.test.ts`、`store/layoutAutoReflow.test.ts`、`store/panelSourceSync.test.ts`、`hooks/useEngineSync.test.ts`、`lib/projectDocs.test.ts`、`e2e/cross-tab-paste.spec.ts`、`lib/autosave/diskWriter.test.ts` |
| 画布标签图层、`CanvasThumb`、标注与 `lib/shapeSwitch.ts`、混排对齐、`writeBackAnnotations.ts`、`EmptyState`、`viewportStore.fitted` | 画布对象、标注与工作区视口 → `canvas-objects-and-workspace.md` | 剪贴板主路径是原生 ClipboardEvent；默认画布名只有一个生成器；缩略图只有一份组件、画的是当前素材；类型切换一次 commit 不换 id、`KIND_FIELDS` 编译期完整；前后端几何公式同源；空态一屏只有一个行动；「适应画布」是模式、直接操纵即退出、模式按画布各自记 | `shapeSwitch.golden.test.ts` + `tests/test_compose_switched_shapes.py`、`canvas/shapeOutline.test.tsx`、`store/workspace.test.ts`、`e2e/cross-tab-paste.spec.ts`、`e2e/nav-audit.spec.ts` |
| `AssetBrowser`、`ScriptLibrary`、`scriptRunStore` / `runtimeAssetStore` / `scriptLibraryStore` / `assetStore` / `depRepairStore` / `envStore` / `aiStore`、`FigurePickerDialog` | 素材库普通入口 → `asset-library.md` | 七个 store 都有项目代际；同脚本防并发、取消等原请求以 `execution_cancelled` 落地；多 Figure 结果绝不只显示第一张；runtime 卡没有假值；「编辑原图」必然加进文档并说出口、「添加到画布」一律 `addFigureToLayout`；`role="option"` 里不嵌可 Tab 控件；TIFF 经 `panelSrc` 走 `/api/render` | `scriptRunStore.test.ts`、`ScriptLibrary.test.tsx`、`AssetBrowser.runtime.test.tsx`、`AssetBrowser.tiff.test.tsx`、`lib/panelSrc.test.ts`、`projectSwitchAssets.test.ts`、`openRequest.test.ts`、`FigurePickerDialog.test.tsx`、`e2e/asset-library.spec.ts`、`projectSwitchDepRepair.test.ts`、`projectSwitchAi.test.ts` |
| `NativeConfirmDialog`、`nativeSessionStore`、`nativePanelState`、`?native=` / `tavotto:open` 的 `native` | `tavotto run` 的桌面面 → `tavotto-run-desktop-side.md` | 前端只提交不透明 `native_id`；交接 ID 两条入口都必须带；确认屏是闸（`blockDismiss`）、两条 → 排队；事件按 `sequence` 判序；屏障处的 build 由界面显式发；未知不等于 native；`clear()` 不杀用户的脚本 | `nativeSessionStore.test.ts`、`NativeConfirmDialog.test.tsx`、`openRequest.test.ts`、`tests/native/test_run_beta_claims.py` |
| `lib/readinessText.ts`、`store/projectReadinessStore.ts`、`RegistryDialog`、左栏偏好、左栏样式面板 `components/left/StylePanel.tsx` / `store/styleBinding.ts` / `lib/stylePanelModel.ts`、`ElementTree` 的行、`WorkdirConfirmDialog` / `WorkdirRow`、`envStore.setWorkdirMode`、`DependencyPrepareDialog` / `depRepairStore.prepare` / `adoptUserEnvironment`、`lib/userEnvironmentText.ts`、通知轨「已改用你的环境」 | 接入状态与左侧外壳 → `readiness-and-left-shell.md` | 句子只有 `statusLabel()` / `reasonText()`；界面不执行动作、冲突不预选；自动让位绝不写回偏好 | `store/projectReadinessStore.test.ts`、`RegistryDialog.test.tsx`、`ProjectReadinessBanner.test.tsx`、`WorkdirConfirmDialog.test.tsx`、`WorkdirRow.test.tsx`、`DependencyPrepareDialog.test.tsx`、`notificationRail.test.tsx`、`canvas/drawerViewportResize.test.tsx`、`store/uiStore.test.ts`、`components/left/elementTreeRerender.test.tsx`、`components/left/workspaceList.test.tsx`、`store/projectSwitchSerial.test.ts`、`components/left/stylePanel.test.tsx`、`lib/stylePresets.test.ts`、`store/styleBinding.test.ts` |
| `lib/exportRequest.ts`、`store/exportStore.ts`、`lib/exportFigures.ts`、`lib/exportName.ts`、`lib/artifactInspection.ts`、`lib/imgRetry.ts`、导出对话框 | 统一导出管线 → `export-pipeline.md` | 载荷只由 `buildExportRequest()` 构造；文件名规则严格同源对；产物核验的解读只有 `inspectionState()` 一处 | `lib/exportRequest.test.ts`、`store/exportStore.test.ts`、`lib/exportFigures.test.ts`、`lib/artifactInspection.test.ts`、`lib/imgRetry.test.tsx`、`components/ExportDialog.test.tsx`、`tests/golden/filename_vectors.json`、`tests/test_export_request.py`、`tests/test_export_phase_labels.py` |
| `store/workspace.ts`、`openFastEdit` / `addFigureToLayout` / `returnToLayout` / `focusLayoutPanel`、`lib/originalSpec.ts` | 两条工作流与原图规格 → `workflows-and-original-spec.md` | 模式是工作区状态不进文档；四个稳定动作是唯一出口 | `store/workspace.test.ts`、`lib/originalSpec.test.ts`、`canvas/fastEditStage.test.tsx`、`e2e/nav-audit.spec.ts`、`tests/test_original_spec.py` |
| `lib/desktop.ts`、`lib/updateNotice.ts`、`UpdateNoticeDialog` | 桌面感知与更新 → `desktop-and-update.md` | 组件不得直接 import `@tauri-apps/*`；只查一条更新通道 | `lib/desktop.test.ts`、`components/UpdateNoticeDialog.test.tsx`、`store/updateStore.test.ts` |
| `web/src/i18n/`、`UiMessage`、`errors.json`、`backendErrorText()`、`lib/stylePresets.ts` / `specBinding.ts` / `exportDefaults.ts` | 多语言 → `i18n.md` | 资源静态 import；code 一旦发布不能改名；`pnpm i18n:check` 是硬门禁 | `web/scripts/i18n-check.mjs`、`i18n/overflow.test.tsx`、`e2e/i18n.spec.ts`、`preflight.golden.test.ts`、`tests/test_i18n_dead_keys.py`、`tests/test_error_codes.py`、`tests/test_style_schema_pair.py` |
| `web/src/playground/`、`pyodide.worker.ts`、`prewarm.ts`、`examples/*.py` | 浏览器 playground（网站 /try） → `browser-playground.md` | Pyodide 版本与包白名单钉在 `packaging/playground-runtime.json`；一个文件 = 一个 Worker | `playground/*.test.ts`、`e2e/playground.spec.ts`、`tests/test_browser_session.py`、`tests/test_playground_build.py` |
| `components/ui/*`、`src/index.css` 的 `@theme`、图标、设置窗口排版、工作台结构 | UI 视觉纪律 → `ui-visual-discipline.md`（全文 `docs/ux/DESIGN_CONSTITUTION.md`） | radius 四档、字号 11–14、控件高 28；同类控件第二套先删 | `components/ui/foundation.test.ts`、`components/ui/keyboardPrimitives.test.tsx`、`lib/motion.test.tsx`、`src/tokenContrast.test.ts`、`iconography.test.tsx`、`e2e/contrast.ts` |

## 验证

- `cd web && pnpm test`（vitest + jsdom；`NODE_OPTIONS` 里禁用了 node 内建 webstorage）；
  `pnpm build` = `tsc -b` + 打包（**别用 `tsc --noEmit`**：恒假绿）；`pnpm i18n:check`。
- 界面用 agent-browser 实测；黄金路径 `pnpm e2e`（Playwright，先 `python scripts/build_frontend.py`；
  `e2e/mcp-canvas.spec.ts` 还要 `python scripts/build_mcp_widget.py`）。jsdom 量不到几何、裁剪、
  对比度、微任务检查点——这几类判据只有真浏览器抓得到。
- 改了 `web/src`：`python scripts/build_browser_playground.py --check`；跑过 `scripts/build_frontend.py`
  之后包内 `src/tavotto/web/` 优先于 `web/dist`，改完要么再同步一次要么删掉它。
