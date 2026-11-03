# ADR 0114：环境「建议 → 检查 → 采用」三个动作，首次由用户确认；有效旧选择不重新询问

日期：2026-10-05 · 状态：**Accepted**（产品目标由「项目 Onboarding × 执行主链路收敛」包给定，编排者 2026-10-05 裁决立本 ADR）
· **2026-10-06 修订**：用户裁决「环境不让用户选」——默认改为自动检测（§六），§一～§二 的确认流程改为可选模式
· **2026-10-23 修订**：维护者裁决「换 Python 版本不问、直接换」——版本不合时自动另建合适版本的 Tavotto 管理环境（§七）
修订：[0057 首开的环境与工作目录](0057-first-open-environment-and-workdir.md) §一（解析解释器第 4 档「首开发现 + 体检 + 自动采用项目 venv」）；
[0079 缺包时先找用户自己的 Python](0079-user-environment-discovery-and-auto-adoption.md) §四（跑前的门里**直接**改用）；
[0107 缺包时无提示改用这台机器上装着它的 Python](0107-silent-system-interpreter-adoption-and-native-workdir.md) §一（运行后缺包的无提示采用，含项目 venv 那一层）
**不修订** 0107 §二（用户自己的 Python 默认在脚本目录里跑）：那条读的是「项目此刻生效的是不是项目外的用户解释器」，
用户确认采用的同样成立
相关：[0018 项目 Python 环境解析](0018-project-python-environment-resolution.md)、[0044 系统解释器作为修复候选](0044-system-interpreter-as-repair-candidate.md)、
[0053 准备接口](0053-foundation-contracts-and-preparation.md)（公开投影不带机器路径）、[0019 受控依赖修复](0019-controlled-dependency-repair.md)

## 问题

三处代码都在**替用户**把一个候选环境记成本项目的决定：

| 位置 | 触发 | 写什么 |
|---|---|---|
| `pool.resolve_worker_python` 第 4 档 | 第一次解析解释器 | `remember(automatic=True, trigger=first_open)`，先**体检**项目 venv |
| `deprepair.decide_environment` → `_auto_adopt` | 跑前的门 / `pool.acquire` / `plan_for` | `remember(automatic=True, trigger=user_environment)`，先问登录 shell、体检一批候选 |
| `pool.try_project_env` / `_adopt_system_interpreter` | 运行后缺包 | `remember(automatic=True, trigger=missing_dependency)` |

后果：**推荐与采用是同一步**。用户没点过任何东西，项目就已经被绑到某个解释器上；为了「显示一个推荐」，候选解释器
（可以执行任意启动代码）和用户的登录 shell 已经被运行过；`GET /api/engine/environment` 在项目已记住解释器时每个进程
还要再起一次它做 `import matplotlib` 复检（T02 实测）。同一路径上被删了重建的环境，落盘的记录仍把它当成当初确认的那个。

## 决策

### 一、三个动作，三种授权

| 动作 | 授权 | 会不会起候选解释器 | 写什么 | 入口 |
|---|---|---|---|---|
| **建议** | 无（纯读） | 不会 | 无 | `envadvice.recommend()`；`GET /api/engine/environment` 的 `project.recommendation`；准备会话的 `environment` 检查项 |
| **检查** | 用户明确点了「检查」，带范围 / 预算 / 取消 | **只起被点名范围里的候选**；登录 shell 只有 `include_login_shell` 才问 | 进程内体检结论（键带环境代） | `envadvice.check()`；`POST /api/engine/environment/check`，`DELETE` 同一路径取消 |
| **采用** | 用户明确点了「使用」 | 现场再体检所选的那一个 | 项目设置里一条 `automatic=False` 的记录，带 `generation` | `PATCH /api/engine/environment {scope: project, candidate, expected_generation}` |

**检查 ≠ 采用**：检查不写项目设置；采用也不隐含安装授权。**使用环境 ≠ 修改环境**：采用只写一条记录，没有任何
`pip`；内置 runtime 始终只读；缺包时「往哪里装」仍归 `deprepair` / `managedenv` 在它自己的确认里授权（0061 / 0019）。
从内置建议切到受管环境之前要显示实际影响并确认——那是依赖准备（T06）的确认，不在执行器内部悄悄切换。

### 二、推荐只看证据层次

`envadvice` 的标签（闭集，同时是排序）：

1. `selected` —— 仍有效的显式选择（用户采用过、路径还在、环境代没变）；
2. `remembered_legacy` —— ADR 0114 之前机器替用户记下的（见第五节）；
3. `project_hint` —— 项目声明 / 编辑器指向的：项目 venv、`.vscode`、`.python-version`、`environment.yml`、shebang；
4. `checked_compatible` —— 用户机器上**已被明确检查**且健康的环境；
5. `machine_hint` —— 终端 / Conda / pyenv 的落盘线索，**未检查**；
6. `bundled` —— 内置 / 默认链条基线（只读，永远可选回；选回 = `remember_default`）。

未实际检查的写 `unchecked`，绝不冒充 verified。**Python 更新、装的包更多都不是优先理由**（这与 0079 §三 `userenvs.rank()`
的「Python 新的优先」不同——那是在「装齐」的候选里自动挑，本 ADR 之后只在兼容开关下仍用）。已知不能用的状态
（`unsupported_python` / `no_matplotlib` / `worker_import_failed` / `unusable` / `missing` / `changed`）三种体检结论
分开报，因为出路不同：换 Python / 装 matplotlib / 修环境（DLL、ABI）。

**什么时候需要用户先决定**（`decision.needs_decision`）：项目里有**项目范围**的环境线索、没有任何项目级决定、没有全局显式选择
压着。机器范围的线索（Conda、pyenv、系统）不在首次打开时提问——它们在缺包时随依赖门的候选表出现（那里本来就列「改用这个环境」，
由用户点）。准备会话据此把 `environment` 检查项写成 `needs_action`（phase `awaiting_confirmation`，没有 `run` 动作）；回答走
采用端点或选回内置，答完 `recheck`。

当前范围的 Python 要求取自支持矩阵的运行时镜像（`projectenv.PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE`，不抄第三份）；`.python-version`
只是**线索**（`python_requirement.declared`），不被伪造成完整兼容区间，`status` 保持 `unknown`。

### 三、什么时候可以起候选解释器

* **永不**：`GET /api/engine/environment`、导入即扫描（T02）、`envadvice.recommend()`、`plan_for` 里的环境证据、`pool.resolve_worker_python`
  的第 4 档。`GET` 现在只 `stat` 项目记住的解释器（`pool.peek_project_resolution`：路径在不在、环境代变没变），不 `import matplotlib`。
* **明确的检查动作**：`envadvice.check()`，候选数上限 `CHECK_MAX_CANDIDATES`、整次时限 `CHECK_DEADLINE_S`、每个候选
  `projectenv.PROBE_TIMEOUT_S`；取消在两个候选之间生效；同一项目同一时刻只有一次。
* **用户自己的运行触发的有界检查**（deliberate-boundary，沿用既有行为）：运行后缺包时 `pool.try_project_env` 为了
  把「改用 .venv / 这台机器上的某个 Python」列进修复卡片而体检候选（每个 build 每对 (项目, 脚本) 最多一次、
  `SYSTEM_PROBE_LIMIT`）；跑前的依赖门为了列出「已装齐」的用户环境而体检候选。两者**只列不采**，且都由用户已经发起的运行
  触发。确认模式下这两处不再问登录 shell，只读检查动作已经问出的答案（`userenvs.discover(ask_login_shell=None)`）。

### 四、采用绑定候选身份与环境代

* 候选 id 是 `userenvs.env_id`（目录 + 真实文件，**不按 realpath 合并**——同一 base 的两个 venv 是两个候选，E02）；路径只来自
  本机自己的枚举，调用方给的 id 换不回路径就是 `environment_candidate_gone`。
* **环境代**（`projectenv.environment_generation`）= 解释器路径本身的 `lstat` + venv 根上 `pyvenv.cfg` 各自的 (inode, mtime_ns, size)
  摘要：重建换代，装包 / 升级内部文件不换；**刻意不含 ctime 与权限位**（`chmod`、macOS 扩展属性、备份工具会悄悄改它们，
  拿来判换代会把用户确认过的环境误判成被重建）。采用时存进记录；用户看到建议那一刻的代作为 `expected_generation` 随采用提交，对不上 → 409
  `environment_changed`，不采用另一个环境。
* **最终 resolver 校验同一选择**：`pool.resolve_worker_python` 第 3 档在记录存着环境代而路径上已是另一代时——用户选的 →
  `project_python_unusable(reason=rebuilt)`，请他重新确认，**不降级、不换别的**；机器记的 → 作废。准备计划记下计划那一刻的
  环境代（`plan.environment.generation`），起会话之前 `_stale_reason` 再比一次：确认之后、spawn 之前环境被换成另一代 →
  `preparation_plan_stale / environment_changed`，一行用户代码都不跑。会话指纹含环境代与 `needs_decision`：实质变化 → 配置修订
  加一、旧动作作废；检查留下的结论、时间戳、进度都不在指纹里，不反复确认。
* **全局显式选择压着时不假装能采用**：环境变量 / 设置里的全局解释器压过一切项目级决定，此时采用返回 409
  `environment_locked`，建议里 `decision.locked_by` 如实给出是谁锁的——不把记录写到永远不会被使用的目标。

### 五、兼容开关与迁移

* `TAVOTTO_ENV_ADOPTION=auto`（`projectenv.silent_adoption_enabled()`）：三个静默采用点的**唯一**开关，保留一版，给还没有可确认界面的
  无头环境用（CI、脚本调用）。默认不设 = 确认模式。它不是第二套逻辑：开着时走原来那几条路径，关着时它们只产出建议。
  与 `TAVOTTO_USER_ENV_DISCOVERY=0`（连发现 / 体检都关掉）是两回事，两个都生效。
  退出条件：T10 给 MCP / CLI 的显式采用参数与能力协商落地（**已满足**，ADR 0117 §三），且一版发布后删除。
  T12 复核写死「一版」的判据：带确认模式的那个正式版本发布之后的**下一个**正式版本里删，前提是 ① 期间没有登记「无头 / CI 只能靠
  静默采用」的 issue（有就先给那条路径显式采用参数，不延长开关）；② 仓库 CI / nightly 里没有任何 job 设置这个变量
  （`grep -rn TAVOTTO_ENV_ADOPTION .github scripts tests` 只剩开关自己的用例）。删除时一并删：`projectenv.silent_adoption_enabled()`、
  三个采用点的 auto 分支、`pool.first_open_outcome` / `plan.environment.discovery`、`test_first_open_environment` /
  `test_user_environments` / `test_project_env` 的文件级开关；`legacy_auto` 的**读者**不删（已落盘的记录仍要被识别成「未确认」）。
* **迁移：已采用 / 已记住的项目不重新询问**。`automatic=False` 的记录就是用户的显式确认（`consent=confirmed`），继续有效；
  没有环境代的老记录不追溯（`generation_changed` 对它回 False），下次用户重新采用时才写入环境代。
  `automatic=True` 的历史记录**证明不了用户确认过**：继续照用（迁移不终止已有运行、不改 native 的原调用，也不让用户突然被问一次），
  但授权来源是 `legacy_auto`，建议里标签是 `remembered_legacy`，不当作显式确认；用户在建议上点「使用」它之后才变成 `confirmed`。
* 新导入、无任何项目级决定的项目：只给建议，不采用。

### 六、修订（2026-10-06，用户裁决）：默认改为「自动检测，能跑就用」，确认模式改为可选

用户原话：「我其实更希望能够智能检测环境，如果一个环境可用就不要让用户选。选不到环境就直接回退到让用户安装安装包。
减少用户的选择，小白用户根本不知道环境是什么意思。」——本节修订 §一 的「采用 = 用户明确点了『使用』」与 §二「什么时候需要
用户先决定」作为**默认行为**；§三（零执行）、§四（环境代、全局锁、计划过期）、§五 的迁移规则照旧成立。

* **模式**（`projectenv.adoption_mode()`，判据唯一出处）：`detect`（默认）/ `confirm`（§一～§五 原样，给想自己挑的人：
  环境变量 `TAVOTTO_ENV_ADOPTION=confirm` 或全局设置 `worker.environment_adoption = "confirm"`，环境变量优先）/ `legacy`
  （§五的兼容开关；旧名 `auto` 同义保留，退出条件不变）。`silent_adoption_enabled()` 现在是「机器可以替用户记决定」
  （detect / legacy），旧三处里只属于 legacy 的那条（第 4 档只看健康就采用项目 venv）改读 `legacy_adoption_enabled()`。
* **什么时候检测**：只在用户发起准备 / 运行之后——准备会话的检查（`preparation.plan_for`）与起会话前的
  `pool.acquire`（编辑已知的图、MCP）都经 `pool.ENVIRONMENT_DECIDERS` 里那**一个**决定者 `deprepair.decide_environment`；
  它在检测模式下就是 `_detect_environment`。扫描、`GET /api/engine/environment`、`envadvice.recommend()` 仍零执行；运行目录那道门
  还要先问时也不检测（与旧模式同一个顺序：没答之前一个进程都不起，答完 `recheck` 再检测）。
* **「能跑这个脚本」**：解释器健康 + 联合计划里脚本要的 import（缺的、已有的、映射不到包名的）全都 import 得到——与
  跑前的门、依赖弹窗的「装齐」同一个判据（`userenvs.evaluate` + `deprepair._plan_imports`），不另写。
* **挑选顺序**：项目自己的线索（项目 venv / `.vscode` / `.python-version` / `environment.yml` / shebang）→ 默认链条
  （内置；能跑就不换，也不去体检机器上别处的环境）→ 其余按 `userenvs.rank()`（登录 shell → 名字对上项目的 Conda → 其余
  Conda / pyenv → 系统；同档 verified 优先、Python 新的优先，ADR 0079 §三）。能跑的写一条 `automatic=True,
  trigger=auto_detected` 的记录（授权来源 `auto_detected`，建议里的标签 `auto_detected`）。
* **都不能跑**：不采用任何环境，报告里只有依赖检查项的一条待办（`dependency_authorization`）与一个动作
  `prepare_dependencies`——同一个动作 id、同一份影响摘要授权（ADR 0115），目标是 Tavotto 管理的环境；没有 Python 时
  同一个动作带私有 Python 供应（ADR 0111）。检测从不把用户的环境当成安装目标：检测自己记下的那条后来跑不了、别处也
  没有能跑的 → 作废它（`invalidated.reason = cannot_run`），安装于是落到受管环境。
* **不动的**：全局显式解释器（环境变量 / 设置里指定的）、用户为本项目选过且仍有效的（含装包后记下的受管环境）、明确
  选回内置的——`pool.machine_chosen_interpreter` 为 False 的一个都不碰；历史 `legacy_auto` 记录能跑就照用，跑不了
  也不被检测作废（装包仍按既有路径）；项目有依赖安装在跑时不改决定（`unless_installing`，与采用同一把锁）；正被安装占着的
  环境不挑（`envlease.is_mutating`）。
* **A 失效（不在 / 被重建 / 体检不过）**：检测模式下 `resolve_worker_python` 第 3 档不再停下报
  `project_python_unusable`，一律作废（`pool._stops_when_unusable`）后重新检测；换到的另一个是**检测的新决定**：作废的事实
  进 `invalidated_decision()`，报告的 `environment.replaced.reason` 说出来；会话指纹含解释器与环境代，修订加一、旧动作作废；
  确认之前做的计划由 `_stale_reason` 拒绝（一行用户代码都不跑）。§四对「静默换成 B」的保护于是变成「换可以，但必须是看得见的
  新决定、旧授权全部作废」。
* **2026-10-10 补充（Codex 安全 #820）**：「用户发起准备之后」不等于「用户点了运行」。项目自己说了算的候选（项目 venv、`.vscode` /
  shebang 指向的路径、路径落在项目根里的）在检查阶段只读体检缓存、不起；点「运行」才体检与采用（见 `docs/rules/backend/dependency-repair-and-packages.md`）。
  点「运行」即同意：采用后就地重算计划继续跑，不要求重新检查。首查有还没体检的项目候选时主动作是「运行」（不先推去受管环境装包），
  体检跑不了才回到常规的安装待办。
* **预算 / 单飞**：候选上限 `userenvs.PROBE_LIMIT`、并发 `PROBE_WORKERS`、每个候选 `projectenv.PROBE_TIMEOUT_S`；同一项目
  同一时刻一次检测（`deprepair._detect_lock`），后到的等它落地再按落地后的记录判断。检测**不可中途取消**（有界；§三的明确
  检查动作仍可取消）。`TAVOTTO_USER_ENV_DISCOVERY=0` 关掉检测（测试进程默认关）。
* **报告**：会话报告多一个顶层 `environment`（= 计划的 `environment.adoption`，`envadvice.adoption_fact`）：
  `{mode, kind ∈ builtin|project|user|managed|locked|null, decided_by ∈ user|auto|default|locked, switched, replaced}`——
  只是可展示的事实，不要求用户动作。建议里的 `decision.needs_decision` 只在确认模式下可能为真，`decision.mode` 给出模式；
  检测模式下不出现 `environment_choice`，界面不出现「确认采用」。
* **迁移**：`confirmed` / `legacy_auto` 记录照旧有效；确认模式下写的、仍有效的显式选择就是用户的选择，检测不碰。
  不新增持久化格式（记录多一个 trigger 值）。

### 七、修订（2026-10-23，维护者裁决）：版本不合时不问，直接另建合适版本的管理环境来跑

维护者问答：「换 Python 版本要不要每次问？」→ **不问，直接换**。理由与 §六 同源：用户不懂「解释器版本」，问了也答不出；
而换的只是 Tavotto 自己的东西。因此在下面的护栏内，版本不合是**自动修复**，不是待用户回答的卡片：

* **只在 Tavotto 自己的管理环境里另建**（`managedenv` 的新一代，`deprepair` 的代事务；`engine/pyversionfix.py`）。**绝不**改用户的
  环境 / 系统 Python / conda——原环境原样保留（它不是安装目标，ADR 0019 §一 / 0044 不变）。新一代验过才切 active、
  并经现有 `projectenv.remember` 记住为本项目的解释器（「同一脚本记住这个环境」）；失败 / 取消不留半成品（代事务未提交的一代
  标 incomplete，active 不动）。
* **基础解释器只有一个挑选处**：`managedenv.base_pick(within=)`（`base_python` 的带版本、带范围形态）——先找合格的系统 Python（范围内
  取最高），没有再用私有 Python（ADR 0063 / 0111）。私有 Python 的锁里只有**一个**版本：不在需要的范围内就不是候选，不凭空下载别的版本。
  下载字节如实进计划（`download_bytes`，已就位 / 缓存为 0）、过程走现有进度通道、可以停止。
* **只针对「版本不合」这一类失败**：`project_env_unsupported_python`、`userenvs.REJECT_CODES` 的 `python_out_of_range` /
  `root_python_mismatch`（带 `root_python` / `env_python`，#1333）、`runcodes.UNSUPPORTED_PYTHON_VERSION`。其余失败码一概不碰。
* **需要的范围必须推得出，推不出就不自动换**（交给后续的 AI 修复 / 卡片，不猜）：起点是失败本身的事实（ROOT 为哪个小版本编译）
  或 Tavotto 的支持区间，再与脚本 PEP 723 / 项目 `pyproject.toml` 的 `requires-python` 求交（`pyversionfix.required_span`）；读不懂的约束、
  空交集、环境其实已在范围内，都是推不出。在范围内挑 Tavotto 支持的**最高**版本。
* **每次运行最多自动换一次**（`prepsession.Session.python_change_run == ledger.run`，防循环）；只对桌面窗口发起的运行生效
  （改磁盘上的环境是桌面用户的授权边界，同 ADR 0127 的 native 闸门）；用户点过取消、全局显式解释器压着、运维开关
  `TAVOTTO_AUTO_PYTHON_CHANGE=0` 都不换。
* **新环境里补依赖走现有联合计划**（`create_joint_plan` → `prepare`），名单内包的口径不变：`deprepair.pinned_base()` 是线程局部的钉子，
  把新一代的基础解释器钉成上面挑出的那个，计划按它的 fresh venv 量事实（与干净机器同形），目标强制 `TARGET_MANAGED`。
* **取消在规划前就挂上；补丁位按完整版本判**：取消事件在运行开始时就挂在会话上（`Session.python_change_cancel`），规划（探测系统解释器）
  期间点停止也能置上，规划返回后、执行前、执行内每个阶段边界都查它，已取消就不下载、不建环境、不激活。`requires-python` 里带补丁位的子句
  （`>=3.11.2`、`==3.12.1`）区间装不下，原样记下，选解释器时对候选的**完整版本**（探测本来就带「主.次.微」，不多起进程；私有 Python 用锁里的精确版本）
  用 `packaging.specifiers` 判；拿不到完整版本而约束含补丁位 = 不满足，宁可不换。
* **进账本，报告看得见**：`attempt_history` 多一种条目 `kind: "python_changed"`（from / to / base / download_bytes / outcome，不占回退预算）；
  报告顶层 `python_change` 带阶段与进度；换版本期间 phase 是 `preparing_environment`（`outcome.reason = python_change`），
  卡片一句话「正在换成 Python 3.12（下载约 N MB）…」+ 进度 + 停止，完成后照常出图，详情里留「换成了 Python 3.12」。
  界面不出现「执行器」这个词。

调用点：`prepsession._done_callback` 里 `_plan_python_change`（只此一处；fixrouter 的 `remedy.kind == 'change_python'` 之后从同一处接入）。
看护：`tests/test_python_version_fix.py`（范围推断 · 基础来源与下载披露 · 只在管理环境另建 · 取消 · 真代事务与用户环境摘要不变）、
`tests/test_python_version_fix_session.py`（会话：换完重跑一次、至多一次、取消、失败保留原结局、非桌面不换）、
`web/src/components/PreparationCard.test.tsx`（进度句 · 停止 · 完成）。

## 不做的事

* 不新增 resolver / 安装器 / worker / 前端可运行判据：解释器怎么选仍只有 `pool.resolve_worker_python`；候选证据复用
  `projscan.environment_evidence`（T02）；前端 `envStore` 保存后端的 `project.recommendation` 投影，不自写「能不能跑」。
* 不往用户环境里装包；不改内置 runtime；采用不动环境文件树（`tests/test_environment_adoption.py` 用文件树指纹钉着）。
* 不做「同配置成功记录」这一层证据（当前没有按配置索引的成功解释器记录；回执里有 `interpreter` 但无反查入口），列入未实现，不伪造。
* 不改 MCP / CLI 的协议：确认模式下它们拿到的是带 `project_env.recommended` 的结构化失败；显式采用参数与能力协商归 T10。
* 不推翻 0107 §二、0047、0061（联合依赖）、0063/0064（私有 Python）。

## 看护

`tests/test_environment_adoption.py`：GET / 建议 / 首次解析零候选进程（哨兵 + 进程入口桩）；确认模式下首次解析、`decide_environment`、
缺包接手都不写记录；兼容开关保留旧行为；检查只起被点名的、预算 / 时限 / 取消 / 单飞；三种不能用的体检结论分开；证据层次而非 Python 版本
决定推荐；采用 → 实际 worker 的 `sys.prefix` 与独立身份一致、同 base 两个 venv 分得开、手选非推荐环境热态与冷重放都不偷换；环境代对不上
409 且零副作用；重建后显式选择停下重新确认、旧计划被拒；全局锁定点名来源；`candidate` 只从本机枚举换路径；采用不改动 venv 文件树、不碰安装入口；
历史自动记录照用且不算确认。`tests/test_environment_session.py`：会话里的 `environment_choice` 要求、选回内置 / 采用后的修订与旧动作作废、
重建后新修订、报告不带机器路径。旧机制的看护（`test_first_open_environment.py`、`test_user_environments.py`、`test_project_env.py` 及
`test_preparation_api.py` 里的一条）在文件级打开兼容开关继续钉着。
`tests/test_environment_autodetect.py`（§六）：默认是检测模式、确认模式可选、旧名 `auto` = 旧三处；扫描 / GET / 建议零执行且不问；
默认链条跑不了时挑能跑的里最好的、能跑时只让项目自己的线索排在前面；都不能跑不采用、作废检测自己记下的；用户选过的 / 选回内置 /
全局锁定不碰；安装在跑时不改决定、被安装占着的环境不挑；真 venv：能跑的项目 venv 直接用、报告只说一句事实、worker 起在它里面；
跑不了的项目 venv + 内置也缺包 → 只有一条安装待办与 `prepare_dependencies`（目标受管环境），装完同一会话出图；确认过的环境被重建
→ 重新检测、报告说出「被重建」、修订加一、旧动作与旧计划都不跑。确认模式的用例（`test_environment_adoption.py`、
`test_environment_session.py`、`test_engine_capabilities.py`）在文件级设 `TAVOTTO_ENV_ADOPTION=confirm` 原样钉着。
