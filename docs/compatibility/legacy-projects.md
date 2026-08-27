# 打开已有的科研项目：兼容层是分层的

> 面向「我有一批以前写的 matplotlib 脚本，想用 Tavotto 编辑它们」的用户与
> 排障的人。设计决策在
> [ADR 0018](../adr/0018-project-python-environment-resolution.md) 与
> [Compatibility Bridge 总纲](COMPATIBILITY_BRIDGE_MASTER_PLAN.md)。

Tavotto 打开一个已有项目时，「用哪个 Python 跑你的脚本」是分层决定的。
每一层失败都有明确的下一步，没有哪一层是无声失败。

```text
Layer 1   Tavotto 内置渲染环境
          装完即用、不联网、版本可复现（视觉基线就在它上面生成）
             ↓ 脚本 import 了它没有的包（missing_dependency）
Layer 2   项目本地 .venv 自动接手                    ← 现在在这一层
          在项目里找到健康的虚拟环境，整体换过去重跑，对用户尽量无感
             ↓ 找不到 / 那个环境也不行
Layer 3   用户自己选一个 Python / Conda 环境
          设置 →「渲染环境」，可以只对这个项目生效
             ↓ 环境对了但**执行语义**仍然不兼容
Layer 4   tavotto run / native 执行
          原 cwd、原 argv、原 env、python -m、自定义启动器
          —— 尚未实施，见文末「决策门」
```

## Layer 1：内置渲染环境

桌面版随包附带一套私有 Python（当前 3.13）与钉版科学栈（matplotlib、numpy、
pandas、scipy、seaborn、pillow，版本见 `packaging/runtime-lock.json`）。
它是默认，也应该继续是默认：

* 装完即用，不需要用户先有 Python；
* 版本可复现——Tavotto 的视觉基线、写回像素门、CompatBench 全在它上面跑；
* 「重装就能修」这条退路始终成立（我们从不往它里面装东西）。

代价是它只带常用科学栈。你的脚本 `import ovito` / `import lmfit` /
`import rdkit` 时，它就到头了。

## Layer 2：项目本地 `.venv` 自动接手

内置环境因为缺包失败时，Tavotto 会在**项目内**找一个能用的虚拟环境。

**找哪里**：从脚本所在目录逐级向上到项目根为止，认 `.venv` / `venv` / `env`
三种目录名（stdlib venv、virtualenv、uv venv 都是这个形状）。判据是
`pyvenv.cfg` 存在且里面真有解释器——光有个叫 `env/` 的目录不算。

**不找哪里**：项目之外一律不碰（那是别人的项目），软链接指到项目外的也不认。
本版**不自动识别** Poetry / Conda / pyenv / pixi / hatch —— 它们的环境往往在
项目之外，要先问各自的 CLI 才知道在哪。用那些工具的话走 Layer 3。

**找到之后会做体检**（不是找到就用）：

| 检查 | 不过时 |
| --- | --- |
| Python 版本在 `>=3.10,<3.14` 内 | 拒绝使用，提示版本不支持 |
| `import matplotlib` 成功 | 拒绝使用——它不是一个绘图环境 |
| 能起 Tavotto worker（`figcapture` / `manifest` / `overrides` 可 import） | 拒绝使用 |
| 缺的那个包在它里面确实有 | 拒绝使用——换过去只会报同一个错 |

体检过了就**整体换解释器**重起 worker、重跑脚本。之后这个项目的
build / 渲染 / 编辑 / 撤销重放 / 预检 / 导出全部固定在这个环境里。

### 三条纪律

* **绝不混装**：不会把 `.venv` 的 `site-packages` 挂到内置 Python 上。编译
  扩展绑死 CPython ABI 与 NumPy ABI，混装轻则 import 即崩，重则算错数还不
  报错。切换的单位永远是完整解释器。
* **绝不改你的环境**：不 `pip install` 任何东西，不往你的 `.venv` 里装
  Tavotto（worker 代码由 Tavotto 提供、交给你的解释器执行）。
* **绝不跨项目**：决策存在这个项目的设置里（相对路径，跟着项目走），
  不写全局。A 项目找到的环境不会变成 B 项目的。

### 安全模型没有变

换的只是解释器。worker 沙盒 cwd、写入与删除守卫、相对路径只读回退、
Figure 捕获、写回校验全部照旧。**这不是 native 执行**：脚本仍然跑在
Tavotto 的 safe 档里。

## Layer 3：自己选一个环境

设置 →「渲染环境」。两个作用域：

* **这个项目**：只影响当前项目，存相对路径（项目挪走仍然有效）；
* **全局**：所有项目的默认，压过自动发现。

用户显式选过的环境**永远压过自动决策**。优先级完整顺序：

```text
TAVOTTO_WORKER_PYTHON  >  设置里指定的  >  这个项目记住的  >  内置 / 系统
```

选之前 Tavotto 会先体检一遍：「选了但用不了」比「没选」更难查。

## 出错时会看到什么

| 情况 | 提示 | 下一步 |
| --- | --- | --- |
| 内置缺包，项目里没有虚拟环境 | 内置环境缺少「X」，这个项目附近没找到可用的 Python 环境 | 选择 Python 环境 |
| 找到了 `.venv`，但它也没有那个包 | 找到了项目 `.venv`，但其中也没有「X」 | 装到那个环境里，或换一个 |
| 找到了，但 import 不到 matplotlib | 它不能导入 matplotlib | 换一个 |
| 找到了，但 Python 版本不支持 | Python 3.9 不在当前支持范围内 | 换一个 |
| 找到多个 | 列出候选，让你选 | 选一个 |
| 环境损坏 / 起不来 | 项目 Python 无法启动 | 诊断里有完整 stderr |

诊断包的 `project.environment_resolution` 一段回答「为什么用了这个 Python」：
来源、是不是自动接手、因为缺哪个包、那个环境的版本。

## Layer 4：`tavotto run`（尚未实施）

Layer 2/3 解决的是「**环境里缺东西**」。还有一类失败是「**执行语义不同**」：

* 脚本假定 cwd 是项目根（`python scripts/fig.py` 从别处跑就崩）；
* 依赖 `sys.argv`；
* 依赖 shell 里导出的环境变量；
* 必须用 `python -m package.figure` 而不是 `python figure.py`；
* 有自己的启动器 / Makefile / 任务运行器。

这些换个解释器解决不了，需要按项目原本的方式运行（ADR 0014 的 native 档）。

**决策门**：只有当真实数据表明剩余失败仍然大量集中在 cwd / argv / shell env /
`python -m` / 自定义启动语义上时，才恢复 `tavotto run`。如果绝大多数项目在
Layer 1–3 就能正常打开，它继续延期——native 执行放弃的是沙盒保证，那个代价
不该为了长尾去付。
