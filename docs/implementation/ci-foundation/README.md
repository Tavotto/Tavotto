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

## 实施状态（2026-09-16）

六阶段全部有交付；**七个 PR 全部 open、一个都没合入 main**；`ci_hosted_ready: pass`（基于 PR full-ci 实测，合入后复核）、`runner_pool_ready: not_run`。
每阶段一份文档 + `evidence/` 一个子目录 + `acceptance.json` 若干条；交接在 [`CI_HANDOFF.md`](CI_HANDOFF.md)。

| 阶段 | PR（stacked，base 是前一个） | 分支 / tip | 文档 | CIP | 一句话 |
|---|---|---|---|---|---|
| CI00 | [#372](https://github.com/Tavotto/Tavotto/pull/372) | `ci/ci00-baseline` `37fb89a1` | [`CI_BASELINE.md`](CI_BASELINE.md) | 001–003 | 86 个 run 的四类时间分解、23 条边分类、pytest / Playwright 计时、覆盖账 28 条；不改 CI 行为 |
| CI01 | [#373](https://github.com/Tavotto/Tavotto/pull/373) | `ci/ci01-dag-edges` `79c5aa38` | [`CI01_EVENTS_AND_DAG.md`](CI01_EVENTS_AND_DAG.md) | 004–010 | 删 backend-fast → 四个重型 job 的 verdict-only 边，留 frontend 短预筛；事件表 + 取消真值表；Gate 闭集不动 |
| CI03a | [#374](https://github.com/Tavotto/Tavotto/pull/374) | `ci/ci03-pytest-shards` `e952fe14` | [`CI03A_PYTEST_SHARDS.md`](CI03A_PYTEST_SHARDS.md) | 016、018–021 | pytest 按文件分 2 片，同进程自验 nodeid 并集（rc 4）；manifest + junit 上传作证据 |
| CI03c | [#375](https://github.com/Tavotto/Tavotto/pull/375) | `ci/ci03c-playwright-shards` `35b912a0` | [`CI03C_PLAYWRIGHT_SHARDS.md`](CI03C_PLAYWRIGHT_SHARDS.md) | 017 | windows-exe-smoke 的 Playwright 按 project 分两台机器，e2e 前自验；两条 Playwright 步 step 级 timeout |
| CI03b | [#376](https://github.com/Tavotto/Tavotto/pull/376) | `ci/ci03b-package-smoke-isolation` `162f54c6` | [`CI03B_PACKAGE_SMOKE_ISOLATION.md`](CI03B_PACKAGE_SMOKE_ISOLATION.md) | 018–019 | package 冒烟按实例隔离：端口租约 + 归属就绪判据 + 进程组终止；macOS getfqdn 停顿查清 |
| CI02 | [#377](https://github.com/Tavotto/Tavotto/pull/377) | `ci/ci02-build-reuse` `bb27bdaf` | [`CI02_BUILD_REUSE.md`](CI02_BUILD_REUSE.md) | 011–015 | 九种 recipe 0 行抽取；缓存四类审计（作用域坏了：合并组 0% 命中）；tsc -b 反证；Windows 去 `--with-deps`（run 证实不需要） |
| CI04 | [#378](https://github.com/Tavotto/Tavotto/pull/378) | `ci/ci04-runner-pilot` `a174eb61` | [`CI04_RUNNER_PILOT.md`](CI04_RUNNER_PILOT.md) + [`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md) | 022–026 | 无部署权限：只读清点 + runner 信任区静态守卫 + 管理员操作表；`runner_pool_ready: not_run` |
| CI05 | 本 PR | `ci/ci05-rollout-handoff` | [`CI05_COMPARISON.md`](CI05_COMPARISON.md) + [`CI_HANDOFF.md`](CI_HANDOFF.md) | 027–030 | 前后对照（10 个 after run + 6 个对照）、CI 侧分片完整性、九条负例的证据类型、四条本机回退演练、覆盖账 24/28 promote、交接 |

**两个状态**（[`CI_HANDOFF.md`](CI_HANDOFF.md) §0）：

- `ci_hosted_ready`: **pass**——无争抢样本上反馈 32.7 → 18.8 min、资格 56.9 → 28.3 min（#374）；加 Playwright 分片后资格 24.8 min（#375）；每档 n = 1–2，不算 p95；
  全部证据是 PR `full-ci` run，合入 main 后用第一个 merge_group run 按 `CI_HANDOFF.md` §12 复核。
- `runner_pool_ready`: **not_run**——没有 VM / runner / 部署权限。

**七条拍板（2026-09-16，用户已逐条拍板，详见 `CI_HANDOFF.md` §13）**：① 合并顺序 #372 → #378 → 本 PR——已授权，执行中；② lab 暴露——迁到私有 ci-infra 仓库（`ADMIN_HANDOFF_RUNNER_POOL.md` F 组，待管理员）；
③ 缓存种子 job——做，**PR 已开**（分支 `ci/cache-seed-on-main`，CI02 §4.1「已实施」）；④ 产品侧 getfqdn——现在修产品，单开分支；⑤ CI01 事件表七条——修 ①③⑥、接受 ②④⑤⑦；⑥ 账户并发上限——已查明 free 计划 20 个并发 job / macOS 5，改推送习惯（`docs/ci/parallel-prs.md`）；⑦ 闲置 runner `tavotto-ci-01-2/-3/-4`——注销，不建池。

四种状态：**源测试通过**（各阶段验证表全 0）/ **服务器部署**（无）/ **代码合并**（无）/ **产品发布**（无）。
