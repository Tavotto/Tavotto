# ADR 0021：`tavotto run` 的产品契约（Native Bridge 的控制面与所有权）

状态：**Accepted**（Compatibility Bridge Session 9，产品面第一版 = **Beta**）
日期：2026-08-28
相关：[0020 Native Matplotlib Bridge](0020-native-matplotlib-bridge.md)（机制
已定稿，本 ADR 只做产品控制面）、
[0014 Safe/Native 两档 Profile](0014-safe-native-execution-profiles.md)（本 ADR
裁决它 §7 的第 3、4 问）、
[0013 Runtime Figure Assets](0013-runtime-figure-assets.md)、
[0003 worker 协议 v1](0003-worker-protocol-v1.md)、
[0008 本机会话认证](0008-unified-local-session-auth.md)、
[0019 受控依赖修复](0019-controlled-dependency-repair.md)（环境租约与它同源）、
[总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)。

## 裁决摘要

| # | 问题 | 裁决 |
|---|---|---|
| 1 | CLI 语法 | `tavotto run [Tavotto 选项] -- <python> <目标> [用户参数…]`，**`--` 强制**（§2） |
| 2 | 谁拥有用户的 Python | **CLI**。桌面 sidecar **绝不** spawn 用户进程（§1） |
| 3 | 控制面形状 | **CLI 托管的认证 raw relay**：两个 listener、两枚 token、透明双向转发（§3） |
| 4 | native 会话进不进 safe 池 | **不进**。独立 `NativeSessionRegistry`，但对外是 Worker-like 接口（§5） |
| 5 | 与 pip 安装的互斥 | 抽出唯一的 `envlease`（环境租约），safe worker / native 会话 / 安装三者共用一张表（§6） |
| 6 | CLI → 桌面怎么交接 | 一次性 **native handoff descriptor**（私有文件）+ argv 只传不透明 ID（§4） |
| 7 | 用户确认何时发生 | **在 spawn 用户 Python 之前**。未确认 = 一行用户代码都没跑（§7） |
| 8 | `continue` 之后的 Figure 基准 | **restore before continue, rebase at next barrier**（§8） |
| 9 | native RuntimeAsset 身份 | 逻辑身份（script+stem+profile+fingerprint）与 live route（session id）**分开**（§9） |
| 10 | 退出码 | 用户脚本启动后一律透传它的真实退出码；之前的失败各有稳定码（§10） |
| 11 | `--json` | **不提供**。用户拥有 stdout；改用 `--status-file`（§11） |
| 12 | 没有桌面 App / Linux | `native_desktop_required`，**在 spawn 之前**报（§12） |

---

## 0. 这一版是 Beta

`tavotto run` 进 CLI、进 README、进帮助，但**明确标 Beta**：

* 只支持 `python 文件.py` 与 `python -m 模块`（§2 列出全部不支持的）；
* 只接管**当前这个 Python 进程**里创建的 Figure（子进程、Jupyter 不在内）；
* **不写回源码、不写回原始产物**；
* 用户代码拥有与他自己敲那条命令**完全相同**的权限——不是沙盒。

文案里不许出现 "Compatible with any Python project" / "Run anything" /
"Safe native execution" / "Fully sandboxed"，理由与 ADR 0014 §2 同一条：
**两档的文案必须与机制逐条一致**。

---

## 1. 进程所有权：CLI 必须继续拥有用户的 Python

这是本 ADR 最重要的一条约束，也是唯一一条**改了就整个产品变味**的。

```text
用户终端
   │
   ▼
tavotto run（CLI 进程）
   │
   ├── 拥有 stdin / stdout / stderr        ← 原样是用户终端的
   ├── 拥有 invocation / env / cwd / argv  ← 原样是这个 shell 的
   ├── 拥有用户 Python 子进程              ← 它是 CLI 的直接子进程
   └── 托管一条认证 relay
           ├──── Tavotto 桌面 sidecar 连进来
           └──── 用户 Python 的 Bridge Runner 连进来
```

**为什么不能让桌面 sidecar 去 spawn 用户 Python**（这是最容易走的那条路，
因为 sidecar 本来就在管 worker）：

| 承诺 | sidecar spawn 时的事实 |
|---|---|
| `stdout` 是用户的 | sidecar 的 stdout 已经写进 `sidecar.log` |
| `stdin` 是用户的 | sidecar 根本没有终端 stdin，`input()` 当场 EOF |
| env 是当前 shell 的 | sidecar 的启动环境是**桌面壳**的，`conda activate` 的结果不在里面 |
| cwd 是当前目录的 | sidecar 的 cwd 是它自己的 |
| Ctrl+C 语义正确 | 终端的信号送不到 sidecar 的子进程 |

要让 sidecar 拿到这些，只能把整份环境序列化到磁盘再重建——那就是
**第二份环境解析实现**，而 ADR 0020 §4 已经裁决过：重建必然与真正的激活有
出入，且出入是静默的。

所以：**用户 Python 是 `tavotto run` CLI 的子进程，CLI 一直活到用户脚本退出。**
CLI **不是** "启动一下就退出" 的 detached launcher。

看护：`tests/native/test_run_cli_integration.py`（父子关系、env/cwd/argv/stdio
真进程对拍）+ `tests/native/test_run_invocation.py` 的结构性守卫。

---

## 2. 稳定 CLI 契约

### 2.1 语法

```text
tavotto run [Tavotto 选项] -- <python> <脚本.py> [用户参数…]
tavotto run [Tavotto 选项] -- <python> -m <模块>  [用户参数…]
```

**`--` 是强制的。** 不写就报 `run_command_missing`，并给出带 `--` 的示例。

不做"猜哪些参数是 Tavotto 的、哪些是用户脚本的"的模糊 parser：用户脚本
完全可以有 `--project`、`--quiet`、`--status-file` 同名参数，猜错的后果是
**Tavotto 吃掉了用户的参数**，而脚本会以一个它没要求的配置跑完并出图——
静默、且结果看起来是对的。

### 2.2 Tavotto 选项（正式）

| 选项 | 语义 |
|---|---|
| `--project <路径>` | Tavotto 用哪个项目目录组织图库/文档。**不改变 cwd**（§2.5） |
| `--quiet` | 抑制 Tavotto 自己写到 stderr 的状态行（用户脚本的输出不受影响） |
| `--status-file <路径>` | 把机器可读的结果写到这个文件（§11） |

内部测试用的 flag 可以存在，但**不进正式 help**（统一 `--x-` 前缀，
`argparse` 的 `help=SUPPRESS`）。

### 2.3 解释器

正式支持：`python` / `python3` / `python.exe` / 绝对解释器路径 /
当前 `PATH` 能解析到的 Python symlink。

**不支持**（一律显式报错，绝不静默丢掉）：

| 形态 | 错误码 |
|---|---|
| `py -3.12`、`make`、`bash`、`sh`、`poetry`、`uv`、`conda`、`Rscript`… | `unsupported_run_command` |
| `python -c`、`python -`、`python -S/-I/-E/-O/-X…/-W…` 等任意解释器标志 | `unsupported_python_option` |

`-m` 是唯一被认作"目标"的标志（它不是解释器行为标志）。

### 2.4 解释器体检（spawn 之前）

在起用户脚本之前跑一次**只读**探针（`<python> -c <一行>`，不 import
matplotlib、不动用户目录），至少确认：

* 版本 ≥ 支持下界 → 否则 `unsupported_python_version`；
* `sys.implementation.name == "cpython"` → 否则 `unsupported_python_implementation`；
* 可执行 → 否则 `interpreter_not_executable` / `interpreter_not_found`。

**绝不静默替换成 Tavotto 自带的 Python**（ADR 0020 §4 已裁决的同一条）。

matplotlib 的存在**不在**体检范围内：它可能装在脚本运行时才生效的路径上，
而真正的答案由真实运行给出（`ModuleNotFoundError` 会原样出现在用户终端上，
与他自己敲那条命令完全一样）。

### 2.5 `cwd` 与 `project_root` 是两个概念

| | 是什么 | 谁决定 |
|---|---|---|
| `cwd` | 用户当前终端的工作目录。相对路径、`sys.path`、`python -m` 全靠它 | **原样继承，Tavotto 一个字节不动** |
| `project_root` | Tavotto 用来组织图库、保存文档、放素材的项目目录 | `--project` > 从目标向上找现有注册表（≤ `MAX_PARENTS`）> 兜底 |

兜底规则：

* **script 目标**：脚本所在目录；
* **module 目标**：当前 cwd。

**绝不静默越过到用户 home 或整个磁盘**（与 `handoff._project_root` 的
`MAX_PARENTS` 同一条纪律）。`--project` 只改 `project_root`，**不改 cwd**。

---

## 3. 控制面：CLI 托管的认证 raw relay

```text
1. CLI 解析并校验 invocation（此时一行用户代码都没跑）
2. CLI 起 relay：两个 loopback listener + 两枚独立 token
3. CLI 写一份一次性 native handoff descriptor（§4）
4. CLI 唤起 / 通知桌面（argv 只带不透明 ID）
5. 桌面展示 native 权限确认（§7）
6. 用户确认 → sidecar 连上 CLI 的 attach listener 并认证
7. **确认之后** CLI 才用当前 cwd/env/stdin/stdout/stderr spawn 用户 Python
8. Bridge Runner 连上 CLI 的 child listener 并认证
9. CLI 在两条已认证连接之间做**透明双向字节转发**
10. sidecar 直接说 worker 协议 v1；用户 Python 直接跑 LiveFigureSession
11. CLI 等用户 Python 退出，返回它的退出码
```

**relay 绝不做的事**（写死在实现里，有结构性看护）：

* 解析并重写 manifest；
* 生成另一套 request envelope；
* 改 canonical patch；
* 碰 Figure；
* 造 native protocol v2。

它只做一件事：

```text
sidecar 侧已认证的字节  ↔  Bridge Runner 侧已认证的字节
```

### 3.1 为什么不是 "sidecar 预留端口 → 子进程直连 sidecar"

那条路更短，但它要求 CLI 把 sidecar 的端点与凭据交给用户脚本所在的进程，
于是：

* 一枚**能连 sidecar** 的 token 落进了用户脚本的进程环境。ADR 0020 §6 的
  纪律是"token 一起来就摘掉"，而那条纪律之所以够用，是因为那枚 token 的作用域
  只有"这一条 bridge 连接"。换成 sidecar 的凭据，作用域立刻大一个量级；
* 断链语义变成三方：sidecar 崩了 CLI 完全不知道，用户脚本卡在屏障上；
  relay 形态下 CLI 是唯一同时知道两边死活的那个，屏障释放（§8.1）因此有
  确定的归属。

raw relay 多的那一跳是**纯字节**，协议面零增加。所以选它。

### 3.2 relay 安全

* 两侧 token **各一枚，互不通用**（`desktop attach token` / `child token`）；
* 只 bind `127.0.0.1`，端口 0 让内核分配；
* 256-bit（`secrets.token_urlsafe(32)`），`compare_digest` 比对；
* **错 token 不消耗 listener**（认证失败断开、继续 accept——否则本机任何进程
  抢先连一下就是 DoS，与 ADR 0020 §6 同一条）；
* 一次成功 attach 之后**关闭该侧 listener**（一次会话一条连接）；
* descriptor 过期 / 已取消 → 拒绝 attach；
* token 不进日志、不进 URL、不进前端、不进 argv、不进遥测。

### 3.3 relay 不是权限提升

CLI / bridge / relay **不得**：改用户 env（除 ADR 0020 §4 那一个立刻被摘掉的
token）、开网络能力（唯一 socket 是需要 token 的 loopback）、改文件权限、
代用户跑额外程序、把钩子传播给孙进程、自动装包、自动重跑命令。

> **Tavotto adds hooks, not privileges.**

---

## 4. Native handoff descriptor

CLI 与桌面之间**不经 argv 传** token / 环境 / 完整命令 / 凭据。

```text
<data_dir>/session/native/<不透明 ID>.json     目录 0700，文件 0600
```

* ID 是 `secrets.token_hex(16)` 的不透明串，**严格格式校验**（`^[0-9a-f]{32}$`）；
* argv 只传这个 ID：`Tavotto --open <project> --native-session <ID>`；
* 有 `expires_at`；**一次性**（consume 后删除）；attach / cancel 之后删除；
* 启动时清理 stale；
* **不含**完整 environment、package index、secret argv 原文；
* token **只**存在于这份私有文件里。

可以包含的 metadata（前端只拿 sanitized 的那一份，**永远不含 token**）：

```text
schema / native_id / relay host=127.0.0.1 / relay ports / attach token（仅文件内）
project_root / interpreter 显示路径 / cwd 显示路径 / target_kind / target 显示
argv 数量 / command fingerprint / created_at / expires_at
```

### 4.1 Trusted descriptor API（TOCTOU 纪律）

网页请求**只能**提交 `native_id`。后端：

1. 在**固定目录**里解析（`realpath` 之后仍必须在那个目录下 → 否则
   `native_handoff_invalid`）；
2. 校验 schema / 版本 / 过期 / 一次性；
3. metadata 与 attach token **来自文件，不来自请求体**。

> 界面确认的是哪条 invocation，执行端就只能执行那条。

前端不得在确认之后替换 interpreter / target / host / port / token
（Session 7B 的 plan-binding 同款纪律）。

---

## 5. `NativeSessionRegistry`：不进 safe 池

**裁决：native 会话不进 `pool._workers`，也不进 workerd 的 LRU 池。**
（ADR 0014 §7 第 4 问的答案。）

理由不是"懒得做"，是**池的语义对它是错的**：

| 池的语义 | 对 native 会话意味着 |
|---|---|
| 超出 `MAX_ALIVE` 按 LRU 淘汰 → `shutdown()` | **杀掉用户正在跑的脚本** |
| `unknown_session → reopen`（workerd） | 重跑用户的命令——那是用户的一次具体 invocation，不可透明重建 |
| key = (figures_dir, script) | native 的身份还含 cwd / argv / env / 解释器 |
| 进程属主 = sidecar | native 的进程属主是 **CLI** |

所以新建独立的 `nativesession.NativeSessionRegistry`。**但它对上层必须是
Worker-like 的**（`ensure_built` / `override` / `export` / `render_png` /
`preview_png` / `svg_path` / `resume` / `detach` / `terminate` / `shutdown`），
否则每个端点都要写第二套 native 分支——那正是"两个入口两个答案"的形状。

统一入口是 `enginesession.resolve()`：按 `execution_profile` + native route
决定给 safe `EngineWorker` 还是 native 会话代理，**禁止**在每个端点里重复
`if native … else pool.get …`。

### 5.1 状态闭集

```text
pending_confirmation → waiting_for_cli → starting_python → running_script
                                              ↕
                                     waiting_for_figure
                                              ↕
                                          barrier ⇄ continuing
                                              ↓
                                    ended / detached / failed
```

**不用多个互相矛盾的 boolean。** 每条 session 至少记：
`session_id / project_root / interpreter / interpreter_fingerprint /
target_kind / target 显示 / cwd / state / barrier_reason / process_pid /
descriptors / active stems / started_at / last_event_at / terminal error /
exit_code / transport`。

### 5.2 transport 只有一个 reader

ADR 0020 的父进程侧用"每次请求开一个读线程 + `join(timeout)`"——spike 够用，
**产品化不行**：超时之后那个线程还卡在 socket 上，下一条请求再开一个，
两个 reader 抢同一条流，framing 就没有证明了。

产品形态：

```text
一个常驻 reader 线程
     ↓  校验过的 JSON 帧
按 request_id 配对的响应队列  +  独立的事件队列/回调
```

* **只有一个线程读 socket**；
* malformed frame → 整条 session 判 `failed`（不猜、不跳过）；
* EOF → 唤醒所有等待者（不让任何人永久挂着）；
* 请求可以串行，不为并发过度设计；
* **超时之后 session 标记 poisoned**——绝不"再开一个 reader 试试"；
* reader 线程**不碰 Figure**（Figure 仍只在用户进程主线程里动，ADR 0020 §7）。

---

## 6. 环境租约：native 与 pip 安装的互斥

`#177` 之后 `pool.mutating_environment()` 在装依赖期间独占一个环境：先
`shutdown_workers_using(python)` 收掉池里的 worker，再让 `pool.get()` 拒起
新的（`environment_mutating`）。**native 会话不在池里，那把锁机制上看不见它。**

**裁决：抽出唯一的环境租约表 `engine/envlease.py`，三方共用。**

```text
状态：idle | safe_workers | native_sessions | mutating
```

| 场景 | 行为 |
|---|---|
| 环境正在 mutating 时启动 native | **拒绝**，`environment_mutating` |
| native 会话活跃期间 | 该解释器上登记一条 active native lease |
| 有 active native session 时开始依赖修复 | **拒绝安装**，`environment_in_use_by_native_session`；**不自动杀 native 会话** |
| native 会话结束（正常 / 崩溃 / CLI 挂了） | 释放 lease |

**不新开第二张 `_native_busy` 表**：`pool._mutating` 整个搬进 `envlease`，
`pool` 变成它的消费者（行为逐条不变，先用测试钉住既有语义再搬）。

用户看到的：

> 这个 Python 环境正在被 Tavotto Run 使用。请先结束正在运行的脚本，再安装依赖。

**"不自动杀" 是刻意的**：那个进程是用户的，里面可能有跑了两小时的计算。
安装依赖是一件可以等的事；杀掉用户的脚本不是。

---

## 7. 用户确认

CLI invocation 本身是用户明确发起的，但 native 与 safe 的权限差异非常大，
**第一次**对某个（项目 × 解释器）组合仍要在桌面显示一次确认。

顺序是硬性的：

```text
CLI 起 relay + 写 descriptor
      ↓
桌面收到 pending native request
      ↓
展示确认（Python 路径 / 工作目录 / 目标 / 权限说明）
      ↓
用户点「运行并连接」
      ↓
sidecar attach relay
      ↓
CLI 才 spawn 用户 Python        ← **用户确认之前，一行用户代码都没跑**
```

核心文案（中英各一份）：

> 此模式使用项目自己的 Python。脚本拥有与你在终端中直接运行时相同的文件权限。
> Tavotto 只接管当前 Python 进程中的 Matplotlib Figure。仅运行你信任的代码。

按钮：`[运行并连接]` `[取消]`；可选 `□ 记住此项目和此 Python`（**默认不勾**）。

### 7.1 "记住选择" 绑定具体环境

**不做**全局 `native_confirmed = true`。许可至少绑定：

```text
project identity + interpreter realpath + cwd/project 关系 + permission schema version
```

解释器路径变化、项目移动、schema 升级之后**重新确认**。不存 argv / env / token。
设置里给撤销入口。

即使记住许可，用户**仍然必须亲自运行 `tavotto run …`**——这不是"允许
AI/MCP 自动执行"。

### 7.2 MCP / Agent 边界

本轮**不新增** `native_run` MCP 工具。模型给的 path / command / interpreter
**不是用户授权**（ADR 0009 的 fail-closed 纪律）。安全导入失败的 UI 里可以
展示一条**可复制的命令**，但绝不由模型静默执行。

---

## 8. Barrier 语义：restore before continue, rebase at next barrier

这是本 Session **必须新增的核心不变式**，也是唯一一条"做错了会改变用户脚本
语义"的。

```python
ax.set_title("Script")
plt.show()                          # ← 屏障：用户在 Tavotto 里把标题改成 "Tavotto"
assert ax.get_title() == "Script"   # ← 脚本继续跑，它凭什么看到 "Tavotto"？
```

Tavotto 的 override 是**呈现层**，用户代码是**执行权威**。所以：

1. 屏障中正常编辑；
2. 用户点 continue；
3. **释放主线程之前**：保存当前 Tavotto patch 列表 → 把 Figure 恢复到本次
   屏障的 script baseline（`overrides.apply(state, [])`——全量列表语义天然
   就是"回到原样"）；
4. 脚本继续执行——它看到的与没有 Tavotto 时**一模一样**；
5. 下一个屏障：
   * 重新 `manifest.instrument(state)`：把用户代码此后产生的新状态当作
     **新 baseline**（`originals` 在第 3 步已被 `apply([])` 清空，这次重新采样）；
   * 按**稳定 gid** 重放之前保存的 patches；
   * 匹配不上的变成 **orphan warning**（`overrides.apply` 本来就报
     "元素不存在"）；
   * **绝不落到"最像的对象"**。

结果：

| 谁 | 看到什么 |
|---|---|
| 用户脚本 | 与没有 Tavotto 时逐字段一致（title 仍是 `"Script"`） |
| Tavotto UI | 下一个屏障里 title 仍是 `"Tavotto"`，xlabel 是脚本刚改的新值 |

**这条不过，不得发布 Beta。** 判别性用例：
`tests/native/test_native_barrier_semantics.py::test_the_script_never_sees_a_tavotto_override`。

### 8.1 任何释放屏障的路径都要恢复 baseline

不只是用户点 continue。**唯一的 `release_barrier(...)` 语义**覆盖：

```text
continue / detach / desktop disconnect / app crash / relay EOF /
confirmation 关闭 / timeout recovery（选择让脚本继续时）
```

否则 App 一崩，用户脚本反而带着 Tavotto 的 override 继续执行——那是最坏的
一种：**故障路径上的语义比正常路径更宽松**。

`terminate` 不需要继续执行，所以它是唯一不必先恢复的释放路径。

### 8.2 `show(block=False)` 与重复 `show()`

`block=False` 的语义**保持 ADR 0020 §5.3 不变**：捕获、不进屏障、脚本继续。
不为了"更快打开 UI"把它改成阻塞。

重复 `show()`：session 不换；已有的图 rebase + replay；新图首次加入；
多图清单更新；不重复加同一张；已有 panel 继续绑定。脚本最终结束还有一次
`script_end` 屏障——**每个屏障都必须被明确 continue / end / detach**，
不允许两边互等（ADR 0020 §5.4 的同一条）。

---

## 9. Native Runtime Asset：逻辑身份 vs live route

ADR 0013 的 asset id 是 `runtime:<script>#<stem>`。**一次性 session token
绝不进 asset id。**

| | 内容 | 生命周期 |
|---|---|---|
| **Logical identity** | `script` / `stem` / `execution_profile` / `source_fingerprint` | 进文档，长期 |
| **Live route** | `native_session_id` | 只在进程内 route map，会话结束即失效 |

* 文档持久化 logical descriptor；
* relay token / endpoint **不进文档**；
* App 重开**不会**自动执行 native 命令；
* panel 保留最后一帧 preview，并明确显示「Native session 已结束」；
* 相同 logical asset 再次 attach 时**可以**重新绑定，绑定后重放现有 overrides；
* 匹配不稳定时**拒绝，不猜**。

### 9.1 safe 与 native 不得静默串路由

同一个 `script + stem` 可能既在 safe 里跑过、又在 native 会话里跑过，
**逻辑 asset id 可能相同**。路由必须由 `execution_profile` 决定：

* native panel：live session 在 → native；不在 → **offline**；
  **绝不静默 fallback 到 safe**；
* safe panel：不因为有 native 会话就切过去。

否则用户看到的是"另一个环境生成的图"，而界面上什么都没说。

### 9.2 同一 logical asset 的并发 native 会话

用户可能在两个终端同时跑同一个脚本。**第一版不静默让后来的覆盖前面的 route。**

> **V1：一个项目 + 一个 logical asset 同时只允许一条 active native route。**

第二条会话照常跑脚本（那是用户的进程，Tavotto 无权干涉），但**不自动占用
现有 panel**，报 `native_asset_conflict`。

### 9.3 只有 barrier 才能编辑

| state | 允许 |
|---|---|
| `barrier` | manifest / edit / render / export / continue |
| `running_script` / `continuing` | **不允许** Figure 请求 → `native_session_not_at_barrier` |

**不在后台排队然后几分钟后偷偷执行**用户之前点的操作。UI 在脚本继续运行时
禁用编辑控件但保留最后一帧 preview。

### 9.4 Export / Preflight / Writeback

| 动作 | native live | native ended |
|---|---|---|
| object-level edit | ✅ | ❌ `native_session_offline` |
| preflight | ✅ | ❌ |
| PDF/PNG/SVG authoritative export | ✅（由 **live Figure** 当前渲染） | ❌ 绝不伪造 |
| canvas composition | ✅ | 用 cached preview |
| **source writeback** | ❌ 恒禁 | ❌ |
| **original artifact writeback** | ❌ 恒禁（ADR 0020 §10） | ❌ |

cache 里有 preview **不等于** live session 还在。

---

## 10. 输出与退出码

### 10.1 Tavotto 的信息全部写 stderr

```text
[Tavotto Run · Beta]
Python: /path/to/.venv/bin/python
Working directory: /path/to/project
Target: figure.py
Waiting for Tavotto desktop…
```

`--quiet` 抑制这些。**用户 stdout 一个字节不解析、不加前缀、不缓冲改写。**

### 10.2 退出码

| 情形 | 退出码 | 常量 |
|---|---|---|
| invocation / usage 错误（`--` 缺失、不支持的命令/标志、目标不存在…） | **2** | `EXIT_USAGE` |
| 用户取消确认 | **3** | `EXIT_CANCELLED` |
| 桌面不可用 / attach 超时 / relay 失败 | **4** | `EXIT_ATTACH_FAILED` |
| 用户在 UI 明确"终止脚本" | **5** | `EXIT_TERMINATED` |
| 用户 Python **已启动之后** | **用户脚本的真实退出码** | — |
| 用户脚本被信号终止（POSIX） | `128 + signo`（shell 惯例） | — |

**不把所有失败都返回 1。** 「Tavotto UI 断开但脚本继续并成功」返回脚本结果
（0），不是错误——那正是 detach-and-continue 的定义。

### 10.3 没有捕获到 Figure

* 不打开空的 Figure picker；
* CLI 结果里给 `no_figure_captured`；
* **脚本 exit 0 但没有 Figure 时，进程退出码仍然是 0**——Tavotto 的产品结果
  和脚本的退出码是两件事，status file 里**分开记**。

---

## 11. 为什么不提供 `--json`

用户程序拥有 stdout：`print(...)`、`tqdm(...)`、输出 JSON、输出二进制。
`tavotto run --json` 如果承诺"stdout 只有一行 JSON"，就与 Native Bridge 的
核心语义直接冲突——要么污染用户输出，要么这个承诺是假的。

**改用 `--status-file <路径>`**：

* **原子写**（临时文件 + `os.replace`）；
* UTF-8 JSON；
* **不含 token**、不含完整环境、默认不含完整 argv；
* 含：`arg_count` / `target_kind` / `script_exit_code` / `figures_captured` /
  `session_result` / `error_code`；
* **只有调用方明确给出 `--status-file` 才写**。

---

## 12. 没有桌面 App / Linux

当前桌面发行只有 Windows / macOS。**第一版：明确要求 desktop app。**

```text
找不到桌面 App  →  native_desktop_required  →  退出码 4
```

关键是**顺序**：这个判断在 **spawn 用户 Python 之前**做完。绝不允许

```text
脚本已经跑起来了 → 才发现没有 UI → 告诉用户
```

（浏览器模式的 fallback 不是不能做，但"做半个"比不做更坏——它会让用户以为
有 UI，而 Figure 无处可去。留给后续，届时必须完整验证。）

---

## 13. 稳定错误码

**code 稳定，文案随时可改**（与 `tavotto open` / `doctor` 同一条纪律）。
唯一出处 `engine/runcodes.py`，中英两份文案同表——加一条码却只给一种语言，
就是把英文用户送回 traceback。

| 分组 | 码 |
|---|---|
| invocation | `run_command_missing` `unsupported_run_command` `unsupported_python_option` `interpreter_not_found` `interpreter_not_executable` `unsupported_python_version` `unsupported_python_implementation` `script_target_missing` `script_target_not_file` `invalid_module_name` `project_root_invalid` `project_unreadable` |
| handoff | `native_handoff_invalid` `native_handoff_expired` `native_handoff_consumed` |
| attach / relay | `native_desktop_required` `native_attach_cancelled` `native_attach_timeout` `native_attach_failed` `native_relay_failed` `native_auth_failed` |
| session | `native_session_conflict` `native_asset_conflict` `native_session_not_at_barrier` `native_session_offline` `native_session_ended` `native_session_disconnected` `bridge_child_exited` |
| 结果 | `no_figure_captured` |
| 环境 | `environment_in_use_by_native_session`（+ 复用既有 `environment_mutating`） |

后端**不得**把整段中文 traceback 当主错误。

---

## 14. 不做的事

任意 shell runner / `make`·`bash`·`poetry`·`uv`·`conda run` / MCP native 工具 /
Jupyter / 钩子传播给孙进程 / 远程 Python / Figure pickle / 往用户环境装
Tavotto / `sitecustomize` / 第二套 manifest·override·export / 静默
safe→native 或 native→safe fallback / native 模式自动 pip install /
源码写回 / 原始产物写回 / 未经同意决定就扩遥测事件表 / 宣称 Stable。

### 14.1 native 会话**不许**自动切到项目 `.venv`

ADR 0018 的「缺依赖就接手项目自己的 `.venv`」是 safe 档的解药，在 native 档
是**反的**：用户已经亲手指定了解释器，Tavotto 替他换一个等于把这个模式最核心
的那句承诺（"跑的是你自己的 Python"）当场违掉，而且不会有任何提示。

今天这条路不可达——`should_try_project_env()` 只认 `missing_dependency`，而
native 侧的错误码里没有它（bridge_runner 产的是 `script_error` /
`non_finite_response`）。所以 `_switched_to_project_env()` 里那个对
`NativeSession` 会 AttributeError 的 `worker.script_name` **不是缺陷，也不加
防御性分支**：加一个今天没人走的分支，只会在没人验证的路径上慢慢腐烂。

写在这里是因为它会变：哪天 native 侧真的产生了 `missing_dependency`，那条路
就活了。**那时该做的不是切环境，是明确拒绝**并把缺的包名告诉用户——让他自己
决定往自己的环境里装什么。

## 15. 遥测与诊断

**本轮不新增 telemetry event。** 采集范围变化要升 `CONSENT_VERSION` 并让所有
人重新表态（与 Session 7B 同一笔账）。兼容率数据由 Session 10 Observatory 收。

诊断包（脱敏后）记：native session state / execution profile / Python
major.minor / Matplotlib 版本 / target kind / argv **数量** / cwd 哈希 /
interpreter 来源与脱敏路径 / barrier reason / figure 数 / relay state /
disconnect reason / exit code / last event sequence / permission remembered。
**不记**：token / 完整环境 / secret argv 值 / package index URL / 用户 stdin /
源代码 / Figure 里的文字。
