# Tavotto CI 前置整合计划 · v1.0

**先缩短反馈路径，再扩并发容量；保持正确性门禁，不先重建一套 CI 平台。**

这是 `Tavotto_Unified_Implementation_Pack`（U00–U11）的前置计划，编号 CI00–CI05。它负责现有 CI 的耗时诊断、任务依赖、测试分片、构建复用和隔离 runner 试点；不在这里实现 RenderCore、项目自动准备或新增 FirstOpenBench 全部功能。

本文件包是实施提示词，不是已部署 workflow、已注册 runner 或已通过的产品测试。2026-09-15 定向读取 `Tavotto/Tavotto@8b95256c0d08a14bfcfc4c81358894ef01168933` 和一个成功的合并队列 CI 样本。未连接实验室 hypervisor，未验证可用物理资源，未运行 Tavotto 基准或更改仓库。所有实施验收初始为 `not_run`。来源与实测摘录见 [审计](01_AUDIT.md) 和 [来源](SOURCES.md)。

## 开始

读取 [总提示词](00_MASTER_PROMPT.md)、[触发与 Gate](02_EVENTS_DAG_GATES.md)、[资源与信任](03_RUNNERS_AND_TRUST.md)，然后执行 [CI00](phases/CI00_baseline.md)。从 CI01 开始交付小型可审查代码改动，而不是一次替换所有 YAML。

建议存放到仓库 `docs/implementation/ci-foundation/`，与 `docs/implementation/tavotto-foundation/` 并列。前置完成后按 [接入说明](06_UNIFIED_HANDOFF.md) 更新统一计划入口，不再叠加三个旧 master，也不重新编号 U00–U11。

## 六阶段与依赖

| 阶段 | 交付 | 依赖 |
|---|---|---|
| CI00 | 当前 workflow/任务/耗时/覆盖/资源的真实基线 | 无 |
| CI01 | 删除非必要等待边，明确事件和准入合同，保留稳定 Gate | CI00 |
| CI02 | 唯一构建生产者、验证后的产物复用与确定性缓存 | CI00；与 CI01 对齐接口 |
| CI03 | 隔离测试资源、分片长测试、控制嵌套并发 | CI00；与 CI01/02 联调 |
| CI04 | 新隔离 runner 池试点、容量与信任验证 | CI00；并发生产使用需 CI03 |
| CI05 | 影子对照、迁移准入、恢复演练与 U00 交接 | CI01–03；CI04 按实际部署状态交接 |

CI01–04 是可并行工作流，不要求串行完成。`ci_hosted_ready` 达成后即可开始 U00；`runner_pool_ready` 可随后达成。尚未部署的自托管池不得写进 required 路由把整个仓库挂死。CI00 的测量也可与 U00 的只读清点并行。

## 这轮最先做的三件事

1. 实测并解除 `backend-fast → package/windows-exe-smoke` 等仅为等“测试通过”而存在的重型串行边；最终 Gate 仍同时检查两者。
2. 对全量 pytest 与单 worker Playwright 做受控分片；不是到处填 `-n auto` 或 `workers: 16`。
3. 有额外资源时新建 PR 隔离池，保留现有可信 lab 资格环境，不直接给旧 `tavotto-lab` 添加多个 PR runner。

2–5 分钟快速反馈、5–12 分钟代表性验证等只是优化目标，不是预先保证的运行时间或 job timeout。一个不变的 35 分钟测试集，不会因为改名为 fast 就变快。

## 成功的含义

当前支持范围的合并前保护没有被搬到合并后；同一变更拿到反馈和资格的时间确有可复核改善，或者准确交代未达目标及原因；分片无遗漏、产物身份正确、测试前提不被缓存污染、旧 PR 可取消且不会误杀合并/发布链。

本包不附伪造可直接部署的 workflow，也不附会把未配置项当成功的测试器。`templates/` 是实施记录模板，不是生产准入配置。`acceptance.json` 的条目数不是仓库永久 required 检查数量。
