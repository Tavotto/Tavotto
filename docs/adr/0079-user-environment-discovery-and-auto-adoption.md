# ADR 0079：缺包时先找用户自己的 Python，装齐的里挑最好的直接改用

日期：2026-09-23 · 状态：**Accepted**
修订：[0018 项目 Python 环境解析](0018-project-python-environment-resolution.md) §三（不做 Conda / pyenv 发现）、
[0044 系统解释器作为修复候选](0044-system-interpreter-as-repair-candidate.md) §二（不无感切换）
本 ADR 自身的修订：文末「修订（2026-09-25）」——映射不到包名的 import 也触发发现（QA ENV-08-B1）；
「修订（2026-10-21）」——Windows 发现补强、确认能跑者优先、落选原因；
「修订（2026-10-21 b）」——脚本 `import ROOT` 时叠加用户装的 CERN ROOT 环境
相关：[0057 首开的环境与工作目录](0057-first-open-environment-and-workdir.md)、
[0061 联合依赖准备](0061-joint-dependency-preparation.md) §六（跑前的门）、
[0053 准备接口的公开投影](0053-foundation-contracts-and-preparation.md) §二（投影不带机器路径）

## 问题

2026-09-23 beta 实测：一位用户的脚本 import 了 `openpyxl` / `pypdf`，她自己终端里的 Conda 环境早就装齐了，
但打开 Tavotto 只看到「要先准备 2 个依赖」→「装进 Tavotto 隔离环境」。换成她自己的 Python 的入口藏在
设置 → 诊断 → 技术详情（折叠）→「使用其他 Python 环境…」，还要手填绝对路径；后端提示里指的「设置 →
渲染环境」分区根本不存在。她用不下去。

根因有三层：

1. **发现太窄。** 桌面版由 GUI 启动，PATH 只有 `/usr/bin:/bin:…`；老链条（`pool._prioritized_candidates`）
   只看 Homebrew / `/usr/bin` / python.org / 三个 Conda **base** 目录。Conda 具名环境、`~/opt/anaconda3`、
   `~/miniforge3`、pyenv、项目里写明的解释器一个都看不见。ADR 0018 §三当时推迟 Conda / pyenv 的理由是
   「要问它们的 CLI 才知道环境在哪，可能要几秒」。
2. **跑前的门只会提议安装。** U04 的依赖弹窗（`DependencyPrepareDialog`）目标只有 Tavotto 隔离环境与项目
   venv；ADR 0044 体检出来的系统解释器只挂在**运行后**的修复卡片上，弹窗里还显式过滤掉了。
3. **就算找到了也要用户点。** ADR 0044 §二：系统环境在用户交给我们的边界之外，采用要点一次。

## 裁决

### 一、发现：只读磁盘上的记录，外加问一次登录 shell（`engine/userenvs.py`）

不问任何 CLI。四类来源，顺序即优先级：

1. **项目线索**（离用户意图最近）：`.vscode/settings.json` 的 `python.defaultInterpreterPath` /
   `python.pythonPath`、`.python-version`（pyenv 版本名）、`environment.yml` 的 `name:`（按名字找 Conda 环境）、
   脚本第一行写死解释器的 shebang（`/usr/bin/env python3` 不算，那就是 PATH 上的那个）。只在「脚本所在
   目录 → 项目根」这条链上找，**脚本路径本身跳出项目时一层都不读**。
2. **终端里默认的 python**：用户的**交互式登录 shell**（`$SHELL -l -i -c`）里 `command -v python3` / `python`。
   Conda 的 init 写在 `.zshrc`，只有交互式 shell 读它。只在 POSIX 上问；8 s 超时；输出按标记行取，
   rc 文件打印的杂讯不影响；进程内缓存。
3. **Conda 全部环境**：`~/.conda/environments.txt`（Conda 自己记的每个环境前缀）+ 常见安装根与它们的 `envs/*`。
4. **pyenv 全部版本**：`$PYENV_ROOT/versions/*`（pyenv-win 在 `pyenv-win/versions/*`）。

老链条的系统解释器（ADR 0044 那一层）并在表尾，来源标 `system`。

**去重键 = (所在目录, 真实文件)**：同一目录下 `python3` / `python` 两个别名是一个环境；目录不 realpath——
`.venv/bin/python` 指向基础解释器，但两者 site-packages 不同，必须分得开（ADR 0044 §四那条老坑）。

### 二、体检与「装没装齐」：看 import，不看包元数据

每个候选一次子进程（`projectenv.probe_environment(python, modules=…)`，最多 12 个、4 个并行、按
（环境, 模块组）缓存）：环境本身健康（Python 版本在矩阵内、matplotlib 与 worker 启动链起得来），再逐个
import 脚本开跑要的第三方包（联合计划的 `missing` + `satisfied`——不只是此刻解释器缺的差集：内置 runtime 有 numpy
缺 openpyxl 时，只装了 openpyxl 的环境按差集量也「装齐」，2026-09-24 修订）与映射不到包名的 import。**装齐 = 全部 import 得到**——worker 跑脚本认的就是
import（Conda 装的、`--user` 的、`PYTHONPATH` 上的都算），这是同一个判据。「环境健康」与「装没装齐」
分开报：不健康的不算装齐，也不说它缺什么。正缺包的那个解释器不体检。

### 三、挑最好的：写死的排序（`userenvs.rank()`）

在装齐且健康的里面：

1. 来源档位：项目线索 > 终端默认 > **名字与项目目录相同的 Conda 环境** > 其余 Conda / pyenv > 系统；
2. 同档里 Tavotto 验证过的组合（`verified`）优先于「兼容但未验证」；
3. 再同档，Python 版本新的优先；仍并列按发现顺序（稳定排序）。

### 四、跑前的门里直接改用（修订 ADR 0044 §二）

联合计划 `ready`（缺包、能装）时先做上面三步。挑得出最好的，**且此刻的解释器是机器替用户挑的**，就把它
记成本项目的自动决策（`projectenv.remember(automatic=True, trigger="user_environment")`）——紧接着起的 worker
按 `resolve_worker_python` 第 3 条解析到它，门按它算出来的计划是 `nothing_needed`、放行，不弹任何框。
正被另一个作业改动的环境（`envlease`）不采用：它此刻的体检读的是装了一半的 site-packages。

**决定的时刻（2026-09-24 修订，Codex #522 两条 P1）**：「换不换解释器」只在 `deprepair.decide_environment()`
一处，且在**解析解释器之前**调——准备计划 `preparation.plan_for` 在拍快照（解释器 / LaunchContext / 环境事实）
之前调它；`pool.acquire()` 要起新会话时经 `pool.ENVIRONMENT_DECIDERS` 在查租约（`is_mutating`）之前、锁外调它。
`gate()` 只读，不换。初版把改用藏在 `gate()` 里，而门排在快照与租约检查之后：计划记着旧解释器、执行前的
过期检查看到新决策，第一次准备必以 `preparation_plan_stale` 收场；`is_mutating` 查的是旧解释器、构造函数
解析到的是新的，worker 可能起在正被装包的环境上。两条是同一个顺序错误。

「机器替用户挑的」的反面，一个都不碰：环境变量 / 设置里的全局显式选择、用户为本项目挑过的解释器
（`automatic=False`）、用户明确选回默认链条（`mode=default`）、项目自己的 venv（那本来就是用户的环境，
缺包该装进它）、干净机器（私有 Python 那条路）。

改用是**说出口、可撤销**的：门回调 `on_user_environment_adopted`，app 发 SSE `engine.environment_adopted`，
界面的通知轨说「已改用 Conda 环境 lab（Python 3.12.4），需要的包它都装好了」并给「改回」。「改回」=
`PATCH /api/engine/environment {scope: project, python: null}` = `remember_default`：本项目明确选回默认链条，
**之后不再自动挑**；缺的包于是又走依赖弹窗，弹窗里装齐的环境仍然列着、仍然预选第一个，只是要点一下。

一个都没装齐 → 照旧弹 U04 的依赖弹窗：先说「这台电脑上没有装齐这些包的 Python 环境」，安装目标预选；
没装齐的环境收在折叠里，只说还缺什么（不可选）。

### 五、公开投影不带路径（ADR 0053 §二）

`preparation_offer()` 的 `user_environments` 每条只带不透明 `id`（(目录, 真实文件) 的 sha1 前 16 位）、来源、
Conda / pyenv 名、版本、还缺什么；SSE 同样不带路径。弹窗里点「改用这个环境」交回 `id` + 脚本名，后端用
**自己的发现结果**换回路径，找不到报 `user_environment_gone`；换回来之后按此刻的联合计划**重新**量一次装没装齐
（`deprepair.recheck_user_environment`，与弹窗列出时同一个判据，不读也不写体检缓存；计划算不出来报
`user_environment_unverifiable`，不拿空的需求集合下结论）——弹窗开着期间环境可能变了，
调用方也可能交回一个界面上不可选的未装齐候选（Codex #522 P2）：缺就 `user_environment_incomplete` + 缺什么，
不健康与手填路径同一组 code，都不记；装齐才 `remember(automatic=False)`。路径只来自本机的枚举，不接受调用方
给——ADR 0044 的安全口径不变。

### 六、开关与测试隔离

`TAVOTTO_USER_ENV_DISCOVERY=0` 整个关掉（不发现、不体检、不自动改用），给用户当逃生口。**测试进程默认关**
（`tests/conftest.py`）：开着的话每条走到真门的用例都会问这台机器的登录 shell、体检它的系统 Python，
某台 CI 机器上的 Python 碰巧装了用例要的包，门就自动改用——结果随机器变。`tests/test_user_environments.py`
自己打开。`script` 可以来自请求体，发现一进来先过 `projectenv.contained_path()`，下游只用它回的那一条。

## 不做的事

* 往用户的 Conda / pyenv / 系统环境里装包。它们仍不是安装目标（ADR 0019 §一 / ADR 0044）。
* 问 `conda env list` / `pyenv versions` 之类的 CLI。磁盘记录已经够了，而且不会卡几秒。
* 运行后（`missing_dependency`）那条修复卡片的候选表暂不换成这份发现：跑前的门已经覆盖了静态可见的
  import，运行时才暴露的缺包仍走 ADR 0044 的那一层。（2026-09-28 起那一层也会无提示自动采用第一个合格的
  系统解释器，判据与本 ADR §四的「机器替用户挑的」同一份，见 [ADR 0107](0107-silent-system-interpreter-adoption-and-native-workdir.md)；
  候选表仍是 ADR 0044 的那一份。）
* Windows 上的登录 shell（PowerShell profile）：不问。Conda / pyenv-win / 项目线索照常发现。

## 修订（2026-09-25）：映射不到包名、此刻又确实 import 不到的 import，也去找用户环境

**动机。** QA 2026-09-24 ENV-08-B1（PR #583 的证据 `env/logs/verify/env08_conda_layout_feasibility.py`）：隔离 HOME 里
摆出 Conda 布局，base 只有 matplotlib、具名环境 `lab` 装着脚本要的 `qa_probe_pkg`，项目里 `environment.yml` 写着
`name: lab`。`qa_probe_pkg` 不在映射表里（`depresolve` 解析不出分发名），联合计划把它列进 `unknown`、永远不装
（FO-034），于是计划是 `nothing_needed` 而不是 `ready`——§四「计划 `ready` 时先做三步」这道前提把发现整个跳过了：
`user_environments` 为空，脚本照样在 base / 内置环境里跑、以 `missing_dependency` 收场。这正是本 ADR 要解决的那位
beta 用户的形状（她的包在自己的 Conda 环境里），只是换成了一个映射表外的包（实验室自家库、小众领域库最常见）。
用户 2026-09-25 拍板：补上这一支（PR #598 列出的选项 B）。

**裁决。** 联合计划 `nothing_needed`、且有**无条件**的 `unknown` import 时，先在**此刻的解释器**里量一次它们
import 不 import 得到（`deprepair.unknown_imports_missing` → `userenvs.imports_missing`：与 §二「装齐」同一条体检
`projectenv.probe_environment(modules=…)`、同一个缓存，不新造子进程协议）。有**确实** import 不到的，就按 §一–§三
同样的三步发现、评估、排序（结果形状仍是 `user_environments`），§四的「机器替用户挑的才自动改用」原样适用：
`decide_environment()` 把「计划 `ready`」放宽成「计划 `ready`，或 `unknown_missing` 非空」。offer 多一个字段
`unknown_missing`（只有 import 名，不带路径，§五不变）。

**边界。**

* 只在「此刻的解释器**确实** import 不到」时触发：import 得到的不找；体检起不来 / 结果里没有这一项 = 判不出，
  不算缺（拿没量到的东西去换环境是替用户做了一个没根据的决定）。此刻的解释器是内置 runtime 时，按 worker 起它的
  同一套量（`runtime.child_env()` / `child_args()`：摘掉 shell 带来的 `PYTHONPATH` 等、带 `-B`），否则体检看得见的包
  worker 看不见（Codex #609 P2）。
* 条件式 import（`try/except ImportError` 包着的、函数里延后的）不触发：它们本来就不在 `unknown` 里
  （`depplan` 只收 `CONTEXT_UNCONDITIONAL`）。
* **仍然绝不安装**：unknown 的 import 永远不猜包名、不装（FO-034 不变），这一支只找、只改用。
* 门（`gate()`）不因此弹依赖弹窗：那个弹窗是「授权安装」，这里没有可装的东西。自动改用走原来的通知轨
  （`engine.environment_adopted`）；用户决定过的（§四那五种）一个都不碰，此时行为与修订前相同——脚本照跑，
  缺包走运行后的修复卡片。
* `blocked` 的计划不走这一支（声明本身有问题，先停在那里）；干净机器不走（没有解释器可量）；开关
  `TAVOTTO_USER_ENV_DISCOVERY=0` 关着时连量都不量。

## 看护

`tests/test_user_environments.py`：发现的顺序 / 来源 / 标签 / 去重（假 HOME 目录树）、线索不出项目
（含脚本路径跳出项目）、`env` shebang 留给登录 shell、登录 shell 输出只认标记行（真起子进程）、
「装齐」按 import 判、挑选排序三把尺子、决定自动改用 + 门只读 + 通知不带路径、五种用户决定一个都不碰、
决定先于租约检查（worker 不起在被占用的环境上）、正被改动的环境不采用、采用前按此刻计划复核、
公开载荷不带路径、正缺包的解释器不体检、真解释器逐个模块回报。
2026-09-25 修订：`tests/test_user_environments.py` 的「映射不到包名」一组——隔离 HOME 的 Conda 布局里装着那个包的
具名环境被发现并自动改用、base 不被用；此刻的解释器 import 得到时不发现也不体检候选；条件式 import 不触发；
全程不装（前后 `pip freeze` 一致）。
前端：`DependencyPrepareDialog.test.tsx`「用户自己的环境」、`notificationRail.test.tsx`「已改用你的环境」、
`useServerEvents.test.ts` 的 `engine.environment_adopted`。

## 修订（2026-10-21）：Windows 上找到「脚本原本能跑」的环境，并说清其余为什么落选

**动机。** 一份真实 Windows 诊断包：脚本 `import ROOT`（CERN ROOT），用户很可能有装好 ROOT 的 Conda 环境，Tavotto 却用了
内置 Python。ADR 0079 / 0114 的机制（`userenvs.discover` → `deprepair._detect_environment_unlocked` → `evaluate` / `rank` /
`best`）已经按 import 名单体检，问题是 **Windows 上发现太窄**：Conda 只认 `~` / `%LOCALAPPDATA%` / `ProgramData` 下四个固定
名字，装在 `D:\anaconda3`、环境放在 `E:\envs` 的看不见；python.org 安装器登记的解释器也不在任何来源里。

**用户裁决（2026-10-21）：脚本默认在它原本能跑的环境里自动运行。** 机制不变（内置能跑就不换；内置跑不了才在发现到的
环境里挑确认能跑的），变的是发现面与可解释性。

**新增来源（仍是只读磁盘 / 注册表记录，仍不问任何 CLI、不执行任何东西）。**

1. `.condarc`（`%CONDARC%`、`~/.condarc`，Windows 再加 `C:\ProgramData\conda\.condarc`）：`envs_dirs` 里的目录，其子目录各是一个环境；
   `pkgs_dirs` 里名为 `pkgs` 的目录，其上一级是 Conda 根。只解析简单列表（块式 `- x` / 行内 `[a, b]`，去引号与行尾注释，
   每键最多 32 项），纯标准库，经 `scanbudget.read_regular_text` 有界读；读不了记一条「看不全」，不抛。
2. PATH 里的 `conda.exe` / `mamba.exe` / `micromamba.exe`（Windows）：`Scripts` / `condabin` 的上一级、`Library\bin` 的上两级
   就是安装根。**只 `is_file`，绝不执行**——「不问任何 CLI」原样保持。另加 `MAMBA_ROOT_PREFIX`，Windows 常见安装根列表补上
   `micromamba`。网络路径（`_local_hint`）不碰。这些根与 `.condarc` 的根走同一个 `_conda_prefixes()` 循环，因此同样过
   重定向检查与扫描预算 / 取消。
3. PEP 514 注册表（`userenvs._windows_registry_pythons`，来源 `windows_registry`）：HKCU 与 HKLM 的
   `Software\Python\<公司>\<标签>\InstallPath`，64 位与 32 位（`WOW6432Node`）视图都读；取 `ExecutablePath`，没有就用默认值
   （安装目录）+ `python.exe`；公司 / 标签各有上限，逐项记预算。非 Windows 回空；读取器可注入（`winreg` 只在 Windows 上
   导入），测试不依赖真 Windows。

**来源档位。** 注册表来源与 Conda / pyenv 同属第 3 档（「用户自己装的解释器」）：安装器替用户登记的，说明用户装过它；但没有
项目线索或终端默认指向它，所以不高于项目 / 登录 shell，仍高于老链条的系统解释器（第 4 档）。Anaconda 的安装器也会登记，
与 Conda 发现同一个文件时按 (目录, 真实文件) 去重，保留先发现的 Conda 标签。体检上限（`PROBE_LIMIT`）与 `static_order`
照旧：新来源排在档位内的发现顺序里，不会饿死更靠前的候选。

**排序（`rank()`）。** 第一把尺子新加「体检确认程度」：确认装齐且健康 > 没量到（延后 / 项目说了算、运行前不起）> 确认跑不了；
之后才是原来的来源档位 → verified → Python 新旧。动机：来源再靠前（项目线索、终端默认），只要体检确认它缺脚本要的
import，就不该排在确认能跑的环境前面。`best()` 本来只在 `satisfies` 的里面挑，所以**挑选结果不变**；变的是候选表
（`user_environment_offer`）里非装齐项的展示顺序。「内置能跑就不换」不受影响——它在 `_detect_environment_unlocked` 的
`runs_now` 分支（此时只有项目声明的候选可排在默认链条前面），`rank()` 不参与。

**落选原因。** 每个被评估但没选用的候选产出一条结构化原因，挂在 `pool.EnvironmentDecision.rejected`
（`decide_environment_pinned` 的返回）上：`{"id", "source", "label", "reason", "missing", "import_failed"}`，不带路径
（§五），`id` 与 `public()` 同一个不透明 id。`reason` 是稳定码闭集（`userenvs.REJECT_CODES`）：

| 码 | 含义 |
|---|---|
| `missing_imports` | 健康，但脚本要的某些 import 得不到（`missing` 列出缺哪些，如 `ROOT`） |
| `import_failed` | 包在，import 时抛错（ABI / 依赖 / DLL） |
| `python_out_of_range` | Python 版本不在 Tavotto 矩阵内 |
| `no_matplotlib` | 不是绘图环境 |
| `worker_chain_failed` | Tavotto worker 的启动导入链断了 |
| `activation_failed` | Conda 激活取环境失败（瞬时，不缓存） |
| `probe_timeout` | 体检超时（`projectenv.probe_environment` 在 `TimeoutExpired` 时多带 `timed_out: true`） |
| `unusable` | 解释器起不来 / 体检进程崩溃 |
| `not_checked` | 没量到（延后到运行时；项目说了算的候选运行前不起） |
| `outranked` | 自己也能跑，只是排序靠后，被选用的那个更优 |

默认链条能跑、没有候选可比时没有体检，`rejected` 为空。这一版只产出字段，不做前端卡片与诊断包接线（后续 PR 读它）。

**前端接线（2026-10-22）。** 准备报告的 `environment.not_used[]`（`envadvice.adoption_fact(rejected=)`，至多 6 条，字段 `source / label / reason / missing / import_failed / root_version?`，不带路径与不透明 `id`）把 `decision.rejected` 送到前端；准备卡的「详情」里按稳定码各说一句（`web/src/lib/envRejectionText.ts`，`outranked` / `not_checked` 不说），并给一个复用既有「选择渲染环境」的入口。默认可见区不变。诊断包接线仍未做。

**不变。** 绝不安装、不执行 CLI、公开载荷不带路径、用户显式决定过的一个不碰、`TAVOTTO_USER_ENV_DISCOVERY=0` 总开关。

**看护。** `tests/test_user_environment_windows_discovery.py`：`.condarc` 解析 / 有界 / `envs_dirs` 发现，PATH 反推（含
`Library\bin`、网络路径不碰、不执行），注册表（HKCU / HKLM / 32 位视图 / `ExecutablePath` 优先 / 非 Windows 空 / 预算耗尽），
来源档位，`rank()` 三档确认程度，落选原因十个码与「不带路径」；`tests/test_environment_autodetect.py`「落选原因」一组：
决定结果上的 `rejected`、都跑不了时仍逐个说明、内置能跑时为空。

## 修订（2026-10-21 b）：脚本 `import ROOT` 时，把用户装的 CERN ROOT 叠到候选解释器上

**动机。** 同一份 Windows 诊断包：`import ROOT as rt` / `from ROOT import RooFit`，Tavotto 用了内置 Python 而失败。上一节补足了"找得到
装了 ROOT 的 Conda 环境"，但 **Windows 上 ROOT 根本不在 Conda 里**：conda-forge 的 `root` 没有 win-64 包，Windows 用户装的是官方二进制包
（`root_v6.38.xx.win64.python311.vc17` / `…python314.vc18…`），**每个包只配一个 Python 小版本**；官方 `thisroot.bat` 只做
`ROOTSYS`、`PATH` 前置 `%ROOTSYS%\bin`、`CMAKE_PREFIX_PATH`、`PYTHONPATH` 前置 `%ROOTSYS%\bin`、`ROOT_INCLUDE_PATH`。用户在终端里跑过它，
从开始菜单启动的 Tavotto 看不到。macOS / Linux 上 ROOT 多来自 Homebrew / 自编译 + `thisroot.sh`，同样适用。

**裁决：不去猜哪个 Python 里有 ROOT，而是替用户做 thisroot 做的事，再用现有体检验证。**

1. **发现（`engine/rootenv.py`，叶子模块，纯 stat，不执行任何东西，包括 thisroot）。** 来源按优先级：环境变量 `ROOTSYS`；Windows 卸载项
   （HKCU/HKLM × 64/32 位，`DisplayName` 以 `ROOT` 开头且后面是空白/分隔符/版本号/结尾——不吃 `Rootkit` 一类）；常见位置
   （Windows：`C:\root`、`C:\root_v*`、`%ProgramFiles%\ROOT*`、`%LOCALAPPDATA%\root*`；POSIX：`/opt/root*`、`/usr/local/root*`、
   Homebrew `…/Cellar/root/*`、`~/root*`）。**判据（唯一，各来源同用）= 安装根下有 `bin/thisroot.bat`（POSIX `bin/thisroot.sh`）
   且 PyROOT 标志**——`bin/`、`lib/`、`lib/root/` 之一里有 `ROOT/__init__.py` 或 `ROOT.py`（官方 Windows 包把 PyROOT 放在 `bin`，
   thisroot.bat 也把它放进 `PYTHONPATH`；POSIX 在 `lib`，Homebrew 在 `lib/root`）。名字像 ROOT 而目录没有这两样的一律不收，
   注册表条目只提供候选位置，不单独作数。至多 8 个，按真实路径去重；版本取 `include/RVersion.h`，退一步看目录名。
2. **叠加层只存"哪个安装根"。** `{"kind": "root", "root", "version"}`；环境变量每次起进程现算（`rootenv.apply_overlay`：`ROOTSYS`；
   `PATH` 前置 `<root>/bin`；POSIX 的 `LD_LIBRARY_PATH`（macOS 为 `DYLD_LIBRARY_PATH`）前置 `<root>/lib`；`PYTHONPATH` 前置 PyROOT 所在目录；
   `CMAKE_PREFIX_PATH`；`ROOT_INCLUDE_PATH`），并先复核安装根仍像 ROOT——被卸载了就不叠加，不把过期路径塞进环境。设置里不存环境变量。
3. **何时用。** 只在 `_detect_environment_unlocked` 里同时满足：脚本 import 名单含 `ROOT`；默认链条此刻跑不了（`runs_now` 为假）；
   所有裸起候选都没装齐。叠加组合与裸起候选进**同一张表**、同一个 `best()`——裸起能导入的永远优先。
4. **组合有界。** 底 = 此刻的解释器（**内置 runtime 也算**）+ 已量过、健康但没装齐的候选；Conda 环境也可以当底（见下「真机修订」第 5 点，原先
   「不叠 Conda」的理由不成立）；ROOT 至多取最新的 2 个；总组合至多 8 对（`rootenv.MAX_COMBOS`）；沿用 `evaluate` 的并行度与
   超时。安装包文件名里的 Python 小版本标记（`libcppyy3_<minor>`）**只做排序提示**，认不出不排除——这条命名规则没有拿真实 Windows 包逐文件核对，
   最终以真探测为准。
5. **探测的顺序与"运行之前不量"。** 体检进程的环境 = 内置 runtime 摘掉 `PYTHONPATH` 之后 / Conda 激活之后**再叠**（与起 worker 同序，
   `pool.launch_env`）；叠加层在内置 runtime 上**替换**而不是前置 `PYTHONPATH`。运行之前的 `spec` 体检以 `-I` 启动、忽略环境变量，叠加组合在那里
   量不了，所以点「运行」之前只读缓存（`cache_only`）、不起进程、不采用；采用发生在用户点「运行」之后。叠加组合的缓存键带叠加层。
6. **选中后真正运行。** 叠加层随 `projectenv.remember(env_overlay=)` 进项目记录（`environment.env_overlay`），`pool.EnvironmentDecision.env_overlay` 与解释器是
   同一次决定；`EngineWorker` 起进程时叠上（只进 Popen 的 env，**不进 `ExecutionSpec`**——spec 进回执，不带安装路径）；带叠加层的解释器只走
   Python 池（`WorkerdWorker` 拒收，与 Conda 激活同形）；会话复用前核对叠加层，变了就重建（`叠加环境已变`）。记着的是内置 runtime 时，
   `remembered_source` 仍报内置（worker 按内置的那套起）。再次启动：记着的解释器按**带叠加层**量，仍能导入就不重新发现。
7. **落选原因。** 新稳定码 `root_python_mismatch`：叠加组合的解释器健康、叠上这个 ROOT 后 `ROOT` 仍 import 不了（二进制包与 Python 小版本对不上是
   最常见原因；体检拿不到异常文本，所以按"叠上了仍不行"判）。`rejections` 多带 `root_version`（只有版本，不带路径，ADR 0053 §二）；叠加候选的
   `id` 是 (解释器, 叠加层) 的摘要，与同一解释器的裸起候选不同；公开投影多 `overlay: {kind, version}`。
8. **依赖计划不走 pip。** ROOT 不是 pip 能装的（PR C 的不可 pip 安装表），本节只保证：有叠加候选被采用后，`unknown_imports_missing` 按带叠加层量，
   不再因为裸起量不到 ROOT 而重新发现或把它当成待装包。

**不做的事。** 不执行 thisroot / 不问 `root-config`；不往 ROOT 或用户的解释器里装任何东西；确认模式（ADR 0114）下
候选表不列叠加组合（采用端点 `recheck_user_environment` 按 id 找回的是裸起候选，叠加组合的确认流程未做，留待需要时另开）；
workerd 路径不接叠加层。

**看护。** `tests/test_root_env_overlay.py`：各来源发现（假目录树 + 注入的注册表读取器）、判据（缺 thisroot / 缺 PyROOT 标志 / 名字像但无结构）、
有界与去重、叠加层构造（POSIX / Windows / 内置替换 / 幂等 / 安装根消失）、只在需要时叠加、组合评估选择（假体检按 Python × ROOT 版本）、
组合上限与最新优先、运行之前不起进程、持久化与再次启动、落选原因与身份、`launch_env` / `_overlay_for` / 体检环境。

### 真机修订（2026-10-21，Win11 + ROOT 6.38.06 官方 zip `root_v6.38.06.win64.python314.vc18` + conda-forge Python 3.14.8）

上面几节按"照抄 thisroot"设计；Win11 真机跑下来**官方做法本身就不够**，以下是事实与由此改的地方（沿用上一节的发现 / 叠加 / 组合骨架）：

* **包布局（事实）。** `root\bin\thisroot.bat`（只设 `ROOTSYS` / `PATH` / `PYTHONPATH=%ROOTSYS%\bin;…` 等）、`root\bin\ROOT\__init__.py`、
  `root\bin\cppyy\libcppyy.pyd`、`root\bin\ROOT\libROOTPythonizations.pyd`、`root\bin\libCPyCppyy.dll` 等 129 个 dll、
  `root\bin\ROOT\_python_version.py`（`_root_python_version` = 构建所用 Python）、`root\include\ROOT\RVersion.hxx`（`ROOT_RELEASE` 由宏拼出；
  `root\include\RVersion.h` 里**没有**版本号）。
* **事实一：只放 `bin` 进 `PYTHONPATH` 不行。** `bin\cppyy\_cpython_cppyy.py` 在 Windows 上 `import libcppyy as _backend`（顶层名）→
  `ModuleNotFoundError: No module named 'libcppyy'`，官方 thisroot.bat 方式同样失败。**不能**靠把 `<root>\bin\cppyy` 加进
  `PYTHONPATH` / `sys.path` 修（第一版这样改，Win11 真机体检崩：该目录里有 `types.py` / `ll.py` / `reflex.py` / `interactive.py` / `_version.py`，启动时遮住标准库
  `types`，`json -> re` 导入链当场崩；手工测试通过只是因为在启动后才改 `sys.path`，标准库已缓存）。**改**：`PYTHONPATH` 只前置 `<root>\bin`（与 thisroot 一致；真机
  `bin\*.py` 只有 cmdLineUtils / rootcp / rootdrawtree / rooteventselector / rootmkdir / rootmv / rootprint / rootrm / rootslimtree，没有与标准库同名的）；
  `<root>\bin\cppyy` 经私有变量 `TAVOTTO_ROOT_CPPYY_DIR`（`rootenv.CPPYY_DIR_ENV`）交给启动钩子，钩子在 `os.add_dll_directory` 的同一处往 `sys.meta_path` **末尾**装一个
  只认顶层名 `libcppyy` 的 finder（`spec_from_file_location("libcppyy", …\libcppyy.pyd, loader=ExtensionFileLoader)`），其余名字一律返回 None。
* **事实二：`DLL load failed while importing libcppyy`。** Python >= 3.8 在 Windows 上不按 `PATH` 找扩展模块的依赖 DLL。**改**：叠加环境里多一个
  Tavotto 私有变量 `TAVOTTO_ROOT_DLL_DIR`（= `<root>\bin`，`rootenv.DLL_DIR_ENV`），worker 启动钩子（`worker._startup_dll_directory`，在装字节码守卫之后、
  引擎装载与用户脚本之前；只用 3.8 起就有的 `os.add_dll_directory`，py3.10 兼容）和体检脚本（`projectenv._PROBE_SRC`）读到就登记成 DLL 搜索目录。
  体检与 worker 同一件事，否则体检会把"DLL 没登记"误判成 `root_python_mismatch`。
* **事实三：没有 Visual Studio 的 C++ 工具，cling 崩溃**（`unable to find a Visual Studio installation` / `'new' file not found`，打印 STACKTRACE）。
  **改**：Windows 上组合评估前用 `rootenv.msvc_available()`（`vswhere.exe -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64
  -property installationPath`，有界超时 8 秒、结论缓存 60 秒；找不到 vswhere 也算没有）判；没有 → 该组合**不起进程**直接落选，新稳定码
  `root_needs_msvc`（进 `REJECT_CODES`，`rejection_reason` 与 `root_python_mismatch` 同通道，带 `root_version`）。不要让 worker 起来再崩。
* **事实四：版本与配对。** `_version_of` 也读 `include\ROOT\RVersion.hxx`：有字面 `ROOT_RELEASE "x.y.z"` 用它，否则按 `ROOT_VERSION_MAJOR/MINOR/PATCH`
  三个宏合成（补零到两位），都读不出回空串（再看目录名），不报错。`ROOT\_python_version.py` 里的 `_root_python_version` **静态读文本、正则取第一对
  主.次、不执行**，得到 `build_python`（如 `3.14`）。底的小版本也已知且与它不同 → 组合 `overlay_reject = root_python_mismatch`，**不起进程**；
  底的小版本未知（此刻的解释器 / 内置没量过）或 `build_python` 读不出 → 不据此排除，仍交给体检。静态落选的组合排在最后、至多 4 个，不占 8 对的名额。
  原先的 `libcppyy3_<minor>` 文件名标记只剩排序提示。
* **事实五：发现与"组合进不进候选流程"。** (a) 没设 `ROOTSYS` 时原先找不到解压在任意目录的 ROOT。**改**：来源多两档——`PATH` 里含 `thisroot.(bat|sh)` 的 bin 目录
  （安装根 = 上一级，至多看 64 项），以及家目录、`Desktop`、`Downloads` 下一层的 `root*` 与 `root*\root`（官方 zip 外层带版本、里层才是安装根；每个 glob 至多 16 项）。
  (b) 组合**不是** `userenvs.discover()` 的来源（它只给裸起候选，设计如此）；组合在 `deprepair._detect_environment_unlocked` 里、点「运行」之后
  才生成（`_root_overlay_entries` → `userenvs.root_installs` + `overlay_candidates`）。真机上组合没出来的原因是**底**：`_root_overlay_entries` 把
  `launcher.launcher_for_interpreter(python) is not None`（Conda 环境）一律滤掉，而 conda-forge 在 Windows 上没有 `root` 包，用户的 Python 3.14 恰是 conda-forge 的。
  **改**：Conda 环境可以当底——叠加层在激活增量**之后**叠（`pool.launch_env`、`projectenv._probe_environment` 同序），激活盖不掉它；conda-forge 自带 ROOT
  的环境裸起就导入得了，根本走不到叠加。检查阶段（点「运行」之前）组合仍只读缓存、不起进程，因此是"未检查"，不是缺席。

* **叠加顺序（真机补充）。** 已激活的 conda 环境（Python 3.14）+ `bin` 进 `sys.path`、`libcppyy` 经 finder + `os.add_dll_directory(bin)` 后 `import ROOT` 成功
  （TCanvas.SaveAs 出 PDF，matplotlib 出 PNG，两种导入顺序都正常）；去掉 `add_dll_directory` 仍 `DLL load failed`（必需）；conda 环境**不激活**时 matplotlib savefig 自己就崩
  （0xc06d007f，与 ROOT 无关）——所以叠加必须在激活捕获之后（`pool.launch_env`、`projectenv._probe_environment` 已是此序，有用例看护）。
  Python 3.12 叠同一 ROOT 报 `ImportError: DLL load failed while importing libcppyy`（不是 ModuleNotFoundError）：体检归 `import_failed` -> `root_python_mismatch`；
  底版本已知时读 `_python_version.py` 提前静态判。装了 VS 2026 Build Tools 后 vswhere 输出 `…\Microsoft Visual Studio\18\BuildTools`，`root_needs_msvc` 不再触发。

**仍是外部前提（Tavotto 不替用户装）**：Windows 需要装有 Visual Studio / Build Tools 的 C++ 工作负载；Python 小版本必须等于 ROOT 包的构建版本。
**看护。** 同 `tests/test_root_env_overlay.py`（Windows 布局 / `cppyy` 目录 / DLL 目录变量 / RVersion.hxx 宏 / `_python_version.py` / PATH 与家目录发现 / vswhere 注入 / 静态落选 / Conda 当底）。
