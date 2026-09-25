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
* **挂 SVG 的 `dangerouslySetInnerHTML` 必须按字符串复用同一个 `{__html}` 对象**（`lib/useHtmlMarkup`）：
  React 19 按 `===` 比这个对象，每次渲染现写一个就每次原样重写 innerHTML——松手提交 override 让
  PanelView 重渲，写在节点上的预览位移随旧节点一起消失，元素**先弹回原位、等权威 SVG 到了才跳到新
  位置**（2026-09-24 用户实测，慢图上停顿半秒）；而字符串没变，`reattachPreview` 的 effect 也不跑。
  看护：`canvas/dragReleaseSnapback.test.tsx`（**真渲染 PanelView**；`fakeRealtimeDrag.test` 手写
  innerHTML 摆 SVG，看不见这一层）。
* **缩放预览只有一处**：`previewScale`（绕不动点的 `matrix(s,0,0,s,e,f) <原始>`，同样从 base
  现算、同样前置于原始变换、`reattachPreview` 连同倍数一起重放）只给图例整体缩放用——
  那里字号与间距同乘一个倍数，线性预览是准的；子图缩放仍然只给线框（matplotlib 重排后刻度与字号不跟着线性缩放，
  假预览会骗人，见 `startAxesDrag` 的注释）。
* **改挂只有一处**：`retargetPreview` 把已提交、还在等权威渲染的预览改挂到另一版上（账本换版、
  只留平移、换等待目标），只在渲染 store 的同步回调里调用（早于 React 换 DOM）。现在只有图例
  缩放的钉对角用它（见 `legend-entries-and-binding.md`）。
* **重放预览用 `useLayoutEffect`**（`PanelView` 的 `mountedEditSvg` 那个 effect）：新 DOM 挂上与重放
  在同一帧绘制之前完成；passive effect 可能先画出一帧没有预览的新图。
* **换一版 SVG 先解码它嵌着的位图**（`lib/useDecodedSvg`，2026-09-25 用户报「松手后整张图
  糊一下」）：imshow / pcolormesh 在预览 SVG 里是 `<image href="data:…">`，innerHTML 一换
  浏览器异步解码，那一两帧是空白 / 低清的中间态。新字符串到了先用离屏 `Image.decode()`
  解一遍、再交出去，**旧 DOM（连同挂着的预览位移）留到那时**；从无到有、纯矢量、撤下
  （null）、没有 `decode` 的环境立即生效；等待上限 `SWAP_DECODE_CAP_MS`，绝不把新图扣住；
  等待途中又来一版以最新为准。因此 `reattachPreview` 的 effect 认的是**真正挂进 DOM
  的那一版**（`PanelView` 的 `mountedEditSvg`），不是 store 里刚到的那一版；几何交互同理
  （#575 评审）：`store/mountedSvgStore` 记每个面板挂着的那份 SVG（记字符串不记键——不同变体
  可能出同一份 SVG），命中层与选中框 / 手柄一律经 `useDisplayedExactManifest` 取 manifest，
  权威那一版的 SVG 还没挂上画面时像权威缺席一样停摆，绝不让用户点着旧图、改新几何。
  **诚实的限制**：本机 Chromium / WebKit 截图与录像都没复现出「糊」本身（截图会强制
  同步解码），这一条防的是最可能的成因；用户机器上的实测才是验收。看护：
  `canvas/renderSwapFeel.test.tsx`。
* **「渲染中」角标过了 700ms 还没画完才亮**（同日，`PanelView.RenderStatusBadge`）：画布上已经
  是用户要的样子时（预览平面 / 上一版挂着），每次松手都闪一下角标只是噪音。冷启动与
  「挂着磁盘原图」（近似预览，画面与文档对不上）照旧立刻说。**`building` 不等于冷启动**：
  SSE 的 `render.started` 每次渲染都写一条 `cold: false`，判据只能看 `cold`（第一版判错、
  真浏览器里角标照闪才发现）。看护：`canvas/renderSwapFeel.test.tsx`。
* **手势不许比它的依据活得更久**（QA 2026-09-24 STATE-02 / 04 / 07，#583）：
  拖动中按 Esc = 先取消这次指针手势（`cancelActivePointerGesture()`，与 pointercancel
  同一条 `finish(true)`），这一下不做别的；松手后等图期间文档变了（撤销 / 重置），
  `settleUnbackedCommit` 按「这次手势写出的那组 patch（`commitElementPreview` 只交手势新写 /
  改写的，不含手势之前就有的无关 override）是否仍逐条在文档里」判——不在就还原，
  **不能**按「键还等不等于 awaitKey」判（松手后改别的也会换键，预览必须继续挂着）；
  拖动中视图倍率变了，`contentDelta` 以变化那一刻重建基准，不按新倍率重算整段位移。
  看护：`canvas/dragGestureLifecycle.test.tsx`、`e2e/drag-gesture-lifecycle.spec.ts`。
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

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 临时 transform 写成 `translate(…) <原始 transform>`、从 base 现算
- `pointercancel` 与 `pointerup` 分开
- 挂 SVG 的 innerHTML 按字符串复用同一个 `{__html}`（`useHtmlMarkup`，否则松手弹回原位）
- 样式预览是白名单且与 `applyStyleEdit` 共用 `styleTargets`
- `'none'` 策略仍写 `wantPatches`
- `reattachPreview` 只在 DOM 真被换过时重放、认真正挂进 DOM 的那一版，几何交互同样只认已挂上的那一版（`useDisplayedExactManifest`）
- 换一版 SVG 先解码它嵌的位图（`useDecodedSvg`）
- 缩放预览只给图例（`previewScale`）
- 「渲染中」普通重渲染过 700ms 才亮、只有 `cold` 算冷启动
