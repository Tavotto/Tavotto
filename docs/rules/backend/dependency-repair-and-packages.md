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
- **体检的主语是 worker 的启动导入链**（#435）：`_PROBE_SRC` import 的是 `worker`
  模块本身（目录由 `projectenv.ENGINE_DIR` 给，用例可指到假引擎目录），不是一份
  手抄的 `figcapture, manifest, overrides` 清单——清单只在写下的那一天与 worker.py
  相同，而 worker 还要 `matplotlib.figure`（→ Pillow）、`figsession`、`wireproto`。
  matplotlib 在、worker 起不来单独成码 `project_env_worker_import_failed`（`error`
  带断在哪一句），与「没有 matplotlib」是两条出路。**全局「渲染环境」
  （`PATCH /api/engine/environment` 不带 scope）与项目路径走同一份体检**，
  只是回给界面的 code 不同（`interpreter_unsupported_python` /
  `interpreter_no_matplotlib` / `interpreter_worker_import_failed` /
  `interpreter_unusable`）：以前全局只问一句 `import matplotlib`，Python 3.9 或
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
