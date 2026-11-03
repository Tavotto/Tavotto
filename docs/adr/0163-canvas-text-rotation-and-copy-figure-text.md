# ADR 0163：画布文字标注自由旋转，与「图内文字复制为标注」「横排 / 竖排」

日期：2026-11-03 · 状态：**Accepted（旋转、图内文字复制为标注、横排 / 竖排）**（维护者 2026-11-03 拍板并给 1.0 豁免：「给，三项都做」）
相关：[0032 typography 能力层](0032-typography-capability-layer.md)、[0060 字体政策](0060-font-policy-and-allowlist.md)、[0065 合成](0065-imported-page-composition.md)、[0072 RenderCore](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、
[0152 所见即所得文字编辑框](0152-wysiwyg-text-editor.md)；字体同原图是另一个 PR 的 ADR 0164（本文不重复）。

## 背景

画布上的文字标注（`TextObject`）一直只能横排不转。为了让「图内文字复制为标注」（y 轴标签是 90° 的）和「竖排」有地方落脚，
标注先要能转任意角度。

**已有的模型**：`ObjectBase.rotationDeg`（文字 / 箭头 / 形状通用，面板不带——面板走 90° 步进的 `rotation`）早已存在：
覆盖层框与手柄绕中心转（`spinOf`）、缩放手柄的屏幕位移反旋转（`anchoredRect`）、命中走 DOM 的 CSS rotate、
导出载荷 `rotation_deg`、RenderCore `Group.transform`、`visualBounds`、属性页「旋转」输入都在。所以本 ADR 不新增字段，
**沿用 `rotationDeg`**（任务书曾设想新字段 `rotation`；同一个量起两个名字只会让两处读写分叉，磁盘格式本来就不变）。

缺的是：用户没有直接在画布上转的手段；转过的对象在**对齐 / 吸附 / 选区框**里仍按未旋转盒算；取值规范化不统一
（属性页存 [0, 360)，没有统一的写法）。

## 决定（旋转）

1. **模型**：`rotationDeg?: number`，缺席 = 0 = 旧文档逐字不变，磁盘格式不升版；回到 0 **删字段**（与 `fontFamily` 同一纪律）。
   顺时针为正（CSS 语义）、绕**未旋转框**的中心；`x / y / w / h` 恒为**未旋转的框**。
   与面板 90° 步进的 `rotation` 的区别要写清：面板的 `x/y/w/h` 在转 90° / 270° 后是**旋转后的包围盒**（矢量置入必须填满目标矩形，
   非 90 倍数没有「填满」可言）；文字没有这个约束，存未旋转框才能让编辑框宽度、换行、对齐语义在任何角度下都不变，
   也不会在 30° 这类角度出现「包围盒宽度 ≠ 换行宽度」的反推。
2. **规范形**：落盘值一律 `normalizeRotationDeg`（`lib/geometry.ts`）：(-180, 180]、0.1° 精度；270 与 -90 只存 -90。
   属性页输入、旋转柄、`typography.applyValue` 三处都经它。旧文档里的 [0, 360) 值照读（几何等价），下次被改时才规范化。
3. **旋转柄**：选中**单个**未锁定、不在编辑态的文字标注时，选框顶边中点外 22px 出一枚圆点（短线连到选框），整组跟对象一起转。
   拖动：角度 = 起手角 + 指针相对中心的方位角增量（按下那一刻不跳）；⇧ 吸附到每 15°（吸最终角，不是增量）；
   一次手势一个事务、一条历史（「旋转对象」），`pointercancel` 丢弃。实现在 `canvas/rotateDrag.ts`，覆盖层只挂一个独立的 `<g>`，
   不碰缩放手柄那一块（与 ADR 0161 的拖柄改动并行时减少冲突）。本 PR 只给文字；形状 / 箭头的柄不在范围（它们的缩放与端点模型不同）。
4. **单行文字先收框再转**（维护者 2026-11-03 补拍）：不会换行的单行文字，在 0 → 非 0 的那一次旋转开始时，框先收到贴字宽——
   字在页面上的位置不动，按 `align` 保持锚点（左对齐锚左边、居中锚中线、右对齐锚右边），多余空白去掉，再绕**收后**的框中心转。
   会换行的文字（正文含换行，或自然宽度已超过框宽、此刻正在折行）不收，保持原框宽。收框与旋转在**同一个事务**里（一条历史
   「旋转对象」）；拖回 0 或取消手势时框宽连同角度一起还原，属性页输入角度同样处理（`TransformSection`）。
   量法不自造第二套：就在画布上那份预览 DOM（`data-text-body`）里量——临时 `width: max-content; white-space: pre` 读 border-box 宽
   （含内边距与描边）后同帧还原，预览与导出的一致性由既有看护保证；实现 `canvas/textFit.ts`。已经转过的文字不再收（只管 0 → 非 0）。
   **余量**：收后的框宽 = 预览量宽 × (1 + 4 %)（`FIT_MARGIN_RATIO`，防整行字宽的系统性偏窄——一行 60–80 个字符时 3 % 就是 1–2 em，定额兜不住）+ 0.5 em（`FIT_MARGIN_EM`，防标点 / 个别字落到别的脸的差）。预览按浏览器 CSS 字体栈排，导出按 RenderCore 的 Liberation / Noto 排；
   Times New Roman / Arial 与 Liberation 度量兼容，但机器上落到别的脸（Linux 无 TNR、中文落到 PingFang / Songti、标点宽度不同）时预览会窄一点，
   刚好贴字的框会让导出折成两行。仓库里没有导出同源的前端字宽表，同步取 RenderCore 的宽度需要异步请求、打断旋转手势，所以用字号比例的余量兜底——
   宁可框略宽，绝不让导出折行。看护：`test_fit_to_text_box_never_wraps_in_export`（真排：含中文、粗斜体、无衬线、两组 60–80 字符的长文字；预览模拟窄 3 % 仍一行，无余量的同框会折行，长文字只有定额余量也会折行）。
5. **对齐 / 吸附 / 选区框一律用旋转后的轴对齐包围盒**（`visualBounds`）：`applyAlign` / `actions.alignIn`（经共用的
   `alignOneVisual`）、分布与精确间距、拖动吸附的移动盒与候选线（`snapCandidates`）、多选联合框与浮动栏锚点、`selectionBounds`。
   写回时只**平移** x / y，不改 w / h；未旋转对象的 `visualBounds` 就是自己，结果与以前逐位相同。
   例外：「统一宽 / 高」（`samew` / `sameh`）比的是对象自身**未旋转**的 w / h（与属性页 W / H 一致），参照物取主选的 w / h 而非外接框；
   否则两个相同的 40×10 都转 90° 时等宽会把目标改成 10。
   旋转后缩放手柄仍不吸附（候选线是页面轴向的，旋转对象的可见边不平行于它们）——沿用既有取舍。
6. **导出与预览一致**：预览 = 按未旋转框排好版再绕框中心顺时针转 θ；`rendercore/plan.py` 把文字块包进
   `Group.transform = rotate_about(-θ, 中心)`（PDF y 向上，顺时针 = 负角）。看护用例以**未旋转导出的首字符原点**为参照，
   断言 30 / -30 / 90° 的首字符原点 == 参照点绕框中心顺时针转 θ、阅读方向 == (cosθ, sinθ)——自由角度与直角档是同一条公式，
   不许分叉。

## 决定（图内文字复制为标注）

维护者 2026-11-03 拍板（用户来信「修改图内内容的时候能否对某一元素复制粘贴」）。图内编辑里选中**文字类**元素，⌘C /
右键「复制为标注」、⌘V / 右键「粘贴为标注」，在画布上得到一个与原文字**页面上重合**的 `TextObject`（同样先向右下错 4 mm）。

1. **哪些元素**：manifest 里带 `text_block` 的——标题、轴标签、`ax.text` / `annotate` 的文字、图例文字（含标题）、**单个**刻度数字。
   刻度**组**（`ticks`）是一组标签、没有「一块文字」，曲线 / 色块 / 集合 / 箭头 / 图例整体都不是。判据只有一个：`figureTextCopy.isCopyableText`。
2. **数据来源**：新增 manifest 键 `text_block`（`engine/manifest.text_block`，加字段协议：老前端原样忽略，老后端没有这个键 = 不可复制）：
   `center`（文字块中心）、`w`（块宽）、`h`（块高）、`asc`（块顶到**第一行基线**）、`step`（多行相邻基线间距）、`text`、`math`、`size`、
   `color`、`weight`、`style`、`rotation`、`ha`，长度都是 figure 分数。为什么不读 `bbox` / `editable`：`bbox` 是转后的外接框，反推不出
   未转的块宽与基线；刻度标签自己的 `editable` 只有 `text`（字号 / 颜色 / 转角在刻度组上）；而基线位置要**对得上原图里画出来的那一行**，
   只有排版那一侧知道。块尺寸与基线用**matplotlib 公开 API** 的探针 `Text`（转角 0、左 / 基线对齐）量，不碰 `_get_layout`（3.11 起返回形状变了）。
   量不出（wrap、usetex、异常）= 键缺席。
3. **换算只在 `lib/figureTextCopy.ts`**（不判断、不发请求、不写文档）：
   * 字号：脚本 pt 经 `pagePtLens`（ADR 0082）换成页面 pt；颜色原样；粗 / 斜走 `weightIsBold` / `styleIsItalic`；水平对齐 = `ha`。
   * 文字：没有公式保持普通文本（旧标记里会被误读的写法按 `serializeRuns` 转义，`\$` 画成 `$`）；有 `$…$` 按 matplotlib 的开合规则切成
     文字段与公式段，写成 LaTeX 子集（ADR 0152）`textFormat: 'latex'`，文字段里的 `\ { } ~` 转义成字面。
   * 字体：`sourceFont = sourceFontFromElement(el)`（ADR 0164，原图那张脸）；`fontFamily` 取 `genericFamilyOf(face_src.family ?? face)`
     （与样式示例的 CSS 回退栈共用一份名字线索表）作回退族。
   * **旋转**：matplotlib 转角 θ（逆时针、y 向上）→ 内容坐标阅读方向 `(cosθ, −sinθ)` → 与面板自己的翻转 / 90° 旋转同一个变换
     `contentToPageVec` → `atan2` 得页面上的**顺时针**角 = `rotationDeg`（y 轴标签 θ = 90 → −90；面板再转 90° 就是 0）。面板被镜像（翻转一次）时
     页面上没有「镜像的文字」：取同一条基线上读得顺的方向，左 / 右对齐跟着对调。
   * **位置**：标注的框绕**自己的中心**转（A 的语义），所以先让**第一行基线**落在原文字的第一行基线上（竖直方向：标注基线在框内的位置
     `size × (行高/2 + K)`，`K = (sTypoAscender + sTypoDescender)/2` 是批准三族各一个常数，`tests/test_figure_text_copy_metrics.py` 从字体文件反推），
     再让文字块沿阅读方向与原文字块同一条边 / 同一条中线（水平方向由 `align` 决定）。框宽 = `textFit.fitWidthMm(块宽, 字号)`——与 A 的
     「旋转前收框」**同一个公式**（`FIT_MARGIN_*` 不另造；多行用块宽加同样余量）。多行时 `lineHeight` 取原文字的相邻基线间距。
4. **剪贴板走既有路径**：复制写的是现有对象剪贴板负载（系统剪贴板 JSON + 魔数）里的**已换算好的 TextObject**，所以跨标签页 / 跨排版粘贴、
   菜单与 ⌘V 与对象粘贴是同一条路（`consumePayload` → `materializePaste`，同画布向右下错开 `DUPLICATE_OFFSET_MM`——与 ⌘D 共用的常量，此前是两处写死的 4）。
   **分派**：`handleCopyEvent` 在图内编辑里选着元素（`elementPanelId` 且 `selectedGids` 非空）时复制的是元素，不是整张图；不能复制的元素也**消费**这次
   复制（放过去的话选区里那张整图会被复制走）。属性页的文字框在**画布上点选**带文字的元素时会抢焦点并全选、随后焦点还给页面，框里留下一段没有焦点的残留选区，浏览器把
   copy 事件派给那个文字框（ProseMirror 也会自己接走）——这种「没有焦点的输入控件里的选区」不算用户在复制的文字（`inIdleEditor`），
   此时复制的仍是元素；真有焦点的文字框 / 别处的真实文字选区照旧让位给原生复制。粘贴不限工作区：单图编辑里粘标注仍是单图编辑（没有第二张图）；在图内编辑里粘贴后**退出图内编辑**、
   选中新标注（与点中别的对象同一件事，否则属性页仍指着图内元素，看不到刚粘出来的东西）。一次粘贴一条历史，撤销 / 重做对称。
5. **几何权威**：读 bbox 之后要写文档 → 只认**权威 manifest**（`exactPanelManifest`，ADR 0017）；权威不在（正在重渲染）时按「还不能复制」处理，不拿旧清单算位置。
6. **不能复制时**：顶栏状态胶囊「{元素类别}还不能复制」（类别词取 `inspector:role.*`；认不出时 `status.elementNotCopyableGeneric`「这个元素还不能复制」，ⓘ），不用横条、不加别的说明；右键弹层里「复制为标注」置灰。
7. **右键入口**：图内元素的右键是 `QuickEdit` 里 `role="dialog"` 的元素弹层（含控件，不是 Radix 菜单），其中本来就有 `MenuButton` 形状的项（恢复 / 隐藏 / 全部属性）。
   「复制为标注 ⌘C」「粘贴为标注 ⌘V」两项加在同一处、同一个外壳，其余项不变，没有长出新的菜单体系。粘贴靠异步读剪贴板，WebKit / 没有 `readText` 的引擎上不摆
   （与对象右键菜单同一判据 `canPasteFromMenu`，用 ⌘V）；已授予读权限且剪贴板里没有本工具的对象时置灰（`peekClipboardHasObjects`：从不为了置灰去弹权限窗，
   不知道 = 可点）。文案走 i18n 中英两份；**不写**「粘出来的标注和原图没有关联」之类的说明（维护者明确不要）。

### 已知边界

* **图例文字：manifest 里的位置与导出 PDF 里画的位置有 1–2 pt 的既有偏差**（教程图实测，8 pt 字：`Blank` 约 1.0 pt，含公式的 `Catalyst` 约 1.9 pt；
  画布上图例文字的选框与字也有同样的错位）。标注复制的是 manifest 的位置、与它逐点吻合（偏差 ≤ 0.02 pt），所以与导出里**原图**的图例文字就有同样的偏差。
  标题 / 轴标签 / 刻度数字没有这个偏差（≤ 0.07 pt）。偏差出在 manifest 的图例几何（图例是 `offsetbox` 布局，原因未查清），不在复制路径，另案处理（issue #1667）。
* 预览（CSS，按 hhea）与导出（按 OS/2 typo）对基线的度量差约 0.09 em，标注对齐的是导出，画布预览里可能比原文字高低约 0.7 pt；既有差异，另案处理（issue #1669）。
* 前端没有字体度量，竖直方向的 `K` 常数按批准三族取；用户字体（`sourceFont`）的 typo 度量与批准族差在 0.02 em 内。
* 非镜像面板才保证左右对齐语义与页面一致（镜像时按「读得顺」取向，左右对调）。
* 复制的是**当前画出来的**文字（含尚未写回脚本的 override）；标注与原文字此后**没有关联**（改一边不影响另一边）。

## 横排 / 竖排（2026-11-03）

画布文字可切 **横排 / 竖排**：从上到下、列从右往左（CSS `writing-mode: vertical-rl`）。**中文、日文假名、全角标点字头朝上；
英文与数字侧躺**（顺时针转 90°，CSS `text-orientation: mixed`）；全角标点用竖排字形（`，。「」（）` 走 OpenType `vert` / `vrt2`）。
可与上面的自由旋转叠加：先竖排排版、再绕未旋转框中心整体转（第 6 条那一条公式，不分叉）。

1. **字段**：`TextObject.writingMode?: 'vertical'`，导出载荷 `writing_mode`。缺席 = 横排，磁盘格式不升版；回到横排**删字段**
   （`'horizontal'` 只是控件取值，不落盘）。走 typography 能力层：`TYPOGRAPHY_PROPS` 加 `writingMode`，支持表 / property path / 校验三张表一起改；
   图内文字不支持（`kind_unsupported`，混选两类时交集里没有它）。控件：属性页与浮动文字工具条各一个「横排 / 竖排」两段切换，值四档照旧
   （多选一横一竖 = mixed，两段都不选中）。
2. **框语义**：`x / y / w / h` 仍是**未旋转**的框。横排是「w 用户定（行长）、h 由内容撑」；竖排对称：**`h` 是行长**（列按它折行）、
   **`w` 由列数撑开**——画布上的 `TextView` 量出列宽写回（历史规则见下面「量完写回」），**右边缘不动**（第一列贴右边、往左长，x 同步挪，字不跳）。
   属性页里竖排文字的 H 可编辑、W 灰掉（「竖排的宽度随列数」），横排相反。切换排向时 w / h 对调、**左上角不动**（维护者 2026-11-03 确认；行长跟着走，首字大致仍在原处；
   绕中心对调会让长条框的上沿甩出页面，且单行标注切换后位置不可预期）。
3. **对齐**：`align` 左 / 中 / 右在竖排里解释为**每一列内的 上 / 中 / 下**（CSS 里 vertical-rl 的 line-left 就是上边，浏览器实测同）；
   列之间永远从右往左。
4. **旋转时的收框**（第 4 条的竖排版）：竖排**单行（一列）**第一次旋转时收的是**高度**（贴字高）：同一个 `fitSingleLineBox`（`textFit.ts` 扩展、
   不另写一套），同一对余量常量（预览量长 × 1.04 + 0.5 em），按 `align` 锚点（左 / 中 / 右 = 上 / 中 / 下）；收的是 `y / h`，不是 `x / w`。
   后端同源看护 `test_fit_to_text_box_never_wraps_in_export_vertical`（预览模拟窄 3 % 仍是一列，无余量折成多列）。
5. **预览与导出同源的机制**（先说清横排现状：**没有共享的折行实现**——预览是浏览器 CSS，导出是 `typography.break_lines`，两边各自实现同一份语义
   「贪心、CJK 逐字、拉丁按词、超宽单词逐字兜底」，靠 `textFit` 的余量与真 PDF 几何用例兜底）。竖排沿用，并**多补一道真浏览器对账**：
   - 折行算法**只有一份**：`break_lines` 多一个 `measure` 参数（沿「行」方向量一个单元的长度），竖排传入 `vertical.column_measure`
     （直立字取竖排进退、侧躺字取横排字宽、公式取盒宽）；传同一把横排的尺得到与横排逐行相同的结果（测试钉着）。
   - 直立 / 侧躺的判据 `vertical.UPRIGHT`（UAX #50 `Vertical_Orientation` 的核心区段：表意文字、假名、谚文、全角形式、CJK 符号标点、
     带圈数字及 `§©®±×÷` 等；区间外一律侧躺，与 CSS 对未登记字符的缺省一致）。
   - `tests/test_vertical_css_parity.py`：同样的批准字体文件、`TextView` 的竖排样式挂进真 Chromium，逐字量落在第几列，与 `vertical.layout` 逐列相比
     （没有 node / 浏览器时 skip 并说明；反证：改坏列长即红）。
   - 预览样式带 `text-spacing-trim: space-all`：Chrome 默认把相邻全角标点收成半宽（`，「`），RenderCore 不收，竖排预览关掉它两边才逐格一致。
     **已知差异（横排同样存在，不是本次引入）**：浏览器的行首 / 行尾禁则（kinsoku）RenderCore 不做；横排预览仍有 `text-spacing-trim` 收窄。
6. **导出（RenderCore）**：
   - **直立字**：HarfBuzz **TTB** 塑形（`HbFace.shape_vertical`，套 `vert` / `vrt2`，读 `vmtx` / `vhea` / `VORG`；Noto Sans SC 有竖排表，
     Liberation 与多数用户字体没有 → HarfBuzz **合成**竖排度量：进退 = 上升 − 下降、字形落在字宽中线，用户字体回退同此，不换脸、不报错）。
     一字一个单字形 `ShapedText`，笔位 = 列中线 x / 字格顶边 y 加上 HarfBuzz 已折好的竖排原点偏移。
   - **侧躺段**：仍按横排塑形（`shape_segment`，含分层 / 粗斜体变体），放进 `Group(rotate_about(-90°, 段起点))`（y 向上空间里 −90° = 顺时针）；
     基线取**中线基线**（em 方框竖直中点落在列中线上，与直立字对齐——CSS 竖排用的同一条基线）。**公式**同为侧躺整块（轮廓与文本层占位在同一个旋转 Group 里）。
   - 不改写入器：几何全是 IR 现成节点，PDF / SVG / EPS / 位图四个写入器不认识「竖排」。
   - **下划线**：每列一条竖线，位置 = 侧躺基线「下面」0.11 字号，即**列的左边**（与横排同一条公式；浏览器竖排下划线同在左边，实测）。背景 / 描边跟框走，不跟列走。
   - **文本层**：内容流按「列从右往左、列内从上到下」写出，按内容流读的读取器（PDFium、poppler `-raw`、MuPDF / pymupdf）得到用户原文顺序（直立字之间读出换行，
     是每字一个文字对象的代价）。**已知读取器限制**：PDFium 对整体转了 90° 的多行文字按几何重排（横排文字转 90° 同样，三行读回来是反的），poppler 的
     版面模式（不带 `-raw`）对竖排按几何拆行——这是读取器的启发式，写入的顺序是对的（竖排文字从 PDF 复制的问题单独跟踪：#1668）；没有引入 WMode=1 的竖排字体（Identity-V）是有意的，那要改写入器与四个后端。
   - **公式（预览）**：与导出同为侧躺整块。`MathFormula` 的竖排形态是一个「列宽 × 公式宽」的内联盒（占满一整列，沿列方向占的长度 = 导出 `VItem.length`，
     落点与浏览器怎么摆内联盒无关），SVG 绝对定位、绕左上角顺时针转 90°，基线在列中线左侧 `(上升 − 下降)/2 × 字号`（`lib/verticalFormula.ts` 纯函数，
     `lib/verticalMetrics.ts` 用 canvas `fontBoundingBox` 量侧躺字的基线）。已知差：浏览器按 hhea、导出按 OS/2 sTypo 定侧躺基线（Liberation 两组不同，横向 ≈ 1 pt @12 pt），
     侧躺的拉丁字本来就带这一差，公式跟同列的字走浏览器那条基线；沿列方向（列、位置、长度、公式后第一个字）与导出逐点相同。
7. **量完写回的历史规则（一套，全路径一致）**：画布文字有一边由渲染撑出来（横排的 `h`、竖排的 `w`），`TextView` 量完要写回；竖排的写回还会挪 `x`（右边缘不动）——
   `x` 不是再量一次就能推回来的值，写回留在历史之外会让撤销回不到操作前的位置（切竖排后撤销，`x` 停在量出来的值上，评审 P2）。规则（`canvas/measuredWriteback.ts`）：
   ① 手势事务开着 → `txnUpdate`，并进这条事务；② 否则，**最后一条历史是刚落下的**（≤ `FRESH_MS`，撤销露出来的旧条目不算）且补丁动过这个对象 → `amendLast`，
   并进那一条（切排向、改字、改字号 / 行距、改框，一视同仁；撤销 / 重做逐值往返）；③ 没有触发者（打开项目、字体 / 公式晚到、窗口缩放）→ `silent`，纯派生值。
   横排的 `h` 同走这条路；没有第二套写法。
8. **竖排的缩放柄**：文字只有「行长」一个自由度——横排是宽（东 / 西柄，`x / w` 变，`y / h` 还原），竖排是高（**北 / 南柄**，`y / h` 变，**`x / w` 不碰**：
   w 由列数撑开、x 随右边缘，都归 `TextView` 的写回，拖动重写会把量出来的值盖回按下时的）。`geometry.dirsFor` 与 `interactions.startResizeDrag` 同改；
   旋转后的竖排文字拖柄只叠加 `anchoredRect` 算出的 x 位移（相对上一帧），同样不覆盖量出的 x。
9. **不做**：样式复制 / 粘贴不带排向（它会改框语义）；缩略图 / 导出预览（`CanvasThumb`）里的竖排用 SVG `writing-mode` 近似画、不折行、与横排一样不旋转；
   竖排里的拉丁连字 / 竖中横（`text-combine-upright`）不做。

## 逃生口与默认

默认：只有用户拖了旋转柄或在属性页填了角度才有 `rotationDeg`，其余文档与导出逐字不变。逃生口：属性页角度填 0 / 拖回 0 即删字段。

## 看护

- 竖排：`tests/test_rendercore_vertical.py`（直立 / 侧躺判据、折行与列序、对齐、竖排字形、中英混排朝向与中线基线、粗斜体、旋转叠加、下划线、公式、用户字体缺 vmtx 回退、文本层顺序）、
  `tests/test_vertical_css_parity.py`（真 Chromium 折行对账）、`test_compose_annotations.py::test_fit_to_text_box_never_wraps_in_export_vertical`、
  `web/src/canvas/verticalText.test.tsx`（能力层 / 字段增删 / 撤销重做 / 控件 / 预览框语义 / 收框高度 / 载荷往返 / 量完写回三种处境 / 竖排拖柄方向与约束），
  `tests/test_vertical_css_parity.py` 另含含公式的竖排逐列对账（列、位置、占位、横向落点）；e2e `features.spec.ts` 竖排用例拖南柄后列长真的变长。

- `web/src/canvas/textFit.test.tsx`：单行收框（左 / 中 / 右字不动）、多行与折行不收、一条历史、取消与拖回 0 不留。
- `web/src/canvas/rotateKnob.test.tsx`：规范化 / 方位角 / 吸附换算；柄的位置与随转；拖动不跳、⇧ 吸 15°、-90 而非 270、回 0 删字段、
  锁定不响应、一次手势一条历史；对齐 / 吸附候选 / 分布取外接框；⌘D 与撤销重做保角。
- `tests/test_compose_annotations.py::test_rotated_text_lands_where_the_preview_puts_it`：导出与预览一致（30 / -30 / 90°）。
- 既有：`rotatedObjects.test.tsx`（手柄随转、缩放锚点）、`test_rotated_text_direction_is_css_clockwise`（90 / 270 方向）。
- 图内文字复制为标注：`web/src/lib/figureTextCopy.test.ts`（换算：横排 / y 轴标签 -90 / 面板转 90° / 水平翻转 / 裁剪 / 多行 / 30° / 字体族 / sourceFont / 粗斜 / 公式与转义）、
  `web/src/lib/figureTextClipboard.test.tsx`（⌘C 分派与曲线胶囊、权威缺席、粘贴一条历史与撤销重做、选中新标注并退出图内编辑、右键弹层两项与置灰）、
  `tests/test_manifest_text_block.py`（块中心 / 尺寸 / 第一行基线对任意转角——期望值取自 `va=baseline` 锚点与真实 artist 外接框两把独立的尺）、
  `tests/test_figure_text_copy_metrics.py`（前端基线常数 ↔ 批准字体的 OS/2 typo 度量）。
