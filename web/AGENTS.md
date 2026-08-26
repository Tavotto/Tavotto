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
（`latest` 表），否则每敲一个字画布都会闪回磁盘原图；④ 连续调整期间
**只给含 `role=="image"` 的面板**发 `preview_dpi: 100`，松手/结束事务由
`flushRender(panelId)` 按默认 dpi 定稿（纯矢量图上降 dpi 零收益，见基线补测）；
⑤ 编辑期每改一个值就多一条变体，`prune(live)` 按文档现存面板清理，
只留在用的与每个文件最近成功的那份；⑥ SSE 的 render.started/done 只带
fileId，写**文件级** `building` 表，绝不盖任何变体条目（盖了的话另一个
副本会永远转圈）。看护：`web/src/store/renderStore.test.ts`、
`web/src/hooks/useEngineSync.test.ts`、`tests/test_engine_variants.py`。

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
- **标注**：任意角度 `rotationDeg`（面板除外；导出走 PyMuPDF morph，
  CSS 顺时针 = Matrix(deg)）；形状 triangle/diamond/polygon/brace + 圆角/
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
