# Tavotto-FigureBench（TAF Phase 0 夹具）

面向 Tavotto AI Foundation（TAF）的科研图任务基准。设计全文：
`docs/implementation/ai-foundation/06-benchmark-plan.md`；架构决策：ADR 0138。

**Phase 0 证明的是基准本身可信，不是任何 AI 能力**：每个改图场景都可解（参考解满足全部
目标与保持断言）、不是恒真（基线不满足目标），而且每条保持断言都被一个能出图的错误改法
打红过（`tests/test_figurebench_fixtures.py`）。TAF 自己跑这些场景从 Phase 2 起；在那之前
任何「通过率」都是 `not_run`。

## 纪律

- 数据全部是写死的合成数据，可公开；不联网、不用随机数（与 `tests/compat/README.md` 同约定）。
- 真值（`truth.json`）手写，测试用标准库核对；`_probe.py` 只给夹具背书，**不是** TAF 验证器，
  Phase 2 的 Figure Verifier 走 manifest / preflight / inspector，以这里的事实对拍。
- 拆分规则固定：`sha256(id) % 5 == 0` 进 `scenarios_holdout.json`，其余进 `scenarios.json`。
  Harness Lab（Phase 6）**只许读 dev**；保留集只出汇总分，不进任何提示词、经验库或优化轨迹。
  新场景按规则落位，不许为了凑分手工挪动。
- 夹具在**临时副本**里执行；执行前后本目录的文件快照必须逐字节相同。
- 普通 CI 不调用任何真实模型；`needs_real_model: true` 的场景只由单独的、明确计费的命令运行（Phase 6）。

## 场景字段（schema 1）

| 字段 | 含义 |
| --- | --- |
| `id` / `title` / `category` / `task_type` | 标识与分类；`category` ∈ single_element / complex_figure / scientific / data_to_figure / reliability |
| `fixture` | 夹具目录（改图）或 `csv_inputs/<文件>`（数据出图）；可靠性场景为 null |
| `instruction` | 交给被测系统的原话——**只有这一项**会给到被测 Agent |
| `check` | Phase 0 怎么核：`probe`（原生执行三类变体）/ `input_truth`（只核输入）/ `policy`（只登记期望结局）/ `covered_by`（现有用例）/ `gap`（`gap_phase` 补） |
| `goal` / `preserve` | 目标断言 / 保持断言（闭集类型，见测试 `ASSERTION_KINDS`）；`subjective: true` 时允许无目标断言 |
| `reference_solution` / `negative_variants` | 对 `figure.py` 的精确文本编辑（`find` 必须恰好出现 `count` 次）；反例的 `must_fail` 必须全红 |
| `expected_outcome` | 任务级期望结局：completed / partial / needs_input / failed |
| `policy` / `unverified` | 数据出图与策略场景的判据文字；明确不覆盖的事 |

## 夹具目录

- `line_two_series/`：两条带标签的折线、图例、带单位的轴标题。
- `multi_panel/`：2×2 共享 x 轴，左列 y 轴标题天然不对齐。
- `errorbar_fit/`：误差棒测量点 + 脚本内最小二乘拟合（标题带斜率）。
- `log_decay/`：对数 y 轴上的衰减曲线。
- `heatmap_colorbar/`：固定矩阵热图、显式色阶范围、带标签的色条。
- `cjk_labels/`：中英混排标题与轴标题。
- `grouped_bars/`：两组并列柱状图。
- `csv_inputs/`：数据出图的八份输入（标准 / 列名不明 / 缺失值 / 混合类型 / 时间序列 / 单位未知 / 双系列 / 夹带指令文字）。

## 场景清单

| id | 拆分 | 类别 | 标题 | 夹具 | Phase 0 核验 |
| --- | --- | --- | --- | --- | --- |
| `FB-C01` | dev | complex_figure | 左列 y 轴标题对齐 | `multi_panel` | probe |
| `FB-C02` | dev | complex_figure | 只改第二个子图的标题 | `multi_panel` | probe |
| `FB-C03` | holdout | complex_figure | 加宽整张图 | `multi_panel` | probe |
| `FB-C04` | holdout | complex_figure | 排版更紧凑但不拆共享轴 | `multi_panel` | probe |
| `FB-D01` | dev | data_to_figure | 标准 CSV 折线图 | `csv_inputs/standard.csv` | input_truth |
| `FB-D02` | dev | data_to_figure | 列名不明确 | `csv_inputs/ambiguous_headers.csv` | input_truth |
| `FB-D03` | dev | data_to_figure | 缺失值 | `csv_inputs/missing_values.csv` | input_truth |
| `FB-D04` | dev | data_to_figure | 数值与文本混合列 | `csv_inputs/mixed_types.csv` | input_truth |
| `FB-D05` | dev | data_to_figure | 时间序列 | `csv_inputs/timeseries.csv` | input_truth |
| `FB-D06` | dev | data_to_figure | 单位未知 | `csv_inputs/unknown_units.csv` | input_truth |
| `FB-D07` | holdout | data_to_figure | 双数据系列 | `csv_inputs/two_series.csv` | input_truth |
| `FB-D08` | dev | data_to_figure | 数据里夹带指令 | `csv_inputs/injection.csv` | input_truth |
| `FB-R01` | dev | reliability | 双窗口重复提交 | — | covered_by |
| `FB-R02` | dev | reliability | 原脚本被外部修改后回滚 | — | covered_by |
| `FB-R03` | dev | reliability | 文件锁冲突 | — | covered_by |
| `FB-R04` | holdout | reliability | 重启后识别中断任务 | — | covered_by |
| `FB-R05` | dev | reliability | Agent 超时 / 取消 | — | covered_by |
| `FB-R06` | dev | reliability | 取消期间产生候选 | — | gap |
| `FB-R07` | dev | reliability | Agent 输出无效 / 候选语法错误 | — | gap |
| `FB-R08` | dev | reliability | 数据源越界路径 / 符号链接 | — | gap |
| `FB-R09` | holdout | reliability | 验证阶段异常 | — | gap |
| `FB-S01` | holdout | single_element | 改一条曲线的颜色 | `line_two_series` | probe |
| `FB-S02` | dev | single_element | 统一加粗线宽 | `line_two_series` | probe |
| `FB-S03` | dev | single_element | 标题字号 | `line_two_series` | probe |
| `FB-S04` | dev | single_element | 移动图例 | `line_two_series` | probe |
| `FB-S05` | dev | single_element | 改轴标题但保留单位 | `line_two_series` | probe |
| `FB-S06` | dev | single_element | 换色图 | `heatmap_colorbar` | probe |
| `FB-S07` | dev | single_element | 改色条标签 | `heatmap_colorbar` | probe |
| `FB-S08` | dev | single_element | 中英混排标题字号 | `cjk_labels` | probe |
| `FB-S09` | dev | single_element | 只改一组柱子的颜色 | `grouped_bars` | probe |
| `FB-V01` | dev | scientific | 「更简洁」不许删点 | `line_two_series` | probe |
| `FB-V02` | dev | scientific | 柱宽调整不改高度与数量 | `grouped_bars` | probe |
| `FB-V03` | holdout | scientific | 误差棒变细，误差不能变 | `errorbar_fit` | probe |
| `FB-V04` | holdout | scientific | 用户要求删除离群点 | `errorbar_fit` | policy |
| `FB-V05` | holdout | scientific | 拟合线改虚线 | `errorbar_fit` | probe |
| `FB-V06` | dev | scientific | 对数轴改线性轴（用户明确要求） | `log_decay` | probe |
| `FB-V07` | dev | scientific | 放大标记，不许丢对数轴 | `log_decay` | probe |
