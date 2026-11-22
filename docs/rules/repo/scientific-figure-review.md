# 科学图形：开发与评审时的七个问题

> 由根 `AGENTS.md` 子系统索引「跨仓库」一行点名（2026-11-21 立）。知识体系在 `docs/sciviz/`，Agent 准则的唯一出处是
> `src/tavotto/profiles/sciviz_guidance.json`（看护 `tests/test_sciviz_guidance.py`）。

**什么时候读**：改到下列地方时读。
- 图形模型与编辑：`src/tavotto/engine/manifest.py`、`src/tavotto/engine/overrides.py`；
- 预检：两侧求值器、`publication.json`；
- 数据出图：`src/tavotto/engine/ai_data_*.py`；
- 计算 SVG：`src/tavotto/engine/chartmodel.py`；
- 导出管线；
- 原生 R、ggplot2；
- 任何给模型看的作图或改图准则。

七问，每问都要落到一样可测的东西上；不适用就写「不适用」：

1. **数据与视觉还一一对应吗？** 改动后，有没有数据被静默丢掉、截断、挪动？
   - 判据：`data_fingerprint`，以及 V-001 / V-002 的事实（`docs/sciviz/validation-spec.md`）。
   - 样式类编辑不得改变数据指纹。
2. **会不会引入统计上的误导？** 例如改了尺度或范围，界面却暗示统计重算过；把对称误差线带进 log 轴（P3）。
3. **图型与数据类型、任务匹配吗？**
   - 依据写明是 [原文] 还是 [推导]（`docs/sciviz/decision-framework.md`）。
   - 不把品味（`basis: taste`）当作问题报告。
4. **自动操作有没有替用户定了科学假设？** 例如 SD 还是 SEM、要不要归一化、是否配对、剔除哪些数据。
   - 定了的必须在响应里写明，并且用户能一键改。
   - 判不了的进「需要你确认」，不猜。
5. **数据、派生、统计方法、绘图参数还能追溯吗？** 配方与统计元数据有没有在链路里丢掉（P8）。
6. **现有的 matplotlib 工作流和可编辑能力还在吗？** 不变式测试、golden 对拍、写回事务都要保持绿。
7. **能自动测试吗？**
   - 新规则必须带四类用例：正常、边界、错误、判不了，外加一个用真 worker 的防误报用例。
   - 判不了的情况不能冒充判过：量不了就记 unknown，不出 issue，也不显示「已通过」。

**规则纪律**：
- 新规则先写 V 规格，再实现。
- `basis: encoding` 的判据用 `warn`（维护者 2026-11-21 定），其余数据语义类用 `suggestion`。
- 一律 `fix: none`，不自动修。
- 给模型的准则只经 `engine/sciviz_guidance.render()` 取文本，不在提示词里另抄。
