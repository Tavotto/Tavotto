# CI 前置计划 · 开工指令

将本包放在 `docs/implementation/ci-foundation/`，与统一实施包并列。

```text
请实际执行 Tavotto CI 前置计划的 CI00，然后按依赖推进小型可审查改动。
先读当前仓库/目标目录 AGENTS 与 CLAUDE 规则、检查 HEAD/status，
再读本包 README、00_MASTER_PROMPT、01_AUDIT、02_EVENTS_DAG_GATES，
以及 phases/CI00_baseline.md。

优先验证 backend-fast 与 package/windows-exe-smoke 之间的非必要判定等待，
再处理全量 pytest 和 workers=1 的 Playwright。先测量、隔离和分片，
不要只增加runner或把所有job cancel-in-progress都改成true。

保持三stable Gate、CLA/CodeQL、merge_group完整合并资格和精确发行物验证。
需要重新安排测试时提供旧→新覆盖映射，不能把当前必需保护挪到合并以后。
TypeScript必须是真正检查references的命令，不能空跑tsc。

现有tavotto-lab是可信发行资格环境，不直接承接公开PR。
新VM需要管理员确认实际资源与隔离，先做一个一次性执行槽试点。
没有服务器权限就提供最小管理员交接并保留hosted路由；
不要让尚不存在的runner进入required配置。

本计划只整理CI基础，不开始PyMuPDF替换或自动准备产品功能，
不修改根LICENSE，不覆盖未提交改动，不push/merge/publish，
不修改ruleset、凭据、hypervisor或plugin-stable。

记录真实命令、退出码、run/attempt/SHA、覆盖和计时证据；
ci_hosted_ready后即可进入原统一计划U00，runner_pool_ready独立报告。
```

本包提供的是实施任务，不是已经在仓库运行的CI。2–5分钟和2×不是验收事实。所有验收初始not_run。
