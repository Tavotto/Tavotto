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
| SG02 | pending | — | — |
| SG03–SG17 | pending | — | — |

## Checkpoint 追加

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
