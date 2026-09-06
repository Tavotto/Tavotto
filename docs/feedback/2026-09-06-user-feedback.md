# 用户反馈批次 2026-09-06

来源：产品所有者亲手试用后的一批反馈（原话见各条「反馈」栏）。
处理方式：集成分支 `feat/user-feedback-2026-09-06`，每条反馈由一个独立子 Agent
在各自的 `uf/NN-*` 分支上复现 → 修复 → 反证，再串行合回集成分支。
本文件是这一批的台账：每条反馈的原话、根因、处置、验证与遗留。

状态取值：`待处理` / `已修复` / `部分完成` / `不是缺陷` / `需用户拍板`。

| # | 反馈（原话） | 分支 | 状态 |
| --- | --- | --- | --- |
| 1 | 在新手教学案例中，我双击示例图片，并不能进入图内编辑。 | `uf/01-tutorial-dblclick` | 已修复 |
| 2 | 对于散点图而言，在选中时，仍然是一个很大的矩形框将其包裹，而不是所有散点的圆形轮廓出现被选中蓝色框。 | `uf/02-scatter-outline` | 已修复 |
| 3 | 目前对于图内中文无法正常渲染，需要增加其适配性。 | `uf/03-cjk-fonts` | 待处理 |
| 4 | 导出功能要增加 eps 和 Tiff 格式。 | `uf/04-eps-tiff-export` | 待处理 |
| 5 | 我目前电脑上明明安装了 Tavotto 的 codex 插件，为什么在编码 Agent 里面还是显示插件市场登记失败未登记。 | `uf/05-codex-marketplace` | 已修复 |
| 6 | 导出中的原图尺寸导出还不好用，我目前已经选中了一个原图，但是还是显示「先选中一张图，才能按原图尺寸导出。」我希望这里做的更好一点，可以直接预览目前的几个图片，用户直接点击就可以。 | `uf/06-original-size-picker` | 已修复 |
| 7 | 目前 Tavotto 里面的图标非常不统一，太丑了，参考 morphicons.com 来统一图标。 | `uf/07-icon-unify` | 待处理 |
| 8 | （原话为空，用户没有写完） | — | 待用户补充 |

说明：第 4、6、7 条属于 1.0 收敛纪律里的「扩大产品能力」，由产品所有者明确
要求，按其决定执行；改动范围限定在反馈点本身，不趁机重写相邻模块。

## 逐条记录

（各条由对应子 Agent 的报告整理而来，合回集成分支时补齐。）

### 5. 插件市场「登记失败」——判据没错，问的那个进程跑不起 codex

- **判定**：不是用户没登记。本机 `codex plugin marketplace list --json` 与
  `codex plugin list -m tavotto --json` 都说 tavotto 已登记、已安装、已启用
  （codex-cli 0.151.0，快照仍在 legacy-local 通道）。
- **根因**：`engine/codexinstall.py` 的市场/插件状态探测用裸 `_run([codex, …])`
  起子进程，继承调用方环境。桌面壳从 Finder 启动时 PATH 只有
  `/usr/bin:/bin:/usr/sbin:/sbin`（`ps -Ewwp` 实测），`/opt/homebrew/bin/codex`
  是 `#!/usr/bin/env node` 的 npm shim，子进程里找不到 node → 退出 127 →
  state=unknown → 界面显示「插件市场登记 失败」。`src/tavotto/AGENTS.md`
  早规定 CLI 子进程一律走 `ai_agents.spawn_env()`，这个模块漏了。
- **处置**：8 处 codex 调用收口到 `_codex_run()`，环境用 `spawn_env(codex)`；
  「问不到」两档的 detail 明说「这不等于没登记 / 没装」，认出缺 node 时给处方；
  zh-CN / en-US 两份 `*_state_unknown` 文案同步去掉「多半是 Codex 本身没启动好」。
- **用例**：`tests/test_codex_install_cli.py` +2（最小 PATH 下 npm shim 仍可问到；
  unknown 提示点名缺 node）。反证：env 改回 None → 红；hint 不认 node → 红。
- **验证**：ruff 全过；相关 pytest rc 0；`pnpm test` 2586 全过；`pnpm build` 过；
  修后最小 PATH 下真 codex doctor 7 步全绿。
- **用户侧**：修复合入前，从终端启动桌面版或直接在终端跑 `tavotto codex doctor`
  即为绿。市场快照换 plugin-stable 通道需自己跑
  `codex plugin marketplace upgrade tavotto`，未替用户执行。
- **遗留**：只在 macOS 复现；Windows 快捷方式启动的 PATH 未验。
  `plugin_python()` 在最小 PATH 下取到 `/usr/bin/python3` 的问题不在本条范围。

### 1. 教程画布双击进不了图内编辑——换文档之后没人再对账

- **根因**：随包分发的 `resources/tutorial_project/tavottofile/Tutorial.json` 里
  p1/p2 面板本来就没有 `script`（静态文件不知道副本落在哪）；`script` 由
  `store/panelSourceSync.ts` 按 `/api/panels` 原地补，但对账只在 SSE 事件与
  `Workspace` 挂载那一次触发。第一次从项目选择器进教程恰好赶上挂载，所以能用；
  「重新开始教程」、在别的项目里点「开始教程」、切回教程项目等都是挂载之后
  `switchDocument`，没人再对第二次账 → `ObjectView.tsx` 双击判据 `obj.script`
  缺席 → 走裁剪态而不是 `enterElementEdit`。真浏览器复现：重置后双击 p2 出现
  「完成裁剪」按钮。
- **处置**：`store/liveSync.ts` 新增 `startDocumentLoadSync()`，订阅
  `documentStore.loadSeq`，整份换文档就在下一个微任务里 `syncLoadedDocument()`；
  `App.tsx` 的 Workspace effect 起停它。挂载那次显式对账保留。
- **用例**：`useServerEvents.test.ts` +3、`lib/onboarding/tutorial.test.ts` +2、
  e2e `tutorial.spec.ts` 「重新开始教程」末尾追加双击 p2 必须进入图内编辑。
  反证：订阅恒早退 → 3 条正向红；App.tsx 拔掉订阅 + 重建 dist → e2e 红。
- **验证**：`pnpm test` 2591 全过；`pnpm build` 过；e2e 该条 chromium 1 passed；
  agent-browser 修前/修后截图在 scratchpad/uf-01/s8.png、s11.png。
- **遗留**：无需用户拍板。

### 6. 「先选中一张图」——对话框只读快速编辑的 activePanelId，看不见画布选区

- **根因**：`ExportDialog.tsx` 的 `figureId` 只从 `workspaceStore.activePanelId` 取，
  而按 ADR 0028 它只在 `fast_edit` 模式非空；画布排版模式的选区在
  `selectionStore.ids`，对话框从没读过。真浏览器复现：画布点中 Fig2_yield
  （属性栏已显示它）→ 导出 → 原图尺寸灰、红字「先选中一张图」
  （scratchpad/uf-06/03-export-bug.png）。
- **处置**：新增 `web/src/lib/exportFigures.ts`，「按原图导哪一张」只在这里判：
  候选 = 文档面板（激活画布优先）+ 还没上画布的素材；上下文 = 快速编辑中的
  → 画布主选（主选是文字则退到选区第一个面板）→ 项目里只有一张图时就是它。
  对话框在此之上叠一层「列表里点过哪一张」（对话框本地状态，不改画布选区，
  点即 `setScope('original')`）。原图尺寸区块下方 `role=listbox` 缩略图卡片，
  缩略图复用 renderStore 的 SVG 或素材库同一条 `panelSrc`，不发渲染请求。
  「没选」与「没得选」分成两句（`no_figure` / 新增 `no_figures`）；
  `ExportRequest.original` 段一个字段没加。
- **用例**：`exportFigures.test.ts` 9 条（新）、`ExportDialog.test.tsx` 改 1 增 8、
  `exportRequest.test.ts` +1。反证 7 组变异（选区分支 / 记选择 / 并档 /
  单图兜底 / 切范围 / 缩略图来源 / 列表显隐）各有 1~6 条红；第一版只拿掉主选
  那一行仅 1 条红，是被兜底盖住的语义 no-op，已换成整段变异。
- **验证**：`pnpm test` 2603 全过；`pnpm build`、`pnpm i18n:check` 过；
  真浏览器三张截图（无选区列 3 张 / 真点后勾选并切范围 / 画布选中后高亮同一张）。
- **遗留**：有 override 但 renderStore 无 SVG 的面板缩略图退到磁盘原图；素材多时
  列表限高可滚动、无搜索；Codex 内嵌画布下未实测缩略图。

### 2. 散点选中只有大矩形——manifest 对 PathCollection 刻意不给几何

- **根因**：`engine/pathgeom.py` 的 `element_geometry()` 对 PathCollection 刻意返回
  None，`engine/manifest.py` 的闸也不含 `scatter` 角色；散点在 manifest 里只有
  `bbox`（且是 `get_datalim` 口径的圆心包围盒）。前端对带 `geometry` 的元素
  一律走路径描示与命中，所以前端源码一字未改，几何权威仍只有一份。
- **处置**：新增 `pathgeom._marker_subpaths()`，按 Agg `draw_path_collection`
  语义还原每颗 marker（path × 尺寸矩阵 × offset）；只对最大那颗拍平抽稀，
  其余颗用同一组顶点经仿射批量映射，避免逐颗 `Path.cleaned()`（500 颗 915 ms
  → 2.6 ms）。**上限 `SCATTER_MAX_MARKERS = 500`**（量的是 manifest JSON /
  指针距离计算 / 覆盖层 d 串三处消费侧），超过整组退回 bbox；几百颗仍收在
  一个 `<path>` 节点里。空心 marker `fill` 为假，s=0 / NaN offset 不出。
- **用例**：`tests/test_manifest_geometry.py` 删「散点有意留在 bbox」加 3 条
  （逐颗落点与半径递增 / 空心语义 / 上限正好 500 有 501 无）；
  `elementPathSelection.test.tsx` 散点夹具换 3 颗 marker 并加命中/不命中；
  e2e `element-path-selection.spec.ts` 真浏览器 60 颗 → 60 段子路径 0 矩形。
  反证：闸去 scatter → 3 红；上限 +1 → 第一版存活（预算与上限是冗余保证），
  拆成两张图后红；忽略尺寸矩阵 → 红；夹具去 geometry → 2 红。
- **性能实测**：100 颗 ≤1 ms、500 颗 2.6–3.3 ms、20000 颗约 130 ms（但 JSON
  4–6 MB，这就是设上限的原因）。
- **验证**：相关 pytest 全绿；ruff 过；`pnpm test` 2587 全过；`pnpm build` 过；
  e2e 2 passed；截图 scratchpad/uf-02/scatter-selected2-zoom.png。
- **遗留**：`plot(..., ls="None", marker="o")` 这种只有 marker 的 Line2D 仍退回
  bbox，用户若这样画「散点图」问题依旧，建议单开一条；散点 bbox 仍是圆心口径。
