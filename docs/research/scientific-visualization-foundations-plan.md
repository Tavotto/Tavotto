# Tavotto 科学可视化理论体系：研究、审计与实施方案（Phase 1–2 交付 + Phase 3–4 方案）

> 2026-11-21 · 状态：**方案，待维护者拍板**（文末「需要你拍板的事」）。本文只做研究、审计与设计，不改产品代码。
> 基线：`origin/main` @ `6f7ac8673`；在途相关 PR：#2067（Core v0.2：共同文档、原生 ggplot2）、#2095–#2098（读懂数据）、#2097（「误差列」角色）。

## 0. 读法与证据标记

每一条结论都带一个标记，三类不混写：

| 标记 | 含义 |
| --- | --- |
| **[原文]** | 已在原著全文里定位并核对过（章节 + 印刷页；短引文 ≤ 25 词，已逐字机械比对提取文本） |
| **[二手]** | 原著转述的他人研究（如 Munzner 转述的 Cleveland–McGill），或我们没读原著的来源 |
| **[推导]** | 我们自己的工程推断与设计建议，书里没有这么说 |
| **[仓库]** | Tavotto 现状，附 `文件:行` 或 ADR 节作证据 |

**来源与版本。** Wilkinson, *The Grammar of Graphics*, 2nd ed., Springer 2005（下称 GoG）；Munzner, *Visualization Analysis and Design*, CRC 2014/2015（下称 VAD）；Wilke, *Fundamentals of Data Visualization*, O'Reilly 2019（下称 FDV；作者在 clauswilke.com/dataviz 公开全文，按章名可核对）。三本由维护者提供的电子版做全文检索阅读；**仓库里只放转述 + 极短引文 + 页码，不放原书文件或长段摘录**。

**阅读覆盖（诚实声明）。**

| 书 | 细读 | 只抽查或没读 |
| --- | --- | --- |
| GoG | 第 1、2、4–7、9、10、12、17–19、20.3 章 | 第 3、8、11、13、14、16 章及各章 Examples。图 2.2 的管线图、Table 7.1/8.1/10.1 在文本提取时损坏，相关结论只取自正文叙述 |
| VAD | 第 2–7、10 章 | 第 1、11、12、13、15 章只读了被引的节；第 8（空间）、9（网络）、14 章没读 |
| FDV | 第 1–4、6–14、16–29 章 | 第 5、15 章没细读 |

---

## 1. 三本书最值得 Tavotto 吸收的核心理论

### 1.1 GoG：图形是「规格」，不是图表类型

1. **管线有不可调换的顺序** [原文]：variables → algebra → scales → statistics → geometry → coordinates → aesthetics（第 2 章，p.31–37）；书明说「we cannot change the ordering of stages in the pipeline」（p.25）。管线是数据流的切面，不规定实现（p.24）。
2. **三种变换，位置不同** [原文]（§6.2，p.93）：变量变换和尺度变换必须**先于**统计；坐标变换**后于**统计，因为它只改外观、不改统计性质。对变量取 log，轴标题应是「Log of Income」；对尺度取 log，轴上是原值、标题仍是「Income」。均值的 log ≠ log 的均值，所以聚合要在非线性变换之后算（p.218）。
   - [推导] matplotlib 里，用户脚本先在原始量级上算 mean/SD，再 `set_yscale('log')`。按 GoG 的顺序，这是「统计先于尺度」。Tavotto 里把轴切成 log 只是改展示，误差线不会重算，在 log 轴上也不再对称。
3. **frame 必须覆盖数据；数据缩放 ≠ 图形缩放** [原文]：
   - 「the frame should always cover the data on which the graphs are based」（p.219）。
   - 为美观裁掉汇总图形周围的数据，书称为 lying with graphics（p.219–220）。
   - 改 frame 的数据边界等于对数据取子集，嵌在里面的统计必须重算；图形缩放只是整体放大（p.191–192）。
4. **统计与几何分离** [原文]：「to separate form from function」（p.120）。同一几何可以表示不同统计；误差线用 interval 画，「The shape of the bars is arbitrary」（p.131）。
   - [推导] **误差线是 SD、SEM 还是 CI，从画出来的几何里永远反推不出来**，只能由统计元数据携带。
5. **数据约束的属性 vs 自由美学** [原文]：「size for area is a data attribute, not an arbitrary value we may change for aesthetic purposes」（p.278）。
6. **规格三分：数据 / 结构 / 样式** [原文]（第 18 章 ViZml，p.589–609）：
   - 结构一改，图「fundamentally different」（p.593）。
   - 样式必须放在结构之外，改样式不影响来自数据的映射（p.595–598）。
   - domain 节点可以用来保证柱状图包含零（p.593）。
7. **零基线是尺度问题，不是图形问题** [原文]：「these are statements about scales rather than graphics」（p.92）；双轴一般应避免（p.351–352）。
8. **guide 由 scale 派生；注解由数据驱动才算 guide，否则是「scribbles or doodles」** [原文]（第 12 章，p.346–354）。书还区分数据坐标标注和页面坐标绘图工具。
9. **Reader（第 19 章）：从图读回数据是逆问题** [原文]。整条链都可逆，结果才能复原；书明说没有元数据就分不清独立组与重复测量（p.612）。
   - [推导] 对 Tavotto 来说，**脚本与数据才是权威，渲染结果只用来校验**。不该走「看图猜语义」的路线。
10. **边界声明** [原文]：
    - 书「not about good taste, practice, or graphic design」（p.1）。
    - 语法能产生丑图，但不会产生无意义的图（p.15）。
    - 书不按 Stevens 指数修正感知映射（p.258–259），也不接受「chartjunk 有害」的一般结论，理由是研究证据不足（p.271）。
    - 书里没有色盲、期刊规范、字号/dpi，也没有 matplotlib/R/MATLAB。

### 1.2 VAD：先抽象数据与任务，再谈编码；理由要分清证据强度

1. **What–Why–How** [原文]：
   - 语义与类型是两个正交问题，光看数值无法知道含义（§2.2，p.22）。
   - 哪些属性是 key、哪些是 value，有时本身就是分析的目标，而不是起点（p.36）。
   - 时间是 key 还是 value，需要外部知识才能判断（p.39）。
   - 属性类型分 categorical / ordered（ordinal、quantitative），方向分 sequential / diverging / cyclic（p.31–34）。
2. **Derive 是核心动作** [原文]（§3.4.2.3，p.50–53）：
   - 「Don't just draw what you're given」。
   - 差值、比值、归一化、log、分箱、聚合都是 derive，而且越往后越有损。
   - 聚合会丢信息，书拿 Anscombe 四重奏作反例（p.7–8，第 13 章）。
3. **四层嵌套与验证** [原文]（第 4 章，p.66–80）：四层是领域情境、数据/任务抽象、编码/交互 idiom、算法。抽象层是否有效，只能由目标用户做自己的工作来验证（p.78）；idiom 层的「按感知原则论证」只是即时证据，证明不了有效（p.77）。
4. **Expressiveness / Effectiveness 与通道排序** [原文]（第 5 章）：
   - 编码应表达「all of, and only」数据里的信息（p.100）。
   - 有序数据的排序：aligned position > unaligned position > length > angle > area > depth > luminance > saturation > curvature ≈ volume；类别数据的排序：region > hue > motion > shape（p.102）。
   - 书说这个排序是「my own synthesis」（p.102）。
   - [二手] 排序底层是 Cleveland–McGill 的实验，Heer–Bostock 复现时唯一的差异是长度与角度大致相当（p.105）。
   - 经验法则被书自己称为「deeply incomplete」（p.117）。
5. **按 key / value 计数分流 idiom** [原文]（第 7 章，p.145–174）：
   - 散点图：0 个 key、2 个 value；柱状图：1 个类别 key、1 个 value；热图：2 个 key、1 个 value。
   - 「Line charts should be used for ordered keys but not categorical keys.」（p.156）
   - 各 idiom 带规模数字，前提是约 1000×1000 像素的屏幕（p.159 脚注）；印刷图需要重新标定。
6. **颜色** [原文]（第 10 章）：
   - 小而分离的区域里，可分辨的类别色约 6–12 个，背景色和默认色也要算进去（p.226–229）。
   - rainbow 在感知层面有三个问题（p.232），但「Rainbows are not always bad」：类别数少的分段 rainbow 可以用（p.234）。
   - 有序数据用亮度单调的色图；避免红绿发散，用色盲模拟器检查（p.235）。
   - 文字的亮度对比至少 3:1（p.223）。
7. **经验法则** [原文]（第 6 章）：
   - 「No Unjustified 3D」：只有任务涉及真实三维形状时，3D 才容易说得通（p.117）。
   - 「Get It Right in Black and White」（p.140）。
   - 并排的小倍数优于依赖记忆的比较（p.131–134）。
   - 叠加层数「two is very feasible and three is possible with care」（p.267）。
8. **书里没有的内容** [原文，检索确认]：不确定性、误差线、置信区间、显著性（全文检索「error bar」「confidence」都是 0 处）；零基线、截断轴、双轴；期刊规范；具体色图名。**这些条目不能挂 Munzner 的名字。**

### 1.3 FDV：「错 / 坏 / 丑」三分法与一批可执行原则

1. **分类学** [原文]（第 1 章，p.2–3）：ugly = 美学问题；bad = 感知问题，即不清楚、混乱、过于复杂或欺骗；wrong = 数学问题，「objectively incorrect」。ugly 比 bad、wrong 更主观，ugly 与 bad 的边界「somewhat fluid」。书里**没有**「good」标签。
   - [推导] 这正好对应用户要求的「科学错误 / 统计或视觉风险 / 审美偏好」三类，可以作为 Tavotto 规则分类的理论锚点（见 §3.3）。
2. **硬规则**，书里用 must / always 的措辞 [原文]：
   - 「Bars on a linear scale should always start at 0.」（第 17 章，p.209）
   - 画在 log 轴上的柱子表示比值，要从 1 起，不从 0 起（p.215）。
   - 0 永远不能出现在 log 轴上（p.20）。书**只谈到 0，没有谈负值**；负值不能上 log 轴是数学事实，不是 Wilke 的话。
   - 误差线必须说明代表什么量或置信水平：「you must specify what quantity and/or confidence level the error bars represent」（第 16 章，p.189）。
   - 不要用误差线是否重叠判断显著性（p.191）。
   - 填充面积图只有 y 从 0 起才成立（第 13 章）。
   - 饼图各块加起来必须是 100%（第 11 章）。
   - x、y 同单位时，两轴网格间距应相等（第 3 章，p.15）。
   - 装饰性 3D「unequivocally bad」（第 26 章，p.305）。
3. **强建议**，书里留了例外 [原文]：
   - 超过约 8 个类别时，用直接标注代替颜色（第 19 章）。
   - 用色觉缺陷模拟器测图（第 19 章）。
   - 小倍数各面板用相同的轴范围；不得已不同时，至少在图注里提醒（第 21 章）。
   - 缩小到实际尺寸看轴标签。书说如果只记住一课就记这一课，但书**没有给任何 pt 数值**（第 24 章）。
   - 直方图要试多个 bin 宽（第 7 章）。
   - 检查密度估计有没有预测出不可能的值（第 7 章）。
   - 样本不够多时不用小提琴图（第 9 章）。
   - 含线条或文字的图避免 JPEG（第 27 章）。
4. **书里明确说「品味」的清单** [原文]（这些可以直接列进「不做规则」名单）：
   - 分组柱还是拆成多图。
   - 直方图还是密度图。
   - 饼图、堆叠、并排「none … is consistently superior」。
   - 误差线带不带端帽。
   - 外框与网格。
   - 空心点还是实心点。
   - 旋转刻度标签。
5. **书里没有的内容** [原文，检索确认]：双 y 轴、symlog、log 轴刻度放置、箱线图与小提琴图的 n 阈值、「图上必须标 n」、数值字号阈值、Matplotlib 的具体规则。

### 1.4 三本书交叉后提炼出的八条「Tavotto 原则」

以下每条都是 [推导]，括号里是依据。

- **P1 数据↔视觉一致**：图上每个数据标记都必须能回到它代表的那个值，被静默丢掉或截断的数据是缺陷，不是风格（FDV 的 wrong、GoG 的 frame 覆盖数据）。
- **P2 统计语义不可从几何推出**：误差线含义、聚合方式、归一化只能由元数据携带。拿不到就明说「不知道」，不猜（GoG §7.1、第 19 章；FDV 第 16 章）。
- **P3 变换分层**：分清变量变换、尺度变换、坐标变换；尺度或范围的编辑不会重算脚本里的统计，界面不得暗示重算过（GoG §6.2、§9.1）。
- **P4 编辑分类**：样式（自由美学）/ 结构（尺度、坐标、几何）/ 数据（约束属性）三类编辑风险不同，数据类编辑不开放手工拖动（GoG ViZml、p.278）。
- **P5 类型与任务先于图型**：推荐是「派生数据 + 编码」，必须附带适用条件与「丢掉了什么」；任务假设由用户确认（VAD 第 2–4、7 章）。
- **P6 理由分三档证据**：数据事实（可核验）、感知原则（指导性，不是定律）、用户确认的任务假设（VAD §4.6）。
- **P7 三类问题分开**：错误（数学或编码不一致）/ 风险（依赖上下文）/ 偏好（不报）。不可判定的问题不冒充判过（FDV 第 1 章；VAD p.13「few absolute truths」）。
- **P8 可复现**：数据、派生、统计方法、绘图参数要可追溯；脚本优先，手工修饰也要可重放（FDV 第 28 章；GoG 第 15 章 integrity）。

---

## 2. Tavotto 现状审计：已有能力与真实缺口

### 2.1 图形表示与编辑

- **表示** [仓库]：
  - worker 常驻活的 matplotlib Figure，外加一个 `FigState` 数据袋（`src/tavotto/engine/overrides.py:117`）。
  - 每次渲染后现算 manifest（`manifest.py:5929 _build_manifest`），元素按位置式 gid 寻址（`manifest.instrument()`，`manifest.py:804`）。
  - manifest **没有 schema 版本号**，靠「老引擎不发的可选字段」演进（ADR 0003；`web/src/lib/api.ts:2752 ManifestElement`）。
- **捕获的语义** [仓库]：
  - axes 的 `xlim/ylim/xscale/yscale` 是可编辑字段（`manifest.py:4450-4483`），另有只读的 `autoscale.expected`（`manifest.py:6163-6191`）。
  - 线、柱、误差线这几族只有样式字段（`_line_fields` 在 :2521，`_bar_series_fields` 在 :3176，`_errorbar_fields` 在 :3254，最后一族**没有误差值，也没有含义字段**）。
  - 能识别 twin 轴（`manifest.py:548-627`）和 `axes3d` 角色（:909）。
- **数据痕迹** [仓库]：只有只读的 `data_fingerprint`，内容是形状加 float64 的 sha256，没有数值（`manifest.py:5035-5142`）。
  - `_fingerprint_parts` 已经明确「主语是数据」：柱取底和高，误差线取数据点和误差段，线取 `get_xydata()`。
  - 消费者**只有 AI 改图与数据出图路径**（`figureverify.data_unchanged`、`ai_data_*`），前端不读这个字段。
- **编辑** [仓库]：
  - 编辑存成 `(gid, prop)` 全量 override 列表（`web/src/types/document.ts:75-92`），重放语义见 `docs/rules/backend/override-application-and-replay.md`。
  - 「改数据」的编辑靠**能力门控**挡住，不开 handler。比如「不给 `bar`：……挪一根柱等于改数据」（`overrides.py:5518`）；数据坐标的曲线不宣称可拖（`manifest.py:391,400`）。
  - 尺度或范围可以改，但只对 logit 做了渲染可行性检查（`_logit_fits`，`overrides.py:4060`）；**从线性切到 log 时有非正值、或改 ylim 把柱截断，都没有任何提示**。
- **写回与导出 .py** [仓库]：
  - 写回是 prepare → verify（全量重放 + 几何 + 像素）→ commit（`docs/rules/backend/writeback-transaction.md`）。
  - 导出 `_edited.py` 不改原脚本，靠重跑核对几何与像素（ADR 0171）。
  - **这两条验证都比几何和像素，不比数据指纹**；`figureverify.py` 头注自己承认「像素相似不等于数据没变」。
- **跨语言** [仓库]：
  - R/MATLAB 只当静态素材（ADR 0184「仅版面」；`foreignsource.py` 不执行）。
  - 可编辑路径是由 AI 转写成 matplotlib，且「不是自动科学保真认证」（ADR 0143）。
  - 在途的 #2067（Core v0.2）首次在桌面原生运行 ggplot2：会话内保留一次构建得到的 gtable 和**统计图层**，首批只开放标题改色。ggplot2 是三种生态里唯一在对象层面带 stat/scale/coord 的。
  - SVG 计算图表的 `ChartModel`（`chartmodel.py:28 tavotto.chart-model/1`，ADR 0182 Proposed）是仓库里唯一持有数值数据、并有显式 schema 版本的模型。它的 `yerr` 只在键白名单里，没有被校验（`chartmodel.py:214, 361-370`）。
- **可复现** [仓库]：
  - 回执里有脚本哈希、观察到的输入文件哈希（只覆盖 `open` 与 numpy DataSource，`observation=partial`），以及 7 个包的版本（`figsession.py:433`）。
  - **导出产物不内嵌来源信息**（`rendercore/inspector.py:1137` 是检查器产出，不写进 PDF/SVG）。
- **RenderCore** [仓库]：是排版与导出后端，PDF 作为外来页 Form XObject 搬入（ADR 0065/0072，`docs/rules/backend/rendercore.md`），对图的科学语义不可见。没有找到「RenderCore 接管 matplotlib 渲染」的计划。

### 2.2 预检与规则

- **规则在哪里** [仓库]：
  - 唯一权威是 `src/tavotto/profiles/publication.json` 的 severity 表。
  - 两个求值器是 `engine/preflight.py`（入口 `run()`，在 :1329）与 `web/src/lib/preflight.ts`，靠 `tests/golden/preflight_vectors.json`（67 例）对拍。
  - 规则目录在 `web/src/lib/validation.ts:136-193 RULES`。
- **严重度** [仓库]：四档 `error / warn / not_verifiable / suggestion`（`engine/profiles.py:38`）。约定是「数据语义类全在 suggestion 档，绝不替用户裁决」（`docs/rules/backend/profiles-and-preflight.md`）。
- **类别** [仓库]：只有导航用途的 `text|lines|layout|color|file|other`（`validation.ts:124`）。**`bar-without-errorbar` 与 `fit-without-ci` 被归在 `color` 类**（:179-180），类别名与内容不符。
- **科学内容规则** [仓库]：全部是 suggestion，共 5 条：`discouraged-colormap`、`palette-semantic`、`bar-without-errorbar`、`fit-without-ci`、`palette-line-markers`（`preflight.py:889-969`）。
  - 没有：log 轴非正值、柱基线截断、双轴、3D、类别色过多。
  - `fit-without-ci` 靠 label 正则判断（`preflight.py:89`）。
- **配方里的科学规则** [仓库]：数据出图 v2 的配方校验里有一整组，但**只拦 AI 生成的配方，不审用户自己的图**（`ai_data_recipe.py:147-210 REJECTIONS`）。
  - 包括 `bar_baseline_invalid`、`log_nonpositive`、`dual_axis_unsupported`、`group_limit_exceeded`（8）、`box_violin_n_small`（<5）、`summary_missing`。
  - 取值类规则在没给 values 时列为 `unchecked`，不假装判过（`ai_data_recipe.py:1363`）。
- **「需要你确认」折叠组** [仓库]：问题面板末尾已经有这一组，给带条件的 v2 期刊事实用，不计数、不报警（ADR 0155 §14；`web/src/components/left/ProblemPanel.tsx`、`web/src/lib/journalV2.ts`）。它是「不可判定问题」现成的出口。
- **单侧规则的先例** [仓库]：干涉三条 `text-overlap / text-over-axes / legend-over-data` 只在 Python 侧（`interference.py:63-65`；`normalize.py:271`），桌面 `RULES` 里没有，golden 也不覆盖。

### 2.3 数据出图（ADR 0178）

- **读数据** [仓库]：
  - 列类型 `numeric/date/text/mixed`；单位只从列名后缀取（`ai_data_figure.py:51/65`）。
  - 列角色是闭集 `ROLES`（`ai_data_roles.py:52`）；#2097 在途，加 `error` 角色。
  - 发给模型的是摘要，不发数据行。
- **记录什么** [仓库]：
  - 配方（含 `summary ∈ mean_sd/mean_sem/mean_ci95/median_iqr`）只写在生成脚本的头注释 `# tavotto-source:` 里（`ai_data_template.py:1187`）。**没有任何下游读它**。
  - 「图里不写脚注」（ADR 0178 修订），汇总定义不进图、不进 manifest。
- **假设** [仓库]：
  - 懒模式「永不返回 ask」，默认汇总是 `mean_sd`（`ai_data_recipe.py:2097`）。每个替用户做的决定都写进响应，用户可以在右栏改。
  - 配对或个体编号只认用户确认（`ASK_FIRST`，:154-160）。这一条符合 P2。
- **推荐理由** [仓库]：只有理由码，加 ≤ 80 字的 `why`（`ai_data_recipe.py:1860`；`CandidateGrid.tsx:138`）；没有结构化的适用条件与局限。

### 2.4 实测：两个「静默」现象（matplotlib 3.11.2，本机复现）

```text
ax.plot([0,1,2,3], [0,-1,10,100]); ax.set_yscale("log")
→ 数据 y 里 2 个 ≤0；显示坐标 y = -3.36e+05（被画到轴外），ylim 自动成 (8.9, 112)，不报错不告警
ax.bar([0,1], [5,8]); ax.set_ylim(4, 9)
→ 柱底 = 0，轴下限 = 4：柱长不再与数值成比例，不报错不告警
```

两件事今天在 Tavotto 里都**没有提示**。用户在检查器里把轴切成 log，也会触发第一种。

### 2.5 缺口清单（每条都有证据）

| # | 缺口 | 证据 | 对应原则 |
| --- | --- | --- | --- |
| G1 | 用户自己的图没有数据一致性检查（log 非正值、柱截断、数据超出范围） | preflight 里没有这几个 id；配方侧有但只拦 AI | P1 |
| G2 | 统计语义没有载体：误差线含义、n、归一化都不在模型里，配方注释也无人读 | `manifest.py:3254`；`ai_data_template.py:1187`；ADR 0178 | P2 |
| G3 | 尺度与范围编辑没有语义提示 | `overrides.py:4060-4082` | P3 |
| G4 | 手工编辑和写回不校验数据不变 | 指纹消费者只有 AI；writeback 只比几何与像素 | P1/P4 |
| G5 | 规则只有严重度、没有「依据类别」维度；科学规则挂在 `color` 下 | `validation.ts:124,179-180` | P7 |
| G6 | 推荐理由没有结构，不分证据档 | `ai_data_recipe.py:1860` | P5/P6 |
| G7 | 同一判据两处实现的风险：`log_nonpositive` 在配方里，新预检规则会是第二份 | `ai_data_recipe.py:171` | 单一权威 |
| G8 | 没有成体系的科学可视化原则文档（docs 下检索不到 Wilke、Munzner、GoG、零基线） | 检索结果 | — |
| G9 | 导出不内嵌来源信息；输入观察是 partial | `receipt.py`；`inspector.py:1137` | P8 |
| G10 | R/MATLAB 没有元素级语义；ggplot2 原生只到标题改色（#2067） | ADR 0184；#2067 | Core |

**不是缺口、不该改的**（维持现状）：
- 能力门控挡「改数据」的编辑，这就是 GoG「约束属性」的工程实现。
- 写回的三段事务。
- `(gid, prop)` override 模型。
- RenderCore 搬外来页、不重画。

这些都与理论一致，没有理由重构。

---

## 3. 长期知识文档结构

### 3.1 目录（建议，待拍板）

```text
docs/sciviz/
  README.md               读法、证据标记、来源版本、更新纪律（≤ 3 KB）
  sources.md              来源登记：书 / 章 / 印刷页 / 阅读覆盖 / 未读声明；新来源进这里才能被引
  principles.md           A. P1–P8 全文（来源、适用范围、不适用情形、工程意义、实现或验证方式）
  decision-framework.md   B. 数据抽象 → 任务抽象 → derive → 编码 → 颜色 → facet/reduce → 理由三档
  validation-spec.md      C. 规则目录 V-xxx（下文 §3.3 的 schema），与 publication.json 的 check id 一一对应
  core-implications.md    D. 对 Core 模型、编辑、跨语言、可复现的影响；短期 / 长期分开
  non-rules.md            明确「不做成规则」的条目及理由（防伪科学规则）
docs/rules/repo/scientific-figure-review.md
                          ≤ 3 KB 的开发 / 评审问题清单（§6），指向 docs/sciviz/；由根 AGENTS.md 子系统索引点名一行
```

**维护纪律**：
- `validation-spec.md` 的规则 ID 与 `publication.json` 的 check id **一一对应**，由一条测试看护，仿 `tests/test_agents_rules_index.py`：每条 `basis` 不为 `journal` 的预检规则都必须有 spec 条目，反过来也一样。
- 新原则必须先在 `sources.md` 登记来源；**[推导] 不能升格为 [原文]**。

### 3.2 A 原则条目样例（完整写法示范 1 条；其余 7 条同一格式）

> **P1 数据↔视觉一致**
> - **来源与证据**：
>   - FDV 第 1 章 wrong 的定义（p.2–3）[原文]。
>   - FDV 第 3 章「0 can never appear on a log scale」（p.20）[原文]；负值不能上 log 轴是数学事实 [推导]。
>   - FDV 第 17 章「Bars on a linear scale should always start at 0」（p.209）[原文]。
>   - GoG §9.1.8「frame should always cover the data」（p.219）[原文]。
> - **适用**：位置或长度编码的数据标记，包括线、散点、柱、误差线、填充带。
> - **不适用**：
>   - 有意的局部放大，比如 inset 或 zoom 面板，此时 frame 裁剪是用户意图，只能作风险提示。
>   - 柱表示差值、浮动区间或甘特，此时柱底不在 0 是语义本身。
>   - symlog 轴。
> - **工程意义**：worker 拥有活的 artist 数据，可以算出「哪些数据画不出来或被截断」的只读事实，和 `data_fingerprint` 同一路径、同一预算。
> - **实现与验证**：V-001、V-002（§3.3）；数据不变量测试（§4 的 E2）。

### 3.3 C 规则 schema 与分类

**规则 schema**（每条 V-xxx 必填）：ID · 依据类别 basis · 科学依据（标记 + 出处）· 适用条件 · 检测所需信息（逐项标注 manifest 现在**有没有**）· 检测逻辑 · 严重度 · 误报风险 · 修复建议（不自动修的写明为何）· 测试案例（正常 / 边界 / 错误 / 不可判定）。

**四类依据，映射到现有严重度，不新造严重度体系**：

| basis | 含义 | 理论锚点 | 默认严重度 | 出现在哪 |
| --- | --- | --- | --- | --- |
| `encoding`（科学错误） | 图与它代表的数据在数学或编码上不一致，可客观判定 | FDV 的 wrong；GoG 的 frame 覆盖数据 | `warn`（放行，必展示；**不用 error 阻断**，理由见 §7 拍板项 2） | 问题面板 |
| `statistics`（统计风险） | 依赖上下文、可能误导读者的统计表达 | FDV 第 9、16 章；GoG 第 7 章 | `suggestion` | 问题面板 |
| `visual`（视觉建议） | 感知层面的建议，书里有例外 | FDV 的 bad；VAD 第 5、6、10 章 | `suggestion` | 问题面板（可折叠） |
| `journal`（期刊格式） | 期刊或规范明文要求 | 期刊原文（ADR 0155），**不是三本书** | 按 profile（现状） | 问题面板 |
| 不可判定 | 仅凭图或数据判不了，只能请作者确认 | GoG 第 19 章；FDV 第 16 章 | **不出 issue** | 「需要你确认」折叠组（复用 ADR 0155 §14 的现成出口） |
| 偏好 | ugly 或「matter of taste」 | FDV 第 1 章及书里的品味清单 | **不做规则** | `non-rules.md` |

[推导] `basis` 是规则目录的一个新字段，与现有导航类别 `category` 正交。现有 5 条科学规则按它重新归类：
- `bar-without-errorbar`、`fit-without-ci` → `statistics`。
- `discouraged-colormap`、`palette-semantic`、`palette-line-markers` → `visual`。

**首批规则目录**（按价值 × 可行性排序，「输入」一列标出 manifest 今天有没有）：

| ID | 规则 | basis | 依据 | 所需输入（现有？） | 严重度 | 误报风险 |
| --- | --- | --- | --- | --- | --- | --- |
| V-001 | 对数轴上有 ≤ 0 的数据画不出来（含误差线下端 ≤ 0） | encoding | FDV p.20 [原文] + 数学 | 轴尺度 ✓；数据值 ✗，需新的只读事实 | warn | 低 |
| V-002 | 线性轴上柱的基线（共同柱底）不在可见范围内 | encoding | FDV p.209 [原文]；GoG p.92 [原文] | 柱底和高（worker 有，manifest 没有）；ylim/xlim ✓ | warn | 中（差值柱、浮动柱要豁免） |
| V-003 | 数据点落在轴范围之外被裁掉 | statistics | GoG p.219、FDV 第 3 章 [原文] | 数据 ✗；轴范围 ✓ | suggestion | 中（有意放大） |
| V-004 | 有误差线，但图内文字没说明 SD / SEM / CI | 不可判定 → 需要你确认 | FDV p.189 [原文]；GoG p.131 [原文] | 误差线 ✓；含义 ✗；图注 ✗（在稿件里） | 不出 issue | — |
| V-005 | 同一面板里类别色 > 8 | visual | FDV 第 19 章 [原文]；VAD p.226–229 [原文] | 颜色 ✓ | suggestion | 中（连续色映射要排除） |
| V-006 | 小倍数同量纲面板 y 范围不一致 | visual | FDV 第 21 章 [原文] | 轴范围 ✓；「同量纲」✗，只能用轴标签相同作启发 | suggestion | 中 |
| V-007 | 抽象数据上的 3D 轴 | visual | FDV 第 26 章、VAD p.117 [原文] | `axes3d` ✓；是否真三维 ✗ | suggestion | 中高 |
| V-008 | 折线连接类别 x 轴 | visual | VAD p.156 [原文] | 刻度是否文字 ✓（启发） | suggestion | 中 |

现有 5 条照旧，只补 `basis`。

**`non-rules.md` 首批**（明确不做成规则）：
- 折线图或点图的 y 不从 0 起（FDV：允许）。
- 饼图本身（FDV：品味）。
- 网格、外框、端帽、空心点、旋转标签（FDV：品味）。
- 「bin 宽不对」：只能作为探索工具，不能判对错。
- 按 Stevens 指数修正面积（GoG 反对）。
- 双 y 轴：只有 GoG 说一般应避免，FDV 与 VAD 都没讨论。先不做规则，只进决策框架。
- 「图上必须标 n」（三本书都没有这条）。
- 任何 pt 字号阈值，只能来自期刊。

### 3.4 B 决策框架（数据出图的骨架，[推导]，依据 VAD 第 2–7 章 + FDV）

1. **数据抽象表**（每列一行，所有推断字段都标 `inferred`，用户可改）：
   - 类型：categorical / ordinal / quantitative / time。
   - 方向：sequential / diverging（**中点必须由语义确认，不能看到负数就判定**）/ cyclic。
   - key 候选：唯一性检验；数值列默认不当 key（VAD p.34）。
   - 角色：沿用 `ai_data_roles.ROLES`。
   - 单位。
   - **统计身份**：原始观测 / 汇总值 / 误差量（SD、SEM、CI 或未知）。

   最后一项是新增的。
2. **任务抽象**：present 还是 discover；compare / summarize / identify；target 取 trend / distribution / correlation / outlier / part-to-whole。**任务是输入或确认项，不由推荐器自己定**（VAD p.78）。
3. **Derive 方案**：差值、比值、归一化、log、分箱、聚合。每个都记录来源列、操作、**丢掉了什么**（VAD p.50–53、第 13 章），以及是否需要用户确认。
4. **编码候选**：按（key 数、value 数、key 类型、任务）查表，再按 expressiveness / effectiveness 分配通道，检查 discriminability（取值数不超过可分档数）。
5. **颜色、分面、聚合**：
   - 类别 ≤ 约 8 个；有序数据用亮度单调的色图；发散色图的中点要有语义。
   - 叠加层 ≤ 2–3 层，再多就改小倍数。
   - 过绘时做聚合，并标明丢掉了什么。
6. **理由三档**：
   - (i) 数据事实：「x 是有序的时间列」。
   - (ii) 感知依据：「位置比面积准确」，标明是指导性原则。
   - (iii) 用户确认的任务：「比较组间均值」。

   三档分开显示或存档，**不合成一个「置信度」**。

现有闭集配方（`FAMILIES` / `MARK_RULES` / `REJECTIONS`）已经是第 4–5 步的工程实现。框架要补的是第 1 步的「统计身份」、第 3 步的「丢掉了什么」和第 6 步的分档，不是推倒重写。

### 3.5 D 对 Core 架构的影响

**短期**（不改架构，沿现有路径加字段）：

- 在 manifest 里加**只读语义事实**，和 `data_fingerprint` 同一模式：worker 端从活 artist 算出来，不进 `editable`、不进文档、有预算。首批两个：
  - 每根轴的「画不出来的数据数」。
  - 柱组的「共同基线值」。
- 编辑按 GoG 三分打标签（样式 / 结构 / 数据），放在 `HANDLERS` 的登记旁，用来：
  - 定一条测试不变量：样式编辑后所有数据指纹都不变（§4 E2）；
  - 给尺度或范围编辑配上 V-001 / V-002 的即时预检。
- 数据出图的配方从「脚本头注释」升级为**可读的统计元数据**：解析结果进项目的图快照或回执，预检和「需要你确认」都能读到。比如对配方生成的图，V-004 可以直接显示「误差线为 SD」这一事实，不必问用户。

**长期方向**（只给方向，不排期，每一步都要单独 ADR）：

- **语义侧车，不替换 manifest**：给元素附一个可选的 `semantics` 块，按 GoG 分层（data / stat / scale / coord / geom / aesthetic / guide），每层的值可以是「已知 + 来源」或 `unknown`。
  - matplotlib 路径大多数层是 `unknown` 或来自脚本静态信息。
  - ggplot2 原生路径（#2067 的后续）天然带 stat 与 scale 层，是第一个「实层」消费者。
  - SVG 的 `ChartModel` 带数据层。
  - 统一的是**语义描述**，不是渲染器。三种生态继续用各自的渲染器，这与 GoG「语法 ≠ 实现」（p.24、p.633）一致。
- **schema 版本**：语义侧车一出现就带显式版本，沿用 `chartmodel` 的 `name/major` 写法，不再走「老引擎不发」的隐式演进。
- **可复现**：导出可选嵌入来源信息（脚本哈希、数据哈希、配方 / 统计元数据），涉及隐私说明，要拍板；回执的输入观察逐步覆盖 pandas。
- **不做**：
  - 重写渲染或 manifest。
  - 从渲染结果反推统计（Reader 路线，GoG 第 19 章自己说不可复原）。
  - 把 matplotlib 的调用强行翻译成 GPL 风格的规格。

---

## 4. 按优先级排序的工程改进

| 序 | 改进 | 性质 | 风险 | 依赖或拍板 |
| --- | --- | --- | --- | --- |
| E0 | 建 `docs/sciviz/`（A–D + non-rules + sources）和评审问题清单 | 文档 | 无 | 目录位置、AGENTS.md 一行（判据路径，需你点头） |
| E1 | **MVP：V-001 对数轴非正值**（§5） | 新判据 | 低 | 1.0 豁免 |
| E2 | 数据不变量测试：每个样式类 `HANDLERS` 键在真 figurebench 图上应用后，所有元素的 `data_fingerprint` 不变；尺度类键应用后指纹也不变（尺度不改数据），但 V-001 / V-002 的事实可以变 | 只加测试 | 极低 | 新增核心不变式测试，提交前手工反证一次（1.0 纪律） |
| E3 | V-002 柱基线截断（复用 E1 的事实通道） | 新判据 | 低–中 | 豁免（同族） |
| E4 | 规则目录加 `basis`；5 条旧规则重新归类；spec↔check 一一对应的看护测试 | 目录与 UI 分组 | 低 | 问题面板按依据分组要你看截图 |
| E5 | 配方统计元数据可读化，V-004 走「需要你确认」或直接显示事实 | 数据出图 | 中 | 与 #2097（误差列）衔接；文案拍板 |
| E6 | `log_nonpositive` 等配方判据与预检共用一份谓词（单一权威），在 `same-origin-pairs.md` 记一行 | 重构（小） | 低 | E1 之后 |
| E7 | 推荐理由结构化（三档 + 适用条件 + 丢掉了什么） | 数据出图 | 中 | UI 拍板（不许堆文案） |
| E8 | V-005 至 V-008 视觉建议 | 新判据 | 中 | 逐条看误报率再决定 |
| E9 | 语义侧车与 schema 版本；ggplot2 stat 层接入 | 架构 | 高 | 单独 ADR |
| E10 | 导出嵌入来源信息 | 隐私与格式 | 中 | 隐私说明 |

---

## 5. 最小可行验证（Phase 3 设计，**尚未实现**）

### 5.1 为什么选 V-001

- **客观**：log(x ≤ 0) 无定义，无需上下文。
- **误报低**：只数有限值，NaN 和掩码值不算。
- **真实**：matplotlib 静默把这些点画到轴外（§2.4 实测）。生物和化学图里「均值 − SD < 0」画在 log 轴上的误差线下端很常见。
- **输入就在手边**：worker 已经为指纹遍历过同一批数组（`_fingerprint_parts`）。
- **同时覆盖编辑引入的问题**：用户在检查器里把 y 轴切成 log，重放后事实立即出现。
- **完全复用**：预检 sink、`publication.json` 的三张表、`RULES`、golden 向量、问题面板、导出报告，都按 id 自动联动。
- **不碰**：写回事务、override 模型、safe_auto（`fix: none`，**不自动修**；改 symlog 或删点是科学决策）。

### 5.2 设计

1. **manifest**：给 axes 元素加只读事实 `scale_domain`。只在某根轴的尺度是 `log` 时才出现，形如 `{"y": {"nonpositive": 2, "errorbar_low_nonpositive": 1, "status": "ok"}}`。
   - 计数源是该轴上可见数据元素的有限值：线 `xydata`、集合 offsets、误差线段端点、柱的底与顶、填充带顶点。
   - 预算与 `FINGERPRINT_BUDGET` 共用；量不了时写 `status: "over_budget" | "unavailable"`，规则静默不判。
   - twin 轴按各自轴归属计数。
   - 同步 `web/src/lib/api.ts` 的 `ManifestElement`。
2. **规则** `log-axis-nonpositive`：
   - 写进三张 severity 表，均为 `warn`，`basis: encoding`，`fix: none`。
   - 文案一句：「对数轴上有 N 个 ≤ 0 的值画不出来」，详情放 title。
   - Python 和 TS 两侧求值器同名实现，golden 向量对拍，**不重蹈干涉三条只在单侧的覆辙**。
3. **不做**：
   - 颜色的 LogNorm（色条上的非正值）留给后续。
   - symlog 与 logit 不判。

### 5.3 测试案例与预期

| 类型 | 案例 | 预期 |
| --- | --- | --- |
| 正常 | `plot([1,10,100])`，y 为 log | 无 issue |
| 错误 | `plot([0,-1,10,100])`，y 为 log | 1 条 issue，N=2，gid 指向该轴 |
| 错误 | `errorbar(y=[1,2], yerr=[1.5,0.5])`，y 为 log（下端 −0.5） | 1 条 issue，`errorbar_low_nonpositive=1` |
| 边界 | 有 NaN 和掩码值，其余全为正 | 无 issue（只数有限值） |
| 边界 | symlog 轴上有负值 | 无 issue |
| 边界 | 被隐藏的 artist（`visible=False`）上有 0 | 无 issue |
| 边界 | 左轴线性含 0，`twinx` 右轴为 log 且全正 | 无 issue（按轴归属） |
| 边界 | 数值总量超出预算 | 无 issue，`status=over_budget`，不冒充判过 |
| 编辑 | figurebench 线图含 0，override `yscale→log` | 出现 issue；撤销 override 后消失 |
| 真图防误报 | `tests/fixtures/figurebench/log_decay`（真 worker；y 为 log 且全正，x 含 0 但是线性轴） | 无 issue |
| 对拍 | golden 向量 Python 与 TS 同结果 | `id / severity / gids / detail` 一致 |
| 反证 | 去掉规则分支或事实字段 | 上面的「错误」与「编辑」行转红（提交前手工做一次并记录） |

### 5.4 验收标准

- 上表全部通过；`ruff`、针对性 pytest、`pnpm test && pnpm build`、golden 生成脚本 `--check` 都通过。
- 不选期刊、不触发时，预检输出和导出产物与改动前**逐字节相同**；golden 中已有的 67 例结果不变。
- manifest 体积与渲染耗时的增量：在 figurebench 上测量并写进 PR；超过 2% 要说明。
- 问题面板只多一行，不撑大任何卡片，不加横条。
- `validation-spec.md` 的 V-001 条目与 check id 有看护。

---

## 6. 长期集成：如何在日常开发中持续生效（Phase 4）

不另起流程，挂到仓库已有的四条链上：

1. **加载链**：根 `AGENTS.md` 的子系统索引加一行，指向 `docs/rules/repo/scientific-figure-review.md`（≤ 3 KB，受 `agents_budget.py` 约束）。改到下列路径时读它：
   - `manifest.py`、`overrides.py`、`preflight.*`、`publication.json`、`ai_data_*`、`chartmodel.py`；
   - 导出管线；
   - 原生 ggplot2。
2. **七个评审问题**，即用户提出的七问的落地版。每问对应一条可测的东西，答不上就写「不适用」：
   1. 改动后数据 ↔ 视觉还一一对应吗？数据指纹或 V-001 / V-002 有测试吗？
   2. 会不会引入静默丢点、截断，或「统计看起来像重算过」？
   3. 图型与数据类型、任务匹配吗？依据是 [原文] 还是 [推导]？
   4. 自动操作有没有替用户定科学假设（SD 还是 SEM、归一化、配对、剔除）？定了的话，响应里写明了、用户能改吗？
   5. 数据、派生、统计、参数可追溯吗？配方或统计元数据有没有丢？
   6. 现有 matplotlib 工作流和可编辑能力还在吗？不变式测试与 golden 绿吗？
   7. 能自动测试吗？不可判定的部分走「需要你确认」，没有冒充判过吗？
3. **Codex 评审口径**：在根 `AGENTS.md` 的 Review guidelines 的 P1 定义里加一句：「静默错图」包括数据被丢、被截断而无提示。这是判据路径，**要你点头**。
4. **回归**：
   - spec ↔ check 一一对应的测试；
   - E2 数据不变量测试进 `test_invariants_engine` 族；
   - 每条新规则都带真 worker 防误报用例（仿 `tests/test_preflight_min_line_width_real.py`）。
5. **错题本**：科学可视化相关的返工（误报、被用户驳回的伪规则）记进 `docs/rules/repo/lessons/`，按现有 3 日升级机制沉淀到 `non-rules.md` 或 spec。
6. **Claude Code 会话**：
   - 需求分析阶段，按 §3.4 的框架先写出数据与任务抽象，再讨论图型。
   - 架构阶段，凡是动模型，就对照 §3.5 写明它属于短期还是长期。
   - 实现阶段，引用 V-ID。
   - 评审阶段，回答七问。

---

## 7. 需要你拍板的事

1. **1.0 豁免**：V-001（以及同族的 V-002 / V-003）属于扩大产品能力，按 `docs/1.0-release-readiness.md` §1.8 要登记豁免。是否给？
2. **科学错误类的严重度**：建议用 `warn`（放行，必展示）而不是 `error`。理由：预检的 error 默认阻止导出，而你定过「所有用户当小白，失败不许卡住」；这两条也存在有意的例外（比如对数轴上故意画出 0 的边界）。
3. **问题面板按「依据」分组**（错误 / 统计风险 / 视觉建议 / 期刊）：涉及界面，先出浅色与深色截图给你看，再动。
4. **误差线含义（V-004）进「需要你确认」**：
   - 与 ADR 0178「图里不写脚注」不冲突：这一项只出现在问题面板末尾，不写进图里，不计数，不报警。
   - 文案需要你签字。
   - 对配方生成的图，是直接显示「误差线为 SD」这一事实，还是什么都不显示？
5. **知识库位置**：`docs/sciviz/`，加上根 `AGENTS.md` 一行和 Review guidelines 一句。后两处是判据路径。
6. **双 y 轴**：只有 GoG 一本书反对。建议不做规则，只进决策框架；你是否同意？
7. **版权口径**：仓库内只放转述、≤ 25 词的引文和页码，原书 PDF 不入库。

## 附：本方案没有做的事

- 没有改任何产品代码，没有推送。
- Phase 3 只完成了设计与测试表，没有实现。
- 三本书没读的章节已在 §0 列出；那些章节的结论本文不引用。
