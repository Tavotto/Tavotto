---
name: Tavotto
description: matplotlib 科研图的可视化编辑器——Paper × Instrument，紧凑的桌面工具
colors:
  bg: "#efefed"
  canvas: "#f5f5f3"
  surface: "#ffffff"
  surface-2: "#f7f7f5"
  field: "#f2f2f0"
  field-hover: "#eeeeec"
  border-control: "#84847c"
  ink: "#1b1b18"
  ink-2: "#4a4a45"
  ink-3: "#6c6c66"
  ink-faint: "#a3a39a"
  accent: "#2c73de"
  accent-subtle: "#ebf2fc"
  danger: "#c4442a"
  warn: "#b07400"
  ok: "#2b7649"
  sel: "#4685e2"
  syntax-keyword: "#8a3350"
  syntax-function: "#5b45a8"
  syntax-string: "#7a5a2b"
  syntax-number: "#2868b7"
  syntax-comment: "#6c6c66"
  syntax-type: "#2a6b5b"
  syntax-builtin: "#86520a"
  shadow: "#1b1b18"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: "30px"
    letterSpacing: "-0.02em"
  heading:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: "24px"
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: "20px"
  reading:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: "16px"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  meta:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: "15px"
  number:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  mono:
    fontFamily: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  panel: "16px"
spacing:
  control: "28px"
  control-lg: "32px"
  setting-row: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "9999px"
    height: "{spacing.control}"
  button-secondary:
    textColor: "{colors.ink}"
    rounded: "9999px"
    height: "{spacing.control}"
  button-ghost:
    textColor: "{colors.ink}"
    rounded: "9999px"
    height: "{spacing.control}"
  button-danger:
    textColor: "{colors.danger}"
    rounded: "9999px"
    height: "{spacing.control}"
  button-danger-tinted:
    rounded: "9999px"
    height: "{spacing.control-lg}"
  button-lg:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "9999px"
    height: "{spacing.control-lg}"
  icon-button:
    rounded: "9999px"
    size: "{spacing.control}"
  input:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "{spacing.control}"
  badge:
    rounded: "9999px"
    height: "16px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  menu:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  dialog:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
---

# Design System: Tavotto

> **这是一份索引，不是第二份宪法。** 规矩的正文只有一处：`docs/ux/DESIGN_CONSTITUTION.md`；
> 值只有一处：`web/src/index.css` 的 `@theme`；门禁在 `web/src/components/ui/foundation.test.ts`
> 与 `iconography.test.tsx`。上面的 frontmatter 是给 impeccable / Stitch 这类工具读的
> 机器层，由 `web/src/designMd.test.ts` 与 `index.css` 逐条对拍——改值先改 index.css，
> 这里跟着改，同一次提交。下面每一节只说一两句话并指向宪法的章节，不复制。

## Overview

**Creative North Star: "Paper × Instrument"**

一件用于科研图制作与论文排版的精密仪器：灰色桌面（`#efefed`）上放导航，作品放进一块白色圆角的工作面板（2026-09-30 重设计，参照 OpenBitFun）、
工程工具的精确（28px 控件、单位排成竖线的数字框、毫米制）、桌面软件的成熟。极简但不空洞，
克制但有设计——精致来自比例、对齐、间距、字体层级、图标与状态，**不来自装饰**。
2026-10-07 设计刷新（审计 `docs/ux/DESIGN_AUDIT_2026-10-07_vs_OpenBitFun.md`，用户拍板）的基础层：墨阶拉开、中性面降黄、
圆角族、复合字体角色、状态色锚点派生、z-index token、Card / Notice / StatusPill / FormSection / FieldGroup / RowMenu 原语、
Dialog 页脚三槽——全文在 **宪法第二十六节**。

**Key Characteristics:**
- 一套字体（系统 sans）、十个字体角色（display 24 / heading 17 / title 15 / reading 13 / 12 档四个 / meta 11）；字重 400 / 500 / 600——600 只在角色（标题）与原语（选中态、表单分区标题、危险浅底胶囊）里
- 持久表面里只有卡有投影（`ui/Card`）；分层靠极轻的明度差与 ink 半透明的 hairline（边框 / hover / selected 都是 ink 的 5%～18% 叠加）
- 蓝色只做小面积：焦点环、AI、画布选择框；主按钮是近黑；链接是灰字
- 密度是「紧凑工具」那一档：控件 28px；对话框页脚 / 页面 CTA 32px（`size="lg"`）
- 动效只是点缀：opacity + ≤4px 位移 + scale 0.97~1，关掉不损失信息；回弹只有 `--ease-spring` 一条曲线（峰值 5%，只给落位收尾）

## Colors

中性灰桌面 + 白面板 + 近黑墨 + 一枚小面积品牌蓝；语义色（danger / warn / ok / info）各是**一个锚点**，底 / 描边 / 字由公式派生。
表在 **宪法第一节**（工具类名 ↔ 值 ↔ 用途），派生公式在 **宪法第二十六节**。

### Primary
- **Ink（近黑）** (`#1b1b18`)：主文字、主按钮填色。不是纯黑。主按钮上的字是 `surface` 色（不写死白）。
- **Tavotto Blue（品牌蓝）**：色相取自用户定的 `#5A92E5`（2026-09-30）。它本身对白只有 3.14:1，所以焦点环 / 蓝字用同色相压深的 `#2c73de`（≥4.5:1），画布选择框用 `#4685e2`（≥3:1）；浅底 `#ebf2fc`。只给选择、焦点、AI——链接是灰字（`ink-2` + 悬停下划线）。

### Neutral
- **Desk（桌面）** (`#efefed`) 应用底：顶栏、左轨、停靠的抽屉坐在它上面 · **Canvas（画布灰）** (`#f5f5f3`) 工作面板里画布那一块 · **Surface（白）** (`#ffffff`) 工作面板 / 浮层 / 卡 · **Surface-2** (`#f7f7f5`) 只读值与徽章底 · **Field** (`#f2f2f0`，hover `#eeeeec`) 所有可编辑框的底（比面板深一级、无边；参考 Codex） · **Selected**（ink 10% 叠加：hover 5% < active 8% < selected 10%） · **Group**（ink 3%：设置页字段组的底）
- **Ink-2 / Ink-3 / Ink-faint** (`#4a4a45` / `#6c6c66` / `#a3a39a`)：标签 / 元数据与说明 / 禁用。Ink-2 白上 8.9:1；Ink-3 白 / 桌面 / 画布灰 5.28 / 4.59 / 4.84，**所有底色都过 4.5:1**（拍板值 `#74746e` 在桌面上只有 4.08，按「不过就最小幅度压深」收到这一档；`index.css` 里 `--color-ink-3` 上方的注释是这条的权威）；faint 不用于要读的字
- **Border / Border-strong**（ink 12% / 18% 叠加）：hairline 只给区域边界 · **可编辑框静态没有边**：Field 底就是「框」，聚焦 / 打开才是不透明 accent 边——有框 = 能改，3:1 由聚焦态承担（宪法第二十二节） · **Border-control** (`#84847c`)：未选中复选框 / 单选、关态开关轨道，≥3:1

### Status
- **锚点** danger `#c4442a` · warn `#b07400` · ok `#2b7649` · info = accent。**派生**：`-surface` = 锚点 10% 混白、`-border` = 30%、`-content` = 锚点 70% 压黑（字，≥4.5:1）。新增一种状态色只写一个锚点；旧名 `*-subtle` 是 `-surface` 的别名，迁完删除。

### Syntax
- **代码着色七档** `syntax-keyword / function / string / number / comment / type / builtin`：只读代码块（/try 的 Code Sheet、助手的代码块）共用，组件里不写 hex；每档在白 / 桌面 / surface-2 上 ≥4.5:1，comment 与 ink-3 同值。

### Named Rules
**The Small Blue Rule.** 蓝色不做任何大块背景、不做按钮填色；主按钮是近黑 `bg-ink`。
**The Hairline Rule.** surface 之间靠极轻的明度差与 hairline 分层，不靠框。
**The Anchor Rule.** 状态色只写锚点，底 / 描边 / 字由公式派生；字一律用 `-content`。

## Typography

**Body Font:** 系统 sans（SF Pro Text / PingFang SC / Microsoft YaHei，`--font-sans`）
**Mono Font:** `ui-monospace`（只给代码、路径、脚本名与取值代号，`--font-mono`；数值、快捷键、kbd 都是系统字体 + tabular-nums）
**Document Font:** Times New Roman / Songti SC（`--font-doc`）——只给画布里的文字对象，模拟论文排版，与 UI 字体严格分离。

**Character:** 一套字体；层级由**角色**决定，不由页面自己挑组合。

### Hierarchy
十个角色 `type-display / type-heading / type-title / type-reading / type-section / type-body / type-control / type-caption / type-meta / type-number`，
值与用途见 **宪法第六节**；`text-[Npx]` 不许出现。`type-title` 15 / 600、`type-heading` 17 / 600 / -0.01em、`type-display` 24 / 600 / -0.02em
（17 与 24 只经角色出现，不进字号阶梯）；`type-reading` 13 / 1.6 给阅读面（助手、对话框正文、设置说明）；`type-caption` 12 / ink-3；`type-meta` 11 / ink-3。

## Layout

密度：**宪法第三节**。控件 28px（`h-7`），对话框页脚 / 页面 CTA / 命令面板输入行 32px（`size="lg"`）；行内 gap 按 4 / 8 走，分区之间靠 `Section` 的固定留白；
设置页一行 48px（`SettingRow`，`layout="balanced"` 是 4 : 6 两列；现状只放文字，路径 / 编辑器 / 预览走跨两列的 `below`），分区是 `FormSection` + `FieldGroup`（12 圆角、ink 3% 底、行内边距 12 / 16、内缩分隔线）。
设置外壳：导航 200px（28px 搜索框；项 30px / 13px / 8 圆角，选中 600；组名 12 / 500 / ink-3），内容是居中的 680 一列，每页一个 `type-heading` 页头 + 一句说明。
标签在左、控件在右的紧凑行，控件从同一条竖线起排（`ui/Field.Row`）。属性栏是一张行网格：标签 `clamp(88px, 28cqi, 112px)` · 控件（full / half 两档）·
20px 常驻状态槽（恢复钮、折叠 chevron），修改点悬挂在标签左 8px（**宪法第二十六节末「属性栏与画布栏」**）。
少用容器：**宪法第八节**——留白、对齐、字体层级、hairline 优先，卡片只给真的是一张卡的东西（`ui/Card`）。
层级（z-index）只来自 token：sticky 10 · canvas-chrome 20 · drawer 30 · overlay 40 · dialog 50 · popover 60 · tooltip 70 · toast 80 · onboarding 90（**宪法第二十六节**）。

## Elevation & Depth

**The Flat-By-Default Rule.** 持久表面不用投影——**只有「真的是一张卡」的东西例外**，而且只经 `ui/Card` 一处（`appearance="raised"`，门禁守着）：`--shadow-card`（环 6% + 0 1px 2px 4% + 0 4px 12px 4%）；分区、列表行、输入框、分段控件仍是平的。改图助手的输入框是浮在对话流上的玻璃（`--color-glass` field 90% + 16px 背景模糊 + `--shadow-composer`，参考 Codex）。浮层是「1px 半透明环 + 一层大模糊」，不画实色边；所有投影都写成 `color-mix(var(--color-shadow) N%)`，暗色只换 `--color-shadow`：
`--shadow-pop: 0 0 0 1px color-mix(in srgb, var(--color-shadow) 8%, transparent), 0 8px 24px color-mix(in srgb, var(--color-shadow) 8%, transparent)`（菜单 / popover / 浮条），对话框与命令面板用更深一档的 `--shadow-dialog`；Tooltip 是 ink 底白字，不带投影。

## Shapes

圆角族，Tailwind 自带的 xl / 2xl 已清空：xs 4（16px 高以下的小片）、sm 6（tooltip、行内小片）、md 8（行、可编辑框、菜单项、小卡 / 缩略图、说明条）、
lg 12（卡片、菜单 / popover 外壳、多行浮动面板）、panel 16（对话框、工作面板、助手输入框、命令面板）、full（带字的按钮、图标钮、分段控件、chip、toast、单行浮动条）。
**外层圆角 = 内层圆角 + 内边距**。浮动外观三档：单行浮动条 = 胶囊、多行浮动面板 = 12、模态 = 16。**宪法第二节、第二十六节**。

## Components

全部原语在 `web/src/components/ui/`，形态与状态在 **宪法第五节**：Button 五档（primary / secondary / ghost / danger / danger-tinted，高 28 / 32）、IconButton（圆）、
TextInput / NumberField（框内单位）、Select（全仓唯一的下拉）、Checkbox、Toggle（名字必填）、
Badge、StatusPill、Notice、Card、Tabs（选中 600 + 2px 下划线）、Segmented（灰容器 + 白色浮起的 thumb，选中 600）、listRowClass（28 / 44 / 52，选中 600）/ rowMetaClass / dropLineClass / TreeRow、
RowMenu（⋯ + 右键 + ⇧F10 同一份菜单）、dropZoneClass（拖放接收态：静态不画、拖入才是 accent 虚线 + 浅底 + 外发光）、SearchInput、Section / Disclosure、FormSection / FieldGroup、EmptyState（40px 图标底座 + 15 / 600 标题）、
Dialog（sm 400 / md 480 / lg 560 / xl 760 / shell；页脚 `{ start, secondary, primary }` 三槽、32px；栈底才画遮罩；Esc = 安全答案）。
四态：hover（surface-hover 5%）< active（surface-active 8%）< selected（selected 10% + 字重 / 对勾）；
disabled 统一 `opacity-40 + cursor-not-allowed`。光标一律箭头（可拖的卡用抓手）。每个上下文一颗主按钮；对话框里的破坏性确认是浅底危险胶囊，不是实心红。
图标只有自绘的一套（`web/src/components/ui/icons/`，ADR 0052；说明书 `docs/ux/ICONOGRAPHY.md`）。

动效：**宪法第七节**。时长只来自 token（fast 120 / base 180 / slow 240 / exit 90），
进场 `--ease-pop`、退场 `--ease-exit`、落位收尾 `--ease-spring`、其余 `--ease-standard`；没写时长的
`transition-*` 默认就是 fast + standard（`--default-transition-*`）；`prefers-reduced-motion` 是硬约束。
加载只有四种写法：不定进度 `animate-sweep`、静态骨架、进行中的字 `text-shimmer`、转圈；Tailwind 的呼吸动画被门禁禁了。
改图助手对话区（流式逐词淡入 / 亮带状态 / 发送 ↔ 中止同钮 / 贴底跟随）见 **宪法第十八节**。
通知轨的计时会让路（hover / focus / 页面不可见时不走表）、同一位置换文字原位换（`ui/SwapText`）见 **宪法第二十三节**。

## Do's and Don'ts

### Do:
- **Do** 改值先改 `index.css`，改规矩先改宪法，同一次提交；这里只是镜像。
- **Do** 用角色（`type-*`）定字体层级，用 token 定时长与层级（`z-*`），用 `IconButton` 的 `label` 同时给可达名与气泡。
- **Do** 卡片用 `ui/Card`，状态说明用 `Notice` / `StatusPill`，对话框页脚用三槽。
- **Do** 让品牌名只来自 `web/src/lib/brand.ts`。

### Don't:
- **Don't** 给蓝色大块背景或按钮填色；不用实心红按钮。
- **Don't** 在 `Card` 以外写 `shadow-card`，或用第二种投影。
- **Don't** 写 `text-[Npx]`、`rounded-xl`、任意值圆角、数字 z-index、手形光标类、第二套下拉、第二种开关。
- **Don't** 在注释里写完整的 Tailwind 类名（扫描器会把它编进产物 CSS）。
