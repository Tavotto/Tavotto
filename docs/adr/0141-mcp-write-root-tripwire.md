# ADR 0141：MCP 来源 safe 运行的写入根绊线——路线选择与授权分离，授权由运行期在事件发生时判

日期：2026-10-20 · 状态：**Proposed**（#898；风险高、1.0 分类 safety；**临时缓解，根因未解**）
相关：[0047 safe 档项目工作目录](0047-safe-profile-project-workdir.md)、[0127 执行策略与统一重试闸门](0127-execution-policy-and-unified-retry-guard.md)、
[0133 运行前路由](0133-run-routing-compatibility-first.md)、[0014 safe / native](0014-safe-native-execution-profiles.md)、[0003 worker 协议](0003-worker-protocol-v1.md)；
规则全文 `docs/rules/backend/execution-entries.md`「写入根绊线」、`figure-capture-and-execution.md`「写入根绊线」；`SECURITY.md`。

## 问题

MCP 的 `argv` 是 Agent 自己给的 token，会原样成为脚本的 `sys.argv`。桥里的范围检查（`_check_argv_scope`）靠**静态读源码**
证明「脚本在这些参数下不会写授权根之外」——`os.chdir` / `contextlib.chdir` / 跨文件 helper 里切目录之后的相对 token，在检查那一刻还指向根内，
脚本跑起来却落到根外（#898 的 A2 / A2′，本地良性探针复现）。补静态黑名单是补不完的。

## 第三路径审查

* **共同前提**：用静态读源码证明动态 Python 的副作用。#898 把静态结论当**授权**用；#1086 只用它**选路线**，但误选 native 会让 safe 守卫整体失效。
* **第三路径**：路线选择与授权分离——授权不来自静态证明，而来自用户批准 + 运行配置身份 + **运行期在事件发生时的判定**。
* **已排除**：①「检查时脚本字节摘要 ↔ spawn 前比对」（做过实现与反证：只把毫秒窗口挪了个位置，对「Agent 先改授权根内的脚本再正常打开」完全无效）；②静态黑名单（与 #1086 裁决一致，不补）；③OS 沙盒（1.0 收敛期不做）。

## 决策

1. **只对 MCP 来源的 safe 运行默认开。** GUI / native / CLI 不装。MCP 桥 `_spawn_worker`（桥里唯一取 worker 的入口）把 `allowed_roots()` 作为 `write_roots=` 传给池。
2. **机制**：`engine/writetripwire.py`，`sys.addaudithook` 的 `open` 事件（写标志）与 `os.rename|remove|rmdir|mkdir|truncate|symlink|link`、`shutil.copy*|move|rmtree` 事件，
   在事件发生时按**真实 cwd** 做 `realpath` 后比对白名单；根外 → `PermissionError`。窗口 = 脚本 build（`arm(True)` … `finally` 里 `arm(False)`）；窗口之外引擎自己要导出到任意临时目录，不拦。
   脚本即使 `except PermissionError` 吞掉，build 结束时也以 `write_outside_roots` 失败——越界的**尝试**发生过，这次不算成功。
   2026-10-26 兼容修订：对已存在的根外目录，`mkdir` 审计钩子直接抛 `FileExistsError(EEXIST)`、不进入创建系统调用。
   `Path.mkdir` / `os.makedirs(exist_ok=True)` 因而保留无操作语义，不记录越界写；目录检查后被删除也仍抛错，不允许竞态创建。不存在的目录与其余写入照常拒绝。
3. **身份**：根经 `execspec.worker_argv` 的 `--write-roots`（唯一出处）进 worker 命令行，所以 workerd 的 spawn 规格哈希（`argv + env + cwd`）天然不同；
   池键 / 缓存目录再并入 `execspec.write_roots_key`（`pool._worker_key` / `_worker_base`）。MCP 与 GUI 取同一脚本得到**不同**会话（实验发现的必要条件：否则 GUI 取到带绊线的会话，或 MCP 取到没有绊线的会话）。
   不用环境变量传：env 在 workerd 路径下只是增量，易遗漏，池键也不感知。
4. **白名单**（`writetripwire.infrastructure_roots` 每一项带理由）：命令行的授权根（= MCP `RootAuthority` 快照里**可信**的根，与 `check_scope` 是同一份）；会话沙盒与会话缓存目录；
   `tempfile.gettempdir()`；`MPLCONFIGDIR` 与 matplotlib 的缓存 / 配置目录；平台用户缓存目录（`XDG_CACHE_HOME`、`~/.cache`、macOS `~/Library/Caches`；Windows 只放行 `%LOCALAPPDATA%` 下的 `Temp` / `pip` / `matplotlib`，**不放行整个 `%LOCALAPPDATA%`**——Tavotto 数据目录在它下面）；
   seaborn（`SEABORN_DATA` 或 `~/seaborn-data`，C0 实测的真误拦）与 scikit-learn（`SCIKIT_LEARN_DATA` 或 `~/scikit_learn_data`）的数据目录。
   **不放行** `sys.prefix` / `sys.base_prefix` 整体（会放行整个环境目录）：只放行 `__pycache__` 里的 `.pyc` 形状（含 importlib 的 `.pyc.<id>` 临时名）和建 `__pycache__` 目录本身；
   **不放行**整个 Tavotto 数据目录（里面有配置与凭据，会话缓存目录已单独列出）；`/dev/*` 放行（设备文件）。
5. **错误码** `write_outside_roots`：稳定码，经 worker `ProtocolError` → 池 `WorkerError` → MCP `BridgeError`；带**相对项目根**的被拒路径（不暴露无关绝对路径，也不列授权根），
   恢复建议「改成写到项目内，或请用户在 Tavotto 窗口里自己运行」。**不属于** `execpolicy.BLOCKED_CODES`：它不触发「换 native 重跑」（native 没有绊线，换过去等于放行）。
6. **回滚**：引擎能力开关 `write-root-tripwire`（`capabilities.FEATURES`）。从 `FEATURES` 去掉，桥就不再传参；旧引擎没有它同理——新插件配旧引擎不传参（行为照旧），旧插件配新引擎不传参。不靠环境变量。

## 它是绊线，不是沙盒——盲区（用例里断言「不挡」，防止误宣称）

* `ctypes` 直调 libc、`subprocess` / `os.system` 起的外部进程、C 层 `fopen`（h5py / netCDF / sqlite3 的 C 层打开）；
* `dir_fd` 相对打开（审计事件里没有目录 fd 的路径）；
* 脚本自己调 `writetripwire.arm(False)` 关掉开关（钩子装上摘不掉，但开关在脚本够得到的进程里）；
* 绊线窗口（build）之外：脚本留下的线程、`atexit`、后续协议请求期间的写；
* `os.chmod|utime|setxattr` 这类改属性的事件没纳入。

**不对抗恶意脚本。** 它只防「合作但有 bug / 被误导的脚本」把写落到授权根之外；威胁模型里「Agent 先改授权根内的脚本再正常打开」仍在，本 ADR 不解决它（因此 PR 写 `Refs #898` 而不是 `Fixes`）。

## 实验证据（C0，基线 9ab0daa14）

* 误拦：181 个起真实 worker 的测试文件 + CompatBench 149 个 case，`log` 模式下 **0** 条；手工补的真机形状里，被拦的只有 `~/seaborn-data` 一类用户目录缓存（→ 白名单）。
* 开销：真实 worker build 中位数 668 → 677 ms（约 +1.3%，噪声内）；每次写打开约 +29 µs，读打开约 +1.5 µs。

## 后果与风险

* 真机用户目录缓存（pooch / huggingface / torch hub …）若不在白名单的缓存目录里会被拦：这是预期方向（给出路径与改法），但白名单漏项会让合法脚本失败——所以先只对 MCP 开、错误里带可行动的恢复建议。
* 钩子装上摘不掉：worker 生命周期内一直带着，窗口外靠开关变哑。
* 新增引擎模块 `writetripwire` 进 `worker._ENGINE_MODULES`（私有包装载闭包），`packaging/tavotto.spec` 与导入架构基线同步登记。

## 未验证

Windows（路径大小写、8.3 短名、UNC、`ntpath.realpath`）、conda 激活环境、h5py / netCDF4 / pandas / scipy 的真实写路径、全量 pytest。
