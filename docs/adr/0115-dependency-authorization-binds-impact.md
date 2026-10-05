# ADR 0115：依赖安装的授权绑定「实际影响」，认领幂等，采用与安装互斥，项目内多作用域互斥不硬合并

日期：2026-10-05 · 状态：**Accepted**（产品目标由「项目 Onboarding × 执行主链路收敛」包的 T06 给定）
修订：[0061 联合依赖准备](0061-joint-dependency-preparation.md) §六（计划绑定：从「计划 id」扩到「计划 id + 影响摘要」；单包修复也只认领一次）、
[0019 受控依赖修复](0019-controlled-dependency-repair.md) §八（用户 venv 原地安装必须由确认的影响摘要回显触发）
相关：[0114 环境建议 / 检查 / 采用](0114-environment-recommend-check-adopt.md)（采用不带安装授权；本 ADR 补上「采用与安装之间的互斥」）、
[0053 准备接口](0053-foundation-contracts-and-preparation.md)（公开投影不带机器路径）、`docs/rules/backend/dependency-repair-and-packages.md`

## 问题

依赖安装的确认一直绑在 `plan_id` 上：计划形成时就把「装什么、装到哪」冻结了，执行只认 id。这对**一份计划**足够，但准备会话
（T01）把「确认」放在了一个更长的时间窗口里——用户看到的是报告里的动作，认领时后端才现算计划：

1. 看到与执行之间世界可能变了（项目多声明了一个包、目标环境被重建、用户换了解释器），但动作还是那个 id——**旧同意会被用来安装新增的东西**；
2. 反过来，与依赖无关的变化（进度、文案、脚本里加了一行注释、用户在另一个标签页换了运行参数）不该让授权失效、更不该重复起安装；
3. 两个标签页（或同一脚本的两份运行配置各一个会话）确认同一份安装，应当只有一个 provider 工作；单包修复（运行后缺包那条路）没有这个认领；
4. 采用环境（`PATCH /api/engine/environment`）与安装各写「项目的解释器决定」，交错时后写的会静默盖掉先写的（安装结束 `remember` 把结果记成项目环境）；
5. 受管环境是**项目级**的、账是并集：A 目录装了 `beta<2`，B 目录要 `beta>=2`——并入账会让 A 的脚本悄悄跑在 beta 2 上，不并入而"准备好了"则 B 带着不满足的版本运行。

## 决策

### 一、授权绑定的是「实际影响摘要」

`deprepair.impact_of` 一处算出一次安装的**实际影响**：目标类型与作用域（`managed_generation` / `project_venv_in_place`）、具体安装集合与约束
（排序后的规范串）、hash 模式、adapter、选中的组、是否新建环境、要不要先下载私有 Python（身份 / 来源 / 字节数）、出网、写入范围、失败后的回滚
性质（受管换代是事务；用户 venv 原地只进不退，**不承诺完整回滚**）、目标环境与它的代（只给不透明引用）、版本号 `IMPACT_VERSION`。
`impact_digest` 是它的摘要。**不在里面**：进度、文案、计划 id、有效期、事实 digest、规划输入指纹——它们变了不撤销授权。

* 跑前的门 / 准备会话显示的摘要与 `create_joint_plan` 绑定出的计划是**同一个函数、同一组输入**（`offer_impact` ↔ 计划的 `impact` 属性；
  `_managed_scope` 是共用的一处），所以用户确认的就是执行前比的那一份；
* 集合 / 目标 / 环境代 / 写入范围 / 私有 Python 下载任一变了摘要就变——旧同意**不覆盖**新增范围；以后新增一类影响要升 `IMPACT_VERSION`，
  旧版本摘要永远对不上；
* 对不上 → `dependency_impact_changed`（HTTP 409；会话里 `preparation_impact_changed`），**认领之前、零副作用**，响应带此刻的实际影响让用户对着它重新确认；
* 会改用户自己环境的动作（`modifies_user_environment`）必须**回显**它看到的摘要（`impact_digest`），光"点了一下"不够；使用（采用）环境不含修改权限，不变。

### 二、准备会话的 `prepare_dependencies` 动作

不新造面板后端、不新造安装器：动作引用 `deprepair.start_confirmed`（= `create_joint_plan` / `create_plan` + `prepare` / `install` + `managedenv` 代事务 +
`envlease`）。会话只记「认领了哪份作业、确认的是哪份影响、终局是什么」，phase `preparing_environment` 由依赖作业的事实派生。

* **认领幂等**：比较修订 → 回显摘要 → 失效检查 → 消费动作 → 提交副作用在会话锁内一次完成（T01 的 check-use 窗口）；重复点击 / 另一个标签页读到同一次尝试；
* **同一份摘要的在途作业只有一个**（`deprepair._joined`）：另一个会话（同一脚本的另一份运行配置、用户在安装时并行填参数）确认同一份影响，认领**原作业**并追加进度监听，
  不起第二个 pip，也不拥有它（不提供取消，只退役自己拥有的工作）；不同摘要撞同一环境则由 `envlease` 报忙；
* 单包修复（`install_async` / `install`）补上联合准备早有的 `_claim`；
* 终态 `done` 之后在**同一个会话**里按新环境重新检查，只重算差额（`dependency_delta`：已装 / 新增 / 目标是否变 / 范围是否变大）；失败 / 取消保留原代（受管换代的失败代不 active），
  报告里给出失败码并重新给一个**新的**授权动作；
* 脚本跑到一半才发现缺包（运行时才知道的那类）：这次执行已经发生（事实保留，`execution_finished: true`），差异计划随报告给出，补齐之后是**新的一次尝试**
  （outcome `needs_dependencies`，`rerun_required`），文案不叫"从异常点继续"；新增集合另行授权，不增加无限重试（轮次与 `_attempted` 照旧）。

### 三、采用与安装互斥

`deprepair.unless_installing(project, action)` 在 `_lock` 内：该项目有依赖作业在途 → `environment_mutating`；认领（`_claim`）与它同一把锁。`PATCH /api/engine/environment`
（项目范围的采用 / 选回默认）经它写；候选环境本身正被别的安装改动时一并拒绝。另一个方向：计划记下形成那一刻项目级解释器决定的签名（`selection_signature`），
执行前再比，期间用户采用了别的环境 → `repair_plan_stale`，一个字节不装。依赖安装自己写下的记录（`trigger=dependency_repair`）不算"用户的决定"。

### 四、项目内多作用域依赖互斥：不硬合并，给明确的出路（D04）

账（`installed_by_tavotto`）的每一笔记它是为哪个**作用域**（脚本所在目录）装的。联合计划对着账再核一道（`_with_scope_check`）：别的作用域装进去的版本不满足本作用域的
声明（已装但不满足的 / 需求 / 约束）→ `blocked`，理由 `dependency_scope_conflict`，带冲突项与两条出路。老账没有归属、同一作用域里用户改自己的声明都不算互斥。

第一版只服务**一个优先的绘图作用域**：

* 「换成本作用域」（`scope_policy=switch`）：新一代只装本作用域的**全集**，账换成本作用域的；这是**更大的影响**，有自己的摘要（`drops`：不再 active 的包 / `changes`：版本会变的包），
  要单独确认；旧代留到没人用，失败不动 active、不动账；只对受管环境（用户的 venv 不能被整个换掉）；
* 「子目录当独立项目」：各有各的受管环境，互不影响（会话之外）。

### 五、全局显式解释器压着时不授权安装（E05）

联合准备补上单包修复早有的 `#465` 纪律：`create_joint_plan` 在全局显式解释器生效时拒绝（`dependency_interpreter_pinned`），门放行，offer 不给影响摘要、只说"是谁锁的"
（来源与变量名，不带路径）；租约在手之后再复查（确认窗口里被钉上的，一个字节不装）。准备会话的 `dependencies` 检查项 `blocked`，不给授权动作。

### 六、终局进任务诊断

依赖作业到终局（成功 / 失败 / 取消）冻结成 `taskdiag.KIND_DEPENDENCY` 的白名单快照（`deprepair.diagnostic_projection`：闭集枚举、稳定码、计数、不透明摘要；包名、路径、pip 原文、
镜像地址一个都不进），按项目认领，后来的成功重试不改写旧失败。

## 不做 / 边界

* 不解决「用户的 venv 原地安装失败后的回滚」——如实说明非事务（摘要里 `rollback = none_partial_changes_possible`），遵守既有 provider 能力；
* 不静态执行 `setup.py`，不声称 dry-run 绝对无代码执行，不改网络 / 代理 / 证书 / 镜像策略（ADR 0111 / 0112 原样）；
* 运行时缺包的会话内授权只提供受管目标；项目 venv 原地安装仍走既有 `/api/engine/dependency/plan` → `install` 两步；
* 前端展示（T09）、MCP / CLI 的等价入口（T10）、冷机 / 联网真实供应（T11）不在本阶段。
