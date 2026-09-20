<!-- README.md -->

# Tavotto：首次打开兼容性改造补充包

日期：2026-09-15。源码审计基线：`Tavotto/Tavotto@6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。

本包补充既有 RenderCore R00–R16，不替换它。核心判断：Python、数据路径、依赖兼容发生在 RenderCore 上游；利用本次改造建立一条“准备项目 → 正确执行 → 捕获原图 → 可靠导出”的产品链，而不是把环境管理塞进 PDF 引擎。

这是源码评审与实施任务书，不是已落地代码，也不是通过了测试的报告。所有验收条目的初始状态为 `not_run`。本次通过 GitHub 连接器读取了相关实现和测试；没有完成仓库克隆、实际运行、跨平台安装或性能测量。候选技术需在当前 checkout 再核验。

## 阅读与执行

先读 `01_AUDIT_AND_DECISIONS.md` 与 `00_MASTER_PROMPT.md`；编码 Agent 按 CP00–CP08 执行。每个阶段都要读取仓库 AGENTS、相应旧实现与测试，不得只按本文中新目录名生造一套并行系统。

`02_RENDERCORE_INTEGRATION.md` 说明与 R03/R08/R11/R12/R13/R14/R16 的接点。
`03_FIRST_OPEN_BENCH.md` 说明首次打开端到端矩阵及门禁。
`04_HANDOFF.md` 为会话交接模板。
`acceptance_matrix.json` 是逐项验收账；不可将未执行项改成通过。
`SOURCES.md` 区分仓库观察与外部技术核验。

建议落点：`docs/implementation/rendercore/compatibility/`。不覆盖原 RenderCore 文件，统一入口通过链接引用本补充。不要因为兼容工作尚未完成而阻止已有 PDF/PNG 的无脚本打开；也不要因为 PDF 引擎已替换，就声称首次打开问题已解决。

## 交付边界

必须实现：首次打开准备状态机、真实 Python 选择、无系统 Python 的受控准备路径、完整依赖语义、数据运行上下文、简化前端、执行回执、首次打开真实测试。

不作为首轮必需：任意 Python 旧版本全面支持、任意 C/C++ 程序的跨平台透明只读虚拟文件系统、任意远端 Jupyter 内核接管、GPU/驱动自动安装、对所有数据源的完整可复现性证明。不能把这些未实现能力包装成已支持。


---

<!-- 01_AUDIT_AND_DECISIONS.md -->

# 仓库审计与决策

## 1. 已存在的机制值得复用

| 位置 | 当前源码支持的结论 | 新工作应如何处理 |
|---|---|---|
| `engine/execspec.py` | 已有不可变 ExecutionSpec、safe/native、cwd_mode 与稳定载荷；project 模式实际指向脚本所在目录 [C01] | 扩展已有执行语义，不另造 argv/cwd/env 拼装器 |
| `engine/projectenv.py` | 有项目内 venv 发现、真实解释器探测、缺包检验；支持 Python 3.10–3.14；Matplotlib 未验证版本有独立等级 [C02/C04] | 前移候选发现；支持等级不等于用户脚本一定可运行 |
| `engine/pool.py` | 显式环境/全局设置优先，随后项目已记住环境，再默认链；缺包后才有新项目 venv 自动接手 [C03/C05] | 对无显式选择的新项目，在首次执行前准备，保留显式选择权威 |
| `engine/workdir.py` | 已有项目级目录开关，默认 sandbox，首次切到脚本目录需确认 [C06] | 不把改默认值冒充无副作用修复 |
| `worker.py` / `figcapture.py` | Python open 类读取回退；有限删除/写入守卫；C/C++ 文件读取与部分写入不在同一保护范围 [C07/C08] | 不能宣称 OS 安全沙箱或任意外部数据均不可读取 |
| `deprepair.py` | 可信单包修复计划、安装验证、上限三轮，现有环境改动需明确确认 [C09] | 前置联合准备，保留未知 import 不猜包与用户环境保护 |
| `managedenv.py` | 按项目在应用数据目录建 venv，manifest ready/incomplete；依赖可重建但非完整 lock 复现 [C10] | 引入版本化、可验证的环境，不污染 bundled runtime |
| `managedenv.base_python()` | 仍依赖已有可创建 venv 的支持范围内 Python；排除内置 embeddable [C11] | 补齐“机器无 Python + 需要额外依赖”的入口 |
| `runcli.py` | 真实产品 native 入口已存在；终端 CLI 拥有用户子进程和 shell 上下文 [C12] | 保留所有权；不能让 GUI 偷换为另一个启动语义 |
| `envlease.py` | safe/native/安装共用环境占用模型，不为安装杀死活跃 native 计算 [C13] | 所有新准备流程接入同一租约 |
| `DependencyRepairCard.tsx` | 已有人话文案、进度、安装计划确认，但位于修复流程 [C14] | 不只是重画 UI，而是把准备过程放到首次打开 |

## 2. 五个高优先级断点

### A. 首次运行选择仍偏向默认环境，而不是项目已有证据

`resolve_worker_python(root)` 不主动对一个从未记住环境的新项目做完整约束选择。新项目没有显式设置时，会落到旧默认链；自动项目接手的错误触发器是 missing_dependency。语法、版本 API、二进制加载等失败不应靠一条缺包正则解决。[C03/C05]

这不意味着“所有不同 Python 都不支持”。当前代码明确允许科学 worker 使用支持区间内的其他 Python；缺的是首次运行前的可靠选择和准备流程。

### B. 静态发现可能早于正确解释器选择而失败

`discover.analyze_script()` 用应用解释器 `ast.parse`，读取/解码失败或 SyntaxError 返回 None；None 也表示不是绘图脚本。[C15]

结论限于该静态发现路径：较新合法语法或声明了非 UTF-8 编码的源文件可能在这里被漏掉，不等于所有手动试运行入口都会消失。需将“无法解析/未识别”与“确认非绘图脚本”分开，并由适当解析器验证。

### C. 目录模式不足以表示真实运行上下文

`project` 是 script.parent，不是项目根。源脚本在 `scripts/fig.py`、数据在 `data/`，从项目根执行 `open('data/a.csv')` 时，切到 scripts/ 仍然错误。[C01/C06]

现有相对读取回退只处理部分 Python open 路径；存在性检查、目录遍历、原生库读取未被统一。项目外真实绝对路径如果本来存在且 OS 允许，可能原本就能读取；不能把所有失败都归为权限阻断。[C07/C08]

### D. 没有系统 Python 的人不一定能创建受管环境

受管 venv 需要 base_python，而当前 base 发现排除内置 embeddable。软件自带能画常见图的 runtime，不等于它具备通用安装/建环境功能。[C11]

应由应用自己受控提供完整、私有的 Python 版本，而不是让新手自己配置系统 Python。uv managed Python 是技术候选，不是已经接入的事实。[E01/E02]

### E. 单包解析不能当完整环境配置解析器

`parse_requirements_text()` 会剥离 extras、截去环境 marker、跳过递归 -r 等；pyproject 辅助收集全部 optional-dependencies，Poetry 一些约束被简化为裸包名。[C16]

这段代码当前主要是为已发生的缺包找安装来源。新建完整环境时必须保留 marker、extras、约束和选中 group，不能从这份损失信息的 dict 直接批量安装。把 `MAX_DEPENDENCY_REPAIR_ROUNDS` 从 3 改成 30 不是解决方案。

## 3. 目标原则

“无感”是已获授权、证据足够时不打断；不是猜数据、偷换 Python、升级科研包或静默放宽写权限。

运行成功不等于科学结果相同。选择能 import 的 Python 只证明初步健康；选择同名 CSV 只证明有个文件；生成一张 PNG 只证明画出了东西。验收必须包含实际数据身份、原生运行对照、可编辑性及重放。

首轮优先级：先消灭错误的首次运行、无基础 Python 的死路、路径模式表达不足、逐包反复报错。之后再扩大旧 Python / 复杂 Conda / 更强工作区隔离。不要等待全部 RenderCore 功能完成才改善首次打开。


---

<!-- 00_MASTER_PROMPT.md -->

# 总提示词：实现 Tavotto 首次打开兼容性，而不是再加一个报错面板

你在 Tavotto 仓库内实际编码。读取根目录/目标目录 AGENTS、原 RenderCore 总提示词和本包阶段提示词。先 `git rev-parse HEAD` / `git status --short`，参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；不同则更新审计。保留用户未提交改动，不自动 reset/checkout；未经授权不 push、合并、发布、修改保护规则。

## 一、产品目标

用户打开已有科研脚本时，优先沿用其已能工作的执行上下文；必要时在 Tavotto 自己的数据目录准备兼容的完整 Python 和依赖。数据路径按原始运行语义解释，不猜测替换，不改用户科学代码。正常流程是“正在准备项目 → 显示原图 → 可编辑”，不是“红色 traceback → 设置 → 尝试 N 次”。

这项工作属于 RenderCore 的上游 `ProjectPreparation` 流程；它将已实际执行的 `ExecutionReceipt` 和源产物交给 RenderCore。禁止在 rendercore 中实现包安装、conda 发现或用户脚本执行。

## 二、必须复用的权威

- `pool.resolve_worker_python` 与既有显式选择优先级；新增 resolver 是该链的重构/消费者，不得永久保留第二套相互竞争的默认选择。
- `projectenv` 的发现、探测和支持等级；`execspec.ExecutionSpec`、safe_spec/worker_argv；Python 池、workerd、one_shot 必须消费同一执行决定。
- `workdir` 的项目设置与旧语义；新 cwd 来源必须版本化，旧 project 值仍指 script.parent。
- `depresolve` 的可信映射原则；`deprepair` 的计划/同意/结果处理；`managedenv` 生命周期；`envlease` 的租约。
- `runcli/runspec/nativehandoff/nativerelay/nativesession` 的 native 所有权、授权和协议；终端用户环境不能由桌面重新猜造。
- 现有 `readiness`、`probe`、`discover`、脚本库存、renderStore/envStore/depRepairStore。结构是否拆模块以实际调用图为准。
- RenderCore 原计划中的 SourceResolver、RenderPlan、Artifact Manifest、ExportJob 和三个稳定 CI Gate。

## 三、权限与行为底线

1. 阅读项目元数据、启动候选解释器、import 健康探测、执行脚本、下载、安装、修改用户环境、切到真实 cwd 是不同副作用；不可统一称“只读预检”。复用已有信任模型，缺少所需授权时只请求必要的一次操作。
2. 本机 metadata 发现可以自动；执行项目解释器及 import 第三方包需位于已授权的项目运行意图内。探测也可能触发 startup hooks 和库初始化，必须限时、可取消。
3. whole-interpreter 切换，不混 site-packages/PYTHONPATH，不为修复污染 bundled runtime；应用/PDF 原生依赖不装进用户科学环境。
4. 默认不安装、升级或删除用户环境的包。必要时优先建立 Tavotto 受管环境；改用户环境必须明确授权，不能伪称可完整回滚。
5. 不把任意缺失 import 当 PyPI 包。先检查授权范围内的本地模块/包、namespace/editable 情况和声明来源；未知即 needs_input，不能发明同名映射。
6. 不静默改变脚本、计算参数、数据内容、随机种子、package API 或显式 Python 版本。不要为跑通而自动删除 import 或将错误捕获吞掉。
7. 不把 Python monkeypatch、普通软/硬链接、进程单独启动说成 OS 只读安全沙箱。既有 safe 是尽力防止误写的语义；实际写入边界必须诚实呈现。
8. 不盲猜 cwd 或按同名文件替换输入。发现和定位只在用户已选择/授权的根内；多个合理候选时请用户确认一个，不逐个执行脚本试错。
9. 自动补救必须有状态、预算、修订号和真实结果。最终失败不可藏起来；已成功修复的中间错误不应先闪红再恢复。
10. 原始运行可能有副作用，不得为了诊断重复执行 N 个解释器 × N 个目录 × N 次脚本。先 metadata/静态/选中环境健康检查，再一次实际执行；后续重试受明确策略与授权约束。
11. 现成 PDF/PNG 的预览和可兑现编辑不依赖科学环境准备完成；artifact-only 与 editable 分开，缓存预览不能冒充本次脚本成功。
12. 不联网上传脚本、实验数据、环境变量、绝对文件路径；诊断/日志本地为主，共享前脱敏并获得同意。CI 硬关遥测。

## 四、建议模型（名称可遵守仓库惯例调整）

`PreparationPlan` 记录 project/entry identity、source revision、候选证据、Python 约束、完整依赖意图、选中环境、cwd 来源、数据绑定、write policy、所需 grant、网络/磁盘预算、失效条件。计划不是执行结果。

`PreparationResult` 区分 ready_for_execution、needs_permission、needs_input、blocked/failed、cancelled；不能因静态解析成功就标 ready_editable。

`ExecutionReceipt` 由真实 worker/bridge 自报并由控制面绑定：interpreter identity/version/implementation/ABI/arch、已观察 package 版本、选定 cwd/context、脚本/可观察本地依赖身份、数据观察范围、worker generation、输出源身份、限制与 guard 事件。敏感路径留本机，不进入公共 manifest。

状态机可以分工作阶段与最终能力，至少包括 inspecting、awaiting_permission、preparing_runtime、preparing_packages、checking_context、running、ready_editable、ready_artifact_only、needs_input、failed、cancelled。不同项目、旧 plan、旧 generation 的异步响应不得互相覆盖。

## 五、执行方式

按 CP00–CP08 逐阶段实际修改、运行测试、提交证据和 handoff。不要只建 Protocol、只写 fake resolver 或 mock 所有关键环境；也不要一次重写整个引擎。

每阶段记录：实际 SHA、改动文件、测试命令和退出码、真实 artifact/运行路径、未验证平台/环境、反证测试。`not_run` 不是 pass，允许本地 skip 不等于必需 CI 资格完成。不能通过删原测试、放宽基线、自动更新 golden 或吞未支持标记使门禁绿。

支持范围扩展需先加入真实兼容矩阵和科学语义验证再变更声明。第一阶段即可改善现有支持范围；不以“先支持所有 Python 与所有文件系统”为前置条件。


---

<!-- phases/CP00_baseline_and_failure_inventory.md -->

# CP00：还原首次打开路径并冻结基线

## 前置

阅读总提示词。将审计从参考 SHA 更新到当前 checkout；定位真实入口，不凭文件头中陈旧的 Session/spike 注释判断功能是否存在。runcli 已是产品入口，需以调用关系为准。

## 实施

1. 从桌面打开文件/文件夹、CLI open/native run、MCP open_figure、试运行探测、原图/画布导出追踪调用图；标明 discovery、trust、environment choice、cwd、deps repair、capture、replay、source materialization 的位置。标记哪些入口复用同一 pool，哪些不是。
2. 建立问题到证据的表：SyntaxError 返回 None；新项目先默认环境；script.parent 与 project.root 区别；open 补丁盲区；no-system-Python managed base 缺失；单包最多三轮；lossy requirements parser。区分源码确认、待实测、历史注释。
3. 建最小可重现项目：两个不同版本/依赖的真环境；scripts/ 与 data/ 分离；绝对数据路径；h5py 文件；本地 lab_utils；多个额外包；只有内置 runtime；新语法脚本；两个同名不同内容的数据文件。所有 fixtures 附数据版权/来源，尽量用程序生成的无敏感数据。
4. 保存当前产品的真实首开表现，包括用户必须点击什么、选择的真实 sys.executable/sys.prefix、实际 cwd、实际读到数据指示值、最终图、失败原因。使用干净用户目录，测试装置无意提供的环境单独记录。
5. 为现有可用案例冻结“原生正确运行 → Tavotto 零 override”的语义/视觉基线，并明确随机性控制仅属于测试夹具，绝不能偷偷改变用户脚本运行。
6. 复核现有 support-matrix、runtime-lock、compat corpus 和桌面 CI，不声称现有所有测试都不覆盖；新矩阵只补尚未实际覆盖的维度。

## 交付与验收

交付调用图、gap ledger、最小 fixtures、原始 FirstOpenBench baseline（真实状态，不是全改绿）。证明一个失败例确实复现，或写明本环境无法复现原因；不能把源码推断当实测。

旧测试不修改语义。CP00 可以不要求旧问题绿，但后续阶段必须有明确用例 ID 对应其修复。


---

<!-- phases/CP01_preparation_state_and_permissions.md -->

# CP01：项目准备状态机与权限边界

## 目标与依赖

依赖 CP00。建立单一编排入口，复用现有执行、修复与 readiness，不再由每个 UI 各自串联重试。这个阶段首先接上现成可用能力，后续 CP02–CP05 增强同一服务。

## 实施

1. 新建或重构 `engine` 下的 preparation 服务与可序列化模型。名称遵守仓库惯例，避免建立第二个 ExportJob 或另一个全局 worker pool。外部入口都请求同一准备计划；执行描述最终仍由 ExecutionSpec 产生。
2. 明确 preparation_id、project identity、entry、source revision、plan revision、environment generation、request origin。所有步骤产生结构化状态与稳定 code；SSE/轮询断线后可恢复，不从日志猜状态。
3. 分离只读静态发现、候选进程启动/import、脚本执行、网络解析/下载、安装、用户环境变更、真实目录写入、外部数据授权。将现有授权状态接入，不能因为打开过某个 PDF 就获得同目录脚本的任意执行许可。
4. “可信项目自动准备”是有界 policy：允许来源、项目/条目范围、下载预算、wheels-only、可变更目标、有效期/撤销、版本锁和数据根。不覆盖未来所有新依赖、新源、源码构建和用户环境改动。新 plan 超出先前 grant 时，停在 needs_permission。
5. 先展示已有素材/轻量工作区。preparation 只阻断需要科学执行的动作，不阻断静态图片浏览或已有源的合法导出。不能伪造新捕获结果。
6. 单个项目切换、取消、关闭窗口后终止对应可取消准备任务；环境创建事务与进程清理必须有 owner。不要清掉另一个项目的任务，不杀用户的 native 计算。复用 envlease。
7. 引入总体探测预算和有限并行，慢候选不串行阻塞所有用户交互；预算到期为“未完成检查/需要选择”，不是缺包证明。不要把原来的每候选 60 秒乘上任意候选数隐藏起来。

## 验收

单测状态转换与恶意/过期消息；同时打开 A/B、相同脚本名不能串状态；关闭 A 不取消 B；撤销权限后无法执行旧计划；只读扫描不启动用户代码。新服务在“已有可用 runtime”场景走到真实 worker，不能只有状态演示。

E2E 捕获 UI 不出现已被自动修复的红错闪烁；真实不可恢复失败仍明确呈现。为异步旧响应注入测试，确认旧 plan 不可覆盖新图。


---

<!-- phases/CP02_runtime_choice_and_parser.md -->

# CP02：执行前选择正确 Python，修复解析器前置漏识别

## 依赖与原则

依赖 CP01。不要求用户 Python 与应用 Python 同版本。whole-interpreter 是切换单位；保持已有显式环境覆盖权威。Python 3.10–3.14 是参考版本的运行时支持范围，不自动扩大到任意 minor/ABI。

## 实施

1. 用同一候选模型整合项目 `.venv/venv/env`、已记住且身份仍有效的环境、原 invocation、应用私有 runtime、明确授权的系统环境与管理器环境。发现与决策分离：候选来源与证据强弱进入结果；不要把“第一个有 matplotlib 的系统 Python”直接解释为正确环境。
2. 按可信且已选的项目范围读取 `.python-version`、pyproject requires-python、PEP 723 脚本元数据、现有支持的 lock/environment 声明。指定版本与范围冲突时明确报冲突，不任意丢弃一条。`.python-version` 是版本偏好/指定证据，无法单独证明包兼容；Tavotto worker 支持范围与用户项目约束求交集。
3. 为已有明确选择做健康复检；不能以自动推断覆盖它。失效明确选择应给具体原因和一键建议；若要改变既有优先级，ADR、迁移、旧用例与用户可见说明必须齐全。
4. Conda 候选不要只看 base 常见目录；在用户授权和预算内用管理器提供的机器可读环境列表。实际运行验证 `conda run` 等管理器上下文是否必要，封装 argv/cwd/env 适配，shell=False，不执行拼接 activate 字符串。Poetry/pixi/uv 只实现已验证 provider，不支持的 provider 明示，不读取配置后伪装完整兼容。
5. 科学环境 readiness 至少包含 actual version/implementation/ABI/arch、worker 模块或 handshake、Matplotlib 等级与关键声明依赖。`import` 成功与用户完整脚本可运行分开。探测在选定环境中完成，不把所有本地源码 import 一遍；探测污染/副作用可记录、限时。
6. `discover.analyze_script` 不再把所有 SyntaxError/解码失败与“不是绘图脚本”压成同一个 None。保持已知非绘图语义，同时添加 typed analysis status：parsed、parser_incompatible_or_unknown、decode_error、io_error、not_plotting。脚本库存仍可显示用户主动选择的文件。
7. 源读取遵循 Python 编码声明。应用解析器失败后，在已获授权且支持的目标 Python 中调用只解析、不执行用户代码的 helper，产出中性、版本化的分析 JSON。复用原发现算法或将其抽出到相应兼容层，不从另一个 parser 悄悄发出不同 stem/entry 规则；不能拿 `ast.parse(feature_version=...)` 当未来语法解析器。
8. 如果目标 Python 能解析而科学 adapter 不支持，不把它标为 full_support。可以显示已有产物、提供原环境运行入口或单独规划 adapter 支持扩展；不得自动升级原项目以“修复”旧 Python。
9. 新选择应进入 worker identity/generation 与缓存键；同一路径 venv 重建、包状态变化不能一直复用旧健康布尔。成本有界，使用实际 runtime/manifest 内容与必要的 stat 失效，再在 session 启动时由 worker 自报核对。

## 验收

真实不同 minor 的双环境：应用 A、项目 B，B 的专有包/语法在 B 成功；没有向 A 注入 B 的 site-packages，sys.prefix 仍为 B。显式配置不被覆盖；两个 venv 指向同一 base 二进制也不去重成一个环境。

较新语法脚本不会从库存静默消失，旧解析器失败能由支持的新解析器分析；普通语法错误仍给出真实位置，不一律归因版本。非 UTF-8 合法源码与不可解码文件分别测试。Conda 单独环境而非 base 用真实启动验证，配置发现成功不等于 import 可用。


---

<!-- phases/CP03_private_python_provisioning.md -->

# CP03：机器无系统 Python 时仍可准备兼容环境

## 依赖与技术验证

依赖 CP01/CP02。优先验证固定版本 uv + 已校验的完整 CPython 分发（或能兑现同样契约的提供者）；不是调用用户 shell 的 uv，也不是要求用户先 pip install uv。

## 实施

1. 应用附带或按已批准策略获取受验证的 provisioner 二进制；记录版本、平台、hash、来源、许可证。包下载/解压校验防止目录穿越，失败不可留下被认为可用的 runtime。
2. 完整 Python 安装在 Tavotto 数据目录，例如 `runtimes/<platform-arch-impl-exact-version-build>/`，与安装目录的 bundled 科学 runtime、sidecar 环境隔离。runtime ready 条件包含真实解释器启动、stdlib、venv 创建和依赖安装工具自检。
3. 应用控制解析后的精确版本与 ABI；不让工具默认选 prerelease、free-threaded、debug 或另一架构而没有 Tavotto 资格。原生扩展兼容由真实环境验证，不能只看 Python 文本版本号。
4. uv 候选需验证私有目录、`--no-bin` / `--no-registry` 或当前固定版本等价选项；不得改 PATH、shell rc、默认 Python、Windows 注册项或用户 `.python-version`。不调用 update-shell，不对用户项目执行 sync/lock。
5. 避免默认读任意环境中的工具配置/索引选项；显式传入已审核目标、约束、数据目录和批准的网络源。网络关闭时不自动触发解释器下载。代理/证书/索引允许通过产品设置显式配置，凭据不写进公开回执。
6. 安装走真实任务：可见进度、取消、校验、解压后验收、atomic manifest ready；重复打开同一需求复用已合格的版本，不重复下载。并发同版本只一份安装、每个请求独立持有引用；取消一个不破坏其他消费者。
7. 下载/磁盘不足/校验错/无匹配版本分别给稳定 code 与人话动作；不要统一成“Python 不可用”。离线有缓存必须真实可用；无缓存给出缺少哪些资源，不展示永远可点的安装按钮。
8. 旧 runtime 和关联环境仍被 live session 使用时不能自动删除或原地升级。GC 只清自身管理资源，应用升级不偷偷替换已绑定科学环境版本。

## 冻结包先验

尽早在真正的 Windows/macOS 产物及适用 Linux 分发形态验证：机器没有系统 Python，没有系统 uv/pip，没有 host site-packages 泄漏，依然能获取私有完整 Python、创建独立环境、装指定 wheel、跑科学 worker。仅在开发机 venv 成功不够。

## 验收

联网允许：首次确认后走通；第二次不下载。离线已有缓存成功；无缓存明确受限。取消/崩溃/坏包不 ready。用户 PATH、默认 Python、shell配置、注册项、原项目与既有环境包不被改变。把 host Python 探测路径去掉后测试仍成功，防止“没有 Python”夹具自欺。


---

<!-- phases/CP04_dependency_set_and_transactions.md -->

# CP04：联合依赖准备，替代逐包失败循环

## 依赖

依赖 CP02/CP03。保留既有单包修复作为兼容入口，但新首开准备不由 traceback 串联安装完成。

## 实施

1. 构建完整 requirement intent：distribution、specifier、extras、marker、selected group、来源文件/位置、index/source 类型、hash/lock identity。优先使用成熟标准解析器，不手写宽松正则；保持 Flask 不引入科学栈的边界，小型纯 Python 依赖按 ADR 审核。
2. 不用旧 project_declared 简化 dict 当完整环境清单。requirements `-r`/constraints 仅在授权项目内有界、循环检测地解析；不把文件内 index/URL/editable 当默认授权。pyproject optional groups 只激活用户/项目实际选定部分。Poetry caret 等不能降成裸包；不支持的 lock 版本回 typed unsupported，不当作无依赖。
3. 读取 metadata 不执行 setup.py/build hooks；静态 import 清单是提示不是完整依赖证明。分 stdlib、本地模块、第三方、可选/TYPE_CHECKING、动态未知。候选版本相关 stdlib 以目标 Python 为准。
4. 本地 lab_utils、src-layout、namespace package 与 editable 先按已授权项目/原 invocation 识别。已安装 editable 原环境可复用；新环境需要安装本地代码时是独立授权与能力，不静默触发构建。未知包名不上传到公共索引试探，不从 import 名猜。
5. 使用成熟 resolver 联合求解项目所需依赖、传递依赖及 Tavotto 科学 adapter 的兼容约束。求解失败给出冲突的两端，不擅自把 numpy/matplotlib 或用户明确锁定版本放宽。没有完整声明时结合可信高频映射给出“已识别依赖”与未知覆盖，不能承诺全依赖已知。
6. 新受管环境优先安装审核范围内的 wheels；source/VCS/editable/私有索引/编译器/GPU/系统库动作需要单独策略和提示。resolver dry-run 不是安全沙箱；必须确保元数据获取不会越过源码构建策略。不能只传一个 no-build flag 就认为所有工作区代码都不执行。
7. 不通过顺序安装 N 个包来模拟联合求解；先形成可审查锁定计划，再安装、检查依赖一致性、关键 import、真实 worker 自测，最后运行选中的用户脚本。动态遗漏可以有界补救，但不可无限循环；把不同故障分类，ValueError/TypeError 不自动安装。
8. 受管环境按版本化目标目录创建，例如 `environments/<project>/<resolved-env-id>/venv`。**从一开始就在最终路径构建**并标 incomplete；验证全部通过后原子切换很小的 active pointer/manifest。不要先在临时目录建 venv 再 rename，venv/console scripts 的路径不保证可移动。[E02]
9. 旧 active 环境不边用边更新；保留至租约释放，失败版本可清理。环境去重/共享策略不可形成可写 site-packages 硬链接串改其他项目。项目作用域与当前 managedenv 迁移保持兼容。
10. 每个安装事务绑定 plan+项目+环境身份+完整要求+授权+有效期。使用 envlease 与既有修复服务，不再造第二个 busy 表。失效、取消、异常都释放占用；不为安装强杀用户 native 会话。并发与磁盘满需真实测试。
11. 用户主动选择改自己的 venv 时明确影响和不完整回滚事实；默认路径不改它。记录实际安装结果而非只有 requested 版本；传递依赖和来源受控，脱敏账不含凭据。

## 验收

多个额外科研包在一轮计划准备后可打开，不要求用户挨个理解 pip；marker 为 false 的依赖不装，extras 正确带入，未选 dev/test groups 不装，Poetry 约束不丢，本地模块绝不误装同名 PyPI。

冲突、缺 wheel、离线、安装中断、selftest 失败时不切 active；旧环境仍可用；用户环境包记录前后一致。真实跨 Python 的 C-extension wheel 与 import 成功是必需重线，不只写纯 Python fixture。


---

<!-- phases/CP05_execution_context_and_data.md -->

# CP05：数据与真实运行上下文，而不是扩大 open monkeypatch

## 依赖与边界

依赖 CP01/CP02。本轮必需解决有证据的 cwd 与外部数据定位；不承诺跨平台任意原生代码完全透明且绝不写原件。

## 实施

1. 显式建模 project/workspace root、entry path、script `__file__`、invocation cwd、import roots、data bindings、write policy、已授权根和来源证据。不要将用户打开的图库目录自动等同于科学项目根；检测到父目录 .git/pyproject 只是候选，越出授权目录前需要确认。
2. 原 invocation 明确提供的 cwd 最高可信；项目记住的选择随后；静态字面量和目录结构可以提供候选，但不等于证明。`scripts/fig.py + open('data/a.csv')` 与 `open('../data/a.csv')` 必须给出不同解释。多个合理根不能按“哪个先找到文件”自动选择。
3. 扩展现有 workdir/ExecutionSpec 时版本化，旧 project 仍是 script.parent；需要项目根/指定已授权目录应有清晰新模型。所有 spawn 和 replay 路径只消费这一份，不在 app/MCP/worker 各自 chdir。
4. 新首开准备在脚本执行前就检查可确定的必需数据；动态路径/目录遍历/数据库输入仍可 unknown，不通过 AST 模拟科学程序。存在不等于可读，外部卷未挂载与文件缺失/权限不足分开。
5. 本来可读的绝对外部路径原样用，不拦后又假装“修复”；不存在的别机盘符路径不能靠改 cwd 解决。数据定位允许用户一次选目录；候选必须精确匹配声明相对布局/保存绑定，重名文件显示差异并确认，不自动替换实验数据。
6. 选择真实 cwd 可能让 open('w')、np.save、C++ 写入、子进程输出触及原文件。首次明确授权“按项目原目录运行，脚本可能生成或覆盖文件”，记住该项目/执行配置；不能只说“允许读取数据”却实质放开写入。保留现有 capture/guards，并报告重要被拦操作；不得宣传仅靠它们绝不改文件。
7. 执行模式至少区分：既有 safe sandbox、已确认原目录上下文、原 terminal-native。原 terminal-native 的子进程仍由 CLI 拥有；GUI 发起的受信运行若新增，必须另有准确语义，不冒充用户原 shell 的 stdin/env。
8. 真正的受保护工作副本/只读 data mounts 可做后续能力：只有 OS/backend 对原生库和子进程访问强制保护并通过用例才标 protected。普通 symlink/hardlink、把文件复制一份、打补丁不等于完整隔离。相对 __file__、多文件配套数据、绝对路径、包资源与巨大数据都必须明确支持范围；不能无条件复制整盘实验数据。
9. 对安全自动检查只读元数据和授权范围，不扫描整个 home/磁盘、不上传文件。大量文件设置有界遍历、软链接循环防护、取消；未知 coverage 在回执中保留。
10. 准備/实际执行记录 data bindings 版本与已观察身份；文件变化导致待执行计划失效或重查。现有 live Figure 是当时数据的结果，用户改数据后不暗中重跑并清空 edits；提示更新来源、明确重新计算动作。导出已冻结 source 与重算是两种行为。

## 验收

真实 worker 中验证：script cwd、project cwd、自定义授权 cwd、__file__ 数据、绝对外部数据、中文/空格/Windows 盘符及适用 UNC、多文件 h5py/原生读者、exists/glob/listdir/mmap（按真实能力）与子进程。

构造同名但内容不同的 CSV；原生程序用 A，Tavotto 必须也用 A，不能以图非空为通过。确认真实 cwd 前不执行会写原件的脚本；确认后行为与文案一致。承诺 protected 的模式必须用真实 C/native writer 尝试改原件证明；不支持则明确该模式不可用，不能放宽断言。

保留旧 test_workdir_mode 中“sandbox 下看不到 exists/glob”的行为断言；新准备层选择授权模式解决首次打开，不篡改旧合同掩盖问题。


---

<!-- phases/CP06_first_open_ui_and_native_handoff.md -->

# CP06：把兼容准备变成默认打开体验

## 依赖

依赖 CP01，逐步接入 CP02–CP05。遵守 Tavotto 现有设计 tokens 与克制风格；这不是重新设计整个设置页。

## 实施

1. 默认主视图只有一条准备状态，例如“正在准备这个项目”“正在读取实验数据”“正在生成原图”。可展开看当前环境/来源/步骤，未必要用户读技术信息。后端状态权威，不把文字/traceback 正则当状态机。
2. 对已获授权且明确的已有环境直接使用，不先抛缺包错误。完成后可折叠显示“使用项目原来的 Python；未安装或升级其中的依赖”。不要声称绝对零写入，因为运行时可能生成 cache。
3. 确需下载/新建环境时，主要动作“准备并打开”；用一两句说清私有环境、网络与大致资源范围，数据大小不能假造。受管环境和用户现有环境的改动不可合并成同一个“确定”。完整计划再展开。
4. 数据未知时一个定位动作“选择数据文件夹”，展示预期路径尾部/项目相对结构，不甩 CWD/sandbox 知识。只有用户能回答的歧义才询问；可以确定的问题自动解决。用户选完后绑定该上下文，后续不重复相同问题，身份/权限变化除外。
5. 首次成功是“正确的原图已出现且可以进行一次真实编辑”，不是准备流程显示100%。phase ready_for_execution 与 ready_editable 分开。原脚本计算慢时展示真实进度/输出摘要与取消，不长期只写“修复中”。
6. 如果已有 PDF/PNG 但科学环境受限，先打开已有图，标记“当前可排版/标注，图内编辑需准备环境”。已有缓存显示其生成时点/来源状态；不能在失败后悄悄把旧图当新图。
7. 统一 Desktop / embed/MCP / CLI 的准备结果和 code， UI 禁止暴露无法兑现操作。MCP 返回 needs_permission/needs_input 与可执行下一步，不自主越过用户环境安装/真实写权限。
8. 为已在终端成功运行的用户提供“沿用原运行方式连接”的出口，复用 tavotto run 的准确命令与当前 cwd/args。它是高级但可靠的补充，不强迫全部新手走终端。
9. 原 native 入口保留 CLI 拥有用户 Python、保留 stdin/out/err、确认后才 spawn。GUI 无法凭一个 Python 路径重建激活过的 Conda、终端输入、IDE/kernel 状态；不要标为同一种语义。Jupyter/远端连接未实现时如实说明，不能用 pip 解决内核状态丢失。
10. 无论成功/失败均可再次操作；预备过程中用户切项目、改脚本、撤销网络、退出程序，结果不污染其他会话。无障碍、键盘、窄面板、中英文、错误码本地化用原有测试规范。

## UX 验收目标（设计目标，非既有实测）

已授权且环境/数据齐备：0 次“修复”点击。新私有环境且计划完整：通常1次明确准备授权。不可推断的数据位置：1次定位；新副作用或额外不确定性不得为了达成次数而隐瞒授权。

记录 time-to-first-correct-editable-figure、用户被迫选择数、重复安装/重复执行次数；分别报告脚本自身计算时间和 Tavotto 准备开销。网络不同分组，不许拿热缓存跑分代替新用户冷启动。


---

<!-- phases/CP07_execution_receipt_and_rendercore.md -->

# CP07：执行回执接入 RenderCore，不将执行器塞进渲染器

## 依赖与接点

依赖 CP01–CP05；与 RenderCore R03/R11/R12 并行对齐。SourceResolver 的源是“已经得到的科学图产物”，不是用户数据仓库的无限快照。

## 实施

1. ExecutionReceipt 由真正执行脚本的 worker/native 自报，再由控制面核对 session/generation/plan revision。不要把前置 probe 结果原样充当实际执行事实。
2. 核心字段：receipt schema/id、source/entry revision、原/变更执行模式、Python impl/exact version/ABI/arch/runtime build identity、实际关键包版本与已知完整性、launch context identity、cwd policy/data binding revision、可观察输入身份、capture IDs/output content hashes、warnings/guard actions、observability limitations。
3. 完整机器路径、带秘密的 argv/env、数据样本、索引凭据、用户名均不进入公共 PDF XMP/Artifact Manifest/telemetry。分 local private、用户同意后的 redacted diagnostic、public provenance 三种视图。哈希也可能是可关联标识，不自动当匿名数据上传。
4. 本地依赖 .py 和数据变更会影响结果，不只 hash entry.py。能枚举的项目模块、lock、已绑定文件记录；动态导入、原生 open、mmap、网络、数据库或运行中变动可能不可完全观察，标 coverage=partial/unknown。只记到部分数据不能声明全可复现。
5. 同一热会话、重放、写回、导出必须对齐科学运行身份和已捕获状态。环境安装/重建、cwd/data mapping 改变触发现有 generation/cache invalidation/lease；不可一边显示 A 环境的 live Figure、一边拿 B 环境重放当一致。
6. 不通过把机器绝对路径塞进稳定 ExecutionSpec hash 来解决身份问题。分 semantic intent hash、local runtime execution fingerprint、source artifact hash 与 RenderCore render fingerprint；公共稳定键与本地失效键不同。
7. RenderPlan 增加 receipt/reference 与 provenance closure；仅携带渲染需要的冻结源文件、规范、字体等。若在执行前复制用户脚本到 staging，__file__/relative import/CWD 会被改掉；除非完整支持该执行模式，否则保留原执行位置并用内容/修订校验绑定实际执行。
8. 现有多格式 ExportJob、source resolver、规范化、original/canvas 不复制。纯静态 PDF 组合无需准备科学 runtime；带 override 的源由正确 worker 产生后再进 RenderCore。
9. 将错误阶段归属标准化：analysis、runtime、dependencies、data/context、execute/capture、compose、artifact-validate。missing data 不报 PDF backend failure；PDF 解析错误不触发 pip 修复。
10. 环境无论用 Python 3.11 还是3.13，RenderCore 不应被迫在该解释器安装/加载 PDF 库。R08 应用侧 raster 子进程与科学 worker 是不同责任，不能互相充当缺失工具的 fallback。

## 验收

两个真实不同 Python 生成源，交同一个 RenderCore 成功导出；科学环境没有被装入应用 PDF 依赖。回执等于 worker 自报而非预期。环境/数据绑定改变时旧结果不串用；旧 live Figure 的冻结导出有正确来源标识。

植入错误 receipt/generation、隐藏输入、敏感路径/令牌、错误源 hash，门禁必须抓到；观察不到的输入保留 unknown，不可让 validator 把 unknown 自动当pass。


---

<!-- phases/CP08_first_open_ci_and_rollout.md -->

# CP08：首次打开资格矩阵与可回退发布

## 依赖

依赖 CP00–CP07；与 RenderCore R13/R14/R16 共同验收。继续保留现有 CompatBench、等价矩阵、单测和三 Gate 分层，不拿新 corpus 替代旧测试。

## 实施

1. 实现 `FirstOpenBench`：从真实产品入口和干净配置出发，记录发现→选环境→所需授权→准备→上下文→首次捕获→真实编辑→重放→最终导出。每阶段都有 expected/observed/evidence，支持度不能只来自fixture自己写的标签。
2. 建维度分层：真实 Python minor/ABI；venv/Conda/私有完整runtime；无系统Python；脚本目录/项目根/外部根；Python/C reader；在线/离线cache；缺包/版本冲突/本地模块；安全授权/取消；桌面/CLI/MCP。
3. PR 快线运行纯契约、状态机、安全策略、选定真实最小场景；依赖下载使用固定本地 wheelhouse/受控测试服务器，确保每次同样输入。预检可 mocks，关键“首次打开成功”路径必须真实环境。
4. merge_group/full-ci 执行跨平台关键矩阵、真实不同 minor、native binary imports、no-system-Python clean artifact smoke。`needs` 与 aggregate_gate required 闭集同步，必需检查 skipped/缺失为失败，不增绕过路径。
5. nightly/lab 扩全矩阵、环境泄漏、进程cleanup、慢/断网、只读数据、真实管理器和长脚本；公共源下载可用性单独分类，不能把网络故障算产品科学兼容错误，也不能忽略实际上无法部署的来源。
6. release 测精确待发 wheel/sidecar/安装器/签名后的应用（按平台）；host Python、uv、PIL、h5py 或开发路径不能帮包补依赖。no-system-Python 要核验环境本身，不是简单设置一个变量假装不存在。
7. 收入基线/用户流失没有数据时不编数字。报告 cold/warm、阶段耗时、正确可编辑率、用户操作数、完整身份覆盖、实际错误、重复执行/安装、数据与环境改动；样本分母固定，失败/unsupported/unknown 不隐去。
8. 为核心门禁故意植错反证：默认选错Python；SyntaxError静默丢文件；用同名错误CSV；剥marker/extras；把用户环境升级；忽略cancel；stamp假receipt；跳过native deps；隐藏缺job。每个对应失败证据入 PR。
9. rollout 按能力逐步打开：先现有环境无感重用和明确CWD；再无Python私有环境与联合依赖；最后更广provider/保护工作区。不通过发布一个全局“自动修复所有问题”开关掩盖未支持场景。
10. 回退作用于准备编排版本/策略，不删除用户数据/环境或自动降级其包。兼容旧设置、旧项目、老客户端；新数据结构未知版本安全失败。Rollback 后已有原图静态查看/导出仍能用。

## 最终出口

每条 acceptance ledger 状态有真实证据；关键first-open案例没有 product_bug、错误数据、未授权改环境、假ready或无限重试。无法测试的平台明确未取得资格，不用Linux结果代替Windows/macOS。

最终报告对照原有首开基线说明哪些问题改善、哪些仍然需要用户提供数据/原命令、哪些环境未支持。拒绝用“tests 全绿”替代这一份能力说明。


---

<!-- 02_RENDERCORE_INTEGRATION.md -->

# 与原 RenderCore 实施包的合并方式

## 两项工程，三个运行责任，不造一个万能内核

```text
用户打开文件/项目/已有原生会话
         │
         ▼
ProjectPreparation（复用 engine 现有模块）
   ├─ 环境候选、约束与授权
   ├─ 完整 Python / 联合依赖准备
   ├─ cwd / 数据绑定 / 写入语义
   └─ 选择已有 ExecutionSpec 或 native invocation
         │
         ▼
科学 worker / 用户原生进程
   └─ 执行、捕获、图内编辑 → SourceArtifact + ExecutionReceipt
         │
         ▼
应用 SourceResolver 冻结该次源产物
         │
         ▼
RenderPlan → Render IR → RenderCore 合成/光栅/产物检查
         │
         ▼
Artifact Manifest + 原有 ExportJob 发布
```

应用自身 Python、用户科学 Python、应用管理的 PDF 渲染运行时，三种责任可用不同进程/解释器；模型不强迫三个完全不同安装，但不能混淆依赖归属。

## 与原阶段的关系

| 原阶段 | 本补充的接入点 |
|---|---|
| R00 / R01 | CP00 补首开基线；CP03 也要提前做精确桌面产物的最小技术验证，不等实现尾声 |
| R02 IR | 不接 Python 候选或包安装；保持纯渲染语义 |
| R03 SourceResolver/RenderPlan | CP07 引入来源回执引用；注意源产物快照与用户脚本/数据执行副本不是同一件事 |
| R04–R06 Typography | 画布字体和科学 worker 图内字体仍分开；不能为了字形一致擅改用户科学环境 |
| R07 PDF / R08 PDFium | CP03 私有 scientific runtime 不是 PDFium worker；应用渲染线程/进程池不跑用户科学脚本 |
| R09 original/annotation | 静态既有源继续可用，无需下载科学环境；带 override 才请求正确科学 worker |
| R10 validation | 产物正确性不等于科学运行可复现；Proof 不得把 receipt partial coverage 变为pass |
| R11 Manifest/Fingerprint/Trace | 绑定 receipt、环境事实、有限输入观察；本地敏感字段与公共产物隔离 |
| R12 UI/HTTP/MCP | 同一 preparation 服务，平稳等待与必要一次操作，不重复实现解析/重试 |
| R13 CI / R14 distribution | FirstOpenBench 与 RenderBench 端到端相接；科学脚本与 PDF 后端分别验依赖闭包 |
| R15 cutover / R16 qualification | 两个独立资格账；渲染器切换通过不意味着兼容模块通过，反之也一样 |
| E01 Manuscript compiler | 每张图有自己兼容上下文和receipt，允许共享经证明相同环境；不把12张图塞进同一可变环境 |

## 合并纪律

不要把 CP00–CP08 机械追加成 R17–R25，导致先等全部换库后才解决新手打开。CP00–CP02/CP05/CP06 的既有能力接入可以先行；CP03/CP04 与 RenderCore 技术验证并行。CP07 最后将两条链的事实接起来。

同一轮产品发布可以要求两套资格同时通过，但代码可分多个可审查 PR。不要求每个阶段必然单个 PR；以真实依赖与仓库合并规则为准。

不新增一份散落全仓的“AUTO_FIX=True”；每次执行由版本化计划、权限和实际能力驱动。已知副作用不能被包在一个隐藏的自动化开关后。


---

<!-- 03_FIRST_OPEN_BENCH.md -->

# FirstOpenBench：首次打开真实资格设计

## 回答不同问题的三种语料必须区分

现有 CompatBench：原生 Matplotlib 与 Tavotto 零 override、可编辑/重放能力。[C19]
RenderBench：旧/新合成与光栅、最终 PDF/PNG/TIFF 事实。
FirstOpenBench：陌生科研项目在干净安装里是否正确选环境和数据、以可接受操作到达正确可编辑图，再能正确导出。

不得将前两者清空后只留下 FirstOpenBench，也不能因它们全绿就宣布首开问题解决。

## 端到端验收路径

1. 在干净配置/用户数据目录启动指定的精确产物。
2. 通过真实桌面/HTTP/MCP/CLI 入口打开fixture，不直接调用底层函数绕过准备流程。
3. 记录授权之前发生的操作；未授权不能跑脚本/改用户环境/突破网络策略。
4. 准备过程中的实际解释器自报、cwd、dependency inventory 和数据身份与预期比对。
5. 原图出现，核对已知数据产生的 plotted values / source semantics；不只检查PNG文件存在。
6. 执行一次真实可见 patch，核对修改发生且未改未授权科学属性。
7. 新 worker 重放，核对同一环境/上下文/修改；原始脚本和不允许修改的数据未改变。
8. 将源送入新 RenderCore，最终格式和产物检查完成；receipt与hash对应本次执行。
9. 取消/关闭/重开后不留坏环境、卡死 lease 或孤儿进程；旧结果不得污染新项目。

## 最小矩阵

| 场景 | 核心断言 |
|---|---|
| 应用较新 Python、项目较旧但受支持 Python | 用项目真解释器与相应binary deps，不混包 |
| 应用解析器较旧、项目语法受支持且较新 | 静态发现不消失，目标解析器分析；普通代码语法错误不误判 |
| 只有安装包，没有系统 Python | 联网授权后准备完整私有Python；不借CI宿主 |
| 无系统Python + 已缓存runtime/wheels + 离线 | 真正成功且无网络请求 |
| 无缓存 + 离线 | 明确受限；已有PDF/PNG仍可查看，不冒充editable |
| 项目现成 .venv / 命名 Conda 环境 | 首次执行前选择，包不被升级；Conda launch env真的有效 |
| 三个以上额外包 | 联合求解/安装和一次用户确认，非逐个missing回圈 |
| marker/extras/Poetry/selected groups | 依赖语义不丢；没选的训练/GPU/dev依赖不下载 |
| 本地 lab_utils/src package | 不去PyPI猜包；原本import成功的上下文保留 |
| scripts/entry.py 与 data/ 分离 | cwd解释正确，不把script.parent当唯一答案 |
| __file__ 相对数据 + 模块相对import | 不因复制entry到缓存破坏来源上下文 |
| 绝对外部数据/盘符/网络卷 | 原路径合法按原语义；路径失效要定位，不能猜同名 |
| h5py / 其他真实原生读者 / mmap | Python open补丁以外也能在选中模式工作 |
| 两个同名不同内容实验文件 | 始终使用授权/原生正确输入；歧义需一次确认 |
| 写数据/删除/rename/子进程 | 所声明写入策略真实成立；protected必须真强制 |
| 安装中断/磁盘不足/坏hash | 不切换active，不改坏可用环境，下次恢复可解释 |
| 两项目同时准备/同环境多个消费者 | 不串状态；取消/GC不破坏其他用户任务 |
| native 正在计算时请求安装 | 不杀native、不改它的包；使用同一envlease |
| 数据/脚本/env在预检后变化 | 旧计划与实际身份不一致必须重新检查；不假报原计划已执行 |
| 脚本错误 / 不支持GPU驱动 / 网络数据未知 | 正确分类和限制，不自动“修复”科学代码 |

对每个fixture标注所需平台、Python构建、包集、运行模式、已知正确输入和参考输出。不可用“version='3.11'”的mock替代真正3.11进程资格。轻量现有test_project_env的继承host fixture可保留用于机制测试，新增资格用独立环境。[C17]

## CI 放置

PR：状态/授权/schema/派发与少量真实场景；固定小型wheelhouse/本地HTTP测试镜像；单测与语义对照独立。
merge_group/full-ci：选定的跨minor、原生扩展、真产物与断点恢复；不能deferred。
nightly/lab：更广矩阵、真实外部源可用性、provider、性能与资源泄漏。
release：精确待发产物、无系统Python干净环境、安装/升级/重开和最终图产物。

沿用 `aggregate_gate.py` 闭集、merge queue、CodeQL、安全的自托管执行边界；未受信PR不得因本测试获得宿主敏感目录/secret访问。制品、缓存按平台、锁、版本和SHA隔离；禁用科学脚本产生的真实产品遥测。

## 性能与产品指标

记录 `first_correct_figure_ms`、`first_editable_figure_ms`、`prep_overhead_ms`、`script_exec_ms`、cold/warm、网络类型/缓存状态、用户决策次数、自动重跑次数、下载字节、误选环境/输入数量、失败类型。

基线必须真实测出，不能预填“提升80%”。同脚本原生运行时间单独记录，避免把用户的长计算误认成准备开销，也不能把准备耗时隐藏在后台预热样本里。正确性/权限/未篡改数据优先于少点击的数字目标。

## 反证与防假绿

最少人为破坏以下十项并证明对应测试变红：选错Python；丢SyntaxError库存；用错误同名数据；删除marker/extras；更新用户包；混入site-packages；取消后仍ready；写假receipt；让真安装包借host Python；让required job skipped。

不得自动接受新的视觉基线，不得将未支持项计入pass。公开索引网络失败与产品bug分别统计，但发布依赖确实无法获取时仍不能声称该在线路径就绪。


---

<!-- 04_HANDOFF.md -->

# 每阶段交接模板

- 阶段 ID / 实际完成范围：
- 开始与结束 SHA / 未提交改动说明：
- 本阶段读取与复用的现有权威：
- 变更文件与对外契约 / schema迁移：
- 权限变化、是否有新增副作用：
- 真实测试命令、退出码、平台与解释器：
- 真产物/运行 evidence 文件和hash：
- FirstOpenBench 原始观测（不是目标数值）：
- 反证测试：故意破坏什么、哪项红：
- acceptance IDs 状态：pass / fail / not_run / blocked（必要的N/A须写理由，不混pass）：
- 仍不支持或未知的输入/环境：
- 对 RenderCore 阶段的影响：
- 下一阶段入口与阻塞：
- 回退方案和不能回滚的用户环境动作（应避免默认发生）：

不得写“所有测试通过”而不列运行集合；不得把文档/JSON完整性校验当真实产品验证。不能为完成某一阶段而悄悄删掉失败用例。


---

<!-- SOURCES.md -->

# 来源与证据范围

仓库固定基线：`Tavotto/Tavotto@6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。2026-09-15 通过 GitHub 连接器重新读取 main ref，仍指向该提交。表中范围为源码行或函数定位；不是聊天引用器的JSON行号。

文档中 [Cxx] 表示源码观察；[Exx] 是外部组件官方文档。设计建议由本包提出，不表示仓库已有这些模块。没有真实测试结果的陈述仅为待验假设。

| ID | 路径 | 已读/定位范围 | 用途 |
|---|---|---|---|
| C01 | `src/tavotto/engine/execspec.py` | 1–270 | ExecutionSpec、safe_spec、project模式=脚本父目录 |
| C02 | `src/tavotto/engine/projectenv.py` | 1–310 | venv范围、整解释器切换、Python支持镜像 |
| C03 | `src/tavotto/engine/pool.py` | 300–925 | 默认候选与resolve_worker_python；首开默认链 |
| C04 | `src/tavotto/engine/projectenv.py` | 310–565 | 真实probe、support_status、最多8个系统候选 |
| C05 | `src/tavotto/engine/pool.py` | should_try_project_env / try_project_env | 连接器定向搜索返回的missing_dependency触发条件；非全文件逐行审查 |
| C06 | `src/tavotto/engine/workdir.py` | 全文件 | 沙盒/脚本目录模式与项目级确认 |
| C07 | `src/tavotto/engine/worker.py` | 1–275 | 真实chdir、读取补丁、有限写删守卫 |
| C08 | `src/tavotto/engine/figcapture.py` | 430–680 | install_relative_read_fallback |
| C09 | `src/tavotto/engine/deprepair.py` | 1–240 | 单包计划、权限、最多三轮、环境目标 |
| C10 | `src/tavotto/engine/managedenv.py` | 1–230 | 项目受管环境、manifest、BASE_PACKAGES |
| C11 | `src/tavotto/engine/managedenv.py` | 300–550 | base_python排除embeddable、create_venv依赖基础Python |
| C12 | `src/tavotto/engine/runcli.py` | 1–190 | 产品native入口与终端所有权 |
| C13 | `src/tavotto/engine/envlease.py` | 1–160 | 安装/safe/native同一租约 |
| C14 | `web/src/components/DependencyRepairCard.tsx` | 1–210 | 现有人话修复UI与计划确认 |
| C15 | `src/tavotto/engine/discover.py` | 850–1040 | analyze_script SyntaxError/读取失败返回None |
| C16 | `src/tavotto/engine/depresolve.py` | 1–265、275–500 | 可信映射；简化markers/extras/Poetry；非完整锁解析器 |
| C17 | `tests/test_project_env.py` | 1–190 | 已有机制测试与host继承fixture |
| C18 | `tests/test_workdir_mode.py` | 1–230 | sandbox失败与project成功的既有合同 |
| C19 | `.github/AGENTS.md` | 1–105 | 稳定Gate、CI分层、CompatBench/等价矩阵 |
| C20 | `src/tavotto/engine/bridge.py` | 1–195 | 底层bridge原理；产品所有权以runcli为准 |

固定版本链接模板：
```text
https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/<path>
```

## 外部技术核验

- [E01] Astral uv 官方 Python versions：支持发现/下载不同Python、.python-version、requires-python；其工具能下载并不等于Tavotto支持该版本。
```text
https://docs.astral.sh/uv/concepts/python-versions/
https://docs.astral.sh/uv/reference/cli/
```
- [E02] CPython venv 官方：venv基于现有Python；不应视为可移动/可复制环境。用最终版本目录+active pointer，而非搬迁venv。
```text
https://docs.python.org/3/library/venv.html
```
- [E03] CPython Windows embedded 官方：嵌入式分发与完整开发/包管理环境职责不同；不能将默认pip管理假定为受支持。
```text
https://docs.python.org/3.14/using/windows.html
```
- [E04] Conda 官方 conda run：命名环境/路径环境与工作目录执行，是管理器上下文适配候选；不能从base环境枚举推出命名环境全支持。
```text
https://docs.conda.io/projects/conda/en/26.3.x/commands/run.html
```
- [E05] pip install 官方：支持dry-run/report等计划能力；它们不自动构成源码构建安全边界。
```text
https://pip.pypa.io/en/stable/cli/pip_install/
```

## 与已有附件的关系

此前生成的 `Tavotto_RenderCore_Full_Prompts.md` 已明确科学worker/应用runtime分离、SourceResolver、R00–R16和稳定CI Gate。本包增补上游兼容编排，不修改其PDF文字、字体、合成与最终产物检查验收。

## 审查限制

相关文件/测试为定向完整段落阅读和搜索，不声称逐行审查整个仓库。未执行Tavotto代码、构建、真实解释器矩阵、安装包或性能基准。本文中的候选依赖/目录/状态/能力均需编码阶段与当前代码对齐并验证。


---

# 验收台账（初始全部 not_run）

| ID | 阶段 | 验收要求 | 状态 |
|---|---|---|---|
| FO-001 | CP00 | 真实入口调用图完整，区分科学执行与RenderCore | not_run |
| FO-002 | CP00 | 首开旧问题有真实baseline或明确未复现说明 | not_run |
| FO-003 | CP00 | 相同输入的原生参考输出与科学数据身份可核验 | not_run |
| FO-004 | CP00 | 所有现有测试覆盖与新gap有对应关系 | not_run |
| FO-005 | CP01 | 只读metadata清点不执行用户脚本 | not_run |
| FO-006 | CP01 | 启动候选解释器/import受正确信任范围约束 | not_run |
| FO-007 | CP01 | 过期plan或撤销grant不能执行 | not_run |
| FO-008 | CP01 | 两个项目同名脚本异步状态不串用 | not_run |
| FO-009 | CP01 | 取消不破坏其他消费者或native计算 | not_run |
| FO-010 | CP01 | 已有静态源查看不被科学准备阻断 | not_run |
| FO-011 | CP01 | 总体probe预算有界可取消，超时不误判缺包 | not_run |
| FO-012 | CP02 | 真实跨Python minor选择正确且无site-packages混入 | not_run |
| FO-013 | CP02 | 用户显式选择不会被自动推断覆盖 | not_run |
| FO-014 | CP02 | 候选声明版本冲突不静默放宽 | not_run |
| FO-015 | CP02 | 同一base的不同venv仍被识别为不同环境 | not_run |
| FO-016 | CP02 | 较新合法语法不会被静态库存静默丢弃 | not_run |
| FO-017 | CP02 | 非UTF8合法源码与普通语法错误准确区分 | not_run |
| FO-018 | CP02 | 目标parser只分析不执行源码，输出stem规则同源 | not_run |
| FO-019 | CP02 | 命名Conda环境实际launch和关键import成功 | not_run |
| FO-020 | CP02 | 不支持Python/ABI不谎报full_support | not_run |
| FO-021 | CP02 | venv重建或依赖改变使健康缓存和generation失效 | not_run |
| FO-022 | CP03 | 无系统Python的真桌面产物能准备完整私有Python | not_run |
| FO-023 | CP03 | provisioner及runtime来源/hash/平台核验 | not_run |
| FO-024 | CP03 | 不改变用户PATH、注册项、shell或默认Python | not_run |
| FO-025 | CP03 | 离线有缓存成功，无缓存准确受限 | not_run |
| FO-026 | CP03 | 校验错/磁盘满/取消不会发布ready runtime | not_run |
| FO-027 | CP03 | 多消费者去重下载与引用清理正确 | not_run |
| FO-028 | CP03 | 不原地升级被live会话使用的Python | not_run |
| FO-029 | CP04 | 保留marker/extras/constraints/Poetry约束 | not_run |
| FO-030 | CP04 | 未选optional/dev/test组不安装 | not_run |
| FO-031 | CP04 | 本地/namespace/editable模块不误装同名公网包 | not_run |
| FO-032 | CP04 | 多个依赖联合求解，非连续单包安装模拟 | not_run |
| FO-033 | CP04 | worker兼容约束与用户锁冲突不静默放宽 | not_run |
| FO-034 | CP04 | 未知import不盲猜distribution | not_run |
| FO-035 | CP04 | 源码构建/VCS/私有源不绕授权 | not_run |
| FO-036 | CP04 | 受管venv在最终路径构建，active指针原子发布 | not_run |
| FO-037 | CP04 | 安装失败旧环境仍可用，未完成环境不激活 | not_run |
| FO-038 | CP04 | 用户环境依赖与bundled环境不被默认更改 | not_run |
| FO-039 | CP04 | 复用envlease且不杀活跃native | not_run |
| FO-040 | CP04 | 真实binary wheel与跨minor自测不是纯Python替身 | not_run |
| FO-041 | CP05 | script.parent/project.root/invocation.cwd准确区分 | not_run |
| FO-042 | CP05 | 旧project工作目录设置语义不变 | not_run |
| FO-043 | CP05 | __file__/相对import不被staging复制悄悄改变 | not_run |
| FO-044 | CP05 | 原合法绝对外部路径保留，不按同名重映射 | not_run |
| FO-045 | CP05 | 歧义数据需要确认且输入内容正确 | not_run |
| FO-046 | CP05 | 真实h5py/C-reader在正确执行上下文可用 | not_run |
| FO-047 | CP05 | 真实cwd写入许可在执行前明确授予 | not_run |
| FO-048 | CP05 | protected能力有原生writer/子进程强制验证或明确不支持 | not_run |
| FO-049 | CP05 | 只扫描授权数据范围，超大目录可取消 | not_run |
| FO-050 | CP05 | 数据变更不造成旧计划假成功或自动丢edit | not_run |
| FO-051 | CP06 | 已授权且齐备项目无需修复点击且无红错闪烁 | not_run |
| FO-052 | CP06 | 准备授权与改用户环境动作明显区分 | not_run |
| FO-053 | CP06 | 数据未知时单一定位出口可继续 | not_run |
| FO-054 | CP06 | ready_editable需实际图与一次真实编辑 | not_run |
| FO-055 | CP06 | artifact-only不冒充本次脚本成功 | not_run |
| FO-056 | CP06 | native仍由终端CLI持有原上下文且确认后spawn | not_run |
| FO-057 | CP06 | MCP/桌面准备状态与权限同源 | not_run |
| FO-058 | CP06 | 断线/切项目/中英文/键盘等场景保持可操作 | not_run |
| FO-059 | CP07 | ExecutionReceipt来自实际worker而非预检猜测 | not_run |
| FO-060 | CP07 | 热态/重放/写回/导出上下文和generation一致 | not_run |
| FO-061 | CP07 | 有限数据观察诚实标partial/unknown | not_run |
| FO-062 | CP07 | 公共manifest不含敏感路径/argv/env/凭据 | not_run |
| FO-063 | CP07 | 科学环境不需要新PDF依赖 | not_run |
| FO-064 | CP07 | RenderCore能消费不同科学Python生成的冻结源 | not_run |
| FO-065 | CP07 | 静态无override导出不强迫执行科学脚本 | not_run |
| FO-066 | CP07 | 故障阶段准确，PDF错误不触发装包 | not_run |
| FO-067 | CP08 | 现有CompatBench/等价矩阵/门禁不被替代或削弱 | not_run |
| FO-068 | CP08 | PR固定资源测试与真实跨平台重线分层 | not_run |
| FO-069 | CP08 | 精确发行产物在无宿主帮助环境完成首开编辑导出 | not_run |
| FO-070 | CP08 | required job缺失/skipped/取消不会假绿 | not_run |
| FO-071 | CP08 | 错误数据/错环境/假receipt/安装泄漏反证测试会红 | not_run |
| FO-072 | CP08 | 冷暖缓存与科学计算开销分离，不编造改善比例 | not_run |
| FO-073 | CP08 | 回退不删除用户数据或强改既有环境 | not_run |
| FO-074 | CP08 | 实际支持/未验证范围和失败分母完整呈现 | not_run |
