# ADR 0051：保留原图的规范化事务——修改约定、真实渲染检测、有界局部修复、产物验收

日期：2026-09-13 · 状态：**Accepted**
相关：[0003 worker 协议](0003-worker-protocol-v1.md)（全量列表 override、热态 == 重放）、
[0006 Codex MCP](0006-codex-mcp-app-and-publication-profile.md)、
[0030 一份验证服务](0030-validation-and-problem-navigation.md)、[0031 统一导出管线](0031-unified-export-pipeline.md)、
[0042 持久 tight 布局的钉住](0042-pinned-tight-layout.md)。

## 问题

真实用户通常已经有一张内容正确、排版不错的图，只需要「改成 8 cm」「换 Times New
Roman」「文字不小于 8 pt」。旧链路上 Codex 只有 `tavotto_apply_overrides`——它接受
任何 `(gid, prop)`，没有基准、没有约定、没有实效检查，于是最小可复现样例上第一次
偏离原图发生在**尺寸这一步**：`figure.size_mm` 只是 `set_size_inches(forward=False)`，
脚本一次性 `tight_layout()` 算好的子图分数不动、文字 pt 不缩，150 → 80 mm 之后标题
探出顶边、轴标题掉出底边、右图的 y 轴标题压进左图（`element-outside-figure` 报得出裁切，
**文字互相重叠 / 图例压数据没有任何检查**）。模型看见一屏阻断项就开始自由「修」：
改字号、挪东西、删刻度。字体那一步还有第二个偏离：`ticks` 没有 `fontfamily`（刻度换不了
字体），而请求的字体没装时 matplotlib 静默退到 DejaVu，manifest 报的是请求的名字、
预检按白名单认名字——「换成 Times」于是在刻度上没落地、在缺字体时假通过。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 入口 | 新工具 `tavotto_normalize_figure {session_id, width_mm?, height_mm?, font_family?, min_font_pt?, font_size_pt?, formats?, dpi?}`。目标只这五个；只给宽度时高度按原长宽比；`min_font_pt` 只补齐低于阈值的文字，`font_size_pt` 才统一 |
| 基准 B0 | 事务开始时会话的 patch 列表 + manifest（含每个元素全部 editable 值、几何、`face` / `math_face`、结构、原有问题）；脚本 sha256；原始产物尺寸（与 live 图不一致时如实标 `mismatch`，**不替换原件、不改脚本**）。整条事务所有比对都对 B0，不滚动 |
| 修改约定 | `engine/normalize.build_contract()`：`allowed` 由目标**推导**（size_mm / 有 `fontfamily` 字段的元素 / 字号 prop），`allowed_adjust` = 顶层 axes 的 `position` + 未锚在外面的图例 `loc`；预算具名（`EDGE_SHIFT_BUDGET_FRAC=0.15`、`AXES_KEEP_FRAC=0.6`、`MIN_AXES_MM=8`、`MAX_REPAIR_ROUNDS=3`）；修复循环拿到的是只读视图 |
| 执行前 / 后 | `authorize()` 看 patch 键在不在约定里；`compare()` 拿真实渲染的 manifest 逐项对 B0：受保护属性一个没变（自动刻度的 `major_values` 与自动刻度文字例外——那是合法的自适应；脚本 `set_xticks` 过的变了就是内容改动）、结构一个没变、字体真的落成了那张脸、尺寸真的到了、几何位移在预算内 |
| 检测 | `engine/interference.py`：`text-overlap`（两方向都咬进 ≥ 0.25 mm）、`text-over-axes`（装饰文字落进**别的**顶层子图绘图区，包含关系不算）、`legend-over-data`（拿得到 `geometry` 时**确定**，只有包围盒时**风险**）。条目与预检同一形状，进同一份报告；`preflight.element_overflow()` 抽成逐元素判据供 B0 对比 |
| 对比 | 问题按 `(id, sorted gids)` 配对分成 new / worsened / unchanged / improved；只有新增 / 加重的 error 级或**确定性**干涉挡事务；由用户目标直接决定的规范规则（`page-width` 等）不挡，进 `profile_conflicts` 与留档；风险级不挡 |
| 局部修复 | 两族确定性候选：外边距 / 间距重排（`adapt_margins`：每列 / 行需要的装饰物 mm 是刚性的，剩余空间按 B0 比例分，留白与间距沿用 B0 的 mm 按比例缩且 ≥ `PAD_MIN_MM`；装不下回 `conflict`）与图例在自己子图内换预设位置（离原位近的先试，只收「图例自己干干净净」的候选）。每个候选真实渲染再验收，阻断项**严格更少**才收；最多 3 轮 |
| 产物验收 | `engine/artifactcheck.py` 在导出作业的**临时目录**里按格式验：PDF 页面 mm + 首页字体名（`pdfbackend.pdf_fonts`）、SVG width/height/viewBox（字体转路径 → 查不了）、PNG 像素 ±1 px + pHYs dpi、TIFF 像素（分辨率标签未核验）、EPS BoundingBox。不过的那一项以 `acceptance_failed` 进 `partial` 不发布；`unverified` 单列，不并进通过 |
| 提交 / 回退 | 四件事同时成立才提交（目标达成且无未授权变更；无新增 / 加重的确定性干涉或裁切；调整在预算内；文件过验收）。否则 `_render(session, B0 patches)`——worker 的 applied / originals 两表回到事务前（ADR 0003 的还原语义），会话 patch_hash 回到 B0，磁盘无新文件。任何异常同样回退 |
| 提交后 | 会话挂上合同：`tavotto_apply_overrides` 只放行「与已提交列表逐条相同」的重发；增删改要 `user_authorized=true`（合同解除、验收作废）；`tavotto_export` 回执 `normalized.verified` 说这次导的是不是通过验收的那一版，并按同一份参数再核验产物 |
| 引擎补的两格 | `("ticks", "fontfamily")` override（`tick_params(labelfontfamily=…)` + 已有标签的 mathtext custom 集）；manifest 的 `face` / `math_face`（真正画字的脸；对数轴 `10^4` 的 mathtext 单列） |

## 1. 为什么是事务，不是「更聪明的 apply」

`apply_overrides` 的语义是全量列表、无条件应用——那正是画布拖拽需要的。给它加
判据会让画布里的每一次拖动都过一遍规范化裁决，而且判据需要一个**固定的基准**：
每轮把上一次结果当新基准会把累计漂移洗白。所以基准、约定、裁决、回退收在一条
独立的事务里，apply 只在**提交之后**多一道「别悄悄撤掉规范化的编辑」的门。

## 2. 布局所有权

脚本用了 `layout="tight"` / `"constrained"`：改尺寸后引擎在 draw 里自己重排，事务
量不到裁切，一条 `position` 都不落（用例 `test_persistent_layout_engines_stay_in_charge`）。
脚本一次性 `tight_layout()` / `subplots_adjust()` / 什么都没做：分数是死的，文字 pt
是死的，缩小后必然撞——这时才做外边距重排，且落在既有的 `axes.position` override
上，热态与全量重放同一条路（ADR 0042 的钉住机制原样可用）。**不新增、不关闭、
不切换任何布局引擎**。

## 3. 承认的边界

* 后端只守得住它管辖的路：模型用 shell 改 `.py`、用 Pillow 重画，后端拦不住——
  技能（SKILL.md「禁止的绕路」）负责，`tests/test_codex_plugin.py` 看住那段文字。
* 图例换位置只在预设表里挑；移到图外、重组网格、改长宽比都要用户明确要求。
* 刻度**数量增长**后新建的标签里的 mathtext 仍在默认字体集（`tick_params` 没有
  math 家族的键）——`math_face` 与最终 PDF 的字体清单都量得出，不会报成已换。
* 误差棒容器 / 图像 / Collection 没有 `geometry`，图例压它们只标风险不挡。
* 原件与 live 图尺寸不一致（`bbox_inches="tight"`）只报告不裁决：把 tight 当图幅
  定义仍是 ADR 级未决问题。
* 没跑真实 Codex 宿主：`tests/test_mcp_normalize.py` 的最后一条是真 stdio server
  子进程的工具级集成，宿主那一段按 `docs/acceptance/codex-desktop-canvas.md` 人工验。

## 4. 看护

`tests/test_normalize.py`（合成 manifest 上的逻辑）、`tests/test_mcp_normalize.py`
（真 matplotlib：零修改往返 × 3 种布局、只改字体、缩宽、持久引擎、手工布局、
新增干涉 + 装不下退出、缺字体退出、无解宽度不碰原件、有意重叠不误判、原有问题
保留、越权与合同、会话隔离、重复执行不漂移、五种格式验收、原件不一致、stdio 链路）、
`tests/test_codex_plugin.py` 末节（技能文字）。引擎侧新 prop 由 `test_invariants_engine.py`
的五条不变式自动覆盖。
