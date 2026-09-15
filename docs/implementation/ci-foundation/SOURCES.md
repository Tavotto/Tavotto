# 来源与推断边界

审计日期2026-09-15；仓库源码固定8b95256。GitHub元数据是历史run的真实记录，不是本次执行的新实验。一个成功样本不代表全部PR或性能分布。

用户粘贴的并行runner/三层CI/全局取消建议是本次评审输入；本包明确保留并行和分层方向，修正总资源、当前lab信任、已有并行事实和取消范围。资源分配、阶段和时间目标是建议，不是实测事实。

统一计划引用当前会话的 Tavotto_Unified_Implementation_Full_Prompts.md 与 Start_Here.md，其CI逐级准入保持；源文件hash见SOURCE_MANIFEST.json。本包不复制旧三个方案成为多份新master。

## 仓库与官方资料

- [G01] main指针
  `https://api.github.com/repos/Tavotto/Tavotto/git/ref/heads/main`
- [G02] 成功merge_group样本
  `https://api.github.com/repos/Tavotto/Tavotto/actions/runs/34970490865`
- [G03] backend3.10时间
  `https://api.github.com/repos/Tavotto/Tavotto/actions/runs/34970490865/jobs?per_page=1&page=8`
- [G04] frontend时间
  `https://api.github.com/repos/Tavotto/Tavotto/actions/runs/34970490865/jobs?per_page=1&page=3`
- [G05] Windows时间
  `https://api.github.com/repos/Tavotto/Tavotto/actions/runs/34970490865/jobs?per_page=1&page=16`
- [G06] CI源码
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/.github/workflows/ci.yml`
- [G07] Playwright配置
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/web/playwright.config.ts`
- [G08] 现有lab与网络记录
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/docs/ci/self-hosted-runner.md`
- [G09] lab资格独占/状态
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/.github/workflows/_lab-qualification.yml`
- [G10] 稳定聚合器
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/scripts/ci/aggregate_gate.py`
- [G11] 当前支持矩阵
  `https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/docs/support-matrix.json`
- [W01] GitHub concurrency与pending
  `https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency`
- [W02] GitHub安全与JIT边界
  `https://docs.github.com/en/actions/reference/security/secure-use`
- [W03] GitHub self-hosted routing/lifecycle
  `https://docs.github.com/en/actions/reference/runners/self-hosted-runners`
- [W04] Playwright sharding
  `https://playwright.dev/docs/test-sharding`
- [W05] pytest-xdist调度
  `https://pytest-xdist.readthedocs.io/en/stable/distribution.html`
- [W06] GitHub matrix/max-parallel
  `https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations`
- [W07] Playwright CI与browser缓存
  `https://playwright.dev/docs/ci`

## 核验范围

本轮重新读取CI关键区段、Playwright、lab文档/reusable workflow与样本job。聚合器/支持矩阵还结合当前会话前一次同SHA读取。网络条件、VM容量、组织访问控制与新分片稳定性尚未现场验证。外部官方文档会变化；实施时按实际锁版本/账户能力核验，勿从“官方有功能”推出已部署。
