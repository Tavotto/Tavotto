# ADR 0124：一次运行意图——用户明确「打开并运行」之后，后端把首次执行之前的准备连续推进，然后执行恰一次

日期：2026-10-13 · 状态：**Accepted**（兼容性改造 P05b；证据 E13–E16、EX13；验收 AC-12 / 13 / 14 / 21 / 22）
相关：[0116 准备会话是 GUI 入口](0116-preparation-session-as-gui-entry.md)（本 ADR **有意修订**它 §一 的「任何入口都不在答完之后自动认领 `run`」）、
[0115 依赖授权绑定实际影响](0115-dependency-authorization-binds-impact.md)、[0061 联合依赖准备](0061-joint-dependency-preparation.md)、
[0122 声明依赖是安装根](0122-declared-dependencies-are-install-roots.md)；规则全文 `docs/rules/backend/preparation-and-receipts.md`「运行意图（P05b）」

## 问题

ADR 0116 把「准备」与「执行」拆成两次用户确认：依赖装好、目录回答完、环境选定之后，会话只**重新检查**并停在 `ready_to_run`，
要用户再点一次 Run（E13 实测：安装 `done` → 异步重核 → 同会话修订 +1、`attempts=0`、`launches=0`）。这个边界在「被动浏览 / 刷新 / SSE 重放
绝不执行」上是对的，但对一个用户**已经明确说了「打开并运行」**的意图是多余的摩擦：他批准了范围内的安装、回答了目录，还要再找一遍 Run。

反过来，不能靠「前端收到 done 就替用户点一下」解决：那让 GET / 刷新 / 组件挂载 / 事件重放都可能制造一次执行，且多标签、重复 done 会多跑（EX13：
先写外部计数再缺包的脚本，任何「准备好之后再来一遍」都会把计数变成 2）。

## 决策

### 一、持续意图是什么，绑定什么

**运行意图**（`prepsession.RunIntent`）是会话里一份**有界、单次、可核验**的记录。它绑定：项目、会话、脚本、运行配置引用（`run_config`，argv 原文不在其中）、
`config_revision`、一份 typed 的安装授权合同（`InstallGrant`）、墙钟截止与步数预算。内存里、随会话；应用重启即消失——**不猜、不重跑**（沿用 0116 §四）。
意图只推进**用户代码第一次执行之前**的步骤，执行被认领即用尽（`launched`）；不是「任意异常后的整段自动重跑」。

### 二、谁能创建

只有后端生成的动作 `run_when_ready`（「打开并运行」）被**用户明确认领**。认领必须回显 `impact_digest`——它是授权合同
（`InstallGrant.contract()`：只进 Tavotto 自己的受管环境且原子回滚、只装项目自己声明算出的联合计划、不改用户自己的环境、不丢 / 不改账上别的作用域的包、不含未核实的
用户指定包、数量上限、整个意图至多一次安装）的摘要，缺失 400 `preparation_impact_unconfirmed`，对不上 409 `preparation_impact_changed`，零副作用。
合同本身摆在动作的 `impact.run_intent` 里，边界只有 `InstallGrant` 一处定义。**GET、刷新、SSE 重放、组件挂载、收到 `done`、`recheck` 都不创建意图**；
`recheck` / 安装终局只会**推进已有的**意图。授权不是对每次安装的预签：每次具体安装仍走 `prepare_dependencies` 的老检查（现算影响摘要与动作回显一致、
失效核对），意图只是在合同范围内代用户做那次原本要点的认领。

### 三、预期变化 vs 外部变化（修订）

意图期间 `config_revision` 变化，只有两类来源被批准，其余一律是外部变化、**撤销意图**（`revoked`）：

* 本意图批准并认领的安装**终局 `done`** → 重新检查带来的环境 / 依赖结论变化（`dep_done_pending`）；
* 本意图正在等的**用户回答**被答了（工作目录 / 环境选择）→ 对应分量变化；依赖在那之前没被评估过时，评估出结论本身也是预期。

判据是纯函数 `classify_transition(old, new, stale, dep_done)`：比较两份计划的指纹分量与脚本字节修订。脚本字节变了（`source_changed`）、解释器被换（`external_change`）、
授权 / 数据绑定 / 改指表变了、已评估过的依赖结论变了，都不沿用旧快照。`run` 认领仍照常核 `preparation.stale_reason`。前端不能伪造影响：影响摘要永远是后端现算的。

### 四、状态机、预算、取消

`active → launched | cancelled | revoked | transferred`，后四者是吸收态（后到的 ready / done / recheck 复活不了）。每步只在会话锁内：
`_advance_locked` 依次判 墙钟预算（`RUN_INTENT_TTL_S`）→ 修订一致 → 有在途尝试则等 → 失效 / 外部变化 → 已有执行或运行时缺包则撤销（`already_executed`）→
走不通的检查项撤销（`blocked`）→ 依赖要授权：核合同（`grant_exceeded:*`）、已装过一次则 `no_progress` / `impact_expanded`，范围内才认领 `prepare_dependencies` →
还有要用户答的（目录 / 环境）或判不出（`unknown` 不当通过）则等 → 全部满足才认领 `run`。步数上限 `RUN_INTENT_MAX_STEPS=2`（一次安装 + 一次执行），**没有进展不循环**：
安装 `done` 后同一份影响还在 = `no_progress`；安装失败 / 取消 = 撤销，不自己重装。重复点击 / 重复 `done` / 多标签：动作是幂等认领，`run` 的认领与意图用尽在同一把会话锁里，恰一次。
**取消**（`cancel` 动作，意图 active 时一直可用）先撤销意图再退役自己拥有的工作。手点 `run` 也用尽意图（同一会话里不能既手动跑一次又被自动再跑）。

### 五、argv 改变 target_key：未执行意图的受控转移

同一脚本不同 argv 是不同会话（0116 / T03）。`run_when_ready` 可带 `transfer_from=<旧会话 id>`：在**新旧两个会话的锁**（按 session_id 排序取）内原子地——旧意图 → `transferred`（预算沿用、血统记下）、
旧会话**未认领**的 `run` / `prepare_dependencies` / `run_when_ready` 动作当场退役，新会话建意图。已认领的 session / claim / attempt / receipt / provider 事实一概保留（「退役」是撤销执行资格，
不是删除历史或遗弃 provider；进行中的安装继续，新会话认领同一份影响时 `joined`）。旧意图不是 `active`、已认领任何执行、或正在执行（`already_claimed` / `no_active_intent`）→ 409
`preparation_run_intent_not_transferable`，零副作用，**不为「换了参数」悄悄再跑**。不带 `transfer_from` 而同项目同脚本另一份运行配置已有 active 意图 → 409
`preparation_run_intent_conflict`（并存会让合计多跑一次，不合并不同 argv 身份）。旧 Run 认领与 argv 提交并发：两者在旧会话锁上串行，要么旧先 `launched`（转移被拒）要么转移先
（旧动作 id 已退役），合计恰一次；旧安装 `done` 之后不会重启旧目标（旧意图已是吸收态）。

### 六、运行时输入与已执行后的缺包

意图**不**创建第二次执行：脚本停在 `input()` 上时，报告 `awaiting_runtime_input` 复用同一个 inputbroker `Pending`（0099），与意图无关。执行已经发生之后才发现缺包
（EX13）仍是 0116 的合同——`needs_dependencies` + 补齐后的**新一次尝试**要用户另行点；意图已 `launched`，不会因补齐而复活（真实外部计数夹具钉住：计数不变成 2）。

## 后果

* 0116 §一 的「任何入口都不在答完之后自动认领 `run`」改为：**没有有效意图的入口仍不**；有效意图只放行用户代码首次执行之前的前置推进。
* 新错误码：`preparation_run_intent_conflict` / `preparation_run_intent_not_transferable`；新动作 `run_when_ready`；报告新增 `intent`。
* 未做（剩余项）：界面的「打开并运行」入口与按 argv 编辑触发 `transfer_from` 的接线；桌面 / Windows 真机；意图的跨重启持久化（有意不做）。
