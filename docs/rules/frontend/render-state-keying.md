# 渲染态：按「文件 + 变体」分键（2026-08-18，Phase F）

> 原文出自 `web/AGENTS.md`「渲染态：按「文件 + 变体」分键（2026-08-18，Phase F）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

键 = `fileId + ' ' + JSON.stringify(overrides)`，唯一出处
`renderStore.renderKeyOf(panel)`；消费方一律 `usePanelRender/usePanelManifest`
（或非 hook 的 `panelRender(state, panel)`）。**旧约定「每个 fileId 只能有一个
说了算的面板」已废除**——那条裁决（`pickRenderTargets`）是为了绕开
「两个同文件不同 override 的副本互顶 wantPatches → React #185」，代价是输家
永远显示赢家的图；现在各存各的，去重只剩「完全相同的两个副本共用一次渲染」
（`renderTargets`）。live figure 仍是一个 stem 一份，靠轮流全量重放
（patch_apply≈0ms、热画 17–28ms，数据见 perf-baseline）。配套：
① **SVG 与 manifest 必须同一次响应**（render 请求带 `inline_svg`，worker
在响应里内联刚写完的那份）——第二跳 GET `/api/engine/svg` 读磁盘，另一个
变体插进来就会图框错配（端点保留兼容，前端不再用）；
② 位图显示走 `POST /api/engine/preview_png`（按 patches 出图、状态中立、
文件名带 patch 哈希前 12 位），`/api/engine/png` 是「谁最后渲染谁说了算」，
只留兼容；③ 自己那份变体还没画出来时退回该文件最近画好的那张
（`latest` 表），否则每敲一个字画布都会闪回磁盘原图——**但那份退回来的
manifest 只能看不能写**，见 `docs/rules/frontend/display-fallback-vs-geometry-authority.md`；④ 连续调整期间
**只给含 `role=="image"` 的面板**发 `preview_dpi: 100`，松手/结束事务由
`flushRender(panelId)` 按默认 dpi 定稿（防抖 / 定稿 / 取消这套调度住在
`web/src/store/renderScheduler.ts`，`actions` 与 `useEngineSync` 都只调用它，谁也不 import 谁）（纯矢量图上降 dpi 零收益，见基线补测）；
⑤ 编辑期每改一个值就多一条变体，`prune(live)` 按文档现存面板清理
（**条目数策略；字节那一维另有预算，见 `docs/rules/frontend/svg-byte-budget.md`**），
只留在用的与每个文件最近成功的那份；⑥ SSE 的 render.started/done 只带
fileId，写**文件级** `building` 表，绝不盖任何变体条目（盖了的话另一个
副本会永远转圈）；⑦ **磁盘原图冒充不了 overrides 渲染结果（2026-08-31）**：
非编辑态需要引擎产物而位图未落地/取图失败时，优先挂这一版（或 latest 退路）
的引擎 SVG，**确实只能退磁盘原图时必须出「近似预览」角标**（与布局版本
预览的「近似预览」同一措辞），失败不吞、上一变体的位图不许冒充当前变体；
「只带基线、还没动过」的面板跳过渲染的前提是后端 `baked_current` 没说
基线已失效（判据出处见 `docs/rules/backend/project-system.md` 的「基线绑定文件身份」，
前端判据只有一份——纯函数 `web/src/lib/bakedBaseline.ts` 的 `isJustBakedBaselineOf`，
`store/actions.isJustBakedBaseline` 是它读素材表的薄包装；`useEngineSync` 订阅素材表让失效
发生在会话中时也能重新裁决）。看护：`web/src/store/renderStore.test.ts`、
`web/src/lib/bakedBaseline.test.ts`、`web/src/store/renderScheduler.test.ts`、
`web/src/hooks/useEngineSync.test.ts`、`web/src/canvas/panelPreviewMode.test.tsx`、
`tests/test_engine_variants.py`、`tests/test_paths_and_baked.py`。

**同步器的两半挂在不同的地方（2026-09-24，松手卡顿剖析）**：`useEngineSync` 由文档一侧
（`useEngineDocumentSync`：文档 / 编辑态 / 素材事实）与渲染态一侧（`byKey` / `tracked`
变了再看一眼、图幅同步）组成。主应用里文档一侧挂在 `App` 的 Workspace 上，**渲染态一侧
只挂在树尾不画任何东西的叶子 `<EngineRenderSync />` 上——Workspace 自己不订阅渲染态**
（不调 `useEngineSync`，不调 `useRenderStore`）。宿主订阅了什么，它下面整棵树（顶栏 /
左栏 / 属性栏）就跟着重画什么；而渲染态在每次新图到达时要变两三回（响应入库、
`prune` 在同步 effect 里清掉掉出近期档的旧变体、`wantPatches` 占位），挂在 Workspace
上时新图到达会出现第二次同样重的整树提交（58 个元素的图上 App 每次约 7 ms、CPU
慢 4 倍时约 30 ms）。改法是收窄订阅，**不是**把显示往后拖（不许用计时器推迟新图上屏）。
消费渲染态的组件各自用 `usePanelRender` 一族的 selector 订阅自己那一条，别指望父级
带着重画。嵌入式画布 / playground 没有那棵大树，照旧用合起来的 `useEngineSync()`。
看护：`web/src/components/left/elementTreeRerender.test.tsx` 的 A4（Workspace 同构替身
上新图到达 + SSE `render.done` + prune 真清掉一条 → 元素树只提交一次）与「A4 的前提」
（按 AST 钉 Workspace：调 `useEngineDocumentSync`、挂 `<EngineRenderSync />`、不调
`useEngineSync` / `useRenderStore`）。
