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
  重算掉。空画布的起步提示按 `lib/emptyStateAnchor` 落在**纸面可见部分**的中心并
  钳进视口，永远不出屏。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 剪贴板主路径是原生 ClipboardEvent
- 缩略图只有一份组件、画的是当前素材
- 类型切换一次 commit 不换 id、`KIND_FIELDS` 编译期完整
- 空态一屏只有一个行动
- 「适应画布」是模式、直接操纵即退出、模式按画布各自记
