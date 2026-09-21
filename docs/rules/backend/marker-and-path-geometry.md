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
  （直角网格抽稀后就是四个角，极坐标 / 翘曲网格是真实边界），`fill=True`、带
  `clip`，仍**不逐 cell 描**（22 万个 cell 就是 22 万条路径）。bbox 一个字节不动。
  前端消费规则见 `web/AGENTS.md`。看护 `tests/test_manifest_geometry.py`。
