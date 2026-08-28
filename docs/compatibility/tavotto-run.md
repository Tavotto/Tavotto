# `tavotto run` · Beta

> 用**你自己的 Python** 跑**你自己的脚本**，Tavotto 只接管那个进程里创建的
> Matplotlib Figure。

```sh
tavotto run -- python figure.py
```

Tavotto uses the Python command you provide and attaches to Matplotlib figures
created in that Python process.

裁决与理由在 [ADR 0021](../adr/0021-tavotto-run-product-contract.md)（产品契约）
与 [ADR 0020](../adr/0020-native-matplotlib-bridge.md)（机制）。

---

## 它是什么

Tavotto 一直有一条 **safe** 执行档：由 Tavotto 挑解释器、切一个沙盒工作目录、
把 `savefig` 吞掉。对"脚本和数据都在图库目录里"的项目它很好用。

真实的老项目常常是另一个样子：

```sh
conda activate paper
python -m figures.fig3 --dataset run7
```

解释器、工作目录、命令行参数、环境变量、`-m` 调用方式——每一项都与 safe 档
不同。`tavotto run` 就是为这一类准备的**第二条执行档（native）**：

| | safe | **native（`tavotto run`）** |
|---|---|---|
| 解释器 | Tavotto 挑 | **你命令行里那一个**，绝不静默替换 |
| 工作目录 | 沙盒 | **你当前的目录，一个字节不动** |
| 命令行参数 | 只有脚本自身 | **你写的那些，原样** |
| 环境变量 | 部分清洗 | **原样继承这个 shell** |
| `savefig` | 吞掉（不写盘） | **照常写**（和你自己跑一样） |
| 写/删守卫 | 有 | **无** |
| stdout / stderr / stdin | 由 Tavotto 接管 | **是你的**，Tavotto 一个字节不碰 |
| 承诺 | "Tavotto 控制执行，不碰你的真实目录" | **"与你自己在终端里运行这条命令完全等同"** |

> **这不是沙盒。** 脚本拥有与你直接运行它时**完全相同**的文件、网络与系统
> 权限。只运行你信任的代码。

---

## 用法

```text
tavotto run [Tavotto 选项] -- <python> <脚本.py|-m 模块> [脚本自己的参数…]
```

```sh
tavotto run -- python figure.py
tavotto run -- python figure.py --sample A --temperature 800
tavotto run -- /Users/me/paper/.venv/bin/python figure.py
tavotto run -- python -m paper.figures.xps --sample A
```

### `--` 是必须的

`--` 左边是 Tavotto 的选项，右边**整条原样**交给 Python。

不写就报 `run_command_missing`。这不是形式主义：你的脚本完全可以有自己的
`--project` / `--quiet`。一个会猜"哪些参数属于谁"的解析器猜错时不会报错——
它会**吃掉你的参数**，然后脚本以一个你没要求的配置跑完并出图。

### Tavotto 选项

| 选项 | 作用 |
|---|---|
| `--project <路径>` | Tavotto 用哪个项目目录组织图库与文档。**不改变工作目录** |
| `--quiet` | 不打印 Tavotto 自己的状态行（你的脚本输出不受影响） |
| `--status-file <路径>` | 把机器可读的结果写到这个文件（见下） |

### 没有 `--json`

stdout 是**你的程序的**：`print`、`tqdm` 的进度条、二进制输出都在那条流上。
承诺"stdout 只有一行 JSON"就与这条语义直接冲突。要机器可读的结果用
`--status-file`：

```json
{
  "schema": 1, "product": "tavotto run", "beta": true,
  "target_kind": "script", "arg_count": 2, "python_version": "3.13.1",
  "script_exit_code": 0, "figures_captured": 2,
  "session_result": "ok", "error_code": null,
  "command_fingerprint": "…"
}
```

它**原子写**，不含 token、不含环境变量、不含你参数的**值**（只记数量）。

---

## 会发生什么

```text
1. 解析并体检你的命令        ← 这一步之前一行代码都没跑
2. 唤起 Tavotto 桌面应用
3. 桌面上出现一次确认（Python 路径 / 工作目录 / 目标 / 权限说明）
4. 你点「运行并连接」
5. **这时才**启动你的 Python
6. 脚本跑到 plt.show() 或跑完 → 图出现在 Tavotto 里，可以编辑
7. 点「继续运行脚本」→ 脚本接着往下跑
8. 脚本退出 → tavotto run 返回**它的**退出码
```

**确认之前你的脚本一行都没跑。** 取消掉，什么都不会发生。

### 那一屏确认

写明**这一条命令**的四件事：目标（脚本或 `-m` 模块）、解释器路径与版本、
工作目录、图库目录。带了参数的话只说**个数**——参数的值不经过界面（Tavotto
从头到尾就没记过它们）。

> 此模式使用项目自己的 Python。脚本拥有与你在终端中直接运行时相同的文件
> 权限。Tavotto 只接管当前 Python 进程中的 Matplotlib Figure。仅运行你信任
> 的代码。

`□ 记住此项目和此 Python` **默认不勾**。勾上之后，同一个图库配同一个解释器
下次不再询问；解释器换了、图库移动了、或者权限规则升级之后会重新询问，随时
可以在设置里撤销。**即使记住，你仍然得自己敲那条命令**——这不是"允许 AI
自动执行"。

点外面和 Esc 都不算回答：那个终端正等着这一屏，随手关掉只会让它挂到超时。

### 会话卡片

脚本跑起来之后，画布右上角有一张卡片，说的是**此刻**的情况：正在启动 /
脚本正在运行 / 已停下（几张图可以编辑）/ 正在继续 / 已结束 / 已放手 / 失败。

| 按钮 | 它做什么 |
|---|---|
| **继续运行脚本** | 先把 Figure 恢复成脚本原样，再放开屏障；你的编辑在下一次停下来时重放 |
| **放手** | 脚本继续正常跑完，Tavotto 不再控制它。**不杀进程** |
| **终止脚本** | 直接结束脚本，退出码固定 5。**只在停下来的时候可用**，且要二次确认 |

脚本正在跑的时候没有「终止」，是刻意的：那时候真正该做的是你在自己的终端里
按 Ctrl+C——那个进程是你的，信号也是你的。

### 图上的角标

| 情况 | 角标 |
|---|---|
| 停下来了，可以编辑 | 无——不打扰 |
| 脚本正在跑 | 「脚本正在运行，停下来才能编辑」 |
| 会话已经结束 | 「会话已结束，重新运行原命令可继续编辑」 |

后两句都是**在你动手之前**说的。会话结束之后画布上那张图仍然看得见（它是
最后一次的预览），但对象级编辑与权威导出要等你重新跑一次原命令。

### 编辑不会改变你脚本的行为

这条是硬保证：

```python
ax.set_title("Script")
plt.show()                          # 你在 Tavotto 里把标题改成 "Tavotto"
assert ax.get_title() == "Script"   # ✅ 通过——脚本看到的还是它自己写的那个
```

Tavotto 在放开脚本之前，会把 Figure 恢复成**脚本原样**；等到下一个
`plt.show()`，再把你的编辑按元素身份重放回去。所以：

* **你的代码是执行权威**——它看到的与没有 Tavotto 时逐字段一致；
* **Tavotto 的编辑是呈现层**——下一个屏障里它还在。

脚本在两次 `show()` 之间改动过的对象，会成为新的基准；被删掉的对象上的编辑
会如实报成"孤儿"，**绝不会落到"最像的那个对象"上**。

### 终端还是你的

* `print` / `tqdm` 原样出现在你的终端；
* `input()` 拿得到你敲的东西；
* **Ctrl+C** 照常打断你的脚本（Tavotto 不吞它，也不会抢在你的进程前面退出）；
* Tavotto 自己的提示**全部写 stderr**，`--quiet` 可以关掉。

---

## 退出码

| 情形 | 退出码 |
|---|---|
| 命令写错了（缺 `--`、不支持的命令或标志、目标不存在…） | **2** |
| 你在桌面上点了取消 | **3** |
| 没有桌面应用 / 连接超时 / 控制通道失败 | **4** |
| 你在界面上点了"终止脚本" | **5** |
| **脚本启动之后** | **脚本自己的退出码**（`sys.exit(3)` → 3） |
| 脚本被信号终止（POSIX） | `128 + 信号号`（如 Ctrl+C → 130） |

"跑完了但一张图都没捕获到"**不是失败**：脚本退出码原样透传（0 就是 0），
Tavotto 的结果记在 `--status-file` 的 `session_result` 里。

---

## 这一版**不支持**什么

`tavotto run` 是 **Beta**，边界是明确的：

| 不支持 | 会看到 |
|---|---|
| 任意 shell 命令、`make`、`bash`、`poetry run`、`uv run`、`conda run`、`Rscript` | `unsupported_run_command` |
| 任何 Python 解释器标志（`-c` / `-O` / `-S` / `-I` / `-E` / `-X…` / `-W…`） | `unsupported_python_option` |
| `py -3.12`（Windows launcher 的版本选择） | `unsupported_run_command` |
| CPython 以外的实现 | `unsupported_python_implementation` |
| **子进程里**创建的 Figure | 捕获不到（钩子只在被直接执行的那个进程里） |
| Jupyter / IPython | 不在本轮范围 |
| 写回你的**源代码** | 恒禁 |
| 写回你脚本自己存出来的**原始产物** | 恒禁 |
| Linux / 没装桌面应用 | `native_desktop_required`（**在启动脚本之前**就报） |

以及：**Tavotto 不会往你的环境里安装任何东西**。它把一份 runner 的绝对路径
交给你的解释器去执行，你的 `.venv` 里不需要（也不允许）有 Tavotto。

---

## 与 pip 安装的关系

同一个 Python 环境上，`tavotto run` 会话与 Tavotto 的"一键安装依赖"是**互斥**的：

* 正在装依赖 → 起不了 native 会话（`environment_mutating`）；
* 有 native 会话在跑 → 装不了依赖（`environment_in_use_by_native_session`）。

**不会自动结束你的脚本**：那个进程是你的，里面可能有跑了两小时的计算。
装依赖可以等。

---

## 出问题时

| 码 | 意思 |
|---|---|
| `run_command_missing` | 缺 `--`，或 `--` 后面什么都没有 |
| `interpreter_not_found` / `interpreter_not_executable` | 那个 `python` 找不到 / 跑不起来。Tavotto **绝不**替你换一个 |
| `unsupported_python_version` | 需要 3.10 或更高 |
| `script_target_missing` / `script_target_not_file` / `invalid_module_name` | 目标不对 |
| `native_desktop_required` | 没找到 Tavotto 桌面应用 |
| `native_attach_timeout` / `native_attach_cancelled` | 桌面没连上 / 你取消了 |
| `native_session_not_at_barrier` | 脚本正在跑，等下一张图出现才能编辑 |
| `native_session_offline` / `native_session_ended` | 那条会话结束了。重新运行原命令即可继续对象级编辑 |
| `native_asset_conflict` | 这张图已经绑在另一条会话上（你在两个终端跑了同一个脚本） |
| `no_figure_captured` | 脚本跑完了，但没有 Matplotlib Figure |

**码是稳定的，文案随时可能改**——写脚本请按码判断。

---

## 会话结束之后

Tavotto 会保留最后一帧预览，面板上标着「这张图来自已结束的 Tavotto Run
会话」。这时：

* 看得到、可以留在画布里；
* **不能**做对象级编辑，**不能**做权威导出（那需要 live Figure）；
* 重新运行原命令，它会重新接上，你之前的编辑会被重放回去。

Tavotto **不会**替你自动重跑那条命令。
