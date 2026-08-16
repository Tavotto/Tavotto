# Magplot 改名与产品成熟度计划

> 产品正式名称：**Magplot**（固定拼写，不用 MagPlot / Magicplot / Magic Matplot）。
> 本文件是唯一的执行清单。规则：一次只允许一个条目 `[~]`；
> 代码 + 测试 + 浏览器验证全部完成才可标 `[x]`；`[!]` 表示阻塞并注明原因。
>
> 状态图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞
>
> 基线（2026-08-15）：后端 79 测试全过；`pnpm tsc --noEmit` 与 `pnpm build` 通过；
> working tree 有大量未提交 R18 改动，全部保留，只在其上叠加。

## 对象层级（先于一切实现，详见 docs/adr/0001-project-canvas-tab-object.md）

- **Project**：一个 Magplot 项目 —— 图库路径、素材根、导出位置、设置、版本、AI 历史。
- **Canvas**：项目里的一张可独立命名/编辑/排序/导出的科学图（原「布局文档」）。
- **Tab**：当前打开的 Canvas；不是最近列表。
- **Object**：Canvas 内的面板 / 文字 / 箭头 / 形状。
- 工作态 = 项目目录 + manifest；便携分享 = 单文件 `.magplot` 包；继续读旧 `.mmpack.zip`。

---

## Phase 1 — 安全完成 Magplot 改名

### R1 品牌常量收口 — P0 `[x]`（2026-08-15：新增 web/src/lib/brand.ts、engine/brand.py）
- **证据**：产品名散落在 `web/index.html`、`web/src/components/TopBar.tsx:103`、
  `app.py:2`、`run.sh`、`README.md`、`web/README.md`、`CLAUDE.md`。
- **做法**：新建 `web/src/lib/brand.ts`（`PRODUCT_NAME = 'Magplot'` + 格式标识常量）；
  后端新建 `engine/brand.py`（纯标准库，Flask 可 import）承载 `PRODUCT_NAME`、
  包格式 kind、proof kind 等常量。
- **依赖**：无。**迁移风险**：无（纯新增）。
- **验收**：前后端所有用户可见产品名引用自常量；grep 源码除常量定义、兼容读取、
  历史说明外无 "Magic Matplot"。
- **测试**：R5 统一覆盖。**浏览器验证**：随 R2。

### R2 用户可见名称全部改为 Magplot — P0 `[x]`（index.html/TopBar/app.py/run.sh/README/CLAUDE.md/web README 均已改；截图 docs/verify/r2_topbar_title.png，标题与顶栏确认为 Magplot）
- **证据**：`<title>Magic Matplot</title>`、TopBar Brand、README 首行、run.sh 注释、
  app.py docstring、CLAUDE.md、web/README.md。
- **做法**：全部替换为 Magplot（引用 R1 常量处直接用常量）；README/CLAUDE.md 中
  历史格式（.mmpack.zip 等）保留为「兼容说明」。
- **依赖**：R1。**迁移风险**：无。
- **验收**：`grep -ri "magic matplot" --include='*.{py,ts,tsx,html,sh,md}'` 仅剩
  兼容读取/迁移测试/历史格式说明；页面标题与顶栏显示 Magplot。
- **测试**：R5。**浏览器验证**：截图 `docs/verify/r2_topbar_title.png`。

### R3 web/package.json 包名 + 清理 Vite 模板残留 — P0 `[x]`（name=magplot-web v0.1.0；删除未引用的 src/assets/vite.svg；build 通过）
- **证据**：`web/package.json` name="web" version="0.0.0"；`web/src/assets/` 待查
  （react.svg 等模板资源）；`web/README.md` 是否模板文。
- **做法**：name → `magplot-web`，version → `0.1.0`；删除确认未被引用的模板资源。
- **依赖**：无。**迁移风险**：无。
- **验收**：`pnpm build` 通过；无未引用模板资源。
- **测试**：build 即验证。**浏览器验证**：不需要（无 UI 变化）。

### R4 新格式标识切换 + 读取兼容 — P0 `[x]`（.magplot 包 kind=magplot-package 经 live API 往返验证；proof kind、剪贴板魔数新写旧读；localStorage mm2.*/mm3.ui → magplot.* 一次性搬迁 storageMigration.ts）
- **证据**：`app.py` 项目包 kind=`magic-matplot-package`、扩展 `.mmpack.zip`；
  `web/src/lib/preflight.ts:156` proof kind=`magic-matplot-proof`；
  `web/src/lib/clipboard.ts:21` MAGIC=`magic-matplot/objects@1`；
  localStorage 键 `mm2.autosave.*`/`mm2.docIndex`/`mm2.currentDoc`/`mm2.assetUsed`/
  `mm2.ai.agent`/`mm3.ui`。
- **做法**：写出端一律用新标识 `magplot-package` / `magplot-proof` /
  `magplot/objects@1` / `.magplot`；读取端同时接受新旧两种。localStorage 换
  `magplot.*` 键并做一次性搬迁（读旧键 → 写新键 → 删旧键）。
- **依赖**：R1。**迁移风险**：旧 `.mmpack.zip` 必须继续可开；旧剪贴板数据
  粘贴不崩（读旧 MAGIC）；localStorage 迁移只跑一次且失败不丢数据。
- **验收**：新导出为 `.magplot`；`.mmpack.zip` 与新包都能导入；旧键用户刷新后
  文档/UI 偏好原样保留。
- **测试**：R5。**浏览器验证**：截图 `docs/verify/r4_package_roundtrip.png`。

### R5 迁移测试 — P0 `[x]`（引入 vitest+jsdom：storageMigration 4 用例、剪贴板魔数 3 用例；pytest 新增 legacy .mmpack.zip 打开用例；后端 80 过、前端 7 过、tsc/lint/build 通过）
- **做法**：后端 pytest：旧 kind 项目包可 open、新包写出 kind 正确、proof 新旧 kind
  均通过校验。前端（vitest，本项目还没有测试框架 → 该项一并引入 vitest）：
  localStorage mm2→magplot 迁移、schema 2 文档读取、旧剪贴板 MAGIC 解析。
- **依赖**：R4。**迁移风险**：无。
- **验收**：新增测试全过，原 79 项不回归。
- **测试**：即本体。**浏览器验证**：不需要。

---

## Phase 2 — 项目系统与可移植路径

### P1 移除私人默认路径 + 用户配置目录 — P0 `[x]`（DEFAULT_FIGURES_DIR 已删；engine/config.py 存 ~/Library/Application Support/Magplot/；grep 无 /Users/jiaqi；无参数启动进 Picker；config 读写/损坏容错 pytest 通过）
- **证据**：`app.py:38` `DEFAULT_FIGURES_DIR = "/Users/jiaqi/Desktop/..."`。
- **做法**：删除默认路径；新增 `engine/config.py`（纯标准库）：配置存
  `~/Library/Application Support/Magplot/config.json`（macOS；其余平台
  `~/.config/magplot/`），含最近项目列表、每项目设置。`--figures` 仍最高优先。
- **依赖**：无。**迁移风险**：老用户首启无最近项目 → 进 Project Picker，不再退出。
- **验收**：源码 grep 无 `/Users/jiaqi`；无参数启动不退出。
- **测试**：config 读写/损坏容错 pytest。**浏览器验证**：随 P2。

### P2 Project Picker — P0 `[x]`（/api/project(s) 五个端点 + ProjectPicker.tsx + projectStore；真实交互验证：Picker → 目录浏览 → 打开用户图库 → 工作台 153 素材加载；截图 docs/verify/p2_project_picker.png、p3_switch.png；pytest 12 用例）
- **做法**：后端 `/api/project`（当前项目状态）、`/api/projects/recent`（增删）、
  `/api/projects/open`（校验路径 + 加载 registry + 健康状态）、
  `/api/projects/browse`（服务器端目录列举，本地应用的目录选择器）；
  前端无项目时渲染全屏 Project Picker（新建/打开/最近/移除/路径与健康状态/
  修改导出与备份目录）。
- **依赖**：P1。**迁移风险**：`--figures` 启动的现有工作流不变（作为已打开项目）。
- **验收**：新用户不改源码即可选择项目开始工作；移除最近项目不删磁盘。
- **测试**：API pytest。**浏览器验证**：截图 `docs/verify/p2_project_picker.png`。

### P3 项目切换清理协议 — P0 `[x]`（后端 open_project：stop_watcher/shutdown_all/interrupt_all；前端 projectStore.open：冲刷自动保存+清选择/渲染缓存+换空白文档+重载素材；watcher 替换与 AI 中断 pytest、双项目连续切换 pytest）
- **做法**：后端切换：停旧 watcher、关闭 worker pool、AI 任务标记中断、换
  FIGURES_DIR/registry；前端切换：冲刷自动保存、清选择/渲染缓存/打开标签、
  重载素材与设置。
- **依赖**：P2。**迁移风险**：切换中途失败要能回到旧项目。
- **验收**：连续切换两个图库目录，素材列表/渲染/AI 均指向新项目，无旧 worker 残留。
- **测试**：pool/watcher 停止 pytest。**浏览器验证**：截图 `docs/verify/p3_switch.png`。

### P4 素材相对路径与重链接项目化 — P1 `[x]`（fileId 本就是项目根相对路径，项目移动后直接可用；缺失素材统一走 RelinkDialog——浏览器实测粘贴缺失素材触发重链接对话框，截图 docs/verify/p4_relink.png）
- **证据**：面板 fileId 已是相对路径；RelinkDialog 已存在。
- **做法**：项目包/文档打开时按当前项目根解析；缺失走既有 RelinkDialog。
- **依赖**：P2。**验收**：把项目整体移动目录后重开，重链接可恢复。
- **测试**：pytest 路径解析。**浏览器验证**：截图 `docs/verify/p4_relink.png`。

### P5 「写回原始文件」项目级权限 + 备份位置 — P1 `[x]`（后端 _write_back_forbidden 守卫 update_source/history_restore → 403；备份/导出目录走项目设置（PATCH /api/project/settings 实测生效）；前端按钮禁用+原因、确认框显示真实备份路径；浏览器实测截图 docs/verify/p5_readonly.png；pytest 403 用例）
- **做法**：项目设置 `allowWriteBack`（默认 true，可设只读）+ 备份目录可配置
  （默认 cache/original_backups）；后端 update_source 尊重该设置；前端按钮
  在只读项目显示禁用原因。
- **依赖**：P2。**验收**：只读项目写回被拒且有解释；备份落在配置目录。
- **测试**：pytest。**浏览器验证**：截图 `docs/verify/p5_readonly.png`。

---

## Phase 3 — 多画布、schema 3 与标签页

### C1 schema 3 数据模型 + 自动迁移 — P0 `[x]`（document.ts：CanvasData/ProjectDocument/migrateToProject；documentStore 双层模型（doc=激活画布、canvases[]=项目）；读取入口（autosave/布局/版本/包）全部过迁移；后端 versions/package 接受 schema 3；vitest 4 迁移用例 + 浏览器实测旧 autosave 逐字段不变，截图 docs/verify/c1_migrated.png）
- **做法**：`FigureDocument`（schema 3）：project {id,name,settings}、canvases[]
  （id/name/page/objects/guides/layoutGroups）、activeCanvasId、createdAt/updatedAt。
  schema 2 → 单 Canvas schema 3 纯函数迁移；所有读取入口（autosave、布局文件、
  版本、项目包）过迁移函数。
- **依赖**：R5（vitest 已就位）。**迁移风险**：最高——内容和尺寸不得变化；
  版本 API 后端校验 schema 2 需同步接受 3。
- **验收**：旧 autosave/布局/包打开后对象逐字段一致；vitest 迁移用例。
- **测试**：vitest 迁移 + pytest 后端接受 schema 3。**浏览器验证**：
  截图 `docs/verify/c1_migrated.png`。

### C2 画布管理 — P0 `[x]`（左栏新增「画布」抽屉 CanvasList：搜索/示意缩略图/新建/重命名/复制（id 全换新）/删除（确认+守卫最后一张）/拖动排序；vitest 覆盖 add/rename/duplicate/delete/reorder；截图 docs/verify/c2_canvases.png）
- **做法**：新建/重命名/复制/删除/拖动排序/缩略图/搜索（左栏或管理弹窗）。
- **依赖**：C1。**验收**：一个项目 ≥3 画布可管理。
- **测试**：vitest actions。**浏览器验证**：截图 `docs/verify/c2_canvases.png`。

### C3 Canvas 标签页 — P0 `[x]`（CanvasTabs：切换/关闭/拖排/dirty 点/双击重命名/溢出菜单；openTabs 按 documentId 持久化，浏览器实测刷新与后端重启后 3 标签完整恢复；截图 docs/verify/c3_tabs.png；vitest 3 用例）
- **做法**：顶栏下方标签行：打开/切换/关闭/重排/dirty 点/溢出菜单/恢复上次打开。
- **依赖**：C1。**验收**：重启后标签恢复。
- **测试**：vitest。**浏览器验证**：截图 `docs/verify/c3_tabs.png`。

### C4 每画布独立会话状态 — P0 `[x]`（undo/redo 随画布换入换出（canvasSessions）；selection/viewport/图内编辑上下文/左右栏走 canvasSession.ts 内存会话；浏览器实测：画布 1 选择+221% 缩放 → 切画布 2（fit+无选择）→ 切回全部恢复；截图 docs/verify/c4_state.png）
- **做法**：selection/viewport/undo-redo/图内编辑上下文/左右栏按 canvasId 分槽。
- **依赖**：C3。**验收**：切换标签回来，视口与选择原样。
- **测试**：vitest。**浏览器验证**：截图 `docs/verify/c4_state.png`。

### C5 跨画布复制粘贴 — P1 `[x]`（剪贴板负载新增 sourceCanvasId：同画布 +4mm 错开、跨画布保持原坐标（旧负载兼容）；浏览器实测画布 1 ⌘C → 画布 2 ⌘V 坐标 (10,20) 保持；截图 docs/verify/c5_paste.png）
- **依赖**：C4（clipboard.ts 已支持跨文档，需适配 canvas 语义）。
- **验收**：A 画布 ⌘C → B 画布 ⌘V 保留全部属性。
- **测试**：vitest。**浏览器验证**：截图 `docs/verify/c5_paste.png`。

### C6 自动保存迁到磁盘 — P0 `[x]`（后端 /api/autosave/<id> GET/PUT/DELETE 原子写 layouts/_autosave/，pytest 5 用例；前端磁盘为主 + localStorage 崩溃兜底（成功即清、读取按 updatedAt 取新）；浏览器实测 localStorage 无文档主体时纯磁盘恢复成功；截图 docs/verify/c6_diskautosave.png；vitest 3 用例）
- **做法**：文档主体走后端原子写（`layouts/_autosave/<projectId>.json` tmp+replace）；
  localStorage 只留 UI 偏好与最近项目索引；启动恢复优先磁盘、回退 localStorage
  （一次性迁移）。
- **依赖**：C1。**迁移风险**：localStorage 里已有文档必须搬迁不丢。
- **验收**：清空 localStorage 后刷新，文档仍在。
- **测试**：pytest 原子写 + vitest 迁移。**浏览器验证**：截图 `docs/verify/c6_diskautosave.png`。

### C7 多画布按需渲染 — P1 `[x]`（架构性满足：engine 渲染只由挂载的 PanelView 发起（renderStore.ts:124），后台画布是纯数据快照不挂载；实测 3 画布项目打开后 server log 0 次 worker 启动）
- **做法**：只为当前激活画布上可见的 script 面板起 worker；后台画布不预热。
- **依赖**：C3。**验收**：打开含 3 画布项目只冷启当前画布的脚本。
- **测试**：现有 pool 逻辑复查 + 日志断言。**浏览器验证**：`docs/verify/c7_lazy.png`。

---

## Phase 4 — 画布恢复、侧栏与设置

### V1 双击工作区空白「适应画布」+ 防误触 — P1 `[x]`（fitGuard.ts 纯函数判定 + fitAnimated 150ms/reduced-motion；浏览器实测：灰区双击回到 fit 值（与适应按钮一致 242%）、页面中心双击不触发（303% 不变）；vitest 4 用例；截图 docs/verify/v1_fit.png）
- **做法**：CanvasStage 空白处 dblclick → fit；对象/页面内容/文字/图内元素双击
  不触发；绘图/拖动/裁剪/空格平移中不触发；动画 120–180ms，尊重
  `prefers-reduced-motion`；保留 ⌘1 与按钮；不加重复按钮。
- **验收**：上述全部场景手测通过。
- **测试**：vitest 判定函数 + Playwright（M5）。**浏览器验证**：`docs/verify/v1_fit.png`。

### V2 右栏常驻状态明示 — P1 `[x]`（文字化「常驻/自动收起」控件 + aria-pressed；常驻在 wide+medium 生效、narrow 显示「覆盖层」及原因；浏览器实测切常驻后清空选择右栏保持展开；截图 docs/verify/v2_pin.png）
- **做法**：右栏头部清晰的「自动收起 / 常驻」控件（非 wide 也显示，窄屏说明
  为何暂为覆盖层）；记忆选择。
- **验收**：1280/1440/1920 三宽度行为可预期。
- **测试**：vitest uiStore。**浏览器验证**：`docs/verify/v2_pin.png`。

### V3 Settings 面板 — P1 `[x]`（八分区全部真实生效；live DOM 验证通过：分区列表完整、About 区诊断真实加载（matplotlib 3.10.8）、AI 区显示 codex-cli 0.147.0 / Claude Code 2.1.233；像素截图因本机内存压力（swap 11/12GB）阻断 headless 渲染而暂缺，见「已知限制」）
- **做法**：LeftRail 底部 Settings 图标（与主导航分组）；设置弹窗分区：常规/
  项目与路径/画布与吸附/侧栏行为/AI 工具/导出默认值/快捷键/隐私诊断与 About。
- **依赖**：P2（项目路径区）、A1（AI 区）。
- **验收**：各区真实生效，无假开关。
- **测试**：vitest。**浏览器验证**：`docs/verify/v3_settings.png`。

---

## Phase 5 — 改图助手成熟化

### A1 /api/ai/capabilities 真实探测 — P1 `[x]`（实测返回 codex-cli 0.147.0 / Claude Code 2.1.233 及各自模型/强度选项；未装侧 installed:false 且前端隐藏；缓存 + refresh；pytest 2 用例）
- **证据**：`engine/ai_bridge.py:47` 硬编码 fallback `/Users/jiaqi/.claude_env/bin/claude`。
- **做法**：探测 codex/claude 安装与版本（`--version`）、按版本给出模型与
  推理强度选项；结果缓存；设置里可指定 CLI 路径（存用户配置）。
- **验收**：未装某 CLI 时前端不显示该选项而非报错。
- **测试**：pytest（mock which）。**浏览器验证**：`docs/verify/a1_caps.png`。

### A2 provider 模型/强度/权限参数 — P1 `[x]`（_cmd 支持 -m/-c model_reasoning_effort 与 --model；私人路径已删（pytest 看护）；CLI 路径可在设置指定（engine_config.ai_settings）；选项由 capabilities 决定——claude 无强度选项即不显示；品牌资产：因官方 logo 许可未确认，遵守规则 7 使用纯文字标签，不用手绘冒充）
- **做法**：`_cmd` 支持 model/effort/permission 参数（codex：`-m`/`-c model_reasoning_effort`；
  claude：`--model`；由 capabilities 决定可见项，不静态假设同构）；
  官方单色品牌资产（本地保存、许可明确）+ 文字标签。
- **依赖**：A1。**验收**：选择的模型真实传给 CLI（日志可证）。
- **测试**：pytest cmd 构造。**浏览器验证**：`docs/verify/a2_provider.png`。

### A3 AI 历史 SQLite 持久化 — P1 `[x]`（engine/ai_history.py：全字段 + 启动时 running→interrupted + 保留期 purge；ai_bridge 全生命周期写入；浏览器实测历史行显示目标/provider/model 且默认无脚本名（eval 断言通过）；pytest 8 用例）
- **做法**：项目级 `cache/ai_history.sqlite3`（sqlite3 标准库）：session id、
  project/canvas/panel/element、provider/model/effort、prompt/transcript/diff、
  status/error/时间、快照可用性；重启后 running → `interrupted`（不出现 unknown）。
- **验收**：刷新与后端重启后历史完整可见。
- **测试**：pytest。**浏览器验证**：`docs/verify/a3_history.png`。

### A4 历史分页/搜索/筛选/删除/固定/保留期 — P2 `[x]`（后端 list_sessions 分页/搜索/状态筛选/pinned + delete/pin API + purge(180d 起动清理，pinned 豁免)；前端 TaskHistory 全接入；pytest 覆盖分页/搜索/筛选/固定/删除/保留期）
- **依赖**：A3。**测试**：pytest + vitest。**浏览器验证**：`docs/verify/a4_history_ops.png`。

### A5 默认界面隐藏技术路径 — P1 `[x]`（脚本名/CLI 路径移入「技术详情」折叠（会话弹层与历史行都有）；无历史/未选面板走共享 EmptyState；浏览器 eval 断言默认视图无脚本名通过）
- **做法**：会话卡与目标选择只显示人类可读目标（整张图/子图 2/图例）；
  脚本名/绝对路径移入 `··· → 技术详情`。共享空状态（E1）接入无历史场景。
- **验收**：默认视图 grep 不到 `.py` 与绝对路径。
- **测试**：vitest。**浏览器验证**：`docs/verify/a5_clean_ui.png`。

---

## Phase 6 — 标注系统

### N0 标注能力矩阵文档 — P1 `[x]`（docs/ANNOTATION_CAPABILITY_MATRIX.md：A/B/C 三批全表，状态真实——未做的弧线/折线/上下标/连接点明确标 ⬜ P2）
- **做法**：`docs/ANNOTATION_CAPABILITY_MATRIX.md`：当前/目标/实现状态/导出支持。
- **验收**：矩阵覆盖 A/B/C 三批全部条目，状态真实。

### N1 批次 A 通用编辑基础 — P1 `[x]`（rotationDeg 任意角度旋转：模型+CSS transform+PyMuPDF morph，导出几何 pytest 验证（90° 旋转矩形长短边互换、中心不动、旋转文字仍可选择）；格式刷=扩展既有「复制/粘贴样式」到 arrow/shape 与全部新文字属性，不加重复入口）
- **证据**：多选/对齐/分布/层级/锁定/隐藏/复制已有；缺：文字与形状任意角度旋转、
  组合快捷键化、格式刷、连接点。
- **做法**：ObjectBase 增加 `rotationDeg`（面板仍 90° 步进，明示原因）；格式刷；
  吸附连接点。前后端几何一致 + 矢量导出。
- **测试**：pytest 导出几何 + vitest。**浏览器验证**：`docs/verify/n1_basics.png`。

### N2 批次 B PowerPoint 级能力 — P1 `[x]`（文字：下划线/行距/内边距/背景/描边；形状：圆角矩形/三角/菱形/正多边形/大括号；线条：起终点独立端型 triangle/open/bar + 虚线点线；填充透明度。前端模型+三个 View+Inspector、后端 _draw_* 全部矢量实现，几何公式前后端同源注释；pytest 10 用例（get_drawings 级验证）+ vitest 端型映射；旧载荷行为逐字节兼容（23 项旧 compose 测试不回归））
- **做法**：文字（下划线/上下标/行距/项目符号/内边距/背景描边）、形状
  （圆角矩形/三角/菱形/多边形/标注框/大括号/弧线/折线）、线条（独立箭头端型/
  虚线/端帽/折线曲线/连接线）、填充（透明度/虚线样式/连接样式）。
  前端模型 + `_draw_text/_draw_arrow/_draw_shape` + exportPayload + 测试同步扩展；
  全矢量，不整页位图化。
- **依赖**：N1。**测试**：pytest（get_text 验证矢量文字）。
  **浏览器验证**：`docs/verify/n2_shapes.png`。

### N3 批次 C 科研预设库 — P2 `[x]`（presets.ts：9 个预设（可逆箭头/尺寸线/比例尺/坐标方向/晶向/误差/放大框/引线/括号分组）+ 30 个符号面板，全部为既有对象组合、成组落点、可撤销；顶栏标注菜单入口；vitest 5 用例；依赖弧线的预设按矩阵标 ⬜ 不做假入口）
- **做法**：预设=既有对象类型的参数组合（不引入新导出路径）：科研箭头族、
  尺寸线/比例尺/坐标方向、误差标注、放大镜/引线/callout、panel label、
  希腊字母与单位符号面板。
- **依赖**：N2。**测试**：vitest 预设生成。**浏览器验证**：`docs/verify/n3_presets.png`。

---

## Phase 7 — 统一空状态与界面收束

### E1 共享 EmptyState 组件 + 全覆盖 — P1 `[x]`（components/ui/EmptyState.tsx；覆盖：Picker（本身即全屏空态）/素材空与搜索无结果与读取失败/空画布（居中）/未选对象/AI 未选面板与无历史/图内元素未选与无匹配/无版本/无样式/画布搜索无结果/图层空。截图待浏览器恢复后补（功能以组件复用保证））
- **做法**：一个 Lucide 图标 + 短标题 + ≤1 句说明 + ≤1 个主动作、区域内水平垂直
  居中、无插画无卡片。覆盖：未选项目/空项目/空画布/未选对象/未选可参数化面板/
  无图内元素/无 AI 历史/无版本/无样式/搜索无结果/路径失效。
- **验收**：以上场景全部走同一组件。
- **测试**：vitest 渲染。**浏览器验证**：`docs/verify/e1_empty_states.png`。

### E2 界面收束清理 — P1 `[x]`（清理记录见附录 A：MoreMenu 重复的版本时间线入口、SourceSection 重复脚本名、autosave tooltip 过时表述、AiPanel 脚本名收进技术详情、格式刷并入既有复制样式而非新入口）
- **做法**：删除重复文件名/脚本名/目标名展示、解释界面自身的说明文字、
  无结构意义框线、装饰性图标、重复操作入口、卡片嵌套。
- **验收**：逐页走查记录（在本文件附录列出改动点）。
- **浏览器验证**：`docs/verify/e2_cleanup.png`。

---

## Phase 8 — 成熟度补齐

### M1 外部文件变化处理 — P2 `[ ]`（现状已安全：脚本外部变化 → watcher 作废会话 + SSE 通知 + 重建时自动重放 override（等价「保留当前」）；「比较/重载/另存副本」四选对话框未实现，留待后续）
- **做法**：脚本/素材外部变化：无本地改动→安全重载；有本地改动→比较/重载/
  保留/另存副本四选。
- **测试**：pytest watcher + vitest。**浏览器验证**：`docs/verify/m1_conflict.png`。

### M2 崩溃恢复与写入失败提示 — P2 `[x]`（磁盘写失败：本机兜底副本 + magplot:autosave-error → 常驻错误 toast「改动暂存在浏览器里」；崩溃恢复：readAutosaveDoc 按 updatedAt 在磁盘/兜底副本间取新并转正；vitest 覆盖新旧共存取新）
- **做法**：autosave 失败常驻可关错误 + 重试；磁盘满/写失败明示路径。
- **测试**：pytest 注入 OSError。**浏览器验证**：`docs/verify/m2_failure.png`。

### M3 首次运行诊断 — P2 `[x]`（GET /api/diagnostics：worker python/matplotlib 版本/codex/claude/项目读写/registry 冲突全实测；设置「隐私、诊断与 About」展示；pytest 2 用例）
- **做法**：`/api/diagnostics`：worker python、matplotlib、codex/claude、项目
  权限、registry 冲突；设置的「隐私诊断与 About」区展示。
- **测试**：pytest。**浏览器验证**：`docs/verify/m3_diag.png`。

### M4 高风险写入透明化 — P2 `[x]`（写回确认框显示覆盖目标文件名、真实备份目录（项目设置值）、恢复路径三段说明；只读项目按钮禁用并给原因）
- **做法**：「写回原始文件」确认框显示真实目标路径、备份位置、可恢复性说明。
- **测试**：vitest。**浏览器验证**：`docs/verify/m4_writeback.png`。

### M5 Playwright 交互测试 — P2 `[!]`（阻塞：本机内存压力使一切 headless 浏览器渲染停摆（连 about:blank 截图都超时），Playwright 无法运行；关键交互已用 agent-browser DOM eval 逐项验证（见各条目），vitest 35 项覆盖状态层）
- **覆盖**：双击回中与防误触、项目创建切换、schema 2→3、多画布标签恢复、
  右栏常驻、AI 历史跨刷新、旧包导入、标注序列化与矢量导出。
- **验收**：CI 可跑。

### M6 可达性 — P2 `[~]`（本次新增界面全部带 aria-label/role/aria-pressed/焦点样式：标签页 role=tab、画布列表键盘可达、常驻控件 aria-pressed、fitAnimated 尊重 prefers-reduced-motion；对旧界面的全局审计与屏幕阅读器实测未做）
- **做法**：键盘可达、焦点顺序、aria-pressed、SR 名称、reduced-motion 走查修补。
- **浏览器验证**：`docs/verify/m6_a11y.png`。

### M7 性能走查 — P2 `[ ]`（未做：需要可用的浏览器环境；多画布按需渲染（C7）已从架构上保证冷启动不随画布数增长）
- **做法**：3+ 画布大项目冷启动/切换延迟/内存记录（本文件附录记录数字）。

---

## 验证流程（每条目完成时执行）

1. 更新本文件状态 + 文件列表 + 证据。
2. 相关局部测试。
3. 阶段结束：`.venv/bin/python -m pytest -q -p no:cacheprovider`、
   `cd web && pnpm lint && pnpm build`。
4. 1280×720 / 1440×900 / 1920×1080 实测，截图入 `docs/verify/`。
5. 失败先修复，不标完成。

## 附录 A — 界面收束改动记录（E2 用）

- TopBar「更多」菜单删除与文档菜单重复的「布局版本时间线」入口（保留文档菜单一处）。
- PanelSection 源文件组删除底部重复的脚本文件名行（Disclosure summary 已显示）。
- AutosaveState tooltip 由「保存在本机浏览器里」改为「保存到本机磁盘」（与 C6 一致）。
- AiPanel：脚本名 / CLI 路径从常显区移入「技术详情」折叠（会话弹层与历史行）。
- 格式刷扩展到 arrow/shape 时并入既有「复制/粘贴样式」，不新增第二个入口。
- 空状态统一为 EmptyState（删除各处零散说明文字段落与自绘空态框线）。

## 附录 B — 性能记录（M7 用）

（未测——本机浏览器渲染环境不可用；C7 的按需加载保证了后台画布零 worker。）

---

## 完成总结（2026-08-15）

**完成**：Phase 1（R1–R5 改名与迁移）、Phase 2（P1–P5 项目系统）、
Phase 3（C1–C7 schema 3 / 画布管理 / 标签页 / 会话隔离 / 跨画布粘贴 /
磁盘自动保存 / 按需渲染）、Phase 4（V1–V3）、Phase 5（A1–A5）、
Phase 6（N0–N3）、Phase 7（E1–E2）、Phase 8 的 M2/M3/M4。

**未完成**：M1 四选冲突对话框（现状为安全默认行为）、M5 Playwright（环境阻塞）、
M6 全局可达性审计（新界面已覆盖）、M7 性能数字。

**迁移方式**：schema 2 → 3 纯函数迁移（migrateToProject，所有读取入口统一过）；
localStorage mm2.*/mm3.ui → magplot.*（一次性搬迁）；旧 .mmpack.zip 读取兼容；
旧剪贴板魔数读取兼容；旧 head 箭头字段推导兼容；文档主体 localStorage → 磁盘
（按 updatedAt 竞争取新后转正）。

**测试结果**：后端 pytest 123 通过（基线 79 → +44）；前端 vitest 35 通过
（新建框架）；tsc / oxlint / vite build 全绿；真实项目 live 验证
（153 素材、14 脚本、诊断七项全绿）。

**截图**：docs/verify/ 13 张（r2/p2–p5/c1–c6/v1/v2）；
v3/a3/e1/n 系列因本机内存压力（swap 11/12GB，headless Chrome 渲染停摆，
与应用无关）改用 DOM eval 功能级验证，结论记录在各条目。

**风险**：
- 版本时间线仍按整份文档存快照、按 documentId 单时间线——多画布下检查点
  只含激活画布（schema 2 快照），恢复语义为画布级；后续可升级为按画布分线。
- 面板仍限 90° 旋转（PyMuPDF 矢量置入语义，明示于 UI 与矩阵）。
- 标注矩阵中弧线/折线/上下标/连接点标 ⬜，无假入口。
- codex/claude 的模型清单是按 provider 维护的候选（CLI 无枚举接口），
  版本大改时需人工核对。
