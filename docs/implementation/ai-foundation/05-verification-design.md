# 05 · 验证设计（Figure Verifier）

三种验证分开（ADR 0138 §二）：本文件只讲**图验证**（L1–L3、L5）与**持久化验证**（L4）。开发验证归 0131。

## 一、分层、复用件与缺口

| 层 | 问的问题 | 复用 | 新增（Phase 2） | 阻断性 |
| --- | --- | --- | --- | --- |
| L1 完整性 | 改的是允许的文件吗；能解析吗；输出契约还在吗 | `ast.parse`；registry 的静态 stem 识别；`scriptbackup` 的 sha256 | 候选树差异、原项目有界指纹、stem 集合一致、必要输出存在 | 硬阻断（failed 不提交） |
| L2 科学语义保持 | 数据 / 误差 / 坐标含义 / 单位没被动吧 | manifest 的 `xlim / ylim / xscale / yscale`、文字元素、`normalize.protected_snapshot` / `compare` | **worker 侧只读 `data_fingerprint`**（见下）；基于它的系列身份、点数、顺序、误差数据比对 | 未被用户豁免的 failed → 结局 failed |
| L3 几何与布局 | 用户点名的版式目标达到了吗；有没有新的重叠 / 出界 / 字号过小 | `preflight.run`、`interference.detect`、`normalize.geometry_issues`、manifest bbox | 「改前 vs 改后」的增量判定（只报新出现的问题）；多子图边对齐（基于 bbox 与容差） | 目标项 failed → partial / failed；新增问题 → partial |
| L4 渲染与导出 | 写进文件的 == 热态 == 重开重放；导出合格 | `_compare_manifests` + `_replay_pixel_diff`（阈值不动）；`rendercore.inspector.inspect` | 把 verify 段从 `app.py` 抽成可调用检查（逐字搬家 + 对拍 `tests/test_pixel_compare.py`）；按任务策略决定是否做 | 写回 / 导出任务硬阻断；改图任务按需 |
| L5 主观评价 | 更美观 / 更专业？ | — | 可插拔 Visual Judge 接口（首轮不接付费模型） | **永不**改变 L1–L4 的 verdict；只写进 explain |

## 二、数据指纹（L2 的前提，Phase 2 最小增量）

- 位置：worker 侧 `manifest.py`，`_line_fields` / `_collection_fields` / `_bar_series_fields` / `_errorbar_fields` 旁，
  新增**只读**字段 `data_fingerprint`：`{n, shape, sha256, finite_n, x_range?, y_range?}`。
- 规范化：float64；NaN / ±Inf / 掩码值固定写法；形状参与哈希；时间数据先转 matplotlib 内部数值（记 `units` 来源）——
  与 FigureBench 探针 `_probe.py::digest` 同一规则，Phase 2 加一条对拍用例。
- 边界：不进 `editable`、不进文档、不参与 gid / identity（0083）；不受 `pathgeom.TOTAL_BUDGET` 截断；超过上限（例如
  10⁷ 个数）时流式哈希或记 `unknown`，不能静默截断。
- 读不到（自定义 Artist、`unsupported[]` 里的元素、native 会话离线）→ 该项 `unknown`，**不是** verified。
- 隐私：指纹是哈希与范围，仍按本地证据处理，不进 `public_projection` / 诊断公开面。

## 三、判定规则

- 目标断言：用户要求的变化发生了（例：title fontsize == 12 ± 1e-6）。
- 保持断言：Figure Intent Contract 的 `preserve` 项在改前 / 改后 manifest 上一致。
- 「看不见」不是「没变」：断言指名的对象在改后找不到 → failed（被删）或 unknown（无法观察），由对象是否在改前可见决定。
- 像素相似度**不**用于判断数据保持（L2 只看指纹与结构字段）。
- 跨 OS / 字体 / matplotlib 版本不直接套用像素阈值：L4 的像素门只比较同一台机器、同一次会话里的两次渲染（与写回一致）。

## 四、每条核心断言都要有反例

Phase 2 起，每加一个判据，同 PR 带一个「能出图的错误改法」让它变红（FigureBench `negative_variants` 的同一形状），
并在 PR 正文附变异清单与退出码。Phase 0 的 19 个探针场景已经各带至少一个反例（`tests/test_figurebench_fixtures.py`），
Phase 2 的验证器必须在同一批反例上给出 failed——这就是验证器的验收用例。

## 五、证据

CheckResult（`03-contracts.md` §三）落 `data_dir()/cache/ai_tasks/<task_id>/checks.json`；保留策略随 `ai_snapshots`
（20 条）。证据只存本地；公开摘要只含 id / layer / verdict / reason_code。

## 六、性能预算（Phase 2 实测后定）

L1：毫秒级（AST + 文件指纹，有界）。L2/L3：刷新后本来就有一次 manifest，新增成本是指纹计算（worker 内，与数据量线性）。
L4：一次性 worker 全量重放 + 两次 1000 px PNG——这是唯一的大头，默认只在写回 / 导出类任务上做。Phase 2 PR 必须给出
FigureBench 上的实测时延分布（中位数 / P95），不能只给估计。
