# 私有完整 Python（统一实施包 U05，ADR 0063，2026-09-21）

> 随 U05 新增；`src/tavotto/AGENTS.md` 只留速查行。这里是这一主题规则的**唯一全文**。改规则改这里，并同步那一行。
> 架构决策在 `docs/adr/0063-private-python-provisioning.md`；目标资格另立一份 ADR（0064，随 PR C）。

它只回答一个问题：**受管环境的基础解释器从哪来**（这台机器没有合格 Python 时）。装包仍是 U04 的代事务
（`docs/rules/backend/dependency-repair-and-packages.md`「事务：受管环境按代」）+ pip；本模块交出去的只是一条解释器路径。

- **模块与输入唯一**：`engine/privatepython.py`（纯标准库，Flask 侧；被 `managedenv` / `deprepair` import）；输入只有
  `src/tavotto/resources/private_python_lock.json`（schema 1，按 `<os>-<arch>` 分目标：url / sha256 / size / `python_rel` /
  `enabled`；`python` 块钉 version / release / flavor）。**不造第二个安装器、第二份安装锁**（D05）：pbs install_only 自带
  venv / ensurepip / pip，`create_generation_venv` / `pip_install_joint_argv` 一个字节不改；uv 不下载、不进锁（U02 runtime spike 那份 ADR（0056）里的
  钉法留作「base 没有 pip 时」的备选）。两个 macOS 目标的 CPython 来源与 `packaging/runtime-lock.json` 是同源对
  （`docs/rules/repo/same-origin-pairs.md`）；Windows 用 pbs 而不是内置 runtime 那份 embeddable（没有 venv / ensurepip）。
- **基础解释器的优先级只有 `managedenv.base_python()` 一处**：系统合格 base（`bootstrap.find_base_python(accept=区间)`）
  → **已供应的**私有 Python（`privatepython.python_of()`，只看磁盘、不联网、不起子进程，**且只在目标仍提供这条路时**
  ——`offered()` 为假就当磁盘上那份不存在，行为回到 U04）。私有 Python 不压过用户已有的合格 Python；它不是渲染 runtime，
  `pool._prioritized_candidates()` 不变。
- **目录按内容命名、不可变；账不是指针**：`<data_dir>/private-python/runtimes/<cpython-<版本>-<sha256 前 12 位>>/`；
  解包 + 成员校验 + 真起一次都在 `runtimes/.staging-<id>-<pid>/`，提交点 = `os.replace` 到最终目录；`python_of` 只看
  最终目录里的解释器（且可执行），staging 对它不存在——应用中途被杀留下的半个解包**不可能**被当成可用 runtime，
  下一次供应按 pid 存活 / 时限清掉。「现在该用哪份」= 锁文件那份的 id，不设第二个指针文件；`ledger.json` 是账。
- **校验先于一切执行**（判据的主语：磁盘上这个文件此刻的字节）：整份归档 sha256 与锁一致 → `.part` 改正式名 →
  逐成员校验（绝对路径 / `..` / 不在 `archive_root/` 下 / 软链接指出根目录 / 硬链接 / 设备文件一律
  `private_python_invalid_archive`——**这是我们自己的第一道**，`tarfile` 的 `data` 过滤器只是第二道）→ 落点 realpath
  在 `data_dir` 之下 → 常规文件 + 可执行位 → **真起一次**（`-I -c`，自报版本必须等于锁的版本）→ 才改名。
  坏 hash / 截断 / 错期望值的路上解释器执行计数为 0、无新目录、`.part` 当场删；hash 不符**绝不重试**，
  传输层失败才有界重试；缓存里对不上的归档不是复用对象。
- **离线三档**：有校验过的缓存 → 零请求（FO24）；无缓存连不上 → `private_python_offline`，有界、不建目录（FO25，
  safe_stop 不计自动成功）；来源回 4xx / 5xx → `private_python_source_unavailable`（要升级 Tavotto，不是重试）。
- **联网只有一条路**：`urllib.request.urlopen`——TLS 校验默认开（源码里没有 `ssl`，AST 钉）、代理只从
  `HTTP(S)_PROXY` / `NO_PROXY` 环境变量来（与 `updater` / pip 同一张脸）、`User-Agent: Tavotto/<版本>`、不带身份；
  **不读** pip.conf / uv 配置 / 用户配置 / 项目设置（对 `config` 只调 `data_path` / `data_dir`，AST 钉）。
- **授权绑在计划上**：没有合格 base 且本目标提供私有 Python 时，`create_plan` / `create_joint_plan` 在计划上挂
  `private_python` 载荷（`offer_payload()`：id / 版本 / 目标 / `download_bytes` / `cached` / `network_required`，
  无机器路径），`offer()` 的受管目标同样带它；界面必须把 `download_bytes` 说出口。执行端按
  `_GenerationJob.provision_private` 在**锁内、建 venv 之前**先供应（进度状态 `downloading_python`，`result.download`
  带 stage / 字节数）；**重建 / 包管理首装没有明示过下载**，没有 base 就照旧 `managed_env_unavailable`。
  锁文件 `enabled=false` 且没开逃生门时行为与 U04 逐字相同。
- **干净机器的第一份计划**（PR B）：一个渲染解释器都没有时 `deprepair.private_python_target()` 以私有 Python 为目标
  ——已供应就真量，没落盘用替身（`private_fresh_facts()`：`privatepython.standin_marker_env` + adapter 已有 + 宿主 stdlib）；
  替身只服务披露，计划带下载（`JointRepairPlan.replan`）时事务供应后按真解释器重算（`_replan_on_base`）——重算前先比
  规划输入的指纹（`JointPlan.inputs_digest`：声明意图 + 脚本与本地模块的字节），下载期间输入变了即 `repair_plan_stale`、
  一个字节不装；执行前重量走同一条路（`_facts_for_plan`）。`_facts_for` 没 base 时也用它量新的一代。`nothing_needed` 在干净机器上照样成计划
  （环境本身就是要的），门（`gate`）在有 `private_python` 段时也问；`preparation.plan_for` 在 `no_worker_python` 时也问门。
  受管目标的可用性（`joint_targets`）看**有没有基础解释器**（每次都建新的一代），没有且提供私有 Python → 可用 + 载荷。
- **去重 / 取消 / 租约 / GC / 配额**：同一个 id 并发只下一次（`_inflight`：一个下载线程、若干消费者各自等；跨进程靠
  staging 带 pid + 最终目录已在就复用）；取消按消费者（D11）——一个取消只是它自己以 `private_python_cancelled` 退出
  （事务收成 `cancelled`、这一代**没登记**），最后一个消费者放弃时下载才中止；提交点之后取消无效。
  `retire_unused(in_use=…)` 只删「不是当前那份、且 `deprepair._private_runtime_in_use` 为假」的旧 runtime：任一项目
  受管环境的任一代记着它为 base（`managedenv.referenced_base_runtimes()`，venv 挪不走 base）或池里 / envlease 上有
  会话用着它就不删；事务提交后各试一次。下载前 `require_free_disk`（归档 × `EXTRACTED_FACTOR` + 余量）→
  `private_python_disk_low`，计划阶段就查一次。
- **隔离**：写入只在 `data_dir/private-python/` 之下；不改 PATH、shell、注册表、默认 Python、用户 `.python-version`
  （用例：HOME 指空目录跑完仍为空、`os.environ` / cwd 前后相同、新文件全在私有目录下）；真起时摘掉 `PYTHON*` /
  `VIRTUAL_ENV` / `CONDA_PREFIX`。Windows 注册表前后快照由目标验证腿（PR C）取。
- **能力默认关**：锁文件每个目标的 `enabled` 在无系统 Python 的资格取得前保持 `false`（06 §2）；
  `TAVOTTO_PRIVATE_PYTHON=1|0` 是工程 / CI 目标腿的逃生门（与 `TAVOTTO_RUNTIME_HOST_ARCH` 同一档），不是产品设置。
  换版本 = 改锁（新 sha256 → 新 id → 新目录）+ 每个目标重新取得资格，不自动追最新。
- **错误码闭集** `privatepython.ERROR_CODES`（九条 `private_python_*`），文案在 `web/src/i18n/locales/*/errors.json` 的
  `engine.repairError`（与 deprepair 同一张表；`tests/test_private_python.py` 钉两种语言都有）。
- 看护：`tests/test_private_python.py`（锁 / 同源对 / 目标名 / 逃生门；本地供应服务 + 假归档跑真实状态机：正例、
  缓存零请求、坏缓存不复用、篡改 / 截断 / 错期望值 / 离线 / 死代理对照 / 404 / zip-slip 六种 + 成员校验逐形状 /
  起不来 / 版本不符 / 无可执行位 / 磁盘配额；并发去重、消费者取消、最后一个取消中止、提交前中止、提交后取消无效、
  退役、孤儿 staging；HOME / 环境隔离、AST 判 import 闭集；`TestRealArchive` 真 pbs 归档要
  `TAVOTTO_PRIVATE_PYTHON_REAL=1`）+ `tests/test_private_python_transaction.py`（真事务：一次授权供应 + 建代、
  资格未取得时行为不变、offer / 单包计划带下载、重建 / 首装不下载、离线 safe_stop 不登记代、坏 hash 旧 active 原样、
  下载期间取消无残留、两项目共享一份、有代记着的旧 runtime 不删；`TestCleanMachine` 四条干净机器；`TestRealChain`
  真归档要 `TAVOTTO_PRIVATE_PYTHON_REAL=1`）+ `tests/test_foundation_private_python.py`（FO24 / FO25 / FO26 经产品 HTTP
  入口，进程内 test_client）+ `web/src/components/DependencyPrepareDialog.test.tsx` + `tests/test_mcp_server.py` 的干净机器一条。
