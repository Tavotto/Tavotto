# ADR 0220：机理图——开源编排与闭源图元 / 通路布局的协议

日期：2026-11-28 · 状态：**Proposed**（2026-11-28 维护者对 ADR 0208 第 ⑥ 期设计草案「整套按推荐」；实现分期见 §十一，本 ADR 随 6.B 骨架一起提交）
相关：[0208 Agent 加矢量元素](0208-agent-vector-elements.md)（§七 机理图、§七之二 可编辑性五条、§七之三 导出给 Illustrator）、
[0198 闭源高级版扩展](0198-closed-source-pro-extension.md)（组件分发与 pro 协议 v1）、[0199 一键排版](0199-quick-layout.md)（同一种开源 / 闭源分工的先例）、
[0193 付费版授权](0193-paid-edition-license.md) §一（功能键闭集）、[0191 嵌套组](0191-nested-groups-and-clipping-masks.md)（组名进文档模型，前置 6.0）、
[0212 矢量编辑工具](0212-agent-vector-editing-tools.md)（写入口、三方合并）。

## 问题

科研机理图（膜、受体、通道、核、通路）要的是**好改的矢量**：每个看得见的东西是一个对象、拆组后外观不变、形状是真路径不是一团锚点、分组有层次、命名有意义（0208 §七之二）。
免费的原生对象（箭头、文字、括号、外框、形状）画不出膜与受体；让模型直接写 SVG 又会得到一堆无名、无组、锚点爆炸的路径。
所以要一组**语义图元**与一个**通路布局**，几何与布局算法是高级版的价值（闭源），而编排、校验、命名、分组、核对必须开源——这样「结构对、好改」这条底线对所有人透明可查，也不依赖闭源组件的实现。

## 决定

### 一、分工：开源编排，闭源只做两件纯计算

```
Agent / 改图助手 ─ add 条目 (kind: frame | glyph | graph) ─▶ 开源 engine/mechanism.py   ← license.has("mechanism-diagram") 只在这里
                                                           │ 1 含 glyph / graph 的整批先过权益（frame 免费；未授权整批拒）
                                                           │ 2 解析条目、校验参数（按 catalog 规格）
                                                           │ 3 prohost.call("mechanism.*", 只有数字与结构) ──▶ 闭源 sidecar
                                                           │ 4 校验回包（闭集、有限数、版本一致）◀──────────── 子路径节点 / 端口 / 折线
                                                           │ 5 铸对象：名字、分组、glyph 字段（只用现有对象类型）
                                                           │ 6 可编辑性核对（mechcheck），不过整批不落地
                                                           ▼ 写入口沿用 0208 第 2 期 / 0212（base_revision、复述、撤销留存）
```

- **开源**：`engine/mechanism.py`（编排）、`engine/mechgroup.py`（命名 / 分组词表与规则，`profiles/mechanism_names.json`）、`engine/mechcheck.py`（可编辑性核对，纯判据）。
  `vectoredit._op_add` 把三种新 `kind` 交给 `mechanism.prepare`，其余条目照旧走 0208 求解器；`vectorscene` / `vectoredit` 自己**不判权益**。
- **闭源**（`tavotto_pro`，独立仓库）：**图元几何**与**通路布局**两件纯计算，不碰文档、不联网、不读盘。
- 不新增对象类型：示意图框 = 一个带名字的组 + 一个 `shape rect`；图元 = 一个 `path`（复合路径只给「带洞」），对象上多一个保留字段 `glyph`。

### 二、功能键与 `pro_required`

- 功能键 `mechanism-diagram`：登记进 0193 §一 的闭集、`license.FEATURES`、`tests/test_license_callsites.py` 的 `REGISTERED_CALLERS`（只有 `engine/mechanism.py` 一处调用）。
- **边界（维护者 2026-11-28 定：框免费，图元与通路图算高级版）**：示意图框 `frame` 免费——未授权也照常收下、照常进命名分组与核对，不碰闭源组件；`glyph` / `graph` 是高级版。
- 引擎在 `prepare` 入口对**含 `glyph` / `graph` 的整批**判权益，失败抛 `MechanismError("not_licensed")`；`vectoredit._plan` 折成结构化结果
  `{ok: false, code: "pro_required", feature: "mechanism-diagram", message: <一句话>, learn_more: {label: "了解高级版", url: <官网高级版介绍页>}, errors: []}`（`url` 取 `engine/brand.premium_page_url(locale)`，与前端 `premiumPageUrl` 同源，中文界面 `/zh/premium/`）——**不是错误**。
  一个 `add` 里只要有一个 `glyph` / `graph`，**整批**（含同批的 `frame` 与前面免费的步骤）都不写，不写一半（维护者拍板）。只有 `frame` 与免费条目的批不判权益。
- 免费用户的工具列表与 schema 里这三种 `kind` 都在（`OPS_DOC` 的 `add` 说明里写明「没有授权回 pro_required」）。插件工具层（`_pro_required`）只把这一句转给 Agent，**工具层不判权益**；
  一键排版的 MCP 工具（0199 §十三）以后复用同一处翻译。免费条目（箭头 / 文字 / 括号 / 外框…）不判权益、不受影响。
- 组件不在 / 起不来 / 回包不合闭集 / 修订号不认 → `EditError("pro_unavailable")`（「需要更新高级版组件」，0199 §十二 同口径），不改文档。

### 三、sidecar 命令（加在 `tavotto_pro` 的 `GATED` 里，键 `mechanism-diagram`；**纯增命令，`pro_api` 不升**）

旧组件回 `unsupported`，宿主折成 `pro_outdated` → `pro_unavailable`。sidecar **只收数字与结构**：条目里的文字（标题、身份、块名、标签）、名字、数据一律不发（0198 §三）。

| 命令 | 入 | 出 |
| --- | --- | --- |
| `mechanism.catalog` | `{}` | `{kinds: [{kind, versions: [int], default_size_mm: [w, h], params: [{name, type: number\|enum\|bool, min?, max?, step?, values?, default, unit?, label: {zh, en}}]}]}` |
| `mechanism.glyph` | `{kind, version, params, size_mm: [w, h]}` | `{version, subpaths: [{closed, nodes: [{p, in?, out?}]}], ports?: {名: [rx, ry]}, array?: bool}`；坐标是**框内比例坐标**，与 `ExportClipPath` 同形 |
| `mechanism.graph`（6.F） | `{direction, bands: [{ref, order}], nodes: [{ref, w_mm, h_mm, band?, shape}], edges: [{from, to, meaning, label_w_mm?, label_h_mm?}]}` | `{nodes: {ref: [x, y]}, edges: [{ref, polyline: [[x, y]…]}], bands: {ref: [x, y, w, h]}, metrics}` |

- **回包闭集**（开源侧逐项校验，不合 = `bad_reply`）：子路径 ≤ 256、节点 ≤ 4000（膜的每个脂质 = 一个闭合圆头 + 两条尾直线，共 3 条子路径；6.C 起上限由 64 放宽到 256）、坐标有限、节点键只许 `p / in / out`、端口 ≤ 16；**不回颜色、名字、文字**——颜色与名字由开源侧定。
- `catalog` 里每个 `kind` 回自己支持的 `versions`（取 `latest` 为缺省）；参数规格让前端属性栏**按规格通用渲染**，不内置任何图元知识（6.G）。

### 四、`graph`（通路图）协议与布局

- 条目 `{kind:"graph", name, direction:"lr"|"tb", bands?: [{ref, order, label}], nodes: [{ref, label, shape, band?}], edges: [{from, to, meaning, label?}]}`。区带 = 细胞外 / 膜 / 胞质 / 核等区室，决定沿主方向的分层顺序。
  开源侧把每个 label 量成毫米尺寸，sidecar 只拿 `{ref, w_mm, h_mm, band, shape}` 与 `{from, to, meaning, label 尺寸}`。**本期（6.B）只认条目、回 `not_ready`**；协议与布局在 6.F。
- **布局自写（纯 Python，进 sidecar），不引第三方**：sidecar 是 Nuitka 编出的独立程序，运行时依赖只有 PyNaCl；dagre / elkjs 是 JS（sidecar 无 JS 运行时，闭源 JS 也不能进 webview，0198 §一；elkjs 另有 EPL-2.0 / GPL）；
  grandalf / igraph / netgraph 按包仓库元数据是 GPL 系、不能闭源分发；networkx（BSD-3）没有分层布局。机理图规模小（几十个节点），算法公开（Gansner 等的分层方法、Brandes–Köpf 坐标分配），自写约几百行、更容易保证**确定性**。
  **许可判断依据的是包仓库元数据，没读 LICENSE 原文**；定稿前读原文，dagre（MIT）只作参考、不拷代码。
- **确定性合同**：输出只取决于输入；不用随机、时间、字典 / 集合遍历顺序；浮点量化到 0.01 mm。黄金向量放 pro 仓库，开源仓库的 `pro_echo` 夹具只测接口与校验。
- **开源侧核对（不信 sidecar 自报）**：节点互不重叠、不相贴；边不压节点边框、边与边不重叠；节点只水平 / 竖直摆；文字至少一部分在节点内、标签互不重叠；同区室的节点与边在该区室内、跨区室的边可穿边界。
  推荐项只报数不拦：边交叉数、拐弯数、宽高比、边总长。这些判据取自 SBGN Activity Flow 的「Requirements」一章（见 §八的来源说明）。

### 五、`glyph`：语义图元

- 条目 `{kind:"glyph", name, glyph: 图元种类, at_mm, size_mm?, params?, version?, identity?, frame?, block?, style?}`；落成**一个** `path`（`style` 只许 `color / strokePt / fill`，颜色与线宽由开源侧默认 `#4a4842` / 1 pt）。
- 对象上存 `glyph: {kind, params, version, hash, array?}`。`glyph` 是字段表（`profiles/agent_objects.json`）里的**保留字段**：Agent 不能直接写（`reserved_field`），只有引擎在对象校验**之后**写。不认识 `glyph` 的旧构建原样保留、当普通路径（0208 §七之二 第 2 档）。
- **`version` = 该图元自己的生成器修订号**（整数，每个 kind 各一份），不是应用版本、也不是 sidecar 版本——sidecar 独立更新、独立回滚（0198 §七）。
  新版本只许**加**参数并给默认值、不许改旧参数含义；重生成默认沿用对象上记的 `version`，「升级到新版样式」是单独的、可撤销的显式动作；sidecar 回滚后不认某个 `version` → `unsupported_version`，该对象退成普通路径，**文件不动、几何不动**。
- **`hash`** = 生成几何（子路径节点的比例坐标，量化到 1e-4）的摘要。「已手动修改」**不靠每个编辑动作打标，读时比 `hash`**：对不上 = 脱钩。整体拖动、等比缩放不改比例坐标，不算脱钩。
- **尺寸不是参数**：重生成用对象当前 `w / h`（毫米）作输入，`params` 只放造型参数，用户拉伸后改参数不会弹回原尺寸。
- **命名端口**：图元回若干端口（`{名: [rx, ry]}`，如受体的「配体结合位点」），用于箭头贴端口。第一版只保证**创建时对齐**；重生成后箭头是否跟着走依赖 0208 §六 的持久化锚点，
  **重生成后可能需要点一次「重新对准」**（维护者拍板接受该范围）。
- **脂质双层**：每个叶片一个**阵列对象**（`glyph.array: true`，对 `one_object` 核对放行），不把几十个脂质拆成几十个对象；备选是极简膜（两条线 + 圆点，对象数 3，无例外）。
- **嵌入膜（6.C 续，维护者 2026-11-28：组合要有正常的排列）**：跨膜蛋白条目带 `embed: {membrane: <同批膜条目 name>, at?: 0–1}`，位置由宿主算，不许再给 `at_mm`。
  回包可带只有数字的 `info`（闭集：`natural_size_mm`、`thickness_mm`、`pitch_mm`、`tm_width_mm`、`tm_length_mm`、`tm_mid_mm`〔框内毫米、y 向下〕、`midline`〔≤ 65 个 `[x, y, nx, ny]`，朝外单位法线〕）。
  宿主分三步：① 先问膜（不带让位），读 `thickness_mm` / `midline` / `pitch_mm`；② 再问蛋白，其 `membrane_mm`（跨膜段长）取膜厚并夹进规格范围；
  ③ 在膜上定位——没给 `at` 的蛋白**按各自实际宽度等空隙排**（空隙 = (膜长 − 蛋白总宽) / (n + 1)，两端也各一个；空隙不到 2 个脂质间距 = `bad_item`），给了 `at` 就照给的；
  核「整个蛋白在膜长范围内（两端各留至少 2 个脂质间距）、两两不重叠」（不合 `bad_item`），
  把 `inserts: [{at, width_mm}]`（宽 = 跨膜部分总宽）回填给膜再问一次（膜的外框按完整的膜算，让位前后尺寸不变），
  最后把蛋白的跨膜段中点放在中线上、沿该处法线旋转（`rotationDeg`，顺时针为正）。`inserts` 是膜的参数（存在 `glyph.params` 里，重生成不必重算）。
  让位**不是**拿掉窗口里的整个脂质、留下大空洞：膜按窗口把每片的弧切成若干段，**每一段重新均匀排布**——贴着杆的一端头部圆心离杆缘 1 个头部半径 + 1/4 个间距，膜自己的两端各留半个间距，
  段内间距处处一致；弧本身不变（弯膜仍是同一条连续弧，每个脂质沿该点法线）。脂质子路径上限 256 见 §三。
- **重生成是高级版入口**（属性栏按 catalog 规格渲染，`PremiumLock` 置灰，6.G）；结果始终是普通可编辑路径，授权过期后仍可手改，只是不能按参数重生成。

### 六、示意图框、命名、分组、可编辑性核对（全部开源）

- **示意图框** `{kind:"frame", name, title?, at_mm, size_mm, fill?}`：一个组「示意图 · 标题」+ 一个 `shape rect` 框线；位置是**画布毫米**（框内条目也是画布毫米，文档里没有「局部坐标」概念，所以导出、拆组、一键排版都不必认识它）。
  免费条目与图元带 `frame`（同一个 `add` 里 frame 的 `name`）与可选 `block`（结构块名，如「细胞膜」）进框；落成 `groupPath: [框组, 块组]`，组名经 0191 修订的 `groupNames` 存下。
  一键排版（0199）怎么对待示意图框**没核**，列入待定。
- **命名** = 「角色 + 身份」（`mechgroup.name_for`）：角色词来自 `profiles/mechanism_names.json`（中 / 英），身份取条目的 `identity` / 标题 / 文字内容 / 条目名；**创建当时按调用方传入的界面语言固化**，不随语言切换改；同批重名自动加序号。
- **叠放顺序**：背景（框）→ 结构（图元）→ 免费条目的箭头 → 文字。
- **可编辑性核对**（`mechcheck.check`，不过整批不落地，`EditError("not_editable", field="核对项:对象")`；`warn` / `unknown` 如实写进复述，口径见 `docs/rules/repo/predicate-subject.md`）：
  1. `one_object`：每个 `path`（不带 `glyph.array`）的各子路径拍平后，相交 / 相接 / 嵌套（洞）算同一个东西，互不相连的 = 几个东西并成了一条 → fail。
  2. 拆组后外观不变：画布上的组没有任何自己的属性（只有成员带 `groupId`），无需判；字节 / 像素对拍沿用 #2333 的测试（`test_svg_export_groups`）；Illustrator 真机是人工步骤。
  3. 形状好改：`native_shape`（拟合得出矩形 / 椭圆 / 直线的路径该用原生对象，容差 0.05 mm）、`glyph_meta`（字段形状、`hash` 与几何一致、`kind` / `version` 在 catalog 里）、
     `path_quality`（自由路径锚点质量，接 6.A 的 `engine/pathquality`；该模块未合入时**如实列 unknown**）、`thin_outline`（疑似轮廓化描边，warn）。
  4. `group_tree`：顶层只有一个组、深度 ≤ 4（字段表 `max_group_depth`）、框下的块不留单成员组（顶层示意图框可以暂时只有框线）。「分得合不合理」判不了，只核结构。
  5. `naming`：无无名对象 / 无名组、同一层无重名、不是 `路径 3 / Group 2` 这类通用名。名字好不好听判不了。
- 缺口（不假装验过）：SVG 里已被作者转成轮廓的文字认不出；虚线圈这类「设计上就是一个东西、但互不相连」的会被 `one_object` 误报（要用 `glyph.array` 或拆开）。

### 七、首批图元清单（建议 12 个；顺序是经验排序，没做过统计，由维护者增删）

`membrane` 双层膜、`receptor_rtk` 受体酪氨酸激酶、`gpcr` 七次跨膜受体、`ion_channel` 离子通道、`vesicle` 囊泡、`nucleus` 细胞核（带核孔的双层核膜）、`mitochondrion` 线粒体、`er` 内质网、
`dna_helix` DNA 双链、`filament` 细胞骨架丝、`antibody` 抗体、`protein_blob` 蛋白团块（参数 `lobes / roundness`，**确定性**、不用随机种子）。**先落 `membrane`、`receptor_rtk` 两个做最小端到端演示**（6.D），其余分批（6.E）。
只收原生对象画不出的；细胞轮廓（`ellipse` + 预设）、配体小球、磷酸化标记、箭头都不做成图元。

### 八、作用箭头：只说「SBGN 风格」，不声称符合

- 作用箭头本来就是免费的原生 `arrow`。建议在**开源 `arrow` 条目**上加可选键 `meaning: positive | negative | unknown`，落成端型预设（`open` / `bar` / `diamond`），仍**免费**（本期不做，列入待定）。
- Tavotto 现有端型（`triangle / stealth / diamond / dot` 为实心，`open` 是三点折线，`bar` 是短杠）**没有空心箭头、空心圆 / 菱形，也没有「短杠 + 箭头」**，所以画不出严格的 SBGN 记号。
  文案与 `sciviz_guidance` 一律写「SBGN 风格的记号」，不写「符合 SBGN」；要不要新增端型是独立的高档 PR。
- **来源说明（只引读过的部分）**：SBGN Activity Flow Level 1 **V1.2**（2015）§2.3.1、§2.6.1、§2.8.1–2.8.4 与第 4 章布局指南；Process Description Level 1 **V2.1** §2.5.1、§2.8.1–2.8.5。
  **AF V1.3（现行版）没读，不引**；读的是文字，没有核对插图（空心 / 描边的细节要看图后才写进 `sources.md`）。「间接 = 虚线」、「转位 / 结合」不是 SBGN 的弧端型（后者在 PD 里是过程节点），是 Tavotto 自己的约定，不得写成 SBGN。

### 九、免费与高级版边界

免费：示意图框 `frame`（带名字的组 + 外框，含命名分组与可编辑性核对；维护者 2026-11-28 定）、所有原生标注（箭头、文字、括号、外框、连线、形状）、导出、图层组名与改名、组名进 SVG 导出（0191 修订 / 0208 §七之三）、自由路径的质量核对（6.A，保护所有人的路径）。
高级版（`mechanism-diagram`）：`glyph` / `graph` 两种条目、按参数重生成。已发布的免费能力不受影响（0193 §一）。功能键是否与 `quick-layout` 同档售卖由 Stripe 后台定，代码只管闭集。

### 十、隐私

sidecar 只收「几何结构 + 毫米尺寸 + 短号」；节点 / 边 / 区带的文字、名字、数据都不发（`docs/privacy.md` 无新增出机数据流，只补一句「机理图条目的文字不出本机组件」）。回包不带文字。

### 十一、分期（对应设计草案 §2.7）

| 期 | 内容 | 状态 |
| --- | --- | --- |
| 6.0 | 组名进文档模型（0191 修订） | 已合入 / 在审 |
| 6.A | 路径质量核对 `engine/pathquality` | 另一分支；`mechcheck` 里留接口，未合入时列 unknown |
| **6.B** | **本 ADR + 骨架**：功能键、`engine/mechanism.py`、`pro_required`、`frame`、命名分组、可编辑性核对、`pro_echo` 假图元、插件工具认三种 `kind` | 本期 |
| 6.C | pro 仓库：`mechanism.catalog / glyph` + `membrane`、`receptor_rtk`、黄金向量、`GATED` 登记 | 闭源 |
| 6.D | 最小端到端演示（不含 `graph`）+ 评测第一轮 | |
| 6.E / 6.F | 其余图元 / `graph` 协议与布局 | |
| 6.G / 6.H / 6.I | 属性栏重生成与「已手动修改」提示 / `sciviz_guidance` 条目与 SBGN 来源登记 / 改图助手接三种 `kind` | 桌面改图助手这一版遇到三种 `kind` 会把该条丢进 `unhandled`（不崩） |

### 十二、待定（本期没有决定的）

- 一键排版（0199）对示意图框的处理（当固定盒子障碍物？）没核。
- `label`（图元旁的文字标签）与 `connect / arrow` 端口锚点（`{item, port}`）本期未做：端口只随回包返回，进 6.D。
- 0208 §六 持久化锚点（分期 4 / 7）合入到哪一步没核。
- **图元外观，agy 第二轮认为可延后的两项**（6.C 首批图元）：膜的尾巴偏短（默认头部半径 0.8、层厚 5.5 r 时每条尾约 1 mm，想更长只能调大层厚，上限 6 r）；受体的跨膜杆穿过磷脂头部时画在上面、遮住一部分磷脂头。
  两项都不影响协议与可编辑性，等维护者看过真画布再定要不要改。颜色归对象样式：`catalog` 的 `style_hint` 只给建议（膜浅暖色、受体浅蓝灰）。
