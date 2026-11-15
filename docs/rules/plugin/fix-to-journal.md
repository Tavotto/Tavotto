# Agent 一步改到期刊合规（ADR 0180）

> 2026-11-12 新增（ADR 0180 第 2、3 步）。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例：`tests/test_mcp_fix_to_journal.py`（真 matplotlib）、`tests/test_specfix_real.py`（事务本体，搬家后不改即过）、`tests/test_mcp_server.py`（工具清单 / 开关）、`tests/test_codex_plugin.py`（技能文字）。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 55
改到: `tavotto_fix_to_journal`、`bridge.fix_to_journal()`、`bridge.issue_fix()` / `annotate_preflight_fix()`（preflight 结果的 `fix`）
主题: Agent 一步改到期刊合规 → `fix-to-journal.md`
必守要点: 只拼 normalize + `specfix.transaction`，不写第二份判据；整体提交或回 B0；`fix` 只在 MCP 整形层
-->

- **跟期刊库总开关走**：关着时 `tavotto_fix_to_journal` 不进 `tools/list`、调用回 `journal_catalog_disabled`、`tavotto_preflight`
  结果不带 `fix`；旧引擎没有 `specfix.transaction` 时同样不出现（`specfix` 是**可选引擎模块**，只经 `_optional_engine`，不进
  `_BRIDGE_IMPORT` / `BRIDGE_IMPORTS_AT_MIN`）。工具名**不进** `usage.TOOLS` 与遥测闭集（与 `tavotto_guide` 同一处置：加进去要升
  `CONSENT_VERSION`，等维护者决定），走 `other` 桶；降级 server 的 `NORMAL_TOOLS` 要有它。
- **两段、各一个现成算子**：① 图幅 = `_normalize_figure(stage_only=True)`，只给 `width_mm`（必要时 `height_mm`），**不给 `min_font_pt`**；
  ② 面板内部 = `specfix.transaction(render, base, scale=1.0, profile, only)`。`scale` 恒为 1：单图会话里图就是它自己的页面
  （`run_preflight` / `_profile_issues` 同样用 `scale=1.0`），桌面端点的缩放比在这里不存在。
- **整体要么提交要么回到 B0**：任何不通过（栏位没选、第 1 / 2 段退出、复检 introduced 里有 error / warn、导出被挡或产物核验不过）都
  `_render(B0)`，会话与落盘记录零改动；通过才 `_persist` 一次（ADR 0078）。`dry_run` 跑完同样回到 B0、不出文件。
- **热态不可信就作废 worker**：任何一次渲染带 warning 或抛异常，`pool.invalidate` 作废（键要带写入根绊线的 `write_roots`，与
  `_spawn_worker` 取 worker 时同一份，否则对不上键、作废落空），在新 worker 上把 B0 重放回来；结果 `worker_retired: true`。
  回滚本身也失败时把会话从账本摘掉，下一次调用按落盘记录（只在提交点写）重建。
- **栏位自动选**：只有**一个**栏位与现宽相差 ≤ 15% 才自动取；否则（含「没有一个接近」）`column_choice_required`、什么都不改。
  候选栏位就是预检 `page-width` 判据认的那些，唯一出处 `preflight.judged_page_columns`（exact + max；typical / range 不参与，
  也不能用 `column` 点名）。exact 栏目标宽度就是它；**max 栏是上限**：现宽超了才压到上限，本来不超就不动（点名也不放大）。
  `fix.columns` 与 `column_choice_required` 的候选都带 `qualifier`。
- **预览图**走 MCP `image` 内容块（长边 1000 px，改前 / 改后各一张），**不进 `structuredContent`**；bridge 返回里的 `_previews`
  由 server 层摘出来。`previews:false` 不渲染。
- **`fix` 只在 MCP 整形层加**（`annotate_preflight_fix`，返回副本、不污染会话缓存），**不改 `preflight.run()` 的输出**——它与前端求值器
  靠 golden vectors 对齐。`auto` 的集合就是 `specfix.FIXABLE_RULES`，不在别处抄第二份清单；`size` 是 `page-width` / `page-aspect`；
  `verify` 是 `not_verifiable` 档；其余 `manual` 带一句 `hint`。
- 提交后若会话上有旧的规范化合同，视同用户的明确要求：合同与验收解除（`contract_released: true`）；没提交则原样留着。
