# Tavotto FirstOpenBench：CI 实施补充提示词

审计基线：`Tavotto/Tavotto@6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。日期：2026-09-15。

**文件性质：实施规格与测试合同，不是已经实现的 CI，也不是已通过的测试报告。** 当前只重新读取了相关测试、支持矩阵、CI 规则和前一份兼容性实施包；未更改仓库、未运行 Tavotto 跨平台测试。

本文件细化前一份兼容性补充包的 **CP08 / 03_FIRST_OPEN_BENCH**，不是另建一套产品，也不是把原有 CI 推倒重来。CP08 的最终资格依赖 CP00–CP07，但测试基础、基线与反例应从 CP00 开始并行建设，不能等兼容代码写完才设计测试。

## 1. 当前源码实际支持的判断

| 观察 | 来源 | 对本轮的意义 |
|---|---|---|
| `test_workdir_mode` 真 worker 成功路径先调用 `workdir.set_mode(..., MODE_PROJECT)`；另测默认模式看不到 exists/glob 数据 | [S1] | 是明确模式的有效测试，但不能单独证明产品会自动发现或引导首开 |
| `test_project_env` 已测缺包后项目环境接手；轻量真 venv 继承宿主科学栈并增加纯 Python fixture | [S2] | 保留机制测试；另外建不继承宿主的真实跨 minor/ABI 资格 |
| `test_dependency_repair` 已有本地 wheelhouse 真安装、计划授权、隔离、取消等测试意图 | [S3] | 复用，不重新做假的安装器；扩为多包、端到端和干净机器 |
| 仓库已有 CompatBench、四路等价、不变量、精确包冒烟及 nightly 安装链 | [S4] | FirstOpenBench 是补充，不取代任何一层 |
| CI 已按 PR、merge_group/full-ci、main 审计、nightly/lab、release 分层 | [S5] | 继续三个稳定 Gate，不新增绕过通道 |
| 当前声明 Python 3.10–3.14；Windows x64 和 macOS arm64 桌面，Linux pip 模式等地位不同 | [S6] | 以当前支持矩阵驱动；不把 Linux 模拟当作其他平台真实资格 |

## 2. 你必须实现的核心判断

不是“脚本退出 0”或“PNG 存在”，而是：

`正确环境 AND 正确输入 AND 预期授权/自动化路径 AND 未越权副作用 AND 真正可编辑 AND 重放一致 AND 最终产物正确`。

自动化行为必须可观察：需要运行脚本的场景，不应先在错误解释器计算一遍再补救；静态预检不能反复执行用户的长计算。不能假设所有脚本都可静态识别依赖，动态依赖允许明确、有界的补救；是否允许再次执行取决于具体执行模式、副作用与授权，尤其不得静默重跑用户 native 命令。

### 两个独立维度

- `product_outcome`: `automatic_success / guided_success / safe_stop / product_failure / unknown`。
- `test_verdict`: `pass / fail / infrastructure_error / not_run / not_applicable`。

“离线且没缓存 → 正确说明受限”是安全边界测试 pass，但 product_outcome 是 safe_stop，**不能计入自动兼容成功率**。声明应成功的场景现在没实现，即 fail/未取得资格，不能改成 expected_stop 蒙混过关。

`not_applicable` 只用于预先声明的平台/版本约束，不用于“缺了需要的依赖”“没有那个 runner”或“实现没有做”。产品正常拒绝与基础设施故障分开记录，基础设施故障也不得让必需资格变绿。

### 先验状态必须先验证

每次执行先核验：脚本/数据是否与 fixture 相符、目标库是否确实不在内置环境而确实在项目环境、Python minor 是否真的不同、缓存到底冷还是热、网络和文件权限是否真按该场景配置。

先验不成立 = `infrastructure_error/invalid_fixture`，不能退回一个更容易通过的场景。不得用 root 权限下形同虚设的 chmod 测试普通用户写权限；不得给测试驱动完整科学栈再把那条路径传给被测应用当作发现结果。

## 3. 测试驱动允许做什么，不允许做什么

**允许**：构建可复现的假想用户项目；为“本来就有 .venv”场景准备那份用户环境；提供固定 wheelhouse/受控下载服务器；准备干净配置、合成数据和预先声明的信任/授权状态；使用公开产品入口完成与用户一样的点击；由外部观察器记录进程、网络、文件和时间。

**不允许**：在“应自动发现”场景预写 Tavotto 解释器设置；设置 `TAVOTTO_WORKER_PYTHON` 替产品选中答案；在打开前调用私有 repair/remember/set_mode；向科学环境注入测试宿主 site-packages；替缺包场景直接 pip install 目标包；替产品迁移数据；预填 receipt、ready 或缓存结果；直接改 store 伪造 UI 成功。

专门测试“用户显式覆盖”的案例可以通过正式设置/环境变量表达覆盖，但必须标成该案例，不能拿它作为默认自动选择证据。组件单测可以 mock 分支；端到端资格的解释器选择、文件读取、依赖安装、捕获和输出核心不能全部 mock。

准备已获授权的项目与“新陌生项目尚未信任”要分开。探测外部解释器/包可能执行其启动钩子或 import 代码，不得把这种探测伪称纯读取；信任边界遵循项目政策。

## 4. 独立的环境与测试驱动

至少区分以下空间：

```text
测试控制环境（pytest/Playwright）
    │ 公共接口 + OS 观察；不向被测应用提供其 Python/site-packages
    ▼
精确待测应用产物 + 私有应用运行时
    ├─ 干净的用户配置/缓存
    ├─ 项目 A：原有 Python / 科学包（场景需要时才存在）
    ├─ 项目 B：与 A 冲突的环境（并发场景）
    ├─ 实验数据目录：只放合成数据，含同名干扰项
    └─ 产品自己创建的受管 Python / 环境（不是测试驱动预建）
```

“无系统 Python”资格不能只清 PATH：应用还可能从已知目录、用户 site、registry/launcher、Conda 位置、缓存或冻结环境借到它。使用真实干净目标虚拟机或同等可验证的隔离目标；测试驱动在目标之外，或能证明其解释器对产品不可见。不得卸载或删除共享自托管主机上的 Python 来模拟。

产物从非仓库工作目录启动；不能靠 `PYTHONPATH=src`、editable 安装或开发源码弥补 wheel/冻结包遗漏。每次记录实际启动的 app 路径、文件摘要、source SHA、构建 recipe 与平台。

## 5. 一条完整用例的步骤

1. 为场景展开准确的 target OS/arch、app 形态、项目 Python/包集、入口、网络/缓存状态和 expected_actions。
2. 外部验证先验，运行原生参考脚本（仅合成、安全 fixture，在独立干净上下文，不能污染被测产品）。
3. 起未配置的 Tavotto，通过真实 HTTP/桌面/CLI/MCP 入口打开项目。桌面至少有一条 Playwright 路径真正点击必要动作，其余高成本组合可以走相同生产控制路径的 HTTP；不能假造权限 token 或跳过会话认证。
4. 记录用户决策与准备动作。自动模式不允许出现补救决策；guided 模式只允许合同声明的动作和次数。一次授权不得覆盖后来出现的新来源或新副作用。
5. 让实际科学进程自报身份，与 harness 独立已知的真值对照；不只信父进程“计划使用的 Python”。
6. 核对读到的数据、计算结果与图内值，而不仅是截图。对可支持的图类型可通过 manifest/协议读取语义；不足时使用固定 fixture 的公开输出与独立 PDF/图像检查，不为测试新增通用任意执行接口。
7. 用产品真实编辑入口改变一个可见属性，验证 B0→B1 确实发生，未授权科学值/其他结构保持。
8. 通过正常应用拥有会话的重建路径进行冷重放，核对环境、输入、patch 与结果。native 按其所有权/屏障合同处理，不能为了测试强杀并重跑用户命令。
9. 按请求经 RenderCore 导出，核对真实最终文件和实际 receipt/源产物/文件 hash 的关联。
10. 再开、取消、退出；验证没有残留作业子进程、损坏 active 环境、悬挂租约或越权文件变更。

## 6. 32 个核心场景合同

这些是基础场景 ID，展开后的 instance ID 应含平台/解释器/入口/网络变体。不是随意取 32 次成功就完成；每个 lane 在执行前生成预期 instance 集合。


| ID | 场景 | 预期产品行为 | 主要证据 | 深度层 |
|---|---|---|---|---|
| FO01 | 脚本同目录 CSV | automatic：打开脚本后读到预期数值并产生可编辑图；不由 harness 改 cwd。 | 脚本/数据哈希；实际 plotted values；文件读取证据；人工修复次数。 | pr |
| FO02 | 脚本目录与运行根目录不同 | guided：产品给出一次可理解的目录选择，接受用户通过公开流程选择 paper 后成功；不得靠猜测匹配同名文件。 | 实际 cwd；选择前后的权限状态；图内值；脚本不变。 | pr |
| FO03 | __file__ 与模块相对导入 | automatic：不因复制入口到缓存或插入引擎模块改变来源语义。 | __file__、模块 origin 的本地证据；所读数据；输出。 | pr |
| FO04 | 项目外有效绝对路径 | automatic：保留原路径含义并正确读取；不拷贝整个实验目录。 | 外部文件身份、输入值、拷贝字节预算、原件未变。 | pr |
| FO05 | h5py 原生读取 | automatic：原生库真正打开 HDF5 并完成已知数值计算；Python open 补丁成功不算替代。 | 原生扩展 origin/ABI；HDF5 值；最终 plotted values。 | integration |
| FO06 | exists/glob/listdir/read 一致 | guided：各读取入口看到一致的数据；不能让 exists=False 与 open 成功相矛盾。 | 枚举集合、存在性、实际读取结果；禁止重复猜目录执行。 | pr |
| FO07 | 同名干扰数据 | guided：先请求选择正确的数据上下文，之后值必须等于选择的那份；不得就近替换。 | 选择前不发布图；独立真值；各文件哈希；读取来源。 | pr |
| FO08 | 项目移动与外部数据失联 | guided：准确说明失联并允许重新定位；不复用旧绑定假报本次运行成功。 | 绑定修订；旧计划失效；重新定位后输出真值。 | integration |
| FO09 | 中文空格、大小写、跨盘符 | automatic：合法路径按平台语义访问，不把字符串替换或 Linux 模拟当实机资格。 | OS/文件系统证据；解释器 argv；读写集合；产物。 | integration |
| FO10 | 读写权限与受保护原件 | contractual：未经授权不扩大写权限；受支持模式内严格符合已声明的保护范围。不能承诺 Python 守卫等于 OS 沙盒。 | 数据原件哈希/存在性；open/np.save/rename/子进程等操作结果；授权记录。 | integration |
| FO11 | 真实不同 Python minor | automatic：首次用户脚本执行前选对项目解释器，不先用内置跑错一次。 | 子进程自报 version/prefix/executable；首次执行计数；无安装。 | integration |
| FO12 | 宿主 AST 不认识目标合法语法 | automatic：脚本不从列表消失；由目标解析器分析；完整执行后正确捕获。另有真语法错误对照。 | 库存可见性；parse 状态；目标解释器证据；正确/错误样例区别。 | integration |
| FO13 | 真实二进制依赖 ABI 隔离 | automatic：只加载所选环境的二进制依赖，已知计算和图结果正确。 | sys.prefix、包版本/origin、扩展文件 tag/路径；独立数值真值。 | integration |
| FO14 | 项目外命名 Conda 环境 | guided：通过一次明确选择使用该环境；真实启动上下文包含必要条件，不只拼 python 路径。 | Conda prefix；依赖 origin；真实原生加载；原环境未变。 | nightly |
| FO15 | 显式环境与项目约束冲突 | safe_stop：提示冲突并等待用户决定，不静默替换显式选择、升级包或执行错误计算。 | 没有擅自选择 B；无安装/运行副作用；稳定错误码。 | pr |
| FO16 | 不支持的 Python/能力 | safe_stop：拒绝声称图内编辑就绪；允许实际可兑现的静态查看/排版，且标清限制。 | 支持矩阵引用；编辑能力关闭；静态产物仍可用；无偷升级。 | pr |
| FO17 | 同一基础 Python 的两个 venv | automatic：按环境而非 executable realpath 混同；选择目标 venv，缓存/租约不串。 | 各自 prefix、pyvenv.cfg、包 origin；选中身份；租约。 | integration |
| FO18 | 三个以上额外依赖联合准备 | guided：一次明确准备授权后在应用受管环境联合安装并继续；不逐个报缺包让用户重复操作。 | 安装调用及环境身份；解析结果；用户决策数；首次脚本执行次数。 | integration |
| FO19 | 本地实验室模块和重名引擎模块 | automatic：用户 import 命中用户模块；不把未知本地名字当成包名联网安装。 | 模块 origin 与输出 sentinel；网络请求记录；未改源码。 | pr |
| FO20 | markers/extras/所选依赖组 | guided：保留条件和版本语义，只准备所选且适用依赖。 | 实际分发包集合、版本、来源；未选包没有被下载/安装。 | pr |
| FO21 | 依赖约束不可同时满足 | safe_stop：显示冲突且保留旧环境；不能删约束、偷偷升级用户包以成功。 | resolver 冲突证据；active 引用与旧环境哈希；无假 ready。 | pr |
| FO22 | 已安装但原生库无法 import | safe_stop：准确区分缺包与环境损坏；有界退出，不无限安装/重跑。 | 真实 import 错误；错误分类；尝试计数；未改工作环境。 | integration |
| FO23 | 无系统 Python/uv/pip 冷启动 | guided：由产品准备自己的完整 Python 和项目环境；测试驱动不能提供基础解释器。 | 目标系统清单；进程树；下载与安装目标；artifact SHA；编辑结果。 | release |
| FO24 | 离线且受管 runtime/wheels 缓存齐备 | automatic：通过合法缓存完成打开或准备；不访问外部网络。 | 网络隔离外部证据；缓存身份；实际环境和输出；无探网。 | integration |
| FO25 | 离线且无可用缓存 | safe_stop：稳定解释缺少组件，给可用的后续动作；不空转、不把旧图当新图。 | 网络尝试预算；终态；未建立假 ready 环境；静态模式标识。 | pr |
| FO26 | 下载损坏、截断和错误哈希 | safe_stop：拒绝使用损坏 runtime/wheel；旧 active 环境保持有效。 | 哈希失败证据；损坏缓存处理；无执行坏下载；旧环境身份。 | integration |
| FO27 | 准备/运行阶段取消 | safe_stop：取消返回明确终态；不再执行后续用户脚本，不把中间环境标 ready。 | 事件顺序；进程/锁清理；可再次打开；数据与环境差异。 | integration |
| FO28 | 磁盘不足和只读目录 | safe_stop：保留上一代环境与可用产物，错误可解释；不能生成半个成功回执。 | 失败操作；目录/文件完整性；active 引用；再次恢复。 | integration |
| FO29 | 并发项目与活跃 native 会话 | contractual：项目不串状态；不得强杀用户计算或原地修改活跃环境；取消只作用于自己的作业。 | 独立输出；envlease；用户进程存活；退出后租约释放。 | integration |
| FO30 | 预检后输入或环境改变 | contractual：按明示快照/新修订策略重新核验或阻断；不把旧计划回执冒充实际执行。 | 内容身份；旧计划拒绝/显式新修订；receipt 与输入和产物关联。 | integration |
| FO31 | 首开/二开/会话重启不重复准备 | automatic：不重复下载安装已验证环境；按声明策略复用或重建，不能复用用户 native 命令静默重跑。 | 冷/热分列；安装与执行计数；会话 generation；实际变更记录。 | pr |
| FO32 | 真实打开—编辑—重放—导出 | guided：从产品入口准备正确原图，修改单个视觉属性，重放保持语义，再由新 RenderCore 产出可核验文件。 | 所有前序证据；B0/B1；最终 PDF/PNG/TIFF 适用能力；实际执行与产物 hash。 | release |

各场景的初始状态见同名配套 `Tavotto_FirstOpenBench_Cases.json`。JSON 是实施用例目录，不是已经实现的 runner 配置。`contractual` 用例必须在 runner 执行前展开成确切成功或确切停止预期，不能运行后选一个最好看的解释。

### 最重要的组合案例

项目 Python 与应用不同 + 真实 h5py + 项目外 HDF5 + 中文空格路径 + 默认没有 Tavotto 环境设置。

外部 HDF5 的已知数据是 `[2, 4, 8]`；沙盒同名干扰数据是 `[200, 400, 800]`。脚本计算并绘制 `y = 3*x + 1`。真值应是 `[7, 13, 25]`，不能是 `[601, 1201, 2401]`。

场景预先提供足够且已授权的原始上下文时要求 automatic；缺少唯一 cwd/外部定位信息时要求一次明确 guided 动作。测试驱动不得在两者之间偷偷改变条件。

应断言：选对项目环境、读对输入、原件不变、已有环境未被安装升级、脚本首次执行次数符合合同、图可编辑、冷重放仍正确、最终输出是本次 revision。**正确输入检查不能只读产品自报的 data hash；必须核对独立已知值。**

## 7. 矩阵与预算，不做无效全排列

维度：真实 OS/arch、应用 Python、项目 Python、科学栈组合、数据位置、读取方式、依赖状态、缓存/网络、入口、生命周期。

以当前支持矩阵为资格边界；版本与 wheel 闭包来自固定锁定记录，不能挑一个本来无法安装的组合然后以 skip 混过。完整跨维度笛卡尔积通常没有必要：

- 每个基本维度至少有一个对应的确定性实例。
- 高风险组合固定覆盖：Python×二进制依赖；cwd×读取器；外部数据×授权；环境修改×native 租约；取消×active 切换；预检×输入变化；冻结产物×私有 Python 准备。
- 用约束后的 pairwise 补齐低风险交叉，但不能代替上述组合。
- 每次新增 bug，先建立最小 fixture，再增加能代表其交互条件的组合实例。
- 用例目录、平台排除、原因和所有者版本化。扩支持范围要先建真实资格，不写“兼容所有环境”。

每轮并发按资源预算限定；h5py/scipy 等场景不要同时挤满所有进程造成假超时。预热只复用经过验证的下载字节，不复用活环境、用户配置或已有成功 worker。冷/热样本必须分列。

## 8. CI 接入现有 Gate

| 层级 | 内容 | 不能做的事 |
|---|---|---|
| PR 快线 | schema/计划/权限/错误分类、测试器自检；少量真实数据/包/引导案例；一条用户流程 | 不能让所有关键路径都是 mock；不能依赖随机公共网络 |
| merge_group / full-ci | 应用与项目跨 minor；真实原生依赖；Win x64/mac arm64 关键路径及相应 Linux pip 路径；核心集成链 | 不 deferred；不能借错误 SHA 的产物 |
| nightly / lab | 更多 provider、组合、共享环境并发、断点、soak；独立公共下载探活 | 不让未受信代码进入长期实验室宿主；不把公网失败算成功 |
| release | 精确候选 wheel / sidecar / 安装器 / 签名后 app 的干净目标验收；安装升级与 FirstOpen | 不用源码测试代替安装物，不把未测试平台算 supported |
| main 落地审计 | 验证落地身份、证据关联及生成物一致性，遵循原队列政策 | 不为所有场景重复做一轮相同打包 |

可以增加内部 jobs，比如 firstopen-smoke / firstopen-platforms，但它们应汇入现有 `CI fast gate` / `CI integration gate`；保留 `CodeQL gate`。新增 job 后 `needs`、`--required`、workflow 测试三处同步。普通 PR 仍可按原合同对全部重资格 deferred，但 merge_group/full-ci 不允许。

### 两层闭集核验

第一层：既有 aggregate_gate 校验上游 job 完整且成功。

第二层：每个 matrix shard 的报告校验：

- 预期 instance ID 与实际 ID 一一对应；不允许重复、缺失、未知或零场景。
- 绑定当前 source SHA、corpus hash、支持矩阵版本、scenario plan hash、目标产物 hash、OS/arch/runtime 身份。
- 必需阶段缺失/未运行/基础设施失败不能算通过。
- fail-fast 取消后剩余未运行必须报告未取得资格；可选 `fail-fast: false` 保留更多失败证据，但不能因此隐藏红灯。
- expected safe_stop 的合格测试不进入成功兼容分子。
- 日志与快照上传失败是否阻断，按必需证据合同判断；核心证据缺失不能只凭控制台一句 success 放行。

GitHub 的条件跳过 job 可能显示 Success，所以不能直接拿 UI 绿勾替代聚合核验。[W1] merge queue 的资格必须在 `merge_group` 事件触发。[W2]

### 构建与缓存

同一 source SHA、recipe、平台的应用与前端可构建一次供兼容 shards 消费，消费方核验内容 hash 和 provenance。不要为每个数据目录变体重建前端或反复生成 canvas.html；也不要使用“最新成功 artifact”而不校验 SHA。

wheels/runtime 缓存以平台、arch、Python ABI、精确闭包锁和构建/下载来源分键并验证 hash。**不缓存移动 venv 作为可搬运产物**；应用受管环境仍在其最终版本目录创建，再切换 active 引用。结果缓存、环境缓存和下载缓存不能混在一起。

## 9. 固定供应源 + 真实外网，分别验证

合并资格使用受控本地 wheelhouse / 测试 HTTP 服务：已知包版本与完整传递闭包，可重复制造缺包、冲突、HTTP 超时、截断、错 hash。安装仍走真实产品的 resolver/installer；允许替换供应地址，不允许替换产品决策。

本地 wheelhouse 不意味着全球禁网：应用控制通道需要 loopback。对离线场景用目标执行边界限制外部网络并从外部记录；`PIP_NO_INDEX` 只约束包索引访问，不保证某段用户代码不会联网。pip 的 `--no-index` / `--find-links` 与 hash 校验属于供应控制，而不是 OS 网络隔离。[W3][W4]

公共下载源、TLS/代理/证书、完整 Python 下载的实际可达性放到独立 nightly/发布探活。公网失灵不能伪装产品逻辑 bug，也不能给在线部署路径假发资格。离线主验证与公网供应验证都需要各自明确结论。

绝不为绕过 TLS 或代理故障自动关闭证书验证。私有索引只能用合成凭据或受控镜像；不能让测试上传用户包名、实验路径、真实数据或环境变量。

## 10. 反证测试：有意植错必须变红

至少包括：

1. 让 resolver 总选 bundled，FO11/13 失败。
2. 让 discover 吞掉无法解析的脚本，FO12 失败。
3. 让读取器取第一个同名文件，FO07/32 失败。
4. 去掉 markers/extras，FO20 失败。
5. 把用户 venv/site-packages 混入应用解释器，FO13/17 失败。
6. 把未知实验室模块按 PyPI 同名安装，FO19 失败。
7. 不管用户显式选择和权限，FO10/15 失败。
8. 安装后不等验证就 ready，FO26/27/28 失败。
9. 杀掉 native 进程为安装让路，FO29 失败。
10. 返回计划身份而不是实际运行身份，FO30/32 失败。
11. 移除精确安装包所需 runtime/原生组件，FO23/32 失败。
12. 删除一个 shard、报告项或把必需 job skip，聚合器失败。

既可以用定点故障注入，也可在隔离工作树做 mutation。不得把某个 mutation 的修复残留在发布代码里。说明哪个测试为什么变红；仅“mutation 脚本执行了”不算反证。

## 11. 报告：既给开发者，也给产品判断

每个 instance 至少输出：

```text
case_id / instance_id / source_sha / product_artifact_sha256
corpus_hash / plan_hash / os / arch / app_python / user_python
expected_product_outcome / observed_product_outcome / test_verdict
stages[]: {name, expected, observed, verdict, evidence_refs}
user_decisions / script_execution_count / install_attempts / download_bytes
first_correct_figure_ms / first_editable_figure_ms / script_exec_ms
cold_or_warm / network_mode / changes_to_user_files / changes_to_user_env
execution_receipt / source_artifact_hash / final_artifact_hash
errors / unknowns / declared_limitations
```

上述是待实现的数据合同示意，不是当前仓库已有字段。UI 消费结构化状态，不解析 pip 日志文字。

给 PR 输出 Markdown 摘要 + JSON + JUnit；失败附脱敏进程树、选环境链、路径解析过程、实际数值、前后图和必要差异。自动成功率使用事先固定的可成功案例分母，报告同时展示全部要求、guided、safe_stop、failure 和 unknown；不借删分母夸大效果。

默认不自动更新 golden；不要为了跨 renderer 抗锯齿差异只扩大容差。先比科学值、结构和真实属性，再按既有共享像素算法做受控视觉比较。不同 Python/科学栈可能有本身渲染差异，要与同环境原生参考对照，不把旧内置图像当所有环境的唯一真值。

身份日志应按用途脱敏：本地测试调试可以保留临时 fixture 路径；公开报告与产物不带 HOME、凭据、完整 env、真实实验内容。receipt 不包含足够观察信息时保留 unknown，不能凭一个 hash 声称完全可复现。

## 12. 安全的自托管策略

实验室 runner 仅承担受信代码的固定语料、夜间长跑或在一次性隔离目标中执行。未经信任的 PR 不直接进入长期自托管宿主，不把 checkout 代码交给具有发布密钥、实验数据、宿主根目录挂载或 Docker socket 的进程。[W5]

清理临时目录不等于还原受污染主机；执行隔离目标需销毁/还原。案例只用小型合成数据，OOM/磁盘满等故障限制在受控卷/配额和目标 VM 中。无权限模拟与真实 OS 资格必须分别标记。

## 13. 分批实施，不等最后才补 CI

### CP08-A：合同与测试器自检（从 CP00 并行）

读取当前源码和全部相关测试/fixture/工作流；建立“已有有效覆盖 / 自动化缺口 / 真实环境缺口 / 安装物缺口”映射。实现用例 schema、预期实例展开、报告聚合与 anti-empty tests；没有生产准备接口时先真实记录不可达，不造 test-only 自动准备实现。

### CP08-B：最小真实首开集（与 CP02/05/06 并行）

先让 FO01/02/03/04/06/07/15/19 等关键场景通过真实产品入口。旧 `set_mode` 单测继续保留；新增首开测试不预设正确 mode。对已支持能力加入 PR gate，新设计尚未实现的资格保持明确未取得，不伪造绿灯。

### CP08-C：环境/安装真实性（与 CP03/04 并行）

真实跨 minor、二进制依赖、联合依赖、权限/取消/冲突与无系统 Python 目标。复用现有本地 wheelhouse与产品 installer，重型场景进入集成/夜间/发行。

### CP08-D：与 RenderCore 的整链路（与 CP07、R13/R14 并行）

将实际 ExecutionReceipt 与 source/final artifact 关联，运行 FO32 的公开用户路径；先证科学输入和编辑语义，再证最终产物。不得只加 receipt 字段即宣布联调完成。

### CP08-E：发布资格与维护

核验精确候选、所有 required instance、平台/能力声明和供应源；通过后才开启相应默认自动化能力。每个用户反馈先脱敏成 fixture、确认原实现真实复现，再修复并保留回归。不要为了让现有开发不停而永久挂 `continue-on-error`；迁移阶段报告和最终 required gate 的生效条件必须明确。

## 14. 交付与停止条件

交付实际测试代码、场景清单/fixtures、固定依赖闭包、CI DAG 修改、聚合/报告、自检反证和版本化基线。记录实际命令/退出码/目标产物与测试范围，不写笼统“所有测试通过”。

每个阶段输出手工改动列表与运行证据、未验证平台及产品阻塞，保护当前未提交工作，不 push/merge/publish，不修改分支保护。用户需要另行批准的仓库设置操作写成明确待办；不要制造一条新的成功 context 绕过已有规则。

没有实现的产品能力不能靠 CI 帮忙完成。目标不是保证任意程序永远能运行，而是在明确支持范围内证明自动处理正确，边界之外安全说明，并防止已解决的类别再次退化。

---

# 给编码 Agent 的启动提示词

请实际实施这份 FirstOpenBench 规格，作为原 compatibility/CP08 的细化。先读取仓库根和目标目录 AGENTS/CLAUDE、当前 HEAD/status、旧 workdir/project_env/dependency_repair 测试及其 fixtures、support-matrix、现有 CI 聚合器和 workflow。

保持原 CI 分层与测试意图；先提交覆盖映射、场景合同和 harness 自检，再从真实默认首开入口补测。不存在的准备能力标产品缺口，不用测试中的私有 helper 补齐。测试定义必须区分自动成功、必要授权后成功和安全停止；所有真实执行状态初始 not_run。

优先证明：用户 Python 与应用不同；脚本/项目根/外部数据语义；真实 h5py；非内置依赖；本地实验室模块；同名干扰数据；无系统 Python；离线/取消/约束冲突。对完整用户链验证原图正确、实际编辑、冷重放和 RenderCore 最终产物。

不允许用 host site-packages、TAVOTTO_WORKER_PYTHON、预先 set_mode、测试先装好缺失包、假 receipt、旧缓存图、skip、扩容差或自动改 golden 让资格变绿。平台与应用 artifact 身份必须由独立证据核验。

按 CP08-A 至 E 分阶段实际实现，运行正反测试并记录 handoff。未通过的产品能力和安装路径必须明确未取得资格；不要推送、合并、发布或变更分支保护。

---

## 来源索引

以下源码观察均固定在审计 SHA；旧注释中的时长与所有“已验证”声明不被本文件当作本次运行证据。

- [S1] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/tests/test_workdir_mode.py
- [S2] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/tests/test_project_env.py
- [S3] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/tests/test_dependency_repair.py
- [S4] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/.github/AGENTS.md
- [S5] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/.github/workflows/ci.yml
- [S6] https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/docs/support-matrix.json
- [P1] 本会话已有 Tavotto_RenderCore_Compatibility_Addendum.md：CP08 与 03_FIRST_OPEN_BENCH。
- [P2] 用户提供的 RenderCore 原方案：Artifact Manifest / Validator / Publication Proof；该方案不是本次运行结果。
- [W1] https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions
- [W2] https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
- [W3] https://pip.pypa.io/en/latest/cli/pip_install/
- [W4] https://pip.pypa.io/en/latest/topics/secure-installs/
- [W5] https://docs.github.com/en/actions/reference/security/secure-use
