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
| SubFigure 里的子图 | 落点只走一半（2.3 mm） | **未修**（见「不做」） |
| `ax.table` | 不宣称可拖 | **未修**：拖起来说「暂不支持」 |
| `fig.add_artist(...)` 加在图幅上的框 | 不登记（点不中） | **未修** |

## 裁决

### 一、锚定框与插框可拖：写法与文字同一套

`AnchoredOffsetbox` 一族与 `AnnotationBbox` 仍按 role `artist` 登记（前端不认识新角色），但宣称
`draggable`、给 `anchor`（框的左下角）与 `drag_prop = "pos_frac"`；manifest 与 setter 共用
`overrides.offsetbox_draggable` 一份判据（AnnotationBbox 按 `boxcoords` 查注释那张可逆表）。

拖过之后框**以左下角挂在写下的点上**（锚定框：lower left + borderpad 0 + figure 分数锚点；
AnnotationBbox：box_alignment (0, 0) + `xybox` 按自己的坐标系逆算）。不保留原挂点再平移锚框：
框的像素宽高随 dpi 不成比例地变（字形度量取整），预览按 72 dpi、manifest 按图的 dpi 成图，挂在
右上角的框左下角两边差 0.1–0.3 mm（本排查实测，变异 E10 钉着）。原样记在 artist 上，撤销放回。

`AnchoredOffsetbox.draw` 不开 SVG 组，前端的乐观预览找不到它的节点（框不跟手、松手才跳）。
`manifest._svg_group` 给它包一层实例级 draw：只在 SVG 里多一个 `<g id=gid>`，别的后端的
`open_group` 是空操作，像素一个不变。

### 二、靠定位器落位的轴：落 position 时摘下定位器

`_set_axes_position` 把 axes locator 摘下来（色条轴摘 `_ColorbarAxesLocator` 包着的那个），原定位器
记在 artist 上、撤销放回。于是插图从 `position_locked` 里放出来（只剩次坐标轴与寄生轴锁着——前者是
「贴在父轴哪一边」的语义，后者由宿主 draw 代画），而 mpl_toolkits 插图、`make_axes_locatable`、
`ImageGrid` 不再「宣称了、弹回去」。挪过的插图从此钉在图幅上：manifest 给插图带 `inset_of`
（宿主 gid），前端拖宿主时由 `axesCompanions` 带着**挪过的**插图写同样的位移（SVG 里嵌在宿主的
`<g>` 里，不单独预览）；没挪过的照旧由定位器带着走、不多写一条。

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
| `annotationArrow` | 带文字的标注的箭头 | 尖指向数据点、尾巴跟着文字走：拖文字 |
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

- **SubFigure 里的子图**：`position` 字段与 setter 用的是子图幅坐标，前端按根图幅分数算位移，落点
  只走一半。修法是 manifest 的 `position` 换成根图幅分数、setter 反算回子图幅；但这个字段同时喂
  对齐、成组缩放、属性页的 mm 读数，要单独改、单独验。SubFigure 里的文字 / 形状 / 箭头早已按根图幅
  算（`pathgeom.root_figure`），不受影响。
- **`ax.table`**：落位是 `loc` + `bbox`（轴分数），可以照锚定框的办法写一个 `pos_frac`；用户报告里没有
  出现，先说「暂不支持」。
- **`fig.add_artist(...)` 加在图幅上的框 / 图片**：`instrument` 不登记 `fig.artists` / `fig.patches`，
  点不中（属于命中登记，ADR 0086 那一侧）。登记之后 Patch 的 `pos_frac` setter 按 transFigure 就能用。
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
