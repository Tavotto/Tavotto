# ADR 0041：Codex / 内置 AI 的显式刷新接线、去重，与 Session 22 的遥测整合

日期：2026-09-02 · 状态：已接受 · 关联：ADR 0025（统一刷新）/ 0026（项目 watcher）/
0027（就绪度事实模型）/ 0006（Codex MCP）/ 0009（工作区根权威）/ 0040（Activity Bus 与 onboarding）

## 背景

ADR 0025 之后，「项目里的东西变了」在后端只有一条编排（`engine/project_refresh`），
watcher（ADR 0026）是它的第四个调用方。但两条**知道自己刚改了脚本**的路径一直
没有接上去：

* **Codex 插件**：技能里写的是「改代码之后重开一次会话」，Tavotto 界面要等 watcher
  下一轮（≤ 2.5 s）才知道有新脚本——而且 watcher 只在 Tavotto 开着的时候才在跑。
* **内置 AI**：`ai.done` 只带 `changed`，前端收到后自己 `markStale` 一次；watcher 随后
  又发一条 `panel.file_changed` 再 `markStale` 一次。同一次修改：两次重建、两条提示，
  而后端的注册表要等 watcher 才更新。

遥测这一侧，Session 17–21 加了多选栏、接入状态、教程、保存 / 恢复、包管理，一条
事件都没记；活动信号（ADR 0040）明确了「不是遥测」，但也没有说清哪几条**可以**经
同意态映射出去。

## 决定

### 1. Codex 有显式的 `tavotto_refresh_project`；两条投递路径，一份刷新服务

工具输入尽量少（`session_id?` / `project_path?` / `reason?` 只认 `codex`）。**项目来自
授权，不来自模型的自由文本**：优先会话（`get_session` 重新做范围校验），其次
`project_path`（与 `tavotto_open_figure` 同一套 `check_scope → resolve_target → check_scope`），
都不传时只在**恰好一个**项目有会话时用它；零个 `no_project`、多个 `ambiguous_project`。

实现（`bridge.refresh_project`）先探 `127.0.0.1:5089/api/version`：

| 情况 | 做法 | `delivered` |
| --- | --- | --- |
| 运行中的 Tavotto 可达 | `POST /api/projects/open {default:false}` → `POST /api/project/refresh?pj= {reason:"codex"}` → `GET /api/project/readiness?pj=`；带本机凭据（`session_client`） | `app`——前端当场收到 `registry.changed` / `assets.changed` |
| 不可达 | 在**本进程**调同一份 `refresh_project_index(reason="codex")` + `readiness.compute()`；没有 SSE 可发 | `local`——下次打开项目读到的就是新注册表 |
| 可达但刷新失败 | 原样带回它的 code，**不退回本地再试**（同一份磁盘事实） | — |

两条路都不复制 discover、不 probe、不跑脚本。本进程那份状态（`_RefreshCtx`）按项目缓存，
第一次如实报 `assets.baseline: true`（ADR 0025 §3），第二次起是真的跨轮 diff。结果里
**没有绝对路径**：项目用与 `app._project_id()` 同一把尺的短 id，脚本 / 图都是项目相对名。

**桌面版的诚实限制**：sidecar 的动态端口与凭据经 Tauri 壳的 stdin 交接、不落盘
（`session_client` 的文档），所以桌面用户这条路总是 `local`——Tavotto 里的更新仍靠
watcher（≤ 2.5 s），工具的文字里如实说「未在运行 / 本地完成」。

### 2. 内置 AI：文件变了就在 `ai.done` 之前走统一刷新（reason=`ai`）

`ai_bridge.run(..., on_changed)` 是注入的钩子（边界同 `RefreshSink`：引擎不 import app）。
pump 线程在算出 `changed` 之后、发 `ai.done` 之前调它一次；`refresh_outcome()` 把结局
压成只有枚举与布尔的摘要 `{status: ok|failed|skipped|not_wired, code?, registry_changed?,
assets_changed?, published?}`，进 `ai.done.refresh` 与历史库新列 `refresh`（`ALTER TABLE`
补列，老库不迁移格式）。

app 层的钩子 `_after_ai_change(ctx, script)` 与 watcher 的 `_dispatch` 顺序**逐字相同**：
作废 worker → 统一刷新 → `panel.file_changed`。区别只在「谁先看到这次写入」（§3）。

**不看 status**：CLI 超时 / 非零退出但文件已经改了，磁盘上的事实就是改了。
**不 probe、不跑脚本**：重渲染由前端收到 `panel.file_changed` 之后按既有纪律决定。
**刷新失败不把 AI 修改伪装成全部成功，也不把会话记成失败**：`changed: true` +
`refresh.status: failed` 是「改成了、项目没刷新」，前端单独说这句（`status.aiChangedRefreshFailed`），
watcher 下一轮还会再试。

### 3. 去重：一次修改只形成一份实质更新

* **后端**：`ProjectWatcher.absorb(paths)` 把这几条脚本**此刻**的签名记成「已消化」并从
  pending 里摘掉，返回真的被吸收的那些。AI 路径先 absorb：吸收了（常态）→ 三件事这里做全，
  watcher 下一轮看到同一签名不再动；没吸收（watcher 已先结算）→ 只再走一次刷新（按内容比，
  无差异零事件），不再作废、不再发第二份事件；没有 watcher → 全做。判据是签名，
  不是时间窗：用户紧接着再改一次照常触发。
* **Codex 路径**：刷新写注册表 → watcher 靠 `is_self_written()` 认出（ADR 0026 §4）；
  新脚本文件本身 watcher 仍会看到 → 刷新一次 → 无差异零事件。
* **前端**：`ai.done` **不再** `markStale`——stale 只由 `panel.file_changed` 置一次；
  `panel.file_changed` 带 `reason: "ai"` 时不弹「脚本已更新」，一次修改只留 `ai.done`
  那一条可理解的提示。老后端没有这两个字段：行为与从前一致（watcher 兜底）。

### 4. 遥测：九条新事件，白名单两侧登记，CONSENT_VERSION 升到 2

| 事件 | 捕获点 | 字段（全部闭集 / 有界整数） |
| --- | --- | --- |
| `project_refresh_completed` | 服务端 `app.refresh_project` 成功之后（四条路径唯一漏斗） | `source ∈ {watcher, manual, codex, ai}`；`changed_bucket ∈ {none, one, few(2–5), many}`。probe / 手工登记 / 打开项目**不记** |
| `project_readiness_opened` | `projectReadinessStore.openCenter({source})` 报告到了之后 | `source ∈ {banner, panel, quickedit, palette}`；`status_bucket ∈ {all_editable, mixed, layout_only}`（零张图不发） |
| `tutorial_started` | `lib/onboarding/tutorial.landTutorial` 真的开始 / 重新开始时 | `source ∈ {picker, help, settings, palette}`；`tutorial_version` |
| `tutorial_step_completed` | `flow.completeStep(id, 'done')`（跳过不记） | `step_id`（`stepIds.ts` 的十个）；`tutorial_version` |
| `tutorial_completed` | 最后一步之后 | `tutorial_version` |
| `context_bar_multi_used` | `lib/activityTelemetry`：活动信号 + **浮动栏来源作用域**；「更多」直接记 | `action_id`（13 个）；`selection_size_bucket ∈ {2, 3_5, 6_plus}` |
| `document_saved` | `documentStore.scheduleDiskWrite` 的三个结局 | `trigger ∈ {manual, autosave}`；`outcome ∈ {ok, conflict, failed}` |
| `recovery_action` | `recoverLocalCopy` / `discardLocalCopy` | `action ∈ {restore, keep_main}` |
| `package_action` | 服务端 `api_packages_run` 的 on_event，作业到终态 | `action ∈ {install, update, remove}`；`outcome ∈ {ok, failed, cancelled}`。**没有包名** |

活动信号 → 遥测**只映射浮动栏那一条**，方向只有「活动 → 遥测（经同意态 + 后端白名单）」；
`ACTIVITY_KINDS` 其余十五种一条都不转（`activityTelemetry.test.ts` 逐种反证）。采集范围
实质性扩大，`CONSENT_VERSION` 从 1 升到 2：已保存的同意当场失效、界面重新征求，
`install_id` 不换（ADR / `telemetry.py` 的既有裁决）。

### 5. 入口整合

命令面板新增 `refresh-project` / `readiness` / `hints-reset`（id 稳定，中英文 label + keywords），
与既有 `tutorial-*` / `shortcut-help` 一起构成「刷新项目 / 显示项目接入状态 / 开始（继续、
重新开始）教程 / 重新显示新手提示 / 显示快捷键」。项目命令按 `projectStore.phase === 'open'`
出现（embedded / playground 没有项目，整组不出现）。顶栏「更多」加同两条入口。三处调的是
同一批 helper：`liveSync.refreshProjectNow`（统一刷新端点）、`projectReadinessStore.openCenter`、
`lib/onboarding/tutorial`。

## 后果

* Codex 改完 .py 之后有了确定的一步；技能与 README 里「重开会话 / 手动刷新」的说法全部
  换成调用刷新工具。
* AI 修改后前端的重建与提示各只剩一次；`ai.done` 到达时后端注册表已经是新的。
* 用户界面文案不再把 parameterizable 机械翻成「可参数化」：主文案用「可编辑的图 /
  仅排版」，注册表对话框用「已登记的源脚本」。
* 新的隐私承诺全部能指得出兑现它的代码：白名单两侧对拍用例、`activityTelemetry` 的
  逐 kind 反证、`test_tutorial_step_ids_match_the_frontend_closed_set`。
