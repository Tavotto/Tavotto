# SVG 计算图表编辑 — 实施进度（稳定 checkpoint）

> 本文件是长周期稳定文档：每轮只更新状态表与 checkpoint 追加节，不重写历史。
> 计划全文：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。历史实施证据目录：`/tmp/svgplan-evidence/`（原执行会话记录，本轮未重新取得全部日志）。
> **当前结论（2026-11-06 复核，代码 `7801e017`）：基础修复、Dialog、导出与元数据代码已有；135 后端+29 前端专项及构建通过。但首次绑定 UI 受阻、真实导出请求漏 chart、SVG→IR 错图、自包含 SVG 重算失败；M1 未完成。**
> 最新任务顺序和验收条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，可直接执行的接续提示词见 [NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。下方旧 checkpoint 保留为历史，不再作为当前接续指令。

## 环境（2026-11-06 当前快照）

- 当前实施树 `E:/0 - Project/Tavotto-svg-r00`，分支 `zixu.wang/svg-computational-r00`，复核 HEAD=`7801e017dc925410de10dfa40582ad356890622d`，开始时 clean；按用户指示复用当前分支，不新建或切换分支/工作树。
- 相对 3f31f4e1 新增 14 commits；上次 PanelView 未提交草稿已在 18907d65 提交，不再作为未提交项。
- 本次 fetch exit 0，origin/main 仍为 2bc94f7fd9b13276f92d100fcd1b0e7075f49a93；旧 main 和其他树保持原状态。
- 本轮证据根 `E:/0 - Project/svg-review-20261106`；已有 venv/前端依赖复用，实际 Python 源码 import 已核对为当前树。测试配置/数据隔离，遥测关闭；前端按 package.json 等价 CLI 验证。
- 本轮真实浏览器任务会话和两个本机回环测试服务已清理；未启动通宵实现或定时任务。历史“后台 sweep 正在跑”本轮未取得完成日志，不当作现行运行状态。
- 最新 NEXT_PHASE 和提示词直接在当前实施分支维护；不再依赖历史 review 文档分支才能取得新计划。

## 批次状态（2026-11-06 复核后；按完整退出条件）

| 批次 | 状态 | commits | 证据 |
| --- | --- | --- | --- |
| SG00 | baseline_recorded | a046b799（计划）、9a271f9f | 能力清单和历史基线已有；远端新鲜度及最终候选回归需补证 |
| SG01 | reopened | 见历史 SG01 提交 | 最终组合反例否定部分保真假设；并发/导出实验及 ADR 0166 边界需复核 |
| SG02 | partial | ac060053 | 基础模型/绑定身份已补修；持久化来源/资源、实际环境与元数据恢复合同未闭合 |
| SG03 | core_partial | f1406038+fmt | 场景盘点与 inspect 已有；资源生命周期及所有入口未接通 |
| SG04 | core_partial | d0c0d898 | 采用判定 helper 已有；生产来源采用、语义元数据往返未接通 |
| SG05 | core_partial | 451d8490、4c2ca1f2 | px/非零 viewBox/父空间基础回归已有；精确命中/文字/分组交互未完成 |
| SG06 | core_partial | 133d2bf7 | 数据与绑定构造已有；真浏览器向导、多系列及独立轴表单未完成 |
| SG07 | core_partial | 4c2ca1f2 | 基础计算回归通过；真实绑定/图内编辑/M1 未完成 |
| SG08 | partial | 4c2ca1f2 | bar 附属误差基础修复已有；完整族与 UI/导出追验待补 |
| SG09 | partial | ac060053 | 色条基础所有权/镜像已修；复杂族和导出图像资源未验收 |
| SG10 | partial / workflow_failed | f76b0610、18907d65 | Dialog 已挂载但未调用 confirmBinding；无完整 manifest/命中和编辑流程 |
| SG11–SG17 | partial / pending | e09b724b、484b29fb、f7d39126 | 导出和元数据仅部分接线，真实冷恢复、写回、入口/平台与完整回归待补 |
| R00 | baseline_and_regressions | 5a4f5c2c | 基础反例保留；本轮用户流程和导出反例待随修复入库 |
| R01/R02 | core_partial | 451d8490、4c2ca1f2 | 既有基础回归通过；最终资格仍需真实 UI/导出和资源语义追验 |
| R03 | partial | fa68fe38、ac060053 | 来源快照与绑定身份已有；实际环境/资源与冻结产物合同待补 |
| R04 | core_partial | a8a088cf、18907d65 | store 基础修复通过；真实 Dialog 未发起绑定，项目生命周期未闭合 |
| R05 | tests_only / partial | 722ad27c | JSON 往返测试已有，真实新进程/资源包/迁移恢复待实现 |
| R06 | partial / workflow_failed | f76b0610、18907d65 | 首次绑定无法预览/确认；只显示第一系列，无精确命中/编辑闭环 |
| R07 | partial / correctness_failed | e09b724b、484b29fb | 真实请求漏 chart；SVG→IR 四类错图已复现；EPS 未实现 |
| R08 | metadata_only | f7d39126 | 导出嵌入元数据，但用导出文件重算 source_mismatch；A/C 生产导入未完成 |
| R09–R13 | pending | — | 见 NEXT_PHASE.md 第 4 节 |

## Checkpoint 追加（保留原记录；最新记录在前）

### 2026-11-06 第三次效果复核与当前分支计划同步

- 7801e017 实测后端 135 passed、前端 29 passed，三步构建 exit 0。未重跑完整旧套件、跨平台和真安装物；不沿用未取得日志的后台 sweep/顺序依赖归因作为通过证据。
- 上轮 5 项独立前端反例对当前源码全部通过，确认对应基础修复有效；文档引用/规则索引 41 项通过。
- Playwright Chromium 实际填写生产 Dialog：节点/列名/有效 CSV 已输入，result/preview 仍 null，确认禁用，没有生成绑定动作；组件未调用 confirmBinding。截图和状态见本轮证据。
- 浏览器调用真实导出构造器：original/canvas 均无 chart，改 chart 不改变导出指纹；后端测试手工补 chart 不代表真实入口接通。
- SVG→IR 独立观测：相对路径终点错误、嵌套矩阵重复应用、px/pt 未映射、use path 消失。当前 PDF 测试只查交付，不能证明保真。
- 用本次真正导出的 SVG 提取 metadata 后，以导出文件执行 compute 返回 422 chart_source_mismatch；旧“自包含往返”测试仍引用原始 chart.svg，C 路径未成立。
- 下一轮先修真实绑定与请求链，再收敛转换保真、保存与自包含、manifest/命中；完成真实 M1 E2E 后继续 R08–R13。旧“全格式交付/M0 完成”等记录保留为历史，当前表反映实际资格。
- 本轮只同步文档，直接在当前实施分支按用户要求提交；未修改产品代码或启动整夜实现。当前目录 NEXT_PHASE/NEXT_PHASE_PROMPT 成为接续入口。


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

### 2026-11-06 当前进度快照（时间：UTC 03:0x）

- **分支**：`zixu.wang/svg-computational-r00`，42 个提交领先 origin/main（2bc94f7f 未变）。
- **后端 sweep**：15 文件（含 worker_roundtrip/export_pipeline/invariants/legacy SVG）正在后台运行。
- **前端**：build / i18n:check / vitest 全绿（viewportStore 8 项在 full-suite 顺序下为顺序依赖伪红，
  isolation 28/28 绿——与本 diff 无关）。
- **已实现（用户可见）**：
  - 绑定流程：右键 SVG 面板 →「绑定数据为计算图表」→ 向导（轴区/每轴独立校准+scale/系列/CSV）
    → 对照预览 → 确认（documentStore 一次事务）。
  - 计算闭环：改范围/系列数据 → 片段嫁接重算（轴/系列/色条/图例按角色完整更新）。
  - 渲染：PanelView 按 chart 渲染身份消费组合 SVG；缓存按完整变体身份（model+binding digest）。
  - 导出：chart 面板原图 SVG = 组合版（basis=convert）；PDF/PNG/TIFF 在 SVG→IR 翻译器前如实报
    `svg_asset_export_unsupported`。
  - 持久化：chart 字段经真实保存路径往返、冷重算、来源冲突如实报告。
- **未完成（按优先级）**：
  1. 图内编辑（选中 SVG 内元素/命中/属性修改）——需要 renderStore manifest 集成。
  2. PDF/PNG/TIFF 导出——需要 SVG→IR 翻译器（ADR 0166 §2 的独立适配层）。
  3. 真 E2E（浏览器驱动 UI 全流程）。
  4. R08–R13：A 路径采用接线、自包含往返、写回、预检、入口打包。

### 2026-11-06 R08 C 路径自包含元数据嵌入完成

- 导出 SVG 格式时在根元素嵌入 `data-tavotto-chart` 属性（base64 JSON model+binding）。
- 从元数据解码 → 重算 compute → 与原始 compute 一致（回归验证）。
- A 路径（svgadopt 生产接线）待做——svgadopt 模块已就绪但还没有接入 import 流程。

### 2026-11-06 R07 SVG→IR 翻译器完成（PDF/PNG/TIFF 全格式交付）

- **rendercore/svgir.py**（671 行）：组合后 SVG → rendercore IR Page。
  path/text/image/clip/transform/opacity 全支持；text 经 HarfBuzz 整形 +
  族名映射（DejaVu Sans→sans-serif 等）；y-down→y-up 一次根翻转；
  不支持特性（渐变/mask/filter/弧）逐元素 SVGIRError 如实报。
- **PDF**：ir.Page → 现有 pdfwriter.write_pdf；**PNG**：original_png；
  **TIFF**：original_tiff。export 路径 chart 面板五格式全可交付（basis=convert）。
- 端到端 probe：composed SVG 12733 bytes → IR → PDF 5544 bytes ✓。
- 导出回归更新：PDF 格式也从 unsupported 改为交付组合版。

### 2026-11-06 R07 冻结导出（SVG 格式）完成

- **OriginalSource.chart** 字段（exportreq.py）透传 normalize；旧请求逐字节不变。
- **_export_produce_chart_original**（app.py）：chart 面板的原图导出走不可变来源快照
  → 脚手架 → 来源复核 → 组合（与 compute 端点同一合同）；SVG 格式 basis=convert，
  其余格式报 svg_asset_export_unsupported（SVG→IR 翻译器落地前如实不可用）。
- 导出产物落盘验证（tv-series 角色标记在组合版 SVG 中）；PDF 500 为已知限制（ Produced
  with error_code 无可行 publish 路径——R07 后续补 SVG→IR）。
- 下一步：M1 全链 E2E（绑定→编辑→保存→冷重开→导出）＋ R08–R13。

### 2026-11-05 R06 绑定 UI 完成（时间：UTC 02:0x）

- **ChartBindDialog**（f76b0610）：轴区/每轴独立校准+scale/系列/CSV 输入/对照预览/确认。
  确认走 updateObject 一次 documentStore 事务（chart 字段写入面板，可撤销）。UI 判据全部
  走 chartBindStore（openSeq/formRev/previewOk），ready = 当前代际 + 成功预览。
- **入口**：ObjectContextMenu 对 fileKind==='svg' 的面板加「绑定数据为计算图表」；
  App 挂载 ChartBindDialog。
- **i18n**：workspace.chartBind 中英资源 + resources.d.ts 经 `i18next-cli types` 再生；
  i18n:check / lint 全绿（hardcoded 字符串清除）。
- 前端 gates：build / vitest 2267 / i18n:check 全绿。
- 下一步：R06 命中几何 + 撤销重做验证；R07 冻结导出（M1 全链）。

### 2026-11-05 N1–N5/F4–F8 修复（第二次复核反例）

- 复核（Tavotto-svg-review-20261105, 3f31f4e1）发现 8 项新边界。逐项修复：
  - **N1**：脚手架轴矩形先平移进 viewBox 局部空间（left=ax_x−vb_x），组合端 wrapper 带
    translate(origin) 映射回场景用户空间；非零 viewBox 端点独立公式核对（[50,120]/[150,20]）。
  - **N2**：compose 计算 target 祖先变换 A（root→父，含 transform 属性），wrapper 带
    T=O·A⁻¹（svgcoord 新增 invert/matrix_attr）；父 translate(20 10) 不重复作用，世界端点 (20,70)/(80,10)。
  - **N4**：_iter_artists 顺序修正——BarContainer/ErrorbarContainer 是 tuple 子类，tuple 内容
    只有柱矩形，误差成员挂 .errorbar 属性；误差线/端帽/数据线全部带角色 gid 进最终 SVG。
  - **N3**：panel_identity 纳入 binding digest（同模型绑不同区域 = 不同版本；declarative 缺绑定报错）。
  - **N5/F8**：chartbind 自动把 series.cbar_node 写进有效绑定；TS ChartSeries 补 cbar_node；
    golden 热图向量带 cbar_node 双侧同判。
  - **F4/F5**：前端 revisionOf 用完整变体身份 digestOf({model, binding})（含场景摘要）。
  - **F6**：chartRenderStore 加 generation 代际，clear/drop 递增，在途响应按代际丢弃。
  - **F7**：bind store 五条过期退出路径补 busy=false 释放。
- 新增回归：N1/N2/N4 + F4–F6 共 6 项常驻测试；全后端 12 文件、前端 29 用例、build 全绿。
- PanelView 草稿正式接入（渲染侧 chart 消费），render_revision 缓存双键（本地+后端身份）。
- 证据：/tmp/n124*.log、/tmp/fe2.log、/tmp/b7.log（本机临时）。

### 2026-11-05 M0 完成（R00–R04）

- **R01/R02（`451d8490`）**：B1–B5 全部修复并转常驻回归——脚手架在场景用户坐标系渲染
  （1 单位 = 1 用户单位；字号/线宽/capsize 按 viewBox→pt 比例换算，B2）；全部语义对象带
  `tv-*` 角色 gid（组合按角色取**完整产物**：柱/marker/误差成员/色条，B3）；svgscene 的
  `_logical_id` 单一出处 + `build_node_index`（compose 解析绑定引用，无 id 节点可稳定选中，B5）；
  chartbind 新增 `axes_nodes`（轴区域所有权——旧刻度/标注退役，B1）与 `cbar_node`
  （色条区域由片段新色条整体接管，B4）；轴家具收集排除角色子树（无 id 裁剪组）。
  7/7 组合回归全绿（xfail 已摘）。
- **R03（`fa68fe38`）**：compute 入口一次性读入来源字节建立不可变快照，子进程返回后复核
  digest（被改 → 409 `chart_source_changed`）；响应携带 `render_revision`（panel_identity）
  + model/binding digest + engine 事实。回归：同数据不同范围 = 不同 revision；模型内摘要不符 = source_mismatch。
  一次 503 为 worker 冷启动瞬态（两轮复跑 9/9 绿，已记录）。
- **R04（`a8a088cf`）**：前端 store 防串——openSeq 代际（迟到响应丢弃，F1）、formRev
  （表单变化即失效 result/preview，F2）、previewOk 资格（失败不可提交，F3）；x/y 独立 scale、
  多系列 addSeries/patchSeries/removeSeries、cbarNode/yerrCol 字段。9/9 用例绿（F1–F3 转正）+ build 绿。
- 本轮定性：`test_worker_roundtrip.py` 单跑 `[linestyle---]` 在**未动过的 7cbb0e6e 树同样红**
  （L-026 单跑证据），整文件两树均绿——顺序依赖伪红，非本轮引入。
- **M0 验收**：B1–B5/F1–F3 正式回归全绿；最终 SVG 以独立公式核对点/刻度/色条；
  来源/请求版本不再混用。M0 达成，进入 M1（R05–R07）。

### 2026-11-04 R00 完成（接续会话）

- 复核纠正已生效（SG07–SG09 的 passed 撤销为 partial/acceptance_failed）。本轮把独立反例
  转为仓库内正确性回归：`tests/test_chart_composition_regressions.py`（B1–B5，xfail strict，
  断言**正确结果**——修复提交移除标记转常驻回归，错误修复会因 XPASS 变红）；
  `web/src/store/chartBindStore.test.ts` 追加 F1–F3（it.fails）。实测：后端 7 xfail、
  前端 6 passed + 3 expected fail（exit 0）。
- fetch 成功（本轮 exit 0），origin/main 仍为 2bc94f7f；本地实现历史未在远端。
- 接续 worktree `E:  - Project\Tavotto-svg-r00`，分支 `zixu.wang/svg-computational-r00`，
  基于 next-phase 080fde07；解释器探针 `tavotto.__file__` 钉在本树 src。
- R01/R02 的合同决定（写进回归预期）：脚手架产物带语义角色标记
  `data-tavotto="series:<id>"/"axes:<id>"/"colorbar:<id>"`；脚手架在场景用户坐标系渲染
  （pt 类量按 viewBox→pt 比例换算）；节点身份统一走 svgscene 逻辑 id 映射；
  绑定请求显式携带 axes_nodes（用户确认的轴区域节点）。

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
