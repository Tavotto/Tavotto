# ADR 0138：Tavotto AI Foundation（TAF）——在现有 AI 桥上长出任务基座，而不是另起一套

日期：2026-10-19 · 状态：**Accepted**（2026-10-20 维护者接受；Phase 0 立项，Phase 1 起逐阶段取得资格，各阶段仍按 `07-pr-roadmap.md` 的入口 / 退出标准）
相关：[0015 Agent 注册表](0015-coding-agent-registry-and-settings.md)、[0014 safe / native](0014-safe-native-execution-profiles.md)、
[0049 写回像素门](0049-write-back-pixel-verification.md)、[0068 ArtifactInspector](0068-artifact-inspector-and-manifest.md)、
[0080 出版规范修复事务](0080-spec-fix-transaction.md)、[0083 override 目标身份](0083-override-target-identity.md)、
[0094 脚本写回与备份](0094-script-writeback.md)、[0110 数据路径改写](0110-rewrite-missing-input-path-in-script.md)、
[0127 执行策略与重试闸门](0127-execution-policy-and-unified-retry-guard.md)、[0131 开发 Agent 验证入口](0131-agent-verification-entry.md)、
[0132 AI 请求幂等与草稿](0132-ai-submit-idempotency-and-draft-cache.md)、[0133 运行前兼容性路由](0133-run-routing-compatibility-first.md)。
实施包：`docs/implementation/ai-foundation/`（审计事实、契约、安全模型、验证设计、基准、路线图、风险登记）。

## 问题

改图助手今天是「一次 Coding Agent 调用 + diff + 回滚」：`ai_bridge.run()` 快照目标脚本、以
`cwd=figures 目录` spawn `codex exec` / `claude -p`，Agent **直接改原脚本**，结束后对目标脚本做 diff、
走统一刷新（`docs/rules/backend/ai-agent-bridge.md`）。可靠性的底子已经很厚——请求幂等与提交账本（0132）、
跨进程忙碌认领、`after_sha256` 判过期回滚、`script_guard`——但有三件事它回答不了：

1. **改对了没有。** 「Agent 退出码 0 + 文件变了」就是 `done`。没有任何独立检查说明用户要的变化真的发生、
   不该变的科学内容（数据、误差、坐标含义）没变。manifest 里线条只有样式字段，**没有数据或数据指纹**
   （`manifest._line_fields`），所以今天连「数据没被改」都无从判起。
2. **改了哪些文件。** 「只修改目标脚本、不要运行脚本、不要改数据」只写在提示词里（`ai_bridge._build_prompt`），
   diff 只比目标脚本；Agent 改了项目里别的文件，系统不知道。
3. **下一种 AI 任务怎么接。** 数据出图、出版规范化、诊断……如果各自复制一套会话 / 执行 / 验证，
   就会出现第二份 Agent 注册表、第二个调度器、第二套历史库——正是单一权威原则禁止的形状。

## 决定

### 一、TAF 是现有 AI 桥的扩展，不是平行系统

| 职责 | 唯一权威（沿用） | TAF 加什么 |
| --- | --- | --- |
| 谁来执行（Codex / Claude Code） | `ai_agents.AGENT_REGISTRY`（0015） | 不加；任务适配器按 agent id 引用，经 `require_usable` 判可用 |
| 请求幂等、提交账本、草稿 | `ai_history` 的 `ai_requests` / `ai_pending` / `ai_drafts`（0132） | 不加；任务就是一条 session，`client_request_id` 原样走 |
| 会话状态与持久化 | `ai_history.sessions` + `_SCHEMA` / `_migrate` 的「看列在不在」迁移 | 按同一模式加列（任务类型、阶段、结局、验证摘要）；**不引入 `user_version`、不建第二个库** |
| 脚本写入、锁、备份、回滚 | `scriptlock.write_script` / `script_guard`、`scriptbackup.replace`、`_refuse_stale_revert` | 不加；候选提交只经这些入口 |
| 运行用户脚本 | `preparation` / `pool.build` / `enginesession.resolve`，safe / native 由 0127 / 0133 决定 | 不加执行路径；验证渲染一律显式 safe，绝不继承桌面授权去 native |
| 产物检查四态 | `rendercore.inspector.VERDICTS`（verified / failed / unknown / not_applicable） | 验证结果直接用这四个值，不另立词汇 |
| 重放一致性 | 写回事务的 `_compare_manifests` + `_replay_pixel_diff`（0049） | 抽成可调用检查后复用，判据与阈值不动 |
| 版式规则 | `preflight.run` / `interference.detect` / `normalize.compare` | 作为 L3 的检查后端，不复写判据 |

新增的只有四样：**任务契约**（TaskEnvelope / TaskResult，版本化）、**任务适配器注册表**（任务类型 → 适配器，
与 Agent 注册表正交）、**Figure Verifier**（L1–L5 分层、四态、证据引用）、**离线 Harness Lab**（只在开发侧）。

### 二、三种「验证」分开记，永不合并成一个「通过」

- **开发验证**：开发 Agent 改 Tavotto 源码后的验证，归 0131 的 `agent_verify`，与产品运行时无关。
- **图验证**（Figure Verifier）：用户要的变化发生了、不该变的科学内容没变（L1–L3，L5 可选）。
- **持久化验证**：写进文件的 == 热态 == 重开重放的、导出产物合格（L4，复用 0049 / 0068）。

TaskResult 里三者各有字段，任务结局（completed / partial / needs_input / failed / cancelled / interrupted）
与验证结论分开：Agent 成功退出 ≠ 图验证通过 ≠ 持久化成功。`unknown` 永不折算成 `verified`。

### 三、改图默认是「表现形式」修改

未经用户明确同意，不得改数据、删点、改归一化 / 拟合 / 误差定义 / 单位 / 坐标含义。Figure Intent Contract 把这条
写成可检查的保持断言；指令本身要求改科学内容（例：「删掉离群点」）时，结局是 `needs_input`，先确认再提交。

### 四、执行面不扩大

- 候选工作区放 `config.data_dir()/cache/` 下，**只复制、不链接**（符号 / 硬链接会让「改副本」变成改原件）。
- 模型生成或模型影响的代码不当作可信代码：safe 档是防意外的守卫，**不是沙盒**（不限网络、读、子进程、
  绝对路径写，见 `docs/implementation/ai-foundation/04-security-model.md`）。数据出图 v1 因此**不执行模型写的代码**：
  模型只给结构化的列映射建议，脚本由 Tavotto 固定模板生成。
- 有界修正受任务策略的预算约束，并复用 0127 的「无新根因不重试」原则；结果不确定（spawn 之后失联）时绝不自动重来（0132）。

### 五、1.0 收敛纪律的登记

根 `AGENTS.md` 的 1.0 纪律禁止扩大产品能力。TAF 的 Phase 0–2 只加内部契约、测试与只读的 worker 侧事实，
**不改变任何用户可见行为**；Phase 3 起的产品接入一律在默认关闭的实验开关后面。

**例外登记（维护者 2026-10-20）**：TAF 按 `AGENTS.md`「1.0 收敛纪律」登记为与统一实施包同类的例外，
范围是本 ADR 与 `docs/implementation/ai-foundation/` 的 Phase 3–6（改图接入、有界修正、数据出图、Harness Lab）。
理由与边界：

- 产品接入全部在开关 `ai.taf.enabled` 后面，**默认关闭**；开关关时 `/api/ai/run` 走旧路径、逐字不变，
  `ADAPTERS` 为空（`tests/test_ai_figure_edit.py` 钉住）。
- 不重写稳定模块：会话、历史库、执行、写回、安全档全部复用现有权威（第一节），只加列、加入口、加只读事实。
- 例外不放宽任何门禁：每个阶段仍按风险档评审、跑 CI，新增核心判据提交前手工反证。
- 打开默认开关（让用户默认看到 TAF 行为）不在本例外内，要另行决定。

### 六、Harness² 只借思想

参考 google-research/harness2（Apache-2.0，HEAD `1cfe254e8` 只读审计）：借鉴「评分与证据隔离并用断言守住」、
「失败回退不抛异常且在结果里留痕」、「预算恒等式」、「cue / lesson / evidence 缺一不可的经验条目」、
「确定性 Demo 适配器做端到端测试」。**不**引入它的运行时（在线自修改 harness、OpenCode / Codex 执行器、
Vertex / gcloud、仅 Linux）。若将来逐字抽取代码，按 Apache-2.0 保留版权头与修改声明并在第三方声明登记。

## 不变的东西（看护在各自原处）

PyMuPDF 不回渲染闭包；Flask 父进程不 import 科学栈（TAF 的父进程侧模块纯标准库，数据指纹在 worker 里算）；
不改用户全局 Codex / Claude 配置；会话认证不加旁路；写回事务 prepare → verify → commit 原样；可写数据只走
`data_dir()`；遥测不收脚本 / 路径 / 提示词 / 列名 / 图中文字，新事件攒进下一次 `CONSENT_VERSION` 升版。

## 后果

- 好处：第二种 AI 任务（数据出图）只需实现一个适配器 + 声明验证项；「改对了没有」第一次有了可复核的证据。
- 代价：manifest 多一个只读字段（数据指纹）、`sessions` 多几列；验证要额外的渲染（预算受任务策略约束，
  见实施包 `07-pr-roadmap.md` 的性能一节）。
- 风险与未决：见 `docs/implementation/ai-foundation/08-risk-register.md`；需要维护者决定的三件事列在实施包 README。
