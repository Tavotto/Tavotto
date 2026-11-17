# Agent 一步改到期刊合规（ADR 0180）

> 2026-11-12 新增（ADR 0180 第 2、3 步）。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例：`tests/test_mcp_fix_to_journal.py`（真 matplotlib）、`tests/test_mcp_error_next.py`（冲突点名与 `next`）、`tests/test_specfix_real.py`（事务本体，搬家后不改即过）、`tests/test_mcp_server.py`（工具清单 / 开关）、`tests/test_codex_plugin.py`（技能文字）。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 55
改到: `tavotto_fix_to_journal`、`tavotto_make_journal_ready`、`bridge.fix_to_journal()`、`bridge.issue_fix()` / `annotate_preflight_fix()`（preflight 结果的 `fix`）
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
- **没改成要点名冲突（ADR 0189 第 3 条）**：`constraint_conflict` / `unsupported` / `not_resolved` / `font_unavailable` 的回包带 `conflicts`
  （最多 `FIX_CONFLICTS_CAP` 条、每个约束短句最多 `FIX_CONFLICT_TEXT_CAP` 字）：`{rule, gid, gids?, constraints: [甲, 乙], prop?, repair?, want?}`——
  哪条规则、哪个元素、哪两个约束冲突，有 override 属性名就给。**只拼现成结果，不新写判据**（ADR 0180）：
  图幅步骤 = 目标图幅 ↔ normalize 的 `blocking` 里不许新增 / 恶化的那条（带 `repair`）；面板步骤 = specfix `changes` 里要写的那条 override
  （`prop` 取自它）↔ `blocking` 的新增问题，`unresolved` 与 `skipped` 单独点名（缺字体带 `want`）；复检 = 本次改动 ↔ `introduced` 里的 error / warn；
  基准图没能完整重放（`unsupported`）没有元素可指，点名渲染告警原文。拿不到的字段不给、不编；`exit` / `stage` / `detail` 的取值不变。
- **失败回包的 `next`** 由 `nextstep.fix_next` 在 `_call_fix_to_journal` 里加：`column_choice_required` / `font_unavailable` 要用户决定（`ask_user`，
  栏位候选来自 `columns`），其余没改成的（含图幅步骤出界）必须回脚本改（`edit_script`，`why` 指向 `conflicts`）。成功的回包不带 `next` 与 `conflicts`。

## 一步交付 `tavotto_make_journal_ready`（ADR 0189 第 1 条）

- **只编排、不新写逻辑，编排放在 server 层**（`server._call_make_journal_ready`）：打开 = `_call_open`、改 = `bridge.fix_to_journal`（**不带 `formats`**）、
  导出 = `_call_export`。理由：参数校验与结果整形（含草稿带回说明、产物核验行、更新提醒、proof）本来就在这三个工具处理器里，
  编排放 bridge 要么复制第二份整形、要么让 bridge 反向依赖 server；bridge 仍是「一个工具对一个会话」的原语。bridge 只多了
  `require_fix_engine()`（总开关 + 引擎有修复事务的门，`fix_to_journal` 与本工具共用，本工具在**打开会话之前**先过它，关着时不留会话）。
- **fix 与导出分开两步，不用 `fix_to_journal(formats=…)`**：后者导出被挡会把整个改动回到 B0；一步交付要的是「改动留着、停在预检、把决定交给用户」。
  所以 fix 通过即提交，之后 `_call_export` 走同一条预检门 + 产物核验；挡了 → `stage=preflight`、不出文件、改动留在会话（没导出的改动关会话也存草稿）。
- **绝不替用户确认**：不接受也不转发 `explicit_confirm`（传了也被忽略）。`preflight_blocked` 回 `ok:false, stage:"preflight"`（非 isError）、
  `blocking` / `not_verifiable`（各最多 `MJR_BLOCKING_SHOWN`）与 `next={action:ask_user}`，选项里点明「用户明确同意后用 tavotto_export 带 explicit_confirm=true」。
- **`stage`**：`open` / `fix` / `preflight` / `export` / `done`，是**停下的那一步**（成功 = `done`）。`dry_run` 跑完 fix 即停（`ok:true, stage:"fix"`），回到改前、不出文件。
  fix 没过（`column_choice_required`、冲突等）= `ok:false, stage:"fix"`，带 `fix_exit`、`conflicts` / `columns`、`next`（`nextstep.fix_next`）、`introduced`（有则）。
  任一步的 `BridgeError` 照常变 isError，只是 `stage` / `session_id`（已打开时）写进载荷，`next` 由 `call_tool` 挂。
- **回包要短**（result-budget）：改动按规则合并成 `changes:[{rule,count}]` + `changes_total`，`remaining` 按规则合并（`count`，最多 `MJR_REMAINING_SHOWN` 行）+
  `remaining_total`，导出只留 `{format,path,status,verdict}`；没有 manifest / patches，要明细用 `tavotto_session_state`。预览图沿用 image 内容块（`previews:false` 不附）。
- **会话不关**，成功也不关；草稿带回（`restored_draft`）、`discard_draft` 与 open 同一条路。入参：期刊三选一（`journal_id` / `profile_id` / `journal`，必给，与 fix 同名）、
  `stem`、`column`、`formats` / `out_dir`（缺省与 `tavotto_export` 一致）、`dpi`、`previews`、`dry_run`，加 open 的运行参数（workdir / argv 等）；不接受 `stems`。
- **开关与遥测**：与 `tavotto_fix_to_journal` 同一处置（总开关 + 引擎有 specfix 才出现；关着调用回 `journal_catalog_disabled`）；不进 `usage.TOOLS`；
  降级 server 的 `NORMAL_TOOLS` 有它。看护 `tests/test_mcp_make_journal_ready.py`（真链路：成功落盘 + 页宽、停在 fix / preflight 不出文件、dry_run、草稿、开关）。
