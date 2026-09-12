---
name: Tavotto
description: matplotlib 科研图的可视化编辑器——Paper × Instrument，紧凑的桌面工具
colors:
  bg: "#f2f2ef"
  canvas: "#eaeae6"
  surface: "#ffffff"
  surface-2: "#f7f7f4"
  border: "#e3e3dd"
  border-strong: "#cfcfc7"
  ink: "#1b1b18"
  ink-2: "#5c5c55"
  ink-3: "#6b6b64"
  ink-faint: "#a3a39a"
  accent: "#2868b7"
  accent-subtle: "#e9f0f9"
  selected: "#ebebe6"
  danger: "#c4442a"
  danger-subtle: "#fdf3f1"
  warn: "#8a5a00"
  warn-subtle: "#f7efe0"
  ok: "#2e7d4f"
  ok-subtle: "#e6f3ea"
  sel: "#2f6fed"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: "20px"
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: "15px"
    letterSpacing: "0.06em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: "16px"
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: "15px"
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
  meta:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', 'Microsoft YaHei', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: "15px"
  mono:
    fontFamily: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"
    fontSize: "11px"
rounded:
  xs: "3px"
  sm: "6px"
  md: "8px"
  lg: "12px"
spacing:
  gap-sm: "4px"
  gap-md: "8px"
  control: "28px"
  setting-row: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    height: "{spacing.control}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "{spacing.control}"
  button-ghost:
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "{spacing.control}"
  button-danger:
    textColor: "{colors.danger}"
    rounded: "{rounded.sm}"
    height: "{spacing.control}"
  icon-button:
    rounded: "{rounded.sm}"
    size: "{spacing.control}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "{spacing.control}"
  badge:
    rounded: "9999px"
    height: "16px"
  menu:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
  dialog:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
---

# Design System: Tavotto

> **这是一份索引，不是第二份宪法。** 规矩的正文只有一处：`docs/ux/DESIGN_CONSTITUTION.md`；
> 值只有一处：`web/src/index.css` 的 `@theme`；门禁在 `web/src/components/ui/foundation.test.ts`
> 与 `iconography.test.tsx`。上面的 frontmatter 是给 impeccable / Stitch 这类工具读的
> 机器层，由 `web/src/designMd.test.ts` 与 `index.css` 逐条对拍——改值先改 index.css，
> 这里跟着改，同一次提交。下面每一节只说一两句话并指向宪法的章节，不复制。

## Overview

**Creative North Star: "Paper × Instrument"**

一件用于科研图制作与论文排版的精密仪器：微微的纸张感（暖灰白底 `#f2f2ef`，不黄不米）、
工程工具的精确（28px 控件、单位排成竖线的数字框、毫米制）、桌面软件的成熟。极简但不空洞，
克制但有设计——精致来自比例、对齐、间距、字体层级、图标与状态，**不来自装饰**。

**Key Characteristics:**
- 一套字体（系统 sans）、四档字号（11 / 12 / 13 / 14）、两档字重（400 / 500）
- 持久表面没有投影；分层靠极轻的明度差与 hairline
- 蓝色只做小面积：焦点环、链接、AI、画布选择框；主按钮是近黑
- 密度是「紧凑工具」那一档：控件一律 28px
- 动效只是点缀：opacity + ≤4px 位移 + scale 0.97~1，关掉不损失信息

## Colors

暖灰白纸面 + 近黑墨 + 一枚小面积品牌蓝；语义色（danger / warn / ok）只表达语义，各带一档 subtle 底。
表在 **宪法第一节**（工具类名 ↔ 值 ↔ 用途）。

### Primary
- **Ink（近黑）** (`#1b1b18`)：主文字、主按钮填色、选中态的字重。不是纯黑。
- **Tavotto Blue（品牌蓝）** (`#2868b7`)：焦点环、链接、AI 入口、画布选择框——小面积。

### Neutral
- **Paper（纸面）** (`#f2f2ef`) 应用底 · **Canvas（画布灰）** (`#eaeae6`) · **Surface（白）** (`#ffffff`) 面板 / 输入框 / 浮层 · **Surface-2** (`#f7f7f4`) 只读值与徽章底 · **Selected** (`#ebebe6`)
- **Ink-2 / Ink-3 / Ink-faint** (`#5c5c55` / `#6b6b64` / `#a3a39a`)：次级、元数据、禁用；前两档在所有底色上 ≥4.5:1，faint 不用于要读的字
- **Border / Border-strong** (`#e3e3dd` / `#cfcfc7`)：hairline 只给输入框、区域边界、浮层

### Named Rules
**The Small Blue Rule.** 蓝色不做任何大块背景、不做按钮填色；主按钮是近黑 `bg-ink`。
**The Hairline Rule.** surface 之间靠极轻的明度差与 hairline 分层，不靠框。

## Typography

**Body Font:** 系统 sans（SF Pro Text / PingFang SC / Microsoft YaHei，`--font-sans`）
**Mono Font:** `ui-monospace`（数字框、路径、代码，`--font-mono`）
**Document Font:** Times New Roman / Songti SC（`--font-doc`）——只给画布里的文字对象，模拟论文排版，与 UI 字体严格分离。

**Character:** 一套字体、四档字号、两档字重；层级由六个**角色**决定，不由页面自己挑组合。

### Hierarchy
六个角色 `type-title / type-section / type-body / type-control / type-caption / type-meta`，
值与用途见 **宪法第六节**；`text-[Npx]` 不许出现，`type-section` 大写 + 0.06em 字距。

## Layout

密度：**宪法第三节**。控件一律 28px（`h-7`），行内 gap 按 4 / 8 走，分区之间靠 `Section` 的固定留白；
设置页一行 48px（`SettingRow`）。标签在左、控件在右的紧凑行，控件从同一条竖线起排（`ui/Field.Row`）。
少用容器：**宪法第八节**——留白、对齐、字体层级、hairline 优先，卡片只给真的是一张卡的东西。

## Elevation & Depth

**The Flat-By-Default Rule.** 持久表面不用投影；唯一允许的投影是浮层专用的
`--shadow-pop: 0 6px 20px rgba(27, 27, 24, 0.09)`（菜单 / popover / dialog / tooltip）。

## Shapes

四档圆角 + full，Tailwind 自带的 xl / 2xl 已清空：xs 3（16px 高以下的小片）、sm 6（控件）、
md 8（卡片与浮层）、lg 12（对话框、命令面板）、full（圆点、开关、徽章）。**宪法第二节**。

## Components

全部原语在 `web/src/components/ui/`，形态与状态在 **宪法第五节**：Button 四档、IconButton、
TextInput / NumberField（框内单位）、Select（全仓唯一的下拉）、Checkbox、Toggle（名字必填）、
Badge、Tabs、listRowClass / TreeRow、SearchInput、Notice、Section / Disclosure。
四态：hover（surface-hover）< active（surface-active）≈ selected（selected + 字重 / 对勾）；
disabled 统一 `opacity-35~40 + cursor-not-allowed`。图标只有 lucide 一套（`docs/ux/ICONOGRAPHY.md`）。

动效：**宪法第七节**。时长只来自 token（fast 120 / base 180 / slow 240 / exit 90），
进场 `--ease-pop`、退场 `--ease-exit`；`prefers-reduced-motion` 是硬约束。

## Do's and Don'ts

### Do:
- **Do** 改值先改 `index.css`，改规矩先改宪法，同一次提交；这里只是镜像。
- **Do** 用角色（`type-*`）定字体层级，用 token 定时长，用 `IconButton` 的 `label` 同时给可达名与气泡。
- **Do** 让品牌名只来自 `web/src/lib/brand.ts`。

### Don't:
- **Don't** 给蓝色大块背景或按钮填色。
- **Don't** 给持久表面加投影，或用第二种投影。
- **Don't** 写 `text-[Npx]`、`rounded-xl`、第二套下拉、第二种开关。
- **Don't** 在注释里写完整的 Tailwind 类名（扫描器会把它编进产物 CSS）。
