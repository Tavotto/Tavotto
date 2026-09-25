# 已有图的保留式规范化（ADR 0051）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「已有图的保留式规范化（2026-09-13，ADR 0051）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **`tavotto_normalize_figure` 是第八个工具**，也是用户「已有一张图、只要改宽度 /
  字体 / 字号下限」时该走的那条路——技能里明写不许用 `tavotto_apply_overrides` 手拼。
  实现全在 `bridge.normalize_figure()`，逻辑全在 `engine/normalize.py`（纯标准库、只算
  不画）：B0 → 约定 → 计划 → `_render` → `compare` → 最多 3 轮局部修复 →
  `export(acceptance=…)` → 提交或 `_render(B0 patches)` 回退。**任何异常都回退**。
- **桥只翻译**：干涉检测在 `engine/interference.py`、产物验收在 `engine/artifactcheck.py`、
  逐元素裁切判据是 `preflight.element_overflow()`——三处都进了 `_BRIDGE_IMPORT` /
  `BRIDGE_IMPORTS_AT_MIN`，`MIN_TAVOTTO_VERSION` 因此在 v0.15.0 抬到 0.15.0。
- **提交之后会话挂合同**（`Session.contract` / `Session.normalized`）：`apply_overrides`
  只放行与已提交列表逐条相同的重发；增删改要 `user_authorized=True`（合同解除、验收
  作废）。这一道既挡修复循环 / 模型扩权，也挡画布账本不带规范化 patch 时的静默还原
  （画布 seed 的是 `overrides: []`，见 `_live_session_for` 的说明）。`export` 回执的
  `normalized.verified` 说这次导的是不是通过验收的那一版；`acceptance` 逐格式给核验结果。
- **由用户目标直接决定的规范规则不挡事务**（`normalize.TARGET_RULES`：要 120 mm 时
  `page-width` 报出来但按用户要求执行，进 `profile_conflicts` 与留档）。其余新增 /
  加重的 error 级与**确定性**干涉才挡；原图已有且未加重的保留并报告，不顺手修。
- **图例候选只收「自己干干净净」的**：换到一个还在压别的东西的位置不叫修好。
  外边距重排只在有裁切 / 压到别的子图的那个方向上做，且相对 B0 有预算。
- 看护：`tests/test_normalize.py`（逻辑）、`tests/test_mcp_normalize.py`（真链路，含一条
  真 stdio server 的工具级集成——**不是**经 Codex 宿主的端到端）、`tests/test_codex_plugin.py`
  末节（技能文字：路由、禁止的绕路、按退出码说话）。
