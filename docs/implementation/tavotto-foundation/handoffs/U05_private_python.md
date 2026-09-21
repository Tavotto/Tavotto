# U05 · 补齐干净机器的基础解释器来源 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U05.private_python。叠栈 PR：**A** 供应器 + 锁文件 + 目录 / 校验 / 离线 / 取消 / 去重 / 租约 GC + 接进
U04 代事务（纯后端，本地供应服务用例 + 真事务用例）；**B** 入口接线（准备 `needs_input` → 一次授权 → 私有 Python →
U04 环境 → 出图；FO24 / FO25 / FO26 经真实入口的场景与台账）；**C** 目标验证腿（三平台真下载 → 校验 → 起 → venv → 装 →
出图；Windows 注册表快照；Linux 空镜像）+ ADR 0064。架构决策：[`docs/adr/0063-private-python-provisioning.md`](../../../adr/0063-private-python-provisioning.md)。
本文件随 A 落地，B / C 各追加一段。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `11a8370e`（`foundation/u04-dependencies-b` 派工时的 head，下面是
U04 A #459 → U03 B #456 → U03 A #454 → U01 #451 → main）；结束 = PR A 的 head（合并后以 `git log origin/main` 里 PR 号
为准）。全部在 worktree `tavotto-wt/foundation-u05` 里做；用户主工作区一个字节没碰；主仓库 `.venv` 没装任何东西；
真实下载只在 session scratchpad 里做过一次（macOS arm64 的 pbs 归档，hash 校验过后复用）。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（解释器链、单包修复、包管理、写回、会话认证）。
**PR A 默认路径上变了的行为**：只有一处——`managedenv.base_python()` 在系统合格 base 找不到时多看一眼**已供应的**
私有 Python（磁盘上有才算，不联网）；今天磁盘上不可能有（锁文件 `enabled` 全 false、没有逃生门就不供应），所以
默认路径逐字不变。拟启用能力：**无**（`plan.json` `new_default_capabilities_enabled: []`）；本阶段不产生任何产品资格。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`packaging/AGENTS.md`、`src-tauri/AGENTS.md`、
`codex-plugin/AGENTS.md`、`.github/AGENTS.md`；`docs/rules/backend/` 的 dependency-repair-and-packages（含 U04 两节）/
preparation-and-receipts / runtime-figure-assets / process-boundaries / profiles-and-preflight / worker-protocol-and-lifecycle /
session-auth / telemetry / update-check；`docs/rules/repo/` same-origin-pairs / predicate-subject；ADR 0008 / 0019 / 0021 /
0044 / 0047 / 0053 / 0056（U02 分支）/ 0057 / 0061；实施包 00 §3 / §5、01（D04 / D05 / D11 / D16）、03（§5 / §9）、04 §2、
05 §4 / §6、06 §2、07、`phases/U05_private_python.md`、registry 的 FO-022 ~ FO-028、FO23 / 24 / 25 / 26 与
`archive/firstopen_cases.json` 原文；U02 / U03 / U04 交接；`packaging/runtime-lock.json` + `scripts/build_worker_runtime.py`
（锁的钉法与 `download()` 的「校验失败当场失败」）。复用（权威不动）：U04 的 `create_generation_venv` /
`pip_install_joint_argv` / `_run_generation_locked` 事务 / `envlease` / `retire_unused`；`runtime.host_os / host_arch /
normalize_arch / CREATE_NO_WINDOW`；`config.data_path`；`updater` 的 urllib 联网面（TLS 默认、代理只从环境变量）。

## PR A：供应器、锁文件、接进代事务（纯后端）

**实际代码与 API / 数据结构变更**（细节见 ADR 0063 §一–§九）：

| 层 | 变更 |
|---|---|
| `src/tavotto/resources/private_python_lock.json`（新） | schema 1：`python` 块（3.13.15 / release 20260814 / install_only / archive_root）+ 五个目标（macos-arm64 / macos-x86_64 / linux-x86_64 / linux-arm64 / windows-x86_64）的 url / sha256 / size / `python_rel` / `enabled=false` / qualification 说明。两个 macOS 目标与 `packaging/runtime-lock.json` 同源（用例钉）；Linux / Windows 的 sha256 从 release 的 SHA256SUMS（sha256 `0e27ff80…7584966`）重新核过，与 U02 spike 表一致 |
| `engine/privatepython.py`（新，纯标准库） | `PythonSource` / `load_lock` / `validate_lock` / `host_target` / `source_for` / `offered`（锁 `enabled` < 环境变量 `TAVOTTO_PRIVATE_PYTHON`）；`python_of`（只看最终目录）/ `status` / `offer_payload`（id / 版本 / 目标 / `download_bytes` / `cached` / `network_required`，无路径）/ `require_free_disk`；`provision(source, cancel_ev, on_progress)`：`_inflight` 去重（一个下载线程、若干消费者）、`_download`（缓存校验复用 / `.part` / 整份 sha256 / 传输层有界重试 / 4xx-5xx 单列）、`_extract`（逐成员 `_validate_member` + `data` 过滤器）、`_verify_executable`、`_launch`（`-I -c` 自报版本必须等于锁）、`os.replace` 提交、`_record` 记账、`_reap_orphans`；`retire_unused(in_use=)`、`list_runtimes`、`touch`；九个 `private_python_*` code + `ERROR_CODES`；进度阶段闭集 |
| `engine/managedenv.py` | `base_python()` 末级 → `privatepython.python_of()`；`register_generation(..., base_runtime="")` 记 `base_source`（system / private_python）与 `base_runtime`；`referenced_base_runtimes()`（扫所有项目 manifest） |
| `engine/deprepair.py` | `RepairPlan` / `JointRepairPlan` 多 `private_python: dict \| None`（进 `to_payload`）；`offer()` 受管目标带 `private_python`（base 探完为假且提供时 available=True）；`create_plan` / `create_joint_plan` 经 `_private_python_offer()`（有 base → None；提供 → 载荷 + 磁盘配额；都没有 → 照旧 `managed_env_unavailable`）；`_GenerationJob.provision_private`；`_run_generation_locked` 在锁内、建 venv 之前 `_provision_private_base()`（状态 `downloading_python`，`result.download` 带 stage / done_bytes / total_bytes；取消 → `cancelled`、这一代没登记；失败原样带 code）；成功后刷新 base 缓存、这一代记 `base_runtime`（`_private_runtime_of` 也认「系统链末级找到的就是私有那份」）；提交后 `privatepython.touch` + `retire_unused(in_use=_private_runtime_in_use)`；新状态 `STATE_DOWNLOADING_PYTHON` |
| `web/src/i18n/locales/*/errors.json` + `resources.d.ts` | `engine.repairError` 九条文案（两种语言；`pnpm i18n:types` 重生成） |
| `tests/support/private_python.py`（新） | `LoopbackServer`（ok / truncate / corrupt / missing / hold / 限速；请求日志）、`fake_archive`（pbs 同形状；POSIX 替身 = exec 宿主的 sh 脚本并记 `launches.log`，Windows = venvlauncher 副本 + pyvenv.cfg）、`closed_port_url`、`snapshot_tree` |
| `tests/test_private_python.py`（新，71 条） | 见细则「看护」一段 |
| `tests/test_private_python_transaction.py`（新，9 条，真 venv + 真 pip + 真 worker 自检） | 一次授权供应 + 建代（含应用重开后探测链末级仍看见它）、资格未取得时行为逐字不变、offer / 单包计划带下载、重建 / 首装不下载、离线 safe_stop 不登记代、坏 hash 旧 active 原样、下载期间取消无残留、两项目共享一份、有代记着的旧 runtime 不删 |
| 文档 | ADR 0063；`docs/rules/backend/private-python.md`（新细则）+ `src/tavotto/AGENTS.md` 速查行；`packaging/AGENTS.md` 一段；`same-origin-pairs.md` 一行；本文件；`evidence/u05/`；`plan.json` U05 in_progress |

**锁文件与来源裁决**（主对话要的那一条）：来源 = pbs install_only（与 runtime-lock 同一份钉法），锁放**包内**
`resources/`（产品运行时要读；`packaging/` 不进包）；**uv 不进产品路径**（pbs 自带 pip，U04 事务原样；再下 37 MB
的 uv 只会成为第二个安装器）；Windows 目标名按 `runtime.normalize_arch` 是 `windows-x86_64`（runtime-lock 那边叫
`windows-amd64`，两边各自的读法各自一致，同源对只登记 macOS 两条）；`enabled` 全 false。

**关联旧要求 ID / 场景 ID**：FO-023（provisioner / 来源 / hash / 平台核验：锁 + 校验先于执行 + 目标名）、FO-024（不改
PATH / 注册项 / shell / 默认 Python：模块级隔离用例；注册表快照待 PR C）、FO-025（离线有缓存成功 / 无缓存受限：
机制面）、FO-026（校验错 / 磁盘满 / 取消不发布 ready runtime：机制面 + 事务面）、FO-027（多消费者去重与引用清理：
机制面 + 事务面）、FO-028（不原地升级被 live 会话使用的 Python：按内容命名不可变 + 代记着就不退役）、FO-022（无系统
Python 的真桌面产物：**未取得**，PR C 起观测）；FO24 / FO25 / FO26 的**机制面**各有真用例，经真实入口的场景资格在 PR B 取；
FO23 保持 planned。registry 220 条 `execution_status` 一条没动。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿（448 files） |
| `PYTHONPATH=$WT/src pytest tests/test_private_python.py` | 同上；应用 `.venv` 3.13.11；本地供应服务 + 假归档 | 0 | 70 passed，1 skipped（`TestRealArchive` 要 `TAVOTTO_PRIVATE_PYTHON_REAL=1`）；约 15 s |
| `TAVOTTO_PRIVATE_PYTHON_REAL=1 TAVOTTO_PRIVATE_PYTHON_CACHE=<scratchpad 缓存> pytest tests/test_private_python.py -k TestRealArchive` | 同上；真 pbs `aarch64-apple-darwin` 归档（25 304 407 B，sha256 `7d50bb42…c5c6a8c5`） | 0 | 供应 → `-I -c` 自报 3.13.15、prefix 在 `runtimes/<id>` 下 → `-m venv` → venv 里 `pip --version` 0、`sys.base_prefix` == runtime 目录；2.66 s |
| `PYTHONPATH=$WT/src pytest tests/test_private_python_transaction.py` | 同上；worker 解释器 = 本机 3.11.14（#452 的配置）；真 venv、真 pip（离线 wheelhouse）、真 worker 自检 | 0 | 9 passed（约 2 分钟） |
| 变异反证 20 条（`evidence/u05/mutations_pr_a.md`） | 同上 | 每条非零；还原后 0 | 17 条第一轮红；M06 / M17 / M20 第一轮绿各补一条用例后红（见证据文件） |
| 工程链 `evidence/u05/engineering-chain-macos-arm64.json`（scratchpad `u05/engineering_chain.py`） | 同上；真 pbs（缓存 + 死代理）、真 wheelhouse（matplotlib 3.11.1 / numpy 2.5.2 cp313 macosx arm64 + six + fixture 包） | 0 | 12/12：计划明示 `private_python`（cached、0 字节）→ `downloading_python` → 建代（base = 私有）→ pip 离线装 → 三层验证 → 切 active → `base_runtime` 记着、venv 的 `sys.base_prefix` == runtime → 独立用该 venv 出 10.6 KB PDF；19.2 s；HOME 里只有两条已归因的既有写入（`.matplotlib` 来自 `probe_environment`、`.rustup/settings.toml` 来自本机 `python -m venv` 的 ensurepip——Homebrew Python 一样，与本模块无关）；runtime 106 MB / 归档 25 MB / 受管环境 187 MB |
| `pytest tests/test_error_codes.py tests/test_i18n_dead_keys.py tests/test_import_architecture.py tests/test_install_locate.py tests/test_source_hygiene.py tests/test_dependency_repair.py tests/test_dependency_plan.py tests/test_agents_rules_index.py tests/test_docs_references.py` | 同上 | 0 | 全绿（import 图没有新环 / 反向边；`engine/` 无 flask；子命令不 import Flask） |
| `cd web && pnpm i18n:check` | 同上（worktree 里真 `pnpm install`） | 0 | 通过 |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文（红项逐条归因） |
| Windows（venvlauncher 替身、`os.replace` 目录、注册表）、Linux、真 pbs 的 Windows / Linux 归档、内置 runtime、e2e、`pnpm test/build`（PR A 只改 errors.json） | — | — | **not_run**（本机只有 macOS；Windows 的假归档形状（venvlauncher + pyvenv.cfg）没有本机证据，由 backend-platforms 的 Windows 腿第一次真跑） |

**本切片的正例、负例、旧行为回归**：正例 = 本地服务一次请求 → 校验 → 真起 → 原子改名 → 记账 → 再要不联网不再起；
缓存齐备零请求；真事务一次授权供应 + 建代 + 装 + 验 + 切 active；两项目共享一份；真 pbs 归档起得来、venv 建得出。
负例 = 篡改 / 截断 / 错期望值（执行计数 0、无目录、无 .part）；离线无缓存有界 safe_stop；死代理对照（回环也连不上）；
404 单列；zip-slip 六种形状 + 成员校验逐形状（含硬链接 / 设备 / fifo）；起不来 / 版本不符 / 无可执行位不发布；磁盘配额
零请求；最后一个消费者取消中止且无残留；提交前中止不提交；提交后取消无效；退役不删当前 / 不删有代记着的 / 判据异常按
在用；孤儿 staging 不被认、被清；HOME / env / cwd 前后相同；重建 / 首装不下载；坏 hash 旧 active 原样；离线不登记代。
旧行为回归 = 锁 `enabled=false` 且没逃生门时 `create_joint_plan` / `offer` 逐字同 U04（`managed_env_unavailable`）；
`test_dependency_repair`（含 15 条负向反证）/ `test_dependency_plan` 全绿；单包 argv / 联合 argv 一个字节没动。

**本次是否改变 case enrollment**：PR A **不改**（FO24 / 25 / 26 的用例是机制面 / 事务面，不经 HTTP 入口；提升到
enforced 在 PR B 随真实入口的场景一起，按 03 §4 逐条写正例 + 负例 + 预算 + lane）。FO23 保持 planned。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：
* 未运行：见上表最后一行。
* 基础设施问题：本机 `worker_python` 指向别的会话的 3.11 venv（#452）——真事务用例的 worker 是 3.11、替身 exec 的也是它
  （版本钉 3.11.14），与应用 `.venv` 不同 minor，顺带证明替身 / base 不必是应用自己的解释器；本机 `python -m venv` 的
  ensurepip 子进程会在 HOME 里写 `.rustup/settings.toml`（Homebrew Python 一样），是机器局部现象，不是产品行为。
* 真正产品失败：无新增。顺带发现的**事实**：① pbs macOS arm64 归档解开是 **106 MB**（ADR 0056 记的 68 MB 偏小），
  `EXTRACTED_FACTOR` 按实测定 5；② `deprepair.offer()` 第一次问时 `managed_available()` 是 None（后台探），私有载荷只在
  探完为假时挂——界面第一次拿到的 offer 可能还没有它，创建计划那一步才是真相（与 U04 原有的三态语义一致）；
  ③ 「没有任何渲染解释器」（`resolve_worker_python` 抛 `no_worker_python`）时 `create_joint_plan` 会先在
  `joint_target_for` 抛出——真正的干净机器要走的是 PR B 的入口：把私有 Python 当目标、供应后再算目标事实与 delta
  （`depplan.plan(facts=None)` 是 `dependency_target_unavailable`，不能拿来当计划）。这是 PR B 的第一条工作。

**Codex #464 第一轮（1 P1 + 3 P2，全部修，提交 `6f138719` + 用例修订）**：P1 代号折进私有 base 的 id（锁换版本而意图不变时
是另一代——重建的代号只由账算，两次重建之间会同号；active 那一代的记录与目录不动，新代失败旧的照常可用；变异 M21 第一版
用例经联合计划建第一代与重建公式不同号、根本撞不上，改成两次重建后红）；P2 跨进程 `.part` 带 pid + 随机后缀（两个进程真并发
供应各自成功、一个最终目录、无残留）；P2 探测链末级只在目标仍提供私有 Python（`offered()`）时才用磁盘上那份——能力关掉行为
回到 U04；P2 死代理对照两种拼法的 `no_proxy` 都清。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动。`LICENSE`、ruleset、`aggregate_gate.py`、默认后端、
`security._PUBLIC_PATHS`、worker 守卫、写回事务、`pool._prioritized_candidates`、`runtime-lock.json`、`pyproject.toml`
一个都没动；没有新增运行时依赖。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改产品源码：受管环境的基础解释器来源、计划载荷、
事务里多一步）→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；变异反证 20/20；文档门禁 0；i18n 检查 0。

**仍缺哪些默认启用 / 精确安装物资格**：全部。五个目标 `enabled` 全 false；无系统 Python 的目标资格（FO23 / FO-022）
一个都没取得——本机验证是「有系统 Python 的 macOS + 把发现链末端置空」的工程验证，不是资格（06 §2）。缺的证据：
真实无系统 Python 目标上的产品路径（frozen 桌面产物）、Windows 注册表前后快照、Linux 空镜像上的 runtime 可用性、
真 pbs 的 Windows / Linux 归档经产品代码（不是 spike）走完整链。这些是 PR C（ADR 0064）的内容。

**下一个无阻塞阶段 / 子切片**：PR B（入口接线）——依赖 U04 PR C 的 `dependency_preparation_required` / `needs_input`
形状（已向 U04 询问；设计：同一份 `required_input` 上并列 `private_python` 段，同一个授权 POST）。PR B 的第一条工作是
上面「顺带发现 ③」：无任何渲染解释器时以私有 Python 为目标、供应后再算事实与 delta。PR A 给 PR B 的输入：
`privatepython.offer_payload()`（挂在计划上的段）、`status()`（环境状态 API / 诊断包）、`STATE_DOWNLOADING_PYTHON` +
`result.download` 的进度形状、`private_python_*` 九个 code 的文案已在。U09 拿走：代记录的 `base_runtime` 进回执的环境身份。

**回退方式、不能假装可回滚的外部副作用**：revert PR A 即回退（新模块 / 锁 / 用例 / 文案删掉；`managedenv.base_python`
回到只看系统链；代记录里多出的 `base_source` / `base_runtime` 两个可选字段旧代码读得懂、忽略）。外部副作用：用户机器上
不会有——`enabled` 全 false 且没有逃生门时一个字节不下；本机只有 scratchpad 里的归档缓存 / wheelhouse / 链的临时数据目录，
删掉即可。
