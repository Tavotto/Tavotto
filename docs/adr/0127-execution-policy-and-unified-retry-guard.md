# ADR 0127：执行策略与统一重试闸门——桌面用户发起的运行里，safe 被自己的守卫拦下后同会话至多回退 native 一次

日期：2026-10-14 · 状态：**Accepted**（兼容性改造 V2 PR1；用户 2026-10-14 裁决 D1–D4；叠在 #1022 / ADR 0126 之上）
相关：[0124 一次运行意图](0124-run-intent-continues-preparation-to-one-execution.md)（修订其「执行恰一次」）、
[0125 桌面拥有的 native 进程](0125-desktop-owned-native-process.md)、[0126 桌面拥有的脚本入口](0126-desktop-owned-script-input-and-run-entry.md)（关闭其「何时发 `execution:"native"`」的开口）、
[0014 native 模式](0014-safe-native-execution-profiles.md) §5、[0021 tavotto run 控制面](0021-tavotto-run-product-contract.md) §9.1 / §14、[0003 worker 协议](0003-worker-protocol-v1.md) 错误码表、
[0107 系统解释器静默采用](0107-silent-system-interpreter-adoption-and-native-workdir.md)（「可追溯记录」的先例）；规则全文 `docs/rules/backend/preparation-and-receipts.md`「执行回退（V2）」。

## 问题

safe 的守卫（P01a，`safe_file_operation_blocked`）会在脚本对真实图库做 `os.replace` / `shutil.move` / `write_text` / 被拦截的 `savefig` 之后又**观察**这些路径时，
把这次运行报成「结果可能与直接 Python 不同」。P06 / P07（#1022）让桌面能 spawn 并拥有用户的 Python，保留真实文件语义，但**没有任何人决定何时用它**：
ADR 0126 把「何时发 `execution:"native"`」留给调用方。结果是用户在桌面上点「打开并运行」，得到一条「请去终端用 `tavotto run`」的死胡同，而桌面本来就能跑。

同时，重试散落在五处互不通气的闸门里（`deprepair` 三轮修复、`projectenv` 每 (项目, 脚本) 一次环境切换、`RunIntent` 的步数、`app._engine_attempt` 的 `range(2)`、
`pool._build_with` 的单次重取），没有一处记「根因」，也没有一处能回答「这次运行总共真执行了几次、每次因为什么」。再加一条 safe→native 的回退而不统一，就是第六个闸门。

## 决策（用户 2026-10-14 裁决 D1–D4）

### D1：桌面用户发起的运行里，按执行证据静默 Safe→Native 回退一次

* **触发**：同一准备会话里，一次 safe 尝试以**根因 `blocked`**（稳定码 `safe_file_operation_blocked`；savefig 衍生的 `FileNotFoundError` 同码）收场。
  其它原因——缺包、缺参数、缺数据路径、脚本自己的异常（含除零）、超时、会话崩溃、没有图——**一律不回退**：换执行语义不会变好，应当去解决真正的原因（D2）。
* **同一会话的第二个 attempt**：不新建会话、不改前端、不多开标签页。第二次尝试用 #1022 的 `_native_preparation_runner`（`execution=native`），解释器**沿用 safe 当时解出的那一个**
  （解出的不一致就不回退，`interpreter_changed`）。回退在 safe 尝试的收尾**同一把会话锁里**接上，读者不会看到「第一次已结束、第二次还没开始」。
* **单向、至多一次**：native 失败就报 native 自己的真实错误，**不回 safe**；一次用户发起的运行（`run` 动作认领）里执行语义最多变一次。用户再点一次运行是新的运行，预算重算。
* **只在桌面用户发起的运行里**——一个**服务端可验证**的事实，不信请求体：进程是 `--desktop-sidecar`（`app.config["TAVOTTO_DESKTOP_MODE"]` 只由 `SidecarServer` 设置）、会话状态里没有本机凭据（`api_secret is None`，桌面模式磁盘上不存在任何凭据）、且该请求带着由启动 nonce 兑换的会话 cookie（`app._desktop_initiated()`）。浏览器模式、带本机凭据头的 API 客户端（MCP / Agent / 脚本）、无认证入口（测试 / `--insecure-no-auth`）一律不是，`decide_fallback` 默认 fail closed（`not_desktop_initiated`）。`run_when_ready` 在创建意图时记下来源，之后自动推进的执行沿用。回退只发生在会话 `run` / `run_when_ready` 认领出来的尝试上。GET / SSE / recheck / 打开项目 / 缩略图不执行用户代码（不变）；
  CLI `tavotto run`（终端拥有进程，`runcli` 不 import 本策略）、MCP 工具（无 `native_run`、无 `execution` 参数）、旧的 `/api/engine/preparation` 端点不触发回退（有看护用例）。
* **重跑让位**：会话里 safe 尝试开始前，同脚本上一次桌面 native 会话与更早的计划票据先让位（复用 #1022 的 `REGISTRY.cancel_project(script=)` 与 `PreparationService.cancel_project(script=, keep=)`），否则回退成功后停在屏障上的 native 进程会一直占着环境租约。
* **取消与过期**：取消落在回退之前（含锁外规划与锁内交接之间的窗口）不再起 native；检查之后源码 / 解释器 / 授权变了（`preparation_plan_stale`）不回退。取消动作落在**最新**的那次尝试上，不是动作当初指着的那次。
* **被取代**：回退计划登记之后再核一次是否已有更新的同脚本**运行计划**（登记顺序）；只数脚本目标，旧端点按素材做准备（资产目标）不算替代运行。不成立就取消刚登记的回退计划、记 `superseded`（或 `cancelled`）。
* **启动认领与被取代判定同锁**：native 起进程闸门（`reserve` / `begin_start` / `check_start`，回退与普通桌面 native 运行共用）问的是 `PreparationService.launch_blocked`——与 `register` 同一把锁里判「已取消，或已有登记更晚的同脚本脚本目标计划」。更新的重跑「已登记、还没轮到自己的 `cancel_project(keep=)`」时旧计划起不了进程；闸门放行之后才登记的重跑走 `cancel_project(keep=)` 的真终止路径。
* **不弹确认框**：见下「授权来源」。
* **外部副作用（场景 17）**：safe 档在被拦之前可能已产生外部副作用（网络、计数文件）；接受回退时这些副作用**重复一次**。这是用户裁决，不是疏漏——见「后果」。

### D2：回退是「兼容性优先」里的最后一步，不是第一步

缺包 / 参数 / 路径类原因先解决真正的原因（既有的依赖准备、参数表单、数据指认），解决后再运行走同一套判断。路由器（运行前预判「最可能成功的方式」）见 [0133](0133-run-routing-compatibility-first.md)；本 ADR 只负责**运行后**的证据回退与它的闸门。

### D3：解释器 · 执行语义 · 启动上下文三维独立

账本里每次尝试记三个摘要：执行语义（`profile`）、解释器身份（路径的 12 位摘要，不可还原）、启动上下文（入口 + 运行配置引用 + 公开 `launch_context` 的摘要）。
「与上次相比变了什么」只在这三维里取（`profile` / `interpreter` / `launch_context`），闭集。选了用户的 Python 不会让 safe 自动变成 native（ADR 0125 不变）。

### D4：统一重试闸门与账本

新增唯一裁决处 `engine/execpolicy.py`（纯逻辑、纯标准库）：

* `ExecutionPolicy`：项目设置键 `execution_policy`（写法同 `workdir`），`compatible`（缺省）/ `safe_only`（用户明确只要 safe：任何证据都只显示，不回退）；坏值按缺省。
  运维开关 `TAVOTTO_EXECUTION_FALLBACK=0`（环境变量优先，其次全局设置 `worker.execution_fallback`，写法同 `TAVOTTO_ENV_ADOPTION`）整个关掉回退。
* 根因**不另造枚举**：就是已有的稳定错误码；`cause_of()` 只把码归到 `blocked` / `cancelled` / `stale` / `other`，`may_fall_back()` 只对 `blocked` 为真。
* `AttemptLedger`（挂在 `Session`）：每次真实尝试一条 `AttemptRecord`——执行语义、解释器 / 启动上下文摘要、触发它的原因码、与上次相比变了什么、结局、（本可回退却没回退时的）闭集理由。
  **不含**路径、argv 原文、env 值、脚本内容。有界（16）、线程安全；按 run 分组，新的用户运行重算预算。
* `RetryGuard`：native 之后不回 safe；一次 run 里 native 回退至多一次；**同一份执行身份 + 同一个根因不重复**（`no_new_root_cause`）。`decide_fallback()` 把「开关 / 策略 / 取消 / 过期 / 原因 / 闸门」按固定优先级过一遍，纯函数，回闭集理由码。
* 诊断：会话报告多出 `attempt_history`；任务诊断快照（`taskdiag`）的 native 尝试多出 `retry:{of, cause, changed, execution}`，只用闭集码与 plan id。
* 本 PR **不**把另外五处闸门接进账本（它们各有现成的有界性与看护，且分属 #1014 / #1019 在改的文件）；统一的是新增的这一条重试，以及今后任何新增重试必须过的同一个门。其余接入列为后续（见 PR 正文）。

### 授权来源（关闭 ADR 0014 §5 / 0021 §7 对桌面用户发起运行的 native 确认框）

0014 §5 要求 native 运行入口有一次显式确认（写明解释器、cwd、全部权限）。对**桌面用户主动发起的运行**，本 ADR 裁定：授权来自 **用户的主动「运行 / 打开并运行」动作 + 项目的执行策略（缺省 `compatible`）**，
范围限于「这个项目 × 这个脚本 × safe 当时解出的那个解释器」，且在诊断里可见（`attempt_history` 的 `profile` / `cause` / `changed`）；用户在项目设置里选 `safe_only`（或运维设 kill switch）即撤回。
CLI 与 MCP 的确认合同**不变**：`tavotto run` 仍由用户亲手敲，许可仍绑（项目 × 解释器 × schema）；模型给的路径 / 命令仍不是授权；不新增 `native_run` MCP 工具。
`nativeperm`（「记住此项目和此 Python」）**不**复用为桌面回退的授权存储——避免 `native_run_permissions` 被误读成「CLI 已确认」。

## 对既有 ADR 的修订

| ADR | 修订 |
| --- | --- |
| 0124 §六 / 标题「执行恰一次」 | 意图名下的**首次执行**仍恰一次；同一会话按本 ADR 的回退条款可有**第二次尝试**，它不计入 `RUN_INTENT_MAX_STEPS`（仍是「一次安装 + 一次执行」），记在 `intent.attempts`。EX13（执行后缺包要用户另点）不变：回退不是缺包补齐，二者互斥（`blocked` 才回退，`missing_dependency` 从不）。 |
| 0014 §5 安全确认 | 桌面用户发起的运行不弹 native 确认，授权来源如上；CLI / MCP 条款不变。 |
| 0021 §14「不做的事」 | 「静默 safe→native 或 native→safe fallback」改为：静默**反向**（native→safe）与**非桌面用户发起**的回退不做；桌面用户主动运行下，按执行证据、单向、至多一次、有明确原因、诊断留历史的 safe→native 回退除外（本 ADR）。§9.1（面板路由由 `execution_profile` 决定）、§1 / §7.2（CLI 拥有进程、无 MCP native 工具）、§14.1（native 不自动切 `.venv`）**不改**。 |
| 0003 错误码表 `safe_file_operation_blocked` 行 | worker 层不变（仍不自动重跑、不改脚本、不原地放行）；「重跑」由**会话 / 策略层**发起，不是 worker。桌面 adapter 已接入（P07），回退由本 ADR 的策略决定。 |
| 0126 残余 | 「何时发 `execution:"native"`」：发 native 的唯一调用方 = 本回退策略（+ 将来显式「用 Native 运行」入口，若产品要）；前端永不发送 `execution`。 |

## 后果

* **用户可见**：桌面上点运行，脚本被 safe 的守卫拦下时不再是死胡同——同一个准备会话里自动换成保留文件语义的执行再跑一次，最终出图。native 路径的取舍如实存在：该图写回被拒（native 面板硬拒绝写回，ADR 0021 §9），脚本的文件写入是真的。
* **脚本最多执行 2 次**。safe 那一次在被拦之前的外部副作用会重复；这是 D1 的已知代价（场景 17）。
* 新稳定行为：`safe_only` / kill switch 下 safe 的限制照旧如实报告，`attempt_history[0].declined` 说明为什么没回退。
* 界面接线（V2 PR4）：素材库 ▶ / 接入中心 / 引导卡「打开并运行」是明确入口，检查完（卡片会说「可以运行了」时）代用户认领 `run_when_ready`，前端**永不发 `execution`**；回退是同一会话里的第二个 attempt，
  面板身份（资产 id）不含执行语义所以不多开标签页；桌面拥有的 native 会话由准备会话独占展示面；`file_ops_blocked` 只剩「后端明确没回退」一种成因，一句话按 `declined` 闭集码说原因、没有主按钮，
  回退也失败时一句话 + 再试一次、`attempt_history` 在详情「运行记录」。规则全文 `docs/rules/frontend/readiness-and-left-shell.md`「准备引导卡」。
* 未做（如实）：项目设置的读写端点与界面开关（改 `app.py` + 前端，列后续）；磁盘面板跟随权威 profile、旧 safe 热 worker 让位（PR3b）；运行前路由（PR2）；资产目标（已有图）的回退（本轮只覆盖脚本目标）；`worker.py` 里「没有换环境重跑」的过期文案与 `errors.json`（PR3a 持有）。

## 修订（显式 native 的来源闸门）

D1 的授权边界（桌面用户发起）原本只拦「路由选的 native」与「回退到 native」，请求体显式写 `execution: "native"` 的会话在任何已认证来源都能认领 `run`。修订：显式 native 与路由 native 走同一道 `_desktop_initiated()` 判据，非桌面来源**拒绝**（403 `native_requires_desktop`）而不降级——显式请求是客户端的明确意图，静默换成 safe 会误导；路由 native 仍按原样降 safe。拒绝点在创建会话端点（最早）与 `prepsession._claim_run`（认领时再核，含 `run_when_ready` 推进）。前端永不发 `execution`，CLI `tavotto run` 走 nativeperm，均不受影响。

## 修订 2026-10-30：浏览器模式的显式确认（#1500）

**起因**：反馈包 TVD-PEK5（pip + 浏览器模式）。论文脚本在 safe 下 `savefig` 并没有真正写盘，随后又 `Image.open(png_path)` 读回那个路径，safe 的守卫拦下（`safe_file_operation_blocked`，worker 文案「做了安全模式不会真正执行的文件操作，随后又读取了受影响的路径」）；D1 的授权边界只认桌面窗口，浏览器模式恒 `not_desktop_initiated`，不回退——
`fixrouter` 随后把用户送进 AI 修复，用户批准 5 次、绕了 6 分钟没修好，而这个脚本换成 native 一次就能跑。浏览器版用户成了「失败卡 → AI 修复」的稳定客户。

**裁决（维护者 2026-10-30，A + B）**：

* **A**：非桌面来源下 safe 被自身守卫拦下时，失败卡一句话说清原因（脚本要真的把文件写到磁盘上才能跑完），**不路由进 AI 修复**（`fixrouter` 里这条优先于 `ai_fix` / `connect_ai`）。
* **B**：同一张卡给一个主按钮「允许并运行」。用户在页面上点一次 = 授权**这一次**运行改以 native 重跑。每次确认、不记住（不写 `nativeperm`、不改项目 `execution_policy`）、至多一次，与桌面 D1 回退共用 `RetryGuard` 账本。
  项目设了「只用安全运行」(`safe_only`)、运维开关 `TAVOTTO_EXECUTION_FALLBACK=0`、本次运行已经回退过一次——都不给按钮，点也不执行。

**授权来源（服务端核得到的「人在页面上点了」）**：D1 的「桌面窗口」是核得到的事实；浏览器模式的对应事实是**会话 cookie**（由启动 nonce 兑换、HttpOnly，页面脚本与本机凭据客户端都读不到它的值）。
动作 `confirm_native_run` 的认领同时要求两件事，缺一个都是零执行的 403：

1. 请求靠**会话 cookie** 认证，且**不带**本机凭据头（`session_client.AUTH_HEADER`）——`app._page_authenticated()`，无会话状态（测试 / `--insecure-no-auth`）一律 False（fail closed）。
   错误码 `native_confirm_requires_page`。MCP、`tavotto run`、AI 修复工具链（`engine/fixtools/`）用的都是后者，过不了这一关。
2. 请求回显的 `confirm_token` 对得上服务端为**那一次失败尝试**签发的那一枚：`secrets.token_urlsafe(32)`，随 `confirm_native_run` 动作一起在失败尝试收尾后生成，绑定尝试 id（`bound_attempt`），
   常量时间比较（`hmac.compare_digest`）；**只随页面 cookie 认证的报告交出**（本机凭据头读到的报告里既没有这个动作也没有 token；不进诊断包、不进 SSE 提示）；验证通过的那一刻即作废（成败都不能再用，重放同一动作 = 幂等的 200 `claimed:false`，无新执行）；
   猜错 token 不烧掉真 token（否则任何人能让用户的按钮失效）；用户重跑出了新的失败尝试，旧动作连同 token 作废（新 token）。
   不信请求体里任何自报的「我是用户」字段（请求体白名单之外的键 400）。

通过之后走**与桌面回退同一套**规划与最后一刻复核（`prepsession._plan_fallback` / `_start_fallback`，参数 `confirmed=True`）：`execpolicy.decide_fallback(user_confirmed=True)` **只顶替第一道来源判据**
（`not_desktop_initiated`），开关 / 策略 / 取消 / 过期 / 原因 / 账本闸门一道不少。native 尝试是同一会话的第二个 attempt，与 D1 一样：解释器沿用 safe 当时那一个、单向、同一次运行至多一次（`already_fell_back`）。
给不给按钮（`_native_confirm_offered`）的判据是账本里该尝试的「没回退」理由**恰是** `not_desktop_initiated`——桌面来源的其它否决理由（`policy_safe_only` / `fallback_disabled` / `cancelled`…）不在此列。

**威胁模型**：这道闸防的是**模型给的参数 / MCP / AI 修复工具链**，不是同用户的本地进程。持有本机凭据文件（0600，浏览器模式）的进程本来就有同用户权限，可以经 `POST /api/session/relaunch` 换一枚 nonce、
兑换出 cookie 再点按钮；它读用户任何文件、起任何进程都不需要 Tavotto 的许可。这一点与 ADR 0008 的威胁模型一致（「能读这份文件的进程本来就能读用户的任何文件」），不在本修订要防的范围里。

**不变的**：

* 显式 `execution: "native"` 的非桌面来源仍 403 `native_requires_desktop`（上一节修订）；本修订是**唯一的例外**，且只在有效确认之后、经 `confirm_native_run` 一条路径。
* CLI `tavotto run`（终端拥有进程，确认合同不变）、MCP 工具（无 `native_run`、无 `execution` 参数）、旧的 `/api/engine/preparation` 端点不触发回退也走不到这个确认；AI 修复的 `switch_execution` 只改项目策略、不运行、不替用户确认（有看护用例）。
* 不写 `nativeperm`（「记住此项目和此 Python」）、不改 `execution_policy`：每次运行被拦都要再点一次。
* 桌面来源仍是静默回退（D1），卡片不变。

**界面**：失败卡一句话「这个脚本要真的把文件写到磁盘上才能跑完」+ 一个主按钮「允许并运行」，不出现 safe / native / 执行器 / 沙盒这些词，不摆 AI 次按钮。remedy 复用 `switch_native`，把 `silent` 从恒 `true` 放宽为布尔：
`silent: false` = 「等用户点一次」。不新增 kind，同源对（`fixrouter.REMEDY_KINDS` ↔ `lib/fixRemedy.ts`）字段表不变，只多一条 golden 向量。

**看护**：`tests/test_native_confirm_browser.py`（来源 / token / 一次性 / 绑定尝试 / 闸门 / 其它入口走不到）、`tests/golden/fix_remedy_vectors.json`、`web/src/components/PreparationCard.test.tsx`「浏览器模式：被拦下后给一次确认」。

### 同一修订：先在 safe 下换入口，再谈 native（#1500 追加）

**现象**：同一个脚本走旧 `POST /api/registry/probe`（素材栏）时，`entry=__main__` 被 `safe_file_operation_blocked` 拦下后，`probe.entry_retry_allowed` 允许换入口，`entry=draw_figure`（只建图、不进 `save()`）在 safe 下就出图；
准备会话（「打开并运行」）没有这一步，直接失败，把本不需要 native 的脚本推向 native / 确认卡。

**决定**：准备会话里 safe 尝试以 `safe_file_operation_blocked` 收场后，**先留在 safe**，按 `probe.entry_candidates(..., static_only=True)` 换入口再跑一次（`prepsession._plan_entry_retry`）：

* 候选与「值不值得换」只复用 probe 那一份（`entry_candidates` / `entry_retry_allowed`）；`static_only` 是同一函数的一个开关：只取**静态能解析出**的出图入口，脚本解析不了就空表，**绝不盲试**（`FALLBACK_ENTRIES` 只留给素材栏的旧行为）。去掉本次运行已试过的入口，取第一个。
* 换入口成功就是这次运行成功：照常 `finalize` 登记、上画布，与正常成功同一条路。
* 换入口仍失败，才走原来的分支：桌面窗口发起 → D1 静默回退 native；浏览器 → 本修订上一节的确认卡（给不给按钮看的是**最后一次**尝试）。
* 每次运行至多换一次入口（`Session.entry_retry_run`），与 native 回退共用 `RetryGuard` 账本；新增总上限 `execpolicy.MAX_ATTEMPTS_PER_RUN = 3`（首次 safe + 至多一次换入口 + 至多一次 native），超了记 `attempt_cap`。换入口不改变执行语义，故不受「只用安全运行」/ 运维回退开关 / 来源约束；
  账本 `changed` 里是 `launch_context`（入口进启动上下文摘要），计划带 `retry_of` / `retry_cause` / `retry_changed`，exec trace 与任务诊断由此看得出「换入口后成功」。
* **代价**：脚本在一次运行里最多被执行 3 次（原来是 2 次）；safe 档在被拦之前的外部副作用会重复，沿用 D1「场景 17」的裁决与说明。

**看护**：`tests/test_entry_retry_session.py`（浏览器 / 桌面 / 无认证三种来源换入口成功、换入口仍被拦后桌面 native 与浏览器确认卡、没有静态可解析入口时走原分支且不盲试、总次数上限）。
