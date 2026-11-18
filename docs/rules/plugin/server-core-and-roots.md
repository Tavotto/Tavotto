# MCP server：只翻译、stdout、路径范围（ADR 0006 / 0009）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布（2026-08-18）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_mcp_roundtrip.py`、`tests/test_mcp_roots.py`、`tests/test_mcp_stdio.py`、`tests/test_mcp_local_scope.py`

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 100
改到: `tavotto_mcp/` 的分层、`rpc.hijack_stdout()`、`RootAuthority` / `roots.WORKSPACE_FAILURES`
主题: MCP server：只翻译、stdout、路径范围 → `server-core-and-roots.md`
必守要点: 只翻译不实现；先存 `_REAL_STDOUT` 再改道；授权失败分档、「没弹框」绝不报成用户拒绝；越界一律拒
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

## 开场零开销：首包 engine 摘要、期刊宽松解析、规则进工具描述（ADR 0192 §4）

> 看护用例：`tests/test_mcp_zero_onboarding.py`（结构化解析 `initialize` / `tools/list`，不对源码做子串断言）。

- **健康状态并进回包**：每个 MCP 进程第一次**成功**的 `tavotto_open_figure`（单图或批量非 `failed`）回包带
  `engine: {version, update?}`，之后不再带（`server._ENGINE_REPORTED`）。数据与话术只有一份：`server._engine_digest()`，
  `tavotto_health` 的 `update` 也从它来；`tavotto_health` 本身不变。更新检查仍不碰网络（`updatenotice` 只读缓存），
  所以进程里第一个 open 可能还没有 `update`——这是设计，`tavotto_export` 的 `notice_once` 兜后面。
- **期刊参数宽松解析**：`bridge.resolve_journal_id`，在 `server.call_tool` 分派前对 `JOURNAL_RESOLVE_TOOLS`
  （`JOURNAL_ID_TOOLS` + `tavotto_fix_to_journal`）做一次，处理器与桥里一律只见精确 id。目录就是 `list_journals` 读的
  那份（引擎 `search_journals`），**不在插件里抄期刊表**。精确 id 原样放行、回包不带 `journal_resolved`；否则归一
  （NFKC、小写、去空白标点）后先与 id / 名称 / 别名整串相等、再子串（出版社不参与）：唯一命中 → 用它，回包
  `journal_resolved: {from, id}`；多个 → `journal_ambiguous` + `candidates`（≤ 8）；没有 → `journal_unknown` + 最接近的
  `candidates`（≤ 8）。与手写 `profile_id` / `journal` 同给时不解析（引擎报 `journal_id_conflict`）；总开关关着 / 旧引擎 /
  非字符串原样放行。**`journal_ambiguous` 是新码：`feat/mcp-error-next`（ADR 0192 §3）合并后须在 `nextstep.py` 登记**
  （`next` 带候选），其 AST 守卫会要求它。
- **规则写进工具描述**：宿主（如 opencode）不一定加载技能，只看 `initialize.instructions` 与 `tools/list` 的 description。
  instructions 与 `tavotto_health` / `tavotto_guide` / `tavotto_open_figure` / `tavotto_apply_overrides` / `tavotto_export` 的
  description 写明：直接开始、工具缺失或引擎报错才调 health、不必先调 guide、期刊直接写名字、patches 发全量、什么必须回代码改、
  导出之后再关会话。**不许改回「开工前先调 health / guide」**；description 保持短，细节进技能 `references/`。

