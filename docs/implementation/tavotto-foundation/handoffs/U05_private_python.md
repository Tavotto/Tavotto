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

## PR C：目标验证腿与 ADR 0064（工程档证据；不是资格）

**实际变更**：

| 层 | 变更 |
|---|---|
| `.github/workflows/private-python-targets.yml`（新，非 required、不进 Gate） | `workflow_dispatch` + 每周 schedule + 本 workflow 直接消费的输入变动时、**且 base 是 main** 的 `pull_request`（paths 过滤：脚本 / 两份锁 / privatepython.py 与 U04 代事务链上的引擎模块 / 三个测试支持文件；`branches: [main]` 与 ci.yml / codeql.yml 同一条纪律——叠栈里 base ≠ main 的层不起这三条重腿，**PR 阶段的证据只在链头产出**；CI 收窄 PR #485 的判据是算出来的集合——目录里每个监听 `pull_request` 的 workflow 都要 `branches == [main]`，不需要登记）；三条腿 ubuntu / windows / macos：装应用 + 按 runtime-lock 钉科学栈 → `pip download` 宿主平台 cp313 的真 matplotlib / numpy 成 wheelhouse → （Windows 前快照）→ `TestRealChain`（真 pbs 经产品代码走完整链，公网下载单列在这一步）→（Windows 后快照并比对）→（Linux 空镜像）→ 机制用例在本目标上跑一遍 → 证据工件。托管 runner、每 job `timeout-minutes`、concurrency 与 ci.yml 同形（`tests/test_merge_queue_workflows.py` / `test_source_hygiene.py` 全绿；actionlint 0） |
| `tests/test_private_python_transaction.py::TestRealChain`（`TAVOTTO_PRIVATE_PYTHON_REAL=1`） | 宿主目标的真 pbs 归档（缓存或公网）→ 事务 → venv 的 `sys.base_prefix` == runtime、mpl / numpy 版本 == 锁 → 独立出图；`TAVOTTO_PRIVATE_PYTHON_DATA_DIR` / `_WHEELHOUSE` / `_CACHE` / `_REPORT` 四个环境变量给腿用 |
| `scripts/ci/private_python_empty_image.sh`（新） | 把供应好的 runtime **只读**挂进 `ubuntu:24.04`（`--network none`）：先证明镜像里没有 python3 / python / pip / uv，再真起 → venv → 离线装 → pip check → 出图 → report.json |
| `scripts/ci/private_python_windows_snapshot.py`（新） | 注册表 `Software\Python`（HKCU / HKLM / WOW6432Node）、用户与系统 `Path` 值、进程 PATH / USERPROFILE、USERPROFILE 顶层条目、`py --list-paths`（只记）；`--diff` 逐字节比，USERPROFILE 顶层两个方向都比：新增只允许 `.matplotlib`（既有的 `probe_environment` 写的），少掉 / 改名一律算改动；判据用例 `tests/test_private_python_windows_snapshot.py` |
| 台账 | FO23 planned → **observing**（`lane: release`，`test: null`——observing 的 case 不指向用例，具名任务写在 notes；registry 同步）；`ENROLLMENT.md` / `generated/FIRST_OPEN_SCHEDULE.md` / `ALL_PROMPTS.md` 重生成（后两份之前就已过期，这次一并派生）；`test_foundation_harness` 的计数 planned 20 / observing 5 |
| 文档 | ADR 0064（三档证据、验证矩阵、翻 `enabled` 的条件）；`evidence/u05/empty-image-linux-arm64.json`；本段 |

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `TAVOTTO_PRIVATE_PYTHON_REAL=1 … pytest tests/test_private_python_transaction.py -k TestRealChain` | macOS arm64 本机；真 pbs 归档（scratchpad 缓存）+ 真 wheelhouse（mpl 3.11.1 / numpy 2.5.2 cp313 macosx arm64） | 0 | 计划明示（cached、0 字节）→ `downloading_python` → 建代（base = 私有）→ 装 → 验 → 切 active；venv `base_prefix` == runtime、3.13.15；PDF 10 616 B；9.0 s |
| `bash scripts/ci/private_python_empty_image.sh <linux-arm64 runtime> bin/python3 <wheelhouse-linux-arm64> <out>` | 本机 docker，`ubuntu:24.04`（arm64，glibc 2.39），归档 `303efcce…`（手工解开挂载——本机起不了 Linux 二进制，产品的 provision 在 ubuntu 腿上走） | 0 | 镜像里没有 python3 / python / pip / uv；`-I -c` 自报 3.13.15、prefix `/rt`；venv + 离线装 11 个 wheel；`pip check` 0；PDF 10 108 B（`evidence/u05/empty-image-linux-arm64.json`） |
| `python scripts/ci/private_python_windows_snapshot.py <a>` ×2 + `--diff` | 本机（POSIX：reg 不存在也是一种状态，逐字节比） | 0；篡改 PATH / 加 `.python-version` → 1 | 判据的两侧各量到 |
| `pytest tests/test_source_hygiene.py tests/test_merge_queue_workflows.py tests/test_e2e_leg_topology.py tests/test_release_workflow_contract.py` | 本机 | 0 | 186 passed |
| `actionlint .github/workflows/private-python-targets.yml` | 本机 | 0 | — |
| 三条腿的首次真实运行：run [35578901967](https://github.com/Tavotto/Tavotto/actions/runs/35578901967)（#467 的 `pull_request` 触发，head b93b782b） | ubuntu-latest / windows-latest / macos-latest | ubuntu 0 / macos 0 / windows 1 | **真链三腿全绿**：ubuntu 真下载 `x86_64-unknown-linux-gnu`（披露 118 484 282 B）→ 事务 → venv `base_prefix` == runtime → PDF 10 610 B，15.6 s；**空镜像**：`Ubuntu 24.04.5 LTS` / glibc 2.39 里无 python3 / pip / uv，runtime 只读挂载真起、venv、离线装 11 个 wheel、pip check 0、PDF 10 108 B；macos 真下载 `aarch64-apple-darwin`（25 304 407 B）→ PDF 10 610 B，33.7 s；windows 真下载 `x86_64-pc-windows-msvc`（47 131 996 B）→ 事务 → `base_prefix` == runtime → PDF 10 610 B，22.6 s，**注册表 / PATH / USERPROFILE 前后一致**（顶层新增 ⊆ {.matplotlib}）。windows 腿唯一的红在最后一步「机制用例」：替身找 `Lib/venv/scripts/nt/python.exe`，3.13 起启动器改名 `venvlauncher.exe`（装置问题，产品代码没碰）——A 分支 533fb736 两个名字都找，下一次运行看它 |
| 后续运行：run [35581821823](https://github.com/Tavotto/Tavotto/actions/runs/35581821823)（d3542bfd） | 同上 | windows 1 | 机制用例里 `test_two_processes_provisioning_the_same_runtime_both_succeed` 确定性红（退出 [1, 0]）：后到的进程 `os.replace(.part → 正式名)` 撞 Windows 共享冲突（先到的正在解包、文件打开着）→ write_failed。真链本身绿 |
| run [35602658293](https://github.com/Tavotto/Tavotto/actions/runs/35602658293)（c1468563，含 A 的 replace 复用修复） | 同上 | **windows 0 / macos 0**；ubuntu 排队 8 分钟后 cancelled（没起步） | Windows 腿 2 分 27 秒全过——`.part → 正式名` 被拒后复用正式名上校验过的那份，本机没有 Windows，这一条就是那笔修复的真判据 |
| run [35603820985](https://github.com/Tavotto/Tavotto/actions/runs/35603820985)（B 分支 c2b118f5，含 A + C 全部） | 同上 | **ubuntu 0 / windows 0 / macos 0** | 三腿全绿（含 Linux 空镜像、Windows 注册表快照一致、两进程供应）；里程碑合并后 #475 的 head 就是它 |

**本段的正例 / 负例**：正例 = 上表；负例 = 空镜像脚本的「镜像里有 python3 → exit 9」（用 `python:3.13-slim` 跑一次退出 9
——本机验过）、快照 `--diff` 对篡改的 PATH / 新条目非零。

**enrollment**：FO23 → observing（理由：具名非 required 任务真跑候选、保留真实失败——03 §3）；FO-022 保持 planned；
FO24 / 25 / 26 仍待 PR B 经真实入口提升。**没有取得任何平台的资格**（ADR 0064 §二 的矩阵：五个目标资格列全是「未取得」）。

## PR B：入口接线（叠在 U04 C 上）——干净机器的第一份计划、一次授权里的「先下载」、三条场景 enforced

**实际变更**：

| 层 | 变更 |
|---|---|
| `engine/privatepython.py` | `standin_marker_env(source)`：私有 Python 还没落盘时替它答 PEP 508 marker 环境（Python 字段来自锁、平台字段来自这台机器） |
| `engine/deprepair.py` | `NO_WORKER_PYTHON`；`private_python_target()` / `private_fresh_facts()`（真量 `fresh_venv_facts(private, provided=adapter)` 或替身 + adapter 已有 + 宿主 stdlib）；`joint_plan_for` / `create_joint_plan` 在干净机器上以私有 Python 为目标、`nothing_needed` 照样成计划；`_facts_for` 没 base 时用它量新的一代；`JointRepairPlan.replan`（计划带下载即真）；`_GenerationJob.replan / groups`；`_replan_on_base`（供应后按真解释器重算 delta / 关键 import / 记账 / 身份；**重算前先比规划输入的指纹** `JointPlan.inputs_digest`——声明意图 + 脚本与本地模块的字节，与事实无关；变了即 `repair_plan_stale`、一个字节不装，Codex #475 P1）；`_facts_for_plan`（执行前重量走同一条路）；`preparation_offer` 多 `private_python` 段、`joint_targets` 受管目标可用性看有没有 base + 带载荷、`gate` 有私有段时 `nothing_needed` 也问 |
| `engine/depplan.py` / `engine/importscan.py` | `JointPlan.inputs_digest`（`depplan.inputs_digest`：声明意图全集 + 扫描读过的文件的字节；载荷里也带）；`ScanResult.files`（脚本 + 跟进过的本地模块，相对路径排好序）——U04 的模块，两个纯增字段 |
| `engine/preparation.py` | `plan_for` 在 `no_worker_python` 时也问依赖门（一行；不提供私有 Python 时门回 None、照旧以原错误收场） |
| `codex-plugin/mcp/tavotto_mcp/bridge.py` | `recovery` 多一句「先下载 Python x（约 N MB）/ 不联网」；顶层没有、只挂在受管目标上（有渲染解释器没 base）时说「选择 tavotto_managed 时会先下载…」（Codex #475 P2） |
| `web/` | `DependencyPrepareDialog` 受管选项下一行（`data-dependency-private-python`；有缓存时说不联网）、`downloading_python` 进度文案、`api.ts` 的 `PrivatePythonOffer` 类型；两种语言三条文案；组件用例一条 |
| `tests/test_private_python_transaction.py::TestCleanMachine::test_a_second_project_on_the_same_machine_still_gets_the_gate_and_its_own_generation` | Codex #475 P1：别的项目供应过私有 Python 之后本项目仍是干净机器——门照样问（载荷「已就位」）、建自己的一代、零请求、`base_runtime` 记共享那份；门级用例 `test_the_gate_judges_by_clean_machine_not_by_the_download_payload` |
| `tests/test_private_python_transaction.py::TestPrivateBase` 的两条合同用例 | U04 C（#470 P1）两条合同在供应路径上：重复 prepare 不起第二个供应（一次请求、一个下载线程）；供应完登记这一代时清单写不进 → `managed_env_write_failed`、runtime 留着、恢复后不再下载 |
| `tests/test_private_python_transaction.py::TestCleanMachine`（5 条真事务） | 替身 → 供应 → 重算 → 建代 → 项目从此用它（真实解析链回受管环境）；只用标准库也建环境；不提供时照旧 `no_worker_python`；离线 safe_stop 不登记代；下载被扣住期间改 requirements + 脚本 → `repair_plan_stale`、零代零账、重新规划把新输入说出口 |
| `tests/test_foundation_private_python.py`（新，3 条经产品 HTTP 入口）+ fixture ⑨ `tests/fixtures/foundation/private_python/` | FO24 缓存齐备离线零请求 automatic（图内值 == 真值）、FO25 无缓存离线 safe_stop（投影无机器路径、有界、不建任何东西、再准备仍 needs_input）、FO26 篡改来源 safe_stop（旧 active 原样、runtimes 只有好的那份、坏归档的解释器一次没起）。**进程内 test_client**：「这台机器没有任何可用 Python」是发现链末端的输入，子进程形态凑不出诚实的无 base（文件头写明理由） |
| 台账 | FO24 / FO25 / FO26 → **enforced**（lane pr、entry `http-inprocess`；registry 的 promotion 合同；harness 集合与计数 planned 10 / observing 7 / enforced 15；ci.yml harness 步加这一文件） |
| 文档 | ADR 0063 §九之二 + 落地表；细则；本段；`evidence/u05/mutations_pr_b.md`；README / plan.json（U05 `implementation_status: done`，产品资格仍 `not_run`）|

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `PYTHONPATH=$WT/src pytest tests/test_private_python_transaction.py tests/test_private_python.py tests/test_dependency_transaction.py tests/test_preparation_api.py tests/test_dependency_repair.py tests/test_dependency_plan.py` | 同上；真 venv / 真 pip 离线 / 真 worker 自检 | 0 | 325 passed, 3 skipped（含 U04 的 30 条事务用例）|
| `PYTHONPATH=$WT/src pytest tests/test_foundation_private_python.py` | 同上；进程内 HTTP 入口、真渲染 | 0 | 3 passed（约 48 s） |
| `PYTHONPATH=$WT/src pytest tests/test_foundation_dependencies.py tests/test_foundation_harness.py tests/test_foundation_plan_integrity.py tests/test_foundation_fixtures.py tests/test_mcp_server.py -k …` | 同上 | 0 | U04 的五条 HTTP 场景仍绿；台账 / 计划 / 夹具门禁绿；MCP 两条 |
| `cd web && pnpm test && pnpm build && pnpm i18n:check` | 同上（worktree 里真 `pnpm install`） | 0 / 0 / 0 | 281 files / 4166 tests；build 绿；i18n 绿 |
| 变异 M24–M29、M40–M52（`evidence/u05/mutations_pr_b.md`） | 同上 | 每条非零 | 供应后不重算 / 干净机器不走私有 Python / nothing_needed 不建环境（用例第一版撞不上那条分支，改成只用标准库的脚本后红）/ 单包修复只在 creates 时问 / 进来前已取消不拒绝 / .part 打不开归成离线 / 重算前不比输入指纹（变异下 pip 真把新加的包装进去了）/ 指纹不含文件字节 / 不含声明意图 / 扫描不报本地模块文件 |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文 |

**顺带发现**：① worktree 里跑起子进程的用例（`running_app` / MCP）时 `PYTHONPATH` 必须是**绝对**路径——相对的 `src` 在子进程的 cwd 下解析不到，import 到主工作区那份，表现是 `/api/engine/preparation` 404（U04 五条场景假红，`shared-workdir-contention` 那条教训的又一形状）；② `deprepair.base_python()` 的进程内缓存在锁换版本（只随升级、进程重启发生）时不会自动失效——用例用 `reset_state()` 表达重启。

**Windows 上的替身能不能当 base——按真依赖的那一步量**（#475 db5f994a 的 `backend-platforms (windows-latest, 1/2)` 首次在 Windows 上跑事务用例：12 + 2 条 `managed_env_create_failed`，detail 空串；下一轮 85b96a8f 目标腿 runner 上替身 `-m venv` 却退出 0、目录齐全——**目录能建出来 ≠ 里面的解释器起得来**，venv 里的 python.exe 是个启动器，找不到真解释器时目录照样齐全）。处置：① 产品侧 `managedenv._venv_built`：`-m venv` 之后让 venv 里的解释器自报一次 prefix，起不来就是没建成、detail 说清哪一步（`_venv_failure_detail` 另外覆盖空输出 / 退出 0 无文件两档）；用例 `test_a_venv_whose_interpreter_does_not_start_is_not_built`，变异 M53（去掉起一次的判据）红；② `support.private_python.STANDIN_BASE_PROBE`：每次会话在 Windows 上探一次（建 venv → 起它的 python → pip），探不过的机器上「用供应出来的解释器建受管代」的用例（transaction 12 条 + FO24 / FO26）挂 `needs_real_base` skip-with-reason，探得过就真跑；③ 探测与真实的一致性由 `tests/test_private_python.py::test_the_windows_standin_base_probe_matches_reality` 看住（按产品路径供应替身、建 venv、起、pip，报告进 CI 日志；两边不一致就红）；④ Windows 的这条真链不缺证据：`private-python-targets.yml` 的 windows 腿用真 pbs 归档跑 TestRealChain（run 35603820985 绿）。enrollment 里 FO24 / FO26 的 notes 写明。

**enrollment**：FO24 / FO25 / FO26 → enforced（理由：真实正例 + 负例 + 预算 + lane，03 §4；进程内 HTTP 入口的理由写在用例文件头与 registry 的 target_scope；FO24 / FO26 在 Windows 的 pr lane 上 skip-with-reason，见上）；FO23 仍 observing；FO-022 仍 planned。**仍没有取得任何平台的 NO_SYSTEM_PYTHON 资格**——五个目标 `enabled` 全 false。

