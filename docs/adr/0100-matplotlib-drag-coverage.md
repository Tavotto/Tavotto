# ADR 0100：matplotlib 对象拖动全族排查——锚定框与插图可拖、落位钉得住、拖不动的说出为什么

日期：2026-09-27 · 状态：**Accepted**（实现随本 PR）
相关：[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)、
[0083 override 目标身份](0083-override-target-identity.md)、[0093 方向键微调](0093-arrow-key-nudge.md)
（本 ADR 排查的对象就是它收成一份的 `InFigureMove` / `inFigureMoveOf`）、ADR 0086（命中几何的
全族排查，#670：能不能点中是拖动的前提）、`docs/rules/frontend/hit-and-selection-geometry.md`、
`docs/rules/backend/override-application-and-replay.md`、`docs/rules/backend/axes-and-artist-families.md`

## 问题

用户反馈：经常有人说拖不动图里的对象，「包括 matplotlib 生成的各种框和图」。「拖不动」在用户嘴里
是三件不同的事，排查要把它们分开：

1. **没宣称可拖**：按下去只是选中，拖了什么都不发生，也没有一句话；
2. **宣称了、落不下去**：拖的时候跟手，松手之后弹回原处或落到别处（静默，最坏的一档）；
3. **落下去了、把别的东西挤动了 / 撤销回不去 / 重开不一样**。

## 排查方法

按 #670 的办法逐族量，两侧互相独立：

- **引擎侧**（`tests/test_drag_coverage.py` 的前身，排查脚本在 PR 正文）：30 张图、约 250 个
  元素，每个元素按前端 `inFigureMoveOf` 的同一套分派算出一次平移要写的 override（`position` /
  `endpoints_frac` / `drag_prop`），同一个热 worker 里应用，量 manifest 里**被移动的那个元素**的
  锚点 / 落位与写下的值之差（换成 mm）；再画一次（不漂）、撤销（回基线）、一次性 worker 全量重放
  （== 热态）。覆盖 Text / annotate 各坐标系 / 纯箭头 / 独立箭头 / 图例（轴内、轴外锚点、fig.legend、
  outside）/ AnchoredText / AnchoredSizeBar / AnchoredOffsetbox / AnnotationBbox（图片、文字）/
  ax.inset_axes / mpl_toolkits inset_axes / make_axes_locatable / ImageGrid / 全部 Patch 族（数据、轴、
  figure 三种 transform）/ axhspan·axvspan / table / 色条（fig.colorbar、插图色条、append_axes 色条）/
  suptitle·supxlabel·supylabel / subplots·gridspec·mosaic·SubFigure / constrained·tight / twinx /
  次坐标轴 / 极坐标 / 3D / 等比子图（饼图、aspect equal）/ 数据系列（曲线、散点、误差棒、箱线图、茎叶、
  imshow、等值线与 clabel）。
- **浏览器侧**（`web/e2e/drag-coverage.spec.ts`）：真 PanelView、真指针、真 matplotlib。鼠标走了
  (DX, DY) 像素，matplotlib 输出的那个 SVG 组也该走 (DX, DY)——拖动途中（预览）、松手成图之后（落点）、
  撤销之后各量一次，另量一个不相干的子图（不许被挤动），最后保存重开比热态。

## 排查结论（修前 → 修后）

| 家族 | 修前 | 修后 |
| --- | --- | --- |
| AnchoredText / AnchoredSizeBar / AnchoredOffsetbox | **不宣称可拖**（role `artist` 只开 visible / zorder），拖了没反应也没提示 | 可拖，锚点误差 0.000 mm；浏览器落点 ≤ 0.3 px |
| AnnotationBbox（图片 / 文字框） | **不宣称可拖** | 可拖（'offset pixels' 等随 dpi 漂的坐标系按设计不宣称） |
| `ax.inset_axes` 插图（含插图里的色条） | **不宣称可拖**（落位归定位器） | 可拖可缩，误差 ≤ 0.001 mm |
| mpl_toolkits `inset_axes`、`make_axes_locatable` 主图与色条、`ImageGrid` | **宣称了、松手弹回原处**（2.2–3.8 mm = 整段位移） | 误差 ≤ 0.006 mm |
| constrained 图上的图例（轴内 / 轴外锚点） | 落点差 1.2–4.5 mm，别的子图跳 1.7–2.1 mm，再画一次还在漂 | 0.000 mm，别的子图 0 |
| constrained 图上拖子图 | 别的子图跳（最多 7.8 mm） | 0 |
| constrained 图上拖色条 | **整张图画不出来**（`box_aspect` 为 0） | 误差 0.004 mm |
| constrained 图上撤销拖过的子图 / 色条 | 回不到原处（0.3 mm / 11 mm），热态 ≠ 重放 | 子图逐位回原处；色条残差 ≤ 0.06 mm |
| constrained 图上的形状 / 独立箭头 | 0.06–0.11 mm（预览与 manifest 两次排版之间漂） | 0.000–0.001 mm |
| 文字 / 标题 / 轴标签 / sup* / 注释（非像素坐标）/ 纯箭头 / 图例 / Patch 全族 / 子图（各种布局）/ twinx / 极坐标 / 3D 子图 / 色条 / 等比子图 | 已可拖、误差 ≤ 0.008 mm | 不变 |
| 数据系列、标注箭头、图例项、刻度、3D 轴标题、次坐标轴 / 寄生轴、像素坐标的注释 | 按设计不拖，**拖了什么都不说** | 仍不拖；拖起来 toast 说为什么（见下） |
| SubFigure 里的子图 | 落点只走一半（2.3 mm） | 已修（见「修订（2026-11-13）」）：误差 ≤ 1e-6 图幅分数 |
| `ax.table` | 不宣称可拖 | **已修（2026-11-18 修订，见文末）** |
| `fig.add_artist(...)` 加在图幅上的框 | 不登记（点不中） | **已修（2026-11-12 修订，见文末）** |

## 裁决

### 一、锚定框与插框可拖：写法与文字同一套

`AnchoredOffsetbox` 一族与 `AnnotationBbox` 仍按 role `artist` 登记（前端不认识新角色），但宣称
`draggable`、给 `anchor`（框的左下角）与 `drag_prop = "pos_frac"`；manifest 与 setter 共用
`overrides.offsetbox_draggable` 一份判据（AnnotationBbox 按 `boxcoords` 查注释那张可逆表）。

拖过之后框**以左下角挂在写下的点上**（锚定框：lower left + borderpad 0 + figure 分数锚点；
AnnotationBbox：box_alignment (0, 0) + `xybox` 按自己的坐标系逆算）。不保留原挂点再平移锚框：
框的像素宽高随 dpi 不成比例地变（字形度量取整），预览按 72 dpi、manifest 按图的 dpi 成图，挂在
右上角的框左下角两边差 0.1–0.3 mm（本排查实测，变异 E10 钉着）。原样记在 artist 上，撤销放回。落位要算的都**先算完再动 artist**：坏值（外部改坏的项目里长度不对的
`pos_frac`）抛在半路的话，apply 把它收成 warning、不记进 applied，之后的空列表还不回来。

`AnchoredOffsetbox.draw` 不开 SVG 组，前端的乐观预览找不到它的节点（框不跟手、松手才跳）。
`manifest._svg_group` 给它包一层实例级 draw：只在 SVG 里多一个 `<g id=gid>`，别的后端的
`open_group` 是空操作，像素一个不变。

### 二、靠定位器落位的轴：落 position 时摘下定位器

`_set_axes_position` 把 axes locator 摘下来（色条轴摘 `_ColorbarAxesLocator` 包着的那个），原定位器
记在 artist 上、撤销放回。于是插图从 `position_locked` 里放出来（只剩次坐标轴与寄生轴锁着——前者是
「贴在父轴哪一边」的语义，后者由宿主 draw 代画），而 mpl_toolkits 插图、`make_axes_locatable`、
`ImageGrid` 不再「宣称了、弹回去」。挪过的插图从此钉在图幅上：manifest 给插图带 `inset_of`
（宿主 gid），前端拖宿主时由 `axesCompanions` 带着**挪过的**插图写同样的位移（SVG 里嵌在宿主的
`<g>` 里，不单独预览）；没挪过的照旧由定位器带着走、不多写一条。插图里还能套插图：沿 `inset_of`
一路走完，走到的每一层里挪过的插图、插图里挪过的标题、插图自己的随行色条轴都一起写。

### 三、布局引擎下拖过的东西不许挤动排版：一张 pin 表

GEO-B2（ADR 0093 之前，文字）已经立了规矩：**排版的输入必须与「没拖过」逐位相同**——排版前把拖过
的放回脚本原样，引擎照常算，排完再落回写下的位置。本 ADR 把同一张表（`_text_pins`，名字沿用）扩到
锚定框、图例、形状、独立箭头与 **constrained 图上的子图**，放回 / 落回只在 `_pin_put_native` /
`_pin_place` 一处分派：

- constrained 的子图：`set_position` 会把轴踢出排版（matplotlib：「外部调用的不参与排版」），整张图
  按「少了一个子图」重排，拖 A、B 跳；色条轴更糟，`reposition_colorbar` 按解开后的长宽比 0 装回去，
  下一步 `apply_aspect` 当场抛。放回原样 = 参与排版 + 色条长宽比；落回 = `set_position` + 再解开。
  撤销还要把 `set_position` 顺手改掉的 `in_layout` 还回去，否则子图从此不参与排版、与重放差 0.3 mm；
- 子图先落，其余按**收过长宽比之后**的子图框换算：排版之后、画之前 `Axes.draw` 还会按 equal 长宽比
  （imshow、饼图）把框收一次，按 transAxes 换算的图例若在收之前算就被带走（带位图的子图上 4 mm）。
  hook 里先替 draw 做这一次 `apply_aspect`，draw 里那次输入相同、结果逐位相同。

tight 布局下子图仍由 `PinnedTightLayoutEngine`（ADR 0042）钉住，不进这张表。

### 四、拖不动的说出为什么

`inFigureMoveOf` 回 null 的元素：点一下照常只是选中；**拖起来**（过了 `trackPointer` 阈值）才 toast
一句为什么，一次手势只说一次，不写项目、不进历史、不占 `interactionStore.kind`。理由只有
`inFigureImmovableReason` 一处，紧挨着 `inFigureMoveOf`、只读 manifest 的 role：

| 理由 | 角色 | 说什么 |
| --- | --- | --- |
| `series` | 曲线、散点、柱、误差棒、茎叶、填充、等值线 / 集合、线组 | 位置由数据决定；要挪整个子图，拖子图空白处或在元素树里选中子图 |
| `annotationArrow` | 带文字的标注的箭头（manifest `arrow_of` 点名它所属的文字；给不出端点的独立箭头、坐标系逆算不回去的纯箭头注释没有它，归 `unsupported`） | 尖指向数据点、尾巴跟着文字走：拖文字 |
| `legendEntry` | 图例项 | 跟着图例走：拖图例本身 |
| `ticks` | 刻度组 / 单个刻度 | 跟着刻度走；刻度在属性页改 |
| `axisLabel3d` | 3D 轴标题 | 位置由视角决定；属性页调它离轴的距离 |
| `pixelCoords` | 不可拖的文字（像素 / 字号单位的注释） | 换分辨率导出会漂，暂不支持 |
| `hostPlaced` | 次坐标轴、寄生轴 | 位置由宿主子图决定，拖宿主 |
| `unsupported` | 其余（表格、认不出来的 Artist） | 暂不支持拖动这类对象 |

方向键微调单选一个这样的元素时说同一句（`immovableMessage`）；多选、锁定、隐藏的仍是
「选中的图内元素不能移动」——那是用户自己的设置，不是「按设计」。光标不改：数据系列铺满子图，
悬停时一直显示「禁止」会比现在的十字光标更吵。

## 不做

- ~~**SubFigure 里的子图**~~：已修，见「修订（2026-11-13）」。
- **`ax.table`**：落位是 `loc` + `bbox`（轴分数），可以照锚定框的办法写一个 `pos_frac`；用户报告里没有
  出现，先说「暂不支持」。
- ~~**`fig.add_artist(...)` 加在图幅上的框 / 图片**~~：2026-11-12 已修，见文末修订。
- **mpl_toolkits 插图跟着宿主拖时的预览**：它不是 child_axes，SVG 里与宿主平级；没挪过的它由定位器
  带着宿主走（落点对），但拖宿主时预览里它不动、松手成图才跟上。
- **constrained 色条撤销的残差**（≤ 0.06 mm，修前 11 mm）：色条摆放读上一帧的装饰物包围盒，要多画
  几次才完全回到原处。
- **AnnotationBbox 的箭头在预览里跟着框平移**（组里含箭头），松手成图后箭头尖回到 `xy`。
- **拖标注的箭头 = 拖标注的文字**：语义可以成立，但「按着箭头拖、动的是另一头的字」需要设计裁决；
  现在按设计不拖、说清楚。

## 看护

- `tests/test_drag_coverage.py`：锚定框五种 × 有无 constrained 落在写下的锚点、再画不漂、撤销逐位回
  基线、子图不动；像素坐标的 AnnotationBbox 不宣称、硬写给 warning；锚定框有自己的 SVG 组；四种
  定位器落位的轴钉得住、撤销放回；插图点名宿主；constrained 下图例（轴内 / 轴外锚点 + 位图子图）、形状、
  独立箭头落点准、子图不动；constrained 下拖子图 / 色条别的子图不跳、撤销回原处；锚定框 / 插图 /
  constrained 三张图热态 == 一次性 worker 全量重放。每条都跑过变异（PR 正文 E1–E11）。
- `web/src/canvas/dragCoverage.test.tsx`：拖曲线不写项目、说出理由且只说一次，点一下不出声；八档理由
  的角色映射；锚定框走同一份 `inFigureMoveOf`；挪过的插图跟宿主（单拖与整组平移）、没挪过的不多写。
  `canvas/arrowNudge.test.tsx`：方向键单选曲线说同一句理由。变异 F1–F5。
- `web/e2e/drag-coverage.spec.ts`：真浏览器里 AnchoredText、AnnotationBbox、插图、constrained 色条、
  圆角框、图例各拖一次：预览跟手、落点、撤销、别的子图不动，误差预算 0.75 px；拖曲线 toast 说出理由、
  零渲染请求；方向键推锚定框、选中曲线按方向键说理由；保存重开 == 热态。
- 已有的 `test_text_drag_anchor.py`、`test_layout_engine_pinning.py`、`test_colorbar_resize.py`、
  `test_worker_roundtrip.py`、`test_figure_recognition.py` 里「插图不宣称 position」的三条断言随本 ADR
  改成正向断言（插图钉得住、代理宣称）。

## 修订（2026-11-12）：整张图 / SubFigure 上的元素全部登记

用户来图（假肢抓取实验组图）：`fig.add_artist(Polygon(..., transform=fig.transFigure))` 画的连接梯形
（把 B 的高亮窗口连到 D 的帧序列）选不中、改不了。排查时 artist 普查（`scripts/dev/matplotlib_artist_census.py
--api`，新增图级样本）在 main 上报出整张图这一层**全部**漏网：`Polygon` / `Rectangle` / `Line2D` /
`fig.add_artist(Text)` / 图级 `LineCollection` / `ConnectionPatch` / `fig.figimage` 的 `FigureImage`；SubFigure
里的 suptitle、文字、图例、形状同样一个都没登记，普查只报一行「SubFigure × N」。

裁决：

- **一处登记、递归进 SubFigure**（`manifest._register_figure_level`）：`texts` / `legends` / `patches` /
  `lines` / `images` / `artists`，`artists` 按族分派到与子图里同一套角色（Text → `text`、Patch → `patch`、
  FancyArrowPatch → `arrow_patch`、Line2D → `line`、Collection → `linecoll` / `collection`、位图 → `image`、
  锚定框照旧、认不出来的 → `artist`）。gid：根图沿用已发出的 `fig.texts_i` / `fig.legend_i` /
  `fig.artists_j`，新增 `fig.patches_j` / `fig.lines_j` / `fig.images_j`；SubFigure 是
  `fig.subfigs_k.<同上>`。序号是所属列表下标，存量 gid 一个不动。
- **图级的形状、线、文字可拖**：形状照旧 `pos_frac`（transform 平移）；图级 `Line2D` 新增
  `("line", "pos_frac")`，同一套平移（`overrides._set_line_pos_frac`，基准 transform、英寸平移、pin 表）。
  子图里的曲线仍不宣称可拖（位置是数据）。
- **量框**：图级集合没有 transData，`_collection_datalim` 改用它自己的 transform 量；`FigureImage` 按
  `get_extent()`（显示像素）量。量不出的照旧进 `unsupported`。
- **`ConnectionPatch` 不出端点**（子图里与图级都是）：它的两端由 xyA / xyB 决定，`_posA_posB` 是构造时的
  占位，从前端点手柄画在图幅对角、拖了不动。改为描真实箭杆（可选中、改样式），不宣称可拖。
- **SubFigure 里的图例拖动只走一半**：`legendmodel._legend_parent_transform` 对 SubFigure 返回了根图的
  `transFigure`，而图例的默认锚框是 SubFigure 自己的框；改用 `transSubfigure`（根图上两者相同）。
- 普查（产品 `manifest.census` 与开发工具）把 SubFigure 当结构件、走进去数它的孩子；SubFigure 的背景
  patch 与根图的 `fig.patch` 一样算结构件。

当时没做：**SubFigure 里子图的 `position`**（落点只走一半）——改口径会让存量文档挪位，属于既有项目
兼容的取舍，已单独修，见下「修订（2026-11-13）」。仍不做：`FigureImage` 只开 visible / zorder（`figimage` 的落位是像素偏移，
没有可重放的分数写法）。

看护：`tests/test_figure_level_artists.py`（登记、拖动落点 / 不漂 / 撤销、constrained、SubFigure、
样式改动、一次性 worker 全量重放）。

## 修订（2026-11-13）：SubFigure 里的子图按根图幅报、按根图幅落

用户拍板单独修（接 #1887）。修前 `axes.position` 直接取 `ax.get_position()`，SubFigure 里那是
**子图幅**的分数，而前端的位移、对齐、成组缩放、属性页的 mm 读数都按根图幅算：1×2 子图幅里的
子图拖 4.6 mm 只走 2.3 mm，右半边那格连起点都对不上。

- **一处换算**：`pathgeom.axes_rect_to_frame` / `axes_rect_to_figsize` 认 SubFigure，经
  `transSubfigure` ↔ 根 `transFigure` 换到显示坐标再换回来（嵌套 SubFigure、`bbox_inches` 的
  图幅平移都在这一来一回里抵消）。manifest 两处（2D / 3D 子图）、setter、色条翻转读 pending
  的宿主矩形都已经走这两个函数，所以口径一处改齐。
- **constrained 的 pin 记根图幅的值**：SubFigure 的框也是 constrained 排出来的，按排版之前的子图幅
  换算出来的 bounds 落下去会跟着子图幅偏，热态（上一帧已排好）与全量重放（还没排）分岔。pin 表里
  存写下的根图幅值，`_pin_place` 在排完之后再换算。
- **兼容代价（用户已接受）**：存量文档里拖过的 SubFigure 子图，存的是旧口径的值，重开后按新口径
  落，会整体挪位。override 没有语义版本号可以区分新旧值，不做迁移。普通子图（不在 SubFigure 里）
  两个函数逐位不变。
- 看护：`tests/test_subfigure_axes_position.py`（普通 / constrained / `bbox_inches="tight"` / 嵌套
  四种 SubFigure：`position` 与 `bbox` 同一矩形、拖到写下的位置、再画不漂、撤销回基线、全量重放
  == 热态）。修前 8 条红。

## 修订（2026-11-18）：子图里位置不是数据的线可拖

用户来图（世界地图上 30 条震中 → 标签的虚线折线引线）：引线是
`Line2D(..., transform=ax.transAxes)` + `ax.add_line`，带白色描边路径效果。它们进 `ax.lines`，
`instrument` 按「子图里的曲线位置是数据」一律 `draggable=False`：只能改颜色粗细，拖了只提示数据系列不能拖。
这是按**所在列表**推断能力，和 2026-11-12 修订之前图级元素的漏网是同一类问题。

裁决：

- **按 transform 判**（`manifest._layout_anchored_line`）。两条都满足才可拖：
  1. x、y 都不经过 `ax.transData`。沿 matplotlib 依赖图（`_parents`）找：`a + b`、
     `blended_transform_factory`、`TransformWrapper` 都会登记依赖。`axhline` / `axvline` /
     `get_[xy]axis_transform()` 有一边是数据，`transData + ScaledTranslation` 整条是数据，都不拖；
  2. 挂在认得的排版坐标系上（`transAxes` / `transSubfigure` / `transFigure` / `dpi_scale_trans`）。
     没登记依赖的自定义 transform 判不出来，按数据处理、不宣称可拖。
- 可拖的线与图级的线**同一套** `("line", "pos_frac")`：基准 transform、英寸平移、pin 表、原样 = 基准
  transform（`overrides._set_line_pos_frac` / `_restore_line_pos`，没有新机制）。前端只看
  `draggable` + `anchor`，不用改。
- 3D 子图与色条轴上的线照旧不拖。gid 不变，存量文档里对这些线的样式 override 照常生效。
- 落位是**整条平移**、钉在图幅上：之后挪子图，拖过的线留在写下的位置，与独立形状相同。逐顶点
  编辑（拖引线的拐点 / 一端）另议。

看护：`tests/test_axes_layout_lines_drag.py`（数据 / 一边数据 / 数据 + 平移 / 子图框 / 图幅 /
子图框 + 英寸八种线的判定；拖动落点、再画不漂、撤销回基线、不挤动子图与别的线；拖动与样式共存；
constrained；一次性 worker 全量重放 == 热态）。修前 3 条红。

## 修订（2026-11-18，续）：位置不是数据的集合与位图可拖

接上一条（线）。同一类问题还在集合与位图上：`ax.scatter(..., transform=ax.transAxes)` 手画的图例点、
`transAxes` 的线组 / 图形集合（`LineCollection` / `PatchCollection`）、`imshow(..., transform=ax.transAxes)`
贴在子图框里的图片、`fig.add_artist(LineCollection(..., transform=fig.transFigure))`，全部 `draggable=False`。
子图里的那几种**选中框还是整块子图**：`get_datalim(transData)` 对它们是空框，退回 tightbbox，点子图里
任何地方都选中它们。

裁决：

- **同一条判据**（`manifest._layout_anchored_artist`，与线共用 `_layout_anchored_transform`）：落位用的那条
  transform 是排版坐标才可拖。散点看 offset transform（路径 transform 是 marker 的像素尺，也不许经过数据）；
  线组 / 图形集合 / 位图看自己的 transform。`ContourSet` 不拖（`clabel` 文字是另外的元素，不会跟着走）；
  3D 与色条轴不拖；`FigureImage` 照旧不拖。图上的集合（没有 transData）也可拖——
  `test_figure_level_artists` 里 `fig.artists_3` 随之从不可拖改成可拖。
- **一把尺**：`pathgeom.layout_extent` 量落位（offsets 决定落位的量 offset 点，否则量路径顶点；位图用
  window extent），manifest 的 `anchor` 与 setter 都只用它。集合的 `get_window_extent()` 在 transform 不含
  transData 时是 ±inf，不能用。选中框用 `layout_ink_extent`：散点再算上 marker 自己的大小，单点散点
  （手画图例点最常见的写法）不至于零面积退回整块子图。
- **平移**：`overrides._set_translated_pos_frac`，与形状 / 线同一套（基准记在 `_mm_pos_bases`、英寸平移、
  pin 表、布局引擎排版前放回原样）；散点平移 offset transform，其余平移 artist transform。
  `("collection" | "linecoll" | "image", "pos_frac")` 三个键，导出修改后的 .py 列入不写（与线 / 形状同一结论）。
- **位图的预览组**：matplotlib 的 SVG 后端不给 `<image>` 写 gid，矢量后端还会把同一子图里相邻的位图合成一张、
  不调各自的 draw——拖动时前端找不到要平移的 `<g>`。可拖的位图包一层 SVG 组并退出合成
  （`manifest._own_svg_image`）：画面相同，矢量文件里是两张图而不是合成的一张；Agg（PNG）本来就不合成。
  可拖的位图的几何编辑不再代理回宿主子图（`geom_gid`）——它不铺满子图。

看护：`tests/test_axes_layout_collections_drag.py`（判定；初始位置等于按子图框独立算的值；拖动后**画出来的
几何**外接框 / 位图框整体走同一位移；再画不漂；撤销回基线；不挤动子图与别的元素；拖出子图框仍可选中；
与样式共存；constrained；全量重放 == 热态），浏览器腿 `web/e2e/drag-coverage.spec.ts` 加散点与位图两格。

## 修订（2026-11-18，续二）：`ax.table` 可拖

结论表里「`ax.table` 未修」那一行。表格每次 draw 按 `loc` / `bbox` 在子图框坐标里重排格子
（`Table._update_positions`），格子与字都画在表格自己的 transform 上（实测：平移叠在表格 transform 上，
格子框与格内文字走同一位移）。所以表格与位置不是数据的集合 / 位图同一套：判据
`manifest._layout_anchored_artist` 认 `Table`，平移 `overrides._set_translated_pos_frac`。表格仍是 `artist`
角色（存量文档的 visible / zorder 不动），`("artist", "pos_frac")` 按类型分派：表格走平移，锚定框 / 插框照旧。
`Table.get_window_extent` 量之前先排一次格子，所以全量重放（apply 在第一次 draw 之前）量到的与热态相同。
`loc="bottom"` 的表格参与 constrained 排版：布局引擎排版前放回原样，拖它不挤动子图。

看护：`tests/test_axes_table_drag.py`（判定；显式 `bbox` 的表格初始框 == 按子图框独立算的矩形；拖动落点、
再画不漂、撤销回基线、不挤动子图；constrained；全量重放 == 热态），浏览器腿 `drag-coverage.spec.ts` 加表格一格。

## 修订（2026-11-19）：引线逐段 / 逐点拖

用户拍板（2026-11-19）：引线（位置不是数据的线）要能「拖动某一段，当成自由线段一样拖」；数据曲线暂时不支持移动。
审阅页六处全按推荐：

- **拖一段 = 那一段两个端点同走一个位移**，相邻段跟着伸缩、整条线不断开。拖第一段 / 最后一段时连着震中的那一头也跟着走
  （与自由线段一致）；想留住起点，拖中间的段或拖拐点。
- **按下拖的是离指针最近的一段**（按内容像素量）；整条平移走方向键、与标签一起多选拖、对齐——都写同一条 override。
- **⇧ 锁水平 / 垂直**（看起手以来哪个方向走得多），直角引线拖完仍横平竖直。
- **拐点方块只在选中时出现**（7 px，手柄同色）；拖方块只动那一个点。
- **不加 / 删拐点**：顶点个数保持脚本原样。
- **超过 `LINE_POINTS_MAX`（64）个顶点的线不出拐点**，只整条平移；数据曲线仍不可拖。

实现：

- 引擎：可拖的线在 manifest 里出 `line_points`（每个顶点的 figure 分数、top-origin，与 `anchor` 同一套换算）。
  新 override `("line", "points_frac")`：**全部顶点**摊平 `[x0, y0, x1, y1, …]`，个数必须与脚本原样相同（不对就报 warning、
  什么都不动）。setter 先把线放回基准 transform（整条平移叠的英寸平移一并去掉），再按基准 transform 的逆把显示坐标换回线
  自己的坐标系写进数据；布局引擎下挂进 pin 表（与 `pos_frac` 同一张表、带 `points` 标记），排版前放回脚本原样的顶点、排完再落。
  原样 = （基准 transform, 脚本原样顶点）。`points_frac` 进 `_FRAC_ANCHORED`，每次重放。
- 一条线只写一种位置 override：前端对出 `line_points` 的线，整条平移（`inFigureMoveOf` → `lineMove`、`alignEntries`）也写
  `points_frac`；存量文档里 #2018 写下的 `pos_frac` 照常生效，之后再逐段编辑时 setter 把平移放回基准，热态与重放一致。
- 前端：`elementGeom.linePointsOf`（文档里写下的 `points_frac` 优先）；`interactions.startLineEdit`（段 / 点、⇧ 锁向、覆盖层
  虚线预览 `linePreview`、拖回原处不写）；`PanelView` 按下分派到最近一段；`OverlaySvg` 拐点手柄；拖子图时改过的引线随行
  （`FRAC_ANCHORED_PROPS` 认 `points_frac`，任意偶数长度按 x、y 交替平移）。导出修改后的 .py 不写 `points_frac`（与 `pos_frac` 同一结论）。

看护：`tests/test_layout_line_points.py`（只有短的排版线出顶点；初始顶点 == 按子图框独立算的位置；拖每一段落点与画出来的
几何一致；整条平移；图上的线；顶点个数不对报错不动；constrained；先整条平移再逐段编辑的全量重放 == 热态）、
`web/src/canvas/lineSegmentEditing.test.tsx`、浏览器腿 `web/e2e/line-segment-drag.spec.ts`（浅 / 深各一次：拖段落点、撤销）。

