# ADR 0014：Safe 与 Native 两档执行 Profile

状态：**Accepted**（Session 1 草案 2026-08-25；机制由
[ADR 0020](0020-native-matplotlib-bridge.md) 定稿，产品面由
[ADR 0021](0021-tavotto-run-product-contract.md) 定稿，2026-08-28）。
本文件的两档 profile 定义（§2）与「为什么不 pickle」（§6）是仍然有效的那部分，
0020 / 0021 都引用它。**§7 的四个待定稿事项现已全部裁决**（见文末表）。
相关：[Compatibility Bridge 总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)、
[事实审计](../compatibility/compatibility-bridge-audit.md)、
[Pylustrator 研究](../compatibility/pylustrator-study.md)、
[0013 Runtime Figure Assets](0013-runtime-figure-assets.md)、
[0009 Codex workspace root authority](0009-codex-workspace-root-authority.md)。

## 背景

当前只有一档执行语义（本 ADR 命名为 **safe**）：Tavotto 挑解释器
（`pool._prioritized_candidates()` 五级优先）、cwd 切沙盒、argv 换成脚本
自身、savefig 被吞掉、相对路径只读回退逐条修补。它对"脚本 + 数据在一个
图库目录里"的项目工作得很好，但复杂老项目的真实运行方式是
`conda activate myenv && python -m figures.fig3 --dataset run7`——解释器、
cwd、argv、env、module invocation 每一项都与 safe worker 不同，逐条在
沙盒里修补是打不完的地鼠（Pylustrator 研究 §3 的结论）。

## 决策（草案）

### 0. 统一 ExecutionSpec（Session 2 已落地，safe 先行）

> **落地记录（2026-08-25，Session 2）**：实现为 `engine/execspec.py`。
> `safe_spec()` 是 safe 档默认值的唯一权威构造函数；worker argv 唯一出处
> `worker_argv()`，`EngineWorker.__init__` 与 `_spawn_spec()` 两条 spawn
> 路径都改为它的消费者（行为逐字节不变，`test_workerd_pool.py` 对拍 +
> `test_execspec.py` golden argv 看护）。`env` 字段只存**增量**（bundled
> runtime 的 `child_env(base={})` 注入项），绝不序列化整份父进程环境；
> 两条控制面怎么把增量落成子进程环境仍是 pool 的机制细节（EngineWorker
> 全量 `child_env()`、workerd 只传增量——与落地前的行为一致）。
> `stable_payload()` 是跨机器稳定字段的子集（fingerprint / 未来持久化
> 只准用这一档）。native 仍未实现，`worker_argv` 对 native 显式拒绝。

所有"跑一个脚本"的入口统一经过一个不可变描述（字段名可按仓库风格调整）：

```python
@dataclass(frozen=True)
class ExecutionSpec:
    profile: Literal["safe", "native"]
    interpreter: str                    # 解释器绝对路径
    target_kind: Literal["script", "module"]
    target: str                         # 脚本路径 或 module 名
    entry: str | None                   # safe 的入口函数；native 恒 None
    argv: tuple[str, ...]               # 脚本看到的 sys.argv[1:]
    cwd: str
    env: Mapping[str, str] | None       # None = 原样继承
    project_root: str
    passthrough_savefig: bool
```

当前 `EngineWorker.__init__` 与 `_spawn_spec()` 两处手拼 argv 的地方是它的
第一批消费者。**Python 池与 workerd 的行为一个字节不变**——这是重构不是
改语义。

### 2. 两档 profile 的诚实定义

| 维度 | safe（现状语义，收进 spec） | native（新增，`tavotto run`） |
|---|---|---|
| 解释器 | Tavotto 挑（五级优先，唯一出处不变） | **用户 invocation 里那一个**，绝不静默替换；不存在即报错 |
| target | script + entry（注册表/探测的 entry 方言） | `python file.py` / `python -m pkg.mod`，无 entry 概念 |
| cwd | 沙盒（写入边界） | **用户的 cwd 原样** |
| argv | `[脚本自身]`（隔离 worker 参数） | **原样**（含用户的 flags） |
| env | bundled 时 `child_env()` 清洗；否则继承 Flask 环境 | **原样继承用户 shell 环境**（仅追加捕获通道所需的最小变量，逐个列名并写进文档） |
| savefig | 吞掉（不写盘）+ 捕获 | **透传**（照常写文件，与用户原命令等效）+ 捕获 |
| 相对路径回退 | 有（只读） | 无需（cwd 本来就对） |
| 写入/删除守卫 | 有（unlink/write_text 守卫） | **无**。脚本拥有当前用户的全部权限 |
| 文案承诺 | "Tavotto 控制执行、不碰你的真实目录" | "与你自己在终端里运行这条命令完全等同" |

**两档的文案必须与机制逐条一致**：native 绝不声称沙盒或只读；safe 绝不
声称"和原来一模一样"。

### 3. 捕获通道（native）

native 不能要求用户改脚本（对照 Pylustrator 的 `import pylustrator`），
捕获由 Tavotto 注入的驱动层完成（候选机制：驱动脚本包装
`runpy.run_path/run_module`，或 `sitecustomize`/`PYTHONSTARTUP` 注入——
Session 7 比较后定稿）。约束：

- 注入只做三件事：拦截 `Figure.savefig`（**记录并透传**）、脚本结束后按
  figcapture 收 pyplot 存活 Figure、起协议通道；
- **capture 语义仍是 figcapture 那一份**（stem 取法、去重、上限、编号）；
- 脚本可见的行为差异必须≈0：argv/cwd/env/`__name__ == "__main__"` 全部
  保持；注入自身对 `sys.modules` 的痕迹要有清单。

### 4. 生命周期

- native 会话由一次显式 `tavotto run`（或 UI 等价动作）创建；协议、超时、
  kill-重建纪律与现有 pool 相同（build 超时档照旧适用——native 不等于
  不设防的挂死）；
- 进程退出时 native worker 一并收掉（`shutdown_all(wait=True)` 同路）；
- 捕获成功后的编辑/重放/导出与 safe 会话走同一条 pool 语义；重放 = 按
  同一 ExecutionSpec 重跑（写回 verify 的 one_shot 同理带 spec）。

### 5. 安全确认

> **Session 9 的修正**：下面这条草案说"CLI 侧不设阻断式确认"。实际裁决是
> **设**——桌面上一次确认，而且**在 spawn 用户 Python 之前**（ADR 0021 §7）。
> 理由是草案没想到的那一半：CLI 与 UI 不是两个入口，而是同一条流程的两段，
> 而 native 与 safe 的权限差异大到值得让用户看一眼"具体是哪个解释器、哪个
> 目录、哪个目标"。顺序更重要——反过来做（先跑起来再找 UI）的表现是脚本
> 已经写了文件、跑了半小时，然后 Tavotto 才说"没有桌面应用"。

- **CLI**：用户亲手敲 `tavotto run -- python fig.py` 是发起，不是全部授权；
  第一次对某个（项目 × 解释器）组合仍在桌面上确认一次（可勾"记住"）。
- **UI**：native 运行入口必须有一次显式确认，写明解释器路径、cwd、
  "拥有你当前用户的全部权限"；每项目记住选择（可撤销），不做全局默认。
- **MCP**：**模型给的路径/命令不是授权**（沿 ADR 0009 的 fail-closed
  纪律）。native run 经 MCP 必须走 elicitation 且默认 false；拒绝/超时
  一律不执行。v1 甚至可以不给 MCP native 工具——Session 7 裁决。

### 6. 为什么不 pickle Figure

- Figure 不可靠地 picklable（闭包 callback、开放文件句柄、后端画布、
  用户自定义 Artist 子类），失败面不可枚举；
- pickle 跨解释器/跨 matplotlib 版本是未定义行为，而 native 的意义恰恰是
  "用用户自己的（任意版本的）环境"；
- 架构原则 2：Figure 留在创建它的进程里，跨进程只走控制命令 + manifest +
  SVG/PNG/PDF——现有 worker 协议已经是这个形状，native 只是换了 spawn 方式。

### 7. 为什么 v1 只支持 Python invocation

- `python file.py` 与 `python -m pkg.mod`（含指定 venv/conda 的解释器路径）
  覆盖了科研项目的绝对主流运行方式；
- 任意 shell 命令（管道、env 前缀、Makefile、bash 包装脚本）的解析与
  注入面是无界的，一旦支持就要对每种形态回答"捕获层注得进去吗、
  argv/env 语义还原了吗"——做不到就会变成静默半支持；
- shell command 注入在总纲"长期增强"清单里单独排期，不进 PR 2。

## 不做的事

- 不在 native 里重写解释器探测/渲染/manifest（Rust 与 Python 的
  机制/策略分界照旧）；
- 不把 safe 的守卫悄悄搬进 native（那是假 native），也不把 native 的
  权限悄悄给 safe；
- 不自动升级：safe 失败绝不静默改跑 native。

## 待定稿事项（裁决记录）

| # | 问题 | 裁决 | 出处 |
|---|---|---|---|
| 1 | 捕获注入机制选型（驱动脚本 vs sitecustomize）与 Windows 差异 | **BRIDGE_RUNNER_SELECTED**——由 A/B 实测差异支撑（sitecustomize 的坑位在 Homebrew Python 上早就有人占着，直白实现会让用户环境里的 matplotlib 直接消失）。Windows 走同一条路径（loopback socket + argv 列表 spawn），**设计与用例就绪、真机未跑** | ADR 0020 §2 / §12 |
| 2 | MCP 是否提供 native 工具 | **v1 不提供**（模型给的路径/命令不是授权，沿 ADR 0009 的 fail-closed 纪律） | ADR 0020 §10 |
| 3 | invocation parser 的错误分类与稳定错误码表 | **推迟到 Session 9**（产品面）。spike 只认 `python 文件.py` / `python -m 模块`，其余显式拒绝 | ADR 0020 §10 / §13 |
| 4 | native 会话是否进池复用 | **不进池**（Session 9 裁决）。池的每一条语义对它都是错的：LRU 淘汰 = 杀掉用户正在跑的脚本；`unknown_session → reopen` = 重跑用户的一次具体 invocation；key 也对不上（native 的身份还含 cwd/argv/env/解释器）。独立 `NativeSessionRegistry`，但对上层是 Worker-like 接口 | ADR 0021 §5 |
| 5 | native 会话与 pip 安装的互斥（rebase 到 #177 之后浮出来的） | **抽出唯一的环境租约表 `engine/envlease.py`**，safe worker / native 会话 / 安装三方共用。有活跃 native 会话时**拒绝安装**（`environment_in_use_by_native_session`），**绝不自动杀用户的脚本** | ADR 0021 §6 |
| 6 | `tavotto run` 的 CLI 契约、UI 确认、是否记住许可 | **已定稿**（Beta）：`--` 强制、CLI 拥有用户 Python、确认之前一行代码都没跑、许可绑定（项目 × 解释器 × schema） | ADR 0021 §2 / §7 |

另有两条本 ADR 起草时没想到、由 spike 定下的机制：

- **控制通道不能走 stdin/stdout**（那是用户程序的语义）→ loopback + 一次性
  token，**协议信封零改动**（ADR 0020 §6）；
- **Figure 归主线程**，native 侧不起后台线程 + `LiveFigureSession` 的线程
  身份断言（ADR 0020 §7）。
