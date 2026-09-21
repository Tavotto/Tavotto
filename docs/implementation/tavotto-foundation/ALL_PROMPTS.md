# Tavotto 统一实施计划 · 完整执行合并版

本文件由包内规范文档生成，替代三个旧master的并行执行。引用路径按ZIP内结构解析；archive旧材料不叠加生效。
阶段及台账为计划，不代表Tavotto产品测试通过。完整原ID追踪在ZIP内registry.json/generated目录。

## 目录

- `README.md`
- `00_MASTER_PROMPT.md`
- `01_SCOPE_AND_DECISIONS.md`
- `02_ROADMAP.md`
- `03_CI_POLICY.md`
- `04_ARCHITECTURE.md`
- `05_TEST_STRATEGY.md`
- `06_ENABLE_AND_RELEASE.md`
- `phases/U00_baseline.md`
- `phases/U01_contracts.md`
- `phases/U02_spikes.md`
- `phases/U03_first_open.md`
- `phases/U04_dependencies.md`
- `phases/U05_private_python.md`
- `phases/U06_render_model_text.md`
- `phases/U07_render_output.md`
- `phases/U08_facade_validation.md`
- `phases/U09_join.md`
- `phases/U10_cutover.md`
- `phases/U11_qualification.md`
- `extensions/X01_extended.md`
- `extensions/X02_manuscript.md`
- `extensions/X03_svg.md`
- `07_HANDOFF.md`
- `SOURCES.md`

---

<!-- 包内来源：README.md -->

# Tavotto 统一实施计划 · v1.0

**一份执行入口，两条实现主线，三类验证，逐级生效的门禁。**

本包正式整合 RenderCore、首次打开兼容性、FirstOpenBench CI 三份方案。它不是第四份追加任务书；从这里开始实施时，以本包为统一任务约定，旧包只作为只读来源和技术细节参考，不再把三个旧总提示词叠加执行。

**性质：重构实施规格，不是已实现代码或产品验证报告。** 用户尚未开始重构。本次只核对原方案、最新 main 指针及部分 CI/支持配置，并对本包结构进行程序校验；没有执行 Tavotto 的重构、测试、构建或发布。

详细原审计：`6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。本次采样 main：`8b95256c0d08a14bfcfc4c81358894ef01168933`。二者不同；最新提交没有被全面重新审计，U00 必须以实施时的实际 checkout 更新差异和基线。

## 前置：ci-foundation 已完成（2026-09-16）

先执行 ci-foundation 的 CI00；`ci_hosted_ready` 后进入 U00，`runner_pool_ready` 可以随后完成。CI00 与 U00 只读清点可并行。
——2026-09-16 状态：`ci_hosted_ready: pass`（基于七个 PR 的 full-ci 实测，合入 main 后用第一个 merge_group run 复核，见 [`../ci-foundation/CI_HANDOFF.md`](../ci-foundation/CI_HANDOFF.md) §12）；`runner_pool_ready: not_run`（无部署权限，管理员操作表见 [`../ci-foundation/ADMIN_HANDOFF_RUNNER_POOL.md`](../ci-foundation/ADMIN_HANDOFF_RUNNER_POOL.md)）。U00 引用 `CI_HANDOFF.md`，不重做 CI 调查，不从别的 SHA 借产品资格。

U00 已于 2026-09-20 执行（`plan.json` 里 `implementation_status: done`，产品资格仍 `not_run`）：产出见
[`U00_BASELINE.md`](U00_BASELINE.md)、[`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md)、
[`U00_CAPABILITY_INVENTORY.md`](U00_CAPABILITY_INVENTORY.md)、[`handoffs/U00_baseline.md`](handoffs/U00_baseline.md)。
每个阶段的交接放 `handoffs/`。

U01 已于 2026-09-20 执行（`implementation_status: done`，产品资格仍 `not_run`）：共同合同（ADR 0053）、
异步准备接口、case enrollment 台账（[`enrollment.json`](enrollment.json) / [`ENROLLMENT.md`](ENROLLMENT.md)）、
闭集校验器与 `invariants` job 的三步落点；唯一 enforced 的切片 `U01-S1` 经真实 HTTP 入口走完首开 → 导出
（旧后端终点）。交接见 [`handoffs/U01_contracts.md`](handoffs/U01_contracts.md)。

U03 已于 2026-09-20 执行（`implementation_status: done`——PR A 后端编排 + 场景用例，PR B 确认交互（前端对话框 /
三档设置 / i18n / MCP 投影）；产品资格仍 `not_run`）：解释器选择前移（项目 venv 首开发现 + 体检 + 记住，显式选择失效不静默替换）、
cwd 三分的生产者（`project_root` 第三档 + 首开按静态证据问一次）、safe worker 经 `bridgeboot` 私有包（#447）、
静态扫描的问题分类与目标解释器解析（ADR 0057）。六条首开场景经真实 HTTP 入口提到 `enforced`
（FO01 / FO02 / FO03 / FO07 / FO15 / FO19），四条 `observing`。交接见 [`handoffs/U03_first_open.md`](handoffs/U03_first_open.md)。

U04 已于 2026-09-21 执行（`implementation_status: done`——三个叠栈 PR：A 无损解析 + import 分类 + 联合计划，B 受管环境按代的
事务与联合安装，C 跑前的门 + HTTP / MCP / 前端一次授权 + 场景；产品资格仍 `not_run`）：依赖声明的无损读法交给 `packaging`
（PEP 508 / 440、有界 `-r` / `-c`、PEP 723 / 735、`unsupported` 闭集）、按 import 上下文分类「需要」、按目标解释器求 marker 的
联合计划；受管环境按代（最终目录里建、验完切 active、旧代留到没人用）、一个事务四条路；起会话前的依赖门（一直问到有答案：授权或
明确 skip）、`/api/engine/dependencies/*`、`DependencyPrepareDialog`、`tavotto_open_figure(prepare_dependencies=)`。ADR 0061
（安装器裁决：pip 留在 U04，uv 经同一事务在 U05 接入）。FO20 / FO21 / FO22 / FO27 / FO31 经真实 HTTP 入口提到 `enforced`
（目标 = 项目自带 venv 变体），FO18 / FO05 `observing`（nightly，联网）。交接见
[`handoffs/U04_dependencies.md`](handoffs/U04_dependencies.md)。

U05 于 2026-09-21 开始执行（`implementation_status: in_progress`；产品资格仍 `not_run`，五个目标 `enabled` 全 false）：PR A
把受管环境**基础解释器的来源**补上——包内锁文件 `resources/private_python_lock.json`（pbs install_only，两个 macOS 目标与
`runtime-lock.json` 同源）+ `engine/privatepython.py`（按内容命名的不可变目录、校验先于一切执行、离线三档、并发去重、按消费者
取消、有代记着就不退役），接进 U04 的代事务（计划上明示 `private_python` 载荷才下载；安装器仍是 pip，uv 不进产品）；ADR 0063。
PR C 接了目标验证腿（`private-python-targets.yml`：三平台真 pbs 经产品代码走完整链、Linux 空镜像、Windows 注册表快照；ADR 0064
把证据分成机制 / 工程 / 目标三档，FO23 → observing）；PR B 接了入口（干净机器上以私有 Python 为目标算第一份计划——替身事实、
供应后重算；一次授权里把「先下载 N MB」说出口；FO24 / FO25 / FO26 经产品 HTTP 入口 → enforced）。`implementation_status: done`；
**无系统 Python 的资格仍未取得**（五个目标 `enabled` 全 false，第三档在 U11）。交接见
[`handoffs/U05_private_python.md`](handoffs/U05_private_python.md)。

## 从哪里开始

先读 [执行总提示词](00_MASTER_PROMPT.md)、[范围与修改决策](01_SCOPE_AND_DECISIONS.md)、[路线图](02_ROADMAP.md) 和 [CI 生效政策](03_CI_POLICY.md)。随后只执行 [U00](phases/U00_baseline.md)，不要在第一步删除 PyMuPDF、更换根许可证或一次启用全部兼容门禁。

以后每次会话读取总提示词、当前阶段、上一阶段交接即可。详细合同见 [职责与数据边界](04_ARCHITECTURE.md)、[测试与判据](05_TEST_STRATEGY.md)、[启用/切换/发布](06_ENABLE_AND_RELEASE.md)。

## 核心完成意味着什么

在现有已承诺能力不被悄悄削弱的前提下，完成 PyMuPDF 发行依赖替换；解决受支持 Python 的项目环境选择、正确数据上下文、额外依赖联合准备及无系统 Python 的私有运行环境路径；从正常入口完成原图、编辑、重放和最终导出，并有真实的分平台证据。

不是让任意 Python / PDF / 字体 / GPU / 网络文件系统都能无感兼容。无法推断的科学意图需要最少必要确认；未实现的范围不能称通过。

## 三类清单不能混成测试总数

`registry.json` 完整保留 **114 条 RC 原要求、74 条兼容原要求和 32 个首开场景**，共 220 个来源条目，其中 188 条是要求、32 条是场景，不等于 220 条互不重复的测试。每项注明统一负责人阶段、解释/拆分/后置决策、生效规则；原文不丢。所有产品执行状态初始仍为 `not_run`。

[生成的追踪表](generated/TRACEABILITY.md) 与 [首开场景编排](generated/FIRST_OPEN_SCHEDULE.md) 由 JSON 派生，不手工维护第二套事实。

## 可复制的开工指令

```text
请在 Tavotto 仓库实际执行本包 U00，之后按本包依赖推进。
本包是 RenderCore、compatibility、FirstOpenBench 的统一实施入口；
不要叠加三个旧包的总提示词，不要第一步执行全部最终验收。

先读当前仓库/目标目录 AGENTS 与 CLAUDE 规则，检查 HEAD 和工作区。
读取 00_MASTER_PROMPT.md、01_SCOPE_AND_DECISIONS.md、03_CI_POLICY.md，
再读 phases/U00_baseline.md。

建立真实基线、来源映射和最小 fixture；不切默认、不移除旧依赖、
不改 LICENSE、不改保护规则，不覆盖我的未提交改动。
执行到哪里就如实记录代码、命令、退出码、证据和 not_run。
每个小切片有对应测试，合并资格、默认启用资格和发行资格分开。
不推送、不合并、不发布，也不自动更新 plugin-stable。
```

## 包内检查

```bash
python tools/validate_plan.py .
python -m unittest discover -s tools -p 'test_*.py' -v
```

这两条只检查本任务书的来源、依赖图和映射，**不是 Tavotto CI，更不能证明兼容性或 PDF 渲染通过**。不要把本包固定12阶段/220来源的自检数量照搬成仓库永久required检查；实际产品runner与准入表在U01按当前代码建设。产品命令在U00确认，后续由各阶段新增。

仓库落点：`docs/implementation/tavotto-foundation/`（U00 于 2026-09-20 入库；`tools/*.py` 只做了 ruff 的 import 排序与格式化，校验逻辑一字未改）。


---

<!-- 包内来源：00_MASTER_PROMPT.md -->

# 执行总提示词：Tavotto 可靠首开与 RenderCore 统一工程

你在 Tavotto 真实 checkout 中编码。本工程同时完成“可靠输入”和“可靠输出”，不是拼接三份旧任务书。正式执行依据是本文件、当前阶段和本包的范围/CI政策。`archive/` 是历史材料，不具有叠加后的更高约束力。

## 1. 工作方法

先检查 HEAD、工作区、相关 AGENTS/CLAUDE 规则。用户的显式要求与仓库安全规则不能被本包绕过；若既有规则与必要改造冲突，先写明 ADR 和具体规则/测试的修订，不偷偷选最宽或把所有“必须”机械叠加。

一次实现一个可审查的小切片。阶段不是一个超大 PR：每个阶段可由几个小 PR 完成；在现有规则允许时由用户安排合并。保留未提交改动，不自动 reset/checkout、push、merge、release 或修改 branch protection。不得伪造 CI、签名或 review 证据。

## 2. 架构底线

- ProjectPreparation 编排环境、完整依赖意图、cwd/数据绑定、必要授权，消费现有 ExecutionSpec，不放进 RenderCore。
- 科学 worker/native Figure 层继续负责脚本执行、捕获、图内编辑和科学重放。原 native 进程归属与屏障合同保持；不得为诊断、安装或导出擅自重跑/终止用户计算。
- RenderCore 只消费冻结的源图产物、画布语义、字体与导出规范，负责合成、栅格化和可兑现的最终文件检查。保持八项基础设施的实用最小实现：IR、Typography、Capabilities、RenderPlan、Manifest、Fingerprint、Semantic IDs、Trace。
- 保留 ExportRequest/ExportJob、originalspec、normalize、preflight、envlease、原安全/更新/CLA gate 的权威。不要另建一套 PublicationJob、全局解释器选择链、安装锁或通用插件框架。

## 3. 必须真的保住的行为

不混合不同解释器 site-packages；不默认升级用户包或污染 bundled runtime；不猜同名数据、不改科学代码或参数；真实目录运行有真实写入含义，必要时先确认；Python 补丁/软链接不是 OS 安全沙箱。

原图与画布导出语义分开，原 PNG byte copy、原位图 native grid、worker SVG/EPS 直出、写回事务、部分成功、取消提交点、会话鉴权和旧客户端兼容保留。单纯静态源可用时，不强制先运行脚本或安装科学环境。

字体来源合法、字形与文字映射正确。不得用文字轮廓化冒充可检索嵌入，或整页位图冒充矢量。不能因换成独立默认字体就要求它与旧内建字体逐像素相同；按批准迁移策略检查布局变化，见 03/05。

同图多实例、跨项目并行与多格式不能串源/串修订。新源失败不能拿旧缓存假装本次执行成功；主动选择使用已有静态产物是另一条明确产品路径。

## 4. CI 不把“未来目标”当“今天回归”

已有门禁照常执行；新小切片的单测/短真实链路随代码进入既有 job。完整新能力的资格从 planned → observing → enforced 按合同提升。只对已纳入当前 lane 的必需实例执行严格闭集核验。

不在 U00 要求全部 220 来源条目通过，不在渲染模型 PR 要求无系统 Python 的签名安装器，不在旧后端仍为默认时全仓禁止 import pymupdf。初期仅禁止新核心偷依赖旧后端；旧桥和旧测试在隔离的迁移路径中继续有效，原断言意图逐条迁移。

修改 gate 的时机、范围与适用前提，不放松 required job 缺失/跳过即失败的判定。失败、未执行、基础设施失败都不能伪装 pass。观察中的失败保留，但不作为无关阶段的 required context；影响已公开能力、安全或数据正确性的实际回归不能借 observation 逃避。

## 5. 范围和工程取舍

本轮必须包含无系统 Python 的私有环境准备，但其真实干净机器资格在启用相应能力/发行时取得；不能让它阻止已有环境路径先交付。联合依赖可先用已有 base Python 建环境，再接 provisioner。

先实现受控的标准 Python/venv + PEP 508 声明路径，遇未知锁格式不丢约束。更广的命名 Conda 自动发现、Poetry/pixi/hatch 完整管理器集成、任意绝对路径重映射等放 X01，已能工作的显式解释器/native 入口不退化。拆分原因和旧条目见 registry。

普通导出仍可以交付合法文件并注明“部分项目未核验”；严格规范要求的未知项不许标成已通过，按该规范决定阻断。始终阻断错误尺寸/损坏输出等核心失败；不以“有一个 unknown”为由封死所有正常导出。

## 6. 证据与收口

每阶段记录实际 SHA、变更、命令/退出码、平台、fixture/产物身份、未验证项、已知缺陷与下一阶段。测试 harness 不能替产品选择环境、安装缺包或提前设 cwd；fixtures 可构造“用户本来已有”的真实状态。

本包的计划校验器只验证任务书，不是可直接替代产品的 runner。把代码存在、源码测试通过、某平台默认可启用、精确发行包合格、实际已发布分开记录。

根 LICENSE 保留 AGPL-3.0-only；不在本工程制作付费墙/账号/闭源版。新依赖和字体的许可及分发义务按实际候选核验，不能凭去掉 PyMuPDF 就宣布法律资格完成。


---

<!-- 包内来源：01_SCOPE_AND_DECISIONS.md -->

# 范围、取舍与相对三个原包的明确修订

本文件是本次重新审查提出的决定，不冒充原方案已经这样规定。原包总体方向保留，原本也有阶段化和不得伪造通过的要求；本次解决的是叠加之后边界/生效时机不够唯一的问题。

## 1. 核心交付与后续交付

| 层 | 本轮核心 | 后续、不作为本轮切换前置 |
|---|---|---|
| 环境兼容 | 受支持 Python 的已有环境选择、目标解析器、标准依赖声明联合准备、私有完整 Python、明确 cwd/数据绑定、正常 UI/HTTP/MCP 路径 | X01：更多管理器完整适配、旧 Python 扩展、任意网络存储/硬编码绝对路径迁移、强只读工作区 |
| 科学编辑 | 现有 capture、manifest、override、native、四路重放不回归 | 不重写科学计算引擎，不做 GPU/驱动自动安装 |
| RenderCore | 八项基础设施的真实最小实现；现有原图/画布/标注写回全覆盖；批准字体、矢量变换、PNG/TIFF 同源、有限产物检查 | X02：Manuscript 批量；X03：新增画布 SVG；完整 PDF 逆向编辑、PDF/X/完整印前另立需求 |
| 可信证据 | 真执行回执、源产物和最终文件关联，unknown 有解释 | 对任意原生 I/O/数据库/远端数据的全量取证、所有平台字节完全一致 |

这些边界不是“缺哪个测试就删哪项”。已公开能力必须先在 U00 清点；已有能力即使属于后续类别也必须保住。X01 是明确缩小**新增自动管理器支持**的首轮范围，不是关闭用户原来可用的 Conda/native 运行。

## 2. 修改记录（D01–D16）

| ID | 原包潜在叠加问题 | 本次正式裁决 |
|---|---|---|
| D01 | 三个 master、重复台账和 CI 章节可能分别变更 | 一份 master、一个 registry、一套 stage DAG；旧材料只读归档，生成表不另写 |
| D02 | 全部最终 must 可能被提前接到 PR | 需求与测试准入分离：planned/observing 不代表通过，也不阻断无关小切片；启用时全部该能力 required 必须真实合格 |
| D03 | 迁移前期全仓无 PyMuPDF 与旧正式代码/测试冲突 | 先限制新核心不借旧库；U10 原测试判据迁移后才启用目标发行闭包“零 PyMuPDF” |
| D04 | 初期选型需要所有最终安装物，尤其签名资格 | U02 分渲染与 provisioner 两个小技术证明；平台候选逐步取得；最终签名/公证后资格只在受信发行候选，不要求每个 PR |
| D05 | CP04 联合依赖严格依赖 CP03 全部私有 Python 完成 | U04 可使用合格已有 base，先真联合安装；U05 只补无系统 Python 的 base 来源，不造第二套安装器 |
| D06 | CP08 放最后或 FO32 必须等待整个 RenderCore | harness 与最小用户链在 U01/U03 建，先通过旧后端导出；U09 再对新后端整链路，两个观测结果不能混称同资格 |
| D07 | 不同默认字体仍被要求旧像素/旧字体名称不变 | 批准字体更换与度量变化；保留位置/内容/框尺寸及无静默重排，审查换行变化；更新实现特定断言，保留用户行为断言 |
| D08 | 任意 PDF 中字体/裁切/数据观察未知就无文件可交付 | 普通导出检查文件完整性与核心事实；可选检查 unknown 显示未核验。严格用户规范的必需未知不标 verified，按政策阻断 |
| D09 | 要求不同 Python/科学栈均等于同一旧内置渲染图 | 每套科学环境对拍该环境的原生参考；科学值先验明确，跨栈差异单列，不能借图像容差掩盖错误数值 |
| D10 | “用户环境一个字节不变”误伤 .pyc/字体缓存 | 对源码/数据/依赖包/配置的未授权变更保持严格；合法且预声明的运行缓存单独记录白名单，不整棵 venv byte-hash 一刀切 |
| D11 | 取消后立刻零字节/零进程与实际提交点竞争 | 定义取消接受时刻和边界；停止后续步骤、有限清理，不承诺撤销已授权的外部副作用；提交点后按既有语义拒取消 |
| D12 | 32 场景 × 所有平台/版本/格式全面排列 | 固定关键交互 + 有约束 pairwise；核心 PR 少量真实集，平台/安装/压力在相应层；每 lane 预先固定实例集合 |
| D13 | 简单图也要求所有原生I/O读写证据与全环境扫描 | 按风险最小证据：已知值+真实身份+原件检查；高风险场景才加 OS 观察/VM。未知观察不冒充完整复现 |
| D14 | 更多 Conda/Poetry 完整解析与三项首开核心绑死 | FO-019/FO14 完整命名环境适配后置 X01；FO-029 的标准依赖语义核心保留，完整未验证锁适配拆出，未知约束必须明确停止 |
| D15 | 黑白名单可能把用户脚本自己的 fitz 也一并禁掉 | “无 PyMuPDF”量的是 Tavotto 目标发行/应用闭包。用户自行拥有的科学环境和 Git 历史/文档提及另分来源，不假称有其再许可权 |
| D16 | CI 不稳只能无限重试或永久 continue-on-error | 分类诊断、复现、受限基础设施重跑、版本化观察/替代判据；不得自动忽略产品断言或已启用核心安全回归 |

## 3. 明确不降低的底线

错误数据、混错 Python ABI、未经授权的环境/文件变更、失去鉴权、伪造 ready/proof、不可恢复原件损坏、隐式旧后端 fallback、required 结果缺失都仍是失败。切片没有实现的功能不得开放给用户并依赖“观察模式”免责。

旧测试分两类：**用户合同**不能无声改变；**旧实现细节**可在迁移 ADR 中替换为新实现独立证据。举例：保护未修改图的数据是用户合同；默认新合法字体仍必须叫 Times-Roman 是实现细节。不能把所有旧断言称为细节，也不能全部逐字冻结。

## 4. 关于“没有永远红”的真实含义

本包让红灯有明确责任和可修路径，不承诺永不红。已存在的基线红灯先复现/定位并修复；不能通过本包自动豁免现有 required gate。未实现新能力可继续开发，但其启用或最终核心交付保持未取得资格。用户可在某平台延后新能力，必须显式说明；不能报告全平台成功。


---

<!-- 包内来源：02_ROADMAP.md -->

# 统一路线图与可并行边界

阶段数减少的是管理边界，不是声称工程工作量缩水。12个核心阶段可拆多个小PR；详细技术要求沿registry溯源，没有删掉原有功能再称完成。

```text
U00 基线 → U01 合同/测试骨架
                  ├─ U02.render_spike → U06 IR/文字 → U07 合成/栅格 → U08 全门面/检查
                  ├─ U02.runtime_spike ──────────────────┐
                  └─ U03 已有环境/数据 → U04 联合依赖 → U05 私有Python
                                                       │
             U03 + U08 → U09.existing_env_join → U10 切换/退役
             U04 + U05 + U08 → U09.managed_env_join ───┐
                                      U10 + 两个联调出口
                                             ↓
                                       U11 综合发行资格
```

U02两个milestone独立，某一方向困难不锁死另一个。U03不等U02。U04有合格已有base即可真安装；U05复用它，不重建安装服务。U09以前已经有多个真实垂直切片，不等这里才接UI/测试。U09也有两个独立出口：已有环境链通过即可推进PDF切换，私有环境链留到综合发行一并收口。

| 阶段 | 可验收成果 | 合并与激活边界 |
|---|---|---|
| [U00](phases/U00_baseline.md) | 基线、差异与核心范围 | 真实记录旧问题，不要求未来能力绿 |
| [U01](phases/U01_contracts.md) | 共同合同与增量测试骨架 | 只门禁本切片schema/harness/已有路径 |
| [U02](phases/U02_spikes.md) | 两条独立技术验证 | 小技术证明；不是全部最终签名资格 |
| [U03](phases/U03_first_open.md) | 已有环境与数据上下文的首开闭环 | 已有环境/目录可独立启用，仍用旧输出 |
| [U04](phases/U04_dependencies.md) | 标准联合依赖与版本化受管环境 | 已有base下联合依赖真实通过即可 |
| [U05](phases/U05_private_python.md) | 私有完整Python与无系统Python | 启用无系统Python前需目标真实资格 |
| [U06](phases/U06_render_model_text.md) | Render IR与真实字体文字切片 | 新核心独立；旧默认/旧测试继续运行 |
| [U07](phases/U07_render_output.md) | 矢量合成与受控栅格运行时 | 候选合成/栅格可测，未全入口不切默认 |
| [U08](phases/U08_facade_validation.md) | 全入口迁移与有限产物验证 | 全入口候选parity，普通unknown不滥阻断 |
| [U09](phases/U09_join.md) | 实际执行回执与两主线联调 | 两主线实际整链，最小证据足够且可信 |
| [U10](phases/U10_cutover.md) | 候选启用与旧依赖退役 | 默认/依赖退役与扫描同时受验 |
| [U11](phases/U11_qualification.md) | 精确发行资格与价值收口 | 最终安装字节和平台资格，不自动发布 |

## 资源和冲突域

单人配合多个编码Agent时，优先限制同时在做的重型切片为两条主线各一项；这只是协作建议，不是产品CI硬阈值。`app.py`、`execspec.py`、前端api类型、packaging、CI gate、依赖锁属于共享冲突域；指定一个集成人，其他切片通过稳定小接口协作，避免并行改同一堆文件。

前端/sidecar按相同SHA+recipe+目标构建一次供shard复用；不能使用“最新成功包”。两条线各自产出最小端到端切片和handoff，减少最后集中集成。

## 后续

X01可以按需求在U03/U04之后独立做某个provider，但不成为U06或U10强制前置。X02/X03等核心资格成立后再推进。原方案的后续功能保留，不让第一版变成环境管理器、字体编辑器、PDF通用引擎和CI平台四个项目一起从零重写。


---

<!-- 包内来源：03_CI_POLICY.md -->

# CI 政策：严格判断已生效合同，不提前阻塞尚未实现的目标

## 1. 四个判定问题分开

1. **此小改动能否合并？** 既有 required gate、当前激活集合及本切片测试。
2. **新能力能否默认启用？** 该能力×平台×执行模式的资格集合，必须真实通过。
3. **能否删除旧运行依赖？** 所有 facade/消费者/测试判据已迁移，候选新路径独立可用。
4. **这个安装包能否发行？** 精确候选字节、目标渠道、签名后运行与许可/资源资格。

没有第4项，不等于不能合并第1项。第1项绿，也绝不等于第4项通过。

## 2. 保留已有三个 Gate

`CI fast gate`、`CI integration gate`、`CodeQL gate` 名称和既有失败语义保留；CLA/安全/更新链也不削弱。新增 job 前先把测试接到合适的已有步骤，确认值得独立拆 job 后同步 needs/required/事件测试。

required job failure/cancelled/skipped/missing/unknown仍失败。普通PR完整重资格可按现有合同整体deferred；merge_group/full-ci不可。不得修改aggregate_gate去理解“这个必需任务还没实现所以算通过”。

GitHub会把部分skip/neutral视为可接受状态，依赖失败也可能跳过下游，因此真实闭集聚合仍然必要。[W1]

## 3. 准入状态是工程事实，不是测试成绩

| 状态 | 含义 | 所在位置 |
|---|---|---|
| planned | 目标与场景已记录，产品或测试尚未齐备 | 台账，不进入当前必需实例集合，不声称通过 |
| observing | 实际跑候选、保留真实失败/unknown，判据或fixture仍在校准 | 非合并必需的手动/夜间观察任务，不反复给普通PR挂全套未完成检查 |
| enforced | 预期明确、实现和真实正反用例成立、预算可控 | 对应PR/集成/启用/发行lane严格阻断 |
| later | 明确的后续扩展，原ID与说明保留 | 不能进入本轮成功分子，也不能宣称已实现 |

**关键区别：**“不在当前门禁”不等于skip后算通过；它是预先版本化的未取得资格目标。基础台账初始全部not_run，只说明还没测。

既有已启用能力的回归不可重新标observing逃避。试验功能即使默认关闭，其可访问接口上的鉴权、原件保护及共享运行路径回归也必须挡住。

## 4. 提升为 enforced 的条件

每个能力/平台明确：用户预期、合法输入范围、fixture先验、测试命令、所需产物、必需证据、负例、维护责任和资源预算。真实冷/热重复观测稳定，并证明至少一个有代表性错误会触发失败。不是以“连续N次碰巧绿”替代负例与设计审查。

提升 PR 同时带：实现/测试、启用开关或默认策略、case enrollment、对支持范围和旧行为的说明。U00定义已有active集合；后续选择器不得依据“包没装/函数不存在/runner没来”临时从集合删除项。

最终核心完成需要所有核心合同在适用能力上有证据；个别后续扩展可以later。未取得某平台资格则不在该平台启用新增能力，必须明确记录，不能把用户的全平台目标默默缩小。

## 5. 合并与矩阵选择

普通PR：已有门禁 + 当前小切片契约与已激活短真实场景。最终建议常驻约6–8个代表性首开/输出场景，这只是预算起点，U00按实际时间和覆盖决定；不要机械把32个都放快线。

merge_group/full-ci：既有完整资格 + 已激活的关键跨minor/原生库/平台路径与候选集成。选择由确定的合同与变更域决定，未知变更走保守广覆盖。不为每个输入路径变体重新构建同一前端/包。

nightly/lab：更广组合、故障与长跑、provider扩展、观察集、单独公网供应探活。

release：实际待发wheel/sidecar/NSIS/签名公证后app，no-system-Python等能力对应的干净目标实测。最终签名不前置到不可信PR。

必须验证：预期实例集合==已提交有效结果集合，无重复、缺失、错SHA或错产物；缺指定runner是infra_error/not_run，不是not_applicable。not_applicable仅能来自事前支持矩阵的真实不适用。

如果某源码变更可能让额外case失效，选例不可只看现有列表；U00建立保守变更域，能力跨域修改触发组合测试。门禁策略的降级须显式审查；不得让候选分支自己删required集合逃过基线合同。

## 6. 三类容差

**必须精确**：授权/项目/会话身份、已知合成科学值（相应数值算法的预先容差）、原PNG复制字节、同一规范RasterBuffer生成的PNG/TIFF解码像素（通道/alpha语义一致后）、未授权源码/实验数据变更。

**需要校准**：跨renderer抗锯齿、不同平台的合法浮点/字体渲染、bbox微小误差。先检查几何与文本/数据，再比较固定读取栈图像；对可接受差异按case/版本记录阈值，不全局放大，不自动位移对齐藏布局错误。

**初期观察**：p50/p95耗时、RSS、包体、CI耗时、视觉指标细微波动。与硬资源上限/超时恢复不同；后者保护系统必须测试。不能事先要求“比旧版快30%”。

字体替换后的旧pixel golden需一次批准迁移。旧字体名、baseline和新字体文件不可同时构成互斥硬断言。真实缺字、文字丢失、越界、错误颜色/alpha依然失败。

## 7. 红灯处置，不靠无限重跑

| 原因 | 正确动作 | 不允许 |
|---|---|---|
| 产品断言失败 | 保存最小fixture、定位改动、修代码/正确合同；重跑相关+上层组合 | 重跑到偶然绿、吞异常、把成功期望改为safe_stop |
| 夹具/锁/目标环境无效 | 验证先验，修复基础设施，重新获取完整资格 | 将不满足前提的场景伪称不适用 |
| 暂时性下载/runner故障 | 固定受控源；有证据时有限重建/重跑，保留全部尝试 | 无限retry、使用其他SHA绿报告 |
| 视觉/性能判据噪声 | 对照原生参考、固定字体/读取器、校准单项阈值；观察项单独隔离 | 放松科学值/身份断言、删掉关键语义检查 |
| 新能力还未实现 | planned/observing，不纳入无关PR；相应启用保持关闭 | 假接口、test-only修复器、虚构pass |
| 原来就红的已有门禁 | U00复现归因并走正常修复；明确阻塞，不擅自豁免 | 以“基线问题”为由修改原聚合器放行 |

重试只处理分类清楚的可恢复基础设施故障，默认一次清洁重试作为起点可审查调整；产品断言不自动重试。不要使用全局continue-on-error。若现有test有严格xfail/XPASS流程，按其明确缺陷追踪使用，不能给整类新功能批量xfail来代表已验证。[W2]

不可靠的**新增观察项**可暂不提升；已enforced的核心保护若真的测试器坏了，应修测试器或提供已审查的同等确定性替代，不悄悄取消保护。风险/范围/负责人/恢复条件必须留档，不能永久隔离后仍称能力已合格。

## 8. 证据层与时间预算

小切片：JSON结果 + JUnit/既有日志 + fixture/命令身份足够；不要为每条纯函数测试要求OS进程跟踪。E2E/发行：实际解释器、图内已知值、权限动作、原件变更、source/final hash、安装物身份。需要的关键证据缺失不能通过；可选截图上传失败不能把已完整核心证据等价为产品坏了。

phase docs里计划目标不是事实。时间阈值由U00实测，先优化最贵共享setup、控制并行和缓存下载bytes；不让CI先造完整虚拟化平台才开始第一项重构。32场景按明确范围逐步实例化、强交互固定、低风险pairwise。

## 9. 安全和缓存

未受信PR不进入含真实实验数据/发布凭据的长期实验室主机。使用可销毁目标和合成数据，不卸载共享主机Python来伪装干净环境。冷/热环境分列；缓存runtime/wheel经hash验证，不缓存成功worker、用户配置或可移动venv。[W3]

本地测试服务可替换供应地址，不能替换产品解析/选择/安装决定。离线禁外网仍保留必要loopback；PIP_NO_INDEX不等于整个进程不能联网。公网供应失败单列，核心在线准备发行资格仍需可兑现的来源；不关闭TLS校验。


---

<!-- 包内来源：04_ARCHITECTURE.md -->

# 职责与跨主线契约

## 1. 数据方向

```text
正常打开入口（桌面 / HTTP / MCP / 原生 CLI）
    │
    ├── 已有静态图 → 直接可用的排版/标注/导出路径
    │
    └── ProjectPreparation
          环境证据 + 完整依赖意图 + 执行上下文 + 授权
                ↓
          现有 ExecutionSpec / native invocation
                ↓
          科学 worker / 用户原生进程
          Figure 捕获、图内编辑与重放
                ↓
          SourceArtifact + ExecutionReceipt
                ↓
          SourceResolver（只冻结本次源图产物）
                ↓
          原 ExportJob 内的 RenderPlan → Render IR
                ↓
          Typography / PDF合成 / PDFium栅格运行时
                ↓
          封口的最终文件 → ArtifactInspector
                ↓
          ArtifactManifest / 有范围的Proof → 原有发布事务
```

源 PNG byte copy、位图原像素网格、科学 worker SVG/EPS 直出是正式分支，不强行经历 PDF round-trip。

## 2. 最小共享合同（在 U01 定义草案，用真实切片修订）

| 合同 | 唯一责任 | 最小字段/约束 |
|---|---|---|
| PreparationPlan | 准备编排 | 项目/入口/revision、原选择与候选理由、Python要求、DependencyIntent引用、LaunchContext、grant、预算；不是实际执行证明 |
| LaunchContext / ExecutionSpec | 现有 execspec/workdir | interpreter、target、原 argv、cwd 来源、授权根/绑定版本、写入模式；旧 project 值仍是 script.parent |
| DependencyIntent | 现有 depresolve/deprepair 的无损扩展 | name/version/extras/marker/selected group/source/constraints；unknown不是空依赖 |
| SourceArtifact | 捕获/源解析 | source ID、实际产物 bytes hash、类型/尺寸、实例/override身份；文件归属和生命周期明确 |
| ExecutionReceipt | worker自报+控制面关联 | 实际 Python/prefix/关键包版本、实际 context、source revision、generation、产物；输入观察 completeness可partial |
| RenderPlan / IR | 导出编译/渲染 | 规范化原 ExportRequest、资源引用、顺序/单位/变换/clip、已排字形；无文件扫描/包安装/任意脚本 |
| ArtifactManifest | 检查与导出结果 | plan/observed/policy 分开；artifact hash、执行回执引用、检查范围、退化与unknown；发布后不改已核验字节 |

一个权威合同可放在既有模块，名称不是强制的新类名。不要复制旧 request 默认值、run 命令解析或错误枚举。UI 与 MCP 消费同一准备结果；允许为旧客户端做明确投影。

## 3. 三种身份用途不能混同

本机环境路径/prefix/配置状态可进入**私有失效键**；不必为了追求跨机器稳定而丢掉区分两个 venv 的关键信息。公开语义身份只包含可公开的规范化意图及获准来源身份。最终文件 hash 是另一个字段，不能把它再写入自身形成自引用。

旧热 Figure 代表当时的数据结果。数据后续改变时，普通导出可以按明确旧快照继续，不自动清空编辑重新计算；请求重新计算/写回时按各自冲突政策复核。SourceResolver 不承担完整实验目录备份，也不复制脚本破坏 __file__。

## 4. 原生库和运行环境

科学 worker 与应用 PDF runtime 不混包。PDFium 生命周期集中，进程内部串行；首轮采用有界单个应用 render child + 排队即可，实测需要再扩多个进程。进程隔离、内存预算和异常恢复依然要做，但不重建 workerd 或通用分布式调度层。

fontTools/HarfBuzz/成熟PDF写入适配的选型由 U02 实证。字体策略与实际写入要支持首轮批准字符/字体集合，未实现字体类型明确边界；不能先宣称“支持所有字体”，再让测试负责证明不可能的范围。

## 5. 数据定位的硬边界

恢复正确 cwd 能修复相对路径语义，但不能普遍修复脚本硬编码的失效绝对路径。只有真实接入的参数、已支持的数据绑定机制或已验证工作区映射能改变输入位置；“记住一个目录”本身不是重映射实现。核心测试先使用可表达的真实上下文，硬编码原生绝对路径迁移放X01并如实提示。

新代码不得增加任意读盘/执行调试 API 来方便测试。测试若缺观察口，优先使用有限产品协议、已知合成数值、文件/进程外部证据，单独论证最小诊断接口。


---

<!-- 包内来源：05_TEST_STRATEGY.md -->

# 测试策略：共享装置，不混淆三把尺

## 1. 三套语料的职责

保留现有CompatBench（科学图捕获/编辑/原生保真）、RenderBench（合成/字形/真实文件）、FirstOpenBench（环境/输入/授权到首张可编辑图）。它们共享fixture基础设施、固定资源和结果格式，不合成一份无法区分失败来源的分数。

原32个FO场景全部保留于registry。FO14的新增命名Conda自动发现明确后置X01；其他场景按本轮受支持标准路径实现。复合场景需预先展开actual expected outcome，不能运行后挑“自动成功或停止都算对”。

## 2. 从U00开始建立的最小夹具

1. 单文件Matplotlib/CSV，同目录，已有可用环境。
2. scripts/entry.py 与 data/ 分离，原cwd明确与歧义两种；__file__及本地包。
3. 同名不同值文件：正确[2,4,8]，干扰[200,400,800]；绘制3*x+1。
4. 项目.venv与应用真实不同Python；真实h5py与HDF5，不继承harness site-packages。
5. 几个小型非内置依赖联合安装；marker false、extra、约束冲突、未知本地模块。
6. 空白/简单路径/箭头/中文LatinGreek上下标/非对称页盒/透明重叠/PNG元数据。

先少量正反对照、每个直接服务当前切片。目录结构、本地wheel、小HDF5都用合成数据，不要求用户上传实验文件。

## 3. 完整用户链的统一验收

准备准确的假想用户环境→核验先验→通过真实产品入口打开→记录必要授权→真实科学执行→核对已知绘图数值→做一次可见patch且数据不变→按现有应用会话重放→导出→独立检查最终文件。

U03/U04的首开可先用旧后端完成终点，报告backend identity；U09/FO32才要求RenderCore终点。这样不是降低新后端验收，而是不让测试装置与两套实现互相等待。

参考原生执行只对安全合成fixture，在独立目录进行，不把它预热的环境/输出当作产品成果。不同科学栈分别与该栈native比较；脚本原生计算成本与产品准备开销分开。

## 4. 自动化、授权和副作用

无配置发现用例不能预写TAVOTTO_WORKER_PYTHON/remember/set_mode；用户已有.venv可以由fixture建设，这正是初始状态。待产品安装的包不得由harness先装。允许通过公开API或真实UI完成合同要求的一次操作，不能伪造store、token、receipt。

区分执行前失败和执行中失败：元数据/import先验通常不运行整段脚本；动态依赖可能运行后才发现，要按真实授权及执行模式有界处理，而非每例强制“整段永远只执行一次”。native不透明自动重跑始终不允许。

数据只读测试针对声明的保护模式：现有有限guard不升级成全OS保证。所有写入测试使用临时受控数据；定义允许副作用清单（例如合法缓存），禁止未授权源码/依赖变更，不能因正常.pyc就判安装污染。

## 5. 独立正确性判据

字体/矢量：独立读取器+解析式fixture+固定rasterizer，不从生产同一个transform helper反算期望。合法文件能打开不等于文字/路径正确；ToUnicode存在不等于映射正确。

同一次RasterBuffer的PNG/TIFF校验尺寸、像素、alpha与DPI；original PNG按原字节。跨renderer不比较文件压缩字节；旧/新PDF用同一读取栈对比，再对同一PDF比较光栅器差异，定位责任。

FirstOpen优先核对真实version/prefix、包来源、确定数据值；回执是产品证据，不是唯一真值。高风险原生访问用额外OS观测；普通fixture不要求装一个全系统跟踪器。

## 6. 分组建议（不是今天立刻启用的强制清单）

- 最终短常驻集候选：FO01、FO03、FO07（必要引导）、FO15、FO19、FO25、FO31 + 一条简短新输出；U03起按已实现逐个提升。
- 集成：FO02/04/05/06/09/11/12/13/17、FO18/20/21/22、FO24/26/27/28/29/30等关键变体；不要求每例全平台全版本。
- 发行：FO23、FO32，以及所有该平台已启用能力的必需代表实例。
- 夜间：更广组合/长跑/真实供应源，FO14在X01实施后取得其独立资格。

原JSON的minimum_deep_lane是旧建议，本包registry里的新分组和03政策统一生效；不是沿用所有旧pr标记第一天全跑。

## 7. 不可伪造的报告

分别记录product_outcome与test_verdict；自动/引导成功分开，safe_stop测试pass不进入兼容成功分子。性能观察缺数据不能填提升比例。

实例标识绑定源码SHA、产物、目标平台、runtime/锁、fixture、入口和能力版本；预期集合在执行前产生。观察台账not_run可以完整，结果台账必需项not_run不可以合格。不把注册一个未来case误当已执行。

关键负例：默认选错Python、AST丢文件、错同名数据、丢markers、误装私有包、坏FontFile/ToUnicode、错误alpha/CTM、旧receipt、取消后发布ready、缺report/job。每阶段做与本次机制有关的少量定点反证，深层mutation集中在夜间；不要求每个文档PR全mutation。


---

<!-- 包内来源：06_ENABLE_AND_RELEASE.md -->

# 默认启用、PyMuPDF退役与发行资格

## 1. 独立启用，不绑成一个总开关

按能力和目标平台记录状态：existing-environment-preparation、managed-dependency-preparation、private-python-provisioning、rendercore-output。新UI/协议不可声称未合格能力可用。短期开发开关只选择已写明的单一策略，不在失败时试另一个backend直到成功。

已有环境首开改善可以在RenderCore未切换时独立使用；RenderCore候选可以在私有Python下载器仍完善时接受源图。最终“本轮核心全完成”必须同时取得两条主线所需资格，不能只成功一半却改名完成。

## 2. 启用条件

开启某能力前：有真实用户路径正例、明确失败/边界例、鉴权/副作用/取消检查、对应平台候选执行、enforced实例表、升级与回退方案。NO_SYSTEM_PYTHON资格只能来自真实可验证目标，不来自清PATH和mock版本。

若不具备某平台验证条件，可先开发/合并关闭状态代码，保持该能力未取得平台资格；不得凭另一平台替代或自动删掉支持承诺。必要的产品范围调整由用户明确决定。

## 3. U10退役的顺序

先让新实现覆盖全部门面及实际消费者，并用新独立判据替代旧实现特定读取断言；候选以新后端明确运行成功。

然后在一个可审查切换变更中：切默认→移除应用依赖与打包残留→运行普通测试和新后端专用退役扫描。切换前要求“候选不依赖旧库”，切换后要求“正式发行闭包没有旧库”，两者不是循环前置。

退役扫描的主语是Tavotto应用/发行/runtime闭包和新核心调用，不是整个用户硬盘。用户科学脚本独立依赖PyMuPDF不能因此被误删/修改；也不能将该用户环境打包进Pro并声称已审计。Git历史、归档文字和隔离历史基线保留，分类清楚。

无需同时把原项目格式做破坏性迁移。字体策略/缓存身份升级有明确版本，处理旧项目的默认字体合法变化；不静默重排用户全部画布。

## 4. U11最终产物

最终windows NSIS/sidecar/CLI、macOS签名公证后app、实际wheel/sdist依照当前支持矩阵分别检查，不创造不存在的Linux桌面版。以最终字节hash绑定构建/签名来源；签名改变内容时不能沿用签名前文件hash当作最终资格。

旧包的下载/回滚路径与当前目标发行的依赖审计分别处理。商业授权问题另行审查，本轮根许可证不变，不构建Pro付费系统。

## 5. 发布前最小价值对照

- 三类首开问题：不同受支持Python、正确数据上下文、非内置包，明确哪些自动哪些一次引导；无系统Python有真实私有准备路径。
- 新默认字体文字可见/检索/来源，flip和已支持组透明保持真实矢量；复杂未知不虚报。
- 原图、画布、多格式、写回与取消/partial正确；实际执行身份与最终artifact可关联。
- 旧必需行为保持，批准改变列明；性能/包体/CI开销如实测量，不要求每项都更快。

已知严重数据/权限/完整性问题不能发行；后续扩展未做或可选验证unknown可以明确说明。未取得必需资格不能写release-ready；实际发布操作仍需用户另行明确授权。


---

<!-- 包内来源：phases/U00_baseline.md -->

# U00 · 统一基线，不先动默认实现

**前置 milestone：** 无。先读总提示词及当前有效handoff；遵循03门禁政策。


**输入：** 总提示词、范围/CI政策、registry原条目、当前仓库规则。详细旧审计与最新采样SHA不同，先以当前checkout为准。

## 实际实现

确认HEAD/status，列出自旧审计以来与pdfbackend、execspec/pool/projectenv、deprepair/workdir、UI/协议、CI/packaging相关变化。未读diff不得写“无变化”。构建一次真实调用图和能力清单，区分现有公开功能、已知缺陷、仅注释承诺与本次新增目标。

按当前命令运行最小已有测试和产品smoke，记录本来就红的检查。不因基线红而改aggregate_gate或全局豁免；将独立基线修复作为最小先行切片。

用少量合成项目冻结：同目录CSV；scripts/data分离；已有不同Python环境；非内置依赖；同名不同数据；一页文字/图形/透明PDF；原PNG metadata。首开失败真实记录，但不马上作为required挂全部PR。

从__all__及真实调用方生成facade迁移列表，标记原图byte-copy、位图native-grid、worker SVG/EPS、annotation写回、缓存、取消partial。旧测试分用户合同和实现特定断言，逐条指定迁移判据。

确认字体最小批准范围、应用Python支持范围、scientific runtime范围、可用runner和真安装目标。记录基础构建时间、测试时间、峰值资源与下载成本，只测不拍硬阈值。

## 出口

有实际基线命令/日志/最小fixture、清单和范围草案；未来能力保持not_run。原生产依赖、默认后端、根LICENSE和用户文件不变。registry映射与当前代码差异已标注，不要求全部新验收绿。

## 随切片测试

清单去重、facade漏项反例、fixture输入真值、原生参考不污染被测环境。已有产品smoke真实跑一条，不用新建test-only准备器。


---

<!-- 包内来源：phases/U01_contracts.md -->

# U01 · 用最少合同连通两条主线，测试从这里开始

**前置 milestone：** U00.baseline。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

在现有模块旁定义或扩展PreparationPlan/Result、ExecutionReceipt最小载荷、SourceArtifact、RenderPlan引用，分清计划/观测、私有/公开身份。先用现有一个真实worker产生最小receipt和source，不做只有Protocol的假进展。

FirstOpenBench/RenderBench共享结果装配与fixture资源，但保留不同case和结论。先实现实例清单、参数/结果schema、身份校验、错误分类及最小真实既有产品路径；不要构建通用CI平台。

新gate enrollment在当前仓库找最小落点，初期作为已有job的步骤。读取03的planned/observing/enforced政策：未来case在台账，不因缺实现全部进入pytest默认发现或required矩阵。观察失败使用清楚命名的独立任务，不让普通PR从此常红。

校验stage scope、case scope、预期实例集合与结果分离；生成JSON/JUnit/摘要接入现有上传方式。纯契约校验始终真跑，不得用“空集合所以通过”冒充产品资格。

为新增异步准备接口复用现有会话认证，第一版能报告现有runtime正在运行、错误、取消和静态源可用；复杂依赖下一阶段补。新权限模型不重复造trust系统。

## 出口与测试

一个真实旧后端首开/导出fixture穿过公共入口；纯模型不依赖科学栈/native对象；错项目/错generation/空报告/重复ID/错SHA负例失败。其他32场景尚未实现可以planned，必须在报告中与“已通过”分开。

本阶段只把本切片的真测试纳入现有快线；不新造需要完整安装VM的required job。


---

<!-- 包内来源：phases/U02_spikes.md -->

# U02 · 两个独立技术证明，不用最终发行门挡模型开发

**前置 milestone：** U01.contracts。先读总提示词及当前有效handoff；遵循03门禁政策。


两个milestone分别验收。`render_spike`通过即可推进U06；`runtime_spike`通过即可推进U05，彼此不要求对方所有平台完成。

## render_spike

用候选固定版本PDFium/pikepdf和成熟字体处理组件，制作小型真实PDF：导入非对称源页、变换/clip、内部重叠的整体opacity，以及中英/Greek/上下标可检索文字。用独立读取器确认几何/字体映射与实际pixels。决定成熟写入器适配还是受限自有emitter，不做通用parser。

确定合法默认字体来源与许可，验证字体真实文件、shaped glyph到PDF编码/子集/ToUnicode，不以outline替代。先支持本轮字体/字符集合；复杂字体仅边界测试。记录会改变的旧字体名称/布局基线。

做最小应用render child，统一串行native调用、错误/close/超限路径。用本机与至少一个可用真实目标构建最小候选freeze，验证资源和child启动；另一目标未测明确保留，不影响纯模型/另一主线开发，但阻止在该目标默认启用。

## runtime_spike

验证一个固定provisioner（uv或等效可兑现组件）、私有完整Python来源、最小venv与wheel安装。只选择一条首轮生产路径，不同时自制多个resolver。验证应用私有目录、不改系统Python/注册/用户配置、无下载授权不联网、坏hash不执行。

尽早做无host帮助的最小目标试验，验证embeddable与完整Python差别。最终完整产品安装器和签名不在此gate；未具备干净目标的部分是未来启用阻塞，不伪造本阶段跨平台成功。

## 交付

两份短ADR或一个含独立结论的ADR：版本/平台/字体来源、实测输入/产物/退出码、失败路线、选择原因、仍缺的目标。不得使用“方案应该可行”替代真实小产物。不存在默认切换。


---

<!-- 包内来源：phases/U03_first_open.md -->

# U03 · 先让已有科研项目顺利打开，仍可用旧PDF后端

**前置 milestone：** U01.contracts、U01.harness。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

把现有发现/体检/记住环境前移到首次科学执行之前，保留显式选择优先级。读取受支持版本证据、.venv及现有配置，按整个解释器选择；同一base不同prefix不能去重。失效明确选择给出原因，不静默换一个能import的Python。

将discover的无法解析/解码/IO错误与确认非绘图分开，保持文件在库存可见；合法源编码正确处理，授权目标解析器只做静态分析并复用stem/entry算法。不能在母进程AST不认识后直接丢脚本。

扩展workdir/ExecutionSpec表达script.parent、project.root、原invocation.cwd和经确认目录。旧project值语义不变。保留原脚本位置、__file__和本地模块来源。对明确有效外部绝对路径沿用，不搬整个数据目录。

有证据且已获所需授权则自动准备；目录含义歧义或首次真实cwd会产生不同写入影响时，正常界面一次清楚确认并记住。未知绝对路径不靠同名搜索猜。选择目录只有实际被消费到启动上下文才算实现。

把准备状态嵌入正常打开路径：静态源先可用、已修复中间问题不闪红；实际脚本失败仍看得见。UI与HTTP/MCP由同一编排服务消费；原native不改所有权/环境。捕获后才ready_editable，做一次真实编辑验证。

## 随切片测试

从干净应用设置打开同目录CSV、scripts/data分离（明确与歧义各一条）、__file__/本地重名模块；真实不同Python用实际版本和prefix，不只mock。正确数据[2,4,8]与同名干扰文件[200,400,800]必须可区分。

旧sandbox/guard机制测试保留，新首开通过授权上下文解决，不在harness预设set_mode。A/B同名项目异步返回、取消和过期授权要测试。禁止预检反复执行整段脚本。

## 出口

已有环境路径真实“打开→编辑→旧后端导出”，证据标清backend。已成熟小场景可enforced；新包下载仍未实现，不作为本阶段卡点。无系统Python能力不能因此被宣称成功。


---

<!-- 包内来源：phases/U04_dependencies.md -->

# U04 · 一次准备多个依赖，不等私有Python下载器

**前置 milestone：** U03.existing_open。先读总提示词及当前有效handoff；遵循03门禁政策。


使用已有合格base Python完成本阶段；U05随后补base来源。复用depresolve/deprepair/managedenv/envlease，不能建立第二个安装锁。

## 实际实现

以成熟解析器保留PEP508的name、specifier、extras、marker与选中group；requirements -r/constraints在授权项目内有界读取，循环/越界失败。不要把旧简化dict当完整依赖。PEP723/pyproject支持由实际元数据范围决定，未知格式显式unsupported，不视为无要求。

区分stdlib、本地模块、第三方、可选/TYPE_CHECKING和动态未知。标准路径联合求解项目约束+scientific adapter约束；默认wheels-only/批准源，未知私有名不去公网试装。完整未验证Poetry/pixi锁转换后置X01，但不能把^或marker剥掉偷偷继续。

在最终版本目录创建新环境并标incomplete；真实安装、依赖一致性、关键import、worker自检都通过再切active。旧环境保持到lease释放。不是把已创建venv从tmp rename过去，也不是原地往所有项目共享site-packages写包。

计划绑定项目/完整要求/环境/源/授权/版本。一次“准备并打开”可以覆盖完整已知计划，不能覆盖后发现的私有源/构建/用户环境修改。动态遗漏有界重新计划；普通ValueError不触发装包。取消不留下假ready，不为安装杀native。

## 测试

用小型固定wheelhouse/本地供应服务做真实多包联合安装。marker不适用、未选group不安装；本地lab_utils不误装；冲突/无wheel/坏hash/取消/自测失败不切active。事前验证缺包真不在目标，harness不得预装它。

检查依赖包/配置没有未经授权变化，合法.pyc/cache按预声明白名单单列。两个项目包版本冲突各自独立；实际原生扩展验证放集成lane，不把纯Python安装成功当ABI资格。

## 出口

已有base条件下正常UI/HTTP/MCP“准备并打开”成功，经真实worker产图并编辑；不要求U05已经能下载Python。对不支持声明有清楚动作，无无限缺包循环。


---

<!-- 包内来源：phases/U05_private_python.md -->

# U05 · 补齐干净机器的基础解释器来源

**前置 milestone：** U04.managed_dependencies、U02.runtime_spike。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

接入U02选定provisioner；复用U04的环境计划/事务/验证，不重写包安装。固定runtime版本/build/ABI/架构和来源，安装在Tavotto可写数据目录，bundled科学runtime继续不可污染。

应用与系统的配置隔离：不更改PATH、shell、默认Python、Windows注册选择或用户.python-version；批准代理/源和凭据有明确配置，下载器不能自行使用任意项目设置。验证解压路径、hash、可执行位和真实启动。

相同runtime下载有界去重、取消按消费者管理，旧运行版本有lease不被GC。离线已缓存可复用，缺缓存明确受限；无匹配版本不能擅自改用户约束。固定版本字段可变更但需新资格，不自动追最新。

## 目标验证

工程阶段先用可运行的本地/目标候选；默认启用此能力之前必须有目标平台的无系统Python验证。发行再测最终安装字节。不要每个PR都临时造全部目标VM，也不能永远只有mock/no-PATH场景。

干净目标无系统Python/uv/pip可被产品借用；控制器隔离且审计先验。通过正常入口请求额外包，经一次完整授权准备私有Python→U04环境→真实出图/编辑。缓存齐备离线与无缓存离线分别测试，后者safe_stop不计成功兼容。

取消/截断/坏hash/磁盘配额不足/应用重开不产生可复用半环境；不操作共享实验室主机安装。实际支持范围依据受测矩阵，未测平台不能声称完成。

## 出口

no-system-Python核心功能已实现，已测/待测目标分列。核心最终交付不能省略此项；缺目标证据可以继续不相关开发，但该平台不默认开启此新能力。


---

<!-- 包内来源：phases/U06_render_model_text.md -->

# U06 · IR、源图计划和可检索文字先形成真实闭环

**前置 milestone：** U01.contracts、U02.render_spike。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

沿用facade作为入口；实现Page/Group/Path/Image/ImportedPage/ShapedText和资源引用，明确pt、坐标原点、变换/clip顺序、paint order、alpha与数值校验。Arrow/Shape可以编译Path，不为每个图标建立插件。

RenderPlan复用ExportRequest与规范化授权，在原ExportJob内解析冻结源产物；不能把原始脚本和整个实验数据复制到staging替代执行上下文。保持source/instance/revision区别；无override静态源不重跑脚本。

按U02合法默认字体落实registry/coverage/fallback/metrics/shaping，批准字体文件身份、face/style与字形缓存。保留primary/CJK/fallback/missing的可解释语义；本轮集合外明示限制，不暗中回退系统脸。字体政策需要ADR及来源allowlist，不删旧provenance测试了事。

将实际glyph、cluster、advance/offset和逻辑Unicode写成可检索PDF文字；正确资源编码、宽度、subset/GID/ToUnicode。采用已验证适配器，不额外造完整PDF parser。缺字/非法字体不伪装成成功嵌入。

前端画布文字得到同源测量/预览且有revision保护。可以先对本轮固定字体支持可见一致策略，不重写整个编辑器；实际输入/选择/可访问逻辑文本仍可用。科学图内部字体继续由原worker决定。

## 验收

简单页+图形+中英Greek上下标的真实PDF、独立文字提取、字体结构、像素与几何；丢FontFile/ToUnicode、错GID、缺字/不同字体同名等负例。授权默认字体离线可用。

旧字体名/像素实现断言按D07批准迁移，区分合理新字体差异和真正越界/内容丢失；不能无授权修改用户框尺寸补救。新核心无旧backend import；旧默认通道和旧测试暂时仍允许存在。

## 出口

一条录制/编译/写入/预览真实切片成立，纯模型可无native import；完整所有canvas放置与raster/原图入口由U07/U08补，不在此时切默认。


---

<!-- 包内来源：phases/U07_render_output.md -->

# U07 · 完整合成与PNG/TIFF同源

**前置 milestone：** U06.ir_text。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

实现ImportedPage Form导入、页盒非零原点、Rotate/UserUnit、Tavotto crop/rotation/flip顺序。普通路径、全部现有shape/arrow/brace与legacy字段按原用户合同迁移。graphics-state及资源命名/继承正确，两个实例不串。

镜像及受支持整体透明保留真实矢量与文字；内部重叠源必须验证透明组语义，不把每笔alpha当整体。复杂mask/blend明确scope/合法降级政策，不全页位图后仍报告vector。

应用拥有的PDFium child统一处理probe/preview/raster/inspect的native调用。首轮一个进程串行+有界队列、pixel/memory上限、timeout/cancel/restart/close；必要时才加受测的多进程并行。不是重写现有Rust supervisor，也不将PDF库装进科学环境。

Canonical PDF完成后生成RasterBuffer，定义stride、RGB/BGRA、bit-depth、colorspace、straight/premultiplied alpha及buffer所有权。PNG/TIFF同参数使用同一状态，保留透明和密度。能复用现有tiffwrite则保留，Pillow确有用途再按正式依赖收集codec。

预览cache含实际源、后端build/字体政策、像素/颜色参数；保持同键去重/临时发布/Windows句柄处理。异常不返回空白图或旧图作成功。

## 验收

解析式非对称几何/页盒、同名资源、透明组、opacity=0、同灰度异色、alpha边缘、padding stride；PNG/TIFF规范解码像素一致。更换renderer的视觉差分与真实geometry分开校准。

并发preview/probe/export无进程内native并发；child崩溃/超时后下一请求可恢复；资源回到文档化缓存预算内，不硬要求所有OS缓存瞬间为0。冻结最小child在目标候选真启动，最终签名留U11。

## 出口

主要合成和raster路径在候选后端上可重复通过；旧门面剩余及事务consumer由U08收口。


---

<!-- 包内来源：phases/U08_facade_validation.md -->

# U08 · 所有用户路径接上，而不是只完成save_pdf

**前置 milestone：** U07.compose_raster。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

按U00 facade清单逐项迁移：探测、预览、text width/coverage、compare_png、original PDF/PNG/TIFF、compose、annotation写回。原PNG直接复制保字节，位图转码保native grid/明确密度，PDF默认第一页，原图不吃canvas变换。原worker SVG/EPS直出不强制PDF转换。

SourceResolver/RenderPlan交给现有ExportJob；多实例中间文件私有，多格式同一科学状态；临时结果验证、取消提交点、命名预留、覆盖、report失败/partial、写回expected identity和原件恢复都沿用既有权威。

重新打开封口staging产物验证核心尺寸/完整性/字体或图像等可观察事实；用户选择严格规范时必需项失败/未知按规范阻断。普通导出对可选复杂PDF检查unknown给说明，不因此拒绝合法成果。故意坏文件/错误实际尺寸/伪造客户端proof不能通过。

字体实际使用/声明分开、carrier与vector/mixed/raster/unknown分开；受限CTM/Form遍历有预算，未支持Type3/复杂clip明确unknown。优先验证本轮发射的受控结构，再扩大任意外部PDF覆盖；不要求所有PDF都可证明无裁切。

HTTP同步/异步/SSE、MCP、native runtime asset、前端导出回执与旧vector投影一起审计。保持字段兼容和错误码双语；未核验不显示绿色。四类Web构建的资源路径保持，独立Playground不冒充有native后端。

## 测试

真实原图/canvas/写回/MCP/格式partial/报告失败/取消/项目切换/并发；独立读取器检查产物而非只信返回JSON。验证不是在发布后才发现必需失败。

旧MuPDF几何测试迁移判据，不要求仿制整个get_drawings API；每个删除/替换的实现断言在ledger有替代证据。旧用户契约测试不凭主观“现在没必要”删除。

## 出口

facade全入口候选覆盖，常见实际输出具有可信有限检查。默认仍可保持旧实现，候选选用新实现必须明确无静默回退；U09再与完整准备链联调。


---

<!-- 包内来源：phases/U09_join.md -->

# U09 · 从正确执行到正确产物的一条证据链

**前置 milestone：** U03.existing_open、U08.facade_parity。先读总提示词及当前有效handoff；遵循03门禁政策。


**两个独立出口：** `existing_env_join` 使用U03已有环境+U08新输出，不依赖私有Python；`managed_env_join` 另需U04与U05完成，并验证产品自行准备环境的整链。先取得前者即可推进U10替换；本轮最终综合资格U11必须两者齐备。不要把后者未完成变成PDF退役的循环等待。

## 实际实现

完善U01最小ExecutionReceipt：来自真正worker/native的Python/prefix/关键包/实际cwd与binding revision，控制面绑定generation/source revision/源图hash；不能复制前置probe充当本次事实。已观察的本地模块/数据身份保留，原生I/O/网络无法完整观察时标partial/unknown。

RenderPlan关联receipt和SourceArtifact；Manifest含来源、实际文件检查、规范结论、artifact/render/semantic/run身份。内部完整路径留本机，公开XMP/报告/遥测不携带秘密或科研正文。单纯hash不证明跨平台复现。

Trace默认有界并能定位当前错误阶段；不建全系统追踪平台。节点只保留真实知道的科学gid/来源关系，opaque页面不编造内部语义。

UI正常打开到输出、MCP准备/需要输入/导出统一结果；环境、数据绑定变化触发正确旧计划失效，不能丢掉用户的live编辑。静态已有成果与重新计算区分；native不靠自动杀进程来模拟重放。

## 核心联合实例

项目Python与应用不同，真实h5py，外部HDF5[2,4,8]、沙盒同名干扰[200,400,800]，中文空格路径。原上下文明确/已授权时自动；歧义时一次选择。独立真值绘图[7,13,25]，不能[601,1201,2401]。

通过真实UI或认证HTTP/MCP首开，做可见patch，新应用worker重放，再新RenderCore导出PDF/PNG/TIFF适用组合；实际科学值、源revision和finalhash对应。两科学环境都可向同一个应用渲染器交图，科学环境不需要新增PDF依赖。

## 收口

FO32先在当前可用目标通过，然后平台资格在U10/U11完成；缺最终签名不能阻止修复联调代码，仍不可宣称发行ready。旧后端早期FO成功记录与此时新终点分开。

至少测试错receipt、过期generation、错误同名数据、数据预检后变化、raw秘密泄漏、报告假绿色。可复现性核心是明确环境下的语义/视觉；严格byte模式和全数据证明非本轮硬目标。


---

<!-- 包内来源：phases/U10_cutover.md -->

# U10 · 让切换本身可验证，不陷入先删才能测的循环

**前置 milestone：** U09.existing_env_join。先读总提示词及当前有效handoff；遵循03门禁政策。


## 切换前

按能力×平台完成新候选全部facade、正常/负例/原件事务和关键FirstOpenBench资格；旧语义批准变化可见。所有用户入口都可用新实现明确运行；不能仅跑内部构造函数。新依赖/字体的实际应用闭包有来源、锁和目标wheel/最低平台核验。

提前对未签或适当开发签名的真实冻结候选做资源/PIL/child/CLI/workerd闭包检查；保留既有最终签名流程。无systemPython能力要有真实目标先验，不能借宿主。平台尚缺证据就不在该平台启用对应新能力；仍然记录核心目标未完成。

## 实际切换

一个可审查变更切default并移除应用PyMuPDF依赖、fitz别名/动态探测/打包残留与旧fallback。普通测试切到新实现+独立判据，历史差分仅在隔离工具/批准资产。此时再启用目标发行closure的零旧库扫描；U06时只扫新核心，时机不可混。

清理缓存/coverage/backend identity与文档，旧项目可打开；默认字体变更采用已经批准的layout policy，不改用户科学脚本和所有画布尺寸。保持必要旧API投影。

## 门禁变化

提升该能力的enrollment为enforced并同步已有gate job契约；已选required不能被缺依赖/无runner隐去。所有core要求在最终lane有已定义验证；复杂扩展later不算pass。

## 验收与回退

干净新进程主动阻断pymupdf/fitz import，实际全部主要路径可运行；扫描应用依赖图、native文件、SBOM与运行链。文档字样/Git历史/用户自有科学环境不误判为应用残留。

失败明确暴露，不悄悄调用旧库。开发回退可回到先前经过验证版本/策略，但不自动发布、不删除用户项目/环境、不把旧依赖偷偷装入新发行包。最终签名安装物资格仍在U11，不把切默认称已发布。


---

<!-- 包内来源：phases/U11_qualification.md -->

# U11 · 精确产物与最终范围逐项收口

**前置 milestone：** U10.cutover、U09.managed_env_join。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际验收

以最终candidate SHA与实际包hash为对象；Windows x64 sidecar/CLI/NSIS、macOS arm64签名公证后的app、实际wheel/sdist与Linux pip模式按现有支持矩阵取资格。控制器与目标环境分离，离开checkout，不依赖PYTHONPATH/宿主预装包/未批准系统字体。

每个已启用能力有对应精确目标测试：真实首开→编辑→重放→新RenderCore输出；no-systemPython完整私有准备；缓存/离线、关键取消恢复；安装/升级/卸载与旧项目。完整32场景按适用前提与新分层验，不做所有笛卡尔积；FO14扩展未做明确单列。

许可/字体来源/NOTICE和native资源按最终包核验。根许可证维持现状，未做商业版权利审核不得说已可闭源发行。本任务不自动发布插件/Release。

## 价值报告

报告旧行为是否保住；首开自动/引导成功分开；真实矢量mirror/透明、默认文字检索和嵌入、PNG/TIFF一致、有效有限产物检查；实际环境→源→文件关联。性能/体积/CI成本给测量和trade-off，不要求全部数字改善。

区分not_run、infra_error、product_failure、合格safe_stop；不删失败分母。核心188要求中属于本轮的scope均有对应证据，拆分后续部分有明确状态；不以220行表已填写代替执行。

检查被重写旧测试的意图映射与关键定点反证；公开范围准确。严重已知权限、错误数据或原件损坏阻断。非核心后续能力未测可保留，但相应默认功能/文案必须不冒称支持。

## 最终交付

一份FINAL_QUALIFICATION.md/JSON关联源码、构建、签名、fixture/case、platform、输出、许可及边界；清楚分“实现完成”“源码验证”“目标包合格”“已发布”。当前只是计划时全部保持not_run；编码后由真实证据更新。不能以本包的validate_plan通过满足任何产品要求。


---

<!-- 包内来源：extensions/X01_extended.md -->

# X01 · 更广兼容与深度核验，不与首轮核心捆绑

本次显式后置的新增范围包括：命名Conda自动发现与完整管理器启动适配、Poetry/pixi/hatch完整锁/任务语义、更多旧Python、网络存储与不可直接由cwd表达的硬编码绝对数据迁移。不是默认放行它们；未适配时沿用真正可用的显式解释器/native路径或清楚说明边界。

每次只添加一个provider：机器可读发现→不丢约束→必要授权→准确启动→实际原生import与数据→捕获编辑重放→最终输出。使用现有candidate/ExecutionSpec/envlease；不拼activate shell、不默默调用任意工作区任务。命名Conda自动化完成后再将FO-019/FO14提升enforced，不能借conda base路径成功替代命名环境。

完整Poetry等锁需要版本化adapter；标准PEP508保持和不丢约束属于U04核心。这里新增完整解释器/包选择时，用真实原管理器参考对拍，未知字段/约束不得忽略。

强只读工作区若实现，必须有能覆盖声明的native writer/子进程的实际保护机制；普通文件副本/软链接不可宣称隔离。相对__file__、多个配套数据、失效绝对路径、授权mount范围、磁盘预算都应明确；不可复制整盘或改用户源码来假装透明。

另有可独立选取的深度核验子范围：更广字体类型/语言脚本的实际塑形与预览、严格字节确定输出模式。核心的批准字体正确性和语义/视觉重放仍在U06/U09，不能后置；这里只扩展尚未声明的范围，必须按明确字体/脚本/环境取得各自资格，不要求默认跨平台文件逐字节一致。

按03政策每个子能力独立观察→资格→启用；保留旧模式不回归。取得不了资格不进入支持声明。此阶段没有“所有管理器/字体/文件系统完成才整体通过”的无界目标，由独立明确子范围交付。


---

<!-- 包内来源：extensions/X02_manuscript.md -->

# X02 · 原E01：批量论文图编译

保留原RC-111/112的全部用户目标：共享PublicationSpec、每图独立source/override/授权、既有normalize有限修改、原ExportJob逐项发布、预算、取消、partial、恢复与增量重试。不把批量变成新的第三scope或平行renderer。

每图可有不同科学环境/执行回执，不塞入共享可变环境。预检不执行未授权脚本，不将opaque PDF误当可改字体科学图。源变化按内容身份只重做相关项，已产文件先验证hash与检查版本，不能凭存在复用。

实现最小真实API/CLI及符合原UI规范入口，随后12项混合用例验证：重复来源不同override、格式差异、名称冲突、缺字体、低PPI、normalize预算、处理中取消、报告失败。成功项真实交付；失败不改原件，合理CJK/math回退与unknown不误判整批一致。

此阶段不制作支付、云同步或账号；即使未来用于Pro，授权实现另立工程。未实现保持later，不作为PyMuPDF退役前置。


---

<!-- 包内来源：extensions/X03_svg.md -->

# X03 · 原E02：新增画布SVG

保留RC-113/114：只对可兑现的IR/来源开放，原worker SVG直出早已由U08保留。ImportedPage不意味着任意PDF能转换成语义SVG。

实现受支持Path/Group/Clip/Image/ShapedText的writer，明确定义物理尺寸、viewBox、变换/clip/alpha、资源ID冲突及文本政策。可合法提供同源font且渲染受验时用文本；用glyph outline时报告转轮廓，不伪称检索/字体嵌入。opaque PDF无真实转换时明确拒绝或用户授权局部栅格降级并报告。

XML/外链/DTD/entities/脚本/事件/CSS url/远程font有实际安全测试；独立浏览器渲染成品与canonical PDF同几何对拍，不要求跨carrier抗锯齿bit exact。UI格式开关只能来自已经验证的能力，不为齐全而开放所有场景。

不包含画布EPS、PDF/X、通用PDF逆向编辑和完整PDF→SVG parser。这一扩展独立资格，不阻塞本轮核心。


---

<!-- 包内来源：07_HANDOFF.md -->

# 统一阶段交接模板

阶段/子切片：
开始HEAD / 结束HEAD / 用户原有工作区改动：
默认路径/候选路径/本阶段拟启用能力：
已读规则与复用的权威模块：
实际代码与API/数据结构变更：
关联旧要求ID / 场景ID：

| 命令 | 目标平台/环境/产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| | | | |

本切片的正例、负例、旧行为回归：
本次是否改变case enrollment（planned/observing/enforced/later）及理由：
未运行/基础设施问题/真正产品失败，分别说明：
批准的字体/视觉差异，及未授权变更检查：
当前可合并依据（不等于可以默认启用/发行）：
仍缺哪些默认启用/精确安装物资格：
下一个无阻塞阶段/子切片：
回退方式、不能假装可回滚的外部副作用：

只保留当前有效交接加必要历史，不要求每个小PR新增一整套独立审计报告。不要把本包结构校验结果列为Tavotto功能测试通过。


---

<!-- 包内来源：SOURCES.md -->

# 来源、事实与新决定

## 附件来源

原RenderCore完整提示词/验收JSON，兼容性完整提示词/验收JSON，FirstOpenBench提示词/32场景JSON，以及原始概念方案和前次评估。全部原始字节存于archive，内容hash见sources_manifest.json。

原包中的绝大部分技术判断来自固定旧SHA：`6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。它们是此前定向源码审计，不是实际性能或跨平台测试证据。原始概念方案关于“几天实现”的估计没有被本计划继承为工程承诺。

## 本次连接器核验

截至本次采样：main指向`8b95256c0d08a14bfcfc4c81358894ef01168933`，已经不同于原详细审计基线。本次只直接复核：

- main ref；
- `scripts/ci/aggregate_gate.py`，1–162：必需job精确闭集、skipped等失败、普通PR整体deferred例外；
- `docs/support-matrix.json`：Python3.10–3.14、Windows x64/macOS arm64桌面与Linux pip等边界；
- `.github/workflows/ci.yml`，1–84：PR/merge_group/main分层、取消规则和遥测关闭。

请求过旧SHA到新SHA的compare，但大响应没有完整逐项审阅；不据此声明全部diff已审计。U00仍必须核验实施时HEAD和真实变更。

固定源码定位模板：
```text
https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/<path>
```

## 重新检查的官方资料

[W1] GitHub required status checks：最新提交、skip/neutral语义、merge_group触发和always聚合。
```text
https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
```

[W2] pytest skip/xfail：不同结果必须区分；本计划不建议用批量xfail掩盖未取得的新能力资格。
```text
https://docs.pytest.org/en/stable/how-to/skipping.html
```

[W3] CPython venv：默认隔离、基于base解释器、不应当作可移动/可复制环境；因此固定最终目录创建后切active引用。
```text
https://docs.python.org/3/library/venv.html
```

[W4] pypdfium2 API文档：PDFium线程安全边界；本计划保持集中串行调用，逐步扩应用子进程。
```text
https://pypdfium2.readthedocs.io/en/stable/python_api.html
```

## 本次新提出的内容

统一阶段U00–U11和X01–X03，D01–D16裁决、按能力准入的enrollment流程、代表性PR场景与观察政策都是本次工程建议。它们不是当前Tavotto已实现代码，也没有从文档“推导”出测试已通过。

本包内实际运行的检查只验证文档链接、JSON来源、220项映射、依赖DAG和计划校验器负例；不涵盖Tavotto源码、Python/包环境、PDF渲染或安装物。

