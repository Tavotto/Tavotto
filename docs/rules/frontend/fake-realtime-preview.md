# 假实时预览：预览平面与历史平面严格分开（2026-08-18，Phase G）

> 原文出自 `web/AGENTS.md`「假实时预览：预览平面与历史平面严格分开（2026-08-18，Phase G）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
