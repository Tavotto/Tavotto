# axes 遍历、特殊 artist 与 artist family 能力层

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **「这张图上有哪些 axes」只有 `axestraversal.ordered_axes` 一处**（看护
  `tests/test_axes_traversal_authority.py` 的源码级门禁）。它住在只依赖标准库 +
  matplotlib 的底层模块 `engine/axestraversal.py`，`manifest` 与 `overrides` 都从它取，
  谁也不反向 import 谁（2026-09-17 拆掉 manifest ↔ overrides 环时从 `manifest._ordered_axes`
  迁出，**只迁移一份、不复制**；按 artist family 继续切出来的模块——`engine/spinemodel.py`、`engine/tickmodel.py`、
  `engine/colorbarmodel.py`、`engine/legendmodel.py`——同样是叶子（族之间只许依赖 `FAMILIES` 里排在
  前面的族；`pathgeom` 是它们共同的基座，`frac_to_display` 住那里），配方门禁 `tests/test_engine_family_modules.py`，依赖清单
  `docs/architecture/figstate-dependencies.md`）。`fig.axes` 之外有
  **两族**：`ax.inset_axes()` / `ax.secondary_[xy]axis()` 挂在 `ax.child_axes`
  上；`mpl_toolkits.axes_grid1`（与 `axisartist`）的
  `host_subplot(...).twinx()` 挂在 `host.parasites` 上（**寄生轴**，#217——
  漏掉它时第二组数据整条不进 manifest，既列不出也改不了，而且不报错）。
  编号顺序是**契约不是实现细节**：`fig.axes` → 子 axes → 寄生轴，**寄生轴
  单独走第二趟**，这样改动前的那份 `axes_i` 序列永远是新序列的严格前缀，
  存量文档里的 gid 一个字节不动。寄生轴的**能力按实况判**：`position` 与
  `visible` 都是死开关（`HostAxesBase.draw` 每帧拿宿主 rect 调
  `apply_aspect`、代画孩子时从不看寄生轴自己的 visible），两条都不出字段、
  各带一个 reason code（`parasite_host_rect` / `parasite_host_draw`）；其余
  （数据范围、刻度、网格、边框、轴标签、曲线）照常给。看护
  `tests/test_parasite_axes.py` + 不变式夹具的 `InvPar` 一格。
- 特殊 artist：轴标签拖动走 `set_label_coords`（恢复时 transform 也要还原）；
  标题拖动要设 `ax._autotitlepos=False`；3D axes 暴露文字类元素 +
  position/visible（可拖动缩放；Axes3D 会按盒比例微调落位，以重建后
  manifest 的真实 bbox 为准）+ 视角（elev/azim/roll，setter 全量带三角避免
  view_init 重置）+ 轴线/背景面板/网格（x/y/z 三轴统一应用、按轴还原）+
  x/y/z 刻度组（TickSet/TickLabel 已泛化到 z；3D 不出 direction/visible）+
  投影方式 proj_type + **轴箭头**（axis_arrows 开关 + arrow_color/width/head；
  `_AxisArrow3D` 在 do_3d_projection 里用 axis3d 的私有几何助手
  `_get_coord_info`/`_get_axis_line_edge_points` 每帧现算落边，隐藏原生
  axis.line、箭头指向坐标增大端，视角旋转/换边自动跟随；matplotlib 升版
  破坏点由 test_axes3d_axis_arrows_roundtrip 看护）。
  盒内数据属性（spines/lim/scale）仍禁用。
- **图内箭头（FancyArrowPatch）**：脚本 `add_patch` 的独立箭头 manifest 带
  `arrow_endpoints`（figure 分数、y 向下），可整体拖动 / 拖单个端点
  （override `endpoints_frac`=[ax,ay,bx,by]，setter 经箭头自身 transform 逆变换
  后 `set_positions`）；arrowstyle / linestyle 两类箭头都可改
  （识别不出的自定义样式报 "custom"，选它=不动）。前端交互语义见
  `docs/rules/frontend/hit-and-selection-geometry.md`「图内箭头交互」。
  * **纯箭头注释同样可拖（2026-09-24，用户的流程图：10 根箭头全是
    `ax.annotate("", xy=…, xytext=…)`，一根都拖不动）**。annotate 的 arrow_patch 每次
    draw 由 `update_positions` 按注释的 `xy` / `xyann` 重定位——**改 patch 下一帧就弹回**，
    这条事实不变；变的是 setter 改的对象：`endpoints_frac` 落到**注释本身的两个锚点**
    （`overrides._set_annotation_arrow`：头经 `xycoords`、尾经 `textcoords` 的变换逆算，
    先写 `xy`——'offset …' 的尾以头为原点），`[尾, 头] = [xytext, xy]`，与独立箭头的
    posA / posB 同口径（未扣 shrink）。manifest 的端点**从注释算**（`annotation_arrow_display`），
    不读 patch 上一帧留下的像素缓存。拖过的注释 `annotation_clip=False`：'data' 锚点离开
    数据范围时 matplotlib 默认整条不画，而端点是 figure 锚定的。原样是 `_AnnAnchors`
    （xy / xyann / clip 三样），只活在 originals 里。
  * **只对「逆算得回去」的开放**（`annotation_arrow_owner`，判不出就不宣称）：文字为空、
    两端坐标系都是 renderer 无关的可逆写法——'data'、'{figure,subfigure,axes}
    {points,fraction}'、尾端另加 'offset …'，以及它们的二元组。**'pixels' 不算**
    （#552 评审）：逆算出的是应用那一刻 dpi 下的原始像素值，导出换 dpi 后像素值不变、图幅
    变了，箭头落到别处（热态 ≠ 导出）。**'fontsize' 也不算**：变换按注释当前字号缩放，
    端点先落、字号后改时锚点跟着字号漂，而 `_must_replay` 不因字号变化重放端点——为一条
    看不见的空注释的字号把它拉进几何档不值得。points / fraction 与 dpi、字号都无关。Artist /
    可调用对象 / Transform / Bbox / 'polar' 不出端点。**有字的注释不出端点**：箭尾从文字框
    算，拖尾巴就是拖字，字自己已能拖（`pos_frac`），两条 override 写同一个 `xyann` 只会互相
    盖写。
  * **「可拖」始终绑在「此刻是纯箭头注释」上**（#552 评审）：有字的注释被清空文字、拖了箭头、
    再恢复文字——端点那条 override 还在列表里但**失效**：setter 按这一轮将要落成的文字
    （`state.pending` 里的 `text`，没有就是此刻的文字）重新裁决，不是纯箭头就把锚点放回
    脚本原样、不发 warning（warning 会阻断写回，而它只是失效）；**裁决一变就重放**
    （`_must_replay` 比这一轮的裁决与上次落下的 `_mm_endpoints_live`），文字变了而端点值没变
    时也会被重新裁决。**不是每轮都重放**：constrained / tight 布局在锚点落下后会重排，热态每轮
    重算会追着布局走，与只算一次的全量重放分岔（等价矩阵写回腿像素门实测 409）。判据不看
    列表序，热态与全量重放判出同一个结果。
  * 看护：`test_arrowpatch_endpoints_and_style_roundtrip`（独立箭头）、
    `test_annotation_endpoints_bind_to_empty_text_through_clear_drag_restore`、
    `test_pure_arrow_annotation_drags_via_its_anchors`（出 / 不出端点的判据、拖完不弹回、
    导出 PDF 在新位置、还原逐位）、`test_pure_arrow_annotation_head_dragged_out_of_axes_stays_drawn`、
    等价矩阵 `s3-pure-arrow-annotation`（含写回后重开）与
    `test_text_annotation_arrow_never_exposes_endpoints`。
- **独立形状（Patch family）可拖动（2026-09-21，用户的流程图脚本：框拖不动）**：
  `ax.patches` 里登记成 `patch` 的形状 `draggable=True`，manifest 的 `anchor` 是
  **包围盒左下角**（figure 分数、y 向下，`Patch.get_window_extent()` 不需要
  renderer），`drag_prop == "pos_frac"`——与文字同名、同一套前端交互。setter
  `overrides._set_patch_pos_frac` 不逐类写位置（Rectangle 是 `set_xy`、Circle 是
  `set_center`、Polygon 是顶点数组…），而是把平移**叠在 artist 级 transform 上**
  （`ScaledTranslation` 记英寸、挂 `dpi_scale_trans`：导出改 dpi 时平移量不变），
  一份实现盖住整族与用户子类；基准 transform 记在 `_mm_pos_base`，每次应用先回到
  基准再量，重放 / 二次拖动才幂等；原样 = 基准 transform，还原即放回。`pos_frac`
  已在 `_FRAC_ANCHORED`，图幅 / 子图落位一变照样重放。**柱不拖**：它们进了柱形
  系列（skip_ids），位置是数据。框里的文字是独立 Text，拖框不带字——要一起走用多选。
  看护：`test_patch_shapes_are_draggable_via_pos_frac`、等价矩阵 `s7-patch-drag`
  （含写回后重开）。
- 散点 marker 可整体替换（set_paths，首改前缓存原始路径，"original" 还原）；
  散点/扁平线的 bbox 走 `_padded_bbox`（PathCollection 用 datalim 换算，零厚度边
  垫 4px，否则进不了 manifest）。
- **Artist family 能力层（2026-08-21）**：`_cls_key` 从「逐个类名的 isinstance 表」
  改成**按 family 认**——任何 `Patch` 子类归 `patch`、任何 `Collection` 子类归
  `collection`、认不出来的 Artist 归 `artist`。**唯一的例外是线组**
  （`LineCollection` / `EventCollection` → `linecoll`，排在 `collection` 之前）：
  它对外那套 prop 名是 `color`（Line2D 口径）而不是 `edgecolor`，gid 也是
  `axes_i.linecoll_j`，两者都已经发出去了——族抽象省的是实现里的重复，不是
  改掉已承诺接口的理由（裁决记在 `docs/audit/2026-08-21-matplotlib-source-audit.md` §14）。同一条 prop 只写一次：
  `_COLLECTION_CAPS` / `_PATCH_CAPS` / `_GENERIC_CAPS` 三张表经 `_install_caps`
  （**setdefault**，族里的专用契约永远优先）注册给 family key。于是 pie 的
  Wedge、axhspan 的 Rectangle、stairs 的 StepPatch、`pcolormesh` 的 QuadMesh、
  `contour` 的 ContourSet、`eventplot` 的 EventCollection、以及**用户自己继承的
  子类**都不用再各写一份。完整对象模型与支持矩阵在
  `docs/architecture/matplotlib-artist-capability-map.md`，升级 matplotlib 走
  `docs/ci/matplotlib-upgrade-checklist.md`。
  * **能力按真实 getter 实况判，不按类名**（`collection_caps()`）。颜色映射中的
    Collection **不给 facecolor**：它的 facecolors 每次 draw 由
    `update_scalarmappable()` 从数组重算，`set_facecolor` 在屏幕上一个像素都不
    会变（3.10.8 / 3.11.1 实测一致）。`pcolor` 的 PolyQuadMesh 与 `hexbin` 的
    PolyCollection 都是 PolyCollection 的子类却永远映射——按类名开放就是
    「界面说改了、画面没动」。反过来 **stroke 对任何 Collection 都开放**：
    此刻没有边不代表加不上边（给 pcolormesh 加网格线是常见需求）。
  * **gid 一个都没变**：`axes_i.scatter_j` / `axes_i.fill_j` / `axes_i.patches_j`
    的序号取的一直是所属列表（`ax.collections` / `ax.patches`）的下标，不是
    「第几个散点」，所以把从前没登记的那些补登记进来不挪动任何已有名字。
    被 stem 容器消费掉的 markerline 另外登记**旧 gid 别名**（只进 `state.index`、
    不进元素表）——历史 override 仍落在同一个 artist 上，界面上不多出条目。
  * **Collection 的包围盒有第二条路**：多数 Collection 的 `get_window_extent`
    回的是无穷大空框（`pcolor` / `hexbin` / `contour` / LineCollection 实测都是），
    老代码判 `width<=0 and height<=0` 恰好成立，于是元素被**静默丢掉**。退路是
    `get_tightbbox(renderer)`（公开 API，与裁剪框求交，永远有限、永远在子图里）。
    已经量得出有限框的继续走原路，包围盒一个像素不变——写回自检比的就是它。
  * **认不出来的 Artist 只开 `visible` / `zorder`**，不开 alpha：前两者由 draw
    的公共机制兑现、任何子类都逃不掉，alpha 要靠每个 artist 自己在 draw 里读。
    宁可少开放，不可开放了却不生效。
  * **manifest 多一个可选的 `unsupported` 诊断清单**（`manifest.census`，
    instrument 时采一次，不是每帧）：画在图上、既没进元素表也不是结构件的那些，
    按类名 + 归属报出来。容器消费掉的成员不算漏。旧前端不认识这个键会原样忽略，
    写回自检只比 gid 集合与几何。
  * **颜色字段不把「没有颜色」显示成一个实色（2026-09-19，#427）**：`overrides.to_hex`
    见到 alpha 为 0 的颜色报 `NO_COLOR`（`"none"`）——没设边色的 patch、`'none'`、空心
    marker 都是；半透明照报 RGB（alpha 另有字段）。之前 `mcolors.to_hex` 默认丢 alpha，
    透明黑显示成 `#000000`，检查器摆出一条并不存在的黑边。Patch 族的 `facecolor`
    走 `manifest._patch_face_hex`：`fill` 是这一组的开关，关着时字段值仍是**开了会画
    的那个色**（`_original_facecolor` 配 artist 的 alpha 重算，与 `bbox_visible` 下的
    `bbox_facecolor` 同一模型；界面 `visibleWhen: FILLED` 收起它）。前端 `ColorField`
    认 `NO_COLOR` 画成「无」色块，两侧常量严格同源（`tests/test_no_color_pair.py`）。
  * 开发工具 `scripts/dev/matplotlib_artist_census.py`（`--api --with-seaborn`）
    普查任意脚本或代表性 API 的 artist 图与 Tavotto 覆盖度。**只用于开发/审计，
    产品路径不依赖它**——`instrument()` 的语义化遍历才是权威。
- **画不画只有一个判据（2026-09-24，用户的 PRB 双联图：两张 `set_axis_off()` 的 imshow
  位图，点左图右缘选中的是右图看不见的「Y 刻度文字」）**：`axestraversal.axis_drawn(ax,
  which)` 与 `frame_drawn(ax)`，与 `Axes.draw` 同一个合取式——`axison`（3D 轴**只看**
  `_axis3don`：`Axes3D` 自己把 `axison` 置 False）× `Axis.get_visible()`，边框 / 背景再乘
  `get_frame_on()`。轴不画时它的刻度组、单条刻度、轴标签在 build 里以 `not_drawn` 丢掉
  （正常缺席，不报进 dropped 诊断），`spine_geometry` 整侧不出，`_axes_fields` 经
  `_axes_prop_drawn` 收掉按了不生效的刻度线 / 网格 / 边框 / 背景色旋钮；只关边框时四侧
  命中区仍在（刻度控得了）、`visible` 如实报 False。轴标签 Text 的 `.axes` 是 None，宿主
  取登记时挂的 `_mm_drag` / `_mm_axis`。
- **面状色图集合与位图同一个几何代理（2026-09-24，同一用户的三联图 (b)(c)：pcolormesh 铺满
  子图，子图拖不动）**：`manifest._is_area_field`——QuadMesh / TriMesh / PolyCollection（含
  pcolor、hexbin、tripcolor）/ 填充的 ContourSet，且 `color_mapping_is_live`——与 imshow 一样
  发 `resizable` + `geom_gid = 宿主 axes`；不填充的等值线、散点、线组不算（子图里有空白可点）。
  宿主 `position_locked`（插图 / 寄生轴）时**位图与网格都不宣称**，与色条代理同一判据。
  代理元素的 bbox 与 `clip_bbox` 求交（前端拿它出吸附参考线、当键盘轮换探针），别的角色
  仍是数据范围口径。看护 `tests/test_figure_recognition.py`、
  `test_manifest_geometry.py::test_quadmesh_outline_is_clipped_to_what_is_drawn`。
- 面板翻转（flip_h/flip_v，先翻转后旋转）：导出按 dpi 位图嵌入
  （show_pdf_page 无镜像；flipH = 行倒序 + 旋转 180°），与 opacity<1 同一取舍。
