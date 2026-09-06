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
| 3 | 目前对于图内中文无法正常渲染，需要增加其适配性。 | `uf/03-cjk-fonts` | 已修复 |
| 4 | 导出功能要增加 eps 和 Tiff 格式。 | `uf/04-eps-tiff-export` | 已完成 |
| 5 | 我目前电脑上明明安装了 Tavotto 的 codex 插件，为什么在编码 Agent 里面还是显示插件市场登记失败未登记。 | `uf/05-codex-marketplace` | 已修复 |
| 6 | 导出中的原图尺寸导出还不好用，我目前已经选中了一个原图，但是还是显示「先选中一张图，才能按原图尺寸导出。」我希望这里做的更好一点，可以直接预览目前的几个图片，用户直接点击就可以。 | `uf/06-original-size-picker` | 已修复 |
| 7 | 目前 Tavotto 里面的图标非常不统一，太丑了，参考 morphicons.com 来统一图标。 | `uf/07-icon-unify` | 已完成 |
| 8 | （无——用户确认没有第 8 条） | — | 无 |

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
- **遗留**：散点 bbox 仍是圆心口径。「只有 marker 的 Line2D 仍退回 bbox」已由
  追加项 `uf/08-line2d-marker-outline` 处理（见下一节）。

### 2 的追加. 只有 marker 的 Line2D 也逐颗描轮廓（产品所有者追加要求）

- **根因**：`engine/pathgeom.py` `element_geometry()` 的 Line2D 分支对
  `linestyle="None"` 刻意 `return None`（当时的取舍：那条折线图上不存在，描它是
  假线；与第 2 条修前的散点同一档）。manifest 的几何闸本来就放行 `line` role，
  所以只差引擎给几何。
- **处置**：`_marker_subpaths` 拆成「取散点的 paths / 尺寸矩阵 / offsets」+ 通用的
  `_stamp_markers`（盖章语义与 Agg `draw_path_collection` 同源）；新增
  `_line_marker_subpaths` 按 `Line2D.draw` 的 marker 段取 marker 路径 ×
  `markersize·dpi/72` × `get_xydata()` 经 `get_transform()` 的落点（忽略 drawstyle、
  NaN 不出、`markevery` 交给 matplotlib 自己的 `_mark_every_path`、半填充每颗两条）。
  `fill` 由 `get_markerfacecolor()`（已解释 `fillstyle="none"`/`mfc="none"`）决定，
  `stroke_pt` = `markeredgewidth`。**既有连线又有 marker 的仍只描折线**：折线穿过
  每颗 marker 中心、容差内都点得中，而 `fill` 是整份一个标志、前端把「闭合或 fill」
  都按面积算，实心 marker 混进来会把折线变成多边形。上限常量改名
  `MAX_MARKERS = 500`，两种 artist 共用同一个数。前端源码一字未改。
- **用例**：`tests/test_manifest_geometry.py` 「有意退回 bbox」那条改成正向 +3
  （6 颗闭合子路径、中心落在数据点、半径 = 4 pt / `"-o"` 仍是 polyline /
  `markevery=2` 只出 3 颗 / `mfc="none"` fill 为假），上限用例参数化成散点与
  marker-only 两档各自两张对照图；e2e `element-path-selection.spec.ts` 现造一张
  24 颗 marker-only 的图（示例图库里没有这样画的），真浏览器 24 段闭合子路径、
  0 矩形、空白处不选中。反证：去掉分支 → pytest 4 红、e2e 红（矩形框回来了）；忽略 markersize → 半径红；
  上限 +1 → 两档都红；markevery 不抽 → 红；fill 恒真 → 红；ls 判据反转 → 10 红。
- **性能实测**：100 颗 0.6 ms、500 颗 2.1–2.3 ms（`element_geometry` 一次）。
- **限制**：半填充 marker 每颗算两条子路径，上限按子路径数计；
  `Line2D` 的 `_marker` / matplotlib 的 `_mark_every_path` 是私有名（3.8–3.11 签名
  一致，前者有公开退路）。

### 3. 图内中文画成方框——matplotlib 那一层没有中日韩回退脸

- **四层诊断**：① matplotlib 图内文字**坏**：`engine/overrides.py` 的
  `FONT_FALLBACK_TAIL = ("DejaVu Sans",)` 没有中日韩脸，且尾巴只在用户改字体时
  才接；PNG 上「中」与「文」逐像素相同（同一个 .notdef 框），PDF 只嵌 DejaVu。
  ② 预检此刻报得对，但 `cjk-fallback-missing` 的主语是正文族名，回退链落地后
  会把画得好好的中文误报成「会是方框」，必须改主语。③ 画布文字（pdfbackend）
  **没坏**，一行未改。④ 前端只需认新字段 + 镜像预检规则。
- **处置（ADR 0045，取代 ADR 0033 §7 第 1 条）**：`overrides.CJK_FALLBACK_CANDIDATES`
  按平台分组（macOS PingFang SC / Hiragino Sans GB / STHeiti / Songti SC…，
  Windows Microsoft YaHei / SimHei / DengXian / SimSun…，Linux Noto Sans CJK SC /
  Source Han Sans / WenQuanYi…），只有 `findfont(fallback_to_default=False)` 真解析
  到的才进链；接入点 `figsession.instrument_all()` 脚本跑完、采 baseline 前逐 Text
  补尾巴，用户族仍在最前。manifest 新报 `cjk_family`（哪张脸画的），汉字由尾巴
  画出不算「换了脸」；预检 py↔ts 主语改为 `cjk_family`，脸不在白名单时报
  `cjkFallbackUnaccepted` 而不是「会是方框」；golden 向量 +2；默认规范
  `cjk_fallback.accepted` 补各平台系统字体。不内置字体；`TAVOTTO_CJK_FALLBACK=0`
  可关。拉丁图有无尾巴 PNG 逐像素相同。
  集成时把 ADR 编号从 0044 改为 0045：0044 已被在飞的 PR #294 占用。
- **用例**：`tests/test_cjk_figure_text.py`（新）：manifest 无缺字 / PDF 文本层
  读回原串且嵌非 DejaVu 字体 / PNG「中」≠「文」/ 预检不响 / 关尾巴的反向对照 /
  拉丁像素不变 / worker 内幂等与用户族在最前；skip 判据是独立探针。
  反证 7 条 6 红；rcParams 兜底那条存活，注释写明它兜的是今天不存在的路。
- **验证**：ruff 全过；针对性 pytest 43 passed；更宽 19 文件只有 canvas.html
  同步门禁 2 红（受管产物未重建，集成时重建）；`pnpm test` 2588 全过；
  `pnpm build`、`pnpm i18n:check` 过。
- **遗留**：Windows / Linux 候选只按 findfont 语义写，本机只验 macOS；ubuntu
  runner 无 Noto CJK 时该用例会 skip（skip 不是绿）；Pyodide playground 没有中文
  字体，行为与改前相同；多张脸同时有字形只报第一张。

### 4. 导出增加 EPS 与 TIFF（ADR 0046）

- **管线**：Flask 父进程（只有 flask + pymupdf）在 PyMuPDF 里合成画布 / 搬运原图，
  PNG 是同一页栅格化；只有 worker 侧的 matplotlib 会 `savefig`。所以 TIFF 是
  「再多一个编码器」，EPS 只有 worker 那条路给得出。
- **TIFF（位图）**：`Canvas.save_tiff` / `pdfbackend.original_tiff` 与 PNG 出自同一次
  `get_pixmap`（逐像素对拍过）；编码器是新写的纯标准库 `src/tavotto/tiffwrite.py`
  （Baseline TIFF + Deflate 无损，RGBA 非预乘），父进程不引入 Pillow。MCP 直连
  那条路由 matplotlib 经 Pillow 写，压缩钉成 tiff_adobe_deflate。位图源只写源
  文件自己声明过的密度，未知时 ResolutionUnit=1。
- **EPS（矢量）**：只在 `scope=original` 且注册表里有这张图的脚本时由 worker
  `savefig(format="eps")`（`ps.fonttype 42`）；画布范围逐项报 `eps_not_for_canvas`，
  无脚本报 `eps_needs_script`，其余格式照常交付（partial）；不做位图裹 PS 冒充
  矢量。要了 EPS 时 PDF/PNG/TIFF 也让 worker 同次重画，四个格式出自同一次脚本
  运行。界面上 EPS 不可用时禁用并说原因，`buildExportRequest` 不发不可用的 EPS。
- **已知限制**：matplotlib PS 后端把半透明画成不透明，只做了按钮上的静态提示；
  画布范围结构性拿不到真矢量 EPS（PyMuPDF 无 PS 写入器）；遥测
  `export_completed` 仍只有 pdf/png 两个布尔，没动。
- **用例**：`test_tiffwrite.py` 9 条（独立读取端 + Pillow 第三把尺）、`test_epsfile.py`
  5 条、`test_export_pipeline.py` +9、`test_worker_roundtrip.py` +1 真 matplotlib 出
  EPS/TIFF、`test_export_request.py` +3、golden `filename_vectors.json` 加 eps/tif/tiff；
  前端 `exportRequest.test.ts` +4、`ExportDialog.test.tsx` +4。反证 10 条全红。
- **验证**：ruff 全过；针对性 pytest 只有 canvas.html 同步门禁 2 红（受管产物
  未重建）；`pnpm test` 2603 全过；`pnpm build`、`pnpm i18n:check` 过。
  合入集成分支时与第 6 条在 `ExportDialog.test.tsx` 末尾各追加了一段 describe，
  手工保留两段；合并态下三份导出用例 67 条全过。
  集成时把 ADR 编号从 0044 改为 0046。

### 7. 图标不统一——同一套 lucide 被用成 10 种尺寸

- **盘点**（改造前）：lucide-react 直接渲染 323 处 / 101 个图标 / 82 个文件，
  size 用了 10 种数值（9–18），描边全是默认 2 外加一处手写 3；别名引入 11 个名字
  32 处；手绘内联 svg 当图标 1 处；浏览器自带 `<details>` 折叠三角 16 处；字符
  当图标 1 处；emoji / CSS 背景图 / 图标字体 0。src-tauri 壳内无图标。
  完整表在 `docs/ux/ICONOGRAPHY.md`。
- **判断**：不换库。morphicons 是 MIT 的变形动画库，底层就是 lucide 这类
  24×24 描边图标；丑的根源是尺寸与语义散掉了，不是图标库。
- **纪律**（唯一出处 `web/src/components/ui/Icon.tsx`）：尺寸四档
  `ICON_SIZE` xs 12 / sm 14（默认）/ md 16 / lg 20；描边 1.75 按比例缩放
  （在 morphicons 的 1.5–2.5 区间内），加粗 2.5 只给填色方块里的对勾；
  `IconProvider` 套在三个 React 根上；`ui/Details.tsx` 统一折叠箭头；
  `ui/iconography.test.tsx` 用 TypeScript AST 守五条规则（内联 svg 按文件按
  个数豁免、size 只能 `ICON_SIZE.*`、strokeWidth 只能 `ICON_STROKE.*`、规范名
  引入、无字符 / emoji 图标、无裸 `<summary>`）。对真源码跑过报出 342 处；
  合入集成分支后它当场抓到第 6 条新加的 `<Check size={10} strokeWidth={3}>`，
  已改成常量——门禁是活的。
- **语义统一**：撤销/重做、刷新、复制、外链、编辑、设置、警告各只用一个图标。
- **morphicons 评估：不接入**。13.3 KB gzip；要另装 vanilla `lucide` 并与
  lucide-react 对齐版本（多一对同源对）；单 `<path>` 渲染让现有 DOM 用例失效；
  默认无视 reduced-motion；仓库里可变形的图标对只有四组，且都在密集列表行里。
- **三组拍板（产品所有者 2026-09-06）**：① Claude 编码 Agent 头像框由 Sparkles
  改为 Bot，Sparkles 只留给右栏「改图助手」入口（已改）；② ShieldAlert 保留为
  「完整性 / 来源变了」专用警告（三处不动）；③ AI 面板「作用范围·Agent」
  按钮维持 SlidersHorizontal。
- **验证**：`pnpm test` 2601 全过；`pnpm build`、oxlint 过；真浏览器前后各 7 张
  截图在 scratchpad/uf-07/shots/{before,after}/（未入库）。

## 集成验证（合并态，集成分支 `feat/user-feedback-2026-09-06`）

七条串行合回后在同一棵树上跑：

| 检查 | 结果 |
| --- | --- |
| `ruff check . && ruff format --check .` | 全过（337 文件） |
| `build_mcp_widget.py --check` / `build_browser_playground.py --check` | 一致（重建过 canvas.html 一次） |
| `gen_canvas_coverage.py` / `gen_preflight_vectors.py` | 与后端 / Python 实现一致 |
| 全量 pytest | 只有 1 红：`test_source_hygiene` 抓到第 5 条新用例的 `subprocess.run` 没钉编码，已修，两文件复跑绿 |
| `pnpm test` | 191 文件 / 2643 条全过 |
| `pnpm build` / `pnpm i18n:check` | 过（`resources.d.ts` 合并后重生成过一次） |
| e2e `tutorial.spec.ts` + `element-path-selection.spec.ts`（chromium） | 6 passed |

合并态才暴露、各分支单独看不到的三件事：① 两个 Agent 与在飞的 PR #294 三份
ADR 0044，重编号为 0045 / 0046；② 第 7 条的图标 AST 门禁抓到第 6 条新加的
`size={10}`；③ 第 4 条与第 6 条在 `ExportDialog.test.tsx` 末尾各追加一段
describe，手工保留两段。
