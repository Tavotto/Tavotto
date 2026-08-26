# Tavotto Compatibility Bridge — 总实施纲领

> 来源：外部实施包 `tavotto-compatibility-bridge-prompts/01_MASTER_PLAN.md`，
> Session 1（2026-08-25）落库。本文件是全项目**不可违反的约束**；各 Session 的
> 进度与交接在同目录 `COMPATIBILITY_BRIDGE_HANDOFF.md`，事实审计在
> `compatibility-bridge-audit.md`。与本纲领相关的 ADR：
> [0013 Runtime Figure Assets](../adr/0013-runtime-figure-assets.md)、
> [0014 Safe/Native Execution Profiles](../adr/0014-safe-native-execution-profiles.md)
> （两份均为 Proposed 草案）。

## 总目标

让 Tavotto 从"只有符合特定文件组织与静态发现规则的项目能够编辑"，升级为：

> 只要一份可信的 Python 项目能够在某个可用 Python 环境中成功创建 Matplotlib
> `Figure`，Tavotto 就应存在一条明确、可解释、可恢复的产品路径将这些 Figure
> 捕获、打开、编辑、撤销、重放、组图、预检和导出。

本计划分两条兼容路径：

```text
安全导入 safe
  Tavotto 控制执行、保持写入隔离、主动 probe

原生运行 native
  使用用户指定的 Python、cwd、argv、env，行为等同用户原来的终端命令
```

自动静态发现仍然保留，但它只能是快速路径，不能成为"能否使用 Tavotto"的
唯一门槛。

---

# 一、已知问题模型

当前失败可能发生在不同层，必须分开诊断：

```text
1. 文件发现：没有列出脚本
2. 静态分析：解不出 entry 或 stem
3. 产品入口：引擎能 probe，但 UI / CLI 到不了
4. 运行环境：解释器、依赖、cwd、argv、env 不同
5. Figure 捕获：没有 savefig、动态输出、多 Figure
6. 素材模型：只有磁盘文件才是 Asset
7. 语义识别：Figure 打开了，但 Artist 不可编辑
8. 重放与导出：编辑可见，但不能稳定恢复或导出
```

不要把以上所有问题统称为"Matplotlib 兼容性"。每个失败都必须记录 stage 和
稳定错误码。

---

# 二、从 Pylustrator 学什么

Pylustrator 最值得学习的是：

- 在用户原本的 Matplotlib 生命周期中拦截 `plt.show()` / `savefig()`；
- 从 live `Figure` 而不是磁盘产物出发；
- 保留原解释器、cwd、argv、env 和本地 import；
- 对 Figure、Axes、Text、Line、Collection、Patch、Legend 建立可定位引用；
- 记录部分对象的创建位置和原始属性。

明确不采用：

- Qt GUI；
- 直接保存即修改源码的默认行为；
- Pylustrator 作为 fallback 引擎；
- 运行时依赖 PyQt5；
- pickle Figure 跨进程；
- 直接复制其 change tracker 或代码生成器。

采用 clean-room 研究，文档中记录来源与差异（见
`pylustrator-study.md`）。

---

# 三、不可违反的 Tavotto 架构原则

## 1. 只有一套 Figure 编辑语义

所有入口必须最终复用：

```text
capture
→ FigState
→ instrument
→ manifest
→ apply patches
→ render
→ replay
→ export
```

不得新增：

- 第二份 manifest builder；
- 第二份 override setter；
- 第二份 undo/redo；
- 第二份 writeback；
- 第二份浏览器捕获器；
- Pylustrator/Qt 编辑器。

## 2. Figure 留在创建它的进程中

不要 pickle 或 RPC 传输 Figure 对象。

Tavotto 可以把控制命令、manifest、SVG、PNG、PDF 和结构化元数据跨进程传递，
但 live Figure 本身必须留在 worker 中。

## 3. File Asset 与 Runtime Figure Asset 是两种正式类型

```text
AssetSource
├── FileAsset
└── RuntimeFigureAsset
```

Runtime Figure：

- 可以编辑、保存、重开、重放、组图和导出；
- 可以 materialize 为 Tavotto 管理的缓存 PDF/SVG；
- 没有原始 artifact 时，不得显示 artifact writeback；
- cache 不是用户原件；
- export 必须以当前权威 worker 渲染为准。

## 4. safe 与 native 必须诚实区分

### safe

- cwd 可切到沙盒；
- 相对路径只读 fallback；
- 保护真实项目写入与删除；
- 由 Tavotto 选择并启动 worker；
- 用户点击后才运行脚本。

### native

- 使用指定 Python；
- 保留原 cwd、argv、env、module invocation；
- 脚本拥有当前用户原本拥有的权限；
- 只对可信项目使用；
- 不声称沙盒或只读。

MCP/模型提供的路径不是 native 执行授权。

## 5. 不自动运行整个项目

项目打开阶段只扫描。任何脚本执行都必须来自明确用户行为：

- 点击"运行并发现图"；
- 执行 `tavotto open script.py`；
- 执行 `tavotto run ...`；
- 经 RootAuthority/elicitation 后的明确工具操作。

## 6. 产品入口必须纳入 CompatBench

"worker 能直接调用"不等于"真实用户能使用"。

CompatBench 至少区分：

```text
desktop_project
cli_open
safe_probe
browser_playground
native_run
```

只有真实产品路由通过，case 才能声明 full support。

---

# 四、核心数据模型方向

名称可按仓库实际风格调整，但语义必须唯一。

## ExecutionSpec

```python
@dataclass(frozen=True)
class ExecutionSpec:
    profile: Literal["safe", "native"]
    interpreter: str
    target_kind: Literal["script", "module"]
    target: str
    entry: str | None
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str] | None
    project_root: str
    passthrough_savefig: bool
```

所有运行脚本的入口使用它，不再各自拼 entry、cwd 和 argv。

## CapturedFigureDescriptor

```python
@dataclass(frozen=True)
class CapturedFigureDescriptor:
    asset_id: str
    script: str
    entry: str
    stem: str
    capture_source: Literal["savefig", "pyplot"]
    execution_profile: Literal["safe", "native"]
    original_artifact: str | None
    size_mm: tuple[float, float]
    source_fingerprint: str
    can_writeback_artifact: bool
    can_writeback_source: bool
```

`asset_id` 必须稳定，不能依赖 PID、临时目录、绝对路径或本次 session id。

---

# 五、两大主要交付

## PR 1：Compatibility Bridge — 安全导入产品入口

完成：

- 统一 execution/capture DTO；
- 所有合理项目内 `.py` 均可被列出；
- 任意项目内脚本均可主动 probe；
- 多 Figure 与 show-only 脚本可进入产品；
- Runtime Figure Asset；
- 素材库普通入口；
- `tavotto open script.py` 自动 safe probe；
- 产品路由 CompatBench；
- Windows/macOS 最终桌面 E2E。

## PR 2：Native Execution — `tavotto run`

完成：

- safe/native ADR；
- Python invocation parser；
- `python file.py`；
- `python -m package.module`；
- 指定 venv/Conda Python；
- 保留 cwd、argv、env、imports、matplotlibrc、style、字体；
- 桌面交接；
- 生命周期与清理；
- 安全确认；
- Windows/macOS 最终桌面 E2E。

---

# 六、完成定义

Compatibility Bridge 最终必须证明：

- 用户能打开没有 PDF、PNG、SVG、没有 `savefig()` 的绘图脚本；
- 任意项目内 `.py` 都能由用户主动试运行；
- Runtime Figure 是正式 Asset，不是假路径；
- 多 Figure 不静默丢失；
- Runtime Figure 可编辑、保存、重开、重放、组图、预检和导出；
- 没有原始 artifact 时，不出现 artifact writeback；
- `tavotto open script.py` 在静态发现失败时仍可 safe probe；
- `tavotto run` 保留原 Python/cwd/argv/env；
- safe 与 native 权限文案一致；
- 产品路由 CompatBench 不再绕过入口；
- 浏览器、桌面、MCP 不出现第二份语义；
- Windows 和 macOS 最终产物各有证据；
- 0 次静默源码损坏；
- 0 个不可恢复项目。

---

# 七、长期增强

以下单独排期，不与 PR 1/2 混合：

- 通用 Artist fallback；
- Source Hint；
- Copy as Matplotlib Code；
- Jupyter capture；
- 任意 shell command 注入；
- R/Julia 等非 Python 绘图环境。

不要为了显得完整而在前两轮提前扩大安全面。

---

# 附：与 1.0 收敛纪律的关系（Session 1 落库时的裁定）

仓库当前处于 1.0 稳定化阶段（`docs/1.0-release-readiness.md`：非
correctness / safety / **compatibility** / release blocker 禁止扩大产品能力）。
本计划属于其中 **compatibility** 一档的定向扩围：解决的是「原脚本能正常出图、
Tavotto 却打不开」这一类真实兼容缺陷（CompatBench 基线里
`shape_pyplot_show_only` 等 18 条 partial_support 的主要成因）。执行时仍受
收敛纪律约束——每个 Session 只动本轮根因，不趁机重写已稳定模块。
