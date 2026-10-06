# ADR 0116：准备会话是 GUI 「准备并打开」的默认入口——一个面板、三种不同的关停、连接失联不是执行失败

日期：2026-10-06 · 状态：**Accepted**（产品目标由「项目 Onboarding × 执行主链路收敛」包的 T09 给定）
相关：[0053 准备接口](0053-foundation-contracts-and-preparation.md)（`PreparationService` / 取消的所有权）、
[0099 脚本 input](0099-script-input-bridge.md) §九（同一问一个展示面）、[0114 环境建议 / 检查 / 采用](0114-environment-recommend-check-adopt.md)、
[0115 依赖授权绑定实际影响](0115-dependency-authorization-binds-impact.md)、[0057 首开](0057-first-open-environment-and-workdir.md) §三（运行目录确认）、
[0106 缺数据指认](0106-missing-input-relink.md)；规则全文 `docs/rules/backend/preparation-and-receipts.md`（会话合同）、
`docs/rules/frontend/readiness-and-left-shell.md`「准备面板」（前端）

## 问题

T01～T08 在后端立起了 script-first 的准备会话（`engine/prepsession.py`：`session_id` / `config_revision` / `observation_seq` /
`attempt_id`，后端生成的 `run` / `cancel` / `recheck` / `prepare_dependencies` 动作，check-use 窗口），但界面一直没有消费者：

1. 素材库「运行并发现图」与检查条的「试运行」走同步阻塞的 `/api/registry/probe`：没有任务句柄，刷新 / 断线之后无处可 GET，
   脚本停在 input 上时 HTTP 一直挂着；
2. 「门 → 弹窗 → 重跑」有三套：`scriptRunStore` 的 `needs_workdir` / `needs_preparation` 相位 + `rerunGated` / `onGateResolved`、
   `depRepairStore` 装完重跑、改指表代际订阅自动重跑——每一套都在前端自己决定「答完了，再跑一次」；
3. 取消：`/api/registry/probe/cancel` 硬杀该脚本键下的会话；准备会话的取消只在 build **返回之后**才收（长计算 / 停在 input 上的
   脚本要等它自己结束），共享会话的等待者也要陪着等完；
4. 「执行结束」「捕获到图」「首次编辑渲染可用」混在一句「已发现 N 张图」里。

## 决策

### 一、一个面板，按钮只来自报告

`PreparationPanel`（非模态，贴在检查条下）是所有已迁移入口共用的**一个**展示面；`projectPreparationStore` 只持有报告投影、参数快照、
订阅与网络状态；句子与主按钮只在 `lib/preparationText.ts` 翻译——**一句话 + 至多一个主按钮**（用户硬性要求「卡片一句话读懂」），
其余（检查项、要装什么、参数、失败原文、那一次的诊断、次要动作）在默认收起的「详情」里。

* 主按钮 = 报告里后端生成的那件事：`run` →「确认并运行」、`prepare_dependencies` →「准备依赖」（回显 `impact_digest`）、
  `environment_choice` →「使用这个环境」（既有采用端点，ADR 0114）、`workdir_choice` →「选择运行目录」（既有确认框）、
  `input_location` →「指认数据位置」（既有对话框，ADR 0106）、`awaiting_runtime_input` → 面板里嵌入同一个答题表单、`completed` →「进入编辑」。
  报告里没有的动作，界面就没有那颗按钮——前端没有第二份 ready 判据。
* 既有对话框（运行目录 / 缺数据 / 环境）在面板里是**薄展示适配器**：面板只交出载荷，作答仍是原来那一个端点；作答引起的环境 / 改指变化
  只让空闲会话**只读地**重新检查（`recheck`），下一步仍由报告给、由用户点。**任何入口都不在答完之后自动认领 `run`。**

### 二、入口

| 入口 | 走向 |
|---|---|
| 项目打开 / 恢复后的检查条，选定目标的「准备并运行」 | 准备会话（`{script}`，参数草稿此刻冻结） |
| 素材库脚本行 ▶ | 同上；行上的状态一句话翻译会话 phase，点它回到面板 |
| 接入中心逐行「试运行」、渲染路上的门（运行目录 / 依赖 / 缺数据对话框）、完整 PNG 的编辑准入 | **未迁移**（保留，见 §七） |
| MCP / CLI | T10 |

### 三、三种关停是三件事

* **关面板**只改呈现（`uiStore.preparationOpen`）：订阅照旧；面板认领过的 input 展示面放手，原对话框接着展示**同一问**（ADR 0099 §九）。
* **切项目**：前端换代（迟到响应作废、轮询停止、订阅丢掉），**后端什么都不取消**——用户的执行与已授权的安装照常跑完；切回来重新打开时同一目标复用同一会话。
* **明确停止** = 会话的 `cancel` 动作，按 owner 只退役本会话的工作，并且**当场**生效：`pool.build_owned(on_acquired=…)` 在取到会话、执行之前把
  `(worker, created)` 报给 `PreparationService.note_owner`；取消时会话是本计划新建的 → `force_cancel(expected_worker=…)` 只关这一条（build 以
  WorkerError 返回、按取消收场）；是别人的（共享会话的等待者）→ 不碰它，本计划直接以取消收场、不再等它，执行线程迟到的结局不改写终局；
  取消比「取到会话」还早到时，取到那一刻按同一规则处理。取消落地后会话可以马上重新检查 / 再跑一次。

### 四、连接、预算、重连与重启

* 取报告的 HTTP 等待有自己的看门狗（15 s，前端常量）：超时 / 断网只把 `connection` 标成 `lost`、按退避继续补拉，**不改 phase、不标失败、
  不重发动作**；执行有它自己的工作预算与取消（build / input 的既有上限不变）。动作请求网络失败时不重发，以补拉到的报告为准。
* SSE `preparation.session` 只是「重读」提示；事件流重连时补拉全部会话。
* 应用重启后旧 `session_id` 404（`unknown_or_restarted`）→ 前端**重建只读检查会话**，面板说「已按此刻的状态重新检查，没有自动运行」；
  不猜那次执行的结局，不自动重跑（合同 §F）。
* 迟到响应：项目代际 + 发请求那一刻的项目 + 同一会话里 `config_revision` / `observation_seq` 只许前进 + 重建后的会话不被旧会话 id 的回包换回去。
  同一条代际纪律补到了 `projectReadinessStore`（A → B → A 时 A 上一代的在途请求既不落地、也不被当成「在途」复用）。

### 五、结果分层与「进入编辑」

报告的 `facts.execution_finished` / `facts.figure_captured` 由后端给；**首次编辑渲染可用**（`first_edit_ready`）是前端观察到的渲染事实（那张图的
渲染态有了精确 manifest），只用来决定说「已进入编辑」还是「正在打开编辑…」，不回写后端、不当业务判据。跑完没图说「运行完成，未发现可编辑图」，
没有「进入编辑」。成功的报告带 `captured`（这次尝试捕获的公开描述符，与试运行响应同一份）；「进入编辑」直接用它走稳定动作 `openFastEdit`
（清单里还没有这张新图时先用描述符加进画布），引擎按热会话渲染，**脚本不再执行**（e2e 以项目外的执行日志计数钉住）。

### 六、「复制诊断」

面板里的失败由 `TaskDiagnostic`（T04，那一次尝试的冻结白名单快照）提供；依赖作业失败用 `kind=dependency`。素材库旧路径上的「复制诊断」
（报错原文 + traceback 进剪贴板）**保留在开关关闭时的旧路径上**，随旧路径一起退役——面板路径不再提供它。

### 七、本地体验开关与退役

`lib/preparationFlag.ts`：`localStorage['tavotto.preparationPanel']`，**默认开**；`'off'` 回到旧的同步试运行（保留一版）。默认开的前提已核实：
参数草稿在打开那一刻随目标冻结进会话（T03 运行配置），冷重放 / 导出读产物里冻结的引用，不会因走面板而退化成空 argv。

晋升（删除开关与旧路径）条件：本 ADR 的入口在 T11 真实首跑资格里通过；接入中心的逐行试运行迁到面板；一个发布周期内没有回到 `'off'` 的需要。
届时退役 `scriptRunStore` 的门相位与重跑协调（`handOffProbeGate` / `rerunGated` / `onGateResolved` / `whenScriptIdle` / 改指代际订阅的自动重跑）、
`ScriptLibrary` 的修复卡 / 门再打开 / 失败恢复 / 「复制诊断」块、`/api/registry/probe` 的前端调用（后端端点保留为 MCP / 旧客户端的兼容入口直到 T10/T12）。
渲染路上的门对话框是**普通编辑时的替代展示面**（合同 §K），保留；它们的「答完重排渲染」（`retryEnvironmentFailures`）重排的是渲染请求，不是脚本首跑。

## 后果

* 正面：首跑有任务句柄，断线 / 刷新 / 重启都能以 GET 补回真实状态；停止当场生效且只关自己的；「答完自动重跑」从已迁移入口上消失；
  执行结束 / 捕获 / 编辑可用分开说。
* 代价：开关默认开的这一版里，旧路径的代码仍在（关开关时用）；接入中心、渲染路上的门、PNG 准入仍是旧展示面。面板与原对话框并存的组合靠
  `scriptInputStore.claimPresentation` 保证同一问只有一个展示面。
* 不改：会话合同（T01）、动作 / 身份 / 授权（T05 / T06）、input broker（T08）、诊断（T04）都没有新字段之外的变化；新增的只有报告的 `captured`
  投影与 `build_owned(on_acquired=)` / `PreparationService.note_owner`。
