# web/ — 前端规则

仓库级路由与不变量在根 `AGENTS.md`；引擎与后端在 `src/tavotto/AGENTS.md`。
技术栈：Vite + React 19 + TS + Tailwind v4。品牌常量唯一出处
`web/src/lib/brand.ts`（与 `engine/brand.py` 同源），界面不得手写产品名。

## 验证

- `cd web && pnpm test`（vitest+jsdom；NODE_OPTIONS 里禁用了 node 内建
  webstorage，否则 jsdom localStorage 被遮蔽）。
- `pnpm build` = 类型检查（`tsc -b`）+ 打包。**别用 `tsc --noEmit` 当类型检查**：
  根 tsconfig 是 `files:[]`+references 的方案文件，`--noEmit` 不走项目引用、
  什么都不编、恒假绿。
- 跑过 `scripts/build_frontend.py` 之后包内 `src/tavotto/web/` 优先于 `web/dist`，
  改完前端要么再同步一次，要么把它删掉退回开发态。
- **改了 web/src 就得重建两个受管产物**：`python scripts/build_mcp_widget.py`
  （Codex 内嵌画布）与 `python scripts/build_browser_playground.py`（/try），
  各有 `--check` 防漂移。
- 界面用 agent-browser 实测；黄金路径 E2E `cd web && pnpm e2e`（Playwright，
  先 `python scripts/build_frontend.py`）。

## 渲染态：按「文件 + 变体」分键（2026-08-18，Phase F）

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
manifest 只能看不能写**，见下一节；④ 连续调整期间
**只给含 `role=="image"` 的面板**发 `preview_dpi: 100`，松手/结束事务由
`flushRender(panelId)` 按默认 dpi 定稿（纯矢量图上降 dpi 零收益，见基线补测）；
⑤ 编辑期每改一个值就多一条变体，`prune(live)` 按文档现存面板清理
（**条目数策略；字节那一维另有预算，见「SVG payload 的字节预算」一节**），
只留在用的与每个文件最近成功的那份；⑥ SSE 的 render.started/done 只带
fileId，写**文件级** `building` 表，绝不盖任何变体条目（盖了的话另一个
副本会永远转圈）；⑦ **磁盘原图冒充不了 overrides 渲染结果（2026-08-31）**：
非编辑态需要引擎产物而位图未落地/取图失败时，优先挂这一版（或 latest 退路）
的引擎 SVG，**确实只能退磁盘原图时必须出「近似预览」角标**（与布局版本
预览的「近似预览」同一措辞），失败不吞、上一变体的位图不许冒充当前变体；
「只带基线、还没动过」的面板跳过渲染的前提是后端 `baked_current` 没说
基线已失效（判据出处见 `src/tavotto/AGENTS.md` 的「基线绑定文件身份」，
前端唯一消费点 `isJustBakedBaseline`，`useEngineSync` 订阅素材表让失效
发生在会话中时也能重新裁决）。看护：`web/src/store/renderStore.test.ts`、
`web/src/hooks/useEngineSync.test.ts`、`web/src/canvas/panelPreviewMode.test.tsx`、
`tests/test_engine_variants.py`、`tests/test_paths_and_baked.py`。

## 显示回退 ≠ 几何权威（2026-08-26，issue #131；ADR 0017）

**旧 SVG 可以继续显示；旧 manifest 不得作为几何写操作的权威输入。**
细则与理由在 `docs/adr/0017-display-fallback-vs-geometry-authority.md`，
动手前先读。要点：

* 两套 API，职责写在名字里。显示：`panelRender` / `usePanelRender` /
  `usePanelDisplayManifest` / `panelDisplayView`（可以退回 `latest[fileId]`）。
  几何权威：`exactPanelRender` / `exactPanelManifest` / `useExactPanelManifest`
  （**只认 `byKey[renderKeyOf(panel)]`**，且要求 `lastPatches` 与当前 overrides
  逐字相等、没被 markStale 标记）。
* **凡是读 `bbox` / `anchor` / `position` / `geometry` / `arrow_endpoints` /
  `follow_gids` / `geom_gid` / `size_mm` 之后要写文档的，一律走权威**：图内
  对齐分布、等宽等高、多选整组拖动、单文字拖动、axes 移动与缩放、成组缩放、
  命中测试、框选、选择框、吸附候选、orphan override 判定与清理。
  列元素 / 认 role / 画角标这类只读的继续用显示那份。
* `panelDisplayView` 是**判别联合**：`fallback` / `empty` 分支在类型上就没有
  `manifest` 字段。别把它改回「一个可选字段 + 一句注释」——issue #131 之前
  正是靠注释提醒的，没拦住。
* 权威缺席时**不是禁用一切**：画布照常显示上一张（不闪白）、命中层停摆、
  一个选择框都不画、`selectedGids` 不清空、入口置灰并说明「正在同步」，
  精确 manifest 回来后自动恢复。纯样式类（颜色/线宽/线型/alpha/visible）
  不依赖 bbox，继续走局部 SVG 预览，不受这道闸影响。
* **离散动作不许被连续手势吞掉**：`documentStore.commit` 在 `state.txn` 存在时
  会静默并入当前事务。对齐、分布、等宽等高、重置元素、清理 orphan、版本
  保存/恢复、写回历史恢复、undo/redo 执行前必须先
  `gestureCoordinator.finishActiveGesture()`。**光调 `endTxn()` 不够**——
  `useFieldGesture` 自己还有 open 标记、安静计时器、SVG 预览会话和挂起的定稿
  渲染，事务被外人收掉而 hook 不知情的话那些状态会一直悬着。
* **没有视觉位移就不写 override**：no-op 判据收在 `layoutBoxes()` 一处
  （round4 之后逐位相等 = 没有可表示的位移，阈值与写出去的 override 精度同源）。
  给「本来就在目标位置」的元素写一条等于当前值的绝对坐标，等于把标题/轴标签/
  图例从 matplotlib 自动布局里钉死，此后改字号改图幅它都不会再让位。
* override 的 upsert **原地改值**，不许 `filter(...)+push(...)`：override 数组
  的 JSON 就是变体键，顺序一变键就变 = 一次完全没必要的重渲染。
* 撤销的落点要还在：每个文件保留最近 4 档成功变体（有界），`latest` 按**请求
  序号**推进——乱序返回时旧变体只入库、不挪 `latest`，也不丢弃（同文件的另一个
  副本可能还等着它）。
* 布局版本预览按**版本自己的 overrides** 出图（`useVariantPng` →
  `preview_png`），出不来就退回磁盘图并明确标「近似预览」，**不许无提示地拿
  磁盘原图冒充版本视觉状态**。只给用户当前展开的那一份渲染。
* 看护：`store/geometryAuthority.test.ts`、`store/alignAction.test.ts`、
  `canvas/alignUndoConvergence.test.tsx`、`lib/authorityTrace.test.ts`。
  测试里「这一版已经精确画好」用 `test/renderFixtures.ts` 的 `seedExactRender()`
  ——手写 `{manifest, status:'ready'}` 造出来的是真实渲染永远不会有的形状。

## 预览表示法：vector / hybrid / raster（2026-08-28，issue #181；ADR 0022）

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

## SVG payload 的字节预算（2026-08-29，issue #181 Session 04）

**条目数不是字节预算。** 上面那条 `RECENT_VARIANTS = 4` 管的是「留几档语义
状态」，它对「留了多少字节」一无所知——hybrid 之后仍有 8～12 MiB 的预览 SVG
乘以 4 档乘以几个文件，就是几百 MB 常驻在 JS 堆里，而每一份都是合法的撤销
落点，`prune` 一个都不该清。所以 `renderStore` 里**两条策略并存**：

```text
entry-count policy   RECENT_VARIANTS = 4        管「留几档语义状态」
byte-budget policy   SVG_RECENT_BUDGET_PER_FILE = 16 MiB
                     SVG_RECENT_BUDGET_GLOBAL   = 64 MiB
```

* **超预算时丢掉的只有 `svg` 字符串**（`dropSvgPayload`）：manifest / rev /
  lastPatches / wantPatches / timings / preview 元数据 / status / stale 一个字
  都不动。**「budget exceeded → delete PanelRender」是错的**——那会连撤销、
  版本恢复与几何权威一起丢掉。语义状态 ≠ SVG 源 payload。
* 记账**每次现算**（`residentSvgBytes`），不维护计数器：账本是第二份权威，
  与 `byKey` 分叉之后没有任何信号说得清哪一份算数。判据吃 `svg != null`。
* 单份大小取后端的 `preview.svg_bytes`（= `stat().st_size`，与硬闸量的是同一
  个东西），老后端退回 `svg.length`。**两条路都不复制字符串**——为了量一个
  「太大了」的东西再复制一遍，正是这套预算要防的事。
* **三条 pin，一条都不能少**：画布上现存面板的变体键（`prune(live)` 每轮刷新
  的模块级 `liveKeys`）、每个文件的 `latest`（显示退路）、在途/渲染中。
  这三份是「清了就没画面」的那几份——**Codex 内嵌画布里那份 SVG 是唯一能
  显示的东西**（`previewPngUrl` 只对 raster 档有缓存位图，`panelSrc` 恒 null）。
  全被 pin 住时**宁可超预算**：预算管的是可驱逐的历史 payload，不是显示所需。
* 驱逐次序两维：先「不在 `recent` 里的」（没人会撤销回去），再按 `svgSeq`
  丢最久没更新的。只按第二维排（纯 LRU）会先丢真的撤销落点。
* 被丢掉的那一版在 `panelDisplayView` 里是**独立的一档 `evicted`**，不是
  `fallback`：画布挂的**就是这一版**，只是画法换成位图（与 `raster` 同一条
  链路）。掉进 `fallback` 的话诊断会说画布挂着**另一个变体**的图，而几何权威
  仍是这一版——issue #131 同款错配。
* `useEngineSync` 对 `svgEvicted` 的条目**重排一次渲染**（ADR 0022 §8 的
  「按 transport 可用路径重新请求」）：桌面/playground 有引擎位图顶着，
  内嵌画布没有第二条路。重画成功后那一版已经 live、被 pin 住，不会来回拉锯。
* 诊断事件 `render.svg_evicted`（file / variant / scope / bytes，全部按 ADR
  0016 的 allowlist 序列化）。
* 看护：`store/svgMemoryBudget.test.ts`、`canvas/panelPreviewMode.test.tsx`
  的 evicted 一条、`hooks/useEngineSync.test.ts` 的重排一组、
  `embedded/session.test.ts` 的种子记账两条。**新增判据前先跑一次变异**——
  本节 20 条错误实现逐条反证过，其中「种子那一帧不记账」最初是全绿的。

## 假实时预览：预览平面与历史平面严格分开（2026-08-18，Phase G）

预览平面（`web/src/store/svgPreviewStore.ts` + `lib/svgStyle.ts`）只活在内存与
SVG DOM 里，rAF 合并成一帧，**不 commit、不进历史、不发后端**；历史平面照旧
是 `documentStore.commit / beginTxn / endTxn`，**没有任何一条路径绕开它**。
数据流：`pointerdown → beginPreview`（只记账）→ `pointermove → previewTransform /
previewStyle`（只改 DOM）→ `pointerup → setOverride(…) + commitElementPreview`
（一条历史 + 一次权威渲染）→ 权威 SVG 换上来时 `reattachPreview` 收工。

* **临时 transform 必须写成 `translate(…) <原始 transform>`，永远从 base 现算**
  ——旧实现直接 `setAttribute('transform', 'translate(…)')` 把 matplotlib 自己的
  变换整个盖掉（`<image>` 的 `scale(1 -1) translate(…)` 就是这么没的），
  字符串累加则会让位移翻倍。base 与账本挂在「面板 + 这一版 SVG」上，不挂在
  session 上：连着拖两个元素时，第二次绝不能把第一次的预览位移当成 base。
* **`pointercancel` / `lostpointercapture` 与 `pointerup` 必须分开**
  （`trackPointer` 的 `TrackEnd.cancelled`）：取消 = 还原 DOM、不写 override、
  不进历史、不渲染。以前两者走同一条路，被系统打断的拖动会静默落成真实改动。
* **`reattachPreview` 只在 DOM 真的被换过时才重放**（`domIntact` 比节点引用）：
  每写一条 override PanelView 都会重跑，此时重新采 base 采到的是「已经挪过的
  位置」——位移翻倍且再也还原不回去。
* **局部样式预览是白名单**（`lib/svgStyle.ts` 的 `STYLE_ADAPTERS`），默认不支持。
  通用规则是「只改本来就声明了该属性、且值不是 `none` 的叶子」，因此
  `fill: none` 的线不会被 facecolor 填实、箭头杆与箭头帽各得其所。文字是唯一
  例外（颜色在字形组上，默认黑色时那条 style 根本不存在，必须允许新增）。
  **能力表说「支持」不等于这个 artist 上改得到**：同一个 role 的两个 artist
  在 SVG 上可以长得完全不同（`fill=False` 的 PathPatch 写的是 `fill: none`，
  改 facecolor 一个叶子都碰不到）。所以 `previewStyle` 除了查 gid 节点在不在，
  还要同步跑一遍 `canStyleEditApply`——它与 `applyStyleEdit` **共用
  `styleTargets` 这一份实现**，分成两份迟早分叉，而分叉的表现正是
  「界面说预览生效了，画面纹丝不动」（预览一旦回 true，调用方就把渲染策略
  降成 `'none'`，那一轮**根本不会发后端**）。`patch` 角色在表里，
  但它的 `fill` 开关**不在**：把 `none` 换成颜色是新增语义，只能让
  matplotlib 自己重画。
  还原记的是**整条 style 属性原文**而不是逐条属性：CSSOM 会把颜色规范化成
  `rgb(...)`，逐条还原写回去的已经不是 matplotlib 给的那份文本了。
  **实测不可预览、必须回退后端的**：`image.alpha`（透明度烤进 PNG 栅格）、
  `errorbar.*` / `bar_series.*` / `ticks.*` / `ticklabel.*`（manifest 的伪元素，
  gid 在 SVG 里根本不存在）。能力表的断言全部打在**真实 matplotlib 输出**上，
  fixture 由 `python scripts/dump_svg_fixture.py` 生成（`--check` 可比对）。
* **渲染策略与历史无关**：`setOverride/setOverrides/requestRender` 的
  `'immediate' | 'defer' | 'none'` 只决定「什么时候麻烦 matplotlib」。`'none'`
  **仍然要写 `wantPatches` 占位**——不占位的话 `syncEngine` 会立刻替它发一次；
  对应地 `flushRender` 的判据是「这一版还没画出来」而不是「有没有挂着计时器」。
* **历史粒度 `historyMode`**（`gesture` 默认 / `granular`）只改事务边界，
  两种模式下后端渲染都推迟到手势结束；无论哪种，文档改动都经过
  `documentStore.commit`。
* 看护：`web/src/lib/svgStyle.test.ts`（真实 SVG fixture 的适配器矩阵）、
  `store/svgPreviewStore.test.ts`、`canvas/fakeRealtimeDrag.test.tsx`
  （100 次 move 零后端 / 取消语义 / 撤销重做）、
  `components/inspector/elementStylePreview.test.tsx`、
  `e2e/fake-realtime.spec.ts`（真浏览器，顺带产出 perf-baseline 的 Phase G 数字）。

## 统一检查与问题定位（2026-08-31，ADR 0030）

完整版在 `docs/adr/0030-validation-and-problem-navigation.md`，改动前先读。
**「这份项目有什么问题」只有一条链**：

```text
preflight.runSpec()      规则求值（两份求值器，golden vectors 对齐）
  → lib/validation.ts    接成可定位问题：画布维度、逐条命中、指纹、fixKind
  → store/validationStore.ts  编排：防抖 250ms + 代次、按画布增量、失败不清空
  → components/left/ProblemPanel.tsx  左侧「问题」抽屉（常驻入口 + 角标）
```

* **导出对话框不再跑第二遍求值器**：它消费 `getValidationSummary(scope, extra)`
  与 `rawIssuesFor(canvasId)`（样式检查报告要的聚合投影，**同一次求值的另一份
  投影**）。摘要的组装只有 `lib/validation.summaryFor()` 一份。
  它也**不列第二套清单**（ADR 0031 §四）：只给数量 + 「查看问题」，
  完整清单、筛选与修复都在左侧问题面板。
* **`ready` / `failed` 不许压扁成「没问题」**：`total === 0` 单独看不足以说
  「检查通过」。打开导出对话框时**当场同步跑一遍**，就是为了不让那 250ms 防抖
  窗口里说出一句假话。
* **逐条命中**（`PreflightOccurrence`）是 TS 侧的展开层，**不进跨语言合同**：
  golden vectors 比的仍是聚合投影。看护用例盯着两者一致（命中的 objectId /
  gid 并起来必须与聚合项逐字相等）。
* **定位只有 `lib/issueFocus.focusObject()` 一处**：切画布 → 切工作流模式 →
  选中 → 视口 → 高亮 → Inspector → 属性字段，失败回**闭集原因**
  （`canvas_missing` / `object_deleted` / `not_editable` / `document_not_loaded`），
  绝不静默不动。属性字段的落点是 `data-prop`（稳定机器标识），**不是
  aria-label**——那是本地化文案，换语言就选不中。
* **普通界面不出现 gid / 对象 id**：措辞唯一实现 `lib/validationText.ts`，
  主语取 manifest 的 `label`（过 `engineLabel()`），精确名词只在每行收起的
  「技术详情」里。
* **`safe_auto` 的三条判据**：目标值唯一、**修完真的能过**（绝对下限不含等号，
  所以"提到正好 8 pt"不算修好）、不动科研数据（字体 / 色图 / 裁剪一律不自动）。
  落地经 `store/issueFixActions.ts` → `documentStore.commit`，一个修复一个事务、
  一批一个批事务；**批量只在当前画布**（撤销栈按画布换入换出）。
* **就绪度不混进问题清单**：面板底部只放一条通往接入状态的链接。
* 看护：`lib/validation.test.ts` / `lib/validationText.test.ts` /
  `lib/issueFocus.test.ts` / `lib/issueFix.test.ts` /
  `store/validationStore.test.ts` / `components/left/problemPanel.test.tsx`；
  Python 侧 `tests/test_preflight.py` 的跨语言同源一条。

## 属性能力层与 Typography 控件（2026-09-01，ADR 0032）

完整版在 `docs/adr/0032-typography-capability-layer.md`，改动前先读。
**「一段文字长什么样」只有一套词汇**：

```text
lib/typography.ts          规范属性名 · 取值语义 · 能力表 · property path · 校验
  → components/inspector/typographyAdapter.ts
       useFigureTypography（能力问 manifest，写 setOverride(s)）
       useCanvasTypography（能力看 TextObject 字段，写 updateObjects）
  → components/inspector/controls/TypographyControls.tsx（一份控件）
       属性页图内文字 / 图内批量 / 画布标注 / 浮动工具条 —— 四个入口
```

* **不许在组件里按对象类型 switch 着写属性。** 要新属性就加进
  `TYPOGRAPHY_PROPS`，三张表（支持 / path / 校验）一起改；在第二个组件里抄
  一份换算就是埋一次分叉——`ContextBar` 的文字快捷编辑以前正是那样，
  没有斜体、没有字体、`o.bold = !o.bold` 与属性页的 `!bold` 在多选下算出不同
  的结果。
* **值有四档，一档都不许压扁**：`uniform` / `mixed` / `inherit` / `unsupported`。
  字号 mixed 画成 9 pt、字体 inherit 画成一次显式设置，都是数据损坏级的误导。
* **能力有两层**：静态支持表只答「值不值得问引擎」，图内真正能改什么由
  manifest 的 `editable` 说了算。
* **property path 只有 `propertyPathOf(kind, prop)` 一份**：检查报的字段名、
  控件挂的 `data-prop`、`issueFocus` 查的选择器同源。`TEXT_BAR_PROPS`
  （「平铺列表要让出哪几条」）**从这张表算出来，不手抄**。
* **画布文字的字体族是闭集**（`serif` / `sans-serif` / `monospace`），与
  `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源（顺序也比）。合成跑在没有
  matplotlib 的 Flask 进程里，画得出来的就是 PyMuPDF 的 base-14；摆一个画不
  出来的选项 = 静默替换。`TextObject.fontFamily` 是**可选字段**，缺席 = 没设过
  = 继承默认族，回到默认值时**删字段**。
* **写入**：invalid 输入不开事务、不 commit、不进历史，校验**不 clamp**；
  连续输入合并成一条历史，且 `write()` **自己会开一轮**——打字那条路没有
  `onScrubStart`。
* **字形归属与科学文本（ADR 0033）**：`lib/glyphPlan.ts` 回答「这个字符导出
  后由哪张脸画、会不会是方框」，判据是**生成的覆盖表**（`@glyphcoverage`
  别名，与 `src/tavotto/glyphplan.py` 严格同源）——**不是浏览器自己的字体
  栈**。拿浏览器画得出当判据的结果正是「预览好好的、导出上是个方框」。
  `TextObject.interpretation` 两档（`auto` 默认 / `scientific`），合成只生成
  渲染表示，**raw text 一个字符不改**；`scientific` 的代价是 PDF 文本层里的
  `⁵` 变成 `5`，所以它必须由用户明确选。缺字形与「换了脸」是**两条规则、
  两句话**（`glyph-missing` / `glyph-substituted`）。
* 装不上的字体：`manifest` 的 `options_unavailable` → 界面**保留名字 +
  warning**，绝不换掉再改文档。
* 看护：`lib/typography.test.ts` / `components/inspector/typographyAdapter.test.tsx`
  / `lib/canvasTextFont.test.ts` / `TextSection.test.tsx` / `textStyleBar.test.tsx`
  / `canvas/TextView.test.tsx` / `canvas/contextBar.test.tsx`；Python 侧
  `tests/test_typography_families.py`。

## 图例条目与绑定（2026-09-02，ADR 0034）

完整版在 `docs/adr/0034-legend-entry-binding.md`，改动前先读。

* **图例项 = 一段文字 + 一个条目**：文字那半走 ADR 0032 的 Typography 控件；
  条目那半（`binding` / `handle_*` / `visible`）是 `legend_text` 元素上的普通
  manifest 字段。`lib/legendModel.ts` 是前端投影：显示顺序、每项此刻的绑定
  （判据与引擎 `effective_binding` 同一条——任一 `handle_*` override 在即
  custom，**不是**「值和源一不一样」）、「恢复跟随」的计划。
  `LEGEND_ENTRY_STYLE_PROPS` / `LEGEND_BINDINGS` 与 `engine/overrides` 严格同源。
* **图例卡**（`inspector/LegendCard.tsx`）承接 `fontsize`（Typography 批量作用
  于全部项）与 `entry_order`（条目列表的上下移动），通用列表让出这两条
  （`LEGEND_CARD_PROPS`）；没有项的图例不出卡、字段留在通用列表。示意线
  预览读 manifest 的 `handle_*`，**不是第二份样式判断**。
* **恢复跟随**只有 `store/actions.restoreLegendEntryFollow` 一处：删全部
  `handle_*` override + 按 `binding_default` 决定写 `binding=follow_source` 还是
  删 binding override，**一次 commit**。别在组件里逐条 `clearOverride`——那是
  一串撤销记录，中间态还会渲染出半跟随半自定义的图例。
* 位置控件没有「自动」：`best` 叫「最佳位置」，拖过叫「自定义位置」。
* 看护：`inspector/legendCard.test.tsx`；Python 侧 `tests/test_legend_binding.py`。

## 坐标轴边框的语义命中区与四边刻度（2026-09-02，ADR 0035）

完整版在 `docs/adr/0035-axis-tick-direct-manipulation.md`，改动前先读。

* **点内侧控向内、点外侧控向外、线本身选中子图**。命中函数是纯的
  `lib/tickSides.spineZoneAt`：边框线端点来自 manifest 的 `spines`（引擎按画出来
  的那条线给，**含偏出去的边框**），带宽按**屏幕像素**定（`ZONE_PX` /
  `ZONE_PX_TOUCH`，调用方传「一个分数单位 = 几个屏幕像素」= 面板内容边长 ×
  zoom），旋转由 `ElementHitLayer.frac` 反旋转——命中函数不知道 zoom 与旋转。
  高亮条用同一把尺（`zoneRectFrac`），与命中带逐像素重合。
* **优先级**：`pickElement` 命中文字 / 曲线 / 别的子图 / 刻度文字时边框命中区
  让路，只有命中 figure 或那条边所属的子图本身（含铺满它的位图）才算；resize
  手柄在 OverlaySvg 层天然在上。角落并列：更近的边 > 此刻画着刻度的边 >
  固定次序（下、左、上、右）；twinx / secondary 与宿主重合的边同一条规则。
* **状态是派生的**：matplotlib 的 `direction` 是整条轴的，`ticks_<side>` 是边的，
  `inward = 边可见 && 方向含 in`。**三处同源**——画布命中区、示意图
  （`TickAndSpineDiagram` 的内 / 外两带）、刻度卡（方向四档 + 「显示边」）都读
  `readAxesTickModel`、走 `toggleSidePlan` / `axisChoicePlan` / `sideVisiblePlan`、
  经 `store/actions.applyTickSidePlan` **一次 commit**（方向落刻度元素、显隐落子图，
  拆开会渲染出一帧半新半旧）。计划的 `effect.coupled` 是「方向那一步连带改到的
  同轴另一边」——hover 文字、示意图 tooltip 必须说出来，不装作每边独立。
* 「隐藏」是四档里的派生态（两边都不显示），不是第四个真值；从它选回方向时
  **删**两边的 `ticks_<side>` override 回到脚本的边，不猜。
* **不支持就不摆**：manifest 没有 `spines`（极坐标 / 3D / 色条轴）画布无命中区；
  引擎没发某条轴的刻度元素时那两条边方向未知，示意图退回单个 `ticks_<side>`
  开关。刻度卡承接 `minor_length` / `minor_width`（`length` / `width` 只动主刻度），
  方向档带 `data-prop="direction"` 锚点供问题面板定位。
* 看护：`lib/tickSides.test.ts`（几何 + 映射全状态扫描）、`canvas/spineZones.test.tsx`
  （命中层：hover / 点击 / 优先级 / zoom / 触控 / 旋转 / 偏出去的边框）、
  `inspector/tickTaskCard.test.tsx`（示意图两带 + 四档 + 显示边 + 锚点）。

## 多选浮动栏与共享排列参照（2026-09-02，ADR 0036）

完整版在 `docs/adr/0036-multi-selection-context-bar.md`，改动前先读。

* **一个外壳三种目标**：`canvas/context-bar/ContextBar.tsx` 解析目标（单个图内元素 /
  单个画布对象 / 两个以上画布对象），出现与让位、落位（`position.ts` 纯函数）、
  Esc、拖动隐藏、portal 都在外壳；三种内容各一个文件。对外仍是 `ContextBar()`。
* **多选栏不是第二套排列系统**：按钮只发意图，落地走 `store/actions.alignSelectedTo`
  / `groupSelected` / `ungroupSelected`——与 `ArrangeSection` 同一个函数、同一条历史
  标签。按钮表在 `inspector/arrangeButtons.ts` **一份**，别在组件里再抄图标与顺序。
* **参照只有一份**：`store/arrangeStore`（UI 会话状态：不进文档、不进撤销、不
  persist、切文档不重置）。要读「此刻按什么对齐」就订阅它，不要再造模块级变量。
* **主选 = `selection.ids` 末位**。OverlaySvg 里主选轮廓 2 px 并挂
  `data-primary-selection`，联合框挂 `data-multi-selection-bounds`——浮动栏、e2e 与
  后续 coachmark 都锚在这两个节点上，别改名。
* **落位不查 DOM**：联合选区经 `position.selectionScreenRect`（与 OverlaySvg 的
  `toScreen` 同一份换算 + 视口原点）算窗口坐标。宽窄档两道判据：静态阈值
  `FULL_BAR_MIN_WIDTH` + 量出来放不下就降级；工具条盒子必须 `w-max`，否则
  `fixed` 盒子被可用宽度压扁、量到的不是自然宽度。
* **锁定对象不动但算进参照框**：`alignSelectedTo` 与拖动同用 `movableTargets`；
  对齐 / 成组 / 取消成组执行前 `finishActiveGesture()`。
* **本地活动信号** `lib/activity.ts`（`tavotto:activity`）：闭集 kind + 枚举 + 计数，
  无用户内容；核心 action 不 import onboarding；它不是遥测，别往 `telemetry` 里接。
* **Tooltip 不吃指针**（含 Radix 定位外壳，`index.css` 那条 `:has([role='tooltip'])`）：
  聚焦触发的气泡会停在下一排按钮上，真浏览器里点上去什么都不发生。
* 看护：`canvas/context-bar/position.test.ts` / `multiSelectionBar.test.tsx` /
  `canvas/primarySelection.test.tsx` / `store/alignSelectedTo.test.ts` /
  `store/arrangeStore.test.ts` / `canvas/contextBar.test.tsx`。

## 画布对象的右键菜单（2026-09-02，ADR 0037）

完整版在 `docs/adr/0037-quickedit-context-menu.md`，改动前先读。

* **两种外壳一个开关**：画布对象 → `canvas/ObjectContextMenu.tsx`（Radix 菜单，
  `ui/Menu.PointMenu` 外壳：零尺寸锚 + `modal={false}` + 键盘不外泄 + 焦点归还）；图内元素 →
  `QuickEdit.tsx` 里的 `role="dialog"` 弹层（含控件，不是菜单）。开合都在 `quickEditStore`。
* **五份清单只发意图**（`data-quick-menu` = `panel` / `panel-layout-only` / `text` / `mark` /
  `multi`）：排列 / 成组走 `alignSelectedTo` / `groupSelected` / `ungroupSelected`，readiness 走
  `projectReadinessStore.focusPanel`，其余走既有 action。菜单里**不许**出现几何、`!!script`
  之外的状态判断、第二份按钮表或参照。
* **右键的选区规则在 `ObjectView.onContextMenu`**：已在选区里一个字不动；不在 → 换成它 / 整组，
  并与左键一样退出图内编辑态（shift 混排进来的标注除外）。
* **`rebuildPanel`** = `POST /api/engine/invalidate`（与 `panel.file_changed` 同一个
  `pool.invalidate`）→ `markStale` → immediate 渲染；不改文档、不进历史；`invalidated: false`
  （native / 内嵌画布）照常重画但 toast 说「源脚本没有重跑」。**`resetOverridesConfirmed`** 就是
  属性页的 `resetOverrides`，只多问一句（写回过的面板换一句话）。批量锁定 / 隐藏收目标状态、
  一条历史（`setObjectsLocked` / `setObjectsHidden`）。
* **Esc 要在 document 捕获层止步**（根菜单与子菜单各一个 `onEscapeKeyDown`）：真浏览器在监听器
  之间有微任务检查点，Radix 关掉菜单后 React 已把节点卸掉，冒泡层的 `onKeyDown` 跑不到；
  **jsdom 没有这个检查点，删掉捕获层守卫照样全绿**——这类判据只有真浏览器抓得到
  （`e2e/quick-menu.spec.ts`）。
* 不可用的项用 `MenuItem.reason` 常驻原因，不用 tooltip（禁用项收不到指针）。
* 看护：`canvas/objectContextMenu.test.tsx` / `store/quickEditActions.test.ts` /
  `tests/test_engine_invalidate.py` / `e2e/quick-menu.spec.ts`。

## 设置外壳与包管理（2026-09-02，ADR 0038）

完整版在 `docs/adr/0038-settings-shell-agents-packages.md`，改动前先读。

* **外壳尺寸是合同**：`SettingsDialog` 固定 `SHELL_WIDTH = 760` / `SHELL_HEIGHT = 600px`
  （`ui/Dialog` 的 `height`），内容区 `[data-settings-content]` 独立滚、切页滚回顶部；<640px 导航变
  顶部一条。**新分区再长也不许让外框撑高。** 十一个分区在 `SECTIONS`；旧 id 走 `resolveSection()`
  的别名表（`profiles → spec` 等），深链的调用方**不要**再写旧 id。
* **深链带返回**：`setSettingsOpen(true, section, { returnTo: 'export' })`；`settingsReturnTo` 是闭集
  （`'export' | null`），每次打开重置。要加新的返回目标先扩闭集。
* **编码 Agent 一级列表只有名称 · 版本号 · 状态**：版本号经 `agentVersionLabel` 只取数字，抽不出
  就不渲染（真机上 shim 的报错行带完整路径）；路径 / 命令 / 检测来源只在 `AgentDetailView`，
  用 `settings/CopyButton` 给复制。**一级页面上不许出现路径、内部包名、解释段、卡片外框。**
* **包管理只操作当前项目的 Tavotto 受管环境**：`store/packageStore.ts` 的 `plan(op, spec)` →
  `run(jobId)` 两步，**`run` 只在 `PackagesSettings` 里被调**——教程 / readiness / watcher 只能
  深链到包管理页，不许替用户点 run。错误文案走 `DependencyRepairCard.repairCodeMessage`
  （`errors:engine.repairError.*`，与缺包修复同一张表）。「没有回滚」那句话常驻，别删。
* **诊断页不显示 `cli_*` 检查**（Agent 页已有），渲染环境卡只在技术详情里一张，内置包清单归包管理页。
  「复制诊断」的文本来自 `fetchDiagnosticsSummary()`（后端同一份采集），前端不另拼。
* 看护：`SettingsDialog.test.tsx` / `settings/PackagesSettings.test.tsx` /
  `settings/DiagnosticsSettings.test.tsx` / `settings/agentState.test.ts` / `e2e/settings-shell.spec.ts`
  （外框逐像素、溢出、窄窗口、英文、方向键、axe——**量之前先等 `getAnimations().finished`**）。

## 交互式 Onboarding 与本地活动信号（2026-09-02，ADR 0040）

完整版在 `docs/adr/0040-onboarding-coachmarks-and-hints.md`，改动前先读。

* **本地活动信号 `lib/activity.ts` 是闭集**：`ACTIVITY_KINDS` 列 kind、`ACTIVITY_PAYLOAD_KEYS` 列允许的
  字段（只有枚举与计数：**没有 id / gid / name / path / text / value**）。新增一种信号 = 加 union 分支 +
  进两张表 + `activity.test.ts` 加样本；**一个 action 一个发射点、只在成功之后发**，组件里不补第二枪。
  它不是遥测：不出网、不落盘；Prompt 22 映射遥测只许从这张表挑，且必须经同意态与后端白名单。
* **教程状态只在 `store/onboardingStore.ts`**（`tavotto.onboarding`）：状态机 / 步骤 id / 提示记录 /
  教程项目与文档 id；不记 DOM、文案、路径、对象 id。改步骤内容升 `ONBOARDING_FLOW_VERSION`，
  **不改 step id**（`lib/onboarding/stepIds.ts` 是持久化格式的一部分）。关掉 coachmark 是 `paused`
  （`pausedBy: 'user'`），切走项目是 `paused`（`'system'`），绝不伪装 `completed`。
* **四个入口共用 `lib/onboarding/tutorial.ts`**（`tutorialEntry / runTutorialEntry / resetTutorial /
  resetHints`）：项目选择器、顶栏更多、命令面板、设置常规。**不许在入口里判状态**。打开教程走
  `projectStore.adoptOpenedProject(status, { prepareDocument })`——与打开任何项目同一条认领链路；
  教程画布的 documentId **必须**是 `metadata.document_id`（T-106）；同一项目里再点入口不走认领。
* **完成条件在 `lib/onboarding/steps.ts`**：状态可说清的读 store，说不清的读 `StepSignals`（引擎按
  信号累计、按 `consumes` 消费）。教程要编辑的是带 `spec_issue` 的那张（T-108）。**不用 DOM 文案 /
  CSS class 猜状态；不为教程复制任何 action。**
* **锚点是稳定的 `data-*`**：`data-onboarding-anchor="export | export-scope | add-to-layout | to-layout
  | tutorial-entry | help-tutorial | settings-tutorial"`、`data-object-id`、`data-card`、`data-rail`、
  `data-issue-row[data-issue-rule][data-issue-object]`、`data-multi-selection-context-bar`、
  `data-element-svg`（+ manifest bbox）。**aria-label / 文案 / class 都不能当选择器。** 改了这些
  属性要同步 `steps.ts` 与 `e2e/tutorial.spec.ts`。
* **coachmark 没有遮罩、不改偏好**：`reveal()` 露出折叠侧栏直接 `uiStore.setState`（不经 `setLeftTab`
  的 persist）；画布对象被平移出 `[data-canvas-stage]` 时只调 `viewportStore.revealRect`。锚点在
  `[role=dialog]` 里就 portal 进那个节点（模态层外面点不到）。Esc 只在焦点落在卡片里时暂停。
* 看护：`onboardingStore.test.ts` / `activity.test.ts` / `selectionStore.test.ts` /
  `lib/onboarding/{position,flow,tutorial,hints}.test.ts` / `components/onboarding/onboardingLayer.test.tsx` /
  `e2e/tutorial.spec.ts`（四条：完整走完 / 刷新恢复 + Esc + 更多菜单 + axe / 重新开始 / 切项目暂停继续）。
  jsdom 里所有盒子都是 0×0：层的用例要给锚点 `getBoundingClientRect` 假矩形；用假计时器时 flush 要
  `advanceTimersByTimeAsync`，别等真的 setTimeout。

## Codex / AI 刷新、入口整合与遥测映射（2026-09-02，ADR 0041）

完整版在 `docs/adr/0041-codex-ai-refresh-and-telemetry-integration.md`，改动前先读。

* **项目文件变化只走统一刷新**：前端唯一的刷新入口是 `liveSync.refreshProjectNow()`（调
  `/api/project/refresh`），命令面板 `refresh-project`、顶栏「更多」、素材库按钮都调它。**不新增
  第二套 watcher，不在前端猜 readiness**——接入状态只读 `projectReadinessStore`（后端事实），
  打开它走 `openCenter({ source })` / `focusPanel(id, source)`，`source` 是闭集
  `banner | panel | quickedit | palette`，新入口必须带上（不带 = 不记遥测，不是默认值）。
* **`ai.done` 不 markStale**：文件变了的话后端在它之前已经作废 worker、跑过刷新、发过
  `panel.file_changed`（`reason: 'ai'`），stale 只由那条事件置一次；`reason === 'ai'` 时不弹
  「脚本已更新」，一次修改只留 `ai.done` 那条提示。`ev.refresh.status === 'failed'` 要单独说
  （`ai:status.aiChangedRefreshFailed`），不把代码改动伪装成全部成功。
* **onboarding 活动信号与遥测分离**：`lib/activity.ts` 不出网；活动 → 遥测的映射**只有**
  `lib/activityTelemetry.ts` 一处、只映射浮动栏的排列 / 成组 / 取消成组（`fromContextBar()`
  作用域内发出的才算），其余 kind 逐种反证为不映射。遥测永远不反过来驱动界面。
* **新遥测事件只捕获成功边界**：`document_saved` 在 `scheduleDiskWrite` 的三个结局；
  `recovery_action` 在恢复 / 保留主版本的动作里；`tutorial_step_completed` 只在
  `completeStep(id, 'done')`（跳过不记）；`tutorial_started` 只在真的开始 / 重新开始。所有
  字段先进后端 `EVENTS` 表（两侧对拍），前端不发表里没有的键。
* **命令面板的 id 是稳定标识**（e2e 与资源都认它）：`refresh-project / readiness / tutorial-start /
  tutorial-resume / tutorial-reset / hints-reset / shortcut-help`；项目命令按
  `projectStore.phase === 'open'` 出现，embedded / playground 整组不出现。中英文 label + keywords
  两份都要有（`CommandPalette.test.tsx` 比两份资源的 id 集合）。
* **UI 文案用「可编辑的图 / 仅排版」**，不把 parameterizable 翻成「可参数化」；注册表对话框那类
  高级入口说「已登记的源脚本」。
* 看护：`lib/activityTelemetry.test.ts` / `components/CommandPalette.test.tsx` /
  `store/projectReadinessStore.test.ts`「打开接入中心的遥测」/ `hooks/useServerEvents.test.ts`
  「AI 修改之后」。

## 前端诊断：状态快照与交互轨迹（2026-08-27，ADR 0016）

完整版在 `docs/adr/0016-diagnostics-v2-frontend-state-tracing.md`，改动前先读。
模块是 `web/src/diagnostics/`，业务代码**只 import `@/diagnostics`**。

- **权威判据不在这里**：诊断报的 `authority_variant` 一律委托上一节的
  `exactPanelRender`（ADR 0017）。诊断**绝不另立一份判据**——否则会出现
  「诊断说权威就绪、写路径当场拒绝」，两边各说各话。因此权威只有两种取值：
  **就是当前这一版，或者根本没有**；「来自别的变体的权威」这个概念不存在。
  ADR 0017 的追踪环（`lib/authorityTrace.ts`）已并入本模块，别再建第二个环。
- **只观察，不当真源**：诊断不参与任何业务判断，快照是**读**业务 store 得来的，
  不维护影子状态（影子状态会漂移，漂移的诊断比没有诊断更坏）。
  `recordDiagnosticEvent` 整体吞异常——诊断把一次编辑弄挂比没有诊断糟得多。
- **隐私是两道判据不同的防线**：`types.ts` 的可辨识联合挡编译期手滑（事件里写
  `text: element.label` 直接 TS 报错），`sanitize.ts` 的**逐事件字段表**挡运行期。
  序列化**遍历 schema 而不是输入的键**——多出来的字段是「根本没被读过」，
  不是「读了再丢掉」。**写入即脱敏**：环里物理上不存在未脱敏的数据。
- **`data-display-key` 是对外暴露面**：它落在 DOM 上（e2e 读它、用户也看得到），
  用的是 `diagnosticHash`。`diagnostics/privacy.test.ts` 是它不泄漏文件名与
  override 原文的唯一看护，别删。
- **定长 240 条、纯内存**：不写磁盘、不自动上传、不进 telemetry；只有用户点
  「导出诊断包」才 POST 给本机后端。**切项目要 `clearDiagnosticTrace()`**
  （已接进 `resetForNewProject`）——否则新项目的包会带着上一个项目的操作序列。
  `seq` 刻意不重置：编号缺口是「这里被清过 / 被环挤掉」的唯一线索。
- **不记 mousemove、不记每一帧预览**；document 摘要只在真状态边界算，
  且靠 immer 的结构共享 + WeakMap 缓存（`digest.ts`），改一个对象只 hash 一个。
- 看护：`web/src/diagnostics/*.test.ts` + `tests/test_diagnostics_bundle.py`
  （服务端第二道校验、ZIP、端到端隐私回归）。

## 命中与选择几何

- 图内元素的命中 / 框选 / 描边全在 `web/src/lib/pathGeom.ts`（距离一律换到 mm
  再比，与图内箭头同一口径；填充按 nonzero 缠绕数算内部——判据的完整理由见
  `src/tavotto/AGENTS.md` 的「PDF 后端边界」，别在别处另写一份 even-odd 的；
  空心只在描边附近命中；框选是「圈墨迹」不是「戳进去」）；`OverlaySvg` 画
  `<path>` 并套上引擎给的 clip 框。
- **文字 / 图例 / 子图 / 组选择继续用矩形**——它们本来就是矩形语义，别为了统一
  硬转路径。画布**原生**形状同理：`lib/shapeGeometry.ts` 的 `shapeOutline` 是
  ShapeView 显示、透明命中层、覆盖层选中描示**三处唯一的一份轮廓**
  （椭圆/三角/菱形/多边形/大括号；矩形不在此列，直线走端点那套）。
  看护 `pathGeom.test.ts` / `elementPathSelection.test.tsx` / `shapeOutline.test.tsx`。
- **重叠候选之间的轮换**（2026-09-03，issue #216）：`pickElement` 只回答得了
  「点这儿选谁」，重叠到**评分逐位相同**时给不出第二个答案——twinx 的孪生轴
  与宿主 bbox 一模一样、role 同为 `axes`，先登记的宿主恒胜，twin 容器直选
  点不中，而两个 bbox 之间没有任何空间信号可用。出路是让用户说「换下一个」：
  * **一份有序候选表** `pickElementStack`，`pickElement` 取的就是它的 `[0]`。
    排序 = 评分升序 + **评分相同按 manifest 登记序**（旧实现「严格小于才换
    优胜者」的逐位等价），所以**不轮换时选谁一个字节没变**；评分相同的候选
    因此在表里相邻，宿主的下一个永远是它的孪生轴。别改成靠 `sort` 的稳定性
    兜着——那是隐含依赖，而这里正是重叠次序唯一的出处。
  * **⌥ 点击**在候选间轮换（`cycleOverlapAt`），排在**边框命中区之前**：孪生轴
    与宿主的边框逐位重合，正是最需要轮换的那一点。⌥ 只换选中，不写文档、不
    进历史、不起拖动；⇧ 归加选，两个修饰键各管一件事。
  * **换到了谁必须说出口**：两者的选择框逐像素重合，只换 `selectedGids` 的话
    画布上一个像素都不变，轮换在用户眼里就是「随机换了个选中项」。toast 走
    `status.elementCycled`，措辞用元素树 / 属性页那份 `engineLabel`
    （「子图 2（右轴）」，引擎侧出处 `engine/manifest.py::_twin_axes_labels`），
    **不另造第二套**；`StatusToasts` 自带 `aria-live`。
  * **⌥ 双击不给破例**：两个 pointerdown 已经各轮换一次，`onDoubleClick` 再弹
    快速改字的话，用户要的是「换一个」、拿到的是一次没要的编辑，而且弹层认的是
    `pickElement`（重叠时恒为宿主），与刚换到的不是同一个元素。
  * **键盘等价路径**（issue #37「画布操作要有对象树 / inspector 等价路径」）：
    ⌘K 的 `cycle-overlap` 命令跑同一个动作，没有指针就拿当前选中元素 bbox 的
    中心当那个点（`cycleOverlapSelection`）；几何权威没就位时什么都不动
    （ADR 0017），由调用方说「正在同步」。元素树本来就分得清孪生轴，那是第二
    条键盘入口。**这条路有两个坑，两个都要堵**：① bbox 中心**不一定落在那个
    元素身上**（U 形曲线的中心在杯口里），所以 `cycleElementAt` 收一个 `anchor`
    排在表首（bbox 恒含自己的中心）；② 探针若每次现取就会跟着选中项漂走，
    第三下落进另一组候选，轮换变成出得去回不来的单程票 —— 所以一轮连续轮换里
    探针与 anchor **只取一次**（`cycleProbe`，钥匙是「上次是我选中的 gid」，
    用户点了别的自然失效）。只堵①不堵②的话环会从 3 缩成 2，照样回不去。
  * 看护：`canvas/twinAxesPick.test.tsx`（两个方向各一组：不按 ⌥ 时命中逐条
    不变 / 按 ⌥ 时换得到 twin、说得出是谁、绕得回来）+
    `e2e/twin-axes-pick.spec.ts`（真浏览器 + 真 matplotlib：引擎真的把孪生轴
    发成一个独立 axes 吗、⌥ 真的带得到命中层吗、播报真的看得见吗——jsdom 里
    命中层的 `getBoundingClientRect` 是桩出来的，这几件事量不到）。**它同时进
    webkit 那一腿**：⌥ 唯一没被量到的维度就是「换个引擎还带不带得到 `altKey`」。
- **图内箭头交互**与画布箭头同语义（2026-08-17，elementArrowEditing.test 看护）：
  命中/框选按**线本身**不按 bbox 空白矩形、选中/hover 沿线描示无矩形外框、
  拖端点 shift 锁 15°、整体拖 shift 锁水平/垂直/45°（分数坐标锁角必须换算到
  内容像素系）；图内文字/子图拖动同样有 shift 锁向，画布对象拖动可吸附图内
  元素中心线（elementSnapCandidates）。

## 项目系统与多画布（前端侧）

- 前端把 pj 存 **sessionStorage**（`lib/session.ts`，按标签页隔离，「不同
  标签页开不同图库」就是靠它）；`?pj=` 出现在地址栏时认下并立刻抹掉。
  SSE 事件带 `pj`，前端只处理属于本标签页项目的那些。后端语义见
  `src/tavotto/AGENTS.md`。
- **schema 3**：`ProjectDocument{project, canvases[], activeCanvasId}`；
  运行时激活画布仍是 schema 2 形状的 `documentStore.doc`（画布编辑代码零改动），
  持久化/读档统一走 `migrateToProject()`（接受 2/3）。画布切换换入换出
  undo 栈（canvasSessions）与 UI 会话（`store/canvasSession.ts`）。
  标签页 openTabs 按 documentId 存本机。后端 versions/package 接受 schema 2/3。
- 文档模型可选字段（schema 仍为 2，旧文档兼容）：
  `PanelObject.lockedGids / flipH / flipV`、`ObjectBase.layoutPinned`、
  `FigureDocument.layoutGroups`（行/列/网格约束，id 即 groupId，
  尺寸变化自动重排、undo/redo 不触发）。
- **剪贴板（2026-08-17）**：⌘C/⌘V 的主路径是**原生 copy/paste ClipboardEvent**
  （`e.clipboardData` 同步读写，`lib/clipboard.ts` 的 handleCopyEvent /
  handlePasteEvent）——WebKit（Safari / 桌面壳）不给非编辑区的异步
  readText/writeText，跨标签页粘贴只有这条路全浏览器通。keydown 层不再拦
  ⌘C/⌘V；按钮触发的复制仍走 writeText（点击是用户手势）。e2e
  cross-tab-paste.spec.ts 看护。
- **撤销防线（2026-08-17，数据损坏级）**：`txnUpdate` 在无事务时**丢弃更新**
  ——绝不静默直写 doc（拖动中事务被外部 endTxn/undo 结束后，pointermove 落进
  静默分支 = 位移绕过历史、撤销永远找不回，真实用户撞见过）。一切撤销入口
  （键盘 / 顶栏按钮 / 桌面菜单加速键）必须走 `runUndoRedo`（带
  undoRedoBlocked 守卫）；undo/redo 的 applyPatches 有 try/catch，坏补丁丢弃
  该条而不是让栈与文档错位。
- **画布标签常驻图层**：每个打开的标签一个图层，非激活的用 canvases 快照渲染
  并 display:none——docToCanvas/canvasToDoc 共享同一 objects 数组引用 +
  ObjectView memo，切换标签 = 纯 CSS 显隐，不重建 DOM / 不重新解码图片。
- **自动保存**：磁盘为主（`PUT /api/autosave/<docId>` 原子写
  `layouts/_autosave/`），localStorage 只留索引 + 崩溃兜底副本
  （写盘成功即清、读取按 updatedAt 取新）。失败发
  `tavotto:autosave-error` 事件 → 常驻错误 toast。
- **`doc` / `canvases` 的变化有三种性质**，`startAutosave` 的订阅按两个代次
  区分，**改这段之前先想清楚新写入属于哪一档**：

  | 性质 | 判据 | `dirty` | `saveState` | 撤销历史 | 落盘 |
  | --- | --- | --- | --- | --- | --- |
  | 载入 | `loadSeq` 变了 | 由载入方声明 | 由载入方声明 | 清空 | 不排队 |
  | 用户编辑 | 两个代次都没变 | 置位 | 推成 `dirty` | 进 | 排队 |
  | 外部派生同步 | `derivedSeq` 变了 | 置位 | **不动** | **不进** | 排队 |

  第三档的唯一写入口是 `documentStore.applyDerivedUpdate()`，唯一调用方是
  `store/panelSourceSync.ts`。「不动 `saveState`」是因为一次外部文件改动不是
  用户的编辑（`hasUnsavedWork()` 读的正是它，推了会让关闭保护拦一件用户没做
  过的事）；「照样排队落盘」是因为 `script` 是**存进文档的字段**，只改内存的话
  下次打开面板又回到不可编辑。写盘本身照常走状态机，`save_error` 一个不吞。
- **外部修改 → 画布的闭环只有一条路径**（Prompt 06）：SSE 事件、素材面板的
  「刷新项目」按钮、SSE 重连恢复，三个入口都走 `store/liveSync.ts` 的
  `refreshAssetsAndSync()`。合并做在两层——`assetStore.load()` 复用同项目的
  在途请求（一批事件一个 `/api/panels`），`syncPanelSourceMetadata()` 无差异
  零改动（并不成一个请求的那些也不会重复置 dirty / 重复弹提示）。
  `assetStore` 的三条并发纪律：**请求序号**挡旧响应覆盖新响应（不是"谁最后
  返回"）、**发请求那一刻的 pj** 挡串项目（`null` 与具体 id 是两个取值）、
  **失败不清空** `panels`/`byId`。`force: true` 永远另起一次（手动刷新不许被
  在途请求吞掉）。
- **派生字段 vs 用户数据**（`panelSourceSync.ts` 的表）：只有
  `script` / `cost` / `fileKind` / `pxW` 由 `/api/panels` 说了算；
  几何、`nativeW/nativeH`、crop、rotation、overrides、成组、锁定、选择一律
  不碰。**图幅不是派生字段**——它是几何（`useEngineSync` 盯着它调 `h`），
  而且权威在这个变体自己渲染回来的 manifest 上，不在磁盘文件上。runtime 面板
  整个跳过（`runtime:` 前缀的 id 永远不在 `/api/panels` 里）。
  **素材不在清单里 ≠ 脚本关系失效**：前者只记 `missing`、对象一个字节不动
  （网盘抖一下不该让一批面板永久失去编辑入口），后者才降级并清掉失效的
  manifest / 渲染缓存（`renderStore.reset`）——只置 `script = null` 是不够的，
  留着的 manifest 会让元素树与检查器继续按"可参数化"办事。
- **标注**：任意角度 `rotationDeg`（面板除外；导出走 PyMuPDF morph，
  CSS 顺时针 = **Matrix(-deg)**——morph 矩阵作用在 PDF y 向上空间、正角是
  逆时针，实测结论见 `_obj_morph` 注释与旋转方向看护用例）；形状
  triangle/diamond/polygon/brace + 圆角/
  虚线/填充透明度；箭头 headStart/headEnd（triangle/open/bar，旧 head 字段
  兼容推导）；文字下划线/行距/内边距/背景/描边。**前后端几何公式同源**
  （shapeGeometry.ts ↔ pdfbackend/pymupdf_backend.py `_polygon_points`/`_dash_pattern`
  同名注释），改一边必须同步另一边，pytest 用 get_drawings() 做几何级看护。
  科研预设在 `lib/presets.ts`（纯既有对象组合）。
- **混排对齐（2026-08-17）**：图内编辑态里 **shift 点画布标注**（文字/箭头/
  形状）= 加入混排选区、不退编辑态（ObjectView 的唯一例外分支）；元素检查器
  的 AlignSection 接受 `MixedEntry`（元素写 override、标注改画布 x/y），经
  `applyMixedAlign` **同一次 commit**——一条撤销回滚两边。标注框由
  `annotationAlignEntries` 换算进面板内容分数空间；面板带旋转/翻转不给条目。
- **写回原图可携带画布标注（2026-08-17）**：写回对话框勾选后，与目标面板
  重叠的标注（重叠面积最大者得、一条只进一张图）由
  `lib/writeBackAnnotations.ts` 换算成**图自身 mm**（长度类字段按显示比例
  同缩），后端 `pdfbackend.annotate_asset` 用导出合成同一组 `_draw_*` 矢量
  画进 PDF、PNG 由注好的 PDF 重栅格化（两载体同源）；只有 PNG 的素材回
  `annotations_need_pdf`。写回成功后画布原件移除（可撤销）。面板带旋转/
  翻转不支持（UI 给原因）。
- **空状态**：一律用 `components/ui/EmptyState`（图标+短标题+≤1 句+≤1 动作）。

## 素材库普通入口（2026-08-26，Compatibility Bridge Session 5）

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
  落进 B（Session 6 评审修复；vitest 各有作废用例看护）。
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
- 看护：`scriptRunStore.test.ts` / `ScriptLibrary.test.tsx` /
  `AssetBrowser.runtime.test.tsx` / `runtimeSourceSection.test.tsx` +
  `e2e/asset-library.spec.ts`（show-only 项目真实后端黄金路径 + 窄视口 +
  保存/关闭/重开/重放/预检/导出完整链 + 多 Figure 选择器）。

## `tavotto run` 的桌面面（2026-08-28，Compatibility Bridge Session 9B；ADR 0021）

CLI 拥有用户的 Python，**桌面只是渲染面与那一道闸**。前端能提交的只有一个
不透明的 `native_id`——host / port / token / 完整命令一律由后端从那份 0600 的
descriptor 文件读。

- **交接 ID 有两条入口，两条都必须带**：首启走落地 URL 的 `?native=`（壳的
  `landing_query()`），二次交接走 `tavotto:open` 事件的 `native`。**漏掉哪
  一条，那一条上的 CLI 就一直挂到 attach 超时（300s），而两边都不报错**
  ——首启这条曾经真的漏过一轮，每条门禁都是绿的。看护
  `test_run_beta_claims.py`（跨 Rust / TS / Python 的连线）+ `main.rs` 的
  `landing_query` 四条 + `openRequest.test.ts`。
- **确认屏是闸不是提示**（`NativeConfirmDialog`）：CLI 此刻阻塞着，用户的
  Python 一行都还没跑。所以 `blockDismiss`——点外面和 Esc **不算回答**。
  展示 descriptor 里那条 invocation；参数只报**个数**（值不经过界面）。
  `□ 记住此项目和此 Python` 默认不勾；记住过的直接批准，不再问。
  **两个终端各跑一条 → 排队**，不是留一个丢一个。
- **`nativeSessionStore` 的四条纪律**（vitest 看护）：事件按 `sequence`
  判序（终态不回头是它的推论）——SSE 断线重连的补发与新事件之间没有次序
  保证，照单全收的表现是脚本已经退出了、卡片却又变回「正在运行」；项目
  代际（与 scriptRunStore / runtimeAssetStore 同一条）；每条会话上的动作
  互斥（单 reader 传输上，连点两次的第二条响应没有人等）；错误存
  **code + params**，不存成品字符串。
- **屏障处的 build 由界面显式发**，后端不在收到 barrier 事件时自己发——
  那条事件在 reader 线程里，而 build 的响应要由**同一个** reader 读回来
  （ADR 0021 §5.2，自己等自己）。一张图直接进画布、多张开
  `FigurePickerDialog`（native 只是换了个数据源）。
- **面板角标只在需要说话时说话**（`nativePanelState`）：停在屏障上 → 无；
  脚本正在跑 → 「停下来才能编辑」；出自 native 但没有活会话 → 「会话已
  结束」。后两句都是**在用户点进图内编辑之前**说的——不说的话他撞到的是
  一条 409，而那两句话描述的是**正常状态**、不是故障。判据**按描述符里的
  asset id 认领，不按 stem 猜**（同名 stem 在两个项目里到处都是）；
  「这张图出自哪一档」来自 `/api/runtime/status` 的 `execution_profile`
  （出处是 `enginesession.profile_of`，与渲染路由同一份判据）。
  **未知不等于 native**：老后端不给这个字段时按 safe，反过来会给每个普通
  runtime 面板都挂上「会话已结束」。
- **`clear()` 不杀用户的脚本**：那些进程是他自己在终端里起的，切个项目不该
  杀掉它们。切回去时 `refresh()` 重新对账（同样按 sequence，不覆盖 SSE 已经
  送到的更新状态）。
- 看护：`nativeSessionStore.test.ts` / `NativeConfirmDialog.test.tsx` /
  `openRequest.test.ts` / `runtimeAssetStore.test.ts`。

## 接入状态与左侧外壳（2026-08-29，Prompt 08）

「这张图能不能编辑」的**事实**只有后端 `engine/readiness.py` 一个出处
（六个 status + 十个 reason code 的闭集，ADR 0027）。前端只翻译不判断——
界面里**一个 `!!script` 的状态分支都没有**。

- **句子与「待连接」只有一份实现**：`lib/readinessText.ts` 的 `statusLabel()`
  （读 `status`）、`reasonText()`（读 **`reason_code`**，不读 `status`），
  以及 `PENDING_STATUSES` / `pendingCount(summary)`——横幅与接入中心顶部说的
  是同一个数，各展开写一遍的话，多一个状态时总有一处会漏掉。四个出口共用它：
  素材卡角标、素材说明条、接入中心每一行、属性栏那条提示。按状态查句子会让
  只读项目里的用户一直等一个永远不来的结果（`auto_linkable` 有四个 code，
  一个是"马上就好"、三个是"不做点什么永远不会好"）。
- **持有者只有 `store/projectReadinessStore.ts`**：并发纪律与 `assetStore`
  逐条相同（请求序号挡旧响应、发请求那一刻的 pj 挡串项目、同批合并、
  `force` 另起一次、失败保留上一次成功那份）；**fingerprint 没变时连报告
  对象的引用都不换**。刷新挂在 `liveSync.refreshAssetsAndSync()` 一处，
  与素材清单同一批事件、同一个 `force` 语义。
- **开关只有 `uiStore.registryOpen`**（`RegistryDialog` 的文件名与导出名保留）。
  就绪度 store 只管 `focusId`；`focusPanel(fileId)` 是 17/18 复用的入口。
  关闭后的焦点归位归 `ui/Dialog`，**别再记第二份**。
- **「没测量」三档不许压扁**：`conflicts` 的 `null`、`project.registry_valid`
  的 `null`、`PanelInfo.capability` 的 `undefined`。第三档的界面表现是
  **什么都不显示**——补成 `layout_only` 就是替后端撒谎。
- **界面不执行动作**：试运行走 `/api/registry/probe`（只由用户点出来，点之前
  先说「Tavotto 将运行这个脚本」）、手工关联走 `PUT /api/registry`（键是
  **`ReadinessPanel.stem`**，不是文件名）、重扫走 `/api/registry/scan`；
  每次成功之后只调一次统一刷新，不手拼状态。冲突**一个候选都不预选**。
- **`role="option"` 里不许再嵌可 Tab 的控件**：状态角标是 `<span>`，
  「查看接入状态」那个真按钮住在 listbox 外面的说明条里。
- **侧栏的「偏好」与「此刻开着没开」是两件事**（`uiStore` 的模块级
  `prefOpen`）：互斥断点的自动让位、窄屏开机的裁剪只改后者，**绝不写回
  本机偏好**。写反了的表现是"把窗口拖窄一次，常驻左栏就再也回不来了"，
  而用户从没关过它。判据只求值一次（`autoShowProperties` 的 `assetsYield`），
  写状态与写偏好共用它。
- 看护：`store/projectReadinessStore.test.ts`、`components/RegistryDialog.test.tsx`、
  `components/ProjectReadinessBanner.test.tsx`、
  `components/left/AssetBrowser.readiness.test.tsx`、
  `canvas/panelReadinessEntry.test.tsx`、`components/inspector/panelCapabilityNote.test.tsx`、
  `canvas/drawerViewportResize.test.tsx`、`store/uiStore.test.ts` 的两个左栏
  describe；e2e `a11y.spec.ts` 的接入状态两条 + `golden-paths.spec.ts`。

## 统一导出管线（2026-08-31，Prompt 12；ADR 0031）

```text
prepareExport(input)   请求成形 + 就地校验（**不发网络**，输入框每敲一个字都能调）
validateExport(input)  真的开始之前能看出来的：重名 / 目录写不写得了
runExport(input)       起作业 → SSE + 轮询跟进度 → 落终局
cancelCurrentExport()  取消（清临时文件；最终目录一个字节没动过）
```

- **载荷的构造只有 `lib/exportRequest.buildExportRequest()` 一处**。组件不许
  自己拼那个对象，也不许在第二个 API 上把同一批参数再抄一遍——那正是
  「预检按一套规矩、导出按另一套」的来源。
- **`scope=original` 的载荷里没有 x/y/w/h，也没有页面尺寸**。不是"记得别填"，
  是那几个键不在类型上。尺寸来自 `lib/originalSpec.getOriginalOutputSpec()`，
  被忽略的变换逐项进 `ignored` 并**说给用户听**。
- **PPI 只在有位图格式时是数字**，否则 `null`。压成一个默认值的话，界面就会
  去显示一个不影响任何东西的设置（T-49 同一个形状）。
- **作业活在 `store/exportStore.ts`，不活在对话框里**：关掉弹窗不取消作业。
  进度经 SSE `export.progress`，**外加一条轮询**——SSE 是加速器不是唯一通道
  （浏览器演练场、断线、代理下必须照样拿得到终局）。两条路进同一个
  `applyExportJob()`，晚到的旧快照按 job_id + 终局状态挡掉。
- **「导出期间又被编辑过」用此刻的文档重算指纹**，不是拿 `lastInput.doc`
  跟自己比（那份是开始时冻住的引用，比出来永远相等，而空的 diff 与"没变化"
  长得一模一样）。指纹量的是**载荷**：改画布名、折叠侧栏、撤销又重做，
  导出结果一样就不该冒这句话。
- **文件名规则是严格同源对**（`engine/exportreq.py`），八条闭集原因 +
  `tests/golden/filename_vectors.json`。首尾空白的字符集**写死一份**，
  不许退回 `String.trim()`（它与 Python 的 `str.strip()` 认的集合不同）。
- **原图不可用时说出原因，不隐藏选项、不静默改成画布**：一个消失的按钮
  无法解释自己，一次悄悄换掉的范围会让用户拿到一张他没要的图。

## 两条工作流与原图规格（2026-08-29，Prompt 09；ADR 0028）

```text
快速编辑：打开一张图 → 修改 → 按原图规格导出
画布排版：加入多张图 → 排列 → 按画布规格导出
```

**一个文档，不是两套应用。** 没有第二个 documentStore、第二个 override
writer、第二份对象模型：一张图在文档里只有**一个**面板对象，两种模式看的是
同一个对象。快速编辑 = 把它单独摆出来（页面纸 / 网格 / 参考线 / 别的对象
全部让开），画布排版 = 它在页面上的落位。

- **模式是工作区状态**：`store/workspace.ts` 的 `mode` / `activePanelId`，
  不进文档、不进撤销、不置 dirty，按 documentId 存本机一档
  （`tavotto.workspace.<id>`，与 `tavotto.tabs.<id>` 同一条纪律）。
  不变式：`mode === 'fast_edit'` ⟺ `activePanelId !== null`。
  **`activePanelId` 是对象 id 不是素材 id**——同一张素材可以有两个实例，
  用素材 id 的话工作区会在两个实例之间随机跳。
- **快速编辑一个字都不写 x/y/w/h**。「从画布进图内编辑再返回，布局不变」
  不是靠"回来时恢复一下"，而是靠**根本没动过**——恢复式的实现总有一条路径
  会漏掉（旋转、成组、布局组重排），而漏掉的表现是用户的版被悄悄改了。
- **四个稳定动作是唯一出口**（11 定位 / 12 导出 / 18 QuickEdit / 21 onboarding
  复用它们，别在界面里重新拼一遍"找对象 / 没有就添加 / 切画布 / 选中"）：
  `openFastEdit(figureId)` / `addFigureToLayout(figureId)` / `returnToLayout()`
  / `focusLayoutPanel(panelId)`。「文档里有没有这张图」的判据也只有一处：
  `findFigurePanel()`。
- **「添加到画布」不复制对象**：已经在文档里就只是聚焦它（`focused`），
  重复点不会叠出第二个面板，overrides 一直在同一个对象 id 上。
- **能不能进图内编辑用既有判据**（`panel.script`，与 `ObjectView` 双击、
  `enterElementEdit` 同一个）；**说什么话**归 `lib/readinessText.ts`。
  两者不是一件事，别在 workspace 里另起一份状态判断。
- **对象消失就退出快速编辑**，判据复用 `usePruneSelection` 里那个 `usable`
  （删除 / 隐藏 / 切画布同一条）。第二份判据迟早与图内编辑态分叉。
- **原图规格只有 `lib/originalSpec.ts` 一份服务**（ADR 0028）。优先级
  ① 渲染回来的 manifest `size_mm` → ② 文档里的 `nativeW/nativeH` →
  ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback（必带 `fallback: true`）。
  ① 在 ② 之前正是因为**图幅不是派生字段**（见上面的派生字段表）。
  画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度只进 `spec.ignored`，**绝不套进
  原图导出**——跟着缩的话字号会一起缩。`getOriginalOutputSpec()` 对不认识的
  id 回 `null`，不发明一张不存在的图。
- 看护：`store/workspace.test.ts`、`lib/originalSpec.test.ts`、
  `canvas/fastEditStage.test.tsx`；后端事实层 `tests/test_original_spec.py`。

## 桌面感知与更新

- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条更新通道（桌面归 Tauri，
  浏览器归 `/api/update/*`）。壳侧细节见 `src-tauri/AGENTS.md`。

## 多语言（zh-CN / en-US，2026-08-18）

完整版在 `docs/i18n.md`，改动前先读。

- 技术栈 i18next + react-i18next + 官方 `i18next-cli`；**资源静态 import 进
  bundle**（离线桌面版是硬要求，不连 CDN）。八个命名空间在
  `web/src/i18n/locales/<语言>/`。默认仍是 **zh-CN**；优先级
  手动 > 系统 > zh-CN，偏好存独立的 `tavotto.locale`，**不进 .tavotto 文档**。
- 组件用 `useTranslation()`；store / lib 用 `import { t } from '@/i18n'`。
  **活得比一次渲染长的文本存描述符** `UiMessage {key, ns?, values?}`
  （撤销标签、toast、确认框），显示那一刻才翻——存成字符串的话切语言后历史
  面板永远停在旧语言，而且再也换不回来（参数已经拼进去了）。用户自己的内容
  包 `literal(text)` 原样透出。这是运行时状态，**文档 schema 一个字节没动**。
- **复数形态按语言定**（`Intl.PluralRules`）：英文 `_one`+`_other`，中文只有
  `_other`。中文写 `_one` 不报错但永远选不中，那句译文是死的。**「单数是
  另一句话」的必须自己分 key**（`deleteObject` / `deleteObjects` 等四对），
  交给复数规则会让「删除 折线图.pdf」在中文界面变成「删除 1 个对象」。
- **不翻**：用户内容（项目/画布/文档名、路径、脚本、图内文字、matplotlib
  输出）、诊断材料（traceback / 日志 / 后端报错原文 / console）。matplotlib
  的属性名与枚举是**开集**，`propLabel/optionLabel` 查不到就回退原文。
- **引擎协议里的中文不动**：manifest 的 `group`/`label` 仍由
  `engine/manifest.py` 发中文，前端 `roles/registry.ts` 用 `ENGINE_GROUP` 表 +
  `ENGINE_LABEL_PATTERNS` 正则翻结构部分、用户内容原样带过去；`GROUP_ORDER`
  仍按引擎名排序（分区顺序不该跟着界面语言变）。
- **Python 不决定界面语言**：用户可见的失败带稳定 `code` + `params`
  （约定写在 `app.py` 的 API 段首），前端 `backendErrorText()` 按 code 翻，
  `error` 原文留作回退。**code 一旦发布不能改名**。
- **Style / Spec / Export 三层各有唯一出处**（ADR 0029）：Style 是
  `lib/stylePresets.ts`（应用 = 一次可撤销的文档修改，走 `applyStylePlan`）、
  Spec 是 `lib/specBinding.ts`（**「这个项目按哪套规范检查」只在这里判**）、
  Export 是 `lib/exportDefaults.ts` + `exportPayload.ts`。清单的持有者只有
  `store/profileStore.ts`，**组件里不许有 fetch，更不许有磁盘格式的知识**。
  文档里存的是**绑定 + 规则全文快照**，「有没有新版」的判据是**内容不等**
  （`sameRules`），不是版本号。profile 在界面上叫什么只有
  `lib/profileText.ts` 一处（内置跟界面语言走、用户起的名字不翻译，
  **默认视图不出现 id 与版本号**）。字号下限一个字都不许写进求值器或界面，
  缺键兜底只有 `lib/profile.FALLBACK_MIN_FONT_SIZE_PT`（Python 侧同名，严格同源对）。
- **出版规范预检的文案在前端**：`web/src/lib/preflight.ts` 的 `PreflightIssue`
  存的是描述符（`message: UiMessage`），`id` 才是稳定身份——golden vectors
  （`tests/golden/preflight_vectors.json`）与 proof report 认的都是 id，
  `preflight.golden.test.ts` 明确**只比判据不比措辞**，所以两侧求值器的中英文
  措辞可以各自演进。proof report 里写的是**当前语言的成文**（人要读）+ id。
- **MCP 画布里的预检条目按 widget 的 locale 渲染**（issue #30）：Python 求值器
  随每条 issue 发可翻译描述符 `message: {key, params}`，widget 用
  `errors:preflight.<key>` 渲染，`it.text`（Python 中文成文）只作老引擎/未登记
  key 的回退。widget 的语言跟随 **Codex host**（握手的 hostContext.locale +
  `host-context-changed` 通知），不落 iframe 的 localStorage。key+params 与
  前端求值器逐字对齐（golden vectors 连它们一起比），新 key 没在双语文案表
  登记时 `test_every_message_key_is_registered_in_both_locales` 先红。
  widget 自己的按钮/状态/标题照常翻。
- **维护**：`cd web && pnpm i18n:check`（= `types --ci` + 自建检查脚本 +
  `lint`），查 key 对齐 / 漏翻多余 / 空翻译 / 插值一致 / 复数形态 / 无用 key /
  硬编码文案 / 类型过期。**CI 里是硬门禁，缺翻译直接红**：接在 ci.yml 的
  frontend job 与 `scripts/build_frontend.py`（每条打包链路都过它）。
  官方提取器覆盖不了本仓库的短助手（`hist('setPageW')` 这种），所以自己写了
  `web/scripts/i18n-check.mjs`——**别为了让官方 CLI 过而降低检查范围**。
- **`errors.json` 另有一道反向门禁**：`tests/test_i18n_dead_keys.py` 查「翻译键
  有没有发射点」。`test_error_codes.py` 与 `i18n:check` 都只管单向（发射的码有没
  有翻译 / 生成物是否最新），三方冲突时「盲目取并集」留下的死键能通过它们全部。
  发射点横跨三处源码——`src/tavotto/**.py`、`web/src/**.ts(x)`、
  **`codex-plugin/mcp/tavotto_mcp/`**（`errors:preflight.*` 的第二个发射点）；
  扫描范围里绝不能放生成物（`resources.d.ts` / `canvas.html`），放进去这道门禁
  就恒绿。判「有发射点」只认**引号字面量**或**完整键路径**——剥掉命名空间之后的
  裸子串会让一个死键靠另一个命名空间的同名活键蒙混过关。豁免按**键**不按文件、
  必须写理由；动态拼接的容器（`` en(`sourceLabel.${…}`) ``）不是整片放行，它的
  子键必须与一个**闭集出处**逐字相等（`EngineSource` 联合类型现取，不再抄一份）。
- 英文更长：`web/src/i18n/overflow.test.tsx` 守字数预算与截断兜底，
  `e2e/i18n.spec.ts` 在真浏览器 1024px 下量 `scrollWidth > clientWidth`
  （jsdom 没有布局引擎，量不出溢出）。
- 桌面壳自带一份文案（原生菜单、splash）——见 `src-tauri/AGENTS.md`。

## 浏览器 playground（网站 /try，2026-08-21）

完整版在 `docs/adr/0007-browser-playground.md` 与
`docs/adr/0011-playground-examples-first.md`，改动前先读。引擎侧
（`engine/browser.py` 的平铺 import 纪律与 ENGINE_FILES 白名单）见
`src/tavotto/AGENTS.md` 末节。

- 前端走 `engineTransport` 的第三条传输（`web/src/playground/`），画布 /
  inspector / stores / undo 与桌面同一份；MCP 与 playground 共用的种子层在
  `web/src/embedded/session.ts`。
- **Pyodide 版本与包白名单钉死在 `packaging/playground-runtime.json`**（唯一
  权威；前端 JSON import + 构建脚本共读）。不自动装任意 PyPI 包；不支持的
  import 在下载科学栈**之前**报 `unsupported_import`（`engine/browser_imports.py`
  纯标准库，分类必须先于 matplotlib 下载）。
- **超时与取消在 Worker 边界**：任意同步 Python 没有协作取消，到点
  `worker.terminate()` 且**会话作废**；一个文件 = 一个 Worker，换文件不复用
  解释器。主线程只接受 id 配对 + 形状合法的 Worker 消息（Python 摸得到
  postMessage）。
- **隐私是可验证的**：源码只进 Worker，不进 localStorage / 不出网
  （e2e 哨兵测试盯着）。
- **「figure.py · 未改动」是两个真哈希比出来的**（2026-08-21）：主线程用
  Web Crypto 算原文的 sha256，Worker 侧用 `pyodide.FS.readFile` 把
  `/workspace/<脚本>` 的字节读出来再用 Web Crypto 算一次，两个数相等才显示
  「未改动」。**别退回 `loadedSource === originalSource` 那种写法**——两个
  变量指向同一个 JS 字符串，恒真，什么也没证明。
  **权威摘要必须在用户的 Python 解释器之外算**（`pyodide.worker.ts` 的
  `fsDigest`）：用户脚本跑在同一个解释器里、而且跑在核对之前，改完自己的文件
  再 monkeypatch `builtins.open` / 换掉 `hashlib.sha256` / 改
  `sys.modules['browser']` 的全局，就能让 Python 侧继续回报原摘要——
  **一个能被它所校验的代码改写的校验不叫校验**（e2e 原样跑那个场景，
  把摘要挪回 Python 就红）。`browser.py` 的 `source_status` 保留，验的是
  引擎语义、跑在 Pyodide 之外，不是重复。写文件必须**二进制**——文本模式在
  Windows 上翻译换行，比对永远 mismatch（只有 CI 的 windows 腿逮得到）。
  **`import js` 必须够不着**（`loadPyodide` 的
  `jsglobals: Object.create(null)`，**无原型是硬要求**——普通 `{}` 上
  `constructor.constructor('return globalThis')()` 就是一台 Function 构造器）
  ——这是上面那条成立的前提：Python 拿到 `js` 就能 `js.eval` 改 Worker 任何
  全局，连 `self.postMessage` 伪造整条响应都做得到（请求 id 自增、猜得到），
  那时**这个 Worker 里没有任何东西可信**。静态分类不是防线：
  `browser_imports` 有意放行 try/except 里的可选 import，`__import__('js')`
  它更看不见。可信原语（digest / Uint8Array / FS 读取）一律在模块求值期与
  init 期绑定好，是纵深防御。两道防线各有判据，少一道都有用例红。
  **定位是「查意外，不是防蓄意」**：`pyodide_js` 是 Pyodide 的基础设施、删不掉，
  而 `pyodide_js.constructor.constructor("return globalThis")()` 实测能拿到
  Worker 全局——只要用户 Python 与验证代码同在一个 Worker，蓄意规避总是做得到。
  按模块名封堵是打不完的地鼠，「挪到独立 Worker 验」也不成立（虚拟 FS 就在
  被攻陷的那个 Worker 里）。**界面上不许出现比这更强的说法**，源码面板的
  完整性明细里已经写明。
  Worker 侧的哈希在**脚本跑完之后**采；复验走独立的轻命令、**只在 worker
  闲着时发**（无阶段请求超时 30s，排在慢渲染后面到点 = 整个会话被
  terminate）。UI 四态：没验完不许说「未改动」，算不出哈希是「查不了」
  不是「没改」，不相等按不变式失效常驻报警。
- **案例优先，上传是次级入口**（2026-08-25，ADR 0011）：idle 首屏的主角是
  案例库（三张构建期真实执行生成的 Figure 封面卡 + 中央试验台），上传降级
  为底部「已有一个独立脚本？」，单文件边界在上传前写明。案例源码唯一真源
  是 `web/src/playground/examples/*.py`（examples.ts 走 vite `?raw`，
  **别在 TS 里抄第二份 Python**）；封面由
  `scripts/generate_playground_examples.py` 在钉死的 matplotlib 版本下真实
  执行生成，manifest 记源码 sha256——改了 .py 不重新生成封面，`--check` /
  examples.test.ts / 构建指纹三道闸都是红。**封面只用于卡片展示**：五条
  启动路径（拖入试验台 / 开始体验 / Enter / Code Sheet / 触屏点击）全部走
  `openSource()` 真执行，**不许用预烤 SVG/manifest 提速**。`EXAMPLES` 里
  **有且只有一个** `featured/starter`（examples.test.ts 看护）。拖拽只认
  鼠标指针（Pointer Events 自实现，不引框架），触屏和键盘走点击/Enter，
  reduced-motion 下不位移不缩放、只靠边框与文字表达。会话来源
  （example/upload）进状态机；首次引导只对内置案例出现且**只观察不代劳**
  ——完成语「一个字也没动」必须来自 verifySourceIntegrity 的真结论。
  加载可取消：`startSession` 的 `onClient` 交出在途 client，取消 = 真
  dispose，绝不并行两个 Worker。三个案例都在 savefig 前 `tight_layout()`：
  默认边距在这个 figsize 下会把 x/y 轴标签整条裁掉，而轴标签正是访客
  第一件想点的东西。
- **`/try` 空闲时预热 Pyodide 核心**（`web/src/playground/prewarm.ts`）：
  **只到核心 + engine.zip 为止**，科学栈仍等 import 分类说了话才下载
  （e2e 断言预热窗口里 wheel 零条）；`saveData` 或 `slow-2g/2g` 不预热，
  Network Information API **一律特性检测**（Safari/Firefox 上它整个不存在）；
  `PlaygroundClient.init()` 幂等去重，「预热中点了示例」接的是同一个在途
  Promise，**不会变成两个 Worker**；暖着的 Worker 还没跑过用户代码，所以可以
  当第一个会话用——**「一个文件 = 一个 Worker」没有松动**。预热是优化不是
  依赖：失败悄悄退回 cold，绝不在用户动手之前弹错误。营销首页
  （`/`、`/zh/`）**一个字节的 Pyodide 都不加载**，那是网站仓库的静态页面。
- 产物：`python scripts/build_browser_playground.py` → `web/dist-playground/`
  （确定性 engine.zip + 指纹 manifest，指纹算法复用 build_mcp_widget.digest）。
  网站仓库 `pnpm sync-playground` 收走并提交、`pnpm check-playground` 防漂移
  ——改了 web/src 或引擎四模块，**playground 与 MCP 画布两个产物都要重建**。
- 验证：`tests/test_browser_session.py`（CPython 上跑同一份 browser.py）+
  `web/src/playground/*.test.ts` + `web/e2e/playground.spec.ts`
  （真浏览器 + 真 CDN Pyodide，慢，专属放宽超时）。
  **篡改钩子只在测试驱动里，产品代码不给任何改工作区源文件的入口**。

## UI 视觉纪律

暖灰白 `#F2F2EF` 底 + 白色 surface；层级靠留白 / 字号 / 轻微背景差，
边框只给真实输入框、区域边界、选择状态与浮层。**持久表面不用 shadow，
浮层（菜单/popover/dialog/tooltip）可使用唯一轻投影 `--shadow-pop`**。
radius：控件 6px、浮层 10px、上限 14px。UI 字号 11-14px；控件高 28px、
树行高 28px、图标点击区 ≥28px。主按钮近黑色（`bg-ink`）；蓝色只用于
选择 / 焦点 / 链接；每个上下文最多一个填色主动作（顶栏=导出、助手=发送、
弹窗=确认）。文字对比：`ink-2`/`ink-3` 均 ≥4.5:1，`ink-faint` 仅装饰 / 禁用。
选中态不只靠颜色（左侧 2px 竖条 / check / 形状变化）。支持
`prefers-reduced-motion`。Document 字体（Times）与 UI 字体严格分离。

工作台结构：顶栏 44px（左=品牌/文档名/autosave，中=撤销重做+工具，
右=缩放/导出/更多）；左侧 44px 常驻图标轨道（素材/结构/图内元素）+
280–360px 上下文抽屉（再点收起）；右栏 296–320px 三模式（属性/改图助手/
画布），无选择且未钉住时不占位；断点 ≥1440 双栏可钉住、1024–1439 左右
互斥、<1024 覆盖式抽屉。底部无常驻状态栏：坐标/选区尺寸只在拖动中出现，
普通状态走短暂 toast，错误常驻可关，autosave 显示在顶栏文档名旁。
