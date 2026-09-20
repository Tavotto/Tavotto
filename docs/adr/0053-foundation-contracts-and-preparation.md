# ADR 0053：统一实施包 U01——共同合同、身份三分、异步准备接口与 case enrollment

日期：2026-09-20 · 状态：**Accepted**（U01 阶段；后续阶段按 §六 修订）

## 背景

统一实施包（`docs/implementation/tavotto-foundation/`）要同时把「可靠首开」与「可靠输出」
两条主线接到同一套合同上。U00 的清点（`U00_CAPABILITY_INVENTORY.md`）给出的事实是：

* 四类入口（HTTP / MCP / CLI / 桌面）到 `ExecutionSpec` 之间**没有共同的「准备」层**——
  环境证据、依赖意图、cwd / 数据绑定、授权分别散在 `pool.resolve_worker_python` /
  `depresolve` / `workdir` / 前端确认文案里；
* `ExecutionSpec.cwd_mode` 只有 `sandbox` / `project` 两档，且 project 档的 cwd 是
  `script.parent`（ADR 0047），FO-041 要求的三分（`script.parent` / `project.root` /
  `invocation.cwd`）没有表达位置；`PATCH /api/engine/workdir` 后端不记「谁授权过」；
* `depresolve.parse_requirements_text` 是安装路径的解析器，刻意窄：extras 剥掉、marker
  丢掉、同名冲突第一条静默胜出、`constraints.txt` 不读（`U00_BASELINE.md` §4.3 实测）；
* worker 不自报 `sys.prefix`——父进程只有解释器来源标签与 `?probe=1` 的 imports，
  「这次到底是哪个 venv 跑的」从路径字符串上猜不出（`.venv/bin/python` 是软链）；
* 「首开 / 导出穿过公共入口」没有任何一条产品用例；32 个首开场景在 registry 里全部
  `not_run`，而 CI 政策（`03_CI_POLICY.md` §3）要求它们的资格状态是**工程事实**而不是
  测试成绩。

本 ADR 定 U01 的四件事：合同放哪、身份怎么分、准备接口第一版长什么样、case enrollment
怎么记账。它**不改**任何既有不变量（§六），只写明后续阶段将怎样修订。

## 决策

### 一、合同放在既有模块旁，不另建 PublicationJob / 全局解释器链 / 安装锁

| 合同（04_ARCHITECTURE §2） | 落点 | 形态 |
|---|---|---|
| LaunchContext | `engine/execspec.py`：`launch_context(spec, grant=…)` | 从 `ExecutionSpec` **派生**的只读视图：`cwd_origin ∈ {sandbox, script.parent, project.root, invocation.cwd}`、`write_mode ∈ {sandboxed, project_dir, unrestricted}`、稳定字段、`grant`。`ExecutionSpec` 与 `worker_argv` 的 golden 一个字节不变 |
| grant（FO-047） | `engine/workdir.py`：`set_mode(project)` 记 `granted_at`，`grant_for()` 读 | 只记时刻不记人（本机单用户，记一个 `user` 字面量是假信息）；老设置「授予过但没记时刻」与「没授予」是两个答案 |
| DependencyIntent | `engine/depresolve.py`：`DependencyIntent` / `parse_intent` / `declared_intents` / `conflicts` | **第二个读法，不是第二个安装器**：name / specifier / extras / marker / group / source / kind 原样保留；看不懂的行是 `kind=unknown` 并保留原文（unknown 不是空依赖）；`constraints.txt` 读进来标 `constraint`；冲突列出不裁决。安装路径的窄语法（安全边界）一字不动 |
| ExecutionReceipt | `engine/receipt.py`（Flask 侧，纯标准库） | worker 自报（§三）+ 控制面账本（generation / `script_sha1` 即 source revision / 解释器来源标签 / spec 稳定字段 / LaunchContext / 描述符）；`completeness ∈ {complete, partial}`，老 worker 没自报是 partial，不补不猜 |
| SourceArtifact | `engine/figcapture.py`：`SourceArtifact` / `source_artifact_from_file` | `source_id`（runtime asset id 或素材相对路径）、`origin ∈ {execution, static}`、`kind`、`bytes_sha256`、`size_bytes`、`receipt_id` / `generation` / `patch_hash`（static 来源恒 None） |
| RenderPlan 引用 | `engine/exportreq.py`：`render_plan_ref(req, resources)` | 规范化 `ExportRequest` 的渲染语义 + 资源的语义身份 → `plan_identity`；字节 hash 单列 `input_bytes`。**只做引用与身份，不做渲染** |
| PreparationPlan / PreparationResult | `engine/preparation.py` | 计划（不可变）与观测（可变）分开，见 §四 |

`project.root` 这一档**今天没有任何生产者**（`test_project_root_origin_has_no_producer_today`
钉着）：占住枚举位是为了 U03 加「在项目根运行」时不被并进 `script.parent`，占位不等于实现。
Python 要求（脚本要哪个 minor）今天没有任何地方声明，计划里如实写 `python_requirement:
{declared: false}`。

### 二、三种身份，三个字段，三个问题（04 §3）

| | 回答 | 含机器路径 | 落点 |
|---|---|---|---|
| 私有失效键 `private_invalidation_key()` | 还是不是同一个环境 | **含**（executable / prefix / base_prefix / 项目根 / cwd）——区分两个 venv 正靠它们 | `ExecutionReceipt` |
| 公开语义身份 `public_identity()` | 这是哪种执行 | 不含；规范化意图 + 获准来源标签 + 版本号 + source revision | `ExecutionReceipt`；`SourceArtifact.semantic_identity()`；`render_plan_ref().plan_identity` |
| 最终文件 hash | 文件长什么样 | — | `SourceArtifact.bytes_sha256`；**不回写进任何语义身份** |

`receipt_id` = 公开身份 + 私有键 + generation 派生的不透明 id：同环境同脚本重建一代换一个 id；
两个项目里的同名脚本永远不同 id（FO-008）。默认 HTTP / MCP 投影不带机器路径，
`to_payload(include_private=True)` 给诊断包。

### 三、worker 自报：加字段，不升协议版

v1 `build` 响应新增 `runtime`（`figsession.runtime_report()`）：`python_version` /
`python_implementation` / `executable` / `prefix` / `base_prefix` / `platform` / `machine` /
`cwd`（`os.getcwd()`，脚本**真正**看到的目录）/ `argv0` / `packages`（闭集
`RECEIPT_PACKAGES`，`importlib.metadata` 读版本、不 import）。ADR 0003 §1：加字段不升版；
legacy 信封形状一字不改。**safe worker 与 native bridge 同一份实现**（`figsession`），
native 的 executable / cwd 因此成为「用户自己的解释器、用户自己的 cwd」两条承诺的可核对
证据。控制面把它记在 `last_build_runtime`（`WORKER_LIKE` 成员，两种 worker 与
`NativeSession` 都有）。

### 四、异步准备接口第一版

三个端点、一个登记表（`engine/preparation.SERVICE`），全部在会话认证之内（ADR 0008 的
guard 是全局 `before_request`，`test_browser_auth.py` 从 url_map 枚举自动覆盖）：

```text
POST /api/engine/preparation {id}          → 202 {plan, result}
GET  /api/engine/preparation/<plan_id>     → {plan, result} | 404 preparation_not_found
POST /api/engine/preparation/<plan_id>/cancel → {cancelling, plan, result}
```

状态闭集：`pending → running → ready | error | cancelled`，以及不起线程的
`static_source_available`（这张图没有脚本：FO-010，静态源可用时不被科学准备阻断）。
「现有 runtime 正在运行」不是状态，是 `existing_runtime` 这条事实（`pool.peek()` 只读）：
已 build 过的直接复用其回执（FO-031），正在冷启动的等它。执行只有一条路——
`pool.build()`（带一次项目环境自动 fallback），不另写 `get + ensure_built`。

取消的边界（D11 / FO-009）：接受时刻只有两个——线程还没碰 pool（一行用户代码不跑）、
build 返回那一刻。会话是本计划新起的才 `pool.force_cancel`；本来就在的（别的消费者的）
一根手指不碰，只是本计划不再等它。native 会话不在池里，永远碰不到。取消不承诺撤销
已发生的外部副作用，`note` 如实说。计划按 `project_id` 认领，别的项目查同一个
`plan_id` 是 404（FO-008）。

UI 与 MCP 消费同一份 `{plan, result}`；本阶段只做 HTTP + 明确投影，前端不接。

### 五、case enrollment：台账是工程事实，不是测试成绩（03 §3）

台账在 `docs/implementation/tavotto-foundation/enrollment.json`（机器可读真值）+
`ENROLLMENT.md`（派生，`tools/generate_enrollment.py`）。每个 case 一条：`case_id`、
`enrollment ∈ {planned, observing, enforced, later}`、`lane`、`stage`、`test`（enforced 才有）、
`scenario_refs`。规则：

* **registry 里的 32 个 FO 场景在台账里逐一出现，enrollment 与 registry 一致**；本阶段全部
  `planned`（FO14 `later`），一个都不进 pytest 默认发现、不进 required 矩阵；
* **enforced 的 case 必须指向一条真实存在的 pytest 用例**，该用例写一条结果记录
  （`tests/support/foundation_harness.py` 的 schema）；
* 预期实例集合在执行前由台账 + lane 生成，绑定源码 SHA / 平台 / runtime & 锁 / fixture
  身份 / 入口 / 能力版本；校验 = 预期集合 == 已提交有效结果集合（无重复、缺失、错 SHA、
  错产物），`product_outcome` 与 `test_verdict` 分开记；
* **空集合永远不是通过**：没有 enforced case 的 lane、或结果目录为空，校验一律红；
* 观察失败用清楚命名的独立任务（`observing`），不挂 required；本阶段只登记，不新建需要
  完整安装 VM 的 job。

U01 的 enforced case 只有一条：`U01-S1`（`single_file_csv` 夹具经真实 HTTP 服务 + 会话
认证 → 准备 → 渲染 → 旧后端导出 PDF/PNG → 独立读回），落点是 `invariants` job 的一步。

## 六、与既有规则的关系——本阶段不改，后续这样改

| 既有规则 | 本阶段 | 谁、怎么修订 |
|---|---|---|
| `pdfbackend/pymupdf_backend.py` 是全仓唯一 import pymupdf 的模块 | **继续成立**；U01 的导出终点是旧后端，harness 只读它的产物 | U02 / U06 的 ADR 在新核心落地时改为「发行闭包零 pymupdf」+ 退役扫描（D03），U10 切默认 |
| 1.0 收敛纪律（不扩产品能力、不重写稳定模块） | 新增的是**合同与只读投影**（LaunchContext 派生、receipt、preparation 接口），没有新的用户可见能力，默认路径一字不变 | 首开 / RenderCore 的产品能力按 06 的启用 / 切换纪律逐阶段开 |
| ADR 0047：project 档 cwd = `script.parent` | 语义不变，只是有了名字 | U03 若加「在项目根运行」，新增 `project.root` 的生产者与确认文案 |
| ADR 0003：协议 v1 加字段不升版 | `runtime` 是加字段 | 改语义 / 删字段才升版 |
| ADR 0008：会话认证不许被新端点绕过 | 三个新端点在 guard 之内 | 不修订 |
| ADR 0019：依赖修复的窄语法是安全边界 | 安装路径一字不动；DependencyIntent 只读 | U04 决定 marker 求值、extras 与 constraints 怎样进联合安装（不放宽语法） |
| ADR 0031：`ExportRequest` / `ExportJob` 权威 | `render_plan_ref` 只引用它们 | U06–U09 RenderCore 消费 RenderPlan；ArtifactManifest 的 plan / observed / policy 分开在 U08 |

## 后果

* 加了：`engine/receipt.py`、`engine/preparation.py`、`tests/support/foundation_harness.py`、
  台账两份、错误码 `preparation_not_found`（两种语言文案）；`WORKER_LIKE` 多一个成员。
* 没加：新的默认行为、新的用户可见能力、新的 required job、新的 trust 系统。
* 看护：`tests/test_execution_receipt.py`（模型 + 纯模型不拉科学栈）、
  `tests/test_preparation_api.py`（状态机 / 取消边界 / 项目绑定）、
  `tests/test_worker_runtime_report.py`（真 worker 自报与独立探针对拍）、
  `tests/bridge/test_bridge_e2e.py`（native 的 executable / cwd 自报）、
  `tests/test_foundation_harness.py`（台账 ↔ registry、校验器负例、`U01-S1` 真链路）。
