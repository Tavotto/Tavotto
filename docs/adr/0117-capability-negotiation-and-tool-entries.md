# ADR 0117：能力协商与工具入口——先问再发、MCP 与 GUI 同一份运行配置与采用服务、没有界面时立即给结构化待办

日期：2026-10-06 · 状态：**Accepted**（产品目标由「项目 Onboarding × 执行主链路收敛」包的 T10 给定）
相关：[0006 MCP](0006-codex-mcp-app-and-publication-profile.md)、[0069 工具结果预算](0069-canvas-payload-under-host-event-cap.md)、
[0078 MCP 会话落盘](0078-mcp-session-survives-server-process.md)、[0099 脚本 input](0099-script-input-bridge.md) §五、
[0105 远程实例窗口](0105-desktop-remote-instance-window.md)（`features` 标记的来历）、[0114 环境建议 / 检查 / 采用](0114-environment-recommend-check-adopt.md)、
[0116 准备会话](0116-preparation-session-as-gui-entry.md)；规则全文 `docs/rules/plugin/first-open-questions.md`、
`docs/rules/plugin/result-budget-and-session-state.md`、`docs/rules/backend/registry-discovery-and-probe.md`（argv 端点）

## 问题

T03 让运行入口接受精确 argv、T05 把环境采用改成「建议 + 用户确认」，但三条入口没跟上：

1. **新客户端连旧引擎**：旧端点不认识的 JSON 字段被静默忽略。新前端把 `argv` 发给旧引擎（远程实例 / 降级回滚），旧引擎照样无参数
   运行，回包看起来一切正常；插件（MCP）同样可能配着一份更早安装的 Tavotto（插件版本 ≠ 已装引擎版本）。HTTP 没有能力位。
2. **MCP 没有 argv 入口**：`tavotto_open_figure` 总是无参数——磁盘上的 `fig.pdf` 是用户在 GUI 里带参数那次写的，MCP 打开它画的却是
   无参数那一版（C19：两个入口两份数据）；同 stem 不同参数的会话会互相沿用（R02）。
3. **MCP 不能回答环境建议**：确认模式下只拿到一句缺包，自己点不了「使用它」，无头场景只能退回 `TAVOTTO_ENV_ADOPTION=auto`。
4. **没有界面时的「需要输入」只有一句话**：`script_needs_input` / `script_needs_arguments` 没有结构化载荷，Agent 不知道用哪个参数答、
   该不该替用户答；失败载荷（完整 traceback、几千条找不到的数据）没有体积上限，可能把宿主 1 MiB 的事件副本顶穿。

## 决策

### 一、能力标记：先问再发

`engine/capabilities.py` 是引擎宣告「这一版会什么」的**唯一**表（`script-argv` / `preparation-sessions` / `environment-adoption`，只增
不改名）。出口：`GET /api/version` 的 `features`（与 ADR 0105 的 `desktop-remote-window` 同一个列表）、MCP `tavotto_health` 的
`engine_features`。客户端只在表里有对应标记时才发新字段，没有就当场以 `engine_capability_missing` 拒绝——**一次都不先「试着无参数
跑」**。前端（`lib/api.ts` 的 `requireEngineFeature`：非空 argv 的试运行与准备会话）与插件（`bridge._require_feature`）各留一份名字
镜像（旧引擎上它们 import 不到引擎常量），`tests/test_engine_capabilities.py` 两侧对拍。旧客户端不读这张表：空 argv / 旧终局逐字节不变。

### 二、MCP 的运行配置：同一份登记、同一个判据

`tavotto_open_figure` 增 `argv`（字符串数组，一项一个 token）与 `run_config`（`rc_…` 不透明引用），二选一，批量不接受：

- 非空 `argv` 走 GUI 的同一个函数（`runconfig.selection_for`，`source="mcp"`）：同一 (脚本, argv) 两个入口得到**同一个**引用；
- `argv=[]` = 用户明确不带参数（旧行为，不读登记）；
- 都不给 = 沿用这个脚本最近一次在 Tavotto 里**明确运行**的配置（`runconfig.default_selection`，即 GUI 磁盘面板 `_disk_panel_run`
  的同一个判据）；没有就是无参数。只有用户在新版 GUI 里带参数跑过才会有默认，所以没有登记的旧场景行为不变；
- Agent 提交的 token **只是配置来源**：不改磁盘面板的默认（`set_default` 只记用户在界面里的明确运行）、不能标敏感（口令不经 Agent）。

运行配置是会话身份的一部分（沿用只认同一引用）；会话冻结打开时的那份，`acquire` / `verify_replay` 都用它。落盘只存引用：带引用的记录
写 `v=2`，旧插件读到它当作不存在（`unknown_session`），不会把带参数的会话当成无参数会话复活（C26）；不带引用的记录照旧 `v=1`。
引用用不了（别的机器 / 敏感值随 GUI 进程没了 / 格式更新）→ 明确拒绝，脚本一次都不执行。回包 `run_config` 只有引用 / 个数 / 来源。

### 三、环境采用：同一个服务

`envadvice.adopt_candidate`（候选 id 只从本机枚举换路径、环境代对拍、全局锁、现场体检、与安装互斥、只写一次项目设置）是**唯一实现**：
HTTP `PATCH /api/engine/environment {candidate}` 与 MCP 的 `adopt_environment=<id>`（+ `expected_environment_generation`）都委派它。
授权模型与 `workdir=` / `prepare_dependencies=` 相同：这是用户的回答，Agent 把候选告诉用户、按用户的选择传入，不替用户选；采用 ≠ 安装。
`TAVOTTO_ENV_ADOPTION=auto` 兼容开关的退出条件之一（显式采用参数）由此满足，删除仍等「一版发布后」（ADR 0114）。

### 四、没有界面时：立即返回结构化待办

MCP 进程不接答题界面，broker 对 `input()` 立即回「无答案」（ADR 0099 §五，从不等待）。桥在失败载荷里**追加**（原字段一个不动）：

- `requirements[]`：`{kind, answer_with, where}` 索引——`answer_with` 是再调 `tavotto_open_figure` 时带的参数名（`workdir` /
  `prepare_dependencies` / `adopt_environment` / `argv`），`None` 时 `where` 说明这里答不了：`tavotto_app`（运行中的输入、口令、指认
  数据位置、显式解释器设置）或 `user`（升级引擎）；
- `input`（`reason` 闭集 + `secret`）、`arguments`（解析类别 + T07 的只读 schema 摘要：flags / required / choices / 类型，无 help 原文、
  无默认值，数量有界）、`environment`（`envadvice.recommend()` 纯读投影：候选 id / 标签 / 状态 / 项目相对路径 / 环境代）。

口令那一问的说明明确要求「不要让用户把口令发给你」。build 期间的失败（`_render`）与跑前的门走同一份投影。

### 五、失败结果有界

单图 open / 显式 apply 摘要的既有预算不变；**其余工具的失败**（`call_tool` 的 BridgeError 分支，apply 除外——省略 summary 的旧 apply
仍保留完整失败）守 `ERROR_RESULT_BUDGET_BYTES = 64 KiB`，量编码后的整个 CallToolResult。预算内逐字段不变；超了按
`ERROR_ELISION_ORDER` 逐项截（traceback 先），`ok` / `code` / `requirements` / `input` / `capability` 不截，`elided` 写明截了哪些、
列表原来几条、完整诊断**没有保留**。

### 六、可选引擎模块

桥用到的新模块（`capabilities` / `runconfig` / `envadvice` / `envlease` / `scriptargs`）比插件最低引擎版本新，只经
`bridge._optional_engine()` 取，不进 `_BRIDGE_IMPORT` / `BRIDGE_IMPORTS_AT_MIN`（放进去等于把最低版本抬到今天）；用到它们的路径先过
能力协商，或在缺席时退回旧行为。`tests/test_mcp_compat.py` 钉住可选集与必需集不相交、取可选模块只有这一个入口。

## 不做 / 边界

- MCP 不建准备会话：open 是「编辑已知图」的执行器（与渲染门同类，ADR 0116 §二），消费同一组跑前门与 provider；只有脚本、没有登记 stem
  的目标在 MCP 里仍是 `no_figure` / `stem_not_parameterizable`（引导用户在 Tavotto 里首跑），不另写一条脚本首跑。
- CLI：`tavotto run -- python 脚本.py 参数…` 是 native 档，argv / cwd / stdout / stdin / barrier 原样（不进运行配置登记）；
  `tavotto open` 仍经 `/api/registry/probe` 无参数交接，未加 argv（交接后用户在 GUI 里填参数）。
- 真实宿主（Codex Desktop / CLI、其他宿主）上的交互未在本 ADR 的测试里验证：协议用例只证明 server 侧合同。
