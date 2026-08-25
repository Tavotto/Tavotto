# Inspector 重构：从「Matplotlib 参数表」到任务驱动的属性检查器

> 状态：实施中（分支 `inspector-redesign`）。
> 修改前截图：`docs/ux/img/inspector-redesign/before/`；修改后截图：`docs/ux/img/inspector-redesign/after/`。
> 截图全部来自真实应用 + `examples/figures` 示例项目（Fig1_kinetics），非手写数据。

## 一、修改前审计

### 1.1 审计方法

- 通读 `Inspector.tsx` / `ElementInspector.tsx` / `TextStyleBar.tsx` / `TextSection.tsx` /
  `PanelSection.tsx` / `StrokeSection.tsx` / `ArrangeSection.tsx` / `TransformSection.tsx` /
  `CanvasPage.tsx` / `roles/registry.ts` / `elementWrite.ts` / `uiStore.ts` / `actions.ts` /
  `QuickEdit.tsx` / `quickEditStore.ts`，以及 `engine/manifest.py` 的字段发射端与全部相关测试。
- 真实运行应用（`--figures examples/figures`），在 1024×768 / 1280×720 / 1366×768 /
  1440×900 / 1920×1080 五档、中英文两种界面下，逐个选中：面板、图内标题、曲线、
  图例、子图、刻度组、多选两个面板，共 23 张修改前截图（见 before/ 目录）。

### 1.2 修改前的真实状态（逐张对照）

| 截图 | 状态 | 观察 |
|---|---|---|
| `zh-1440-panel-selected` | 面板选中 | 首屏只有「图幅 80×57.6mm」+ 折叠的 背景/源文件/高级 |
| `zh-1440-title-selected` | 标题选中 | 顶部 `[衬线 ▼][9 pt]` **无可见标签**；对齐藏在齿轮弹层里 |
| `zh-1440-line-selected` | 曲线选中 | 名称/颜色/线宽/线型/透明度可见，但 **marker 全部折叠在「线条与标记」里**；线型是文字下拉「实线」 |
| `zh-1440-legend-selected` | 图例选中 | 位置是文字下拉「右下」；列数/间距/标题折叠在 样式/布局 |
| `zh-1440-axes-selected` | 子图选中 | 首屏是「子图占比 0.125 / 0.11 / 0.775 / 0.77」**四个裸分数**；数据范围/刻度线/网格与边框全部折叠 |
| `zh-1440-ticks-selected` | 刻度组选中 | 字号/颜色/旋转可见；**主刻度模式与间距折叠在「刻度定位」里** |
| `zh-1366x768-title-selected` | 1366×768 | **左树开着时右栏整个不存在**——从树里选中标题后没有任何属性出现 |
| `zh-1440-multi-select` | 多选两个面板 | 汇总 + 对齐工具条（这部分是好的） |
| `en-1440-title-selected` | 英文 | `[Serif ▼][9 pt]`，同样无 Font/Size 可见标签 |

### 1.3 问题清单与代码根因

**P1 — 1366×768 下左右栏互斥，选中元素后看不到属性。**
根因：`uiStore.ts` 的 `WIDE = 1440` + `exclusive(s) = s.layout !== 'wide'`；
`toggleLeft/toggleRight/railClick/setLeftTab/setRightTab` 在非 wide 下互相顶掉。
1366×768（最常见的笔记本分辨率之一）落在 `medium`，左树与右属性栏物理上不能共存；
从元素树点选 gid 只写 `selectedGids`、不触发 `autoShowProperties`，于是出现截图里
「选中了标题、右侧什么都没有」的状态。用户被迫在「找对象」与「改属性」之间来回开关侧栏。

**P2 — 字体与字号没有可见标签。**
根因：`TextStyleBar.tsx` 把 `fontfamily` 的 Select 与 `fontsize` 的 NumberField 挤进一行，
只有 `ariaLabel`。代码注释写明这是 296px 宽度约束的直接后果
（「这一行宽度是 296–320px 定死的……英文下要 321px」）。

**P3 — 右栏 296–320px，可调范围只有 24px。**
根因：`RIGHT_MIN = 296` / `RIGHT_MAX = 320`。这个宽度装不下「标签 + 控件」的
专业检查器排版，是 P2 与大量嵌套弹层的共同上游。

**P4 — 高频属性折叠，折叠判据是「值是否偏离中性默认」。**
根因：`roles/registry.ts` 的 `groupHasContent` + `ElementInspector.tsx` 的
`openGroups[name] ?? auto`——脚本没动过的属性组默认收起。后果（见截图）：
曲线的 marker、子图的数据范围/刻度四边/网格、刻度组的定位模式、图例的列数，
全部要先展开一层才能看到。且 `openGroups` 是挂在面板组件上的 `useState`，
**换一个面板全部归零**，用户偏好不被记忆。

**P5 — enum 无条件渲染成文字 Select。**
根因：`ElementInspector.tsx` `FieldRow` 的 `case 'enum'` 一条路。线型（`-`/`--`/`:`/`-.`）、
marker（`o`/`s`/`D`/`^`…）、hatch、colormap 名、图例位置全部是文字下拉，
用户必须先在脑子里把 Matplotlib 编码翻译成视觉效果。

**P6 — manifest-first 的信息泄漏。**
子图首屏的「子图占比」直接显示 figure 分数坐标的裸 rect（0.125 / 0.11 / 0.775 / 0.77），
这是 manifest 的 `position` 字段原样落进表单的结果；对用户有意义的 mm 宽高反而排在折叠组后面。

**P7 — override 来源状态过弱。**
被修改的属性只在字段下方多一行小字「恢复到脚本」（`FieldRow` 里 `overrides.some(...)` 时
渲染的 button）。没有头部计数、没有一眼可辨的「已修改 vs 脚本」状态；
「恢复整个元素」藏在「高级」折叠组里。

**P8 — 属性 / 助手 / 画布三个异质上下文做成同级 tab。**
`Inspector.tsx` 的 `TABS`。且 `autoShowProperties` 在助手 tab 打开时**不切回属性**
（「停在助手时不抢」），选中对象后可能仍然对着与对象无关的助手页。

**P9 — Quick Edit 不可发现。**
`QuickEdit.tsx` 只有右键 / 双击两个隐形入口，界面上没有任何提示它存在。

**P10 — 两种「文字」两套界面。**
画布标注文字走 `TextSection`（标签在左的行式布局、字号+BIU 一行），
图内文字走 `TextStyleBar`（无标签工具条 + 弹层）。同一个概念两套操作语言。

### 1.4 修改前做得对、必须保留的

- **能力真实**：控件严格按 manifest 出（`fieldOf` 查不到就不画）；不支持的能力走
  `UNSUPPORTED_ROLES` 说明而不是假控件；未知 prop/enum 回退显示原文（`propLabel`/`optionLabel`）。
- **写入通道统一**：`useElementWriter` / `useFieldGesture` 把预览、事务、渲染时机收在一处；
  属性页与右键弹层共用；连续调整压成一条撤销 + 一次定稿渲染。
- **逐字段「恢复到脚本」**、孤儿 override 清理、warnings 贴在出错字段下方。
- ArrangeSection 的图标工具条、CanvasPage 的真实比例页面预设、批量编辑、混排对齐。
- 元素树的分组 / 搜索 / 显隐锁定。

## 二、新的信息架构

### 2.1 三层结构（所有对象一致）

```
┌ 身份头 ────────────────────────────┐
│ [图标] 标题 “Reaction kinetics”  ⋯ │
│ Fig1_kinetics / 子图 1 · 2 项已修改 │
├ 第一层：主要属性（永远展开） ───────┤
│ 角色模板决定的 4–8 个高频属性        │
├ 第二层：更多（单一折叠区） ─────────┤
│ ▸ 更多                 1 项已修改   │
├ 第三层：源文件与高级（默认折叠） ────┤
│ ▸ 源文件与高级      fig1_kinetics.py│
│   脚本 / override 计数 / 恢复全部 /  │
│   写回 / 历史 / gid / 诊断           │
└──────────────────────────────────┘
```

- 第一层不允许折叠；第二层只有一个「更多」，展开状态**按角色**持久化
  （`inspectorPrefsStore`，localStorage），折叠时标题右侧显示「N 项已修改」摘要；
  第三层默认关闭，收纳一切触碰磁盘或低频诊断的东西。
- 折叠判据不再是「值是否偏离中性默认」——`groupHasContent` 只保留给
  未建档角色的兜底展示。

### 2.2 Presentation registry（`inspector/presentation/`）

manifest 继续回答「有什么能力、当前值、怎么写入」；新增的展示注册表只回答
「摆在哪、用什么控件、叫什么」：

- `types.ts`：`InspectorPriority = 'primary' | 'more' | 'advanced'`、
  `ControlKind`（`line-style` / `marker` / `hatch` / `colormap` / `legend-position` /
  `arrow-style` / `side-toggles` / `font` / … + 基础类型）。
- `roleProfiles.ts`：11 类角色的首屏模板（见 §2.3）。
- `registry.ts`：`presentFields(role, fields)` 把 manifest 字段分桶排序；
  `controlKindOf(role, field)` 按 prop 名 + 字段类型推断控件。
- 兜底规则（未建档角色 / 未知 prop）：无 group 的字段进 primary，
  有 group 的进 more，`高级`/`排列` 组进 advanced——**一个字段都不丢**，
  显示原始属性名也比隐藏好。
- registry 只决定展示；某字段 manifest 里没有就绝不渲染。

### 2.3 角色首屏模板（与引擎真实字段对齐）

| 角色 | 第一层 | 备注 |
|---|---|---|
| title / text / axis_label / legend_text | 内容、字体、字号、B/I、颜色、对齐 | 共享 TextControls（§2.5） |
| line / linecoll | 名称、颜色、线宽、线型（预览）、marker（预览）、marker 大小 | |
| scatter | 填充色或 colormap、marker、大小、描边色、描边宽、透明度 | |
| bar / bar_series / fill / patch | 填充色、描边色、描边宽、hatch（预览）、透明度 | bar_series 另有名称 |
| legend | 位置（3×3 网格）、字号、边框开关、边框颜色、背景 | |
| axes | 尺寸 (mm)、数据范围、坐标轴类型、刻度与边框状态图、网格 | 裸 rect 移入高级 |
| ticks | 主刻度模式、间距/值、四边刻度状态图、字号、颜色 | 状态图写宿主 axes 的真实字段 |
| colorbar | colormap 预览、vmin/vmax、方向、刻度字号 | manifest 无 loc 字段，不造 |
| 画布文字标注 | 与图内文字同一套 TextControls 视觉结构 | writer 不同、界面一致 |
| panel | 进入图内编辑、位置与尺寸、缩放、裁剪、适合/填充、恢复比例 | |
| arrow / shape（画布+图内） | 线宽、颜色、线型（预览）、箭头端型（预览）、填充 | |

### 2.4 视觉选择器（`inspector/controls/`）

`LineStylePicker`（真实线段 SVG 预览）、`MarkerPicker`（图形网格）、
`HatchPicker`（纹理缩略）、`ColormapPicker`（真实渐变条，stops 离线采样自
matplotlib，不联网）、`LegendPositionPicker`（3×3 网格 + best）、
`ArrowHeadPicker`（箭头端型预览）、`TickAndSpineDiagram`（可点击的坐标轴
状态图）。写入值全部仍是 Matplotlib 原始 enum；未识别值不丢失，显示
原文 + 通用预览。全部控件 radiogroup 语义 + 键盘 + 非颜色的选中标记。

### 2.5 统一文字控件

`controls/text/TextControls.tsx`：可见的「字体」「字号」标签 + B/I（/U）+
颜色 + 对齐的共享骨架；`ElementTextAdapter`（走 `useElementWriter`）与
`CanvasTextAdapter`（走 `updateObjects`）各接各的 writer，不复制状态逻辑。

### 2.6 外壳与响应式

- 右栏：默认 360px，可调 320–480px；`PREFS_VERSION` 升到 2 做一次性迁移
  （旧的 296–320px 宽度迁到 360；右栏默认打开且固定；用户此后的主动设置不再被动）。
- 断点：`wide`（可双栏钉住）从 1440 降到 **1280**；1024–1279 维持互斥停靠；
  <1024 覆盖式抽屉。1366×768 下左树 + 画布 + 右栏三者共存
  （280 + 44 + 1366−324−360 = 682px 画布，验收截图见 after/）。
  1024–1279 不做双停靠的理由：两栏 + 轨道至少 644px，画布会跌破 600px，
  违背「画布是主角」；覆盖式混合停靠引入的模式复杂度大于收益。
- 右栏 tab 收敛为「属性 / 画布」两个对象上下文；助手移出 tab 行，成为右上角
  带运行状态点的独立入口（仍复用 AssistantPanel）。选中对象时一律回到属性页。
  未拆成完全独立 Dock 的原因见 ADR 0010。

### 2.7 明确不修改的范围

- 引擎协议 / manifest 结构 / overrides 应用语义：一个字节不动。
- 写回验证、全量重放、baked overrides、历史与事务模型：不动。
- 文档 schema：不动（新增的只有 UI 偏好，存 localStorage）。
- `pdfbackend` / 导出合成 / 预检：不动。
- ArrangeSection、CanvasPage、元素树、批量编辑、混排对齐的现有交互：保留。

## 三、修改后验收

### 3.1 修改后截图（after/，与 before/ 同一套状态矩阵）

| 截图 | 验收点 |
|---|---|
| `zh-1440-title-selected` | 标题首屏：内容 / **字体**（衬线，Aa 预览）/ **字号** + B/I / 颜色 / 对齐——全部带可见标签，零折叠可达 |
| `zh-1440-line-selected` | 曲线首屏：名称 / 颜色 / 线宽 / **线型（真实线段预览）** / **标记（图形选择器）** / 标记大小 |
| `zh-1440-legend-selected` | 图例首屏：**3×3 位置网格 + 自动档** / 字号 / 边框开关 / 描边色 / 背景色 |
| `zh-1440-axes-selected` | 子图首屏：X/Y 范围 / 轴缩放 / **可点击的刻度与边框状态图** / X/Y 网格；裸 rect 移入「源文件与高级」 |
| `zh-1440-ticks-selected` | 刻度组首屏：主刻度方式 / 字号 / 颜色 / 四边状态图（写宿主子图的真实字段） |
| `zh-1440-panel-selected` | 面板三层：图内编辑 + 几何 + 裁剪/适配常驻；旋转/翻转/透明度/替换进「更多」；写回/历史/诊断进「源文件与高级」 |
| `zh-1366x768-title-selected` | **左树 + 画布 + 属性栏三者共存**，画布 >650px |
| `en-1440-title-selected` | 英文 Font / Size / Color / Align 可见标签，无溢出 |
| 各分辨率 `*-title-selected` | 1024（互斥停靠）/ 1280 / 1366 / 1440 / 1920 无溢出、无截断 |

上下文工具条（Quick Edit 的可发现入口）在 line/legend/title/panel 各 after 截图中
均可见：贴着选择框、给 3–5 个高频动作 + 「全部属性」出口，且被约束在画布区
（不盖侧栏）。

### 3.2 量化验收清单（§22 逐条）

- ✅ 选中文字：字体/字号/字形/颜色 无需展开折叠组（after: title 截图 + e2e 流程 A）
- ✅ 选中曲线：颜色/线宽/线型/marker 无需展开折叠组（after: line 截图 + e2e 流程 B）
- ✅ 线型不读 `--`/`-.` 编码：LineStylePicker 真实线段预览（pickers.test）
- ✅ marker 不记字符：MarkerPicker 图形网格（pickers.test）
- ✅ 图例位置不开文字 Select：3×3 网格（e2e 流程 C）
- ✅ 1366×768 左树与属性栏共存（uiStore.test + e2e 布局用例 + after 截图）
- ✅ 右栏默认 360px（320–480 可调），标签可见（uiStore.test 迁移用例）
- ✅ 字体/字号不存在「只有 aria-label」的控件（textStyleBar.test：可见文字断言）
- ✅ 高频操作最多一层交互（primary 平铺；popover 仅限 marker/hatch/colormap 长列表）
- ✅ 高级与写回默认折叠（inspectorFolding.test：源文件与高级默认关闭）
- ✅ 未知 manifest 字段仍可访问（presentation/registry.test：未知字段进「更多」不丢失）
- ✅ preview / render / undo / redo / replay / reset / write-back validation 全部不变：
  写入仍走 setOverride/updateObjects/useElementWriter（elementStylePreview.test 16 例
  原样通过：局部预览零后端、一轮一条历史、granular 语义），引擎与写回代码零改动
- ✅ 中英文无溢出无裸 key（pnpm i18n:check + e2e/i18n.spec 17 例）
- ✅ 现有测试全部通过（vitest 74 文件 871 例；受影响 e2e 5 个 spec 全绿）
- ✅ 无新增 P0/P1 数据安全风险（文档 schema、引擎协议、写回三段事务零改动）

### 3.3 测试命令与结果（2026-08-25）

```
cd web && pnpm test                       # 74 files, 871 tests, 全绿
cd web && pnpm build                      # tsc -b + vite，零错误
cd web && pnpm i18n:check                 # 类型 / 对齐 / 硬编码 / 复数，全绿
cd web && pnpm exec playwright test \
  e2e/inspector-redesign.spec.ts \        # 流程 A+E / B+C / 1366 共存，3/3
  e2e/fake-realtime.spec.ts \             # 假实时不回归
  e2e/element-path-selection.spec.ts \    # 路径选中不回归
  e2e/i18n.spec.ts e2e/a11y.spec.ts       # 17 例全绿
python scripts/build_mcp_widget.py --check   # 画布产物已重建、指纹一致
.venv/bin/python -m pytest tests/test_codex_plugin.py   # 全绿
```

### 3.4 未解决 / 明确取舍

- **1024–1279 维持互斥停靠**：双停靠会把画布压破 600px。此档从树里选元素
  仍不会自动唤出右栏（焦点在抽屉里不抢），需点画布或收起树——记录在案。
- **Assistant 未拆成独立 Dock**：以「独立入口 + 选中即回属性 + 运行状态点」
  达成本轮目标；Dock 化的代价与理由见 ADR 0010 §3。
- **单选面板的「层级」工具带（ArrangeSection）排在「源文件与高级」之后**：
  两者分属不同组件树（类型专属段 vs 通用排列段），本轮不动组件边界。
- **TickAndSpineDiagram 的关侧提示较淡**（虚线 + 30% 透明度）：有 tooltip 与
  焦点态兜底；如需更强提示，可在 post-1.0 调整对比度。
- **browser playground / 网站产物未同步**：`web/src` 变更后 playground 与
  MCP 画布两个产物都要重建——MCP 画布已重建进本分支；playground 的
  网站同步（Tavotto_website `pnpm sync-playground`）属发布流程，随下次发版走。

### 3.5 改动范围声明

- **引擎协议 / manifest / overrides / 写回验证 / 重放 / 文档 schema：零改动**
  （`src/tavotto/` 下唯一变化是无——本分支不含任何 Python 改动）。
- 新增持久化仅两处 localStorage 键：`tavotto.ui`（版本 2 迁移）与
  `tavotto.inspector`（「更多」按角色展开偏好）。
- `codex-plugin/mcp/widget/canvas.html` 为受管构建物随源码重建。

### 3.6 新增 / 修改文件清单

新增：`web/src/components/inspector/presentation/{types,roleProfiles,registry}.ts(+test)`、
`web/src/components/inspector/controls/{OptionGrid,LineStylePicker,MarkerPicker,HatchPicker,ColormapPicker,LegendPositionPicker,ArrowPickers,TickAndSpineDiagram,textRows}.tsx`、
`controls/{colormapStops,fontStack}.ts`、`controls/pickers.test.tsx`、
`web/src/store/inspectorPrefs.ts`、`web/src/canvas/ContextBar.tsx(+test)`、
`web/src/components/inspector/inspectorFolding.test.tsx`、
`web/e2e/inspector-redesign.spec.ts`、`docs/adr/0010-inspector-presentation-layer.md`、本文档与前后截图。

修改：`uiStore.ts`（宽度/断点/迁移/autoShowProperties）、`Inspector.tsx`（tab 收敛、
助手入口、身份头计数）、`ElementInspector.tsx`（三层结构、控件分派、行内恢复）、
`TextStyleBar.tsx`（重写为带标签行）、`TextSection.tsx`（共享行 + 更多）、
`PanelSection.tsx`（三层）、`StrokeSection.tsx`（视觉选择器）、`QuickEdit.tsx`
（图例位置网格）、`CanvasStage.tsx`（挂 ContextBar）、i18n 两语言资源、
`elementStylePreview.test.tsx`/`textStyleBar.test.tsx`/`TextSection.test.tsx`/
`uiStore.test.ts`（契约更新）、`e2e/{fake-realtime,element-path-selection}.spec.ts`
（选择器去歧义）。

### 3.7 手动验证步骤（关键交互）

1. `./run.sh --no-browser` → 打开 examples/figures → 双击 Fig1_kinetics.pdf。
2. 选中面板：确认三层（图内编辑/几何/图片 → 更多 → 源文件与高级）；
   上下文工具条出现，点「编辑图内元素」。
3. 左轨道点「图内元素」开树：1366×768 窗口下确认树、画布、属性栏同屏。
4. 点标题：右栏可见「字体/字号/颜色/对齐」；改字号 → 画布即时变化 →
   头部出现「1 项已修改」→ 行尾恢复按钮点掉 → 归零。
5. 点曲线：线型四格预览选虚线；标记选择器换方块；⌘Z/⇧⌘Z 逐步回退重做。
6. 点图例：3×3 网格换位置；画布上拖动图例 → 位置显示「自定义（拖动过）」。
7. 点子图：状态图上点上边框（关）→ 图上边框消失；再点它下方出现的
   「已修改」chip → 恢复。
8. 「源文件与高级」内：写回原始文件按钮仍在且默认折叠；gid 在此层可见。
