# 画布对象、标注与工作区视口

> 原文出自 `web/AGENTS.md`「项目系统与多画布（前端侧）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **剪贴板（2026-08-17）**：⌘C/⌘V 的主路径是**原生 copy/paste ClipboardEvent**
  （`e.clipboardData` 同步读写，`lib/clipboard.ts` 的 handleCopyEvent /
  handlePasteEvent）——WebKit（Safari / 桌面壳）不给非编辑区的异步
  readText/writeText，跨标签页粘贴只有这条路全浏览器通。keydown 层不再拦
  ⌘C/⌘V；按钮触发的复制仍走 writeText（点击是用户手势）。e2e
  cross-tab-paste.spec.ts 看护。
- **默认画布名只有一个生成器** `types/document.defaultCanvasName(n)`（「Figure N」，
  2026-09-06 审计 T05）：空文档的第一张、新建、教程项目全走它。此前空文档叫
  「Fig 1」、新建的叫「Fig 2」、教程里的叫「Figure 1」，三种写法混在一行标签里。
- **「这是哪一张版」的缩略图全产品只有一份组件** `components/CanvasThumb.tsx`
  （2026-09-06 审计 T04 补做）：画布列表与**版本列表**共用它，喂进去的是
  `types/thumb.ts` 的 `ThumbObject`——内存里的 `CanvasObject` 与后端草图的公共
  最小形状（字段名逐字相同，两侧都不需要转换层）。它画的是**当前磁盘上的
  素材**，回答「哪一版」；「那一版长什么样」是版本详情里的 `LayoutSnapshot`
  （按 overrides 出图，出不来时明确标「近似预览」），两者不许互相冒充。
  「一张缩略图画几个对象 / 几个字」这两个数字**只在这个组件里**，版本列表把
  它们随请求发给后端（`/api/versions/<id>?sketch=&sketchText=`，后端的两个
  常量只是传输封顶）——写进 Python 就是同一条规则的第二份权威。
  **列表缩略图靠草图，不靠正文**：每条版本存的是整份文档，按行去取一次打开
  就是 120 份；草图是列表端点本来就已经解析出来的那份数据的投影
  （实测 24 MB 预算下 170 → 183 ms，`_version_sketch`）。
- **画布标签常驻图层**：每个打开的标签一个图层，非激活的用 canvases 快照渲染
  并 display:none——docToCanvas/canvasToDoc 共享同一 objects 数组引用 +
  ObjectView memo，切换标签 = 纯 CSS 显隐，不重建 DOM / 不重新解码图片。
- **标注**：任意角度 `rotationDeg`（面板除外；导出经 RenderCore 的 `Group.transform`
  绕中心旋转，CSS 顺时针在 PDF y 向上空间里是负角——`rendercore/plan.py` 的换算与
  旋转方向看护用例）；形状
  triangle/diamond/polygon/brace + 圆角/
  虚线/填充透明度；箭头 headStart/headEnd（triangle/open/bar，旧 head 字段
  兼容推导）；文字下划线/行距/内边距/背景/描边。**前后端几何公式同源**
  （shapeGeometry.ts ↔ `rendercore/geometry.py` `polygon_points`/`dash_pattern`，共享向量
  `tests/golden/shape_geometry_vectors.json` 两侧各跑一遍），改一边必须同步另一边，
  pytest 从合成的 PDF 内容流抽坐标做几何级看护（`tests/test_compose_arrow.py`）。
  科研预设在 `lib/presets.ts`（纯既有对象组合）。
- **画布标注的类型切换（2026-09-07，cap-shape-switch）**：矩形 ↔ 椭圆 ↔ 其它形状、
  直线 ↔ 箭头。「能不能切 / 能切成什么 / 切完长什么样」的唯一出处是
  `lib/shapeSwitch.ts`（纯函数），写入是 `store/actions.switchObjectKind`
  ——**一次 commit、一条历史、不换对象 id**（选择 / 成组 / 布局组 / 锁定全靠它）、
  数组位置不动（数组序即 z 序）。两个入口共用这一组函数、各自不许再判一遍：
  属性栏对象标题那颗类型徽标兼作切换（`inspector/ObjectKindSwitch.tsx`），
  右键菜单的「更改为 ›」（ADR 0037 的 2026-09-07 修订）。
  **只在族内互换**：`box`（矩形 / 椭圆 / 三角形 / 菱形 / 多边形 / 大括号）与
  `linear`（直线 / 箭头）——族内几何一个字不动，跨族等于替用户重画一个。文字与
  面板不参与，多选**全部同族**才给且作用于全部。字段去留只有一条判据「目标类型
  会不会读它」（`sides` 只有 polygon 读、`cornerRadius` 只有 rect 读、`head*` 只有
  arrow 读），删掉的值由撤销负责逐字段还回来。`KIND_FIELDS` 的完整性是**编译期**
  断言，不靠人记得回来改：给 `ShapeObject` / `ArrowObject` 加字段而没归类，
  `shapeSwitch.ts` 当场编译不过。磁盘格式不升版。
  导出侧是**生产者 / 消费者共读一份向量**（`tests/golden/shape_switch_payloads.json`）：
  前端 `shapeSwitch.golden.test.ts` 断言产出它，`tests/test_compose_switched_shapes.py`
  断言 pdfbackend 画得出它，**两侧都不重新实现对方那一半**。
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
- **空状态**：一律用 `components/ui/EmptyState`（图标+短标题+≤1 句+≤1 动作
  +≤1 条**次级文字链接**）。次级那一条只给「起步」空态用（画布空时的
  「试用示例」，走 `runTutorialEntry` 统一入口），画成裸文字链接而不是第二颗
  按钮——一屏只有一个看起来像行动的东西。**工作台的起步屏一共只有一个动作**
  （画布中央的「添加图」）：图层树 / 元素树 / 素材库 / 检查器的空态一律是
  轻量占位，一颗按钮都不配（审计 T03）。
- **项目就位后按文档页面适配一次视口（2026-09-06，审计 T03）**：这一次挂在
  「项目就位」上（`adoptOpenedProject`），与舞台挂没挂载无关——`CanvasStage`
  自己那次只在首次挂载时跑，而顶栏项目切换器**不经过 `phase: 'none'`**，工作台
  整个不卸载，新项目于是沿用上一个项目的缩放。
- **「适应画布」是一种模式，不是一次性动作（2026-09-13，审计 B01 / B17 / B57）**：
  `viewportStore.fitted` 为真表示视口显示的就是最近一次 `fit` / `fitAnimated` 算出来
  的落点（取景框记在模块级 `lastFit`）。**只在这个模式下**舞台尺寸变化——第一次量到
  尺寸、侧栏开合、窗口缩放——会按同一个取景框重算（`setViewRect` 里那一条）；首次
  打开的那次适配是在侧栏展开之前算的，不重算就是审计里 194% 装不下画布、空画布
  提示被挤到右缘那一幕。任何直接操纵（平移 / 缩放 / 定位 / 还原）各自
  `leaveFitMode()` 退出模式，之后尺寸再变一位都不碰——视口是用户的。**模式是每张
  画布各自的**：`canvasSession` 的会话记 `fitted`，切回来时是 → 按此刻的舞台重新
  `fit`，否 → `setView` 瞬时落回并退出模式；直写 `zoom / pan` 会把上一张画布的
  `fitted` / `lastFit` 原样留下，下一次侧栏开合就按别的画布的取景框把还原出来的视口
  重算掉。**取景算法只有 `fitTarget` 一处，所有入口（⌘1 / 打开画布 / 换页面尺寸 / 加图取景）都走它**；
  画布底部的浮动工具条显示时，下边距不小于 `TOOLBAR_FIT_CLEARANCE`（取景框底边落在工具条顶边
  之上，#770 评审 P2；`setFitBottomClear` 由 `CanvasToolbar` 随它的显示判据设，隐藏即 0、回到
  上下对称留白，适应模式里变化会按同一取景框重算）。空画布的起步提示按 `lib/emptyStateAnchor` 落在**纸面可见部分**的中心并
  钳进视口，永远不出屏。**e2e 量取景几何要等补间落定**：`viewportStore.tweening` 挂在
  舞台的 `data-world-transform` 上（`data-view-tweening`），等它消失再量；补间途中 zoom /
  pan 与刚变的页面尺寸对不上（页面尺寸是瞬间变的），量出来的「居中」是半路上的值，
  「比例已过阈值」这类条件可能被上一步的取景提前满足（#720 的 posix-e2e 偶发红）。
- **换画布尺寸就重新取景；新加的图软上限缩放；页面外画淡（2026-09-28，用户反馈）**：
  同一份文档、同一张画布的 `page.w/h` 一变（预设、手填、横竖对调、样式预设带的页面、
  以及它们的撤销 / 重做）→ `store/pageFit.startPageSizeFit` 按新页面 `fitAnimated`，
  **不看**是否在适应模式——页面尺寸是用户对「这张画布是什么」的显式决定，旧视口已经
  对不上新页面。切标签、换文档 / 载入 / 切项目、快速编辑各有自己的适配点，订阅一律
  跳过，不抢着写视口。加图的尺寸与位置只有 `lib/panelPlacement.placePanelInPage` 一处
  （`addPanel` / `addRuntimePanel` 共用）：原图相对页面可用区域（扣安全边距）的倍数经
  `softCap` 平滑压到最多 `OVERSIZE_CAP`（1.15，用户拍板）倍，比页面小的保持 100%；
  **不用阈值**——「130% 以内不缩、以上缩进页面」在阈值处必然跳变（大一点的原图放上去
  反而小一截），单调 + 连续 + 「稍大不缩、很大不超出多少」只能是一条逼近上限的曲线。
  **新图自动避开已有对象（2026-09-30，设计稿 C9）**：不是拖放落点时，`placePanelInPage`
  收当前画布上所有没隐藏对象的包围盒（`lib/geometry.visualBounds`：text / arrow / shape 的
  `rotationDeg` 转出外接矩形，面板的盒本身已是旋转后的），先在安全边距内找空位——候选是
  「某个已有对象的右边（同顶）」与「它的下方（贴左边距 / 同列）」，按阅读顺序取第一个装得下
  的（一行放得下排右边、放不下换到下一行），与已有对象、与彼此之间留 `PLACE_GAP`（3 mm）；
  右边与下方都不行才退到同行左侧 / 页面左上；**都放不下、页面空着、或图本身比页面大**
  退回下面的旧行为（居中 + 钳进页面）。**有拖放落点不避让**——那是用户在屏幕上挑的位置。
  判据只在 `placePanelInPage` 一处，`addPanel` / `addRuntimePanel` 两个 action 各传一次
  `occupiedBoxes()`，于是素材栏 / 快编栏 / 教程（`addFigureToLayout`）、选图对话框、脚本库、
  接入状态对话框、`tavotto run` 交接（`addPanelToCanvas` / `addRuntimePanelToCanvas`）全经
  同一处；新增加图入口不许自己算位置。看护 `lib/panelPlacement.test.ts`（算法边界）、
  `store/addPanelAvoid.test.ts`（入口一侧）。
  装得下的那一维钳进页面，比页面大的那一维在页面上居中、两边均匀伸出。这推翻了此前
  「按原始尺寸放、比页面宽也不缩」的做法；缩小后的等效字号由问题面板照常报，不在放置时
  另判。伸出去的那截由 `canvas/PageOutsideMask` 画淡（导出时 PDF 页框本来就裁掉它），
  页面轮廓压在内容之上——参考可画「页面即蒙版」，但只画淡不隐藏、不吃指针事件。遮罩是
  屏幕空间的四条 div 色带，**不用 svg**（e2e 有「舞台里第一个 svg / img」的等渲染定位）。
  **页面轮廓只有这一圈**（2026-10-07 设计审计 §10.1）：`PageSheet` 不再在世界层里画 outline（它随缩放变粗、
  与遮罩那圈画两遍）。从素材库拖图进来时舞台上有落点预览框（`CanvasStage` 的 `data-drop-ghost`），框就是
  `placePanelInPage(原图尺寸, 页面, 指针)`——与松手后 `addPanel` 落的是同一个计算；被拖的素材 id 在 dragstart
  冒泡到 document 时记下（拖动中读不到 dataTransfer 的内容）。看护 `canvas/canvasContextMenu.test.tsx`。
  - **加图是一条分层链，本条是它的唯一权威**（`asset-library.md`、`web/AGENTS.md` 引用这里，
    #706 评审 P1）：
    1. `workspace.addFigureToLayout(figureId)`——按素材 id 加，**去重 / 聚焦**：已在文档里就
       只聚焦（`focused`），否则先回排版、再经第 2 层新建。素材库的「添加到画布」
       （Shift+Enter / 就近入口 / 看大图弹窗）、快编上下文栏、onboarding 走这一层；
    2. `workspace.addPanelToCanvas` / `addRuntimePanelToCanvas`——拿着 `PanelInfo` / 描述符
       直接新建一张并**取景**（`frameAddedPanel`）。自己判过「文档里有没有」或本来就是「再摆
       一张」的入口走这一层：选图对话框（已有就选中）、脚本库与接入状态对话框的 runtime
       卡、接入状态对话框的磁盘图、`tavotto run` 交接（已有就选中）、舞台拖放；
    3. `actions.addPanel` / `addRuntimePanel`——**只改文档**、不碰视口。只许 `store/workspace.ts`
       调（第 2 层，以及 `openFastEdit` 需要先停放排版视口、再自己判取景的那一处），
       `store/addPanelEntry.test.ts` 按 TypeScript AST 钉住（追踪改名 / namespace 导入与
       转手再导出，只数真实调用；注释、字符串不算）。
    各入口走哪一层按它**原有**的去重语义定，别为了统一改行为。
  **新加的图怎么进视野只有一处判：`workspace.frameAddedPanel`**，输入是工作区模式与停放的
  排版视口（#706 评审 P2 四条都出在入口分散上）。
  排版上：取景「页面 ∪ 这张图」（`fitRectAnimated`）并**留在适应模式**（之后抽屉收起、
  窗口缩放页面仍居中）；拖放到一点、图整张已在视口里就不动视口。快速编辑里（对话框在快编
  时加图，或 `openFastEdit` 打开一张还不在画布上的图）：**不动**正在编辑的那一屏，只把新图
  记到停放的排版视口上（`openFastEdit` 先停放、再记，停放的永远是加图之前那一片，与有没有
  补间无关）。停放记录同时记停放时的页面尺寸；回排版时（`layoutViewOnReturn`）页面尺寸比
  **最终值**（快编里改了 W / H 又改回 / 撤销算没变）且新图都整张在停放那一片里 → 原样还原
  （审计 T01），否则取景「页面 ∪ 新图」（没有新图就按新页面，与 `startPageSizeFit` 同一
  落点）。`addFigureToLayout` 新建时先回排版再判，不 `revealRect`（那会退出适应模式）；只是
  聚焦已有面板时照旧 `revealRect`。这块非页面的取景框随画布会话走（`viewportStore.fitFrame()`
  → `canvasSession` 的 `fitFrame`，切回来 `fitRect` 瞬时还原）：会话只记 `fitted` 的话，
  切走再切回按页面重新适配，伸出页面那截被裁掉、之后窗口缩放也只按页面算；取景的就是
  页面时存 null，回来按那时的页面算。

## 2026-10-07 设计刷新（审计 §10.3）：缩略图画的是页面

`CanvasThumb` 的盒子是透明的，**画出来的是页面矩形本身**（纸白 + 1px `border-strong` 发丝线、圆角 4px，内容裁在页面里）：
横版与竖版的缩略图一眼可辨（此前白底与边框画在 svg 盒上，两种页面是同一个白框）。画布列表与版本列表仍共用这一份组件。
画布抽屉：「+」在标题行动作槽、计数在标题旁；行是 `listRowClass` lg（52）、`ui/RowMenu`、F2 改名 / ⌥↑↓ 排序 / 拖动落点线。
