# SVG 计算图表编辑 — 实施进度（稳定 checkpoint）

> 本文件是长周期稳定文档：每轮只更新状态表与 checkpoint 追加节，不重写历史。
> 计划全文：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。历史实施证据目录：`/tmp/svgplan-evidence/`（原执行会话记录，本轮未重新取得全部日志）。
> **当前结论（2026-11-04 复核，代码 `7cbb0e6e`）：核心模块已部分实现，产品闭环尚未完成；最终 SVG 的五类缺陷及前端状态机三条反例已复现。**
> 最新任务顺序和验收条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，可直接执行的接续提示词见 [NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。下方旧 checkpoint 保留为历史，不再作为当前接续指令。

## 环境（SG00 建立记录及本次复核）

- 实施 worktree：`E:\0 - Project\Tavotto-svg-computational-editing`，分支 `zixu.wang/svg-computational-editing`。
- 基线：`git fetch origin` 于 2026-11-03T15:09:56Z 成功；origin/main = `2bc94f7fd9b13276f92d100fcd1b0e7075f49a93`（与计划基线一致）。本地 main（`69bcafae`）落后 origin/main 118 个提交，未被使用。
- 工具链：Windows 10.0.26200 x64；Git Bash；miniconda Python 3.13.11 → 实施 venv `E:\0 - Project\Tavotto-svg-computational-editing\.venv\Scripts\python.exe`（pip install -r requirements-dev.txt -e ".[worker]" 成功；matplotlib 3.11.2 / pytest 9.0.3）。
- import 探针：`tavotto.__file__ = E:\0 - Project\Tavotto-svg-computational-editing\src\tavotto\__init__.py`（editable install 钉在本 worktree，符合 L-040）。
- 字体：`scripts/fetch_fonts.py` 已跑（liberation 12 个 + NotoSansSC-Regular.otf，sha256 校验过）。
- 测试环境变量：`TAVOTTO_NO_TELEMETRY=1`。
- 原工作区 `E:\0 - Project\Tavotto`（main@69bcafae，clean）未被触碰；计划 worktree `Tavotto-svg-computational-plan`（分支 zixu.wang/svg-computational-editing-plan@06d07acc）保持只读。
- 本轮复核对象：实施分支 `7cbb0e6ed6746441fbf0d867b941a0fe79f7fa6f`，相对已缓存 `2bc94f7f` 共 24 个提交；工作树 clean。2026-11-04 两次 fetch 失败（连接关闭、SSH 公钥认证失败），远端最新状态未验证。
- 本轮文档工作树：`E:\0 - Project\Tavotto-svg-next-phase`，分支 `zixu.wang/svg-computational-next-phase`，从上述实施快照创建；本轮不修改产品代码。
- 本轮新证据目录：`E:\0 - Project\svg-review-20261104`。专项测试与独立反例均导入实施工作树源码；不是完整回归或发布资格。

## 批次状态（2026-11-04 复核后；按原计划退出条件）

| 批次 | 状态 | commits | 证据 |
| --- | --- | --- | --- |
| SG00 | baseline_recorded | a046b799（计划）、9a271f9f | 能力清单和历史基线已有；远端新鲜度及最终候选回归需补证 |
| SG01 | reopened | 见历史 SG01 提交 | 最终组合反例否定部分保真假设；并发/导出实验及 ADR 0166 边界需复核 |
| SG02 | partial | 2fa9841b | 模型与 golden 已有；完整渲染身份、来源资源、兼容与持久化合同未闭合 |
| SG03 | core_partial | f1406038+fmt | 场景盘点与 inspect 已有；资源生命周期及所有入口未接通 |
| SG04 | core_partial | d0c0d898 | 采用判定 helper 已有；生产来源采用、语义元数据往返未接通 |
| SG05 | partial / acceptance_failed | ced09ecc | 坐标工具已有；最终 pt/px 组合错位、无 id 节点失败，图元交互未实现 |
| SG06 | core_partial | 133d2bf7 | 数据与绑定构造已有；真浏览器向导、多系列及独立轴表单未完成 |
| SG07 | acceptance_failed | f505c216 | 专项绿；最终 SVG 旧刻度未更新、散点组合失败；不具备计算闭环资格 |
| SG08 | partial / acceptance_failed | 834c0053+f74757f5 | bar/errorbar 脚手架已有；柱组合失败、误差棒丢失；完整族仍缺实现 |
| SG09 | partial / acceptance_failed | d962ecae | 热图核心已有；最终色条未更新；极坐标/3D/双轴/inset 是产品待实现 |
| SG10 | partial / not_integrated | 00de628a+c95bdb03+b4d229ab+c3d15aaf+b2688a39 | HTTP/client/store 已有；状态机反例未修，UI、渲染、命中、撤销接线与 E2E 未完成 |
| SG11–SG17 | pending | — | — |

## Checkpoint 追加（保留原记录；最新记录在前）

### 2026-11-04 补充整夜无人值守执行合同

- 按用户要求，将 R00–R13 明确为一次启动后自动衔接的任务序列；M0/M1/M2 不等待人工“继续”，最终验收仍沿用原范围。
- `NEXT_PHASE.md` 第 5.1–5.6 节补充启动预检、按依赖选任务、失败分类和有限重试、checkpoint 字段、中断恢复及允许结束的条件；`NEXT_PHASE_PROMPT.md` 同步。
- 本轮仅完善计划与提示词，产品实现状态不变；未启动通宵实现、定时任务或常驻调度器。会话/机器不可用时不能靠提示词保证自行恢复，必须保留真实 checkpoint。

### 2026-11-04 执行效果复核与下一阶段重排

- 本轮实际运行：新增后端 10 个文件共 **112 passed / 0 skipped**；前端 store/golden **23 passed**；`pnpm --dir web build` exit 0。JUnit 与构建日志在本轮证据目录。未重跑完整现有套件、真实 SVG 编辑 E2E、桌面产物和跨平台验收。
- 后端五类反例：范围变化后旧刻度保留；px/pt 的最终路径缩小至 75%；bar/scatter 组合失败、errorbar 丢误差；热图保留旧色条；无 XML id 的节点绑定成功后计算失败。复现脚本 exit 0 的含义是**五类缺陷均被观察到**，不是产品通过。
- 前端三条独立反例：A 的迟到响应覆盖 B 场景；表单变化保留旧确认产物；预览失败仍满足当前 ready 判据。Vitest exit 1、3 条目标断言失败；store 尚无生产 UI 消费者，属于接线前缺陷。
- 替换旧接续建议：不能仅增加 `ChartBindDialog` 就宣告 SG10 完成；不能把 `renderKeyOf + data_digest` 作为完整渲染身份；不能把“旧程序忽略 chart 字段”当成兼容证明。
- 下一阶段先完成 R00–R04 的最终产物正确性和版本防串，再以 R05–R07 打通 B 路径实际保存/重开/导出；后续补齐 A/C、场景交互、全部适用图表族、写回、预检、入口和资格。详细依赖见 `NEXT_PHASE.md`。
- 本轮仅修改评估与计划文档，未修复上述产品缺陷，未推送/合并/发布。历史 checkpoint 中的“通过”“已可用”只代表当时判断，不覆盖本次实际结果。

### 2026-11-04 SG09 核心通过（时间：UTC 04:4x）

- `d962ecae`：dataset.grids（矩形/有限数校验 + 条件摘要）、series kind=image（grid 引用）、
  chartdata.parse_grid、chartbind grid_text、脚手架 imshow+colorbar（cmap/vmin/vmax 生效）、
  compose 图像替换；vectors 16 条双侧同判；TS 镜像同步（grids 校验 + image 角色 + 摘要口径）。
- 极坐标/3D/双轴/inset：**未实现**，信息前提登记在 SG00 能力清单 §3（3D 需 z 数据等）——
  按计划「无原输入的信息不冒充已恢复」，该批以「已实现网格/色阶 + 对等盘点」记账，非全部退出条件。

### 2026-11-04 SG10 进行到 2/3（时间：UTC 06:4x）

- `b4d229ab`+`c3d15aaf`+`b2688a39`：web/src/store/chartBindStore.ts（向导状态机：
  openFor→inspect→data→confirmBinding(chartBind)→confirm(chartCompute 预览)；错误稳定码透传；
  6 个 vitest 用例全绿）+ TS strictness 修复（pnpm build 绿）。
- **SG10 3/3 的确切剩余**：
  1. `web/src/components/chart/ChartBindDialog.tsx`：用 `ui/Dialog`（宽度 md/lg）+
     `useChartBindStore` 渲染三步（data/confirm 两步已够 v1）；确认按钮调
     `documentStore` 的 updateObject 把 `chart` 字段写进面板（一次 commit、可撤销）；
     入口挂画布 SVG 面板的右键菜单/Inspector（object-context-menu 规则）。
  2. PanelView 渲染接线：fileKind==='svg' 且 `obj.chart` 存在的面板 → chartCompute
     （键 = renderKeyOf + model.data_digest，结果进 PanelRender.svg 三件套——遵守
     render-state-keying 与 svg-byte-budget 纪律）。
  3. e2e：`web/e2e/chart-bind.spec.ts`（B 路径真浏览器）+ playwright.config 白名单评估。
  4. i18n：向导文案走 `t()`，新 key 进 errors.json / ui 资源（`pnpm i18n:check` 是硬门禁）。
- 修复纪律备忘：build 失败的提交绝不入树（本轮曾连犯两次，均以 fix 提交补救）。

### 2026-11-04 SG10 进行到 1/3（时间：UTC 05:5x）

- `00de628a`：POST /api/svg/chart/bind（probe 子进程取引擎身份 → chartbind 确认产物；
  缺 matplotlib = 503 chart_engine_unavailable）、POST /api/svg/chart/compute
  （svgscene 重读+verify_scene → svg_scaffold 子进程 → 引擎身份对账（错 = 409 chart_engine_mismatch）
  → compose；原文件零改动；monkeypatch 断言不建脚本会话）。svgchart 子进程协议
  （stdin/stdout JSON + probe 模式 + engine 身份回报）。compose 序列化注册 ns 前缀（ns0: 修复）。
- `c95bdb03`：document.ts PanelObject.chart?: PanelChartState（语义真值=模型+绑定；
  旧前端忽略退化为静态预览）+ lib/chartApi.ts（inspect/bind/compute 客户端）。
- **SG10 剩余**：② 绑定向导 UI（inspect → 选轴区/CSV/系列 → chartBind → chartCompute 预览 →
  documentStore.commit 写 chart 字段；取消不落文档）③ PanelView/渲染链：chart 面板的
  renderStore 键「文件+变体」（变体=model rev+binding）与 chartCompute 结果的 svg 接入
  （复用 exactPanelRender 家族纪律）④ 元素树/命中：svgcompose facts 的 series_points →
  manifest 式几何 ⑤ e2e spec + playwright 白名单。
- 验证基线：后端全部 chart 套件 exit 0（endpoints/compute/grid/series/bind/model/vectors）；
  pnpm build 绿；golden 17 用例双侧同判。

### 接续指南（SG10 开始时的状态快照）

- 分支 `zixu.wang/svg-computational-editing`，HEAD ≈ d962ecae + checkpoint commits。
- **后端计算核心已可用**：chartmodel/chartdata/chartbind（确认产物）/svgchart（worker 脚手架，
  worker 命令 `svg_scaffold`，请求 {model, frame, salt}，响应 {fragment_b64, facts}）/
  svgcompose（组合）/svgscene+svgcoord（盘点+坐标）/svgadopt（路径 A 采用）/两个端点
  （GET /api/svg/chart/inspect 只读盘点）。
- **还没有的服务端接线**：bind/compute 的 POST 端点（复用 pool worker 跑 svg_scaffold——
  需要走 pool.get 或 one_shot 的哪一个要想清楚：脚手架不建会话，可用 one_shot 式隔离或
  轻量命令通道）；文档 schema 的 chart 面板字段（PanelObject 新可选字段 + SCHEMA 升版评估）；
  compose 产物作为面板渲染版本的提供路径（renderStore 按「文件+变体」分键）。
- **SG10 的最小正确切分**：① 文档契约（types/document.ts + documents.py 同批加可选字段，
  不升 schema 版本——旧程序读新字段的防损策略写清）② api client（inspect/bind/compute）
  ③ 绑定向导 UI（选轴区→CSV→系列→对照→确认/取消，全部进 documentStore commit/撤销）
  ④ PanelView 对 chart 面板渲染 compose 后的 SVG。每一小步带测试；前端改动跑
  `pnpm exec vitest run <spec>` + `pnpm build`（Windows 下 pnpm test 脚本的 POSIX 语法不可跑，
  用等价命令 NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run）。
- 前端 e2e 的 chromium 已装（E:\devcache\ms-playwright）；spec 接入 playwright.config 的
  project 白名单（webkit/chromium-en 显式 testMatch，见 SG15 清单）。
- 反证纪律延续：每个新不变式在一次性 worktree 变异验证（git worktree add --detach <tmp> <HEAD>，
  从实施仓取 HEAD，勿用主仓）。

### 2026-11-04 SG08 通过（时间：UTC 03:5x）

- `834c0053`+`f74757f5`：chartmodel（AXIS_KINDS categorical、SERIES_KINDS +bar/errorbar、
  样式键 +facecolor/edgecolor/bar_width/label、series.yerr 字段、dataset 允许非空字符串列、
  log 轴正值范围约束）；chartdata（text_columns 保留 + issues 带 column 字段，文本列的
  not_numeric 不阻塞确认）；chartbind（类别轴校准 [−0.5, n−0.5]、yerr 列、数据摘要覆盖合并列）；
  svgchart（bar/errorbar 渲染、类别刻度、位置=序号且 x 文本值必须与 categories 逐位一致）；
  svgcompose（按模型角色区分数据节点/图例手柄：手柄 retint 不替换；未知角色报
  compose_node_role_unknown）；前端镜像全量同步 + vectors 14 条双侧同判 + pnpm build 绿。
- 教训（本轮反复踩）：对已 ruff format 的文件跑 heredoc 字符串替换会静默失配——**改动已格式化
  文件必须用 Edit 工具或先 Read 再改**；每次变异/补丁后核对「补丁真的打上了」（L-030）。
- web node_modules 曾被一次中止的 pnpm 清理弄残（31 项）；`rm -rf node_modules && pnpm install` 重建。
- 下一步：SG09（色阶/heatmap/cmap 绑定 + 复杂图能力对等盘点）→ SG10 前端集成。

### 2026-11-04 SG05–SG07 通过（时间：UTC 02:5x）

- SG05 `ced09ecc`：svgcoord.py（viewBox→pt 全语义、变换累积含 use x/y 深度语义——行向量下
  内层先作用，prepend 累积；与 ir.compose 逐位一致的对拍用例）；svgscene 增补 clip/opacity/转曲判据。
- SG06 `133d2bf7`：chartdata.py（格级类型化错误：非数值/缺失/日期/千分位/列长，不静默填零）；
  chartbind.py（校准证据→轴范围：线性外推/log 域内插/倒轴识别/log 非正值拒绝；系列选择限图形节点；
  确认产物直接过 chartmodel.validate——确认与消费同一合同；preview_deviation 几何偏差报告）。
- SG07 `f505c216`（M1）：engine/svgchart.py 脚手架（模型+场景框→1:1 对齐片段+轴分数事实；
  matplotlib locator/formatter 复用；salt 固定确定性）；worker 新命令 svg_scaffold（会话无关，
  真实子进程不建会话、不跑用户脚本，输出与进程内逐字节同）；svgcompose.py 组合
  （系列替换/轴区域退役/defs 上提——修了「同一 def 内外双引用」的差集缺陷；产物过 svgcheck）；
  chartmodel 新增 log 轴正值范围约束。test_worker_roundtrip 回归 exit 0。
- 诚实缺口（SG07 标 M1）：数据锚定注释、NaN 间断数据入口（chartdata 现拒绝 NaN 列——
  间断语义待 SG08+ 的数据清洗入口）、版本提交顺序的产品接线（SG10/11）。
- 下一步：SG08（柱/误差/图例联动）→ SG09（色阶/能力对等）→ SG10（编辑器集成）。

### 2026-11-04 SG02–SG04 通过（时间：UTC 01:5x）

- SG02 `2fa9841b`：engine/chartmodel.py + web/src/types/chartModel.ts 严格同源；
  golden vectors 12 条两侧同判；跨语言摘要用类型标记+IEEE754 位模式（1 与 1.0 同值），
  TS 侧同步 sha256（修了 w 数组 16→64 的实现 bug）。
  反证：一次性 worktree 变异 scene_digest / data_digest 校验各一处 → 对应用例红（exit 1）。
- SG03 `f1406038`+`03b7be20`+`5c8c3392`：svgscene.py（场景盘点/稳定节点 id/gzip 拒绝/预算）
  + GET /api/svg/chart/inspect（safe_resolve 内，422 稳定码，零写入）。
  反证：绕过 svgcheck → 恶意语料被端点放行 → 两用例红。test_svg_assets 回归绿。
- SG04 `d0c0d898`：engine/svgadopt.py（figure/declarative/refused 纯事实判定；
  快照字节绑定+回执身份=授权；native 离线不 fallback；冷态显式引用可采用；
  portable_model 如实声明）。反证：移除回执核对 → 伪造元数据用例红。
  svgstore/export_svg 回归绿（exit 0）。
- 前端 vitest 全量 12407 绿 / 3 处失败为 Windows CRLF 检出伪影（designMd frontmatter、
  playground 封面 sha、onboarding 闭包扫描；本机改动为纯新增文件、tracked diff 为空，
  CI Linux 检出 LF 不发作——已按 L-026 定性为环境缺失非产品失败）。
- workerd：cargo build + cargo test exit 0。

### 2026-11-04 SG01 通过（时间：UTC 00:3x）

- 状态：SG00 passed、SG01 passed
- 分支 zixu.wang/svg-computational-editing；基线 2bc94f7f；commits：a046b799（计划）、9a271f9f（SG00）、本次 SG01 提交
- 架构决定：**C2 精化「片段嫁接」**（ADR 0166，Accepted）——路径 A 复用 live-Figure 管线零新计算；
  路径 B worker 脚手架产出坐标对齐 SVG 片段 + 父进程节点级替换（defs 上提契约）；路径 C 自包含包冷重放零执行；
  导出经新增 SVG→IR 翻译器（stdlib+现有依赖，零 PyMuPDF 不变）。C1 否决（重建漂移 7.3mm）、C3 否决（计划禁令+实验二）。
- 实验证据（`E:\0 - Project\svg-exp\`，仓库外）：exp2 158 断言、exp3 15 断言 + 浏览器 vs PDF 像素
  changed 1.74% / ink IoU 0.848、exp4 冷重算逐字节同 + 恶意样本拒绝，全部 exit 0。
- 五个结构性契约进 ADR：子区域所有权、跨区域 defs 上提、多根级 defs、叶级 clip、mathtext 经片段表达。
- 验证：tests/test_docs_references.py + test_agents_rules_index.py exit 0（41 通过）；agents_budget --check exit 0。
- 未验证：macOS / 真桌面 / workerd 控制面腿（workerd 已构建、cargo test exit 0，等价矩阵 workerd 腿在 SG15 前补跑）。
- 下一步：SG02 模型和身份契约（冻结 schema、来源判别、错误码闭集；前后端同源镜像同提交）。

### 2026-11-03 SG00 进行中（时间：UTC 15:2x）

- 状态：running
- 已完成：fetch/基线核对；worktree+分支建立；计划文档带入并提交（a046b799）；venv 建立 + import 探针 + 字体；
  三份只读盘点（SVG 后端管线 / 导出与写回 / 前端文档与渲染态）完成，结论写进 SG00-CAPABILITY-MAP.md。
- 进行中：SVG 专项 + 等价矩阵 + 不变式基线测试（后台，结果将记入本文）；SVG 语料；能力映射文档。
- 未验证：macOS / 真桌面窗口 / 插件宿主（本机无条件，按计划记 unverified_platform）。
- 下一步：SG00 退出 → SG01 六个前置实验。

## 关键既有事实（盘点结论摘要，详证见各 file:line）

- 静态 SVG 是**只读素材**：`svgasset.py` + `rendercore/svgcheck.py` 校验边界（32MiB/200k 元素/DTD·脚本·外链拒绝），
  经 `/api/file` 服务；**不是** RenderPlan 可放置源（`rendercore/ir.py:200` `FILE_KINDS` 不含 "svg"）；
  画布合成遇 SVG 素材报 `svg_asset_export_unsupported`（app.py:2800 附近）。
- Figure 的 SVG 导出走 worker 同次 draw 配对（teedraw + ADR 0150），`engine/svgstore.py` 按
  `{project, source_id, patch_hash, static_sha256, remap_fingerprint}` 身份存 PDF/SVG 对 + durable 快照。
- 导出五格式：EPS 只能出自 worker matplotlib；画布范围走 RenderPlan（ir → pdfwriter → pdfium 栅格 → svgwriter）；
  SVG 写出默认保留 `<text>`（ADR 0144）+ 资源 id/CSS 隔离。
- 写回事务：prepare(mtime/script_sha) → verify(one_shot 全量重放 + `_compare_manifests` 几何
  [REPLAY_GEOM_TOL=0.005] + 像素门 REPLAY_PIXEL_TOL={0.001,0.5,64}) → commit(备份+replace+回滚)；worker 一条 warning 即阻断。
- override 应用顺序 `_apply_rank`：-1 frame → 0 size_mm → 1 cb_orient → 2 cb_extend → 3 position → 4 scale → 5 rest → 6 tick_model → 7 tick_text。
- 前端：PanelFileKind 已含 'svg'；文档事务 documentStore（commit/beginTxn/txnUpdate/runUndoRedo）；
  渲染态按「文件+变体」分键 renderKeyOf；预览平面 svgPreviewStore 只动 DOM；
  命中几何由引擎 manifest 提供（前端不解析 SVG DOM）。
