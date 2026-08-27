# ADR 0018：项目 Python 环境的自动发现与无感切换

状态：已实施（2026-08-27）
相关：[Compatibility Bridge 总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)、
[0013 Runtime Figure Assets](0013-runtime-figure-assets.md)、
[0014 Safe/Native Execution Profiles](0014-safe-native-execution-profiles.md)（仍是
Proposed；本 ADR **不是**它的实现，两者的关系见文末）、
[兼容层分层](../compatibility/legacy-projects.md)。

## 背景

PR 1（#127）合并后，真实用户拿一批旧项目复测，结论收敛得比预期干净：**绝大
多数脚本现在都能正常发现与打开，剩下的失败几乎只有一类——内置渲染环境缺第
三方依赖。**

一个真实例子（用户的 `2d 处理` 项目，8 个脚本）：

```text
ModuleNotFoundError: No module named 'ovito'
```

这类项目自己就带着一个能跑通的环境，`.venv/` 就在脚本旁边。而 Tavotto 当时
给用户的唯一出路是「去设置里手填一条解释器路径」——对科研用户来说门槛过高，
很多人根本不知道自己那个环境的解释器在哪。

## 决策

内置 runtime 仍然是默认（稳定、可复现、装完即用）。**它因缺依赖失败时，
Tavotto 自动去项目附近找一个健康的本地虚拟环境，整体换过去重跑。**

```text
内置 runtime worker
    ↓ missing_dependency
自动发现 <项目>/.venv
    ↓
体检（Python 版本 / matplotlib / 缺的那个模块 / 能不能起 worker）
    ↓
作废旧会话 → 用 .venv 自己的 Python 从头起 worker → 重跑脚本 → 捕获 Figure
    ↓
后续 render / override / replay / export 全部固定在这个环境
```

## 一、绝不混装 site-packages

这是本 ADR 最硬的一条。**严禁**：

```python
sys.path.append("<项目>/.venv/lib/python3.x/site-packages")   # 不行
PYTHONPATH="<项目>/.venv/lib/python3.x/site-packages"          # 不行
```

用户 venv 里的 numpy / scipy / h5py / netCDF4 / rdkit / opencv / torch 是编译
扩展，绑死 CPython ABI、Python minor 版本、操作系统、CPU 架构、NumPy ABI 与
系统动态库：

```text
内置 Python 3.13 + venv 里的 cp311 扩展            → import 即崩
内置 numpy 2.x  + venv 里对 numpy 1.x 编译的 scipy → 不可预测状态
```

后者尤其危险——它不一定当场崩，可能只是**算出错的数而不报错**。一个绘图工具
把用户论文里的数字改了还不吭声，是比崩溃坏得多的失败模式。

所以环境切换的单位只能是**完整解释器**：

```text
<项目>/.venv/bin/python → engine/worker.py → 用户脚本
```

看护：`tests/test_project_env.py::test_never_mixes_site_packages`（断言 worker
的注入环境里没有任何 `PYTHONPATH`，且真正被执行的 argv[0] 就是 venv 自己的
解释器）。负向反证：给 worker 注一条指向 venv site-packages 的 `PYTHONPATH`，
该用例当场变红。

## 二、为什么项目 venv 不需要安装 Tavotto

`engine/worker.py` 一直是 `sys.path.insert(0, HERE)` 的**平铺 import**
（`import figcapture, manifest, overrides`），依赖只有 matplotlib 与 numpy。
所以 Tavotto 把 worker 代码交给**用户的解释器**执行即可，不必也不允许往用户
环境里 `pip install tavotto`。

体检里专门有一步确认这件事（`tavotto_worker_ok`），用例
`test_project_venv_starts_the_worker_without_installing_tavotto` 钉着它：
这条断言红了就意味着我们开始要求修改用户环境。

## 三、第一版只认本地 venv

只找三种目录名：`.venv` / `venv` / `env`，覆盖 stdlib venv、virtualenv、
uv venv。它们的共同点是**目录里就有一个真正的解释器**，判据是 `pyvenv.cfg`
存在且解释器文件在——不解析 `activate`（那是给交互 shell 用的），不猜目录名
（项目里叫 `env/` 的经常是「环境配置」而不是环境）。

**本轮不做** Poetry / Conda / pyenv / pixi / hatch / tox：它们都要先问自己的
CLI 才知道环境在哪（`conda env list` 可能几秒），而且环境往往在项目之外——
那是另一个安全模型。等真实用户数据表明有需要再加。

### 搜索范围与优先级

范围严格锁在**项目根内**：从脚本所在目录逐级向上到 `figures_dir` 为止，
不上溯到项目之外（那是别人的项目），不顺软链接跳出去（按 realpath 收敛）。

同时存在多个时的裁决顺序，写进用例、不许随实现漂移：

1. 离脚本最近的那一层；
2. 同层内 `.venv` → `venv` → `env`；
3. 仍并列时按规范化路径字典序（只为「每次给同一个答案」，不是语义）。

「随机选一个」是不可诊断的：用户报「有时候能打开有时候不能」，而两次跑的是
两个环境。

## 四、优先级：用户显式选择 > 自动猜测

`pool.resolve_worker_python(figures_dir)` 是项目级解释器决策的唯一出处：

```text
1. TAVOTTO_WORKER_PYTHON     环境变量
2. 设置里指定的               全局显式选择
3. 这个项目记住的             自动接手的结果，或用户为该项目挑的
4. 内置 runtime / 自身 / 系统  原有链条（select_worker_python）
```

第 3 条排在内置**之前**而不是之后：一旦某个项目已经确认「内置缺包跑不了它、
项目 `.venv` 可以」，每次都先从内置重来一遍只是把同一个 `missing_dependency`
重演一次，用户看到的是「每次打开都先失败一下」。

**两条显式来源各自判，不能 `env or configured` 短路**：环境变量指着一条已经
不存在的路径时（改过环境、跟着别的 shell 配置进来的老值），短路会让一条完全
有效的设置里的解释器被跳过，自动决策于是压过了用户的显式选择。这个形状不是
假想的——它是在全量测试套件里被另一个用例漏出来的环境变量当场撞出来的。

## 五、决策是项目作用域，不是全局

自动接手的结果写在**项目设置**里（`config.project_settings(<项目路径>)` 的
`environment` 键），**绝不写全局 `worker.python`**：

```text
A 项目找到 .venv → 写全局 → B 项目也开始用 A 的 .venv
```

持久化的是**项目相对路径**（`.venv/bin/python`）：项目整个挪走、换台机器
同步过去、从 `~/paper` 变成 `/Volumes/T7/paper` 时，绝对路径当场失效而相对
路径照样成立。只有用户显式挑的项目外解释器（conda 环境）才存绝对路径——
它本来就不跟着项目走。

计算这条相对路径时**刻意不 `resolve()` 解释器本身**：`.venv/bin/python` 在
POSIX 上就是一条指向基础解释器的软链接，跟着它走的话每一个项目 venv 都会被
判成「在项目外」。要的是布局意义上的位置，不是软链接的终点。

## 六、worker 身份包含解释器

`pool.get()` 复用会话的判据里加了一条「渲染解释器已变」，与原有的「入口已变」
同形（不另起一套 key，风险最小）。没有它，自动切换之后还可能复用那条内置
runtime 起的会话，用户看到的是「明明切了环境，还是报缺包」。

这条守卫真正生效的场景是**用户切回内置环境**（`projectenv.forget()` 不作废
任何 worker）；自动接手那条路顺手 `invalidate()` 过，所以只测「切过去」那一半
是空门禁——最初的用例正是这样抽掉守卫仍然全绿的。

## 七、整个 worker 生命周期一个解释器

热态 build、render、override、undo/replay、preview、export，以及写回自检的
**干净重放**（`pool.one_shot()`），全都经过同一个 `EngineWorker.__init__` /
`WorkerdWorker.__init__`，因此自动拿到同一份项目级决策。

这不是巧合而是必须：写回事务要保证「热态所见 == 写进文件的 == 重开后重放
出来的」。热态跑项目 `.venv`（matplotlib 3.10）、重放跑回内置（matplotlib
3.11）的话，几何比对必然发散，而原因深埋在两个进程之间。

## 八、只有 missing_dependency 触发，且一次最多切一次

判据的唯一出处是 `pool.should_try_project_env(exc)`。`ValueError` /
`TypeError` / `FileNotFoundError` 换个解释器一样错——为它们切环境既要多跑一遍
脚本，又把真正的代码错误伪装成了环境问题（用户于是去折腾环境，而 bug 在第
12 行）。

重试上限：一次用户 build 最多自动切换一次（`projectenv.mark_attempted`）。
没有它，「内置缺包 → 切 venv → venv 也缺 → 切回内置」会一直打转，用户看到的
是界面卡在「正在运行」而后台在反复起 Python。用户手动重试走
`projectenv.reset_cache()`，可以重新开一轮。

## 九、安全模型一个字节都没放松

换的只是解释器，**执行安全模型不变**：worker 沙盒 cwd、写入守卫、
`Path.unlink` 守卫、相对路径只读回退、Figure 捕获、写回校验全部照旧。
不切到用户原始 cwd，不传原始 shell 环境全集，不重建 activation。

**本轮不做自动 pip install**，一个包都不装：内置 runtime 必须保持可复现
（「重装就能修」这条退路不能失效），自动升级依赖会引入供应链风险，
dependency resolver 可能把 numpy/matplotlib 升到基线之外，而且网络不可控。
用户 venv 同样一个字节都不改。看护：`test_nothing_is_ever_installed`。

## 十、支持口径

| 维度 | 区间 | 区间外的处置 |
| --- | --- | --- |
| Python | `>=3.10,<3.14`（`docs/support-matrix.json`） | `unsupported`，**不自动使用** |
| matplotlib | `>=3.8,<3.12`（pyproject 的 `worker` extra） | `unverified_but_compatible`，照用但如实标注 |

**刻意不对称**：Python 版本是硬边界（语法、ABI、我们从没跑过的 CI 组合）；
matplotlib 在钉版之外但能 import 的多半是好的，拒绝它等于把一个能出图的环境
判死，而 Tavotto 的视觉基线只在钉版上重生成过，所以也不能声称验证过。

`engine/projectenv.py` 里的常量是那两份文件的**运行时镜像**（它们不随 wheel
发布），`tests/test_support_matrix.py::test_project_env_mirrors_the_matrix`
逐条对拍——改了矩阵不改镜像当场变红。

## 十一、诊断

诊断包的 `project.environment_resolution` 回答「为什么用了这个 Python」：
来源、是不是自动接手、因为缺哪个包、那个环境的 Python 与 matplotlib 版本、
支持等级、**项目相对**的解释器路径。

版本这些事实在**切换当时**就随决策一起存下来了：生成诊断包时不该再去起一个
解释器问一遍（体检最长 60 秒，用户点的是「导出诊断包」不是「重新体检」）。

隐私沿用既有口径：项目内的解释器只出项目相对路径（用户主目录名不进包），
不读 `pip freeze` 全量列表，不上传环境变量与文件内容。

## 十二、与 `tavotto run` / ADR 0014 的关系

ADR 0014（safe/native 两档执行 profile）**仍然是 Proposed，本轮没有实施**。
本 ADR 解决的是「环境里缺东西」，0014 解决的是「执行语义不同」（原 cwd、
argv、env、`python -m`、自定义启动器）。兼容层因此是分层的：

```text
Layer 1  Tavotto 内置 runtime
   ↓ 缺依赖
Layer 2  项目本地 .venv 自动接手      ← 本 ADR
   ↓ 仍不可用
Layer 3  用户选择的 Python / Conda
   ↓ 执行语义仍然不兼容
Layer 4  tavotto run / native 执行     ← 尚未实施，见「决策门」
```

**决策门**：只有当真实数据表明剩余失败仍大量集中在 cwd / argv / shell env /
`python -m` / 自定义启动语义上时，才恢复 `tavotto run`。绝大多数项目能正常
打开的话，它继续延期。

## 后果

* 用户不再需要知道「渲染环境」是什么——项目自带 `.venv` 时整个过程无感。
* 多了一份项目级状态（可诊断、可关闭、可覆盖），但没有第二套设置界面：
  它长在既有的「渲染环境」那一块上。
* 发现范围保守（只找项目内的三个目录名），会漏掉 Poetry / Conda 用户——
  他们仍然走「选择其他 Python」那条明确的路，不是无声失败。
* 项目 `.venv` 的 matplotlib 版本可能与 Tavotto 的视觉基线不同。这是**如实
  标注**而不是拒绝：支持等级里的 `unverified_but_compatible` 就是给它的。
* **体检与 worker 的环境条件不完全一致**（已知，本轮不改）：体检跑在 `-I`
  （隔离模式，忽略 `PYTHONHOME` / `PYTHONPATH`）下，而 worker 只在
  `source == bundled` 时才由 `runtime.child_env()` 摘掉那些变量。用户从
  终端启动 Tavotto、shell 里为 conda 设了 `PYTHONHOME` 时，体检会过而
  worker 可能起不来。**这不是本轮引入的**——`configured` / `system` 两档
  一直如此；项目 venv 只是加入了同一条船。真要修得同时动两条控制面与
  `ExecutionSpec` 的 env 模型（`spec.env` 只存增量，表达不了「删掉某个
  变量」），那是独立的一轮。发作时 worker 会正常报错并走恢复引导，不会
  静默出错图。
