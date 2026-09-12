# Product

<!-- impeccable:product-schema 1 -->

本文只写**产品真相**（谁用、做什么、什么不能变），供 impeccable 的各命令读取。
视觉参数不在这里：规矩在 `docs/ux/DESIGN_CONSTITUTION.md`，值在
`web/src/index.css` 的 `@theme`。仓库级规则仍以根 `AGENTS.md` 为准。

## Platform

web

React 19 + Vite + Tailwind v4 的同一套界面，交付到三个宿主：Tauri 桌面应用
（macOS arm64 / Windows x64 supported）、Codex 插件内嵌画布（MCP widget，
`canvas.html`）、浏览器 playground（tavotto.com/try，Pyodide）。Linux 没有桌面
安装包，只有 pip / pipx 的浏览器模式（beta）——支持等级以 `docs/support-matrix.json`
为准，这里不另写一份。Tauri 只是壳，设计语言不是原生 macOS / Windows。

## Users

**主用户（2026-09-12 确认）**：绘图脚本大部分由 Codex / Claude 写出来的科研人员。
人做的是收尾——看着不对、点一下、改掉。**不能假定他们熟悉 matplotlib 术语。**

次要用户（README 的原始叙事，仓库证据，未被排为主用户）：自己写 matplotlib
脚本、投稿前要把图变成合格 Figure 1 的研究生 / 博后 / PI；懂 matplotlib，但不想
为了挪一个图例再跑一遍脚本。

贡献者不是用户；README 明确要求普通用户不要克隆或构建仓库。

## Product Purpose

打开 matplotlib 已经画出来的图，点标题、图例、曲线，就地修改；把多个面板按毫米
排成一页；按期刊规则预检；导出投稿级 PDF / PNG 等。

**成功 = 「Figure 1」做完了，而脚本一次都没有重跑。**

**aha 时刻（确认）**：点标题、把 9 pt 改成 11、图变了、脚本一个字没动。
Onboarding 与空状态一律往这一刻引，不先教别的。

## Positioning

- **知道自己在编辑什么。** 矢量编辑器看到的是路径和字形；Tavotto 看到的是标题、
  图例、刻度、数据系列、色条。（README「Where Tavotto fits」）
- **改动始终挂在脚本上。** 每次修改存为文档旁的 override，在下一次打开时重放到
  脚本的全新一次运行上；撤销、版本历史、导出质量重渲染都建立在同一机制上。
- **导出前按期刊规则检查。** 规则是一份带版本的 JSON
  （`src/tavotto/profiles/publication.json`），Python 引擎与 TS 前端共读，无第二份。
- **不发明图的内容。** 新曲线、新面板、新数据仍来自脚本——这是刻意的边界，不是缺口。

## Operating Context

- **三个宿主、一套界面**：桌面应用；Codex 会话内（插件的 MCP 工具 + 内嵌画布）；
  浏览器 /try（不装任何东西的试用）。
- **与编码 Agent 的往返**：Codex 改了绘图脚本会调 `tavotto_refresh_project`，
  Tavotto 只做静态分析、不跑脚本，已打开的窗口自己刷新。可选的助手面板把请求交给
  本机的 Codex / Claude CLI 改脚本：先快照，再刷新、看 diff、一键回退；刷新失败时
  状态行如实说失败，不假装整次编辑成功。
- **文件位置**：导出、命名画布、版本历史都在项目内的 `tavottofile/`；文档与自动
  保存在系统应用数据目录；用户的脚本与图**只读**，除非显式「写回原文件」
  （写回会从头重跑脚本证明结果一致；可按项目锁死）。
- **全部本机运行**；插件装进本地 `~/.codex`，云端会话看不到工具。
- **运行环境**：Python 3.10–3.13；worker 依赖 matplotlib ≥3.8,<3.12（发行与实验室
  验证档 3.11；开发机往往不是发行档）；`tavotto run`（接手用户项目 .venv）为 Beta。
- **语言**：zh-CN / en-US 两档同为一等公民，默认 zh-CN，系统语言为第三种时落到
  en-US。用户自己的内容（项目名、文件名、脚本、图内文字、matplotlib 输出）永远不翻。
  **任何界面评审都要两档各看一份**（2026-09-12 确认）。

## Capabilities and Constraints

**能改什么**（README「What you can edit inside a figure」，与实现同源）：
文本（标题、轴标签、刻度标签、图例项、注释；可拖）；数据系列（线宽、虚线、颜色、
marker、图例顺序）；箭头（`FancyArrowPatch` 整体或端点可拖，`annotate()` 的箭头
只改样式）；坐标轴（locator / formatter、刻度线、网格、四条 spine、范围、比例、
纵横比；拖子图时归属它的标签、色条、twin 轴一起走）；色条（方向、extend、色图、
范围、刻度样式，就地重建）；3D 轴（视角、投影、轴线、pane、网格、逐轴刻度）；
图（毫米尺寸、背景）。

**术语立场（2026-09-12 确认）**：**界面用 matplotlib 原生词**（spines、tick
locator、FancyArrowPatch……），不另起一套出版语言。理由：写回源码时不能有两套名字，
用户回头读脚本时词要对得上。与主用户「不一定熟悉这些词」的张力，由**就地解释**
（tooltip、说明文字、问题面板的措辞）解决，而不是改名。

**硬约束**：
- 产品名只能来自 `web/src/lib/brand.ts` / `engine/brand.py`；界面与导出格式不得手写。
- 支持矩阵唯一权威 `docs/support-matrix.json`；宣传文案不得超出它。
- 新增文案必须双语齐全（`pnpm i18n:check` 是 CI 硬门禁）。
- 注释里不写完整的 Tailwind 类名（扫描器会把它编进产物 CSS）。
- 控件可访问名是类型必填的（`Toggle` / `ColorField`）。
- 横向溢出只认 `web/e2e/overflow.ts` 那一把尺子。
- 「近似预览」必须打角标；磁盘原图不许冒充 override 渲染结果。

**明确未决**：无（本次访谈的四个问题都已有答案）。

## Brand Commitments

- 名称 **Tavotto™**（README 有 Trademark 节；许可证 AGPL-3.0-only）。
- 品牌资产：`assets/brand/`（lockup / mark / mono / reverse 四组 SVG）；
  README 主视觉 `assets/readme/hero.svg`、`assets/readme/workbench.png`。
- **已有并具约束力的视觉系统**（本文不扩写，只指路）：
  `docs/ux/DESIGN_CONSTITUTION.md`——方向「Paper × Instrument」，值在
  `web/src/index.css` `@theme`，门禁 `web/src/components/ui/foundation.test.ts`
  与 `iconography.test.tsx`。改值先改 index.css，改规矩先改宪法，同一次提交。
- 语气：README 的口吻——直说事实、不吹、把边界（「不做什么」）当卖点讲。

## Evidence on Hand

- 两个普通 matplotlib 脚本 `examples/figures/fig1_kinetics.py`、
  `fig2_comparison.py`（+ `paper_style.py`），README「Try it in 30 seconds」用的就是它们。
- 产品截图：`assets/readme/workbench.png`（左树 / 中页 / 右属性栏，含源文件名）。
- 期刊规则样本：`src/tavotto/profiles/publication.json`（`lab-publication-v1`）。
- 性能基线 `docs/perf-baseline.md`；支持矩阵 `docs/support-matrix.json`。
- 可访问性证据：`web/e2e/a11y.spec.ts`（axe，chromium + webkit 两腿）。
- **没有的东西，不得编造**：用户证言、案例研究、客户 / 机构 logo、使用量数字、
  期刊背书。

## Product Principles

1. **脚本是源，界面从不遮掩这一点。** 属性栏旁边永远看得到那个 `.py` 文件名；
   任何会碰源码的路径都要用户点名要求。
2. **就地教词，不改词。** 主用户可能不认识 spine，但界面上的词必须与他将要读、
   将要写回的脚本一致；解释放在词旁边。
3. **一次点击到达价值。** 第一分钟只做一件事：点、改、看到图变了、看到脚本没动。
   其余能力靠上下文发现。
4. **诚实的状态优于假装的成功。** 刷新失败要说、预览近似要标、不支持的属性要列出来
   （`UnsupportedProps`），不把「拿到空的」当「成功」。
5. **不发明内容。** 新图、新数据来自脚本；产品的边界本身是承诺。

## Accessibility & Inclusion

- 目标：axe 在 chromium 与 webkit 两腿零 violation（`web/e2e/a11y.spec.ts`，属性栏
  逐个折叠区展开后再扫）。
- 正文对比度 ≥4.5:1 已按 token 实测并写在 `index.css` 注释里；`ink-faint` 不用于要读的字。
- `prefers-reduced-motion` 有 e2e 覆盖。
- 双语界面：日 / 法 / 德语系统的用户第一屏是英文，不是简体中文。
