# ADR 0133：运行前路由——桌面用户发起的运行里，凭静态证据在 safe / native 之间先选一次

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
3. **上次成功沿用**：`launchctx` 记录（`profile: safe|native`）此刻仍成立 → 沿用那个语义，reason `previous_successful_profile`。「成立」= 解释器 / 环境代 / 脚本字节 / 运行目录档没变，**且记录的运行配置（argv 的不透明引用）就是本次请求的运行配置**，不一致就不沿用。
   上次是 safe 成功就留在 safe，即使静态规则会说 native：safe 的完整成功是被运行期账本验证过的。
   **native 记录只在观察过输入时沿用**（#1047 修订）：`source.basis_observed: true` = 桌面拥有的 bridge 在脚本跑完那一刻报过完整的 helper 导入 / 数据读取观察，与 safe 记录一样逐个核 sha256；`false`（没观察 / 观察不全 / 收口在 `show()` 屏障上还没补）与 #982 的 `reuse_last_success` 同样保守，回到静态路由。记录照写（诊断与 #982 读它）。
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
* **native 成功也记**：`launchctx` 记录升到 v2，多 `profile`（v1 读作 safe）。safe 成功由 app 的成功收口按回执写；native 成功由**同一个**成功收口经 `launchctx.record_success`（`NativeBasis`）写，解释器 / 环境 / 运行目录档 / 绑定取计划快照。脚本字节与计划的 `script_revision` 不一致（执行期间被改）→ 不记。
  **helper / 数据依据（#1047 修订）**：桌面拥有的启动带 `--observe-inputs`，bridge 装上与 safe worker 同一个 `figcapture.InputObserver`（三处 Python open + numpy `DataSource.open`，后者在用户 import numpy 时由 import 钩子补），脚本跑完那一刻定格 `report()`（数据文件 + 项目内本地模块，项目相对路径 + sha256），经 build 响应的 `observed_inputs` 交给父进程——不进 `runtime`（回执身份不随「跑没跑完」变）、不进会话公开状态 / 事件。引擎自己的读（savefig 簿记算产物摘要、屏障里的预览 / 导出）暂停记账。收口时已有完整观察 → `source.basis` 与 safe 同形、`basis_observed: true`；收口在 `show()` 屏障上（脚本还没跑完，之后可能再读）→ 先记 `false`，脚本跑完、界面在结束屏障上 build 时由 `launchctx.observe_native_basis` 按 `launch_id`（计划号）补齐，只动同一次运行、尚未观察过的 native 记录。截断 / 有文件没算出摘要 → 留 `false`。终端拥有的运行（`tavotto run`）不观察，命令行逐字节不变。每次进 `show()` 屏障先记一份依据摘要（先见者赢），脚本跑完时任何一条与之不同或消失 → 不报观察、记录不沿用（脚本停着时改了 helper / 数据，进程执行的是旧内容）。
* 授权来源：与 0127 同一条——桌面用户主动「运行 / 打开并运行」+ 项目策略（缺省 `compatible`）；高置信静态证据只是**选择**，不扩大授权范围（同一个项目 × 脚本 × 解出的解释器）。`safe_only` 撤回。CLI / MCP 的确认合同不变。

### 保守边界与收口原则（#1034 第三轮起）

* **high 的结构性门槛（一条规则，取代逐个形状的补丁）**：R1 / R3 的每一个证据点（`savefig` 与读回；写、改名、读）都必须出现在**会无条件执行的位置**——模块顶层，或可达函数体的顶层语句序列——且**不在任何条件分支、循环、`try` / `except` / `finally`、`with` 体、`match` 分支、`lambda` / 推导式 / 三元 / `and`·`or` 里**；函数要靠无条件的按名调用才算可达。例外两处：`if __name__ == "__main__":` 的 body 按无条件处理（脚本入口的通行写法），`with` 的 item 表达式（`with open(p, "w") as f:` 的 `open`）在进入 body 之前无条件执行。其余位置证据最高 medium，走 safe，由 0127 回退兜底。常量假分支同属这一规则。
* **遮蔽**：不做作用域精析。matplotlib 别名（`plt`）在模块任何作用域被非 import 方式重新绑定过（赋值 / 形参 / `def` / `class` / `for`·`with` 目标 / `global`·`nonlocal`），或接收者名（`fig`）不是恰好绑定一次，一律当解析不了（`R1_unproven`，不升 high）。
* **不可达**：只排除条件是常量假的 `if` / `while`（`False` / `0` / `None` / `TYPE_CHECKING`）的 body，里面的调用与证据都不算；`else` 照常；其它形状不处理。
* **无后缀 `savefig`**（#1034 第五轮）：没有后缀且没有字面非 None `format=` 的 `savefig("stage")` 实际写 `stage.<savefig.format>`（运行期账本 `figcapture.savefig_effective_names` 同一规则，不记字面名）；静态侧不复刻 rcParams，这种调用的字面目标不算确定，不产生 R1 / R3 的 high（medium，走 safe）。字面 `format="svg"` 时文件名原样，照常。`**kwargs` / 多于一个位置实参 / 非字面 `format` 同样不算确定。
* **无条件终止之后**（#1034 第五轮）：同一语句序列里，`return` / `raise` / `sys.exit()`·`os._exit()`·内建 `exit()`·`quit()`（解析到且未被重新绑定）/ `assert False` / 没有 `break` 的 `while True` / body 以终止结尾的 `__main__` 守卫之后的语句，证据与按名调用都不再算「无条件执行」（不升 high）。只认能证明无条件的终止；条件内的终止不影响后面。
* **终止的外延**（#1034 第五轮续）：常量真分支（`if True:` / `if 1:`）的 body 以无条件终止结尾，视为外层序列的终止；`try` / `with` 块内（含 handler / `else` / `finally`）出现任何无条件终止，其后外层语句也不算——这类终止可能被吞掉，证明不了，按「宁可漏判」处理。本模块唯一绑定、无装饰器、非生成器的 `def f()`，函数体顶层序列在任何 `return` 之前无条件 `raise` / 已解析的 exit 族（可经同样的 `def` 链递推），则顶层 `f()` 调用视为终止（无条件 `return` 不算）；类体在定义时执行，其中的终止让后续模块语句不再算无条件。不做跨模块、数据流推导。
* **同一份内容**（#1034 第六轮）：`plan_for` 里路由分析的字节与计划的 `script_revision` 必须是同一份——路由读一次字节、记下 sha256，计划末尾算出 revision 后核对其字节摘要；不一致（分析之后脚本被改写）则 native 降回 safe，`declined: script_changed`。
* **`async def` / 生成器**（#1034 第六轮）：裸调用 `f()` 只造出协程 / 生成器对象，不执行函数体；`async def` 与体内（不含嵌套 def / lambda / 类）有 `yield` / `yield from` 的函数，体内证据不算可达（`R1_unproven`、R3 降 `R3_partial`），其调用也不算终止；不推导 await / 迭代是否发生。
* **`__main__` 守卫的前提**（#1034 第八轮）：`if __name__ == "__main__":` 的 body 按无条件处理，只在 `__name__` 在模块**任何位置**从没被绑定时成立（赋值 / augassign / 注解赋值 / walrus / `del` / `for`·`with`·`except` 目标 / 形参 / `def`·`class` / `import as` / `global`·`nonlocal` / `match` 捕获，或出现 `globals()` / `vars()`——宁宽）。否则守卫体不算无条件。
* **Windows 根路径**：`C:\`、`C:/`、`C:foo`、`\\server\share`、单分隔符根 `\foo` 一律按「不在项目内」，用 `ntpath` 判，不依赖当前平台。
* **native 启动记录**与运行默认值走 `runconfig.commit_success` 同一次写入，不分两步。
* **收口原则**：静态规则还会被找出新的边角。**漏判**（该升 high 没升）退化为 safe + 0127 回退，无副作用，记 issue 收口、不在评审轮里逐个追；只有**误判升 high**（不该直达 native 却直达了）才继续修。新增规则一律往保守方向走。

### 伤害闸门（#1034 第七轮起，取代「逐个证明可达」）

前几轮不断补「这条证据走不走得到」（常量假分支、终止语句、`async` / 生成器、`__name__` 改写、条件绑定的路径……），评审每轮都能再找出一种写法。这一族是**静态可达性证明**，补不完；而它们的**伤害**始终是同一个：路由成 native 后，safe 守卫本会拦下的文件操作（删除 / 改名 / `write_text`）真实执行了。所以改为结构性收口：

* **闸门**：脚本源码全文件（任何位置——函数体 / 条件 / 类体 / lambda，不做可达性推导）只要出现 safe 守卫会拦的操作形状，`auto` 路由一律不出 native 高置信：回 safe，reason `guarded_file_ops_present`，置信度 medium，R1 / R3 证据照实带出。靠 0127 的静默回退兜底——真需要 native 的脚本多一次 safe 白跑，但守卫拦住了，无副作用。
* **守卫表同源**：形状清单取自 `figcapture.BlockedFileOps.OPS`（safe worker 守卫记账的同一张表）去掉 `savefig`；`runroute` 导入它（唯一允许的非标准库导入，`test_runroute_boundary` 钉），不另抄。匹配宁宽勿窄：限定名（`os.remove|unlink|rmdir|rename|replace`、`shutil.rmtree|move`，按 import 绑定解析，头名被重绑也按字面名算）；方法名 `.unlink` / `.rmdir` / `.rename` / `.rmtree` / `.write_text`；单实参的 `.replace(x)`（`Path.replace`；`str.replace` 至少两个实参）。`list.remove` / 泛名 `.move` 只认限定名。表里没有的（`write_bytes`、`open("w")`）以守卫表为准，不算。
* **写入同属闸门**（#1034 第八轮）：safe 模式下普通写入落到沙箱、native 落到真实目录，二者的差异本身就是伤害面（`open("victim", "w").write(...)` 在 native 里会截断用户真实的 `victim`）。所以写打开也算守卫操作：`open` / `io.open` / `Path.open` 的 mode 为字面量且含 `w` / `a` / `x` / `+`，mode 不是字面量或实参带 `*` / `**`（判不了，宁宽），`os.open` 的 flags 不是 `os.O_RDONLY`；另有 `write_bytes` / `mkdir` / `touch` / `os.mkdir` / `os.makedirs` / `shutil.copy*`。只读打开（默认 mode、`r` / `rb`、`PIL.Image.open`）不算；`savefig` 本身不算（它是证据）。
* **已知写出器同属闸门**（#1034 第九轮）：事件收集里已认的写出器（`_NUMPY_WRITE`、`_DATA_WRITE_TAILS`）与常见科学 / 数据写出口按方法名或限定名末段认，不看接收者——`save` / `imsave` / `imwrite` / `savemat` / `to_netcdf` / `to_zarr` / `to_sql` / `write_image` / `symlink_to` / `chmod`…；限定名 `os.write|truncate|symlink|link|chmod|chown|utime|renames|removedirs`、`sqlite3.connect`、`dbm.open`、`shelve.open`、`shutil.copy*|make_archive|unpack_archive`；**任意调用**的 `mode=` 实参或第二个位置实参是可写的 mode 样字面量（`ZipFile(p, "w")`、`h5py.File(p, mode="a")`、`tarfile.open(p, "w:gz")`），字符串方法（`replace` / `split`…）除外。`.rename` 只认恰一个位置实参、无关键字的形状（`Path.rename(t)`；`df.rename(columns=…)` 不算）。这仍是启发式清单：清单外的写出（第三方库自定义写函数）看不见，记 #1086。
* **后果**：R3 的链（写 → `os.replace` / `rename` / `move` → 读）本身含守卫操作，所以 R3 在生产路径上不再直达 native（规则与证据仍计算、仍进 `Route.evidence`）；R1（`savefig` → 读回）不含守卫操作的脚本照旧直达。不含守卫操作的脚本误判的后果与终端直接运行相同，没有伤害。
* **源码解码**（#1034 第十轮）：按 Python 自己的规则解码（PEP 263 声明 / BOM / 默认 UTF-8，复用 `scriptargs.decode_source`）；解不开就当作没读懂（`parsed=False`，走 safe）。不用 replacement 字符糊过去——那会把 Latin-1 的 `café` / `cañ` 折成同一个路径，伪造出读回。
* **路径与当前目录**（#1034 第十一轮）：路径规范化只折叠证明得了的写法——POSIX 上反斜杠是文件名字符、任何 `..` 段可能经符号链接指向别处，都按「不可比」处理（不升格）；`Path.parent` 按字面取上级。脚本任何位置调用 `chdir` / `fchdir`（含 `contextlib.chdir`）时，cwd 相对的「存」与「读」可能不在同一目录，R1 / R3 不直达（medium，走 safe + 回退）。
* **不在静态可见范围**：`subprocess` / `ctypes` 搬文件、第三方库自定义写函数看不见，记 #1086。动态分派这一类已由下节的「不可证明门槛」结构性收口，不再逐个补写法。

### 不可证明门槛（#1086，取代对动态混淆写法的枚举）

伤害闸门是「列出看得见的操作」；`getattr(os, "re" + "move")`、`importlib.import_module("os").remove`、`exec(…)`、`__import__("os").remove` 让调用目标**静态不可证明**，闸门看不见，结果是 safe 守卫缺席、操作在 native 真实执行（失败开放）。所以判据从「没看到受守卫操作」改成「能证明没有受守卫操作」的一类规则：

* **规则**：脚本任何位置（不做可达性推导，同闸门）出现**证明不了调用目标**的动态分派，R1 / R3 一律不直达 native：回 safe，reason `unprovable_dynamic_dispatch`，置信度 medium，证据照实带出，靠 0127 静默回退兜底。事实 `ScriptFacts.dynamic_dispatch`（`_Collector.has_dynamic_dispatch`）。
* **形状类别**（不是函数名清单）：名字实参不是字面字符串的 `getattr` / `setattr` / `delattr` / `methodcaller` / `attrgetter`（或它们被当值传走）；`exec` / `eval` / `compile` / `__import__`（裸名或 `builtins.X`）与 `importlib.import_module` / `runpy` / `pkgutil.resolve_name` 一类按名取模块、跑文件的入口；`globals()` / `locals()` / 无实参 `vars()`、`__dict__` 等反射面、`sys.modules`。字面属性名 `getattr(obj, "foo")`、`re.compile`、`df.eval` 不受影响。
* **不扩权**：只在本会升 high 时降级，其余路由逐字节不变；只会让路由更保守。
* **与 MCP argv 门的关系**：argv 门的「不可证明」是 argparse 前缀专用的判据（`scriptargs.response_file_prefixes`，失败封闭），与此处是两个不同的主语（argv token 会不会被展开 vs 调用目标是谁），没有可共用的帮助函数；本节不动 argv 门。
* **用户可见代价**：只发生在「本会静态直达 native 且脚本含动态分派」的脚本上，退化为 safe 先跑一次、因 `safe_file_operation_blocked`（`blocked` 类，`execpolicy.may_fall_back`）被拦后桌面发起的运行静默回退 native（`prepsession._plan_fallback` / `execpolicy.decide_fallback`）：多一次 safe 执行，结果相同。`safe_only` 项目或关了回退时不回退（与其它 safe 路由一致）。

## 后果

* 对 `savefig → 读回` / `tmp → replace → 读回` 的脚本，执行次数 2 → 1。静态判错（把 safe 能跑对的脚本送去 native）会丢掉沙盒守卫——所以只有 high 才直达，并有同源对看护：`tests/test_runroute_consistency.py` 对每个高置信形状用真 safe worker 实跑核对「确实被拦」，对几个 safe 形状核对「确实成功」。
* 漏判（动态路径等）退化为 0127 的「safe 白跑 + 回退」，无副作用（守卫拦住了）。
* 路由要多读一遍脚本：1093 行 / 42 KB 的脚本约 5–15 ms（审计 B 实测同量级；不是缺口，且有 64 项 LRU）。没有现成耗时门禁，本 PR 也只断言解析次数（每个文件修订 1 次）而不断言毫秒。
* 上次 native 成功只在观察过输入时沿用（见优先级 3）：观察只覆盖 Python `open` 与 numpy 两类通道（与 safe 相同的盲区：h5py / netCDF / C 扩展直读、`os.open`、网络），这些通道读的数据变了不会让记录失效——与沿用 safe 成功同一个风险面。收口在 `show()` 屏障上、用户没放行到脚本结束就关掉的运行，记录保持不沿用（下次多跑一次 safe）。沿用意味着重新打开并运行会真执行一次，与 safe 重开同样会重跑。用户随时可以在项目设置选 `safe_only`。
* 已知局限：`launchctx` 记录按脚本一份（不分 argv）；`native` 记录的 `cwd_mode` 取计划那一刻的档位。
* 证据只认能证明会执行、且确属 matplotlib 的调用（#1034 评审）：`savefig` 的接收者必须解析到 matplotlib（`plt.savefig`、`from matplotlib.pyplot import savefig`、`plt.figure()` / `plt.gcf()` / `Figure()` / `plt.subplots()` 得来的 Figure）；用户自定义同名函数 / 方法、形参、被重新赋值的名字解析不了，记 `R1_unproven`（medium，走 safe）。证据只出现在模块从未调用到的函数体里时（未调用的 helper、未被调用的 `main`），R1 记 `R1_unproven`、R3 降为 `R3_partial`，都不直达 native；经 `atexit` / 线程 / 回调间接调用的函数视为不可达——宁可走 safe 再回退。
* 用户显式 `execution: "safe"` 一律走 safe，路由不覆盖；省略（或 `auto`）才由路由决定。显式 safe / native 各有自己的会话键，不复用路由选了 native 的会话。

## 修订的既有条款

* 0003 `safe_file_operation_blocked` 行：桌面用户发起的运行里，高置信形状在运行前就被路由到 native，根本不会走到这个错误（该行的回退处置沿用 0127）。
* 0127 D2：「路由器是后续 PR」→ 本 ADR。
* 0126 残余「何时发 native」：发 native 的调用方现在有两个——0127 的回退与本 ADR 的路由（都在准备会话里，前端仍不发 `execution`）。
