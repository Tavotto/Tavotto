# ADR 0020：Native Matplotlib Bridge（用用户自己的 Python 跑，只接管 Figure 生命周期）

状态：**Accepted（technical spike）**——机制与边界已定稿并有可运行实现 +
用例。**产品化已完成**：`tavotto run`（Beta）的产品契约见
[ADR 0021](0021-tavotto-run-product-contract.md)。本 ADR 描述的机制在产品里
逐条成立，只有两处被 0021 修订（都记在 §11 与 §5.4 里）：控制通道多了一跳
CLI 托管的 raw relay，屏障多了 `restore before continue, rebase at next
barrier` 的基准纪律。spike 入口（`python -m tavotto.engine.bridge_spike`）
仍然不是对外承诺（§11）。
日期：2026-08-28（Compatibility Bridge Session 8）
相关：[总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)、
[0014 Safe/Native 两档 Profile](0014-safe-native-execution-profiles.md)（本
ADR 是它 §3「捕获通道」与 §7「待定稿事项」的裁决）、
[0013 Runtime Figure Assets](0013-runtime-figure-assets.md)、
[0003 worker 协议 v1](0003-worker-protocol-v1.md)、
[0018 项目 Python 环境的自动发现](0018-project-python-environment-resolution.md)、
[0008 本机会话认证](0008-unified-local-session-auth.md)、
[Pylustrator 研究](../compatibility/pylustrator-study.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| Spike verdict | **GO** |
| 注入模型 | **BRIDGE_RUNNER_SELECTED**（§2，由实测差异支撑） |
| 传输 | 127.0.0.1 loopback + 一次性 token（§6）；**协议信封零改动** |
| 线程 | 控制循环跑在用户主线程；runner 侧无后台线程（§7） |
| Figure 跨进程 | **绝不 pickle / 绝不经 multiprocessing 传递**（§8） |
| 用户环境 | **不要求、也不允许安装 Tavotto**（§3） |
| v1 支持面 | `python 文件.py` 与 `python -m 模块`（§10 列出不支持的） |

---

## 1. 背景与形状

safe worker（现状）是 Tavotto 挑解释器、切沙盒 cwd、换 argv、吞掉 savefig、
逐条修补相对路径。它对"脚本 + 数据在一个图库目录里"的项目工作得很好。
复杂老项目的真实运行方式是

```sh
conda activate paper && python -m figures.fig3 --dataset run7
```

——解释器、cwd、argv、env、module invocation 每一项都与 safe worker 不同，
在沙盒里逐条修补是打不完的地鼠（Pylustrator 研究 §3 的结论）。

native bridge 的形状：

```text
tavotto 父进程
     │  spawn（用户的解释器，用户的 cwd/argv/env 原样）
     ▼
用户自己的 Python 进程
     │
     ├── bridge_runner.py  ── 收回 sys.path[0]
     │                     ── 装私有命名空间（bridgeboot）
     │                     ── 挂 meta_path 后置 import 钩子（不 import 任何东西）
     │
     └── 执行用户原目标（script / module，与真实 python 逐字段对拍）
              │
              ▼
          Figure（**留在这个进程里，永不离开**）
              │
              ▼
       LiveFigureSession（= safe worker 用的同一份编辑语义）
              │  worker 协议 v1（= safe worker 用的同一套信封）
              │  127.0.0.1 loopback + token
              ▼
        Tavotto UI / CLI —— 只收 SVG / PNG / PDF / manifest / 元数据
```

**关键不是"Tavotto 模拟用户环境"，而是"用户自己的 Python 就是环境"。**

---

## 2. 注入模型：BRIDGE_RUNNER_SELECTED

两种把钩子送进用户进程的办法都真跑过（`tests/bridge/test_bridge_injection_models.py`，
9 条，两种实现都在文件里）：

```text
A  Bridge Runner   <用户python> /abs/bridge_runner.py … -- <目标>
B  sitecustomize   PYTHONPATH=<注入目录> <用户python> <目标>
```

| 维度 | A Bridge Runner | B sitecustomize | 证据 |
|---|---|---|---|
| **坑位冲突** | 不占任何公共坑位 | **`sitecustomize.py` 早就有人占着**——Homebrew 的 Python 自带一份，而正是它把 matplotlib 所在的 site-packages 加进 `sys.path`。B 的直白实现在本机默认 Python 上**当场让 matplotlib 消失** | `test_naive_sitecustomize_breaks_homebrew_python` |
| 用户自己的 sitecustomize | 照常执行 | 被顶掉，**排前面也救不回来**（第一个找到的赢）。B 想安全就必须重新实现一遍 CPython 的发现逻辑去接力调用 | `test_sitecustomize_silently_replaces_the_users_own` |
| sys.path 污染 | 启动第一件事收回 engine 目录 | 注入目录**必须**留着（否则它自己不会被 import）——结构使然，不是实现没写好 | `test_sitecustomize_leaves_its_directory_on_sys_path_forever` |
| `python -S` / `-E` | 照常工作（钩子在它自己被执行时装） | **静默失效**，一句来自 Tavotto 的提示都没有 | `test_sitecustomize_silently_does_nothing_under_E` / `_under_S` / `test_bridge_runner_is_immune_to_E` |
| 孙进程 | 只注入 token，且 runner 一起来就摘掉 → 孙进程干净 | `PYTHONPATH` 被继承，**钩子装进了孙进程**（科研脚本调 subprocess 很常见） | `test_sitecustomize_infects_every_grandchild_process` |
| 目标表达 | `ExecutionSpec` 独家表达 + 逐字段对拍 | 只装钩子，跑什么靠外面那条命令——argv/引号/转义的还原责任跑到父进程侧，且没有一处能断言"我跑的就是他敲的那条" | `test_sitecustomize_cannot_express_the_target_itself` |
| backend 时序 | 两者相同（都用 meta_path 后置钩子） | 同 A | `test_user_code_is_the_first_to_import_pyplot` |
| script/module 语义 | 需要自己做对拍（已做，逐字段一致） | `-m` 天然是真的（CPython 自己跑的） | `test_bridge_invocation.py` |
| 打包 | 两个 .py 随包发（§9） | 需要一个注入目录 + env 编排 | — |
| Windows | 与 POSIX 同一条路径（无 shell、无 env 前缀） | 同 A，但多一份 `PYTHONPATH` 拼接的平台差异 | §12 |
| 可调试 | `ps` 里看得见完整命令；直接手敲就能复现 | 只看得见用户的命令，钩子从哪来要去查 env | — |
| 生命周期/清理 | 进程退出即结束，无残留 | 同上，但 env 会留给孙进程 | 同上 |

**B 唯一的真实优势**是 `-m` 语义天生正确。A 把这条补齐的成本是一次
`runpy.run_module(alter_sys=True)` + 一行 `sys.path[0] = cwd`，并且有逐字段
对拍看着（§4）。B 的劣势里有两条是**静默的**（坑位冲突、`-S`/`-E` 失效）
——静默失败在这个产品里代价特别高：用户看到的只会是"Tavotto 说这个脚本
不出图"。

**裁决：BRIDGE_RUNNER_SELECTED。** 不留"两种都可以，以后再决定"。

---

## 3. 用户环境**不装** Tavotto

用户项目的 `.venv` 里可能只有 matplotlib / numpy / pandas / scipy / seaborn
和项目自己的包。Tavotto **不要求也不自动**在那里 `pip install tavotto`。
做法是把 runner 的**绝对路径**交给用户的解释器：

```sh
<项目>/.venv/bin/python  /abs/path/tavotto/engine/bridge_runner.py  …
```

这与 ADR 0018 已经在用的那条路径同构（那里也是让项目 `.venv` 的 Python
去跑 Tavotto 的 `worker.py`）。

看护分两层，因为它们各自会在不同的地方失效：

- **结构性**（永远跑）：`bridge_runner.py` / `bridgeboot.py` 里不许出现
  `import tavotto`，也不许出现包内相对 import（`from . import …`）——它们
  是被**按文件路径**执行的，包上下文根本不存在；
- **行为性**：报告里 `tavotto_importable` 必须是 False。**CI 上这条会跳过**
  ——`pip install -e ".[dev]"` 与 `pip install matplotlib` 装在同一个解释器
  里，那台机器上唯一能跑 matplotlib 的 Python 同时装着 Tavotto。跳过时
  明说原因，不假装通过；真正的行为证明由 `-m slow` 的**真 venv E2E**
  （`python -m venv` + `pip install matplotlib`，先断言 `find_spec("tavotto")
  is None`）承担。

### 3.1 命名空间：用户项目里的模块**永远赢**

safe worker 把 engine 目录永久插在 `sys.path[0]` 上并平铺
`import manifest / overrides / patchspec`。在它自己的进程里没问题；在用户的
进程里这一手是数据损坏级的缺陷——用户项目里完全可能有

```text
manifest.py   overrides.py   config.py   runtime.py   figsession.py …
```

他的 `import manifest` 必须拿到他自己那份。三条不变量（`bridgeboot`）：

1. **`sys.path` 逐项还原**——装载窗口关掉时与打开前一模一样；
2. **顶层 `sys.modules` 逐项还原**——引擎模块搬进私有包
   `tavotto_bridge_runtime.*`，原本存在的名字原样留着、原本没有的删掉；
3. **不装出第二份**——两阶段装载时把上一批按平铺名摆回窗口内，
   否则第二阶段的 `import figcapture` 会再装一份（不报错，会在别处以
   "捕获表对不上"的形状出现）。

同一处还有一个反向的坑：装载窗口内必须把**用户已经 import 过的同名模块挪开**
——`importlib.import_module("figsession")` 先查 `sys.modules`，根本不会走
`sys.path`，拿到的会是用户那份。表现是
`AttributeError: module 'tavotto_bridge_runtime.figsession' has no attribute
'LiveFigureSession'`，指向完全错误的方向。（这条是用例抓出来的，不是设计时
想到的。）

配套的一处源码改动：`overrides.py` 里两处 late import
（`from manifest import _ordered_axes`，为绕开与 manifest 的模块层循环）
改走 `overrides._sibling("manifest")`——按**模块自己的包前缀**解析。
它们在**用户代码跑起来之后**才执行，裸名会命中用户项目里的 `manifest.py`。
safe worker 的平铺形态下前缀为空，行为一个字节没变。

看护：`tests/bridge/test_bridge_namespace.py`（6 条）。

---

## 4. invocation 对拍

判据不是"看起来对"，而是拿**同一个解释器、同一份夹具**跑两遍逐字段比
（`tests/bridge/test_bridge_invocation.py`）：

| 字段 | `python probe.py A B` | bridge script | `python -m paper.figure A B` | bridge module |
|---|---|---|---|---|
| `sys.executable` | 用户的 | 同 | 用户的 | 同 |
| `os.getcwd()` | 用户的 | 同（继承） | 用户的 | 同 |
| `sys.argv` | `["probe.py","A","B"]` | 同 | `[".../paper/figure.py","A","B"]` | 同 |
| `sys.path[0]` | 脚本目录（绝对） | 同 | **cwd**（绝对） | 同 |
| `__name__` | `"__main__"` | 同 | `"__main__"` | 同 |
| `__package__` | **`None`** | 同 | `"paper"` | 同 |
| `__spec__` | `None` | 同 | `paper.figure` | 同 |
| `__file__` | **绝对** | 同 | 绝对 | 同 |
| `sys.modules["__main__"].__dict__ is globals()` | True | 同 | True | 同 |
| 选定环境变量 | 用户的 | 同（原样继承） | 用户的 | 同 |
| `sys.flags.isolated` / `no_site` / `dont_write_bytecode` | 0/0/False | 同 | 同 | 同 |

两处实现要点：

- **script 形态不用 `runpy.run_path`**：它把 `__file__` 设成传进去的原串、
  把 `__package__` 设成 `""`，而真实 `python file.py` 给的是**绝对**
  `__file__` 与 `__package__ is None`。这不是学术差异——`if __package__ is
  None:` 是相对 import 兜底的常见写法，`os.path.dirname(__file__)` 到处都是。
  所以按 CPython 自己的做法组装 `__main__`。
- **module 形态用 `runpy.run_module(..., run_name="__main__", alter_sys=True)`**，
  它在 `__name__` / `__package__` / `__spec__` / `__file__` / `argv[0]` 五项上
  开箱就对；唯一要补的是 `sys.path[0] = cwd`（真实 `-m` 放的是它，runpy 不动
  sys.path）。**对拍用例是版本无关的**——它拿同一个解释器跑两遍再比，所以
  CI 的 3.10 与 3.13 两条腿各自证明各自那一版。（顺带实测过：直跑
  `python` 时 `sys.path[0]` 绝对化、`__package__ is None` 这两条规则在
  3.9.6 / 3.11.14 / 3.13.11 上完全一致。）
- **argv[0] 用 `ExecutionSpec.raw_target`**（用户敲的那一串），不是规范化
  后的 `target`（项目相对 POSIX 路径，那是**身份**）。拿身份当 argv[0] 会让
  脚本里的 `os.path.dirname(sys.argv[0])` 指到别处。
- **解释器不加任何标志**：没有 `-B`、没有 `-S`、没有 `-I`、没有 `-E`。
  那是用户环境的地盘。
- **`compile(..., dont_inherit=True)`**：`compile()` 默认会把**调用处生效的**
  `__future__` 语句一并传给被编译的代码，而 runner 自己有
  `from __future__ import annotations`。不关掉的话用户脚本会在不知情的情况下
  拿到 PEP 563 语义——`x: NoSuchType = 5` 直跑报 `NameError`，在 bridge 里
  **静默通过**。这是最坏的那种不一致：朝着"更宽松"的方向、无声地发生。
  脚本自己写的 future import 不受影响（它在源码里）。
- **module 的源文件必须在开跑之前解析**（`resolve_module_origin()`，
  `pkg` → `pkg.__main__` 与 runpy 同款）：`run_module(alter_sys=True)` 返回时
  会把 runner 装回 `sys.modules["__main__"]`，跑完再读 `__file__` 拿到的是
  `bridge_runner.py`，于是 asset id 变成 `runtime:bridge_runner.py#Fig1`
  ——用户的 override 保存重开之后全成孤儿。只 savefig 不 show 的 `-m` 目标
  走的正是这条。

### 环境继承

env **原样继承**启动 `tavotto run` 的那个 shell。Tavotto **不重建**
`conda activate` / `poetry shell` / `uv shell` / `PATH` / `CONDA_PREFIX` /
`LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH`——重建等于第二份环境解析实现，
而它必然与真正的激活有出入。`resolve_interpreter()` 默认取**当前 shell 里
的 `python`**，找不到就报错，**绝不回退到 Tavotto 自己的解释器**（静默换
解释器是 native 档最不该有的行为：用户看到的是"跑起来了但缺包/结果不对"）。

bridge 注入的环境变量**只有一个**：`TAVOTTO_BRIDGE_TOKEN`，而且 runner
一起来就把它从 `os.environ` 摘掉——用户脚本与它起的每个子进程都看不到。

---

## 5. 捕获：钩什么、什么时候钩

### 5.1 不提前 import pyplot

用户脚本有权决定后端：

```python
import matplotlib
matplotlib.use("Agg")          # ← 这一句只在 pyplot 还没 import 时是纯的
import matplotlib.pyplot as plt
```

bridge 只要先 import 了 pyplot，`use()` 就变成 `switch_backend()`——而
**它的文档承诺会销毁我们刚捕获的 Figure**：

> `switch_backend` docstring（matplotlib 3.10.8）：
> *"If the new backend is different than the current backend then all open
> Figures will be closed via ``plt.close('all')``."*

实测同版本**并不会**：`use("pdf")` 之后建两张图再 `use("Agg")`，
`plt.get_fignums()` 仍是 `[1, 2]`；`switch_backend` 的**函数体里根本没有
`close(` 这个调用**（那句只在 docstring 里，`matplotlib.use()` 的函数体里
也没有）。也就是说**这一版的文档与实现不一致**。

依赖"实现碰巧不销毁"而不是"文档说会销毁"，是那种下一次发版就塌的赌注。
所以判据不是"当前版本安不安全"，而是**根本不去踩这条路**：bridge 不提前
import pyplot，`use()` 就永远是纯的那一支。

所以钩子挂在 `sys.meta_path` 上：一个**后置 import 钩子**，自己不 import
任何东西，只在别人 import 到 `matplotlib.figure` / `matplotlib.pyplot`
**完成的那一刻**回调。

- 为什么不 patch `builtins.__import__`：`importlib.import_module()` 不经过
  它（matplotlib 内部大量使用），钩子会在最需要的时候安静地不响；
- 为什么不轮询 `sys.modules`：`plt.show()` 是在脚本执行**中间**调用的，
  等脚本跑完再看已经晚了；
- 钩子**可卸载**：脚本跑完就摘掉，不让 Tavotto 参与用户之后每一次 import
  的解析。

判据是"**谁决定 pyplot 什么时候进来**"，不是"某个后端名字碰巧对不对"：
夹具第一行就 `assert 'matplotlib.pyplot' not in sys.modules`。

### 5.2 最小钩子集（v1）

| 钩子 | 行为 |
|---|---|
| `Figure.savefig` | **记录 + 透传**（`passthrough_savefig=True`）。与 safe 相反：safe 吞掉写盘（沙盒纪律），native 照常写——用户的命令本来就会产出那些文件 |
| `pyplot.show` | 收 Gcf，然后按 `block` 决定进不进屏障（下节） |
| 脚本结束后的 Gcf 兜底 | 收一遍还活着的 Figure |
| OO Figure（不经 pyplot） | 由 savefig 钩子认领——它不在 Gcf 里，兜底看不到 |

**本轮不做**：`Axes.text` / plot 的 source hint、代码生成（总纲"长期增强"）。

捕获**策略**（stem 怎么编、怎么去重、上限多少、描述符怎么造）是 `figcapture`
那一份——safe worker 与浏览器 playground 用的是同一批函数。在 native 里另写
一份的表现是：同一个脚本在两条入口里产出不同的 stem，而前端按 stem 索引一切。

引擎自己出图（预览 SVG / PNG / 导出）时**暂停捕获**：不暂停的话
`export("/tmp/whatever.pdf")` 会被当成用户脚本的一次 savefig，凭空多一个 stem。

### 5.3 `plt.show()` 的语义

| 调用 | 行为 |
|---|---|
| `show()` / `show(block=True)` | 收 Gcf → **进屏障**：用户在 Tavotto 里编辑，点"继续"之后 show() 返回、脚本接着往下跑。与交互式后端同构（窗口关掉之前 show() 不返回） |
| `show(block=False)` | 收 Gcf → **立刻返回**。脚本明确说了不要阻塞。图仍在捕获表里，脚本结束的那次屏障还在 |
| 重复 `show()` | 每次都收一遍 Gcf；已捕获的按 Figure 身份去重 |

**不把 show 永久换成 no-op**：那会改变"先 show 再接着算"的脚本行为，
也会让 `block=False` 与 `block=True` 变得不可区分。

### 5.4 屏障（barrier）

> **ADR 0021 §8 的修订**：屏障释放之前必须把 Figure 恢复成**脚本原样**，
> 下一个屏障重新采基准并按稳定 gid 重放用户的编辑。本节下面描述的是 spike
> 的形态（编辑留在 live Figure 上），产品里不是那样——那会让用户脚本
> `show()` 之后看到 Tavotto 的 override，也就改变了直接跑 Python 的语义。


```text
plt.show()  →  收 Gcf  →  屏障（主线程服务控制循环）  →  continue  →  show() 返回 → 脚本继续
脚本结束    →  收 Gcf  →  屏障                        →  continue/shutdown  →  进程退出
```

屏障事件是**带外帧**（`{"bridge_event": "barrier", …}`），v1 的请求/响应信封
零改动；调用方按键名区分（v1 响应永远带 `protocol_version`）。native 需要它
是因为它的生命周期与 safe 相反：**脚本在指挥，不是我们**。

**每个屏障都必须被应答。** 一次运行里屏障可能出现多次；只应答第一个然后去等
`exit`，两边就各等各的（本机挂死过一次）。

**每个屏障也都要先把新捕获同步进会话。** 钩子写的是模块级捕获表（它们是类
属性级 monkeypatch，拿不到会话实例），而会话是**第一个屏障**那一刻才建的。
此后脚本继续跑、继续产图，那些图只会落进模块级表——不同步的话第二个屏障里
看不到第二张图（stems / build 响应 / 可编辑会话里都没有），而脚本明明画出来
了。同步必须是幂等的：`add_figure` 对已有 stem 不覆盖，`instrument_all()`
只给还没有 FigState 的图建状态——已经在编辑的那些带着用户的 override，
重建等于把编辑丢掉。

父进程走掉时屏障**放开**——native 里那个进程是用户的，控制通道断了只说明
"没人在编辑了"，脚本该接着跑完。

---

## 6. 传输：换传输，不换协议

safe worker 的协议跑在 stdin/stdout 上。native 里这条路是**封死的**：

```python
print("hello")                 # 用户的 stdout，是他程序的一部分
input()                        # 用户的 stdin
print('{"cmd":"render"}')      # 用户完全可以打印出一行合法 JSON
```

把 stdout 当协议管道 = 用户的每一行输出都是一帧可能被误解的协议数据，而且
他再也看不到自己的输出——恰恰违背"与你自己在终端里跑这条命令完全等同"。

**方案**：控制通道走 127.0.0.1 loopback + 一次性 token；子进程的
stdin/stdout/stderr **原样继承**到用户的终端。

要求逐条（`tests/bridge/test_bridge_transport.py`）：

- 只 bind `127.0.0.1`（**绝不** 0.0.0.0），端口 0 让内核分配；
- token = `secrets.token_urlsafe(32)`（256 位），**一次会话一枚**；
- token 走**环境变量不走 argv**——同机上 `ps` 对别的用户可见，而环境在
  macOS/Linux 上默认只有属主读得到；子进程一起来就摘掉；
- 有握手帧；token 比对用 `secrets.compare_digest`；
- **认证失败立即断开，但继续 accept**——认证失败就关掉监听等于把 DoS 送
  出去（本机任何进程抢先连一下，用户的 `tavotto run` 就永远起不来）；
- 握手成功后关闭监听：一次会话一条连接；
- token 不进任何可能被打日志的结构（握手帧里的 token 收到就丢）；
- 会话结束关 socket、收子进程。

**协议语义零改动**：请求信封由 `pool.build_envelope()` 独家产出（与
stdin/stdout 那条控制面是同一个函数），执行侧由 `wireproto.V1Handler`
分派（与 `worker.py` 是同一个类）。相同的 request/response 信封、相同的
`worker_generation` / `render_revision` / `canonical_patch_hash` 回显纪律、
相同的错误码表。native 只多一个命令：`continue`（放开屏障）。

> **没有** `NativeBridgeProtocolV2`。抽象是"哪条管子"，不是"哪套语义"。

---

## 7. 线程模型

**控制循环跑在用户的主线程上，runner 侧全程只有主线程。**

matplotlib 的 Figure 不是线程安全的。后台线程读 socket 再回主线程投递是可行
设计，但"根本没有那个线程"是更强的保证：不存在的线程不会在某次重构后开始
动 Figure。

`LiveFigureSession` 另有一条**线程身份断言**兜底：它记住创建它的线程，
每个会改变 Figure 或从 Figure 取几何的入口（`instrument_all` / `render` /
`do_render` / `do_render_png` / `do_preview_png` / `do_export`）先核对一次，
不符就抛 `WrongThread`。把约定变成断言，是因为约定会在某次"顺手把渲染挪到
回调里"之后**静默**失效，而失效的表现是随机的段错误或画错的图——"碰巧没事"
正是这类缺陷最常见的样子。这条对 safe worker 恒真（它本来就单线程串行读
stdin），等于免费。

代价：脚本正在跑的时候没人读 socket，父进程的请求会等到下一个屏障。这是
**诚实的**——那时候本来就还没有 Figure 可编辑。

父进程侧（`bridge.py`）用一个读线程做超时（与 `pool._readline` 同源：
Windows 的 `select` 对管道不成立，两条路径的超时语义必须一致）。那一侧
没有 Figure。

---

## 8. 为什么不 pickle Figure

Figure **始终留在创建它的进程里**。跨进程只走控制命令 + manifest +
SVG / PNG / PDF + 结构化元数据。

- Figure 不可靠地 picklable（闭包 callback、打开的文件句柄、后端画布、
  用户自定义 Artist 子类），失败面不可枚举；
- `pickle` 跨解释器 / 跨 matplotlib 版本是未定义行为，而 **native 的意义
  恰恰是"用用户自己的（任意版本的）环境"**；
- 用 `multiprocessing` 把 Figure 传给 Tavotto 进程同理（它就是 pickle）。

看护：`test_no_engine_module_ever_pickles_a_figure`（按源码判整个 engine
目录，`pickle` / `multiprocessing` / `copyreg` / `dill` / `cloudpickle` 一个
都不许出现）+ `test_the_control_channel_only_ever_carries_json`（会话跑完
之后每一份响应都能重新 JSON 编码；导出的 PDF 是**子进程自己写盘**产出的，
通道上传的只是一个路径）。

---

## 9. 打包

native 需要两个文件随包发（`packaging/tavotto.spec`）：

```text
bridge_runner.py   ← 用户自己的解释器按绝对路径执行它
bridgeboot.py      ← 私有命名空间装载器 + import 钩子
```

它们是**真 .py 文件**（不是 PyInstaller 归档里的条目）——用户的解释器读的是
磁盘，编进归档它看不到。同一条纪律下 safe worker 的传递闭包也在那张表里，
而那张表由 `tests/test_runtime_build.py` 从**源码的 import 闭包**反推校验，
本轮把它的根从一个（`worker`）扩到两个（`worker` + `bridge_runner`）——
只查半条链的门禁比没有门禁更坏，这条 docstring 自己说过，本轮是同款缺陷
换了个位置。

`bridge_runner.py` 与 `bridgeboot.py` 是**纯标准库**且必须在 3.10 上跑得起来
（用户环境的版本我们说了不算）。本机验到 **3.11**（3.13 是主验证版本，3.11
上单独跑过一次无图路径）；**3.10 由 CI 的 `backend-fast` 那一格执行**——
`tests/bridge/` 走的是默认 pytest，所以那条腿每个 PR 都会跑。

---

## 10. 明确不支持

| 不支持 | 原因 |
|---|---|
| **任意 shell 命令**（管道、env 前缀、`Makefile`、bash 包装脚本） | 解析与注入面无界；一旦支持就要对每种形态回答"捕获层注得进去吗、argv/env 语义还原了吗"，做不到就变成静默半支持（ADR 0014 §7） |
| **子进程里产生的 Figure** | 钩子只在被 runner 直接执行的那个进程里；孙进程是干净的（§2 的一条优点，同时也是这条限制） |
| **Jupyter / IPython** | 没有"脚本结束"这个时刻，`show` 语义也不同；总纲"长期增强"单独排期 |
| **`conda activate` / `poetry run` / `uv run` 的重建** | §4：不重建，用当前 shell 的环境 |
| **写回用户脚本源码** | v1 恒 `can_writeback_source=False`（ADR 0013 §7） |
| **写回原始产物（native）** | native 的 savefig 透传写到哪由用户脚本决定，那不是 Tavotto 管的图库；描述符里 `original_artifact` 恒 None |
| **R / Julia 等非 Python 绘图环境** | 总纲"长期增强" |
| **native 会话进池复用** | 本轮不做（spike 先证明单次跑得通）；ADR 0014 §7 的第 4 问留给 Session 9 |
| **MCP 的 native 工具** | 本轮不提供。模型给的路径/命令不是授权（ADR 0009 的 fail-closed 纪律）；要提供必须走 elicitation 且默认 false |

---

## 11. 安全定义

本模式是 **Native / Attached execution，不是 safe sandbox。**

> 用户代码拥有与他自己直接运行那条 Python 命令**完全相同**的权限。

文案与机制必须逐条一致（ADR 0014 §2）：native **绝不**声称沙盒或只读。

同时，**Bridge 本身不得额外扩大任何权限**：

- 不开网络能力（唯一的 socket 是 loopback 控制通道，且需要 token）；
- 不放宽文件系统（safe 的 `Path.unlink` / `write_text` 守卫在 native 里
  本来就不存在——那是 safe 的语义，不是 native 摘掉的）；
- 不改 env（只加一个 token，且立刻摘掉）；
- 不代用户起子进程。

> **Tavotto adds hooks, not privileges.**

spike 入口（`python -m tavotto.engine.bridge_spike`）**不是产品**：它没有接进
`tavotto` 的 CLI（看护 `test_the_spike_cli_is_not_wired_into_the_product_cli`）、
不进 README、不进官网、没有稳定契约。

**产品入口是 `tavotto run`（ADR 0021，Beta）**，它与 spike 共用同一份
`bridge_runner.py` / `figsession` / `wireproto`，但控制面完全不同：一次性
handoff descriptor、CLI 托管的认证 relay、独立的会话注册表、单 reader 传输。
CompatBench 的 `native_run` 路由**必须**走产品那条（看护
`tests/test_compat_product_routes.py::test_native_run_route_goes_through_the_product_control_plane`）
——拿 `BridgeSession` 代表产品成功，正是"基准替产品打掩护"的形状。

---

## 12. Windows / macOS

| | 做法 | 状态 |
|---|---|---|
| macOS | 见上；开发机（arm64）上跑通全部用例 | ✅ 已验证（CI 侧 `backend-platforms (macos-latest, 3.13)` + `macos-app-smoke` 同轮亦绿） |
| Windows | 同一条路径：loopback socket 跨平台一致（不用 fd 继承、不用 Unix socket、不用命名管道）；spawn 不经 shell（argv 列表）；控制通道两侧钉 UTF-8，而用户的 stdio 一个字节不碰；`creationflags=CREATE_NO_WINDOW` 复用 `runtime` 那份；跨盘符的 `relpath` 显式报错不裸抛 | ✅ **已验证**：入队轮 `backend-platforms (windows-latest, 3.13)` 首次执行 `tests/bridge` 全部 69 条，一次全绿；同轮 `windows-exe-smoke` / `package (windows)` 亦绿 |

用例本身是平台无关的（没有一条依赖 POSIX 语义），而且走默认 pytest——
所以 `backend-platforms`（merge_group / `full-ci`）会在 macOS 与 Windows 上
各执行一遍。缺的只是**看见那一遍的结果**："从没跑过的门禁不会保持正确"。

本轮已经被仓库既有的 Windows 门禁抓到过一次真缺陷：
`test_source_hygiene.py::test_windows_bound_subprocesses_pin_their_decoding`
发现新用例里 9 处 `subprocess.run(text=True)` 没钉 `encoding`——Windows 上
会用系统默认编码（cp936/cp1252）解码子进程输出，而这些用例里恰恰有靠
stderr 内容分诊的判据。这条说明"平台无关"不能只靠眼睛看。

---

## 13. 未完成（进 Session 9 的入场券）

1. ~~**Windows 执行**~~ —— **已完成**（合入那一轮 `backend-platforms`
   mac + Windows 首次执行 69 条用例、一次全绿）。剩下的只有 **WebView2 /
   WKWebView 壳内交互**的真机取证，那是 PR 1 起就挂着的遗留项，与 native
   bridge 不特别相关。
2. **native 会话进池 / 复用**（ADR 0014 §7 第 4 问）。**#177 落地后这一问
   多了一个必须回答的具体场景**：`pool.mutating_environment()` 在装依赖期间
   独占一个环境——`shutdown_workers_using(python)` 收掉**池里的** worker，
   再让 `pool.get()` 拒起新的（`environment_mutating`）。native 会话
   **不经过池**（自己 `subprocess.Popen` 用户的解释器），所以那把锁对它
   **机制上不可见**——不是漏了一个分支，是实现方式决定的。

   `tavotto run` 一旦成为产品：用户握着 live Figure 的同时，另一个请求可以往
   **同一个解释器**装包，而 pip 会替换 / 删除已有包的文件。三个候选答案
   （native 也进那把锁 / native 进池 / 装包时显式拒绝并说明有活跃 native
   会话）各自的代价不同，选哪个与本条第 2 问是同一个决定。

   **今天够不着**：native 唯一入口是没接进任何产品面的 spike CLI，两条路凑不
   到一起。这条是 rebase 到 `8118ba2` 之后读代码才浮出来的——两个子系统各自
   的用例都绿、`pool.py` 的改动区域也不相交（#177 在 1726+/1850+，本轮在
   80+/822+），**三方合并零冲突**。冲突检测回答的是「文本能不能合」，不是
   「语义能不能共存」。
3. **产品面**：`tavotto run` 的稳定 CLI 契约与错误码、桌面交接、UI 确认文案
   （必须写明解释器路径、cwd、"拥有你当前用户的全部权限"）、每项目记住选择。
4. **CompatBench 的 `native_run` 路由**从 `not_implemented` 升级（总纲 §六）。
5. **`_refresh_axes_follow` 的静默 except**：那处 late import 失败会被
   `except Exception: pass` 吞掉，行为判据测不到（本轮改用不吞异常的
   `FigState.resolve` 那条路径 + 一条结构性守卫兜住）。要不要收窄那个
   except 是独立的一笔，本轮不动。
