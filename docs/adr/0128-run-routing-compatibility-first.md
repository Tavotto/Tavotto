# ADR 0128：运行前路由——桌面用户发起的运行里，凭静态证据在 safe / native 之间先选一次

日期：2026-10-14 · 状态：**Accepted**（兼容性改造 V2 PR2；用户 2026-10-14 裁决 D1–D5；叠在 [0127](0127-execution-policy-and-unified-retry-guard.md) 之上）
相关：[0127 执行策略与统一重试闸门](0127-execution-policy-and-unified-retry-guard.md)（本 ADR 兑现其 D2 里「路由器是后续 PR」）、
[0126 桌面拥有的脚本入口](0126-desktop-owned-script-input-and-run-entry.md)（「何时发 native」）、[0125 桌面拥有的 native 进程](0125-desktop-owned-native-process.md)、
[0003 worker 协议](0003-worker-protocol-v1.md) `safe_file_operation_blocked`、[0047 safe 档项目工作目录](0047-safe-profile-project-workdir.md)、[0057 首开环境与工作目录](0057-first-open-environment-and-workdir.md)；
规则全文 `docs/rules/backend/preparation-and-receipts.md`「运行前路由（V2 PR2）」。

## 问题

0127 的回退能兜住「safe 白跑一遍、被自己的守卫拦下」，但对**一眼就看得出**要保留文件语义的脚本（`savefig("t.png")` 之后 `Image.open("t.png")`；写临时文件 → `os.replace` → 读回），
先在 safe 里跑一遍再换 native，是一次可以省掉的整脚本执行（对慢脚本是分钟级）。D2 的目标是「运行前尽量识别最可能成功的方式」。

## 决策

### 路由器：`engine/runroute.py`，纯静态、零执行

`runroute.decide(facts, cwd_mode, project_root, script, policy, last_success, operator_enabled) → Route{execution, reason, confidence, evidence, dynamic_paths, blind_spots}`。
脚本事实 `ScriptFacts` 来自**一次** `ast.parse`（`facts_of` / `facts_of_file`，64 项 LRU，键 `(realpath, mtime_ns, size)`）；不 import 用户代码、不起进程、不连网络、不 `find_spec`，纯标准库。
**只在用户发起的运行 / 准备里被调用**：`preparation.plan_for(execution="auto")` 是唯一入口；被动扫描（`projscan` 一系）、CLI、MCP 不 import 它（`tests/test_runroute_boundary.py` 钉）。
不统一、不改别的扫描器（它们各自再解析一遍的成本见下「后果」）。

### 优先级（高 → 低）

1. **用户显式**：项目策略 `safe_only` → `safe`，reason `user_explicit_profile`。显式 `execution=safe|native` 的调用方（CLI / MCP / 回退自己规划的 native 计划）根本不经路由。
2. **运维开关**：`TAVOTTO_EXECUTION_FALLBACK=0` → `safe`，reason `operator_disabled`。一个开关关掉 V2 的全部自动选语义（直达 native 与 0127 的回退），否则「关了回退、却还在悄悄直达 native」就是名不副实的开关。（0127 的闭集之外多出的唯一一个原因。）
3. **上次成功沿用**：`launchctx` 记录（`profile: safe|native`）此刻仍成立 → 沿用那个语义，reason `previous_successful_profile`。「成立」= 解释器 / 环境代 / 脚本字节 / 运行目录档没变。
   上次是 safe 成功就留在 safe，即使静态规则会说 native：safe 的完整成功是被运行期账本验证过的。
4. **静态高置信**：规则 R1 / R3（下）→ `native`，reason `savefig_readback` / `native_file_semantics_required`（后者的 `required_execution` 与 0003 的 `preserve_file_semantics` 同一个词）。
5. 其余 → `safe`，reason `ordinary_plot`；`confidence` 说明有没有疑点（high 无 / medium 有 R2·R3_partial·跨函数读回 / low 只有动态路径或解析不了）。**低、中置信度永不升级**。

### 规则（审计 B §5，全部按 import 绑定解析限定名，不按尾名猜）

* **R1 high**：同一函数体内 `savefig(P)` 之后读 P（`open` / `PIL.Image.open` / `imread` 系 / `Path.read_*` / `np.load` / `pd.read_*`），P 折叠后规范路径相等，且两处不在互斥分支。与 cwd 档无关（safe 的 savefig 拦截对任何路径都生效）。文件对象（`BytesIO()`、`with open(...) as fh`）不是路径。
* **R2 medium**：`savefig(P)` 之后只对 P 做 `exists` / `getsize`。运行期账本对 savefig 目标的 stat 刻意不算观察（`print(os.path.exists(...))` 校验打印很常见），所以静态也不升级。
* **R3 high**：写 T → `os.replace` / `os.rename` / `shutil.move` / `Path.replace|rename`（T→F）→ 读（或 `exists`）F，且 **T 在真实图库内**（safe 的删除 / 改名守卫只看源）。T 在沙盒 / 系统临时目录里 safe 本来就真执行，不升格；
  `cwd` 相对路径是否在图库内取决于 cwd 档（`sandbox` 不在；`project` / `project_root` 在）。看不见写或看不见读 → `R3_partial` medium。
* **R4 low**：写 / 改名的目标折叠不出来（形参、`argv`、`input()`、时间戳命名、被多次赋值的名字）→ 只记 `dynamic_paths` 计数，safe + 0127 回退兜底。
* 路径折叠：字符串常量 / f-string / `+` / `%` / `.format`；`Path(...)` 链（`/`、`joinpath`、`with_suffix`、`.parent`、`.parents[n]`、`resolve`）；`os.path.join/dirname/abspath`；`__file__`；单赋值的 Name（含 `a, b = x, y`）；`tempfile.gettempdir()`。
  规范化后统一到「项目根内相对路径」再比较，所以脚本目录相对写法与 `project` 档下的 cwd 相对写法是同一个文件。
* **blind spots**（`Route.blind_spots` 如实随结果带出，禁止宣称覆盖）：跨函数 / 跨文件的路径传递、本地 helper 模块里的 savefig / replace（不跟进 `importscan`）、别名与反射、`subprocess` 搬文件、运行时才定的分支。覆盖不到的由运行期账本 + 0127 回退兜底。

### 接线

* `PreparationPlan.route`（`Route.public()`：闭集原因 / 置信度 / 规则 / 行号 / 耗时，**不含**路径与源码）。**不入语义指纹**（`plan.execution` 已在里面）；显式 safe / native 的计划 `route` 为 None 且不进 `to_payload()`，逐字节不变。
* `prepsession._plan_of`：脚本目标且调用方没给 `execution` → `auto`；素材目标仍 safe。`target_key` 把 `auto` 与省略同键。路由选 native 但没有解释器 → 回 safe，`declined: no_interpreter`，原因与证据保留。
* 命中原因与预检耗时：`tavotto.preparation` 日志 `run route …`、会话报告 `plan.route`、任务诊断快照 `route:{execution, reason, confidence, rules, dynamic_paths, elapsed_ms}`（闭集码，本地诊断 / 基准读；不进遥测）。
* **cwd 确认**：路由到 native 后，`workdir` 的「首开要不要问」（0057）**不短路**：native 的 cwd 只有「项目根 / 脚本目录」两档（`execspec.desktop_cwd`），而数据证据指向项目根时，这一问仍决定 native 用哪一个——短路会让脚本静默找不到数据。0047 的「不自动切换」说的是 safe 沙盒写入边界，与路由无关。
* **native 成功也记**：`launchctx` 记录升到 v2，多 `profile`（v1 读作 safe）。safe 成功仍由 app 的成功收口按回执写；**native 成功由 `prepsession` 在尝试成功后调 `launchctx.record_native_success`** 写（native 没有 helper 观察回执，记录取计划快照，`source.basis` 为空且 `basis_observed: false`：它回答「上次用哪种语义」，**不是**「数据与 helper 没变」的证据，#982 的沿用入口读它时要看这个标记）。脚本字节与计划的 `script_revision` 不一致（执行期间被改）→ 不记。
* 授权来源：与 0127 同一条——桌面用户主动「运行 / 打开并运行」+ 项目策略（缺省 `compatible`）；高置信静态证据只是**选择**，不扩大授权范围（同一个项目 × 脚本 × 解出的解释器）。`safe_only` 撤回。CLI / MCP 的确认合同不变。

## 后果

* 对 `savefig → 读回` / `tmp → replace → 读回` 的脚本，执行次数 2 → 1。静态判错（把 safe 能跑对的脚本送去 native）会丢掉沙盒守卫——所以只有 high 才直达，并有同源对看护：`tests/test_runroute_consistency.py` 对每个高置信形状用真 safe worker 实跑核对「确实被拦」，对几个 safe 形状核对「确实成功」。
* 漏判（动态路径等）退化为 0127 的「safe 白跑 + 回退」，无副作用（守卫拦住了）。
* 路由要多读一遍脚本：1093 行 / 42 KB 的脚本约 5–15 ms（审计 B 实测同量级；不是缺口，且有 64 项 LRU）。没有现成耗时门禁，本 PR 也只断言解析次数（每个文件修订 1 次）而不断言毫秒。
* 上次 native 成功沿用意味着：重新打开并运行会**真执行一次**，native 会真写盘——与 safe 重开同样会重跑是一致的，区别只在 native 的写是真的。用户随时可以在项目设置选 `safe_only`。
* 已知局限：`launchctx` 记录按脚本一份（不分 argv）；`native` 记录的 `cwd_mode` 取计划那一刻的档位。
* 证据只认能证明会执行、且确属 matplotlib 的调用（#1034 评审）：`savefig` 的接收者必须解析到 matplotlib（`plt.savefig`、`from matplotlib.pyplot import savefig`、`plt.figure()` / `plt.gcf()` / `Figure()` / `plt.subplots()` 得来的 Figure）；用户自定义同名函数 / 方法、形参、被重新赋值的名字解析不了，记 `R1_unproven`（medium，走 safe）。证据只出现在模块从未调用到的函数体里时（未调用的 helper、未被调用的 `main`），R1 记 `R1_unproven`、R3 降为 `R3_partial`，都不直达 native；经 `atexit` / 线程 / 回调间接调用的函数视为不可达——宁可走 safe 再回退。
* 用户显式 `execution: "safe"` 一律走 safe，路由不覆盖；省略（或 `auto`）才由路由决定。显式 safe / native 各有自己的会话键，不复用路由选了 native 的会话。

## 修订的既有条款

* 0003 `safe_file_operation_blocked` 行：桌面用户发起的运行里，高置信形状在运行前就被路由到 native，根本不会走到这个错误（该行的回退处置沿用 0127）。
* 0127 D2：「路由器是后续 PR」→ 本 ADR。
* 0126 残余「何时发 native」：发 native 的调用方现在有两个——0127 的回退与本 ADR 的路由（都在准备会话里，前端仍不发 `execution`）。
