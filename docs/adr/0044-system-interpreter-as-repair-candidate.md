# ADR 0044：项目环境接手的第二层——这台机器上已有的解释器作为修复候选

日期：2026-09-06 · 状态：**Accepted**

## 背景

ADR 0018 让内置 runtime 缺包时自动改用项目自带的 `.venv`，并明确写下「第一版只认
本地 venv……等真实用户数据表明有需要再加」。数据来了：

一个科研目录（九个 ovito 分析脚本、二十个 stem）里**一个 venv 都没有**，能跑那些脚本
的是系统 `/usr/bin/python3` 加用户 site 里的 ovito。Tavotto 的表现是：

1. 接手一步报 `project_env_not_found`，转入依赖修复，建了受管环境；
2. 用户手边那套明明能跑的环境从头到尾没被看过一眼，界面上也没有一个字提到它。

两处结构性原因：

* `pool._prioritized_candidates()` 第五级「系统 Python / Conda」的注释说它存在的意义
  就是「脚本要 import 内置 runtime 里没有的包时，用户自己那套环境才是对的」——但老
  链条只问「谁有 matplotlib」，内置 runtime 永远有，第五级在桌面版里**事实上从没
  起过作用**。
* `projectenv.discover()` 的范围严格锁在项目根内。两头都把系统解释器漏掉了。

复核时还抓到体检本身的一个「量错对象」：`probe_environment` 用 `-I` 起子进程，它
顺手关掉了用户 site 目录与 `PYTHONPATH`，而真正的 worker（`execspec.worker_argv`）
不带 `-I`——`pip install --user` 装的科学栈在体检里「不存在」，起 worker 时明明
import 得到。实测这台机器上的 `/usr/bin/python3`：`-I` 下 matplotlib 与 ovito 都
「没有」，去掉之后是 matplotlib 3.9.4 + ovito 3.12。

## 决策

### 一、venv 那一层没接手成之后，再体检老链条枚举出来的系统解释器

`pool.system_python_candidates()` = `_prioritized_candidates()` 里来源为
`current_process` / `system` 的那些（丢掉前三级：环境变量与设置里指定的若存在就是
当前渲染环境，正是报缺包的那个；内置 runtime 永远不缺 matplotlib 却正因缺别的包才
走到这里）。**不新加任何发现逻辑**，不问 conda 的 CLI——ADR 0018 §三对 Conda /
pyenv / pixi 的推迟原封不动。

`projectenv.probe_system_candidates()` 逐个跑 `probe_environment(python, module)`：
版本在矩阵内、matplotlib 与 worker 模块能起、缺的那个包真的 import 得到，三件事
一次验完。纪律与 venv 那一层相同：**第一个健康的就停**（候选已按优先级排好，每个
体检最长 60 s）；上限 `SYSTEM_PROBE_LIMIT`；报缺包的那个解释器不再探；结果按
（解释器路径, 模块）缓存，`reset_cache()` 一并清（用户点重试 = 推翻旧结论）。

### 二、不无感切换：系统解释器只是候选，采用要用户点一次

项目内 venv 无感接手是因为它在用户交给我们的边界之内。系统环境在边界之外，一台
机器上往往好几个，静默选中哪个都可能不是用户想的那个。所以体检结果**只挂在接手
失败的结构上**（`outcome["system"]`，`ok` 仍是 False），由依赖修复面板列成第三种
目标 `system_interpreter`，排在最前——它一个字节都不装、不联网、不改任何环境，比
两种安装都便宜。受管环境那条路仍并列在同一个面板里。

它**不是安装目标**：不进 `deprepair.TARGETS`，`create_plan` 对它一律
`dependency_install_not_allowed`。采用走既有的项目环境 PATCH
（`PATCH /api/engine/environment`，`scope=project`），新增可选的 `module`：面板列出
候选与用户点下去之间那个环境可能变了，采用时连缺的那个包一起再验一次，验不过报
`project_env_module_missing` 而不是先记下来让下一次渲染去撞。记录带 `module` 与
`trigger=missing_dependency`、`automatic=False`，诊断包答得出「为什么这个项目用了
系统 Python」。持久化的是绝对路径（ADR 0018 §五：项目外的解释器本来就不跟项目走）。
`pool.remembered_source()` 把项目外、非受管的解释器标成 `system`，不再借用
`project_venv` 那个标签——`/usr/bin/python3` 不是「项目自带的虚拟环境」。

### 三、探到了但不合格的也要说出来

「缺的那个包其实 import 得到、只是环境本身不合格」（Python 版本不支持 / 没有
matplotlib / 起不来）单列在 offer 的 `system_rejected` 里，界面按 code 三句话：
「找到 /usr/bin/python3 装了 ovito，但它的 Python 3.9.6 不在支持范围内，没有采用」。
这一句比多一层自动切换更能减少困惑——用户明明有一套能跑的环境，界面只提议建受管
环境，那才是困惑的来源。**包本来就没有的不列**：那只是「别的 Python 也没有它」。
支持口径不变：`unsupported` 仍然不自动使用，也不成为目标。

### 四、体检的启动条件与 worker 对齐

`probe_environment` 去掉 `-I`，env 原样继承（与 `execspec.worker_argv` 起用户解释器
的条件一致）。`-I` 真正想挡的只是 Flask 进程的 cwd 进 `sys.path[0]`（里面一个
`matplotlib.py` 就能骗过体检），改由把子进程 cwd 换成一个**空的临时目录**来做。
venv 的用例形态不变（venv 自己关了用户 site）；系统解释器从假阴性变成真结论。

去重与缓存键**按路径字符串、绝不 realpath**：`.venv/bin/python` 是指向基础解释器
的软链接，按 realpath 会把项目 venv 与它的基础 Homebrew Python 判成同一个，后者整个
从候选表里消失（实测抓到）。这是 `projectenv.contained_file` 那条老坑的第三次。

## 安全模型一个字节没放松

换的只是解释器，执行安全模型不变（ADR 0018 §九）。系统候选**只来自 pool 自己的
枚举**，不接受调用方给路径；采用那一步的路径走的是已有的项目环境 PATCH，它本来就
接受用户显式指定的绝对路径并先体检。`module` 来自请求体，只认
`projectenv.valid_module_name` 那一份判据，不合形状当没给。不装任何东西；offer 在
渲染失败的响应路径上**不起任何解释器**——结论只读接手那一步的体检表。

## 不做的事

* Conda / pyenv / pixi 的环境发现（ADR 0018 §三原话仍成立）。
* 自动采用系统解释器（哪怕只探到一个健康的）。
* 把系统解释器当安装目标。往 `/usr/bin/python3` 里 pip install 是另一个安全模型。

## 顺便记录：这一层救不了那个目录

那九个脚本用 `os.path.exists("1/etch_5-5.lammpstrj")` 和 `glob.glob("./**/…")` 找
数据，而 worker 的 cwd 是沙盒；相对路径只读回退只覆盖 `open` 三个入口
（`src/tavotto/AGENTS.md` 的「相对路径只读回退」），`os.path.exists` / `glob` /
ovito 的 C++ 读取器都在盲区。就算接手到了正确的解释器，脚本仍然零张图、报
`stem 不存在`。两件事正交，本 ADR 只解决前一件；后一件（回退覆盖面、错误里带上
worker 日志尾部）另开。

## 看护

`tests/test_project_env.py` 「第二层」一节：不自动切换、报缺包的解释器不再探、第一个
健康的就停 + 不合格者留表、无可验证模块不起子进程、去重不按 realpath、体检看得见
worker 看得见的（`PYTHONPATH`）、体检看不见父进程 cwd、采用端点连 module 一起验、
敌意 module 名不进命令行；`tests/test_dependency_repair.py` 「六b」：健康的列成
采用目标且不是安装目标、不合格的单列说明、offer 不起解释器；
`web/src/components/DependencyRepairCard.test.tsx` 「这台机器上已有的解释器」一组。
