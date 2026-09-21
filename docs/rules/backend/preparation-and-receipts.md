# 准备计划、执行回执与源图产物（统一实施包 U01，ADR 0053）

> 2026-09-20 随 U01 新增，同日随 U03（ADR 0057）扩到「需要输入」/ 过期计划 / 环境证据；速查行在
> `src/tavotto/AGENTS.md`「按改动路径找细则」表里（`engine/preparation.py` / `receipt.py` 那一行）。
> 这里是这一主题规则的**唯一全文**；速查表只留一行。改规则改这里，并同步那一行。

- **计划与观测分开**：`preparation.PreparationPlan` 是「打算怎么跑」（环境证据 / 依赖意图 /
  LaunchContext / grant / 预算），`PreparationResult` 是「实际发生了什么」（状态 / 现有
  runtime 的事实 / 回执 / 错误 / 取消时刻）。计划**只读**产品自己的决定
  （`pool.resolve_worker_python` / `projectenv.state` / `workdir` / `depresolve`），不替产品
  选环境、不装包、不改 cwd。终局字段先于终局 `status` 写（与 `exportjob` 同一条纪律）。
- **执行只有一条路**：`pool.build_owned()`（`build()` + 池锁里给出的 `created`）。准备接口不另写
  `get + ensure_built`——自动 fallback 只有一份实现，漏掉一个入口就是「素材库里能打开、准备
  接口打不开」。已 build 且解释器决策没变的会话直接用它记下的 build 响应装配回执，不发请求。
- **取消只碰自己起的会话**（D11 / FO-009）：接受时刻只有「还没碰 pool」与「build 返回」
  两个；`created_runtime` **由池原子给出**（不从 `peek()` 快照推断——两份计划同时起步、池只建
  一条时主人只能是一个），为真才 `pool.force_cancel`，别的消费者的会话不碰；native 会话不在
  池里，永远碰不到；不承诺撤销外部副作用，`note` 如实说。
- **按项目认领**（FO-008）：`SERVICE.get / cancel` 都带 `project_id`，对不上就是没有
  （404 `preparation_not_found`），绝不返回别的项目的状态；执行线程用 `bound_project`
  钉项目。三个端点在会话认证之内，不进 `security._PUBLIC_PATHS`。
- **回执两半缺一半就是半张**：worker 自报（`figsession.runtime_report()`，随 v1 `build`
  的 `runtime` 回来，加字段不升版，safe / native 同一份）+ 控制面账本（generation /
  `script_sha1` / 来源标签 / spec 稳定字段 / LaunchContext）。老 worker 没自报是
  `partial`，不补不猜；`RECEIPT_PACKAGES` 是闭集，回执不是环境普查。
- **身份三分不许混**：私有失效键**含**机器路径（区分两个 venv 靠它）；公开语义身份不含
  任何路径；最终文件 hash 只在 `SourceArtifact.bytes_sha256`，不回写进语义身份；`receipt_id` /
  `generation` 是实例元数据，语义身份（`semantic_identity` / `plan_identity`）只吃回执的公开身份
  `receipt_identity`。默认 `to_payload()` 不带机器路径，诊断包用 `include_private=True`；
  `PreparationPlan.to_payload()` 同样：项目外的解释器只给来源标签（路径存私有字段），错误分支的
  `project_env` 只留 `ok / code / module / reason`。
- **LaunchContext 是派生视图**：`execspec.launch_context(spec)` 从 spec 算，`ExecutionSpec`
  与 `worker_argv` 的 golden 一个字节不动。四个 `cwd_origin` 各有且只有一个生产者
  （`sandbox` / `project`=脚本目录 / `project_root`=项目根 / native；ADR 0057 §二）；
  grant 只由 `workdir.set_mode / grant_for` 记账，只记时刻不记人，多 `mode` 与 `decided`。
- **环境选择前移的落点就是 `plan_for`**（U03，ADR 0057 §一）：它调 `pool.resolve_worker_python(root,
  script=…)`——项目 venv 的发现 + 体检 + 记住在这里已经发生（每进程每项目一次），计划里
  `environment.python_version / matplotlib_version / support / discovery / invalidated / error.explicit`
  如实写下选了谁、凭什么（体检量到的事实，ADR 0053 的公开投影：项目外的路径一律 None）、发现了什么
  没采用、上一条自动决策是不是刚作废、显式选择为什么用不了。计划仍然只读产品的决定，不替它选。
- **首开要问的事是终局 `needs_input`**（U03，ADR 0057 §三）：`workdir.decision_for` 说要问
  （数据只在项目根找得到 / 两处同名不同值）时 `register()` 直接落 `needs_input`，
  `result.required_input` = `workdir.confirmation_payload`（选项 / 证据 / 怎么回答），不起线程、
  不碰 pool。runner 抛 `workdir_confirmation_required`（计划后决定被清掉）落同一终局。答完
  （`PATCH /api/engine/workdir`）重新准备。
- **过期计划不执行**（FO-007）：执行线程在起会话之前把 `workdir.grant_for(root)` 与计划记下的
  `grant` 比一次，不一致就 `preparation_plan_stale`，一行脚本不跑。
- **DependencyIntent 是第二个读法，不是第二个安装器**：extras / marker / constraints / 冲突
  原样可见，看不懂的行 `kind=unknown` 保留原文（Poetry 的 `^` / `~` / 表值也是 unknown，不剥成
  任意版本）；安装路径的窄语法（ADR 0019 安全边界）一字不动。
- **case enrollment 台账**（`docs/implementation/tavotto-foundation/enrollment.json` +
  派生 md）：32 个 FO 场景逐一在台账里、与 registry 一致；enforced 的 case 必须指向真实
  pytest 用例（按 AST 找，不按子串）并写结果记录；预期实例集合在执行前生成；**空集合永远不是通过**。校验器在
  `tests/support/foundation_harness.py`，落点是 `invariants` job 的一步。
- **台账的 enforced 集合**：U01-S1 + U03 的 FO01 / FO02 / FO03 / FO07 / FO15 / FO19
  （`tests/test_foundation_first_open.py`，经真实 HTTP 入口，装置在 `tests/support/foundation_app.py`）；
  safe_stop 的 case 记录 `product_outcome=safe_stop` + `test_verdict=pass`，校验器按台账预期的结果
  对拍（`outcome_mismatch`）并分开计数，**不进自动兼容成功的分子**。
- 看护：`tests/test_execution_receipt.py`、`tests/test_preparation_api.py`、
  `tests/test_worker_runtime_report.py`、`tests/bridge/test_bridge_e2e.py`、
  `tests/test_foundation_harness.py`、`tests/test_foundation_first_open.py`。
