# 接到 Tavotto 统一实施计划，而不是新增第四套判据

## 权威分工

- 本包：现有CI的执行位置、运行资源、分片、缓存、路由与快速反馈基础。
- 统一计划：RenderCore/兼容性产品范围、U00–U11实现次序和新能力准入。
- 既有仓库AGENTS/安全/版权/发布规则：继续有效；要修改必须正常提案和审查，不因提示词内容自动覆盖。

本前置不删除统一计划原来源条目，也不把CI00–05追加成U12–U17。前置完成后U00继续基于实际新HEAD审计产品；不重做已经取得、仍适用的CI调查，不从别的SHA借产品资格。

## 获准实施时对统一计划做的最小编辑

1. README/Start Here加一段：“先执行ci-foundation的CI00；ci_hosted_ready后进入U00，runner_pool_ready可以随后完成。CI00与U00只读清点可并行。”
2. U00引用`CI_HANDOFF.md`，补本轮产品调用图/旧行为，不重复把整个CI重构一遍。
3. 03_CI_POLICY的三Gate和planned/observing/enforced/later保留；新增测试的runner_class、阶段、timeout、资源前提与最小证据由本轮收敛后的统一接口接入。
4. U01不要新造第二个调度器、结果汇总器或case-enrollment数据库；复用实际落地的集合展开/结果验证。尚不存在则由U01实现产品用例管理，本前置不交付空假接口。
5. U02–U11的真实包/FirstOpen/RenderBench消费本次产物与分片机制，避免每个新用例重建前端。两种语料的科学问题仍分开，不合并成一个成功百分比。

## CI_HANDOFF.md 必填

```
source_sha / workflow_policy_revision:
CI实际改动范围:
稳定Gate与其输入来源:
当前PR/ready/full-ci/merge_group/main/nightly/release映射:
覆盖迁移（旧集合→新执行位置）:
构建producer/consumer与artifact身份:
pytest/Playwright分片及serial集合:
已有runtime/toolchain/缓存策略:
runner各信任区、实际资源与未部署项:
真实性能样本及局限:
负例和取消/掉线/回退演练:
ci_hosted_ready: pass|fail|not_run
runner_pool_ready: pass|fail|not_run
需要管理员的最小下一步:
U00应沿用的内容与需重新调查的产品问题:
```

`not_run`不是“永久不用做”。用户需要全套能力时，未完成的新池明确保留待办；但不要让它成为已有hosted上U00无法动工的假依赖。
