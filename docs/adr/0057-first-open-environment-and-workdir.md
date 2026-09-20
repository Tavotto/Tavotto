# ADR 0057：统一实施包 U03——环境选择前移、cwd 三分的生产者、首开的一次确认与 safe worker 的私有命名空间

日期：2026-09-20 · 状态：**Accepted**（U03 阶段；后续阶段按 §六 修订）

## 背景

U01（ADR 0053）把「准备」做成了一份可读的计划与一个异步接口，但计划**只读**产品当时的决定，
而那些决定本身有四个已实测的缺口（`U00_BASELINE.md` §1.6 / §4；registry FO01–FO19）：

1. **项目 venv 只在跑错之后才被找出来**：`pool.resolve_worker_python` 的链条是显式 > 记住 >
   内置 / 自身 / 系统；项目自带的 `.venv` 只在内置环境报 `missing_dependency` **之后**由
   `try_project_env` 接手。FO11 明令「首次用户脚本执行前选对项目解释器，不先用内置跑错一次」。
2. **失效的显式选择被静默替换**：`select_worker_python` 对存在但 import 不到 matplotlib 的候选
   直接滑到下一条——用户在设置里指了 A，图是 B 画的，界面一个字不说（FO15 / FO16 / FO-013）。
   记住的项目解释器失效时同样只留一行 warning 就回退默认链条。
3. **cwd 只有两档，而且不问**：`sandbox`（默认，只读回退到脚本目录）与 `project`（脚本目录，
   ADR 0047）。`paper/scripts/figure.py` 读 `data/x.csv`、数据在 `paper/data/` 的项目（FO02）
   两档都读不到；脚本目录与项目根各有一份同名数据（FO07）时没有任何机制先问一次。
   `execspec` 的 `project.root` 来源自 U01 起只是占位，没有生产者。
4. **safe worker 把引擎模块留在用户的顶层命名空间**：`worker.py` 把 engine 目录永久插进
   `sys.path[0]` 并平铺 `import manifest / overrides / …`，用户项目里同名的模块永远轮不到
   （issue #447 / FO19）。native bridge 早就用 `bridgeboot` 的私有包解决了同一件事。

另有两件小事随手落地：静态扫描把 IO / 解码 / 语法错误与「确认非绘图」压成同一个 `None`
（FO-016 / FO-017），宿主 AST 不认识的合法语法让脚本从列表消失（FO12）。

## 决策

### 一、解释器选择前移：项目 venv 进入首开链条，失效的显式选择停下来说原因

`pool.resolve_worker_python(figures_dir, *, script=None, discover=True)` 的优先级变成五条：

| # | 来源 | 失效时 |
|---|---|---|
| 1 | `TAVOTTO_WORKER_PYTHON` | 路径不存在 → 按「没设」（shell rc 里的过期值，既有语义，用例钉着）；存在但用不了 → **`explicit_python_unusable`** |
| 2 | 设置里指定的 | 不存在 / 用不了 → **`explicit_python_unusable`**（reason = missing / no_matplotlib） |
| 3 | 项目记住的 | 用户为项目挑的（`automatic=False`）失效 → **`project_python_unusable`**；自动记住的失效 → 作废（`projectenv.forget`）并记 `invalidated_decision()`，继续第 4 条 |
| 4 | **项目自带的 venv（新）** | `projectenv.first_open_candidate`：`discover` → `probe_environment`（版本 / prefix / matplotlib / worker 模块）→ 第一个健康的采用并 `remember(automatic=True, trigger=first_open)`；不健康的进 `rejected` 带 code |
| 5 | 内置 / 自身 / 系统 | `select_worker_python()`，一字不变 |

三条纪律：**显式选择不被自动推断覆盖**（1–3 里用户的选择失效一律停下来，异常上挂
`explicit = {source, python, reason}`，四类入口都能说出是哪一条）；**发现与体检每进程每项目
一次**（`first_open_candidate` 按项目缓存；`discover=False` 给只想读「此刻的决策」的环境状态
端点，它不起子进程）；**用户明确选回默认链条要记成决定**（`projectenv.remember_default`，
`mode == "default"`），否则下一次首开又把 venv 发现出来盖掉用户的选择。「整个解释器」是选择
的单位：候选按路径字符串去重不 realpath（既有），同一 base 的两个 venv 各自选各自的，回执的
私有失效键（prefix）不同（FO17）。

`preparation.plan_for` 就是「前移」的落点：计划里 `environment` 多了 `evidence`
（体检当时的 python / matplotlib 版本、支持档）、`discovery`（找到的候选与各自被拒的原因，
项目相对路径）、`invalidated`（刚作废的自动决策）、`error.explicit`。计划仍然只读——它读的
是 `resolve_worker_python` 已经做完的决定。

### 二、cwd 三分的生产者：第三档 `project_root`，`project` 语义不变

`execspec.CWD_MODES = (sandbox, project, project_root)`。`project_root` 的 cwd 是项目根
（`figures_dir` 原串），`launch_context` 派生 `cwd_origin = project.root`、
`write_mode = project_dir`；`worker_argv` 与 `project` 一样只多 `--cwd <目录>` 两个 token，
worker 本身一字不改（它只认「cwd 换到哪」）。**`project` 过去是、现在也是脚本目录**
（ADR 0047）——项目根是加的第三档，不是对它的重新解释；三条 spawn 路径仍从 `workdir.mode_for`
取模式，native 仍然没有这个维度。四个 `CWD_ORIGINS` 从此各有且只有一个生产者。

### 三、首开的一次确认：静态证据决定要不要问，问了就记住

`workdir` 从「模式」变成「决定」。项目设置里 `workdir` 键**不存在 = 没决定过**；
`{"mode": "sandbox", "decided_at": t}` = 决定继续用沙盒（没有授权）；
`{"mode": "project" | "project_root", "granted_at": t}` = 决定过且授予了真实 cwd 写入许可。
`grant_for()` 多了 `cwd_write.mode` 与 `decided`；切回沙盒撤销授权但**决定留着**（首开不会因为
用户选了沙盒就每次再问）；`forget()` 才回到没决定过。

判据只有一份：`workdir.decision_for(root, script)`，准备计划读它写进 `workdir_decision` /
`required_input`，`pool._new_worker()` 在起任何会话之前调 `resolve_mode()`——四类入口
（HTTP 渲染 / 准备接口 / MCP 的 `session.acquire()` / probe）都从这里起会话，门只有一处。
证据来自 `engine/databinding.py`（纯标准库、静态、不执行）：脚本源码里像相对数据路径的字符串
常量（带数据类扩展名或目录分隔符；f-string / `%` / glob 模式一律不算；存图调用的实参归输出），
在「脚本目录」与「项目根」下各自是不是一个文件。五档结论：

| verdict | 含义 | 首开 |
|---|---|---|
| `none` / `unknown` | 没有相对路径字面量 / 一处都找不到 | 不问，按默认（沙盒）；真跑出来的失败经既有 `no_figures_captured` 路径可见 |
| `default_ok` | 脚本目录找得到（沙盒的只读回退够用） | 不问 |
| `project_root` | 只有项目根找得到 | **问**，推荐项目根（FO02） |
| `ambiguous` | 两处都有且内容不同 / 两处各有一半 | **问**，不预选，机器不裁决（FO07） |

问的形状是结构化的「需要输入」（`workdir.confirmation_payload`）：`code =
workdir_confirmation_required`、`reason ∈ {project_root_evidence, ambiguous_data}`、三个选项
各带该目录下找得到的字面量、`recommended`、`conflicts`、`answer`（`PATCH /api/engine/workdir`）。
准备接口新状态 **`needs_input`**（终局、不起线程、`result.required_input`）；渲染 / 导出等端点
以 500 + 同一 code + `confirmation` 字段返回（状态码与别的 worker 错误一致，语义在 code）；
MCP 的 `open_figure` 拿到同一 code 的结构化 BridgeError。**不猜、不就近替换、不自动切到真实
cwd**——ADR 0047 的「不自动切换」原样成立；数据找不到时也**不按同名搜索**（FO08）。

过期授权（FO-007）：执行线程在起会话之前把 `workdir.grant_for(root)` 与计划记下的 `grant`
比一次，不一致（撤销了 / 换了模式）就报 `preparation_plan_stale`，一行脚本不跑。

### 四、safe worker 经 `bridgeboot` 装引擎模块（issue #447 / FO19）

`worker.py` 与 `bridge_runner.py` 同一套装载形态：摘掉 CPython 自动塞进 `sys.path[0]` 的 engine
目录 → 按文件路径装 `bridgeboot` → `load_engine_modules(HERE, _ENGINE_MODULES)` 把整条平铺
import 闭包装进 `tavotto_bridge_runtime.*`，`sys.path` 逐字还原、顶层名字还给用户。safe 档
一次装完（不像 native 要分两阶段避开 matplotlib——safe 由 Tavotto 挑解释器，backend 在装引擎
之前就钉死）。`_ENGINE_MODULES` 是清单也是边：`tests/test_runtime_build.py` 从它反推 PyInstaller
spec 的 datas，`tests/test_import_architecture.py` 把它登记成 worker.py 的 extra_edges。

### 五、静态扫描的分类与目标解释器

`discover.read_source` 按 PEP 263 声明 / BOM 解码（`tokenize.detect_encoding`），`parse_source`
用宿主 `ast`；`inspect_script` 回 `{info, problem, parser, entry_candidates}`，problem ∈
{io_error, decode_error, syntax_error}，与「确认非绘图」（`info=None, problem=None`）分开。
报告多 `problems` 表，清单（`probe.script_inventory`）多 `problem` / `parser` 字段——
`reason` 的闭集不变，`unparseable` 仍是那一档，只是说得出是哪一种。

宿主判语法错误而项目的解释器是另一个（`resolve_worker_python(discover=False)` 的决策）时，
`analyze_in_interpreter` 在那个解释器里 `-I` 起一个子进程装本模块跑**同一份**分析（合成
`tavotto.engine` 命名空间包让相对 import 解析到 engine 目录；只解析，绝不 import 用户脚本；
按文件内容缓存）：它解析得了就按它的结果算（`parser == "target"`），脚本不从列表消失；它也
解析不了才是确认的语法错误，两边版本都记进 problem。目标解析器起不来 / 超时时保留宿主的判断并
记 `parser_error`。

## 不做的事

* 不在 `pool` / `workdir` 里猜：证据说不出话时走默认；同名文件不搜；绝对路径不碰（FO04：
  明确有效的外部绝对路径沿用，不搬数据目录）；任意绝对路径的重新定位归 X01。
* 不做每脚本一份 cwd 决定：决定是项目级的（与 ADR 0047 同粒度）。同一项目里脚本形状混杂
  （一半读同目录、一半读项目根）时用户要按项目选一档——记为已知边界，看真实用户数据再说。
* `databinding` 只认单个字符串常量：`os.path.join("data", "x.csv")` / `Path(...) / "x"` 拼出来
  的不算（那时是 `unknown`，走默认）。复用 `discover._Analyzer` 的抽象求值是后续的事。
* 不改 worker 的守卫、savefig 捕获、写回事务、会话认证；不装包（U04）；不改默认后端。
* 不给 HTTP「需要输入」换状态码：三条门禁钉着 worker 错误响应的字面量 `, 500`。

## 与既有规则的关系

| 既有规则 | 本阶段 |
|---|---|
| ADR 0018：解释器优先级链、只认本地 venv | 链多一条（项目 venv 前移到首开）；发现范围不变（仍只在项目根内、不顺软链接出去） |
| ADR 0044：系统解释器是修复候选、不无感切换 | 不变。首开只无感采用**项目内**的 venv（它在用户交给我们的边界之内） |
| ADR 0047：`project` = 脚本目录、首次开启确认一次、不自动切换 | 语义不变；多第三档；确认从「设置里的开关」扩到「首开按证据问一次」 |
| ADR 0053：LaunchContext 派生视图、`project.root` 占位、grant 只记时刻 | 占位有了生产者；grant 多 `mode` / `decided`；计划仍只读 |
| ADR 0020 / 0021：native 的所有权与环境 | 一字不动；`bridgeboot` 反过来被 safe worker 复用 |
| 根 AGENTS「守卫不放松」 | 守卫原样；`project_root` 与 `project` 一样只换 cwd |

## 看护

`tests/test_first_open_environment.py`（真实 venv：前移、FO17、显式失效三种、自动作废、默认链
条不被盖回、FO11 按 alt python 可用性）、`tests/test_first_open_workdir.py`（决定 / 门 / 第三档 /
真 worker 在中文空格路径的项目根运行）、`tests/test_databinding.py`（五档结论与「不猜」的负例）、
`tests/test_discover_problems.py`（编码 / 分类 / 目标解析器真子进程）、`tests/test_preparation_api.py`
（needs_input / 过期计划 / runner 抛确认 / 显式失效）、`tests/test_foundation_first_open.py`
（FO01 / FO02 / FO03 / FO07 / FO15 / FO19 经真实 HTTP 入口，结果记录进 harness）、
`tests/test_import_architecture.py` + `tests/test_runtime_build.py`（worker 的装载清单 ↔ 登记 ↔
spec datas）。
