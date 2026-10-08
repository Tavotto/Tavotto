# 标记形状事实与路径几何

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **标记形状是只读事实，不是取值（2026-09-06，UI/UX 审计 T16 补做）**：
  marker 一族的 enum 字段（曲线 / 散点 / 茎叶 markerline / 图例示意标记）带一个
  `marker_current`，回答「图上此刻画的是什么形状」——而 `value` 回答的是
  「用户选中的是哪个取值」，**两件事不是一回事**：散点没被整体换过标记时
  `value` 是 `"original"`（继承脚本），曲线的 `value` 可能是 `(5, 1, 0)` /
  `$\alpha$` / 一个 Path 的 repr，界面上只剩一行认不出的字。它是**渲染态派生
  数据**：不进用户文档、不是 override、不参与写回，有 override 时发的就是
  override 之后的形状。四个消费者只有 `_marker_field()` 一处构造，加第五个
  也走它。
  * 认名字的判据是**顶点 + codes 逐个比对**（容差 1e-6），比的是
    `MarkerStyle(name).get_path().transformed(get_transform())`——`Axes.scatter`
    与 `overrides._set_scatter_marker` 造路径用的正是这一句，于是散点与曲线
    两条路给出同一个答案。**不按 `get_marker()` 的字面量认**：散点根本没有
    那个字面量（脚本写的 marker 在 `ax.scatter` 里当场就化成了路径）。
  * **只有 `_MARKER_SHAPE_NAMES` 那 13 个名字会以 `named` 发出去**——前端
    `MarkerPicker.markerShape()` 的那份 switch 逐个画得出它们。表外的
    （`H` / `8` / `P` / `X` / 元组 / mathtext / 自定义 Path）一律发归一化几何
    （单位框 `[-0.5, 0.5]`、y 向上、4 位小数，CLOSEPOLY 占位点不参与包围盒
    也不发真坐标）。两侧万一漂了**只会退回代码字样**，不会画错一个形状，
    所以这不是一条需要 golden 向量的同源对。
  * 顶点超过 `_MARKER_PATH_MAX_VERTS`（256，实测 mathtext 标记最贵的
    `$\int_0^\infty$` 是 132）只说 `too_complex`；一个 collection 里混着两种
    形状说 `multiple`，不拿第一条冒充全体；**字段整个缺席 = 引擎说不出**，
    与 `none`（这个对象没有标记）是两个不同的答案。
  * **override 之后「脚本原始」那一格说不出形状**（2026-09-07，cap-marker-orig
    补做）：`marker_current` 读的是图上此刻那条路径，脚本原来那条已经不在图上。
    同一个字段因此多发一个 `marker_original`——同一套五档结构，**唯一出处是
    `state.originals`**：override 系统在第一次应用**之前**采下的那份脚本原样
    （`apply()` 里 `state.originals[key] = getter(...)` 排在 `setter(...)` 之前），
    撤销时回灌的也是它。两边同一份值，「回到脚本原始 = 回到这个形状」这句话
    因此可兑现，不是另算一遍的巧合（`_marker_shape_of_spec()` 是此刻与原样
    共用的那一句 `MarkerStyle(...)`）。
  * **发不发的判据是 `state.applied` 里有这条 `(gid, prop)`，不是 `originals`
    里有**：① 广播型 prop 会替组员代采一份原样（`alias_seeded`，marker 经 stem
    系列就是广播）；② setter 抛异常时原样已经采下、`applied` 还没记——照
    `originals` 判的话，一次**失败**的 override 会让那一行从此显示「改过」，
    而图上一个像素都没变。**没有 override 时字段整个缺席**（缺席 = 与
    `marker_current` 相同），前端不用再判一次「改没改过」。原值的类型由
    `overrides.HANDLERS` 那侧的 getter 决定（曲线 / 图例示意是 marker 规格、
    散点是 Path 列表、茎叶是按成员列表的一份规格），四个消费者各自对着写。
    也因此 `_fields_for` 收的是 `(el, state)`：原样不在 artist 上，只有 state 里有。
  * 看护 `tests/test_manifest_marker_shape.py`。
- **路径几何 `geometry`（2026-08-18）**：manifest 给曲线 / fill_between /
  `ax.fill()` 的 Polygon / PathPatch 带上**真正画出来的那条路径**（figure 分数、
  y 向下，与 bbox 同一套），前端据此沿路径描边与命中——bbox 里绝大部分是空白，
  拿它画选择框会画出与图形对不上的矩形，拿它做命中会让用户在空白处误选。
  唯一实现 `engine/pathgeom.py`：`Path.cleaned()` 一次拿 numpy 数组（逐段迭代
  在两万点谱线上要 +550ms）、非仿射先 `transform_path_non_affine`、贝塞尔在
  display 空间细分、NaN 拆子路径、超长路径先按段取极值再 RDP（见
  docs/perf-baseline.md 的「路径几何」一节）。它是**渲染派生数据**：不进用户
  文档、不是 override、不参与写回，几何一变下一版自然就是新的。
  **散点（PathCollection）与只有 marker 没有连线的 Line2D 给的是每一颗 marker
  的轮廓**（2026-09-06，用户反馈「选中散点罩的是一个大矩形」及其延伸
  「`plot(..., ls="None", marker="o")` 画的散点也要逐颗描」）：两者共用
  `pathgeom._stamp_markers`，语义按 Agg `draw_path_collection` 走（`paths[i % Np]`
  × 逐戳记矩阵 `[i % Nt]` × `offsets[i % No]`），**形状只拍平一次**（逐颗
  `Path.cleaned()` 一颗 1.8ms，500 颗就是 0.9 秒），其余各颗是同一组顶点经
  `A_i ∘ A_max⁻¹` 的批量仿射，抽稀容差按尺度分档。散点侧 `_marker_subpaths`
  取 `get_paths()` × `get_transforms()` 的尺寸矩阵 × offsets；Line2D 侧
  `_line_marker_subpaths` 取 marker 的 `get_path()`/`get_alt_path()` ×
  `markersize·dpi/72`（`','` 不缩放）× `get_xydata()` 经 `get_transform()` 的落点
  （忽略 drawstyle、NaN 不出、`markevery` 交给 matplotlib 的 `_mark_every_path`；
  半填充 marker 每颗两条子路径）。标记数超过 `pathgeom.MAX_MARKERS`（500，
  **两种 artist 同一个数**）整组退回 bbox 并在 stderr 说明——上限量的是消费侧
  （每次渲染往返的 manifest JSON、前端每次指针移动沿全部线段算距离、覆盖层的
  d 串），不是生产侧；散点的 bbox 仍是**圆心**的包围盒（`get_datalim` 的口径），
  最边上的半颗 marker 伸在 bbox 外是正常的。**既有连线又有 marker 的 Line2D
  仍只描折线**：折线穿过每颗 marker 的中心、命中容差内每颗都点得中，而 geometry
  的 `fill` 是整份一个标志、前端把「闭合或 fill」的子路径都按面积算，实心 marker
  混进来会把那条折线一起变成多边形——要并存得先给 geometry 分层。**箭头不给**
  （它有 `arrow_endpoints` 那套契约，两套并存只会打架）。
  `ax.fill()` 的 Polygon 与 PathPatch 现在登记成 `axes_i.patches_j`（role=patch）。
  **柱形系列（`ax.bar()` 的 BarContainer 伪元素）逐根描柱**（2026-09-13，用户反馈
  「选中柱形系列罩的是一个把柱间空白也罩进去的大矩形」）：`pathgeom.patch_group_geometry`
  一根柱一条闭合子路径（Rectangle 的 `get_path()` + `get_transform()`，barh / 负高度 /
  对数轴同一条路），隐藏的不描，根数超过 `MAX_MARKERS` 整组退回 bbox（与散点同一个
  数、同一种降级）。命中 / 框选 / 描示前端一个字没改。
  **彩色网格（QuadMesh）描外轮廓 + 裁剪框**（2026-09-21，用户的 PRB 三联图：点子图
  背景时选中框比子图高出一截）：从前它什么都不给、退回 bbox，理由是「铺满一块矩形，
  bbox 本来就是准的」——**数据范围超出坐标轴范围时这条前提不成立**（bbox 是未裁剪的
  整块网格，`shading="nearest"` 还各向外垫半格），而用户看到的是被 axes 裁掉之后的
  那块。`pathgeom._quadmesh_outline_subpaths` 沿 `get_coordinates()` 的四条边绕一圈
  （直角网格抽稀后就是四个角，极坐标 / 翘曲网格是真实边界），**凸的外轮廓先裁进 axes 框再发**
  （`_clip_ring_to_rect`，Sutherland–Hodgman；凹的——U 形翘曲网格——与矩形的交可能不相连，
  S–H 会造沿裁剪边的假桥，所以原样发、由前端按 clip 裁）：前端框选按「框与边相交」判、填充内部刻意
  不算圈中，未裁的四条边全在子图之外时盖住整个子图的选择框也圈不中它；`fill=True`、
  `clip` 照发，仍**不逐 cell 描**（22 万个 cell 就是 22 万条路径）。`offsets` 按渲染器口径处理：
  一条（或全相同）的偏移经 `offset_transform` 加到轮廓上，多条不同的偏移让 cell 各奔东西、
  退回 bbox。bbox 一个字节不动。
  **误差棒 / 茎叶系列出成员几何的并、Patch 全家族描真实路径、注释文字框只是字**（2026-09-26，
  用户：选中误差棒时高亮贴不住、点这组选中那组；ADR 0086）：`pathgeom.series_group_geometry`
  让每个成员按自己那一族出几何（与单独登记时同一份 `element_geometry`）再合成一份，画着东西的成员
  给不出就整组退回 bbox；`fill` 只在没有多于两点的开放折线时成立（连线的数据线不能被当成多边形）。
  圆 / 椭圆 / 扇形 / 圆角框 / span 矩形与 Polygon 同一条 `get_path()` + `get_transform()`，只有阴影线的
  形状内部也算墨迹。带字注释的箭头（不出端点的那种）描真实箭杆、只按描边命中
  （`element_geometry(arrow_path=True)`）；注释文字的 bbox 不含箭头、带底框文字的 bbox 含底框
  （`manifest._text_ink_extent`）。
  前端消费规则见 `web/AGENTS.md`。看护 `tests/test_manifest_geometry.py`、`tests/test_series_shape_geometry.py`。
* **manifest 量文字用矢量输出的那把尺**（2026-09-25，#576，`manifest.vector_text_metrics`）：
  manifest 在文档 dpi（通常 100）的 Agg 渲染器上量，而画布挂的是矢量 SVG（字形经 `TextToPath`
  在 100 pt、不带 hinting 下度量）、导出的是 PDF。Agg 的度量带 hinting、按像素取整，小字差一圈
  （6.9 pt 的图例高 0.135 vs 0.121，figure 分数）——锚在预设位置的图例左下角因此报错，第一次
  拖动写成绝对位置就跳。所以**测量阶段**（布局 draw 之后的全部 `get_window_extent`）把 canvas
  那个 Agg 渲染器实例的 `get_text_width_height_descent` 换成 `TextToPath` 度量（按
  `points_to_pixels` 换算，usetex 不动），出 `build_manifest` 即撤。
  **布局那一次 draw 不换尺**：`constrained_layout` 的结果在 ulp 级依赖上一次 draw 留下的位置，
  Agg 的 26.6 定点度量把末位噪声吸收掉；布局也换成连续的矢量度量后，「上一张预览是 hybrid 还是
  纯矢量」会让 manifest 末位不同（`test_preview_hybrid` 的逐字节不变量在 3.10 上抓到；把矢量度量
  量化到 1/64 px 反而更糟）。图例位置在测量时现算（`OffsetBox.get_offset`），但自动布局仍可能
  在 SVG draw 时改变，最终预览须按下面的同源规则重测。图例子项的偏移是 draw 时写死的，`_layout_legends_for_measure` 在一次性
  渲染器上按同一把尺给**每个**图例补排版（隐藏图例 #413 的那条路扩到全部）。
  **度量缓存挂上 / 撤掉各清一次**：缓存键有渲染器实例、没有度量方式。两代实现都认：3.11 起每个
  渲染器一份（`_get_text_metrics_function(r).cache_clear()`），3.8 / 3.10 一份全局 lru
  （`_get_text_metrics_with_cache_impl`，只能整份清）。
  看护：`tests/test_manifest_vector_text_metrics.py`（以预览 SVG 里图例边框的路径坐标为独立一侧：
  manifest 框 == SVG 框、把报出的锚点写回图例不动；段内矢量 / 段外 Agg 的隔离）、
  `tests/test_hidden_legend_geometry.py`、`tests/test_preview_hybrid.py`（表示法不改 manifest）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 形状是只读派生事实不是取值，13 个名字之外发归一化几何
- 发不发看 `state.applied`
- geometry 是渲染派生数据不进文档
- 散点 / 纯 marker 线 / 柱逐个描，超过 `MAX_MARKERS` 整组退回 bbox
- 彩色网格只描外轮廓 + 裁剪框，不逐 cell
- 误差棒 / 茎叶出成员几何的并，一个成员给不出整组退回；Patch 全家族描路径；注释文字框不含箭头（ADR 0086）

- **预览几何与最终 SVG 同源（2026-10-01，柱顶数字首拖少走）**：初始 Agg 排版与
  随后的 SVG 排版可能因为自动刻度数量、字体度量而改变子图框。`figsession.render` 与
  `browser._render` 保留前置准备，但交付的 manifest 由同一份
  `manifest.capture_preview_manifest` 按最终 SVG draw 的 renderer / dpi 重测：临时
  rasterize 必须已经还原，只恢复测量坐标，不再 draw / 排版，不永久关掉自动布局。
  软闸升档时取最后一遍 draw，成功 / 异常都摘回调、还原 dpi 与 frame。
  最小命中厚度仍按文档像素换算，不能把 SVG 的 pt 当 px；会回写 setter 的 axes.position
  保留几何精度，不分别舍入宽高破坏固定长宽比（普通数值字段的显示精度不变）。
  看护 `tests/test_preview_layout_geometry.py`：直接读冷 build 的 SVG，比真实位移、
  改图幅后的图例框与坐标清单；不能先空渲染一次把首轮错位暖掉，也不能只比 patch 与 manifest。

- **PNG 编辑预览也必须像素与几何同源**（#779）：`preview_png(with_manifest=True)`
  返回同一次 Agg draw 的 PNG 与 manifest，使用该次 bucket DPI 和 Agg 文字度量；
  只取缩略图的旧 Path / Blob 接口不变。临时 patches 仍须还原，不能覆盖 canonical
  SVG manifest（MCP 的 replay / preflight 仍依赖它）。桌面、playground、MCP 三端
  都传递这个配对；前端等对应面板、变体、响应身份、URL 的图片加载后才授予几何权威，
  新 bucket 待解码、旧请求晚到、同 rev 的 worker 重建都不能混用。MCP 旧引擎缺少此
  能力时保留位图显示并给升级提示，不伪造 PNG 几何。看护 `tests/test_preview_png_geometry.py`
  的实际 PNG 像素、30 组布局 / bucket / frame、位移与还原，以及前端的图片加载用例。

- **被裁剪的 Patch 选中框只罩画出来的那部分（2026-10）**：柱子从 y=0 画起而 `ylim` 下限为正
  （或负值柱被 `ylim` 截断）时，`get_window_extent` 是未裁剪的整根柱，`bbox`（选中框 / 命中
  参考框）会伸出子图甚至图幅。`manifest._fold_into_clip` 把 Patch（柱、色块、多边形）与柱形
  系列成员的 bbox 折进 `_clip_extents`（与 `clip_bbox`、`geometry.clip` 同一个裁剪判据，
  `clip_on=False` 不裁）。整个元素落在裁剪框外时**不折**，bbox 仍指到数据位置（元素树仍可选，
  命中由 `geometry.clip` 挡住）。曲线 / 散点 / 集合刻意不折（扁平线垫宽、预检负对照靠未裁剪口径）。
  **系列并集不并入完全在外的成员**：`_fold_into_clip` 回 `(bb, folded, outside)`，柱形系列
  的并集 bbox 跳过 `outside` 的成员（子柱自己的 bbox 仍是数据位置）；全部成员都在外才退回
  数据位置并集。超过 `MAX_MARKERS` 的系列没有 geometry，前端只用这个并集，故同样收窄。
  看护 `tests/test_manifest_geometry.py` 的 `ClipBarFig` 四条与 `ClipSeriesFig` / `ClipOverCapFig` 三条。
