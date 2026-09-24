# 素材库普通入口（2026-08-26，Compatibility Bridge Session 5）

> 原文出自 `web/AGENTS.md`「素材库普通入口（2026-08-26，Compatibility Bridge Session 5）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

素材面板分「图」（FileAsset + RuntimeFigureAsset 同一个 listbox，runtime
卡带「运行时图」badge、cache 预览、stale 角标与重跑）与「脚本」
（`ScriptLibrary`，项目内每个合理 .py 一行）两个区。普通路径必须在这里
完成；RegistryDialog 只留冲突裁决 / 手工 stem / 高级诊断。

- **数据源三件套**：`scriptLibraryStore`（`/api/registry` 全视图，缓存 +
  幂等去重）、`runtimeAssetStore.assets`（`GET /api/runtime/assets`，只读
  清单 + `previewNonce` 预览换代）、`scriptRunStore`（运行状态机）。
  三者都在 `registry.changed` SSE 时重取**已经取过的**，项目切换全清。
  **三个 store 都有项目代际（epoch）**：模块级 in-flight 请求活得比一次
  Zustand reset 长，`clear()` 必须换代 + 清 inflight，A 项目的响应绝不
  落进 B（Session 6 评审修复；vitest 各有作废用例看护）。`packageStore`
  按同一条纪律换代（见 `docs/rules/frontend/settings-shell-and-packages.md`）——**新写一个会在项目之间
  存活的 store 时，先回来把它加进这份名单**。
- **`scriptRunStore` 的四条纪律**（vitest 看护）：同脚本防并发（busy 即
  no-op，后端另有 409）；cancel 走后端取消端点（置标志 + 硬杀 worker），
  行内状态等**原请求**以 `execution_cancelled` 落地——绝不「界面装停了、
  脚本还在跑」；每次 run 换代，迟到响应丢弃；`clear()` 升 epoch，在途
  响应绝不落进新项目。错误存**原始 code + params**，显示那一刻才翻
  （i18n 纪律）。SSE `probe.started` 驱动 starting_runtime → running。
- **运行/取消是同一个按钮**（busy 态翻转）：取消后焦点天然留在原脚本行，
  不做焦点搬运。状态行 aria-live=polite，只随相位变化播报。
- **多 Figure 结果进 Dialog**（自带 focus trap），每张各有「添加到画布」，
  `dropped_figures` 如实显示——绝不只显示第一张。
- **safe 失败的恢复路径**：文案解释「可能依赖原来的 Python 环境 / cwd /
  参数」，真实入口只有「选择渲染环境」（设置 about 段的
  EngineEnvironmentCard）与「复制诊断」；**native 未落地前不渲染任何
  可点但无功能的按钮**（PR 2 合并后再升级为实际入口）。
- **素材卡的两个动作各说各的后果（UI 审计 T06）**：「编辑原图」（Enter / 双击）
  进快速编辑——图还不在文档里时它**必然**把图加进来（ADR 0028：快速编辑的
  对象只能是文档里的面板对象），这一步由 `openFastEdit` 用状态提示
  `fastEdit.addedForEdit` 说出口，一条历史、撤销即移除；图已在文档里时零文档
  改动。**这句话分两份，别合成一份**：可见的常驻说明在 `FastEditBar`
  （`data-fast-edit-added-note`，**不带 role**），读屏播报在 `CanvasStage` 的
  `data-fast-edit-live`（`role="status"`，sr-only）。播报那份挂在 `CanvasStage`
  而不是浮动条里，是因为浮动条本身就是进快速编辑那一刻才挂上的——活动区跟它一起
  插进 DOM 的话，插进来时就已经填好了字，而读屏播报的是**区内内容的变化**，
  「带着内容整个插入」的活动区各家 AT 行为不一致、很可能一声不吭，等于用一个
  role 承诺了一件它并没有做的事。区先在、内容后变，由
  `fastEditStage.test.tsx`「播报区常驻」那条钉着。「添加到画布」（Shift+Enter / 就近入口 / 看大图弹窗）一律走
  `addFigureToLayout`（文件与 runtime 同一条路，已在文档里只聚焦）。列表下方
  `SelectedAssetActions` 给一对 listbox 之外的真按钮——option 里不许嵌可 Tab
  控件。**不许再用一个中性的「打开」承载加入文档。**
  搜索词与筛选在 `store/assetBrowseStore`（组件会被卸载；换项目 `clear()`，
  不落 localStorage）。同脚本 + 同 stem 的 runtime 条目紧跟它的磁盘图并写
  「同源：X.pdf」（`runtimeSiblingOf`）；`assets.changed` 时 runtime 清单也
  重取（只重取已取过的）——「哪张图有原件」正是那一刻变的。
- **runtime 卡片没有假值**：没跑过的没有尺寸、没有描述符，主动作是
  「运行并发现图」；「添加到画布」只走描述符（`addRuntimePanel`），
  绝不解析 id、绝不指望磁盘路径。运行时图的写回区
  （`PanelSection.RuntimeSourceArea`）**显示原因**（没有原始图文件，
  导出会创建新文件）而不是无声隐藏；按钮缺席只是礼貌，硬拒绝在后端。
- **交接定位认 runtime 素材（Session 6）**：`applyOpenRequest` 找不到磁盘
  面板时按 stem 查 `GET /api/runtime/assets`（只读），有描述符就
  `addRuntimePanel`；没有描述符**不造假面板**，引导去脚本区运行。多
  Figure 交接（`?pick=<脚本>` / `tavotto:open` 事件的 `pick`）打开
  `FigurePickerDialog`——每张可见、各自可加、**绝不静默选第一张**；条目
  从 assetStore + runtimeAssetStore 现算（磁盘图走 addPanel、runtime 走
  描述符），没跑出预览的条目不渲染假按钮。看护
  `openRequest.test.ts` / `FigurePickerDialog.test.tsx`。
- **TIFF 素材（issue #534）**：`/api/file` 回原字节，Chromium / WebView2 画不出 TIFF——`panelSrc`
  的位图分支按**浏览器能力的允许清单**（`BROWSER_RASTER_EXTS` = png / jpg / jpeg）判，清单之外一律走
  `/api/render` 分档渲染（后端转 PNG）；新加一种素材格式时默认落在这条安全的路上。这张表不是素材清单，
  不与 `project_refresh.IMG_EXT` 成对。卡片格式名按扩展名说实话（`formatOf`：JPEG / TIFF 不再叫 PNG）。
  范围之外的 TIFF（`/api/panels` 的 `unsupported`）不给卡片、也不静默消失：图区末尾逐个一行
  （`UnsupportedAssets`，`data-asset-unsupported`），跟着同一组搜索 / 来源 / 类型筛选走，文案取
  `errors:backend.<code>`；它非空时不出空态 / 「没有匹配」（空态与清单不同时出现）。看护 `lib/panelSrc.test.ts`、`AssetBrowser.tiff.test.tsx`。
- 看护：`scriptRunStore.test.ts` / `ScriptLibrary.test.tsx` /
  `AssetBrowser.runtime.test.tsx` / `runtimeSourceSection.test.tsx` +
  `e2e/asset-library.spec.ts`（show-only 项目真实后端黄金路径 + 窄视口 +
  保存/关闭/重开/重放/预检/导出完整链 + 多 Figure 选择器）。
