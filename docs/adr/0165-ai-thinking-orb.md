# ADR 0165：改图助手的思考小球（点阵小球代替转圈 / 线稿图标）

日期：2026-11-03 · 状态：**Accepted**（用户 2026-11-03 在演示页上逐项拍板，「按推荐的来，开 PR」）
相关：`docs/rules/frontend/ui-visual-discipline.md`（加载、图标 ②④）；`docs/ux/DESIGN_CONSTITUTION.md` 第七节「加载」；
[0156 图标微动效](0156-icon-micro-motion.md)；上游 [thinking-orbs](https://github.com/Jakubantalik/thinking-orbs)（MIT）。

## 动机

用户看了 thinking-orbs（给 AI / agent 界面用的点阵「思考小球」，9 种形态、2D canvas），要改图助手用上，并且「魔改成适配我们的」。
改图助手原来的状态只有一道 shimmer 加 Sparkles / Wrench 线稿图标；用户嫌「已思考」前的星星「太简单，没有设计感」，并要求**结束后的
图标也一直在动**。

原规则有两条挡着：加载只许 sweep / 静态骨架 / shimmer / 转圈（禁呼吸动画），「加载中」只有 `ui/Spinner`；空状态图形只用 `ui/Sketch`
线稿封闭集。用户裁决放开，范围只到改图助手。

## 决定

### 放在哪里、用哪种形态（封闭集，按「这一步在干什么」选）

| 位置 | 尺寸 | 形态 | 墨色 / 速度 |
| --- | --- | --- | --- |
| 过程组进行中，最新一步是思考 | 16（`row`） | `breathing` | ink、正常 |
| 过程组进行中，最新一步是运行脚本（`$ …`） | 16 | `solving` | ink、正常 |
| 过程组进行中，最新一步是其他动作（读 / 改） | 16 | `composing` | ink、正常 |
| 发出后还没回第一个字（状态行前） | 16 | `connecting` | ink、正常 |
| 过程组结束，纯思考 | 16 | `breathing` | ink-3、半速（`settled`） |
| 过程组结束，动过手 | 16 | `listening` | ink-3、半速 |
| 还没开始对话的空白面板 | 64（`hero`） | `searching` | ink-3、半速 |

展开后的每一步（`StepRow`）仍是静态图标；文件读写、导出、安装这类「加载」照旧 `Spinner`。

### 只抄几何，自己画

- `web/src/components/ui/thinkingOrb/engine/` 是上游 `src/engine/` 的纯几何（逐字搬运，只去掉分号与上游的 canvas 画笔），
  每个文件头标出处与提交；不装 npm 包。许可证全文在 `THIRD_PARTY_NOTICES.md`。
- 画笔 `thinkingOrb/paint.ts`：上游按深浅写死灰度值，这里统一用一种墨色——canvas 自己计算后的 `color`（`text-ink` / `text-ink-3`
  的语义 token），深浅只经 `globalAlpha`（最近 1、最远 0.18，16px 提到 0.32）。亮暗同源，换主题（`useEffectiveTheme`）当场重读重画。
- 像素对齐：半径不到 0.6 物理像素的点画成整像素方块，其余圆心吸附 0.5 物理像素（与 10-31 图标清晰度同一做法）。
- 尺寸：64 用上游原调参；16 由上游 20 那档派生（点数 ×0.55–0.7、点径 ×1.3），在演示页上看过。全局速度 0.75×。

### 循环

所有小球共用一个时钟和一个 rAF 循环；滚出视口的不画（`IntersectionObserver`），长对话里的历史小球不会同时在转；标签页隐藏时不画；
`prefers-reduced-motion` 下画一帧静止姿态、不进循环。宿主不发 rAF 时（演示页在预览框里实际碰到过）由 100ms 的定时器兜底。
测试里由 `test/setup.ts` 的全局 afterEach 停掉（错题本 L-087）。

## 不做

- 不给按钮、卡片之类开放呼吸动画：`foundation.test` 照旧禁 `animate-pulse`，放开的只有思考小球。
- 不给小球加第七种形态；新位置要用，先改本 ADR 的表。
- 不改「展开后每一步」的图标、不改 `Spinner` 的其他调用处。

## 1.0 纪律

这是改图助手的视觉表现，不扩大产品能力（不加端点、不改协议、不动文档与写回）。上游代码只进前端、无运行时依赖。
