# SVG 计算图表编辑 — 实施进度（稳定 checkpoint）

> 本文件是长周期稳定文档：每轮只更新状态表与 checkpoint 追加节，不重写历史。
> 计划全文：`IMPLEMENTATION_PLAN.md`。证据目录（仓库外）：`/tmp/svgplan-evidence/`（本机）。

## 环境（SG00 建立后冻结，变化时更新）

- 实施 worktree：`E:\0 - Project\Tavotto-svg-computational-editing`，分支 `zixu.wang/svg-computational-editing`。
- 基线：`git fetch origin` 于 2026-11-03T15:09:56Z 成功；origin/main = `2bc94f7fd9b13276f92d100fcd1b0e7075f49a93`（与计划基线一致）。本地 main（`69bcafae`）落后 origin/main 118 个提交，未被使用。
- 工具链：Windows 10.0.26200 x64；Git Bash；miniconda Python 3.13.11 → 实施 venv `E:\0 - Project\Tavotto-svg-computational-editing\.venv\Scripts\python.exe`（pip install -r requirements-dev.txt -e ".[worker]" 成功；matplotlib 3.11.2 / pytest 9.0.3）。
- import 探针：`tavotto.__file__ = E:\0 - Project\Tavotto-svg-computational-editing\src\tavotto\__init__.py`（editable install 钉在本 worktree，符合 L-040）。
- 字体：`scripts/fetch_fonts.py` 已跑（liberation 12 个 + NotoSansSC-Regular.otf，sha256 校验过）。
- 测试环境变量：`TAVOTTO_NO_TELEMETRY=1`。
- 原工作区 `E:\0 - Project\Tavotto`（main@69bcafae，clean）未被触碰；计划 worktree `Tavotto-svg-computational-plan`（分支 zixu.wang/svg-computational-editing-plan@06d07acc）保持只读。

## 批次状态

| 批次 | 状态 | commits | 证据 |
| --- | --- | --- | --- |
| SG00 | passed | a046b799（计划）、9a271f9f | SG00-CAPABILITY-MAP.md；基线 331+41+94 绿 / skip 全定性 |
| SG01 | passed | 见下方 SG01 提交 | SG01-EXPERIMENTS.md + ADR 0166；实验 exit 0 ×4 |
| SG02 | passed | 2fa9841b | 契约+golden vectors 双侧同判；两处变异反证红（scene/data 摘要校验移除） |
| SG03 | passed | f1406038+fmt | svgscene 盘点 + inspect 端点；反证红（绕过 svgcheck）；svg_assets 回归绿 |
| SG04 | passed | d0c0d898 | svgadopt 采用判定；反证红（移除回执核对）；svgstore/export_svg 回归绿 |
| SG05 | passed | ced09ecc | svgcoord 变换链（preserveAspectRatio 全语义/累积矩阵含 use 深度语义）+ clip/opacity 事实 |
| SG06 | passed | 133d2bf7 | chartdata 类型化数据入口 + chartbind 校准/确认产物（经 chartmodel 同一合同） |
| SG07 | passed(M1) | f505c216 | 脚手架+组合+worker 分发；热=冷字节一致；注释锚定/NaN 间断入口未完成（SG08+） |
| SG08–SG17 | pending | — | — |

## Checkpoint 追加

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
