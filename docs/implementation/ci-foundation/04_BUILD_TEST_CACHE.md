# 构建复用、测试分片与性能

## 1. 构建产物和“通过结果”不同

同一SHA/recipe的web资源可构建一次，其他job核验后消费；分平台的native库、二进制和签名仍分别构建。普通web、MCP widget和Playground若构建目标不同，不能把它们互相替代。

producer输出至少包含：实际checkout SHA/tree、目标/OS/arch（适用时）、Node/Python/Rust/包工具版本、构建命令、相关锁hash、文件清单、内容hash。消费者在开始前核验；传输用能保留隐藏文件和模式的已验证archive，复用现有plugin_stage。

不要永久信任文件名`latest-build.zip`；同一PR head SHA与GitHub测试merge SHA也不同。归档是待验证输入，不是信任证明。不以“源码相同”证明重建后的包与待发布字节相同；release资格继续对最终候选。

首次优化可以只解开DAG等待，不马上改所有构建复用；实际样本web build只有22–40秒，传输和校验成本可能吃掉部分收益，按测量决定是否值得跨job抽取。

## 2. pytest先测再并行

保存每个测试的setup/call/teardown耗时、nodeid、所用worker子进程与资源类别。不要只按文件数量等分：一份重放测试可能比数百个纯函数测试都慢。

先按风险分类：
- 独立纯测试，可在有界workers下并行。
- 独立临时目录与真实子进程测试，隔离前提验证后可分片。
- 修改全局环境/模块/固定端口/共享文件的测试，先放serial isolation组或独立job。
- 需要真实插件/完整artifact/特别字体的测试，进入有对应前提的消费者，不能靠importorskip错过。

初期优先按文件或有序组分成2个独立job，使用各自干净工作空间。需要pytest-xdist时显式固定worker数，并根据fixture使用loadfile/loadgroup等策略；这些只保证一次xdist运行内部的组合，不是跨job锁。[W05]

禁止全套`-n auto`：科学测试会再起worker，BLAS还可能多线程，额外并行可能更慢且OOM。每个job有并行预算；例如2个pytest进程×每进程最多科学worker×BLAS线程需能装进VM预算。对测试进程设置OMP/OPENBLAS/MKL线程是测试配置，不能偷偷改产品对用户环境的合同。

分片完整性核验应在相同target条件下比较收集nodeid集合：预期=各shard并集，无未知/遗漏；serial组完整；重复运行如为交叉验证须显式标注，不与遗漏互抵。skip按已声明平台/能力合同处理，不能因为分片缺资源而变成功。

## 3. Playwright先分机器，再扩大单机workers

当前workers1有资源理由，保留为首个试点。用2个独立VM/job文件级shard跑同一平台产物，先验证隔离与完整性，再评估单机workers2。Playwright官方支持sharding，fullyParallel=false时仍可按文件分配，文件过重时需经过测试审查再拆分。[W04]

保留Chromium、WebKit及英文locale各自验证意图。Windows上的WebKit测试不能替代macOS签名.app或WKWebView实际壳验收。

用正常Playwright报告机制合并shard信息，并用准确project/file/test身份核验覆盖。blob/html是诊断；必需shard没到不能生成一份“其他shard都通过”的绿总报告。

CI目前配置失败后retry一次。本轮不要单靠增加retry消掉红灯。记录第一次失败与重试，区分产品确定失败和可解释基础设施故障；改变原retry合同单独审查。新资格不得靠概率性重跑得绿。

## 4. 真隔离条件

每个执行实例有：repo/run_id/run_attempt/job/shard组成的工作目录；独立TAVOTTO_DATA_DIR、CONFIG_DIR、HOME/缓存策略、MPLCONFIGDIR（仅在测试合同允许处）、coverage/JUnit/Playwright输出目录。

端口由系统分配或经过可靠租约提供。用健康检查代替sleep8；不要先找空端口释放再盲目重用而不处理竞争。端口冲突专用用例保留有意冲突，不把产品行为改掉以便并行。

子进程由该job的进程组/cgroup/Job Object或可验证owner记录清理；不能`pkill python`、扫描整个共享临时目录删除、终止另一个job的Tavotto进程。取消后也清理并reap。产物目录只清当前实例；跨attempt同run不撞。

审计原lab_preflight/cleanup/flock的全局假设。可以保持lab串行，不必把lab所有脚本改为分布式；PR新池不要复用整棵lab state root。

## 5. 缓存分四类

| 类别 | 政策 |
|---|---|
| 下载bytes/toolchain/browser/runtime archive | 验证来源/hash/版本，适用平台键；可预热 |
| 编译缓存cargo等 | 绑定toolchain/target/features/锁与信任域；避免无关job共享可写target |
| 构建artifact | 精确SHA+recipe+内容hash；消费前验证，不等价于测试通过 |
| 运行状态/用户配置/live worker/已经准备好的科学环境 | 不用来让冷启动/首开资格走捷径 |

不要把整个venv归档后跨路径迁移。不把依赖cache命中当安装校验成功。当前pytest使用未固定matplotlib的档位需查明是测试latest意图还是漂移；分别定义bundled/minimum/current受控约束，不机械把所有档固定为同一版本后声称兼容覆盖不变。

## 6. 测量口径

四类时间分别记录：
- `dependency_wait`：根据DAG前提可见时点估算，不能用job.started-created替代。
- `runner_wait`：job可调度/创建到start的可观察部分，无法精确分解时标unknown。
- `execution`：setup、install、test、build、artifact步骤分列。
- `feedback/qualification`：本次push或候选创建到对应有效Gate结束，不用全部workflowupdated字段胡乱替代。

样本含失败/取消/重试，不只挑cache热的成功run。比较墙钟时间、job资源分钟、P50/P95（样本够且分组明确时）、timeout/OOM/flake、可用runner数、并行强度和冷暖。独立硬件改变带来的加速与删除测试带来的“加速”必须分开。
