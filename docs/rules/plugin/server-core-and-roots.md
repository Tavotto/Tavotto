# MCP server：只翻译、stdout、路径范围（ADR 0006 / 0009）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布（2026-08-18）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_mcp_roundtrip.py`、`tests/test_mcp_roots.py`、`tests/test_mcp_stdio.py`、`tests/test_mcp_local_scope.py`、`tests/test_mcp_error_next.py`（失败回包的 `next`）

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 100
改到: `tavotto_mcp/` 的分层、`rpc.hijack_stdout()`、`RootAuthority` / `roots.WORKSPACE_FAILURES`
主题: MCP server：只翻译、stdout、路径范围 → `server-core-and-roots.md`
必守要点: 只翻译不实现；先存 `_REAL_STDOUT` 再改道；授权失败分档、「没弹框」绝不报成用户拒绝；越界一律拒；失败回包的 `next` 只在 `nextstep.py` 集中映射
-->

ADR 0005 的「skills-only / 不做 MCP server」这一条**已被 ADR 0006 推翻**
（交接那条路不变）。

- 插件清单加 `"mcpServers": "./codex.mcp.json"`（ADR 0109 前叫 `./.mcp.json`；改名是因为别的宿主会自动读插件根
  `.mcp.json`）；它是**本地 stdio**
  （`command: python3` + `args: ["./mcp/server.py"]` + `cwd/env_vars/tool_timeout_sec`）。
  字段形状取自 Codex 官方插件装出来的清单，**不要猜**。
- **`codex-plugin/mcp/tavotto_mcp/` 只翻译不实现**：会话、manifest、override、patch 规范化、
  导出全部落回 `tavotto.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。
  发给 worker 的 patches 与 Flask `/api/engine/render` 走同一条路径，所以 ADR 0003 的
  等价性不变式原样成立（`tests/test_mcp_roundtrip.py` 用真 matplotlib + 真 stdio 逐条验：
  热态 == 全新 worker 重放、figure 尺寸变、axes 几何变、关掉重开）。
- **stdout 归协议独占**：`rpc.hijack_stdout()` 把 `sys.stdout` 改道到 stderr，**必须先
  存下真正的 stdout 句柄**（`_REAL_STDOUT`）。顺序反了协议帧全写到 stderr 上，症状是
  「initialize 永远等不到响应」且零报错（开发期真撞到过）。
- **路径范围只有一个权威 `RootAuthority`**：显式 `TAVOTTO_MCP_ROOTS` → host 明确
  声明后的 `roots/list`（Roots 已弃用，只作兼容）→ 用户经 `elicitation/create`
  批准、只活在本连接内的精确 realpath → 宿主工作区变量 → 安全 cwd。模型传来的
  `project_path` 只是候选，不能自证权限；相对路径只有恰好一个可信根时才解析。
  确认框默认 false，拒绝/取消/超时一律 fail-closed，重新 initialize 清掉授权；
  **授权失败要分档**（issue #173）：唯一出处 `roots.WORKSPACE_FAILURES`，一个稳定
  `code` ↔ 一个 `disposition` ↔ 一句下一步；「宿主声明了能力却没弹框」是独立一档
  （`fix_host_wiring`），**绝不能报成用户拒绝**——两者的处置正好相反。code 只作机器
  标识，不许当文案念给用户；
  **每个入口都拒主目录本身及其上级**（ADR 0109：MiniMax Code 的插件路线 `roots/list` 回 home、WorkBuddy / MiniMax
  的插件进程 cwd 可能是 home），与生成器同一判据；被拒又没有别的根时报 `workspace_root_too_broad`。
  **本机兜底 `TAVOTTO_MCP_SCOPE=local`**（ADR 0009 §1b，2026-11-05）只在宿主没给可用目录时生效：范围 = 本机任意位置，
  来源 `local_scope`；判「这条路径能不能碰」**只经 `bridge._judge_path`**（打开目标 / argv / 会话恢复都过）；这一档按 `_lookup_chain` 逐段走系统实际走的路，每段每跳都判，只拦
  `readscope` 凭据黑名单（`path_denied`），引擎没有黑名单就全拦；不装写入根绊线；相对路径要求改传绝对路径。
  root 改变后旧 session 必须回 `workspace_root_changed`。server→client 请求只能在
  活跃 `tools/call` 内发，reader pump 必须保序且有界等待。越界一律拒，**绝不
  「就近找一个能用的」**。看护 `tests/test_mcp_roots.py`、双向协议用例与
  `tests/test_mcp_stdio.py`。**没装 Tavotto 时降级而不是退出**（降级 server 握手正常、每个工具说人话）
  ——静默退出在 Codex 里表现为「插件没有工具」。

## 失败回包的 `next`（ADR 0189 第 3 条）

- **`next` 是增量字段**：每个 `BridgeError` 的失败回包可带 `next`，**不改任何 `code` 的取值与含义**（老调用方照旧按 `code` 分诊）。
  只进 `structuredContent`，**不进念给用户听的 `content`**（与 `code` 同一条纪律）。`next` 在 `ERROR_PROTECTED_KEYS` 里：
  错误预算要整键删字段时它与 `code` 一样留下，所以它必须短（一句 `why`，候选 / 选项最多 `nextstep.MAX_OPTIONS` 个）。
- **三种形状**：`{tool, args, why}`（直接再调一个工具）、`{action: "edit_script", why}`（必须回代码改）、
  `{action: "ask_user", question, options}`（要用户决定，Agent 不替用户猜、不替用户授权）。
- **码 → `next` 的映射只在 `tavotto_mcp/nextstep.py`**，挂接点只有一处：`server.call_tool` 把 `BridgeError` 变成结果的地方
  （`nextstep.attach`）；不散在各个 raise 点。`BridgeError` 自己显式带了 `next` 的不覆盖。
- **参数能补全就补全**：重新 open 带当次调用的 `project_path` / `script_path` / `stem`；期刊不认识带 `query` 去 `tavotto_list_journals`；
  栏位 / stem / 会话候选直接来自错误的 extra。补不全就写 `<...>` 占位，**不编造值**；拿不到足够上下文时宁可不给 `next`。
- **每个码要么有映射（`BUILDERS`），要么在显式的「没有可行下一步」清单（`NO_NEXT`，每项一句理由）里**。看护用例按 AST 收集
  `bridge.py` / `server.py` / `roots.py` 里出现的全部码（`code=` / `error_code=` / `default_code=` 关键字、模块级常量、引擎常量、
  `code ==` 比较、`fail("...")` 结局码、`WORKSPACE_FAILURES`），新增一个码而没登记会红；清单里有桥里已不存在的码也红。
  **不用子串断言源码。** 守卫有自检（喂一段故意漏登记的源码必须被抓到）。
- 未登记的码（多半是 worker 新增）只按「需要输入」的结构化载荷兜底（`confirmation` / `dependency_preparation` / `missing_input` /
  `explicit`），其余不给 `next`。
- `fix_to_journal` 的没改成**不是** `BridgeError`（回包 `ok: false` + `exit`），它的 `next` 由 `nextstep.fix_next` 在 `_call_fix_to_journal`
  里挂；冲突点名见 `fix-to-journal.md`。
- `nextstep.py` 是纯 Python、不 import `tavotto.engine`，不在桥的三处同源约束里；它是运行时文件，进 `configure.RUNTIME_FILES`。
- server `instructions` 写明「失败结果带 `next` 时照它做」。
