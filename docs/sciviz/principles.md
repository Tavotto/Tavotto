# A. 科学可视化原则（P1–P9）

每条原则按同一格式写：来源与证据 · 适用 · 不适用 · 工程意义 · 实现与验证。证据标记见 [README](README.md)。
原则本身都是 **[推导]**，即从三本书交叉提炼出来的；依据里每一条单独标明来源。

### P1 数据与视觉一一对应

- **来源与证据**：
  - FDV 第 1 章 wrong 的定义：「objectively incorrect」（p.2–3）[原文]。
  - FDV 第 3 章：「The value 0, on the other hand, can never appear on a log scale.」（p.20）[原文]。负值同理，这是数学事实 [推导]。
  - FDV 第 17 章：「Bars on a linear scale should always start at 0.」（p.209）[原文]。
  - GoG §9.1.8：「the frame should always cover the data on which the graphs are based」（p.219）[原文]。
- **适用**：用位置、长度、面积编码的数据标记，包括线、散点、柱、误差线、填充面积、饼块。
- **不适用**：
  - 有意的局部放大（inset），这时只是风险，不算错误。
  - 差值柱、浮动柱、甘特图，柱底本身有含义。
  - symlog 与 logit 轴。
- **工程意义**：
  - worker 持有活的 artist，可以算出「哪些数据画不出来、哪些被截断」这类只读事实。
  - 和 `data_fingerprint` 走同一条路径、共用同一份预算（`engine/manifest.py`）。
- **实现与验证**：
  - V-001、V-002、V-003（[validation-spec](validation-spec.md)）。
  - Agent 准则 G-001、G-002、G-004、G-009、G-035。

### P2 统计语义不能从几何推出

- **来源与证据**：
  - GoG §7.1：统计与几何分离，「to separate form from function」（p.120）[原文]。
  - GoG §7.2：「The shape of the bars is arbitrary.」（p.131）[原文]。
  - GoG 第 19 章：没有元数据，就分不清独立组和重复测量（p.612）[原文]。
  - FDV 第 16 章：误差线必须说明代表什么（p.189）[原文]。
- **适用**：误差线、误差带、汇总线、平滑线、归一化、分箱。
- **不适用**：Tavotto 自己生成的统计，例如数据出图的配方。这时含义是已知事实，应当直接展示，不必再问。
- **工程意义**：
  - 拿不到含义时明确说「不知道」，不猜。
  - 问题走「需要你确认」，不出 issue。
- **实现与验证**：
  - V-004。
  - G-005、G-006、G-008、G-016、G-038。
  - 数据出图的默认 `mean_sd` 默认列入「需要你确认」，并提供 SD / SEM / 95% CI 一键切换（维护者 2026-11-21 定）。

### P3 变换分层：尺度或范围的编辑不会重算统计

- **来源与证据**：
  - GoG §6.2：变量变换与尺度变换先于统计，坐标变换后于统计（p.93）[原文]。
  - GoG §9.1：数据缩放与图形缩放不同（p.191–192）[原文]。
  - GoG §9.1.8：均值的 log 不等于 log 的均值（p.218）[原文]。
- **适用**：检查器里的 `xscale` / `yscale` / `xlim` / `ylim` 编辑；AI 改图。
- **不适用**：
  - 纯坐标操作，例如翻转、纵横比、交换 x 与 y。按 GoG 的说法，这些不改变统计。
- **工程意义**：界面不能暗示「已经重算过」。轴切成 log 之后，原来对称的误差线不再对称。
- **实现与验证**：
  - V-001 覆盖用户把轴切成 log 的情形（编辑用例）。
  - G-009、G-010、G-011。

### P4 编辑分三类：样式 / 结构 / 数据

- **来源与证据**：
  - GoG 第 18 章 ViZml 的数据 / 结构 / 样式三层（p.589–609）[原文]。
  - GoG §10.5：「size for area is a data attribute, not an arbitrary value we may change for aesthetic purposes」（p.278）[原文]。
- **适用**：所有 `HANDLERS[(family, prop)]` 编辑，以及画布矢量编辑。
- **不适用**：画布上的标注，它们本来就不是数据（GoG 第 12 章：页面坐标的绘图工具）。
- **工程意义**：
  - 现在的能力门控已经是这一条的工程实现：不开放挪柱子，数据坐标的曲线也不宣称可拖（`engine/overrides.py`、`engine/manifest.py`）。
  - 缺的是一条测试不变量：样式编辑后，数据指纹不变。
- **实现与验证**：
  - 数据不变量测试：样式类与尺度类键应用后，所有元素的 `data_fingerprint` 不变（研究方案 E2）。
  - G-036。

### P5 先定数据类型与任务，再定图型

- **来源与证据**：
  - VAD §2.2：语义与类型正交（p.22）[原文]。
  - VAD §2.6.1：key 与 value 的区分可能就是分析目标（p.36）[原文]。
  - VAD §3.4.2.3 derive：「Don't just draw what you're given」（p.50–53）[原文]。
  - VAD 第 5、7 章：expressiveness、effectiveness，按 key / value 数量分流（p.100–102、p.145–174）[原文]。
- **适用**：数据出图、Agent 选图、改图建议。
- **不适用**：用户已经明确指定了图型。这时照做，只在有 `encoding` 类问题时提醒。
- **工程意义**：
  - 推荐的是「派生数据 + 编码」，并附上适用条件和丢掉了什么。
  - 任务由用户确认。
- **实现与验证**：
  - [决策框架](decision-framework.md)。
  - G-012 至 G-030、G-037、G-040。

### P6 理由分三档证据

- **来源与证据**：
  - VAD 第 4 章：抽象层的有效性只能由目标用户「doing their own work」来验证（p.78）[原文]。
  - VAD 第 4 章：idiom 层按原则论证，只算即时证据（p.77）[原文]。
  - VAD 第 5 章：通道排序是作者「my own synthesis」（p.102）[原文]。
- **适用**：推荐理由、候选卡片上的说明、Agent 的解释。
- **不适用**：—
- **工程意义**：
  - 三档分开写：数据事实（可核验）、感知依据（指导性）、用户确认过的任务假设。
  - 不合成一个置信度；不说某种图「被证明最优」。
- **实现与验证**：
  - G-039。
  - 数据出图跑分用例的 `guidance` 字段，与 ADR 0178 那条线约定。

### P7 错误、风险、偏好分开；判不了的不冒充判过

- **来源与证据**：
  - FDV 第 1 章：ugly 比 bad、wrong 更主观，ugly 与 bad 的边界「somewhat fluid」（p.2–3）[原文]。
  - FDV 全书有一份自称「matter of taste」的清单，见 G-042 至 G-050 [原文]。
  - VAD §1.11：「few absolute truths」（p.13）[原文]。
  - GoG 第 1 章：「not about good taste, practice, or graphic design」（p.1）[原文]。
- **适用**：所有规则与建议。
- **不适用**：—
- **工程意义**：
  - 品味条目只给 Agent 作参考，永不进入预检。
  - 量不了时记为 unknown：不出 issue，也不显示「已通过」。
- **实现与验证**：
  - `basis` 闭集。
  - `tests/test_sciviz_guidance.py::test_taste_entries_never_become_checks`。

### P8 可追溯、可复现

- **来源与证据**：
  - FDV 第 28 章：脚本化、可重复；随机元素要记录种子（p.325–331）[原文]。
  - GoG 第 15 章：integrity 是让评判者能判定质量的信息，常包括审计轨迹（p.452）[原文；第 15 章只读了开头]。
- **适用**：数据、派生、统计方法、绘图参数、手工修饰。
- **不适用**：—
- **工程意义**：
  - 脚本优先，手工修饰可以重放：写回事务和导出 `_edited.py` 已经做到。
  - 缺的有两样：
    - 统计元数据在下游可读（配方现在只写在脚本注释里）；
    - 导出产物里的来源信息。
- **实现与验证**：
  - [core-implications](core-implications.md) 的短期第 3 条与长期部分。
  - G-016、G-017、G-034、G-037。

### P9 组图：面板的顺序、分组、主次、对齐与共享

- **来源与证据**：
  - FDV 第 21 章区分两种组图：小倍数是「multiple panels arranged in a regular grid」（p.255），复合图是「assembled in an arbitrary arrangement」（p.255）[原文]。
  - 同一种量并排比较，各面板用相同的轴范围：「it is important that each panel uses the same axis ranges and scaling」（p.257）[原文]；图 21-3 因各面板 y 范围不同被作者标成「bad」（p.258）[原文]。
  - 面板顺序：「Always arrange the panels in a small multiples plot in a meaningful and logical order.」（p.260）[原文]；Trellis 的 main-effects 排序是按派生量（中位数）排序，让总趋势与离群一眼可见（VAD p.282–283）[原文]。
  - 对齐：「The axes and other graphical elements of the individual panels should all be aligned to each other.」（p.264）[原文]；对齐的小倍数用于「comparison with the highest precision」（VAD p.273）[原文]，对齐比不对齐「higher-precision comparison」（VAD p.278）[原文]。
  - 复合图的字母标注：约定用拉丁字母、从左上起按行排（FDV p.260–261）；标注「should not be the first thing you see」，作者把它比作页码（p.261）[原文]。小倍数不需要字母，因为分面变量已经标明了每个面板（p.260）[原文]。
  - 视觉语言一致：同一对象在各面板颜色、顺序一致，图例只留一份（FDV p.261–263）[原文]。
  - 书里**没有**的：面板的主次、大小或跨格（FDV 第 21 章不讨论；VAD §12.3.2 的大小窗口是交互式 overview–detail，不能当作静态组图的依据）；同一组放行还是放列；「省去重复轴标签」。这几处下面标 [推导]。
- **适用**：
  - 多面板的组图：一键排版的图内排版（ADR 0199）、Agent 拼图、预检。
  - 小倍数与复合图两种都适用，但字母标注只对复合图适用。
- **不适用**：
  - 各面板的量纲或数量级本来就不同：FDV 对不同轴范围的态度是「generally recommend against」；确实避免不了时「at a minimum」要在图注里说明（p.259）[原文]。「不同量纲就不必统一范围」是 [推导]（见 G-027 的例外）。
  - 面板之间没有比较关系，只是并列的独立结果：对齐与同尺只管有比较关系的那几组。
  - 用户或期刊已指定顺序与版式：照做。
- **工程意义**：
  - **谁来定意图**：组图的目的（谁与谁比较、谁是主结果、叙事先后）是作者的目的，从图和数据里看不出来 [推导，同 P6]。Agent 依据轴标题、图型、数据类型和用户的话判断；说不清时按 [决策框架](decision-framework.md) 第 2 节出选择题，不自己猜。求解器只落实 Agent 给的 `intent`（`order` / `compare` / `emphasis`），自己不做判断。
  - **主次**：主结果可以通过面板大小与位置体现 [推导]；这是 Tavotto 的设计取舍（ADR 0199 §十三：最多占 2 × 2 格、不超过整版一半），不是书里的结论。
  - **不改数据范围**：同一 `compare` 组的 y 范围不一致，只按 V-006 提示，不替用户改 `ylim`（对齐靠排版，不靠改数据；P3、P4）。
  - 排版层只动位置与大小，不动数据，属于 P4 里的「结构 / 样式」类编辑。
- **实现与验证**：
  - ADR 0199 §十三（修订草案 #2137）：`intent` 的核对与退回规则。
  - V-006（小倍数面板 y 范围不一致，待评估）。
  - Agent 准则 G-027、G-059、G-060、G-061、G-062；写法建议 G-058（一张图回答一个问题、各面板分担不同的论证，`taste`）与本条互补。
