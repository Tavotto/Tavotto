# 受控依赖修复（ADR 0019，2026-08-27）

> 原文出自 `src/tavotto/AGENTS.md`「受控依赖修复（ADR 0019，2026-08-27）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

缺包时「一键装上并继续」。**动它之前先读 ADR 0019**——它是本仓库唯一一个会
往磁盘装第三方代码的子系统，边界比实现重要得多。

- **四个模块各是自己那件事的唯一出处**：`engine/depresolve.py`（import 名 →
  distribution 的可信解析 + 包名语法）、`engine/managedenv.py`（Tavotto 替
  项目管的隔离环境）、`engine/deprepair.py`（计划 / 安装 / 取消 / 验证 /
  记账）、`engine/pool.py` 增量（环境改动期间的 worker 生命周期）。
  全部纯标准库（Flask 父进程 import 链上）。
- **内置 runtime 永远不是安装目标**。它是「重装就能修」这条退路的前提。
  缺包时它只是触发器。安装目标只有两种：用户的项目 `.venv`（要明确确认）
  与 Tavotto 受管环境（我们自己的，可删可重建）。
- **第三种目标不是安装目标（ADR 0044）**：项目里没有 venv 时，接手那一步
  （`pool.try_project_env`）会把 `pool.system_python_candidates()`——老链条第四、
  五级本来就枚举的系统解释器——逐个 `probe_environment(python, module)`，结果挂在
  失败结构的 `system` 键上；`deprepair.offer()` 把健康的列成 `system_interpreter`
  目标排在最前，**采用一个字节都不装**，走项目环境 PATCH（`scope=project` +
  `module`，采用时连缺的那个包再验一次）。它刻意不进 `TARGETS`，`create_plan`
  对它一律拒绝。**不无感切换**：系统环境在用户交给我们的边界之外。探到了但
  不合格的（包有、Python 版本不支持 / 没 matplotlib / 起不来）单列
  `system_rejected`，界面要说出原因。offer 在渲染失败的响应路径上**不起任何
  解释器**——结论只读接手那一步的体检表。
- **体检的启动条件与 worker 对齐**：`probe_environment` 不带 `-I`、env 原样继承
  （`execspec.worker_argv` 起用户解释器就是这样），cwd 换成空临时目录挡住
  父进程 cwd 进 `sys.path[0]`。以前的 `-I` 关掉了用户 site 与 `PYTHONPATH`，
  `pip install --user` 的科学栈在体检里「不存在」而 worker 里明明 import 得到。
  解释器去重 / 缓存键**按路径字符串不 realpath**（`.venv/bin/python` 是指向基础
  解释器的软链接，realpath 会把 venv 与它的基础 Python 判成同一个）。
- **import 名不是包名**。只认 `project_declared` / `curated` 两档高置信解析，
  外加用户手填的 `user_specified`。**没有「同名试试看」这一档**——那是抢注
  攻击的入口。依赖声明只读：不改 requirements.txt / pyproject.toml，不
  `pip install -r`。
- **包名语法是安全边界不是输入校验**：`shell=False` 挡不住 pip 自己把 `-r` /
  `--index-url` / `--target` 解析成选项。白名单语法在
  `depresolve.parse_requirement`，安装前在 `_pip_install` 里**再验一次**。
- **计划绑定，不是 `confirmed=true`**：plan（说清楚装什么装到哪）与 install
  （只发 plan_id）分两步；执行端一个字节都不从请求体里读，且执行前重算环境
  指纹（`repair_plan_stale`）。没有计划 → `dependency_install_not_allowed`。
- **pip exit 0 不等于修好了**：验证三层——import 那个包 / import matplotlib /
  **真起一次 worker 跑通 build**（`deprepair.worker_self_test`，argv 走
  `execspec.worker_argv` 那一份，不另拼）。
- **安装期间那个环境上不许有 worker**：`pool.mutating_environment()` 先把该
  解释器上的会话全停、并让 `pool.get()` 拒起新会话（`environment_mutating`）。
  锁的粒度是**一个环境**，不是全局。装完 `pool.invalidate()` 点名作废——
  磁盘上多个包不会让已经起来的解释器看见它。
- **用户环境上的安装只进不退**：本轮禁止任何自动 `pip uninstall`。取消之后
  对用户 `.venv` **不假装完整 rollback**，如实说「可能已发生部分修改」；
  受管环境标 incomplete、下次重建。
- **隐私**：安装日志两道脱敏（pip 特有的 index 地址与 URL 凭据归
  `deprepair._sanitize`，路径与密钥走 `diagnostics.redact_text` 那一份）；
  诊断只记 `custom_package_index: true/false`，**绝不记地址**。本轮**没有加
  遥测事件**（EVENTS 扩容要升 CONSENT_VERSION 并让所有人重新同意，理由见
  ADR 0019 §十二）。
- 看护：`tests/test_dependency_repair.py`（十五条负向反证）+
  `tests/test_dependency_repair_e2e.py`（真建 venv、真跑 pip、真起 worker、
  真出图；不联网靠手工 wheel + `PIP_FIND_LINKS`/`PIP_NO_INDEX`）+ web 的
  `DependencyRepairCard.test.tsx`。
- **包管理（ADR 0038，2026-09-02）住在同一个模块的 §包管理**，没有第二套
  执行器：`create_package_job(project, op, spec)` → `run_package_job(job_id)`
  两步（签名里**没有解释器参数**，目标只有受管环境；作业绑项目 + 环境指纹）；
  `_run_pip` 是 install / uninstall 共用的流式执行器，`pip_install_argv(..., upgrade=)`
  默认 argv 一个字节没变、`--upgrade` 只给 update；`pip_uninstall_argv` 带 `-y`
  （确认在界面上）。**「内置」= `BASE_PACKAGES` + 目标环境里现算的依赖闭包 + pip**
  （`inventory()` 一次子进程读 `importlib.metadata`，`protected_distributions()`），
  卸它一律 `package_protected`；卸载作业把账上的依赖者交回去让界面二次确认。
  改动前后各记一份 freeze 快照（`managedenv.record_snapshot`，不是回滚）；改完必须
  `probe_environment` + `worker_self_test` 仍过，否则标 `incomplete`。端点
  `GET /api/engine/packages`、`POST …/plan|run|cancel`、`GET …/job`，进度 SSE
  `engine.package`。看护 `tests/test_package_management.py`（45 条，含离线真安装）。
- **包查找（ADR 0038 的 2026-09-07 修订）是这一页唯一会出网的动作**：
  `GET /api/engine/packages/lookup?name=<pkg>` → `{name, versions, latest,
  installed, source}`，`lookup_package()` 只读、一个字节都不装。**走
  `pip index versions` 而不是直连 pypi.org 的 JSON**——查找必须问安装会问的那个源
  （镜像 / 内网 index / 代理都由 pip 的配置说了算），而 numpy 的 pypi.org JSON 有
  几十 MB、10 s 读不完（实测）。`pip_index_argv` 是唯一出处、逐字节钉住；
  **`--retries 1` 是判据的一部分**：`--retries 0` 时「连不上索引」与「索引上没有
  这个名字」的输出逐字相同，离线就再也认不出来。进 argv 的包名由
  `argv_package_name()` **按常量字母表重拼**（首字符另一张表，只有字母数字），
  拼不出来就抛——校验与使用之间隔着归一化，重拼把两个动作合成一个；argv 里
  还有一个 `--`，「名字会不会被当成选项」从此不取决于名字长什么样。失败四档闭集
  `LOOKUP_ERROR_CODES`（not_found / offline / timeout / failed），网络判据排在
  「没有这个包」之前——不确定时**宁可报 offline**，反向的错误会让用户去改一个本来
  就对的包名。解析只认两行前缀，认不出一律 failed，**绝不回空版本表冒充「找到了」**；
  `installed` 只在受管环境自己回答时才有值。响应结构上没有地址 / 路径 / pip 原文，
  `source` 三档（`unknown` 不许并进 `pypi`）。唯一执行点 `_run_lookup`（也是测试的
  唯一注入点）。看护 `tests/test_package_lookup.py`（71 条，一次网络请求都不发）。
- `GET /api/diagnostics/summary`：诊断包同一份 `build_report()` 摊平成文本
  （`diagnostics.render_text`），给设置里「复制诊断」用；project 段由
  `app._diagnostics_project_status()` 与 zip 端点共用。

## 联合依赖准备（统一实施包 U04，ADR 0061，2026-09-21）

> 随 U04 新增；本节的模块是 `engine/depresolve.py` 的 intent 段、`engine/importscan.py`、`engine/depplan.py`
>（PR A：纯逻辑，不装任何东西）。事务 / 门 / 端点随 PR B / C 追加到本节。

- **声明的无损读法只有一份**（`depresolve.declared_intents`），交给 `packaging`（运行时依赖，只在
  `depresolve` 里延后 import；旧安装路径 `parse_requirement` / `resolve` / 单包 `create_plan` 一个字节不用它）。
  kind 四档闭集：`requirement` / `constraint` / `unknown`（认不出，保留原文）/ `unsupported`（认得出、不做，
  闭集 `UNSUPPORTED_REASONS`）。**两档都不是空依赖**：选中组里出现任一条，联合计划就 `blocked`，不把 `^` /
  marker / 约束剥掉偷偷继续。加一条 reason 就要在 ADR §三的「下一步」表与前端文案里说清用户该做什么。
- **`-r` / `-c` 只在项目根内有界跟进**（`MAX_DECL_FILES`；resolve 后仍在根下，软链接跳出去算越界）；缺失 / 越界 /
  环 / 超限各是一条 unsupported **留在引用它的那一行的位置**，不是忽略；读不了 / 超过 `MAX_DECL_BYTES` 的文件是
  `unreadable`（不是空）。被 include 的条目归引用它的组；同一文件按 (文件, 组, kind) 只读一次——换组或换成约束再
  include 是另一份条目。
- **组与默认选中**：文件组 id = 相对项目根的路径；pyproject 的是 `pyproject.toml:<段>`；脚本的 PEP 723 是
  `pep723:<脚本>`。默认只选任何层级的 `requirements.txt`、pyproject 主依赖、PEP 723（`default_group`）；其余组
  由项目设置 `dependency_groups` 点名；约束不分组、永远生效。
- **「需要」按 import 的上下文判**（`importscan`）：四个桶（stdlib / local / third_party / unknown）× 六种上下文；只有
  **模块层无条件**的第三方 import 是 `needed`；本地模块永远不装、unknown 永远不猜；经本地模块的 import 取两处里较弱
  的上下文。stdlib 名字表按**目标解释器**的（`depplan.target_facts`），不按宿主。
- **marker 按目标解释器求值**（`target_facts` 在目标里量 PEP 508 环境；启动条件与 `probe_environment` 对齐：不带
  `-I`、env 继承、cwd 空目录）。Flask 进程的 `sys.platform` 不是判据的主语。
- **计划的集合**（`depplan.plan`）：`requirements` = 缺的那些 distribution 的全部选中声明（extras / specifier 原样；只有
  curated 映射的给裸名）；`constraints` = 其余选中声明 + 约束文件；`adapter`（`ADAPTER_REQUIREMENTS` ↔ pyproject 的
  `worker` extra，用例钉着）**只并入受管环境**，用户 venv 不并入；版本不满足声明的已装包只报告不改（FO-038）。
  hash 模式 = 锁文件语义：整份选中集合按 `--require-hashes` 装，缺一条 hash 就 `blocked`。
- **交给安装器的字符串一律 `requirement_string()` 重新序列化**（名字 PEP 503、extras PEP 685、specifier 规范串）；
  原文不进 argv / 需求文件。
- 状态闭集 `nothing_needed` / `ready` / `blocked`，**blocked 优先于 nothing_needed**（不完整的计划什么都不缺也是
  blocked）；blocked 理由闭集 `BLOCK_REASONS`（四条）。`identity` 只由意图
  决定、不含路径（受管环境代目录按它命名，PR B）。
- 看护：`tests/test_dependency_plan.py`（语法 / include 边界 / PEP 723 / PEP 735 / Poetry / 3.10 无 tomllib 的分支 /
  上下文 × 桶 / 选择 / 计划的每一条「不装」）+ `tests/test_execution_receipt.py::TestDependencyIntent`。

### 事务：受管环境按代（PR B，ADR 0061 §五–§六）

- **受管环境按代，不再原地改写**：每次换代在最终目录 `envs/g<身份>/` 里新建 venv（`managedenv.register_generation` 先记
  `incomplete` → `create_generation_venv`），一次 pip 装**完整集合**（`generation_requirements`：adapter + 账上记过的 +
  这次的 delta）→ `pip check` → 关键 import（`probe_imports`）→ `worker_self_test` → **才** `managedenv.activate`
  （manifest 的 `active` 字段原子写，**唯一**指针，不设第二个指针文件）。任一步不过 = 这一代 `incomplete`、`active`
  不动、上一代原样可用；不把 tmp 里的 venv rename 过来；不往任何共享 site-packages 写。旧布局的 `venv/` 是隐式的
  `legacy` 一代，第一次按代时登记进 `generations`。
- **四条路一个事务**（`deprepair._run_generation`）：联合准备 `prepare()`、单包修复到受管环境 `install()`（delta 一条）、
  重建 `rebuild_managed()`（delta 为空 = 按账重建）、包管理里环境还不在时的首装。没有第二套建 / 装 / 验代码；包管理对
  **已有** active 那一代的原地 install / update / uninstall 不变。
- **锁仍是 `envlease` 那一张表**：换代拿合成 key `tavotto_managed:<项目指纹>` + active 那一代的解释器，
  `pool.mutating_environment(..., shutdown=False)`——**不收掉旧代上的 worker**、不杀 native（有活跃 native 租约就拒绝
  开始 `environment_in_use_by_native_session`）；用户 venv 目标仍是解释器路径 key + `shutdown=True`（原地，ADR 0019 §八）。
- **旧代留到没人用**：`managedenv.retire_unused(in_use=…)` 只删「不是 active、池里没 worker 用、没有 native 租约」的代；
  事务开始与提交后各试一次；删不掉的留到下次。
- **argv / 文件唯一出处**：`pip_install_joint_argv`（`-r` / `-c` 指向 `write_plan_files` 从解析结构生成的两份文件，每行过
  `parse_intent` 形状关 + `requirement_string` 重新序列化，`--hash` 只在需求文件里；其余参数与单包路径逐字相同、没有
  `--upgrade`）、`pip_check_argv`。用户 venv 的联合安装同一份 argv、只装 delta、不并入 adapter。
- **计划绑定**（`JointRepairPlan`）：项目 / 脚本 / 完整需求 / 约束 / hash / 目标类型 / 环境指纹（含 active 代号）/ 目标事实
  digest / 组 / 有效期；执行只认 `plan_id`，执行前重算指纹（`repair_plan_stale`）；不是 `ready` 的计划拒绝绑定
  （`dependency_plan_blocked` 带 `joint` 载荷——blocked 的理由与 `nothing_needed` 都在里面）；建代前查磁盘
  （`package_disk_low`）。
- **取消的接受时刻**（D11）：拿锁前 / 建 venv 与 pip 期间（kill）/ 验证期间 → `cancelled`、这一代 `incomplete`、目录留给
  同身份下次重建；**提交点（切 active）之后拒绝**（`cancel_status` → `committed`，`progress()` 带 `committed: true`）。
- 失败码新增三条：`dependency_consistency_failed`（pip check）、`dependency_hash_mismatch`（require-hashes 不符，
  `classify_pip_failure` 排在冲突之前）、`dependency_plan_blocked`。
- 看护：`tests/test_dependency_transaction.py`（argv / 文件 / 集合钉字节；代的登记 / 切换 / 旧布局 / 退役；**真**事务：
  三包一次成代、二开不重装、marker 为假与未选组不装、声明冲突与求解器冲突各停在切 active 之前、无 wheel / 坏 hash /
  取消 / 自检 / 一致性 / 关键 import / 只读目录 / 磁盘不足各不切 active、两项目并发独立、native 租约不杀、旧代留到没人用、
  重建成新一代、单包修复走同一事务、用户 venv 原地不并入 adapter、过期计划拒绝）。

