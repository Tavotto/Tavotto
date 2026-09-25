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
- **跑前的门先找用户自己的环境（ADR 0079）**：联合计划 `ready` 时，`deprepair.user_environment_offer()`
  拿 `engine/userenvs.discover()`（项目线索 / 登录 shell / Conda 全部环境 / pyenv 全部版本，只读磁盘记录）
  + 老链条系统解释器，逐个 `probe_environment(python, modules=缺的 import)`——**装齐按 import 判**，
  「环境健康」与「装没装齐」分开报；正缺包的解释器不体检。装齐的里按 `userenvs.rank()` 挑最好的
  （来源档位 → verified → Python 新），**此刻的解释器是机器替用户挑的**就 `remember(automatic=True,
  trigger=user_environment)`、发 SSE `engine.environment_adopted`；**换不换只在 `deprepair.decide_environment()`
  一处，且在解析解释器之前**：`preparation.plan_for` 拍快照之前、`pool.acquire()` 查租约（`is_mutating`）之前
  （`pool.ENVIRONMENT_DECIDERS`，锁外）——`gate()` 只读；正被改动（`envlease`）的环境不采用（Codex #522 两条 P1）；显式全局选择 / 为本项目挑过的 /
  明确选回默认 / 项目 venv / 干净机器一个都不碰（判据唯一出处 `deprepair._auto_adopt_allowed`）。
  公开载荷与 SSE **不带路径**（ADR 0053 §二），只带 `userenvs.env_id()`；采用走 `PATCH
  /api/engine/environment {scope: project, user_environment: id, script}`，后端用自己的发现结果换回路径，
  找不到报 `user_environment_gone`；换回来之后按此刻的计划重新量装没装齐（`deprepair.recheck_user_environment`，
  不读也不写体检缓存；量的是 `missing` + `satisfied` 全部，不是此刻解释器的差集），缺就 `user_environment_incomplete`、
  计划算不出来就 `user_environment_unverifiable`，都不记（Codex #522 / #562 P2）。`pool.acquire()` 锁外窥视说能复用、
  锁内却要重建时出锁补上决定再来一遍，锁内起会话前再查一次租约；自动改用写记录用 `remember(only_if=…)`，
  「项目记录允不允许自动换」与写入在 `projectenv._decision_lock` 里一起判，判完到写之间落地的用户决定不会被盖掉。发现 / 体检缓存挂在 `projectenv.RESET_HOOKS` 上随 `reset_cache()` 一起清
  （`projectenv` 不 import `userenvs`：它要 import 本模块的体检）。`TAVOTTO_USER_ENV_DISCOVERY=0` 整个关掉，
  **测试进程默认关**（`tests/conftest.py`，否则用例结果随 CI 机器上碰巧装了什么而变）；`script` 可来自请求体，
  `discover()` 先过 `projectenv.contained_path()`，下游只用净化器回的值（CodeQL `py/path-injection`）。
- **体检的启动条件与 worker 对齐**：`probe_environment` 不带 `-I`、env 原样继承
  （`execspec.worker_argv` 起用户解释器就是这样），cwd 换成空临时目录挡住
  父进程 cwd 进 `sys.path[0]`。以前的 `-I` 关掉了用户 site 与 `PYTHONPATH`，
  `pip install --user` 的科学栈在体检里「不存在」而 worker 里明明 import 得到。
- **体检的主语是 worker 的启动导入链**（#435）：`_PROBE_SRC` 执行的是 `worker.py`
  这个文件本身（`spec_from_file_location` + `exec_module`，目录由 `projectenv.ENGINE_DIR`
  给，用例可指到假引擎目录），不是一份手抄的 `figcapture, manifest, overrides` 清单——
  清单只在写下的那一天与 worker.py 相同，而 worker 还要 `matplotlib.figure`（→ Pillow）、
  `figsession`、`wireproto`；也**不是 `import worker`**：那个解释器的 sitecustomize / .pth
  若已经 import 过一个不相干的顶层 `worker`，import 语句拿到的是缓存里那一个、体检就绿了
  （评审 #443 第十轮；真 worker 是 `python worker.py` 起的，与体检一样执行的是文件）。
  matplotlib 在、worker 起不来单独成码 `project_env_worker_import_failed`（`error`
  带断在哪一句），与「没有 matplotlib」是两条出路。**全局「渲染环境」
  （`PATCH /api/engine/environment` 不带 scope）与项目路径走同一份体检**，
  只是回给界面的 code 不同（`interpreter_unsupported_python` /
  `interpreter_no_matplotlib` / `interpreter_worker_import_failed` /
  `interpreter_unusable`）；路径先绝对化（`os.path.abspath`，**不 resolve**——venv 的
  python 是软链接，落到真身就丢了 venv）再体检、再存：相对路径按 Flask 的 cwd `is_file()`
  判得过，体检却在空的 scratch 目录里 spawn，ENOENT 会被报成 `interpreter_unusable`
  （评审 #443 第十四轮）。以前全局只问一句 `import matplotlib`，Python 3.9 或
  Pillow 的 DLL 坏了的 Conda 都能被存下来，第一次渲染才以「渲染进程退出」收场。
  看护：`tests/test_environment_health_parity.py`。
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
  指纹（`repair_plan_stale`）。没有计划 → `dependency_install_not_allowed`——
  这个 code **只**留给「没有计划 / 计划不属于这里 / 目标不合法」三种没有用户
  意图的情形（#466）；「同一环境同一需求这一轮已经装成功过」是
  `dependency_already_attempted`，「目标环境里已经 import 得到」是
  `dependency_already_present`，各自有各自的话。`_attempted` 只在 **pip 退出码
  为 0 之后**登记：它挡的是「装完还缺、再装还缺」，pip 没跑成（断网 / 取消）
  的那次允许重试——写在 pip 之前的话，失败文案说「检查网络后重试」，重试撞到的
  是「已经试过了」。`create_plan` 查一次之外，**租约在手、解释器已知之后 pip 之前
  再查一次**：两个页签各自形成的计划都有效（指纹看不见 site-packages），A 装成功后
  B 不该再跑一遍。
- **全局显式解释器生效时不提供任何目标（#465）**：`TAVOTTO_WORKER_PYTHON` /
  设置里指定的解释器只要**存在**就压过 `pool.resolve_worker_python()` 第 3 档
  （ADR 0018 §四），而自动接手、采用系统解释器、装进项目 `.venv` / 受管环境最后
  都写在那一档——那时提供安装等于让用户真的联网装一遍、装完渲染照样缺。判据
  唯一出处 `pool.explicit_worker_python()`（与 `resolve_worker_python` 同一份，
  指向不存在路径的设置不算生效；**`bootstrap.install()` 写进 config 的自建 venv 不算
  显式选择**——它是自动决策，作为 `managed_venv` 候选留在老链条里、排在自身之后
  系统链之前（config 那条同路径时**不占**「用户指定」的靠前槽位，去重留的是第一次
  出现的位置），不压项目级环境、也不会让卡片走到「清掉它」）；载荷只从
  `deprepair.pinned_payload()` 出
  （`{python, source, variable}`，`variable` 是 `env_override` 时**供值的那个**变量名，
  旧名 `MM_WORKER_PYTHON` 供的值要点它的名）。`offer()` 回
  `code=dependency_interpreter_pinned` + `pinned` 且 `targets` 为空；`create_plan()`
  拒绝；**`install()` 在租约（`pool.mutating_environment`）里再复查一次**——环境
  指纹只看目标环境，确认窗口里从别处钉上的全局解释器它看不见，不复查 pip 照跑；
  而全局解释器的改动（`PATCH /api/engine/environment` 全局档）必须经
  `envlease.unless_mutating()` 走、与租约同一把锁互斥：先钉上 → 复查看得见，先拿到
  租约 → 改动 409 `environment_mutating`。租约之前查没有用（查完到拿到租约之间照样
  能钉）。复查不过计划一并作废（后端是边界，不靠按钮）。界面按 `source` 给出口：`configured` 一键「恢复自动检测」
  （清全局设置 + 重排失败的渲染），`env_override` 按 `variable` 点名要清哪个变量、
  然后重启。**不改优先级本身**——「项目显式 > 全局显式」是
  ADR 级的另一个问题。
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
- `deprepair` 里每个 `ERROR_*` code 在两种语言里都要有文案——
  `engine.repairError.<code>` 或 `backend.<code>`，与卡片 `repairCodeMessage` 的查法
  同源（`test_every_repair_code_has_text_in_both_languages`，常量名从 AST 取、值从
  模块取）：`test_error_codes.py` 不扫 `RepairError`，这张表以前只有反向的死键门禁。
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
- **import 了却从未用到的不算「需要」（ADR 0061 §二 2026-09-24 修订）**：`importscan` 按 `figcapture.unused_imports`
  （唯一判据，只收 AST 能证明的：起了别名、不带点的 `import X as Y`——裸 `import X` 可能是为了副作用，一律不收；X 还必须在无副作用名单 `figcapture.SIDE_EFFECT_FREE_IMPORTS` 里（别名也可能只为副作用，评审 #555 两条 P1）——判据是进程级副作用快照 `tests/support/import_side_effects.py`（matplotlib / 环境变量 / warnings / logging / 导入钩子 / 信号 / excepthook / atexit / builtins / codec 与 locale……任何一项变了就不进），扩名单要用它实测；不在 `try` / `with` 里、绑定名与 X 在别处一次都不出现、
  没有 `globals` / `eval` / `__dict__` 这类读不清的用法）标 `unused`，`needed` / `unknown` 不含它，`JointPlan.unused`
  只列不装；本地模块也 import 了它照旧按上下文判；任何被跟进的本地模块能够到脚本命名空间（`figcapture.reaches_main`：`__main__` / 脚本 stem 的 import 或字符串、栈帧取 globals），或跟进看不全（截断 / 读不了 / 编译扩展 / 非字面量动态 import），脚本的 `unused` 整份作废（评审 #555 P2）；跟进到的包里全部 .py 与相对导入解析到的目标都交给 `reaches_main`，相对导入解析不到 / 越出项目根 / 落到编译扩展同样作废（这一遍不改 needed 的集合）。缺的那一行由 worker 给占位（`figure-capture-and-execution.md`）。
- **marker 按目标解释器求值**（`target_facts` 在目标里量 PEP 508 环境；启动条件与 `probe_environment` 对齐：不带
  `-I`、env 继承、cwd 空目录）。Flask 进程的 `sys.platform` 不是判据的主语。
- **计划的集合**（`depplan.plan`）：`requirements` = **目标里没有的**那些 needed distribution 的全部选中声明（extras /
  specifier 原样；只有 curated 映射的给裸名）；`constraints` = 其余选中声明 + 约束文件；`adapter`（`ADAPTER_REQUIREMENTS` ↔
  pyproject 的 `worker` extra，用例钉着）**只并入受管环境**，用户 venv 不并入；版本不满足声明的已装包只报告不改（FO-038）。
  **两份事实**：`facts` 是此刻会跑脚本的解释器的（`missing` 按它量——门问的是「现在起会话会不会缺包」）；`install_facts`
  是装到哪的（`deprepair._facts_for`：用户 venv = 同一个；受管 = active 那一代，没有就是从 base 新建的一代——marker 环境与
  stdlib 按 base、已装为空 `fresh_venv_facts`）。集合、marker 求值、stdlib 名字表都按 `install_facts`；两者是同一个环境时
  就是一份（Codex #461 P1：否则选中项目 venv 而目标受管时新的一代漏装 venv 里碰巧有的包）。
  hash 模式 = 锁文件语义：整份选中集合按 `--require-hashes` 装，缺一条 hash 就 `blocked`；受管目标下 adapter 给不出
  hash、**不写进需求文件**，锁必须已经把 matplotlib / numpy 用 `==` 钉在 adapter 范围内（没钉 → `dependency_hashes_incomplete`
  带 `adapter`；钉在范围外 → `dependency_conflict`，`_adapter_against_lock`）。
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
  这次的 delta；**hash 模式只有锁本身**）→ `pip check` → 关键 import（`probe_imports`）→ `worker_self_test` → **才** `managedenv.activate`
  （manifest 的 `active` 字段原子写，**唯一**指针，不设第二个指针文件）。任一步不过 = 这一代 `incomplete`、`active`
  不动、上一代原样可用；不把 tmp 里的 venv rename 过来；不往任何共享 site-packages 写。旧布局的 `venv/` 是隐式的
  `legacy` 一代，第一次按代时登记进 `generations`。**目录名永远不撞在册的代**（`managedenv.fresh_generation`：同一份
  身份再来一次——重建两次同一份账——而那一代还 active / 旧代还有人用，就 `g<身份>-2`、`-3`……；`register_generation`
  拒绝重新登记 active 或 `ready` 的代），身份字段照记（Codex #461 P1：否则 active 目录会被当成「上次建到一半的」删掉）。
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
  digest（两份：`facts_digest` + `install_facts_digest`）/ 组 / 有效期；执行只认 `plan_id`，执行前重算解释器指纹**与**
  事实 digest（不走缓存；确认期间有人往目标里装 / 卸了包 `pyvenv.cfg` 不变而 digest 变——`repair_plan_stale`，Codex #461
  P2）；不是 `ready` 的计划拒绝绑定
  （`dependency_plan_blocked` 带 `joint` 载荷——blocked 的理由与 `nothing_needed` 都在里面）；建代前查磁盘
  （`package_disk_low`）。
- **取消的接受时刻**（D11）：拿锁前 / 建 venv 与 pip 期间（kill）/ 验证期间（含 `worker_self_test` 之后、切 active 之前
  再看一次——接受了的取消不能照常提交，Codex #461 P2；用户 venv 原地那条路同样，包已装进去就如实报 cancelled + 体检）→
  `cancelled`、这一代 `incomplete`；**提交点（切 active）之后拒绝**（`cancel_status` → `committed`，`progress()` 带
  `committed: true`）。**取消句柄在 `prepare_async` 起线程之前登记**（`_register_cancel`，`prepare()` 复用同一个）：
  `/prepare` 一回 202 用户就能取消，哪怕线程还在重算事实、还没拿锁——句柄不在表里 `cancel_status` 只能回 `not_found`、
  安装照常改环境（Codex #470 P1）；`prepare()` 拿锁之前看一次事件，不论怎么退出都在 finally 里清句柄；`_run_pip` 起 pip
  之前先看事件，已取消的不起。**同一份计划只认领一次**（`_claim`，同样在起线程之前、锁内）：第一次完成前重复
  `/prepare` 不起第二个线程，只把在途进度交回去（`started: false`）；同步入口 `prepare()` 撞上在途的拿
  `dependency_install_not_allowed`（Codex #470 P1 第二轮）。**提交点**「看事件 + 定提交」与 `cancel_status`「看提交 +
  设事件」同一把锁，两边只会有一个赢。
- **清单写失败不是成功**：`managedenv.write_manifest(strict=True)` 在登记一代与切 active 这两处照抛 `OSError`（卷满 /
  只读 / `os.replace` 被拒），事务撤回「已提交」、这一代按 incomplete 记（尽力而为）、报 `managed_env_write_failed`，磁盘上
  `active` 仍指上一代；记账那些路（mark / record / snapshot）仍尽力而为、回 False（Codex #470 P1：此前 `activate()` 吞掉
  写失败，`done` + `activated: true` 而清单还指旧的一代）。
- **取消端点只取消当前项目的计划**：`plan_id` 随 SSE `engine.dependency` 广播给每个订阅者，别的项目的标签页拿到 id 也
  不能取消这里的安装——计划还在而 `plan.project != root` → 409 `dependency_not_allowed`（与 `/prepare` 同一道判据，
  Codex #470 P2）。
- **`script` 参数只经 `projectenv.contained_path` 钉回项目内**（`app._project_script`：先 realpath 再按前缀判——`..`
  回溯、软链接指到项目外、项目外绝对路径都在那一步现形，项目内绝对路径照旧允许），之后交给文件系统的只有它回的那一个
  路径，不拿原串重拼（CodeQL #470 三条 py/path-injection 的处置；`contained_path` 是 projectenv 里唯一允许把用户派生路径
  交给文件系统的入口）。
- 失败码新增三条：`dependency_consistency_failed`（pip check）、`dependency_hash_mismatch`（require-hashes 不符，
  `classify_pip_failure` 排在冲突之前）、`dependency_plan_blocked`。
- 看护：`tests/test_dependency_transaction.py`（argv / 文件 / 集合钉字节；代的登记 / 切换 / 旧布局 / 退役；**真**事务：
  三包一次成代、二开不重装、marker 为假与未选组不装、声明冲突与求解器冲突各停在切 active 之前、无 wheel / 坏 hash /
  取消 / 自检 / 一致性 / 关键 import / 只读目录 / 磁盘不足各不切 active、两项目并发独立、native 租约不杀、旧代留到没人用、
  重建成新一代、**重建两次不碰 active 目录**、**hash 锁真跑 `--require-hashes` 成代且文件只有锁**、**选中项目 venv 而目标
  受管时新代装全 needed**、**自检期间的取消算数**、单包修复走同一事务、用户 venv 原地不并入 adapter、过期 / 指纹变 /
  **目标里的包变**的计划拒绝）。

### 跑前的门与入口（PR C，ADR 0061 §六）

- **门只有一处**：`pool._new_worker` 起会话之前先过 `workdir.resolve_mode`，再过 `pool.SPAWN_GATES` 上登记的
  `deprepair._spawn_gate`（`deprepair` 在 import 时登记；`pool` 不 import `deprepair`——反向 import 是环）。
  门的判据 `deprepair.gate`：计划 `ready`、还有轮次、用户没说「直接跑」→ 抛带 `dependency_preparation` 载荷的
  `WorkerError(code=dependency_preparation_required)`；`blocked` / `nothing_needed` / 没轮次 / 已跳过 → 放行。
  `preparation.plan_for` 用同一份判据落 `needs_input`，`dependency_preparation` 字段无论问不问都写（诊断面）。
- **门一直问到有答案**：一次成功的准备（`_gate_skipped` 清掉、计划变 `nothing_needed`）或明确的
  `skip_preparation`（`POST /api/engine/dependencies/skip` / 授权框「不准备，直接运行」/ MCP
  `prepare_dependencies="skip"`）。不许「只问一次、第二次悄悄放行」。
- **投影三处同一份**：渲染端点 `_worker_error_payload`、素材库试运行 `probe._error_from_worker`（U04 顺带把 U03 的
  `confirmation` 也接上，此前试运行把两道门都压成 `script_probe_failed`）、MCP `_bridge_error_from_worker`
  （`structuredContent.dependency_preparation` + `recovery`）。
- **端点**：`GET /api/engine/dependencies?script=`、`POST …/plan`（非 ready → 409 `dependency_plan_blocked` + `joint`）、
  `POST …/prepare`（只发 `plan_id`，进度 SSE `engine.dependency` `flow: joint`）、`POST …/cancel`
  （`accepted / reason`，过提交点 `committed`）、`POST …/skip`、`PATCH /api/engine/dependencies`（`groups`，改了就
  `reset_state(project)`）。`script` 参数按试运行端点同一份判据（realpath 之后在项目内、`.py`、存在），三个
  code 同一闭集。全部在会话认证之内。
- **MCP**：`tavotto_open_figure(prepare_dependencies=tavotto_managed | project_venv | skip)` = `create_joint_plan` +
  `prepare` 同步执行再开图（返回多 `prepared`）；批量 open 不接受；`deprepair` 进 `_BRIDGE_IMPORT` /
  `BRIDGE_IMPORTS_AT_MIN`，新名字 `getattr` 守着、缺就 `engine_too_old`。
- **台账**：FO20 / FO21 / FO22 / FO27 / FO31 enforced（pr，`tests/test_foundation_dependencies.py`，目标 = 项目自带
  venv 变体；受管变体的机制面在 `test_dependency_transaction.py`）；FO18 / FO05 observing（nightly
  `foundation-observing`，`TAVOTTO_FOUNDATION_ONLINE=1` 联网取科学栈 wheel）；FO13 / FO28 / FO29 planned，理由在
  `enrollment.json` 的 notes。
- 看护：`tests/test_foundation_dependencies.py`、`tests/test_preparation_api.py`（门的两种终局）、
  `tests/test_dependency_repair_e2e.py`（门之后 skip 再走运行后那条路）、`tests/test_mcp_server.py`（投影 /
  `prepare_dependencies` 闭集 / 批量拒绝）、`web/src/components/DependencyPrepareDialog.test.tsx`。

