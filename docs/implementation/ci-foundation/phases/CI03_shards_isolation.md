# CI03 · 长测试分片，但先证明资源隔离

依赖CI00；与CI01/02联调。复用当前测试意图，不为了并行而取消端口冲突、取消/退出或真实子进程等难测行为。

## 实际实施

1. 从计时结果得到pytest文件/组与Playwright文件权重，建立小而清楚的serial名单及原因。新测试未知时进入保守组，不因计时文件没它而遗漏。
2. 优先两个独立job分片，每个独立工作空间。相同target收集完整实例集合，分片执行后核验并集。对required资源缺失/全skip/重复报告/遗漏报告做自动负例。
3. pytest-xdist作为可选第二步：只对证明安全的组用固定workers和合适调度方式。session fixture不能假设在所有worker只执行一次；模块全局state不以“每个进程一份”掩盖共享磁盘/进程外资源。
4. Playwright先workers1+两个file shards；保存browser/locale project匹配和排除规则。重文件需要拆时保留其顺序/fixture生命周期。实际编辑、export等fixture仍跑真实被测产物，不用store注入替代。
5. 清理/tmp/smoke、固定端口5199、sleep8及其他可能碰撞点：每run/attempt/job/shard独立目录，动态端口与健康检查；显式测试故意端口冲突场景仍工作。
6. 覆盖取消/异常/进程崩溃的owner清理，退出wait/reap和Windows句柄释放。不能global pkill，不共享coverage、sqlite、registry或MPL缓存。需共享的只读资源有明确锁和信任边界。
7. 设定VM层和job层并发预算：pytest/vitest/Playwright/cargo/BLAS/科学worker的乘积不能超过内存上限。先2并行对比串行，再扩大；记录CPU/RSS/IO而不是盯CPU100%就加进程。
8. 保留旧全量串行运行的短期观察对照，证明新分片和原集合语义等价后撤销重复长期运行。结果合并保存第一次失败和retry；不以覆盖率总数抵消缺失具体实例。

## 验收

至少两条分片实际同时执行，完整合并后的测试集合与合同一致；同机/跨VM两种实际采用方式没有目录/端口/cleanup串扰；至少一个故意漏片与一个碰撞反例失败。原生平台未跑不得借Linux证明。资源预算未达时减少并发，不放宽产品断言。
