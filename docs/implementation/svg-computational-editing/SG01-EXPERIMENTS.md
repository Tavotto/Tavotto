# SG01：前置实验记录与架构决定

> 状态： experiments done（六个实验有可运行证据）→ 决定见 ADR 0181。
> 实验代码与产物在任务隔离目录 `E:\0 - Project\svg-exp\`（仓库外，不进产品树）；
> 本文档记录可复核的数字与结论。运行环境：实施 venv（matplotlib 3.11.2 / pypdfium2 / playwright+chromium）。

## 0. 三个候选（计划 §3.1）

| 候选 | 内容 |
| --- | --- |
| C1 | 声明式模型全部生成 matplotlib Figure（全图重画） |
| C2 | 保留 SVG 场景，绑定区域用现有计算内核重算，替换对应区域 |
| C3 | 原生 SVG 场景直接承担全部科学计算（自写 tick/scale 规则） |

## 1. 实验一：零编辑保真（exp1_rebuild_drift.py）

- C2：零编辑 = 原始字节不动（按构造成立，无需渲染）。
- C1：同一组数据、同一 figsize 重建整图（无原脚本布局引擎），axes 矩形漂移
  **(6.56, 4.08, 18.64, 10.32) pt，最大 18.6 pt ≈ 7.3 mm**——原语料用了 `tight_layout`，
  重建没有它，标题/图例/边距全部重排。结论：**C1 对外部 SVG 的外观是重画，不是保真**；
  对「可信 Figure 来源」的路径 A，正确形态是复用既有 live-Figure 编辑（本来就有全保真），不需要 C1 式重建。

## 2. 实验二：脚手架计算正确性（exp2_scaffold.py，158 项断言全过，exit 0）

worker 侧「脚手架」= 一个只有绑定 axes 的 matplotlib Figure（1 unit = 1 pt），
复用 matplotlib 的 locator/formatter/scale（不另写刻度规则），产出 JSON 事实：
刻度（值/轴分数/标签文字/mathtext 标记）、次刻度、offset text、系列逐点轴分数。

- 线性 [0,10]、窄域 [2,8]、log-y [50,100]、倒轴、固定刻度+格式：**每个刻度值与数据点的轴分数与独立公式一致到 1e-9**；
- log 轴下 y≤0 的数据点：脚手架给出 <0 的分数（出界，由裁剪处理）——公式口径同步覆盖；
- 确定性：同输入两次计算 JSON 逐字节相同；
- 语料闭环：解析 `line_two_series.svg` 自身的折线坐标反推数据，与 CSV 及公式一致（校准正确性）。
- **副产物事实**：matplotlib 默认 margins=5%——语料真实范围是 [-0.5,10.5]/[-5,105]，绑定校准必须用真实范围；
  log 轴刻度标签是 mathtext（`$\mathdefault{10^{2}}$`）→ 纯 JSON 文本回填会丢字形 → 见实验三的「片段嫁接」。

## 3. 实验三：区域替换 + SVG→IR→PDF 导出（exp3_graft_export.py，15 项断言全过，exit 0）

**架构精化：片段嫁接（fragment graft）**。worker 把绑定 axes 按模型重算后**直接渲染成 SVG 片段**
（figsize=面板 pt、axes rect 与原场景一致 → 片段坐标与场景坐标系 1:1 对齐），父进程只做节点级替换：

1. 零编辑对照：同 rect 同范围嫁接，几何与公式一致（对齐无误）；
2. 编辑 xlim [0,10]→[2,8]：替换 2 个 axis 子树 + 2 个系列组；
   编辑后折线点**全部落在新范围的独立公式位置**（matplotlib 自带视区裁剪，出界点在 [0,1] 外）；
   刻度标签自动变为 2..8；
3. **未绑定内容逐字节保留**：标题、图例、轴外全部文本内容前后一致；
4. 脚手架字节确定性（`svg.hashsalt` 固定 + 元数据时间戳剥离）；
5. **SVG→IR→PDF**：edited.svg → IR（翻译器子集：path/text/use/rect/clipPath/transform/dash/opacity）
   → 现有 `pdfwriter.write_pdf` → pypdfium2 渲染：页面尺寸一致、文本层含新刻度 2..8 与标题图例、有墨；
6. **独立渲染器对照**：Chromium 截图 edited.svg vs PDFium 渲染 edited.pdf：
   `changed(>24)=1.74%`，`mean_abs_diff=1.13`，ink IoU **0.848**（逐目视：标题/刻度/图例/虚线/marker/裁剪一致；残余为抗锯齿与字形栅格差）。

**实验揪出的四个结构性契约**（全部进了 ADR/后续批次）：

| # | 事实 | 契约 |
| --- | --- | --- |
| 1 | matplotlib SVG 的 title/legend 在 `axes_1` **子树内部** | 区域所有权必须是**子区域级**（axis 子树+系列组），整树替换会静默重画装饰 |
| 2 | 图例（未绑定）引用被替换系列组**内部**的 marker `<defs>` | 替换前必须把被外部引用的 defs **上提**到根 defs（跨区域 def 生命周期） |
| 3 | matplotlib 3.11 把 clipPath defs 放在 `<svg>` 末尾（多个 defs 元素） | defs 收集要遍历全部根级 defs，不能只取第一个 |
| 4 | clip-path 挂在**叶子** path 上；log 刻度标签是 mathtext | 翻译器支持叶级裁剪；**片段嫁接**让 mathtext 标签由 matplotlib 自己渲染（回退 JSON 文本会丢字形） |
| 5 | 系列 marker 由 `<use>` 引用 defs + x/y 位移 | use→IR 需带 fill 与位移（实验中修） |

**SVG→IR 翻译器能力边界**（本实验子集，逐项诚实）：native=路径(M/L/H/V/C/S/Q/T/Z)、rect/circle/ellipse/line/poly、
任意路径裁剪（Group.clip）、组/叶 opacity、dash、text（批准字体+HarfBuzz 整形，旋转经 Group 变换）；
unsupported=渐变/mask/filter/pattern/弧命令（报错点名元素，不静默降级）。matplotlib 自产 SVG 不含后者 → 全支持。

## 4. 实验四：冷恢复（exp4_cold_and_meta.py，6 项断言全过，exit 0）

- 包 = `model.json`（闭式 schema）+ `scene.svg` + `data.csv`，**无任何可执行源**；
- 全新进程 `python -I`（隔离用户 site、无原脚本、无 TAVOTTO 环境）读包重算同一编辑：
  **冷片段与热片段逐字节相同**；
- 结论：声明式模型 + 片段重算的冷恢复成立；计算环境身份（matplotlib 版本）进 SG02 的重放合同。

## 5. 实验五：同一文件两实例不同编辑（分析结论，无新机制）

模型编辑是**文档补丁**（同 PanelObject.overrides 形态的全量列表语义），走既有 `documentStore`
commit/undo/版本时间线；不存在第二套撤销栈或独立编辑器。两实例并发语义与既有面板完全一致
（保存代际 + 项目代际挡串项目；后提交者基于自己看到的基线，冲突由版本历史承载）。
SVG 面板不引入新的并发机制——这一条在 SG10 集成时用既有 e2e（STATE-08 等）追验。

## 6. 实验六：恶意输入（同上脚本，全过）

- `external_danger.svg`（DTD+ENTITY、`<script>`、外链 `<a>`/`<image>`）被 `svgcheck.parse_svg` 拒绝（`svg_dtd_forbidden` 等）；
- 元数据闭式 schema：字段集之外的一切键（含 `transform: "import os; ..."`）被拒绝——**按字段集判，不按值判**；
  与既有边界一致：导入不执行、本地 hash 只证一致性不证可信（ADR 0181 §安全）。

## 7. 架构决定（详见 ADR 0181）

**采用 C2 精化形态「片段嫁接」**：
- 路径 A（可信 Figure 来源）：**复用既有 live-Figure 编辑管线**（零新计算），SVG+回执只是来源身份与预览面；
- 路径 B（普通 SVG + 绑定）：SVG 场景保留为底，绑定区域由 worker 脚手架按模型重算成**坐标对齐的 SVG 片段**，
  父进程做节点级替换 + defs 上提；导出经 **SVG→IR 翻译器**（新 `rendercore/` 模块，纯 stdlib+现有依赖）走既有 pdfwriter/svgwriter；
- 路径 C（保存重开）：模型+绑定+场景+数据为自包含包，冷重放零执行（实验四）。
- C1 否决（实验一：7.3mm 级布局漂移）；C3 否决（计划禁令 + 实验二证明复用 locator 才有正确刻度）。
