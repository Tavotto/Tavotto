# ADR 0079：缺包时先找用户自己的 Python，装齐的里挑最好的直接改用

日期：2026-09-23 · 状态：**Accepted**
修订：[0018 项目 Python 环境解析](0018-project-python-environment-resolution.md) §三（不做 Conda / pyenv 发现）、
[0044 系统解释器作为修复候选](0044-system-interpreter-as-repair-candidate.md) §二（不无感切换）
本 ADR 自身的修订：文末「修订（2026-09-25）」——映射不到包名的 import 也触发发现（QA ENV-08-B1）
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
  import，运行时才暴露的缺包仍走 ADR 0044 的那一层。
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
  不算缺（拿没量到的东西去换环境是替用户做了一个没根据的决定）。
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
