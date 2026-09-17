# 验证

> 原文出自 `web/AGENTS.md`「验证」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `cd web && pnpm test`（vitest+jsdom；NODE_OPTIONS 里禁用了 node 内建
  webstorage，否则 jsdom localStorage 被遮蔽）。
- `pnpm build` = 类型检查（`tsc -b`）+ 打包。**别用 `tsc --noEmit` 当类型检查**：
  根 tsconfig 是 `files:[]`+references 的方案文件，`--noEmit` 不走项目引用、
  什么都不编、恒假绿。
- 跑过 `scripts/build_frontend.py` 之后包内 `src/tavotto/web/` 优先于 `web/dist`，
  改完前端要么再同步一次，要么把它删掉退回开发态。
- **改了 web/src**：playground 产物 `python scripts/build_browser_playground.py`（/try，网站
  仓库提交，`--check` 防漂移）照旧；Codex 内嵌画布 `python scripts/build_mcp_widget.py`
  **不再入库**（ADR 0043）——本地构建只为试用，CI 从每次 checkout 现建并验证完整插件，
  两个各改 `web/src` 的 PR 不再为同一份 HTML 相撞。
- **Tailwind 的扫描面 = `web/src`，散文不再被扫**（2026-09-14，issue #322）：`src/index.css`
  第一行 `@import 'tailwindcss' source('../src')`（路径相对那个 CSS 文件）。此前没有显式声明，
  Tailwind 按 vite 根自动探测、连 `.md` 与 `e2e/` 一起扫，扫描器又不分代码和注释——一句
  散文里出现某个「还没人真用过」的工具类名，它就被当成用到了，往产物 CSS 里凭空加一条规则
  （2026-09-03 #210 的 PR：一条 e2e 注释给 `canvas.html` 加了一条淡出动画的规则；#319 反证时
  又量到本文件的一句话独自吊着一条规则）。现在 `.md` / `e2e/` / `scripts/` / 三个 HTML 入口
  都在面外，文档可以正常写类名。**`web/src` 里的代码注释仍在面内**（含 `*.test.tsx`），
  那里的完整类名照样会进产物（`ui/Checkbox.tsx` 注释里的 `accent-ink-2` 就是活例）——
  要提就写成不成立的形态或用中文描述。三条构建链共用这一份 CSS，声明一次三份产物都收窄。
  门禁 `scripts/tailwind-scan-check.mjs` 接在 `pnpm build` 后、读产物 CSS，两向各一条：
  正向抽 11 处真实用法（字面量 / 模板字符串 / 变体 / 任意值 / `@utility` / 主题 token）必须在；
  反向靠一只**常驻在本文件里的金丝雀**——`tracking-widest` 是产品里没人用的真实工具类，
  它只出现在这段散文里，产物里**不许**有它的规则；本文件里这个词被删掉时门禁同样红（少了
  输入的反向判据恒真）。哪天产品真要用它，换一只并把脚本里的 `CANARY` 一起换。
  画布不再入库，看不到 diff 了——判据换成 CI `plugin-candidate` job 里真起 server 读回
  的资源与构建物逐字相同；想本地对比就构建两次到不同 `--out` 再 diff。
- 界面用 agent-browser 实测；黄金路径 E2E `cd web && pnpm e2e`（Playwright，
  先 `python scripts/build_frontend.py`）。`e2e/mcp-canvas.spec.ts` 还要
  `python scripts/build_mcp_widget.py`——`canvas.html` 不再入库（ADR 0043），没建过的
  工作区上那四条会以 ENOENT 红，CI 每次 checkout 现建所以看不到这一幕。
- **控件的可访问名是必填的，靠外面包一层 `<label>` 不算数**（2026-09-07，#299 webkit 腿）：
  HTML-AAM 给 `button` 的取名方式是「name from content」，`Toggle` 那颗 `<button role="switch">`
  的内容只有两个装饰用的 `<span>`——`<label>` 包着它只保证点文字能切换，**不给它取名**。
  chromium 大方地把标签文字算了进去，所以 posix 腿一直是绿的；webkit 按规范办事，axe 当场
  报 `button-name` critical。同一屏还有 `ColorField` 的两个输入框（取色盘 + 十六进制），
  一行可见标签既不是 `<label for>` 也指不了两个控件，报 `label` critical。
  **两个组件的名字现在是类型必填的**：`ColorField` 要 `ariaLabel`，`Toggle` 要
  `aria-label` 或 `aria-labelledby`（二选一，联合类型）——一次性补齐会漏，类型必填才不会烂。
  给名字时**用渲染那句可见文字的同一个表达式**，别另写一句同义的（审计 T39 担心的分叉）；
  `SettingRow` 的标签自带 `settingRowLabelId(controlId)`，那一族用 `aria-labelledby` 指它。
  看护：`e2e/a11y.spec.ts` 的「图内编辑的属性栏」——它把属性栏里每个折叠区**一个不剩地展开**
  再扫（`aria-expanded="false"` 且非弹层触发器、非禁用），收起来的控件 axe 看不见，
  「这一屏干净」原本只说明「默认展开的那部分干净」。锚点 `data-inspector-panel`。
- **横向溢出只有一把尺子：`e2e/overflow.ts` 的 `horizontalOffenders(page, rootSel)`。**
  逐个元素扫、只认 `overflow-x: visible`，并且**只量 HTML 元素**——SVG 里的
  `scrollWidth` / `clientWidth` 量的不是页面宽度（样式页示例图那条 `rotate(-90)` 的轴标题
  报 `sw=66 cw=21`，两个数是同一段字的两种量法），主语错了只会产出假红。这条原先只补进了
  `settings-shell.spec.ts` 那一份抄本，另外两份带着盲区活到 2026-09-07 才被发现（#299），
  所以现在只留一份，新用例一律 import 它、别再抄第四份。
