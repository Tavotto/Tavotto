# C. 科学图形检查规格（V-xxx）

规则的判据实现以 `src/tavotto/profiles/publication.json`（severity）加两侧求值器（`engine/preflight.py` / `web/src/lib/preflight.ts`）为准，本文是每条规则的**规格全文**。
不要为了凑数量造规则：只有证据能支撑、误报可控的条目才进这里；品味类条目永不进，见 `sciviz_guidance.json` 的 `basis: taste`。

每条必填：ID · 预检 id · basis · 科学依据 · 适用条件 · 检测所需信息（写明现在**有没有**）· 检测逻辑 · 严重度 · 误报风险 · 修复建议 · 测试案例（正常 / 边界 / 错误 / 判不了）· 状态。

现有 5 条科学类规则按 basis 归位如下（判据不变，只登记依据）：

| 预检 id | basis | 对应 G |
| --- | --- | --- |
| `bar-without-errorbar` | statistics | G-012、G-050 |
| `fit-without-ci` | statistics | G-008 |
| `discouraged-colormap` | visual | G-022 |
| `palette-semantic` | visual | G-022 |
| `palette-line-markers` | visual | G-024 |
| `log-axis-nonpositive` | encoding | G-001（V-001） |
| `bar-baseline-truncated` | encoding | G-002（V-002） |

### V-001 对数轴上有画不出来的数据

- **预检 id**：`log-axis-nonpositive` · **basis**：encoding · **状态**：已实现（#2111；维护者 2026-11-21 给豁免、定 warn）。看护：`tests/test_preflight_log_axis.py`。
- **依据**：
  - FDV 第 3 章，0 永远不能出现在 log 轴上（p.20）[原文]。
  - 负值的情形：log 在 x ≤ 0 处无定义 [推导]。
- **适用**：尺度为 `log` 的那一维。
- **检测所需信息**：
  - 轴尺度：manifest 里有。
  - 数据值：manifest 里没有，由 worker 新增只读事实，与 `data_fingerprint` 同一路径、同一预算。
- **检测逻辑**：
  - 只数三类可见元素的有限值 ≤ 0：线 `xydata`、散点 offsets、误差线段端点。误差线下端单独计数。
  - 只数这一维真正经过该轴 `transData` 的坐标（`contains_branch_seperately`），以排除 `axvline`、`axhspan`、`transAxes` 等。
  - 排除柱、直方图、`fill_between`、色条。
  - twin 轴按各自的轴归属计数。
- **严重度**：warn · **修复**：`fix: none`。不自动修，因为改用 symlog、删点还是换线性轴，是科学决策。
- **误报风险**：低。已在 matplotlib 3.11.2 上用 15 种写法验证过原型。
- **测试**（全表见研究方案 §5.3）：
  - 正常：全为正。
  - 错误：线、散点、误差线下端 ≤ 0。
  - 边界：NaN、symlog、隐藏元素、twin 轴、混合变换、柱 / 直方图 / 填充带。
  - 编辑：override 把 `yscale` 改成 log 后出现，撤销后消失。
  - 判不了：超出预算时不出 issue，不冒充判过。

### V-002 线性轴上柱的基线被截掉

- **预检 id**：`bar-baseline-truncated` · **basis**：encoding（**这是 Tavotto 的工程归类**：FDV 把截断柱标为「misleading」，没有贴 wrong）· **状态**：已实现（维护者 2026-11-21 定 warn、`fix: none`、归 `lines`，1.0 豁免与 V-001 同一行）。看护：`tests/test_preflight_bar_baseline.py`。
- **依据**：
  - FDV 第 17 章，「Bars on a linear scale should always start at 0.」（p.209）[原文]。
  - 注意：GoG p.92 反而说条形的定义本身不要求底在零，所以不能拿它当支持证据。
- **适用**：线性轴上的柱组，横柱看 xlim。
- **检测所需信息**：
  - 柱底与柱高：worker 有，manifest 没有，与 V-001 共用同一条事实通道。
  - 轴范围：manifest 里有。
- **检测逻辑**：
  - 先取共同基线，即最底层所有柱的底都相同。
  - 共同基线不在可见范围内时报。
  - 没有共同基线（浮动柱、甘特图）时不判。
  - 差值柱（有正有负、0 可见）不报。
- **实现口径**：worker 只读事实 `bar_baseline` 只记基线（只看可见柱、直角坐标轴、竖柱 y / 横柱 x，堆叠上层与浮动柱不收）；
  范围与尺度由规则从同一 axes 元素的 `[xy]lim` / `[xy]scale` 读，尺度正是 `linear` 才判；可见范围按 min / max 取闭区间。
  直方图（`hist` 也是 `BarContainer`）按同一规则判。全文见 `docs/rules/backend/profiles-and-preflight.md`。
- **严重度**：warn · **修复**：none（MCP 手修提示只说两种改法：从 0 起，或改成点图）· **误报风险**：中。
  已知会报、尚未裁决的写法：inset 放大图、断轴图的上半幅、逐柱循环画的瀑布 / 甘特图（每个容器一根柱，天然有共同基线）。
  仓库里含柱的真脚本实测都不报：figurebench `grouped_bars` 与 examples / compat / acceptance 共 10 份量到了柱；
  `ca_projections` 只有极坐标柱，不发事实；另 5 份跑不起来（要命令行参数、没有 `main` 入口、缺 pandas），未执行。
- **测试**：
  - 正常：0 可见。
  - 错误：柱高 5、8，ylim 设为 4–9。
  - 边界：浮动柱、堆叠柱的上层、差值柱、横柱、log 轴上的柱（不在本条判）。
  - 判不了：柱数据量不了时不出 issue。

### V-003 数据点落在轴范围之外

- **basis**：statistics · **状态**：待评估。
- **依据**：GoG §9.1.8（p.219）[原文]；FDV 第 3 章，超出轴范围的数据会被丢弃（p.13）[原文]。
- **检测所需信息**：数据值（缺）、轴范围（有）。
- **逻辑**：数据元素有一部分有限值落在 lim 之外时，按元素给出个数。
- **严重度**：suggestion。
- **误报**：中。有意放大是常见用法，所以只提示「有 N 个点在画面外，汇总仍包含它们」。
- **测试**：
  - 正常：全部在范围内。
  - 错误：ylim 裁掉了 3 个点。
  - 边界：inset 子图、autoscale 带的 margin。
  - 判不了：超出预算。

### V-004 误差线没有写明代表什么

- **basis**：undecidable · **状态**：规格已定，数据出图那部分由 ADR 0178 那条线实现。
- **依据**：FDV 第 16 章（p.189）[原文]；GoG §7.2（p.131）[原文]。
- **检测所需信息**：
  - 误差线：有。
  - 含义：只在数据出图的配方里有。
  - 图注：没有，它在稿件里。
  - 图内文字：有。
- **逻辑**：
  - 图内文字含 SD / SEM / s.d. / CI / 标准差 / 标准误：不列。
  - 配方生成的图：列出事实，如「误差线为 SD（自动选的）」，附 `SD | SEM | 95% CI` 一键切换。默认 SD 时默认列入（维护者 2026-11-21 定）。
  - 用户脚本画的误差线：列一条「这些误差线代表什么？请在图注写明」。
- **严重度**：不出 issue。只进「需要你确认」，不计数、不报警。
- **误报**：不适用，因为不报警。
- **测试**：
  - 正常：图例写了「± SEM」。
  - 边界：配方 `mean_sem` 已知时列事实，不提问。
  - 错误：误差列名为 `err` 时不许被标成 SD。
  - 判不了：用户脚本画的误差线，列出问题行。

### V-005 同一面板里类别色超过约 8 种

- **basis**：visual · **状态**：待评估。
- **依据**：FDV 第 19 章（p.234）[原文]；VAD §10.3.1（p.226–229）[原文]。
- **检测所需信息**：元素颜色（有）。
- **逻辑**：同一 axes 里离散数据元素的不同颜色数超过 8 时报。连续色映射的元素（有 cmap 的）不算。
- **严重度**：suggestion · **误报**：中。
- **测试**：
  - 正常：5 条线。
  - 错误：10 条不同色的线。
  - 边界：colormap 散点、灰色背景线。

### V-006 小倍数面板的 y 范围不一致

- **basis**：visual · **状态**：待评估。
- **依据**：FDV 第 21 章（p.259）[原文]。
- **检测所需信息**：
  - 轴范围：有。
  - 是否同一量纲：没有，用「轴标题文本相同」作启发。
- **逻辑**：同一行或同一列里，轴标题相同、y 范围不同的面板。
- **严重度**：suggestion · **误报**：中。图注可能已经说明，而图注不在图上。
- **测试**：
  - 正常：范围相同。
  - 错误：标题相同、范围不同。
  - 边界：标题不同、只有一个面板、共享轴。

### V-007 抽象数据上用了 3D 轴

- **basis**：visual · **状态**：待评估。
- **依据**：FDV 第 26 章（p.305）[原文]；VAD §6.3（p.117）[原文]。
- **检测所需信息**：`axes3d` 角色（有）；数据是否为真实三维形状（没有）。
- **逻辑**：3D 轴上只有柱，或者是饼。
- **严重度**：suggestion · **误报**：中高。真实三维数据很常见，所以只对 bar3d 类报。
- **测试**：
  - 正常：曲面图。
  - 错误：bar3d。
  - 边界：3D 散点。

### V-008 类别横轴上连了线

- **basis**：visual · **状态**：待评估。
- **依据**：VAD §7.5.1（p.156）[原文]。
- **检测所需信息**：刻度是否为文字（有，启发）；线元素（有）。
- **逻辑**：x 刻度全是非数字文字，且同一轴上有连线的 Line2D。
- **严重度**：suggestion · **误报**：中。月份这类文字是有序的，要排除可识别的有序序列。
- **测试**：
  - 正常：数值横轴。
  - 错误：组名横轴上画了折线。
  - 边界：月份缩写、日期刻度。

### V-009 成品里的实际字号低于规范下限

- **预检 id**：无（不是预检规则，是导出后对成品的观测）· **basis**：visual · **状态**：观测已实现（`rendercore/inspector.py` 的 `observed.text_size` 与 `text_size_verdict()`；看护 `tests/test_rendercore_text_size.py`）。**不进 `checks`、不改导出裁决、界面不显示**；要不要接成一项检查见 ADR 0203（Proposed）。
- **依据**：
  - FDV 第 24 章，文字大小要在最终尺寸下看（p.291）[原文]。
  - 上下标被排成更小的字：matplotlib 3.11.2 实测，7 pt 的 `$R^2$` 在 PDF 里有一段 4.9 pt 的文字 [推导]。
  - XG 的 `audit_pdf_text.py` 用同一个思路量 PDF 的 `Tf` [二手]；它写死的 5 pt 不引用。
- **适用**：导出的 PDF。预检的 `font-too-small` 按 manifest 的 `fontsize × scale` 判，看不到一段文字内部更小的字；本条量的是成品。
- **检测所需信息**：成品 PDF 的内容流（有，检查器本来就在遍历）；下限（只来自出版规范，中性底没有下限时不判）。
- **检测逻辑**：
  - 每段看得见的文字的有效字号 = `Tf` 字号 × 文字矩阵与累计 CTM（含 Form 的 `/Matrix`）在字高方向的缩放。
  - 找到一段低于下限即 failed。
  - 量到的都够大，但有量不了的段，记 unknown。量不了的段有三种：不画只进文本层的文字；em 不明的 Type 3；矩阵退化。
  - 没有任何可量的文字段：not_applicable。预算耗尽或没有读取器：unknown。
- **严重度**：待定（接成检查时由规范决定是否必需；默认只说明、不拦导出）· **修复**：none。
- **误报风险**：低。量的是字节里的数，不做估计。已知漏报：画成轮廓又不带文本层的字，在内容流里与普通路径分不开，量不到也数不到。
- **测试**：
  - 正常：纯文字、`Tm` 里带字号的写法、缩放后的面板。
  - 边界：旋转文字、`q`/`Q` 恢复字号、`BT` 重置矩阵、下限取等号、容差由调用方给。
  - 错误：有一段小字，同时有量不了的段，仍是 failed。
  - 判不了：不画的文字、非标准 Type 3、退化矩阵、预算耗尽、没有读取器、没有下限。
  - 真 matplotlib：带上下标的图量出 4.9 pt（fonttype 3 与 42）；不带上下标的同一张图量出 7 pt，不报。

### V-011 画出来的东西撞上了（干涉检测接进常规预检）

- **预检 id**：`text-overlap` / `text-over-axes` / `legend-over-data` · **basis**：visual（版面，不是数据语义） ·
  **状态**：已实现（维护者 2026-11-23 裁决「接」；1.0 豁免并在 V-001 那一行）。看护：`tests/test_preflight_interference.py`、
  `web/src/lib/interference.test.ts`、`web/src/lib/validation.test.ts`「干涉检测接进常规检查」。
- **依据**：文字压字、文字压进别的子图、图例盖住数据都是**测得出来的版面缺陷**——读者读不到被压住的那部分 [推导]；
  不涉及科学判断，所以不在 `sciviz_guidance.json` 另起 G 条目。
- **适用**：有引擎 manifest 的矢量面板（位图面板没有几何，不判）；隐藏的面板不判。
- **检测所需信息**（都已有，不加协议字段）：元素 `bbox` 先折进 `clip_bbox`（画出来的部分）；数据的 `geometry`（真画出来的折线 /
  路径）；`editable` 里的 `visible` 与顶层子图的 `position`。
- **检测逻辑**：判据只有 `engine/interference.detect` 一份（MCP 与保留式规范化逐对用的也是它），`preflight.run` 把逐对结果折成聚合项：
  - `text-overlap`：两段文字的布局框在两个方向都咬进 ≥ 0.25 mm（`MIN_OVERLAP_MM`，含等号）。同一图例的条目之间、条目与自己的框不算；
    整体隐藏的图例的条目不算（条目自己仍报 visible）。斜排文字（`text_block.rotation` 不是 90° 的整数倍、没有 `clip_bbox`）
    布局框命中后再按 `text_block` 的有向矩形复核一次（四条边法线上的投影都 ≥ 0.25 mm 才算），**只否决不新增**，
    detail 与文案仍是布局框那组数。主语是两段文字。
  - `text-over-axes`：子图的装饰文字（标题 / 轴标题 / 刻度 / 图例）伸进**另一个顶层子图**的绘图区 ≥ 0.25 mm × 0.25 mm。自己的
    插图、被自己包住的子图、没有 `position` 的次坐标轴 / 寄生轴不算；标注（`text`）不算。主语是那段文字，被压的子图进 `detail.against`。
  - `legend-over-data`：图例框与**同一子图**数据的真实几何相交是「确定」（`detail.certain = true`）。真实几何是 manifest 的
    `geometry`：曲线折线、散点每颗 marker、**误差棒每个点 / 每根误差线段 / 每个帽**、茎叶的茎与 markerline（维护者 2026-11-23
    裁决：误差棒 / 茎叶不再按容器包围盒判，`BBOX_BASIS_ROLES` 清空）；图像的包围盒就是它画出来的矩形（`RECT_BASIS_ROLES`），
    相交也是确定。给不出 geometry 的（超预算 / 超 `MAX_MARKERS`、柱形系列里单根柱的元素、没有路径的 Collection）只有包围盒，
    是「风险」（`certain = false`，文案说「可能」）。两种同一 id、同一档位。
    聚合时确定恒排在风险前（`INTERFERENCE_CERTAIN_RANK`）。figure 级图例不判。主语是图例，被压的数据在 `detail.targets`。
- **图例一侧的取法（与数据出图摆放共用）**（维护者 2026-11-23，与数据出图 tavotto-46 同一口径）：`legend-over-data` 量的是
  图例**真正挡住数据的东西**，不是图例的整块外框。唯一实现 `interference.legend_cover_rects` ↔ `interference.ts`
  `legendCoverRects`，数据出图给图例找位置时复用同一个函数，不另写一份。
  - 框画出来且背景不透明（`frameon` 为真、框可见、背景此刻的有效不透明度 > 0）：用整块图例框（图例默认 zorder 5，
    框确实盖在数据上面）。有效不透明度取 `frame.get_facecolor()` 的 alpha——matplotlib 的 `framealpha` 是**替换**
    颜色里的 alpha，不是相乘，这就是画出来的那个数；`facecolor="none"` 为 0。
  - 无框或背景完全透明：只用每条条目文字（本图例的 `legend_text`）的框，加上每一格示意（色块 / 线段 / marker）的框，
    各外扩 1 个显示像素，不用整块框。
  - 图例和所在坐标区的数据区不相交（放在轴外）：一律不报。
  - 「确定」与「可能」沿用原写法：数据的真实几何（或图像的矩形）压到整块框 / 示意格是确定；只擦到条目文字的框、
    字形本身未必压住的，归「可能」；数据给不出几何、只有包围盒相交的，仍是「可能」。
  - 事实来源：manifest 只读事实 `legend_cover`（`engine/manifest.legend_cover`，legend 元素上；`status`
    闭集 `LEGEND_COVER_STATUSES` = `ok` / `over_budget` / `unavailable`，`opaque` / `face_alpha` / `px_mm` / `handles`）。
    单个图例条目数上限同 `pathgeom.MAX_MARKERS`，整图与 geometry 同一个上限、另一份计数器（同 V-001）；量不了
    （老 manifest 没有这条、`status` 不是 `ok`）退回整块图例框。`detail.legend_basis` 写明用的是 `frame` 还是 `entries`。
  - 测试：有框、线只穿过框内空白（报，确定）；无框、同一摆法（不报）；无框、文字压住线（报，可能）；无框、示意压住线
    （报，确定）；外扩 1 px 的边界；图例在轴外、`clip_on=False` 的线伸过去（不报）；事实量不了时退回整块框。
- **严重度**：沿用 `publication.json` 原有登记——`lab-publication-v1` / `journal-base-v1` 都是 warn，`free-form-v1` 没写、兜底 warn。
  **修复**：`fix: none`（挪图例 / 重排外边距要真实渲染裁决、可能放不下，目标值不唯一，不进 safe_auto；MCP 的
  `tavotto_normalize_figure` 仍是它们的修法）。问题面板归「版面」（`layout`）。
- **误报风险**：
  - 低：`legend-over-data` 的风险档只剩给不出 geometry 的元素；误差棒按真实几何判后，`errorbar_fit`（`loc="best"`）不再报。
    单根柱（`bar` 元素）没有自己的 geometry，图例整个落在一根柱的内部时只说「可能」（压到柱边时柱形系列的 geometry 报确定）。
  - 低：斜排文字有 `text_block` 时按有向矩形复核（45° 长标签不再报，排得太密真叠上的照报）；没有 `text_block`
    （wrap / usetex / 排不出的文字）或带 `clip_bbox` 的斜排文字仍按布局框判，可能多报。`text-over-axes` 不做这层复核。
  - 低：其余写法（`loc="best"`、inset、色条、90° 竖排标签）在真 worker 上不报。
- **两侧同源**：`engine/interference.detect` ↔ `web/src/lib/interference.ts` `detectInterference`（逐行移植，数值格式化与取整按
  Python 的取偶规则 `pyFixed` / `pyRound`）；`preflight._check_panel_interference` ↔ `preflight.ts` `checkPanelInterference`。
  golden：`tests/golden/interference_vectors.json`（逐对）与 `preflight_vectors.json` 的 `interference-*`（聚合）。
- **不重复报**：Python 侧 `preflight.run` 的 `with_interference` 默认关（生产调用方——规范化 / 按规范修图事务、改图验收、
  MCP 桥，含已发布插件里不带参数调 `run()` 的旧桥——都已逐对调 `detect`）；golden 生成器与对拍用例显式开，与 TS `runSpec` 同一份输出。
  MCP 桥另按 id 滤掉聚合项（`bridge._without_interference`），对外仍是逐对清单。
- **测试**：
  - 正常：FigureBench `line_two_series` / `cjk_labels` / `log_decay` / `heatmap_colorbar` / `grouped_bars` 不报；
    `loc="best"`、inset、色条不报；90° 竖排与 45° 斜排的长标签不报；`errorbar_fit` 与「图例只落在误差棒包围盒里的空白处」不报。
  - 错误：横排长刻度标签互相重叠；图例压住一根误差线段（点在框外）判「确定」；排得太密的 45° 标签墨迹叠上；`loc="center"` 的图例压住两条线（确定）；`multi_panel`（没有 tight_layout，右列的 y 轴标题与刻度
    画进了左列子图框里，肉眼可见）报恰好那 4 段文字。
  - 边界：恰好 0.25 mm（含等号）、差一点、贴边；clip 折叠；单点 / 闭合回边 / 水平线段的路径；同图例家族；隐藏图例的条目；
    恰好落在一半上的取整（1.25 → "1.2"）。
  - 判不了：bbox 读不懂（布尔、字符串）的元素不参与；图幅读不出来什么都不报；只有包围盒时只说「可能」、不冒充确定。

## 不做成规则的条目

依据与理由见 `sciviz_guidance.json` 中 `basis: taste` 的条目（G-042 至 G-051），以及 G-041（双 y 轴）。
另外三条明确不做：
- 「图上必须标 n」：三本书都没有这条。
- 任何 pt 字号阈值：只来自期刊。
- 凭 y 落在 [0, 1] 就猜「做过归一化」：伪规则。

XG（来源登记见 `sources.md`）里另有几样明确不做成规则：
- 面板重复、缺对照、「一张图一个结论」：见 G-058（`basis: taste`）。
- 按源码里有没有 `dropna`、`sample`、随机数来猜数据被丢或被伪造（XG 的 `validate_figure.py`）：判的是脚本文字，不是数据，改个写法就绕过，也会冤枉正常脚本。
- 行内三四张图必须等宽：版面选择，不是对错。
