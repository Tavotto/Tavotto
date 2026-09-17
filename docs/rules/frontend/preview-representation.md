# 预览表示法：vector / hybrid / raster（2026-08-28，issue #181；ADR 0022）

> 原文出自 `web/AGENTS.md`「预览表示法：vector / hybrid / raster（2026-08-28，issue #181；ADR 0022）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

**画法可以换，能编辑的东西一个都不许少。** 细则在
`docs/adr/0022-complexity-aware-editor-preview.md`，动手前先读。要点：

* 渲染响应带 `preview`（`web/src/lib/previewBudget.ts` 的 `PreviewMetadata`）。
  **加字段协议**：老后端不返回它，`EMPTY.preview` 就是 `VECTOR_PREVIEW`，
  每一条路径的行为与从前逐字节相同。
* `PanelView` 的分档只有一句：`render.preview.mode === 'raster'` 时编辑态也走
  引擎位图（复用 `useEnginePngBlob` → `previewPngUrl`/`enginePreviewPng` 那条
  既有链路，**不写第二套 objectURL 生命周期**），否则照旧内联 SVG。
* **`hybrid` 不是 `raster`**：它有 SVG，照旧内联。前端**没有为 hybrid 新增
  任何分支**，这是它做对了的证据，不是漏做——混合产物就是一份 SVG，里面几个
  数据层是 `<image>` 而已。因此 hybrid 对用户尽量无感。
* **hybrid 下被 rasterize 的那几层在 DOM 里没有 gid 节点**（Session 03 实测：
  `<g id="axes_0.collections_0">` 整个不出现，而 `axes_3.lines_0` /
  `axes_3.legend` / `axes_0` 一个不少）。`svgPreviewStore` 的假实时因此**只
  覆盖矢量层**：拖动数据层时 `findGidNode` 返回 null，既有实现安静退出、
  覆盖层接管，落点由后端权威渲染补上。这是刻意的取舍——为了保住假实时去造
  几千个隐藏占位节点，等于把 #181 的 DOM 节点数又搬回来。
* **raster ≠ 只读**：`ElementHitLayer` 照常挂着，几何权威仍是
  `useExactPanelManifest`（ADR 0017 一个字不放松）。把它做成「图太大所以不能
  编辑了」是最容易滑进去的错误——#181 的用户要的恰恰是编辑这张图。
* **二道闸收在 `resolvePreview()` 一处**：后端说 raster、或后端说 vector 却给
  了一份超过硬闸的 `svg`，都在这里被丢掉，绝不 `prepareSvg` + 存进 store +
  `dangerouslySetInnerHTML`。丢的时候 `reason` 改成 `fallback`——**是谁拦的**
  要说得出口，否则排障时会以为后端那道闸生效了。
* **`panelDisplayView` 多一档 `raster`**：它是「挂着**自己**这一版，只是画法
  不同」，与 `fallback`（挂着**别人**的图、几何交互停摆）不是一回事。
  诊断的 `display_variant` / `display_exact` 直接读它。
* **退回来的 SVG 要带着它自己的表示法**（`mergeRender` 里 `preview` 跟着
  `svg` 走）。拿自己那份（还没画出来 = 默认 vector）去解读别人的 SVG，
  就是 raster 面板在退回窗口里闪一下矢量图。
* Codex 内嵌画布：位图来自 `tavotto_apply_overrides` **同一次响应**的
  `preview_png_base64`，`mcp/session.ts` 按变体存一版。拿不到这一版自己的
  就宁可没有——「一个面板显示了另一个面板的图」是有前科的。
* 角标 `panelBadge.memoryEfficientPreview` 只在**编辑态**出现，带 tooltip，
  **不弹对话框、不说文件太大**：这是我们主动做出的显示决定，导出质量一点
  没变（不变量 2）。
* 看护：`canvas/panelPreviewMode.test.tsx`、`lib/previewBudget.test.ts`、
  `store/renderStore.test.ts` 的「二道闸」一组、`mcp/session.test.ts` 的
  raster 一组；Python 侧 `tests/test_preview_budget.py`。
