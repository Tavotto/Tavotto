# 桌面端 UX 一致性修复

> 分支 `feat/desktop-ux-consistency`（基线 `aa98ccb`，即 PR #128 编码 Agent 注册表之上）。
> 修改前截图 `docs/ux/img/ux-consistency-pass/before/`；修改后 `after/`。
> 全部截图来自**真实运行的应用** + `examples/figures` 的 Fig1_kinetics，非手写数据。
>
> 上一轮的 Inspector 重构见 `INSPECTOR_REDESIGN.md`（本轮不推翻它，是在它建立的
> 展示注册表 / 视觉选择器之上补齐四类**一致性**缺陷）。

## 一、调查范围

- 通读 `ElementInspector.tsx` / `TextStyleBar.tsx` / `controls/textRows.tsx` /
  `controls/TickAndSpineDiagram.tsx` / `presentation/{registry,roleProfiles,types}.ts` /
  `elementWrite.ts` / `ai/AiPanel.tsx` / `store/aiStore.ts` / `SettingsDialog.tsx` /
  `ui/{Tooltip,Popover,Field,Input,Segmented,Select,Toggle}.tsx`、中英文 i18n 资源，
  以及 `engine/manifest.py` 的 `_text_fields` / `_tick_fields` / `_axes_fields` 发射端。
- 真实运行（`python -m tavotto --figures <examples/figures 的拷贝>`），在
  1440×900 / 1366×768 / 1024×768、中英文下逐个复现任务书列出的七个状态，
  共 20 张修改前截图。
- 一致性扫描覆盖：属性检查器、批量编辑、Context Bar、AI 弹层、设置 Dialog、
  其余窄 Popover、中英文界面。

## 二、修改前的根因（逐条）

### R1 跨角色批量为何失效

`ElementInspector.tsx` 的批量判据是**角色完全相同**：

```ts
const batch =
  selected.length > 1 && selected.every((e) => e.role === selected[0].role) ? selected : null
```

图标题的 role 是 `title`、轴标题是 `axis_label`——最常见的那个组合
（统一一张图里所有文字）直接落到 `batch === null`。而 `alignGroup`
（几何对齐）只要多选就成立，于是右栏整个被换成对齐工具条：
`before/zh-1440-title-plus-axis-labels.png` 里连一个字号输入框都没有。

更深一层：`alignGroup || batch ? null : (…)` 把「样式」与「几何」写成了**互斥**
的两态。多选三条文字既该能对齐也该能统一字号，这两件事本来就不冲突。

### R2 B/I 为何在批量模式退化

单选文字走 `TextStyleBar`（B/I 是 `IconToggle` 图标按钮）；多选走
`BatchFieldRow`，那里按 `field.type` 分派，`weight` / `style` 都是 `enum`，
于是落进 `case 'enum'` 的通用 `Select`——`before/zh-1440-two-axis-labels.png`
里就是「字重：常规 ▾」「字形：正体 ▾」两个文字下拉。

两条渲染路径各写各的控件，是这类分叉的**结构性**成因：只要「一个」与「多个」
是两份实现，迟早分叉。同一个根因还波及线型、marker、hatch、图例位置——
多选两条曲线时，视觉选择器全部退化成文字下拉。

顺带一个更危险的形状：旧批量行把 mixed 的数字画成 `0`、颜色画成 `#000000`
（`value={mixed ? 0 : …}`）。用户看到一个具体的值，会以为「它们本来就一样」，
一敲回车就把两个不同的值抹平——这是数据损坏级的误导，不是显示瑕疵。

### R3 刻度方向与次刻度为何难以发现

「刻度」这件事被拆在三个地方：

| 能力 | 住在哪个 manifest 元素 | 界面上在哪 |
| --- | --- | --- |
| 四边刻度开关 `ticks_*` | `axes_0` | 子图页的状态图（可点） |
| 方向 / 长度 / 宽度 | `axes_0.xticks` / `.yticks` | 刻度组元素的「刻度线」折叠组 |
| 次刻度 `minor_visible` | 同上 | 同一元素的「刻度定位」折叠组 |

而「刻度组」这个元素本身要先在元素树里展开「坐标轴」才找得到。用户得先理解
axes / xticks / yticks 三个内部对象的关系，才能改一件事。

状态图还有一个**说谎**的问题：`tickMarks(side)` 把刻度短线画死在框外，
不读 `direction`。`examples/figures/paper_style.py` 里写的是
`xtick.direction: "in"`——也就是说 `before/zh-1440-axes-ticks.png` 上那张图，
刻度朝外，而它描述的那张真实图刻度朝内。这不是抽象风险，是真数据上的反例。

### R4 AI effort 为何溢出

弹层宽 232px，六档强度（`minimal / low / medium / high / max / xhigh`）做成
一排等宽 `Segmented`：`before/zh-1440-ai-popover.png` 上 `minimal` 与 `xhigh`
两头都被切掉。等宽按钮的总宽随档位数线性增长，而弹层宽度是常数——
这个控件形态与这个能力天然不匹配。

雪上加霜的是同一个弹层里还常驻两段说明（「执行改动的命令行工具」「直接改脚本
文件；每次运行前自动快照，可回滚」），把本来就不够的宽度进一步压缩。

另外 `usable.length === 1` 时照样渲染 Provider 分段控件——一个只有一项、
点了没有第二个选择的「双选」。

### R5 设置页为何形成文字墙

九个分区全在一个 783 行的 `SettingsDialog.tsx` 里，每个分区都是同一个形状：

```tsx
<Row label=… ><Toggle/><span>短提示</span></Row>
<p className="text-xs …">说明</p>
<p className="text-xs …">更多说明</p>
```

`<p>` 与控件的字号、颜色、行距接近，扫视时分不出「这是我要改的」和「这是在
跟我讲道理」。About 一页更极端：品牌 + 隐私长文 + 遥测两段 + 许可证 +
渲染环境（含**完整解释器绝对路径**）+ 五条诊断项，全部平铺首屏。

## 三、本轮修复项

### A. 文字能力批量模型（commit 1）

- **文字语义家族**（`textStyleModel.ts`）：`title` / `axis_label` / `legend_text` /
  `text`。这张表只决定「值不值得尝试」，**能改什么仍由 manifest 字段交集说了算**。
- **`ControlValue` 三态**：`uniform` / `mixed` / `unavailable`。mixed 的字号是
  空输入框 + 「多个值」占位，颜色旁明写「多个值」，字体是 placeholder；
  没有任何一个控件会把 mixed 画成某个具体的默认值。
- **`TextStyleControls` + `useTextStyleAdapter`**：单选与多选**同一个实现**
  （adapter 接的就是一个数组）。控件那边看不到目标是一个还是三个，
  所以「多选就退化」这类分叉在结构上不可能再出现。`TextStyleBar` 现在只是
  「一个元素」这个特例的适配器组装。
- **三态 B/I**：`aria-pressed="mixed"`（ARIA 认这个值）+ 一条非颜色的短横提示。
  点击语义 mixed→全开、全开→全关、全关→全开；**没有「点回 mixed」**。
- **`styleBatch` / `batch`(同角色) / `alignGroup` 拆成三个独立概念**，可以同时成立。
- enum 选项取**交集**而非要求逐字相等（引擎会把元素当前用的字体插进
  `fontfamily` 选项表，要求相等的话「字体」会整条消失）；number 范围取最紧的那个。
- `ha` 不进 `TEXT_STYLE_PROPS`，批量适配器**结构上**拿不到它——同一个 `left`
  在图标题与 Y 轴标题上语义不同，批量写会让元素意外移动。

### B. 刻度任务卡（commit 2）

- `TickTaskCard`：X/Y 分段切换 + 次刻度 + 方向 + 长度 + 宽度，紧挨状态图。
  同一个组件两种入场：子图页给两个轴（出切换），刻度组页只给它自己那个轴
  （切过去会写到另一个元素，而用户选的是这一个）。
- 状态图读**真实字段**：`direction` 三档各画各的，`minor_visible` 开启后在主刻度
  之间画出明显更短的次刻度（6 vs 3）。上下边读 x、左右边读 y，两轴互不影响。
  关掉某一边时主次刻度**一起**变关闭样式。
- 宿主映射收敛到 `tickAdapter.ts` 一处；协议一个字节没动。
- 不造引擎没有的能力：没有 `major_visible`，所以开关说的是「只要主刻度 /
  主刻度 + 次刻度」；3D 轴没有 `direction`，那一行整条不画，状态图回落
  matplotlib 默认 `out`。
- consumed props：刻度组页把 `direction` / `minor_visible` / `length` / `width`
  让给卡，通用列表不再出第二套；逐字段「恢复到脚本」一条没少。

### C. AI 模型选择器（commit 3）

- **离散强度滑杆**（`ui/StepSlider`）：每一格对应 `caps` 里真实存在的一个档位，
  滑到第 i 格写的就是 `efforts[i]`。滑杆宽度与档位数无关，加到八档也不溢出。
  原生 `<input type="range">`，方向键 / Home / End / 触摸免费拿到；
  `aria-valuetext` 报当前语言的档位名；轨道下的小点表明它是离散的。
  只有一档 → 不可调；一档都没有 → 整块不出现。
- 档位名是**开集**：codex 的 `xhigh` 是从用户配置里带上来的（后端
  `EFFORTS` 里没有它），查不到就回退原文。
- 模型行换成既有的 `Select`；清单为空 = 跟随 CLI 默认，不伪造模型名。
- 只装一个 Agent 时不出双选。
- `agentNote` / CLI 版本 / 路径 / 强度原始值 / 打开设置全部进「技术详情」。
- 弹层 232 → 288px。

### D. 设置页渐进披露（commit 4）

- 基础构件 `settings/SettingRow.tsx`：`SettingSection` / `SettingRow` / `HelpTip` /
  `InlineWarning` / `DiagnosticDisclosure` / `DiagnosticItem`。
- `SettingsDialog.tsx` 783 → 107 行，只剩导航与分区分派；九个分区各住一个文件。
- **`HelpTip` 四种触发方式都真的能用**：一个 Radix Popover 同时接悬停 / 聚焦 /
  点击 / 触摸，不是 Tooltip 套 Popover（那样会有嵌套焦点陷阱）。`keepFocus`
  阻止焦点被搬进浮层——否则鼠标划过一个问号就抢走键盘焦点。Esc 由 Radix
  挂在 document 上；关的是气泡，不是整个设置对话框。
  悬停这条路**只认 `pointerType === 'mouse'`**：触屏的 pointerover 与 click
  连着来，不区分的话触屏「点一下」会变成「开了又关」。
- 逐分区减负（详见 §4 的对照表）。
- About 页内拆三块（导航 id 仍是 `about`，schema 一字未动）：产品与版本 /
  隐私与数据 / 渲染环境。首屏渲染环境只给「解释器来源 + matplotlib 版本」。

### E. 一致性扫描直接修掉的

| # | 审计项 | 处置 |
| --- | --- | --- |
| 1 | 同一个 prop 单选 / 多选控件不同 | 文字样式走共享 adapter；**线型 / marker / hatch / colormap / 图例位置 / 箭头端型在批量行里也用同一组视觉选择器**（`BatchFieldRow` 的 `case 'enum'` 改为按 `controlKindOf` 分派） |
| 2 | 通用 enum 下拉覆盖视觉选择器 | 同上；六个 picker 新增 `value: string \| null`，多选不一致时一个格子都不标选中，也不把空值塞进选项表 |
| 3 | 能力藏在高级 / 技术详情 | 刻度方向与次刻度提到首屏（B） |
| 4 | 行内说明 + 额外段落并存 | 设置页全量清理（D） |
| 5 | 实现术语暴露 | 强度档位显示为「最低/低/中/高/最高/极高」，原始值只在技术详情 |
| 6 | 长路径 / 版本 / 内部 ID 占首屏 | About 首屏不再出现解释器绝对路径与包清单 |
| 7 | 窄宽度截断 | AI 弹层改滑杆 + 加宽；e2e 用真布局逐元素量 `scrollWidth > clientWidth` |
| 8 | mixed 被显示成默认值 | `ControlValue` 三态 + 各控件的 mixed 占位 |
| 9 | 只支持 hover 的帮助 | `HelpTip` 四种触发 + Esc |
| 10 | 同一操作不同控件 | 语言与导出 DPI 两个手写 `<select>` 换成 `ui/Select`（全仓库仅剩的原生下拉） |

`canvas/ContextBar.tsx` 的图内文字分支此前自己写了一份 `<Button active={bold}>`
——长得与属性页一样，但**是第二份实现**。本轮换成同一个 `StyleToggle`：
同形的两份实现迟早分叉，R2 那批缺陷正是这么来的。

顺带修掉的既有 a11y 缺陷：`Segmented` 的**纯图标项此前没有可达名**（只有
tooltip，屏幕阅读器读到的是无名 radio）。新增 `item.ariaLabel` 与整组
`ariaLabel`，方向三档、对齐三档、排列参照系都受益。

## 四、设置页逐分区处置

| 分区 | 首屏保留 | 移入问号 / 技术详情 | 仍然常驻的风险信息 |
| --- | --- | --- | --- |
| 常规 | 语言、自动保存状态、恢复默认布局 | 语言何时生效、自动保存实现、重置影响 | — |
| 项目与路径 | 当前项目、脚本数、导出/备份目录、只读开关 | 默认路径规则、目录解析、注册表是什么 | **只读开启时的副作用**（InlineWarning）、保存失败的错误 |
| 画布与编辑 | 子图联动开关、打开画布设置按钮 | 「关联元素」整段解释、其他设置在哪 | — |
| 侧栏行为 | 左/右常驻开关 | 断点与自动收起规则 | — |
| 编码 Agent | （#128 的实现，本轮未改） | — | 探测失败与安装入口 |
| 导出默认值 | DPI、格式、proof 开关 | DPI 只影响位图、PDF/PNG 各自用途、proof 是什么 | — |
| 快捷键 | 打开速查表 | 「按 ? 随时打开」 | — |
| 检查更新 | 版本、自动检查、检查按钮、状态 | 安装方式、更新渠道、签名校验 | **检查错误、升级失败、手动下载退路** |
| 隐私、诊断与 About | 产品/版本/许可证、遥测开关 + **最短摘要**、解释器来源、matplotlib 版本 | 发什么/不发什么两份清单、本机优先承诺、诊断包内容、完整路径、包清单、五条诊断项 | **隐私最短摘要、硬开关生效说明、渲染环境不正常时的整张恢复卡** |

「最短摘要」的成文（新增 key `about.telemetry.summary`）：

> 仅在你明确开启后发送匿名功能使用情况，不发送图、脚本、文件名、路径、
> 科研数据或提示词。

## 五、关键交互原则（本轮确立）

1. **同一属性，同一视觉语言。** 判据不是「渲染它的是哪个组件」，而是「用户
   在改哪个属性」。落实手段是**共享 adapter**而不是共享约定——两份实现迟早分叉。
2. **mixed 是一种状态，不是一个值。** 任何控件都不许把「多个值」画成某个具体值。
3. **能力交集，不是角色相等。** 用户关心「这些对象有哪些属性能一起改」，
   不关心它们在 manifest 里是不是同一个 role。
4. **manifest 仍是能力唯一权威。** 界面可以重新摆放能力（把两个元素的字段并到
   一张卡上），但不许发明能力（没有 `major_visible` 就不造一个）。
5. **渐进披露有硬边界。** 错误、数据覆盖风险、写源文件风险、隐私授权摘要、
   缺依赖、不可逆操作、当前设置的重要副作用——这七类永不折叠。
6. **帮助控件必须四种触发方式都能用**，且不许抢焦点。

## 六、before / after 截图索引

目录：`docs/ux/img/ux-consistency-pass/{before,after}/`

| 文件名 | 视口 / 语言 | 看什么 |
| --- | --- | --- |
| `zh-1440-axis-label-single` | 1440×900 zh | 单选轴标题（对照组：控件形态不该变） |
| `zh-1440-two-axis-labels` | 1440×900 zh | 多选两个轴标题：before 是「字重 ▾ / 字形 ▾」下拉，after 是 B/I 图标 |
| `zh-1440-title-plus-axis-labels` | 1440×900 zh | 图标题 + X/Y 轴标题：before **完全没有样式区**，after 有公共样式 + 对齐并存 |
| `zh-1440-axes-ticks` | 1440×900 zh | 子图刻度：before 刻度画死朝外、无方向/次刻度控件；after 刻度朝内（真实状态）+ 刻度卡 |
| `zh-1440-ticks-element` | 1440×900 zh | 从刻度组进入：同一套控件、不重复 |
| `zh-1440-ai-popover` | 1440×900 zh | AI 弹层：before 六档按钮两头截断 + 两段说明；after 滑杆 + 技术详情折叠 |
| `zh-1440-ai-popover-details` | 1440×900 zh | （after 专有）技术详情展开后 CLI 版本 / 路径 / 强度原始值都在 |
| `zh-1440-settings-general` | 1440×900 zh | 常规：before 两段说明常驻 |
| `zh-1440-settings-project` | 1440×900 zh | 项目与路径 |
| `zh-1440-settings-canvas` | 1440×900 zh | 画布与编辑：before 三段文字 |
| `zh-1440-settings-sidebars` | 1440×900 zh | 侧栏行为 |
| `zh-1440-settings-agents` | 1440×900 zh | 编码 Agent（#128 的页面，本轮未改，作对照） |
| `zh-1440-settings-export` | 1440×900 zh | 导出默认值 |
| `zh-1440-settings-update` | 1440×900 zh | 检查更新 |
| `zh-1440-settings-about` | 1440×900 zh | About：before 完整解释器路径 + 五条诊断项在首屏 |
| `zh-1440-settings-about-collapsed` | 1440×900 zh | （after 专有）三块分区、无绝对路径 |
| `zh-1440-settings-about-diagnostics-open` | 1440×900 zh | （after 专有）展开环境诊断后完整路径才出现 |
| `zh-1440-settings-help-open` | 1440×900 zh | （after 专有）小问号展开态 |
| `zh-1366-axes-ticks` | 1366×768 zh | 窄视口下刻度卡不拥挤 |
| `zh-1024-axes-ticks` | 1024×768 zh | 最窄档 |
| `en-1440-title-plus-axis-labels` | 1440×900 en | 英文跨角色批量 |
| `en-1440-axes-ticks` | 1440×900 en | 英文刻度卡（Direction / Minor ticks 不溢出） |
| `en-1440-ai-popover` | 1440×900 en | 英文 AI 弹层 |
| `en-1440-settings-general` / `en-1440-settings-about` | 1440×900 en | 英文设置 |

截图脚本 `web/.uxshots/`（**未提交**，是取证工具不是产品代码）：
`run.sh` 起一个干净实例（独立 data/config 目录 + `examples/figures` 的拷贝），
把一次性 nonce 交给 `shots.mjs`。

## 七、测试与视口矩阵

### 单元 / 组件（vitest，94 文件 1088 例全绿）

| 文件 | 例数 | 覆盖 |
| --- | --- | --- |
| `inspector/textStyleBatch.test.tsx` | 27 | 家族判定、字段交集、选项交集、数值范围收紧、跨角色 UI、mixed 三态、三态 B/I 点击语义、一次点击一条历史、撤销重做、与对齐并存、单选一致性、**批量里的视觉选择器不退化** |
| `inspector/tickTaskCard.test.tsx` | 20 | 宿主映射、子图页配置、X/Y 各写各的、三档方向、`minor_visible`、示意图读真实 direction、次刻度更短、关边后主次同为关闭态、能力缺失不出控件、刻度组页只给自己那轴、consumed props（展开「更多」后再查一遍）、逐字段恢复、键盘语义 |
| `ai/aiModelPicker.test.tsx` | 17 | 单/双 Provider、无可用时的恢复入口、各自偏好、模型清单来自 caps、空清单不伪造、档位数 = 数组长度、滑到第 i 格写 `efforts[i]`、未知档位回退原文、单档不可调、无能力不出现、越界回落、原生 range、文案减负与技术详情 |
| `settings/settingsDisclosure.test.tsx` | 23 | 六个分区无文字墙、解释进问号、问号四种触发 + Esc + 不抢焦点、只读副作用常驻、隐私摘要常驻、遥测默认关闭、About 首屏无绝对路径、展开诊断才有、页面内三块分区、标签列宽一致 |
| `SettingsTelemetry.test.tsx`（改） | 6 | 摘要与政策链接常驻、两份清单在问号里**且折叠时确实看不到**、问号键盘可达 + Esc |
| `i18n/render.test.tsx`（改） | 10 | 语言下拉改为驱动 Radix Select |

### 反证（每条新门禁提交前手工验一次）

| 变异 | 结果 |
| --- | --- |
| 关掉 `styleBatch` | 红 13 例 |
| 抽掉 `toggleStateOf` 的 mixed 分支 | 红 1 例 |
| 字号 mixed 换成默认值 | 红 1 例 |
| 示意图退回「永远朝外」 | 红 1 例 |
| 刻度组页 consumed props 置空 | 红 1 例 |
| Provider 双选无条件显示 | 红 1 例 |
| 档位写成 `` `effort-${i}` ``（凭空生成） | 红 1 例 |
| AI 说明文字搬回常驻 | 红 1 例 |
| 两段说明搬回设置首屏 | 红 3 例 |
| 环境卡从折叠区提到 About 首屏 | 红 1 例 |

反证过程本身逮到两个**假绿**，都记在这里因为它们是会重复发生的形状：

1. 第一次跑「consumed props 置空」时是绿的——**变异的 replace 模式缩进写错，
   压根没改到代码**。「反证跑了」不等于「反证生效了」，得先确认变异真的落到文件里。
2. 改对之后仍然绿——因为 `direction` 本来就住在「更多」折叠区里，
   只查首屏的话 consumed 完全失效也照样绿。**空门禁**。现在展开「更多」后再查一遍。
3. 写 `HelpTip` 悬停用例时第一版是绿的，但什么都没测：React 的 `onPointerEnter`
   是用冒泡的 `pointerover` 委托实现的，直接派 `pointerenter` 谁也收不到。

### 评审修复（#142）

| 级别 | 问题 | 处置 |
| --- | --- | --- |
| P1 | 3D 图的 `axes_*.zticks` 被选中时，`selfAxis === 'z'` 落进 else 分支退回 `all`，刻度卡摆出一组**写到 `xticks` / `yticks`** 的控件；同时 `TICK_CARD_PROPS` 又把 Z 自己的 `length` / `width` / `minor_visible` 从通用列表里拿掉——改错对象 + 真控件消失 | 卡严格只给选中的那个轴（Z 没有适配器 → 一个都不给）；consumed 规则改成「卡真的接管了这个元素才让出字段」。3 条用例，两半各有反证 |
| P2 | 两个图内文字选中后 Shift 加选画布标注：`selected` 仍是两个 manifest 元素，`styleBatch` 照样成立，但它的写入器只写 override——点一次加粗只改到 3 个里的 2 个，而对齐区写着「已选 3 个对象」 | `mixedWithAnnotations` 闸同时管住 `batch` 与 `styleBatch`（**同一形状的两个消费点，只修一个等于没修**）；对齐不受影响。4 条用例，两个消费点各有反证 |

P2 顺带**证伪了本文档先前的一句话**：原文写「`isTextLikeSelection` 只认 manifest
元素，标注混进来时样式区不出现，不会产生改了一半的状态」——错的。
`isTextLikeSelection(selected)` 只看 `selected`，标注在另一个数组里，所以样式区
照样出现。上面 §8 的第 1 条已按事实改写。**审计文档里的断言也要指得出兑现它的
那行代码**，这一条当时没有。

### E2E（Playwright，真浏览器 + 真引擎）

`e2e/ux-consistency.spec.ts` 四条，全绿：

- **流程 A** 图标题 + X/Y 轴标题：先把标题单独改成 12pt 造出 mixed → 断言输入框
  留空 + 「多个值」占位 → 批量改 13pt / 加粗 / 换字体 → 逐个元素确认都变了 →
  撤销三次逐步回退 → 重做三次全部回来。
- **流程 B** 刻度：开上边与右边刻度 → **起手就断言示意图是 `in`**（paper_style.py
  写的就是 `direction: "in"`，这一条本身就是 R3 那个缺陷的真数据反例）→
  三档方向都带动示意图 → Y 设成 inout 且 X 不受影响 → 开 X 次刻度出现更短的
  次刻度 → 等真实渲染 → 撤销重做。
- **流程 C** AI：技术详情折叠、正常态无「自动快照」字样；有滑杆时用**方向键**
  调档 → 关掉重开偏好还在；只有一个 Provider 时不出双选；**逐元素量横向溢出**
  （只认 `overflow-x: visible` 的元素，裁切与可滚容器不算）。
- **流程 D** 设置：五个分区逐个量「长文段数 ≤ 1」且无横向溢出 → 键盘聚焦问号
  即展开 → Esc 关气泡但**设置对话框仍在** → About 首屏用正则断言**没有绝对路径** →
  展开环境诊断后同一个正则**必须命中**。

回归（全绿）：`golden-paths` 8 例、`keyboard-golden-path` 3 例、
`inspector-redesign` 3 例、`i18n` 12 例、`a11y`、`coding-agents`。

### 视口 / 语言矩阵

| | 1024×768 | 1366×768 | 1440×900 |
| --- | --- | --- | --- |
| zh 刻度卡 | 截图 | 截图 | 截图 + e2e |
| zh 批量文字 | — | — | 截图 + e2e |
| zh AI 弹层 | — | — | 截图 + e2e 溢出量测 |
| zh 设置 | — | — | 截图 + e2e 溢出量测（五个分区） |
| en 全部 | e2e `i18n.spec` 1024 溢出量测 | — | 截图 |

## 八、延后项

| # | 事项 | 为什么延后 | 风险 | 建议后续 issue 范围 |
| --- | --- | --- | --- | --- |
| 1 | **图内文字 + 画布标注的跨 writer 批量样式** | 图内元素写 `override`（走引擎重放），画布标注写文档对象（走 `updateObjects`）。两条通道的事务、撤销标签、渲染时机都不同，合成一次 commit 要动 `applyMixedAlign` 那一层。本轮不做，**并且显式关掉了这种选择下的批量入口**（`mixedWithAnnotations`）：标注混进选区时 `batch` 与 `styleBatch` 都不给，对齐照旧可用 | 低（现状是能力缺失，不是错误行为） | 参照 `applyMixedAlign` 的做法做一个 `applyMixedTextStyle`，一次 commit 两边 |
| 2 | **快捷键分区迁到 Help** | 会动设置导航结构与 `settingsSection` 的取值集合（AiPanel 按 `'ai'` 跳转依赖它），超出本轮范围 | 无 | 迁移时同步 `SectionId` 与所有 `setSettingsOpen(true, …)` 调用点 |
| 3 | ~~**AI 任务历史抽屉的状态筛选仍是原生 `<select>`**~~ | 那是 #128 新加的面（`AiPanel.tsx:737`），本轮不进它的实现 | 低（P3，视觉不一致） | **已做（#145）**：连同 `EndpointDialog`（预设 + wire 两处）、`AgentDetailView`、`CodingAgentsSection` 一起换成 `ui/Select`，全仓库不再有原生 `<select>`，由 `components/ui/nativeSelect.test.ts` 的源码扫描看住 |
| 4 | **About 首屏没有 Python 版本行** | 任务书建议的结构里有「Python 3.13.x」，但 `/api/engine/environment` 与 `/api/diagnostics` 都不单独发 Python 版本（`worker_python` 那条 detail 是「路径（来源）」）。加一个字段就是为界面方便扩展协议，违反本轮约束 | 无 | 若确有需要，在 `engine/environment` 里补 `python_version`，与诊断包同源 |
| 5 | **AI 弹层保留「作用范围」** | 任务书说「作用范围已在目标选择中表达时不要重复」——但本产品里它**没有**第二个入口，删掉等于删能力。保留并压缩成一行 + 一行面包屑 | 无 | 若将来目标选择器独立出去，再把这一段摘掉 |
| 6 | **1024–1279 仍是左右互斥停靠** | `INSPECTOR_REDESIGN.md` §3.4 已记录的取舍，本轮不重开 | 无 | — |
| 7 | **Context Bar 的图内文字分支没有专属用例** | 现有 `contextBar.test.tsx` 覆盖的是**画布文字对象**，图内元素那条分支要一整套 panel + manifest + CanvasStage 的挂载。本轮把它的加粗按钮换成共享的 `StyleToggle`（去掉了第二份实现），正确性靠「只剩一份实现」+ 类型检查 + 全量套件保证，**没有**新增针对它的行为门禁 | 低（同一个组件，分叉的可能性已经被结构消除） | 补一个图内元素的 ContextBar 挂载 harness，顺带覆盖它的「全部属性」出口 |
| 8 | **Context Bar 只有加粗、没有斜体** | 本轮只做去重，不改它的动作集（那是产品决策不是一致性缺陷） | 无 | 与上一条一起做 |

## 九、明确不改的范围（复核过）

- 引擎协议 / manifest 结构 / overrides 应用语义：**零改动**（本分支 `src/tavotto/`
  下唯一的变化是 `codex-plugin/mcp/widget/canvas.html` 这个受管构建物）。
- 写回三段事务、全量重放、baked overrides、历史与事务模型：不动。
- 文档 schema：不动（新增持久化只有 AI 偏好那份既有的 localStorage）。
- Codex / Claude CLI 探测逻辑、每个 Provider 独立的模型与 effort 偏好、
  `prunePrefs` 旧偏好清理、`effectiveAgent` 的「首选不可用时不改用户首选值」：不动。
- telemetry 默认关闭、三档同意、`TAVOTTO_NO_TELEMETRY` 硬开关：不动。
- 既有视觉选择器：不但没退化，还扩展到了批量路径。
- 纯键盘黄金路径：`keyboard-golden-path.spec.ts` 3 例原样通过。
