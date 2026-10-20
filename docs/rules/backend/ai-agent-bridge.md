# 编码 Agent 桥

> 原文出自 `src/tavotto/AGENTS.md`「编码 Agent 桥」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `POST /api/ai/run` → spawn 本机的编码 Agent CLI（`codex exec` / `claude -p`），
  cwd=figures 目录；修改前快照到 `cache/ai_snapshots/`，结束后 diff 经 SSE
  `ai.done` 推送（`ai.*` 事件由 app 层补上发起项目的 `pj`，#589）；revert 恢复快照——**只回滚这次 AI 改完的那一版**：会话结束时
  记下 `after_sha256`（内存会话 + sidecar），脚本此后又变过（人工 / 另一次 AI /
  删除）就 409 `ai_revert_conflict`、原件零改动；没有记录（中断会话、老 sidecar）
  是「不知道」一档，照旧放行（`tests/test_ai_revert_stale.py`，QA STATE-09）。**文件真的变了就在 `ai.done` 之前走统一刷新**
  （ADR 0041）：`ai_bridge.run(on_changed)` 是注入的钩子，app 层接
  `_after_ai_change(ctx, script)`——顺序与 watcher 的 `_dispatch` 逐字相同（作废
  worker → `refresh_project(reason="ai")` → `panel.file_changed`，payload 带
  `reason: "ai"`），先 `engine_watch.absorb()` 问 watcher 这次写入它消化过没有，
  消化过就只补刷新、不再作废也不再发第二份事件。结局经 `refresh_outcome()`
  压成枚举进 `ai.done.refresh` 与历史库 `refresh` 列：`changed: true` +
  `refresh.status: failed` 是「改成了、项目没刷新」，**不许合成一个「成功」**，
  也不许把会话记成失败。项目 watcher（`engine/project_watch.py`，ADR 0026）
  仍是兜底：AI 路径没跑成、或 CLI 之外的改动，它都会看到。**不 probe、不跑脚本。**
  MCP 侧同一件事是 `tavotto_refresh_project`（`codex-plugin/AGENTS.md`）；
  `app.refresh_project` 是四条路径（手动 / watcher / codex / ai）唯一的漏斗，
  `project_refresh_completed` 遥测只在这一处、只收这四个来由。
- **「支持哪些 Agent」的唯一权威是 `engine/ai_agents.py` 的 `AGENT_REGISTRY`**
  （ADR 0015，改动前先读）。候选探测、启动验证、无副作用就绪检查、命令构造、
  流式输出分类、一键安装包名，全部由各自的 `AgentDefinition` 适配器给出。
  `ai_bridge.py` 只剩会话编排（快照 / SSE / diff / revert / cancel / history），
  **不许再写 `if agent == "codex"` 这种分支**——`tests/test_ai_agents.py` 用一个
  Fake Adapter 反证「加第三个 Agent 不用改通用层」，写回分支那条会红。
  依赖方向单向：`ai_agents ← ai_providers ← ai_bridge`；第三方接口的注入结果
  由 ai_bridge 算好、经 `RunContext` 交给适配器拼装（`ai_agents` 不 import 另外两个）。
  生产注册表**只放真的能跑起来的** Codex 与 Claude Code：架构支持第三个 ≠
  假装已经支持，界面上不出现「即将推出」的占位行。
- `GET /api/ai/capabilities` 实测本机每个已注册 Agent（安装/版本/就绪/模型/
  推理强度），返回**动态 `agents[]`**（旧的 `providers` / `settings` / `active`
  三个硬编码对象已整体移除，见 ADR 0015）。`argv` 不公开——前端没有消费者。
  通用设置走 `PATCH /api/ai/agents/<id>`（`enabled` / `path_override`），
  安装走 `POST|GET /api/ai/agents/<id>/install`；自定义路径**不允许硬编码私人
  路径**（pytest 看护）。run 可带 model/effort。
- **CLI 子进程一律用 `ai_agents.spawn_env()` 的增强 PATH**（探测与运行同一份）：
  桌面壳从 Finder / 开始菜单启动时继承 GUI 的最小 PATH，npm shim 的
  `#!/usr/bin/env node` 解析不到 node（`env: node: No such file or directory`）
  ——把 CLI 所在目录 + 常见安装目录补到 PATH 末尾即可，不改用户已有排序。
  **`engine/codexinstall.py` 问 Codex 的每一跳也走它**（`_codex_run`，2026-09-06
  用户反馈 05）：同一台机器终端里 `tavotto codex doctor` 全绿、设置页却报「插件市场
  登记失败」，差的只是 spawn 它的那个进程的 PATH（桌面壳 `ps -E` 实测只有四个系统
  目录）。「问不到」是独立一档，detail 里要明说这不等于「没登记」。
- **CLI 探测（Windows 尤其）**：`search_locations()` 在 PATH 之外把 npm 全局、
  `%LOCALAPPDATA%\Microsoft\WindowsApps`（**商店版 codex 的执行别名——真身在
  受 ACL 保护的 WindowsApps 包体里，只能走这个入口**）、WinGet/scoop/choco/
  bun/volta 全翻一遍；macOS 上 ChatGPT 应用内置的 codex 由 **codex 适配器自己的**
  `extra_search_locations()` 追加（排在常规位置之后）。Windows 上 **Codex 桌面版自带的
  CLI**（`%LOCALAPPDATA%\OpenAI\Codex\bin\<哈希>\codex.exe`，不在 PATH，#726）由 codex
  适配器的 `extra_candidates()` 给出，来源 `codex_desktop_bundled`；列文件与新到旧排序
  **只有 `ai_agents.desktop_codex_candidates()` 一份**，`codexinstall.find_codex()`
  （`tavotto codex install`）调的也是它——两边对「这台机器上的 codex 是哪一份」不许
  各写一份探测（`test_desktop_bundled_codex_agrees_with_codex_install`）。排序：
  设置 → PATH → 目录落点（npm 全局、WindowsApps 执行别名、常见目录）→ 桌面版自带
  （新到旧）→ npm 包内二进制 → MSIX 包体。理由：用户自己装的 CLI 是他选的那份、通常
  也更新，排在前面（与 macOS 上 `chatgpt_bundle` 排在常规位置之后同一个道理，也与
  `find_codex()` 的 PATH → 兜底目录 → 桌面版顺序一致）；桌面版那份是完整可用的安装，
  排在后两手「残缺安装的补救」之前。新增来源要同步前端
  `settings.agents.source.<来源>` 的中英文案（`test_every_detection_source_has_a_label_in_both_locales`
  逐字对齐 `SOURCES`，少一档界面就露出原始 id）。每个候选带一个 `source`
  标签，**只是诊断信息、不参与「能不能用」的判断**——判据只有「`--version`
  真的起得来」这一条。找不到时 capabilities 的 `diagnostics.searched` 告诉用户
  找过哪儿，第一个坏候选记在 `diagnostics.broken_path`（界面据此把「安装不可用」
  与「未安装」分开说）。npm 装出来的 `codex.cmd` 外壳经 `resolve_shim()` 解析成
  真正的 exe/node 脚本——经 cmd.exe 中转会吃掉提示词里的 `%`、`&`、`^`、`<`、
  `>`、`|`（中文提示里写个「透明度调到 50%」就够出事）。路径拼接一律用字符串，
  不用 pathlib（`os.name` 一变 Path 就分派到另一半实现，连跨平台测这段都做不到）。
- **就绪检查（readiness）与探测是两件事**：探测回答「有没有一个能启动的可执行
  文件」，就绪检查回答「它现在能不能干活」。**只允许跑官方 CLI 明确提供的本地
  状态命令**（`codex login status` / `claude auth status`），不发模型请求、不建
  会话、不产生费用、不改登录状态，`stdin=DEVNULL` + 10s 硬超时；不支持 / 超时 /
  看不懂 → `unknown`，映射为「已安装」。**绝不因为「配置目录存在」就宣布已登录，
  也绝不为了让那一行变绿偷偷发一个真实 Prompt。** `claude auth status` 的 JSON
  里带着邮箱 / 组织名 / 订阅档位——**只取 `loggedIn`**，其余一个字节都不进
  capabilities、日志或诊断包。
- **状态模型六档**（ready / installed / needs_auth / broken / not_installed /
  disabled）+ 前端本地的 `detecting`。`usable = enabled && 能启动 && state ∉
  {broken, not_installed, needs_auth}`。`enabled` 是三态：没表过态跟着「装没装」
  走，**明确关过就一直关着**。判据在后端（`require_usable`），`/api/ai/run`
  自己判一次——只靠前端隐藏不够，那个端点可以被直接调；**它的兜底判据就是
  `usable` 那一个字段**，在那里重列一遍条件就是第二份定义，分叉的表现是
  「界面把它藏了、API 还放它进来」。
- **接了第三方接口时，CLI 自己的登录态不参与判定**：注入凭据的全部意义就是让
  CLI 不必用官方登录跑起来，拿它的登录态回答「能不能派活」是**把判据的主语搞错**
  ——表现是「配好 DeepSeek 的用户发现 Codex 从选择器里整个消失」。判据取
  `ai_providers.spawn_overrides()` 是否**真的**产出了参数/环境变量，不是「配置里
  有没有一条记录」（codex 侧 base_url 为空时它什么都不注入，那时登录态仍算数）。
- **`path_override` 来自请求体、最终会被 spawn**：非空且无 NUL → `realpath`
  归一化（`..` 与符号链接在判断**之前**解掉）→ 存在的普通文件且可执行 →
  **文件名必须指向该 Agent**。最后一条挡的是「把 Tavotto 指向 /bin/sh」那一整类。
- **遥测的 agent 白名单取自 `telemetry.EVENTS` 的枚举，不是注册表**：拿「在不在
  注册表里」当白名单，加第三个 Agent 之后恒真，而 capture() 只收表里那几个值
  ——那个 Agent 的调用会被静默丢弃。
- **第三方 API 接入（`engine/ai_providers.py`）**：按**协议族**分支（anthropic 走
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` 环境变量，openai 走
  `-c model_provider=tavotto` + `[model_providers.tavotto]` 临时覆盖 +
  `TAVOTTO_CODEX_API_KEY`），谁支持接第三方接口由适配器的 `endpoint_family` 说了算
  ——**不再自己列一份 agent 名单**。
  **一律 spawn 时注入，绝不改写用户的 `~/.claude/settings.json` 或
  `~/.codex/config.toml`**（cc-switch 是改文件的，那对它合理，对我们是越权：
  用户在别的终端里跑 claude/codex 必须还是他自己那套）。密钥存用户配置
  （目录权限收到 0700），接口一律只回「有没有 + 尾四位」。
- **codex 模型清单读 `$CODEX_HOME/config.toml`（默认 `~/.codex`）的 `model` +
  `[profiles.*].model`，绝不在源码里写模型名**——OpenAI 换代后旧名会被服务端
  直接拒收（400 `The 'gpt-5' model is not supported when using Codex with a
  ChatGPT account.`）；读不到就交白卷，让 CLI 用自己的默认值。强度档位
  minimal/low/medium/high/max 是 CLI 侧开关，与模型清单无关。
  前端 `aiStore.prunePrefs` 在每次探测后丢弃 localStorage 里已失效的选择，
  否则用户会永远卡在一个报错的旧模型上（vitest + pytest 双侧看护）。
- **用户配置按 agent id 分键**：`ai.agents.<id>.{path_override, enabled}`。
  旧的 `ai.codex_path` / `ai.claude_path` 由 `config._migrate_ai_agents()`
  一次性迁移并**立刻删掉旧键**（两份权威并存 = 一边改路径另一边不知道）；
  规则写成正则而不是列举两个名字。不认识的 agent id 原样留着。
- **幂等与并发（ADR 0132）**：`POST /api/ai/run` 可带前端点发送时生成的 `client_request_id`（UUID，
  `[A-Za-z0-9_-]{8,64}`，格式不对 400，不静默当没带）。`run()` 在 `script_guard` + `_REQUEST_LOCK` 里先按
  **(项目, id)** 查内存会话与历史库（`ai_history` 的 `client_request_id` 列 + 部分唯一索引），命中抛
  `DuplicateRequest`、app 层返回既有 `{session, script, deduped: true}`，**不 spawn、不重复记遥测**；
  去重先于忙检查。同脚本上另一个请求还在跑（`script_busy`）→ 409 `ai_script_busy`——并发保护在后端，
  不再只靠前端的 `runningHere`。`GET /api/ai/requests/<id>` 只读、按当前项目隔离，回
  `{session,status,changed,script}` 或 404 `ai_request_not_found`（前端判「已发出未确认」要不要重发的唯一依据）。
  `ai.*` 事件带 `client_request_id`。**请求 id 与提示词一样不进日志 / 遥测 / 诊断包**
  （`tests/test_ai_idempotency.py::test_prompt_and_request_id_never_reach_the_log`）。
- **草稿与待确认账本（ADR 0132）**：助手输入框的未发送草稿与「已发出、等确认」的提交存 `ai_history.sqlite3`
  的 `ai_drafts` / `ai_pending`（主语 (项目, fileId[, rid])，按项目隔离，不进项目目录 / 日志 / 遥测 / 诊断包）。
  端点 `GET|PUT|DELETE /api/ai/drafts`（PUT 全文覆盖、>20 KB 413 `ai_draft_too_large` 不截断；DELETE 带 `if_text`
  条件删除）、`PUT` 带单调写入戳 `stamp`（旧戳 → `{stale:true,stamp}` 不覆盖；空文本 / 超限留墓碑；条件删除与 accept 推进戳；`ai_drafts.stamp` 旧表补列），`PUT|DELETE /api/ai/drafts/pending/<rid>`、`POST /api/ai/drafts/pending/<rid>/accept`（事务内清账本，
  仅当草稿当前的写入戳仍等于账本记的 `draft_stamp`（提交时草稿的戳；自提交后没被改过）才删草稿，**不比文本**——提交文本是 trim 过的；账本没记戳就不动草稿；`GET /api/ai/drafts` 回行戳 `stamp`（含墓碑）供客户端抬高戳下限）。每项目 30 份、每面板 8 条未解决账本（满了拒绝新提交 409 `ai_pending_limit`，不淘汰旧的）、30 天过期（启动与写入时有界清理）。
  **`run()` 先写幂等记录再 spawn**：写库失败不 spawn，spawn 失败标 `failed`。
  **读-判-写一律在一个写事务里**：`put_draft`（比戳）、`put_pending`（数名额）、`accept_pending`（读账本再按戳删草稿）、
  `mark_interrupted_running` 都走 `BEGIN IMMEDIATE`（`ai_history._immediate`），两个连接 / 两个服务进程不会各拿着过期的视图互相覆盖。
  `_connect` 打开后即提交迁移回填，连接不会因为迁移的写语句而碰巧占着写锁。`POST /api/ai/run` 在 rid 格式校验之后**立刻**
  查 (项目, rid)，命中直接按去重语义回既有会话，再往后才解析面板与注册表（可变目标：原请求登记后映射变了，同 id 重发也不能 404）。
  **`supersedes` 与墓碑**：`PUT /api/ai/drafts/pending/<rid>` 带 `supersedes` 时，同一个事务里先给每个旧 rid 写墓碑预留（`ai_requests.session='~superseded'`）再删旧行；
  晚到的旧 rid 在 `run()` 的原子预留处被 409 `ai_request_superseded` 拒绝，`GET /api/ai/requests/<rid>` 回 `status: superseded`；
  旧 rid 已被真实预留则回滚并 409 `ai_supersede_conflict`。取代处理完之后、写新行之前（同一个事务），同 (项目, 面板) 或 `same_script`
  认作同一份脚本的面板上还有待确认行 → 409 `ai_pending_unresolved`（带 `rids`，不写）：「同一修改不执行两次」由这道闸保证，不靠客户端的本地视图。
  每面板 8 条的名额先判；闸守住之后同面板最多一条，名额只对守住之前留下的旧数据起作用。路由包装层对 spawn 之前的任何异常也删该 rid 的待确认行，spawn 之后的绝不删。
  **`GET /api/ai/history?status=open`（无搜索 / 固定筛选，恢复专用）一次返回全部「还没结束」的行，不分页**——集合是「running，或已取消 / 已中断但
  `_pump` 还没收尾（status IN (cancelled, interrupted) 且 ended_ms 为空）」，即 `OPEN_SESSION_SQL`（运行中的会话天然有上限；翻页期间集合会变，offset 翻页会漏掉
  仍在运行的），硬上限 2000，超过时响应带 `truncated: true` 并记一条日志（实际不会发生）。取消后刷新时后者没有占位、没有轮询，
  之后到的 `ai.done` 会被丢掉，所以恢复要这一集合。**其余查询（含任务历史面板的 `status=running` 筛选）照常分页**，`status=running` 不再有一次取全的特例。
  前端 `doRestore` 因此一次请求取全。
  会话详情 `GET /api/ai/sessions/<sid>` 是**显式白名单 DTO**（`SESSION_DTO_KEYS`），会话 dict 里的内部对象（`proc`、`exited` 的 Event 等）
  列在 `SESSION_INTERNAL_KEYS`，新增字段必须在两者之一表态（有用例钉住）。
  **会话详情 `GET /api/ai/sessions/<sid>` 带 `final`**：只有 `_pump` 收尾（changed / diff / refresh 都确定、已落库）之后才为 true。
  `cancel` 之后 status 立刻是 `cancelled`，但结果还没算出来——那份快照 `final: false`，前端不得当终态。历史库里的行以 `ended_ms`
  是否已写判（`cancel` / `interrupt_all` 只改 status、不写 ended_ms；没写时属主进程已不在才算终态），重启后的 sidecar 回退路径恒为 true；启动清理给死掉的 cancelled 行补 `ended_ms`。
  迁移补列吞掉 `duplicate column name`（两个连接同时迁移同一个库时后到的会撞上）。
  **请求预留是跨进程原子的**：进程内的查找只是快速路径（两个 Tavotto 进程共用同一数据目录时管不到对方）。真正的闸门是
  `ai_history.record_start`——`BEGIN IMMEDIATE` 事务里对 `ai_requests` 做**普通 INSERT**（不加 OR IGNORE / OR REPLACE），
  唯一约束冲突 = 别人已预留，回 `(False, 既有会话 id)`，`run()` 清掉为这次尝试备好的快照 / sidecar、不 spawn、按去重回既有会话；
  预留与会话行同一事务，会话行用普通 INSERT，不再顶替同请求 id 的行。
  **「同一脚本忙」同样跨进程**：`sessions.owner_pid` 记登记它的服务进程；`record_start` 在**同一个 `BEGIN IMMEDIATE` 事务**里，
  预留之后再查同 (项目, 脚本) 上 `status='running'` 且**属主进程还活着**（`procalive.pid_alive`，POSIX `os.kill(pid, 0)` / Windows
  `OpenProcess`；量不了按活着算；行超过 24 h 当属主已死，防 pid 复用造僵尸锁）的行，命中抛 `ScriptBusy`（整个事务回滚，请求 id 不被消耗），
  `run()` 回 409 `ai_script_busy`、清掉半成品、不 spawn。属主未知 / 就是本进程 / 已死的 running 行不算忙。
  **启动清理 `mark_interrupted_running` 只标属主已死的行**，不碰共用同一数据目录的另一个仍活着的服务进程里的会话。spawn 失败仍保留 `failed` 记录（同 rid 去重到它，明确没执行；前端换新 id 重发）。看护 `tests/test_ai_drafts.py`。
- 会话历史在 `engine/ai_history.py`（SQLite `cache/ai_history.sqlite3`，
  按 project 列过滤）；启动时 running→interrupted + purge(180d，pinned 豁免)；
  历史 API：list（分页/搜索/筛选）/delete/pin。前端默认只显示人类可读目标，
  脚本名收在「技术详情」。
- **TAF 任务（ADR 0138，`engine/ai_tasks.py`）**：一个任务就是一条会话——`sessions` 上追加的 `task_type` / `phase` /
  `outcome` / `verification` / `envelope` / `budget_used` / `parent_task_id` 七列（`_ADDED_COLUMNS`，老行全 NULL = 不是任务），
  幂等仍是 `ai_requests` 的原子预留、中断识别仍是 `mark_interrupted_running`；**不另建表、库、状态机或 Agent 名单**（执行者
  只认 `AGENT_REGISTRY` 或 `deterministic`）。`outcome` 与 `status` 分开记，终态与结局经 `record_end(task_result=…)` **同一条
  UPDATE** 落库；`record_task_phase` 只推进 `status='running'` 的行。验证四态只取 `rendercore.inspector.VERDICTS`，结局只由
  `ai_tasks.derive_outcome` 推（unknown 永不算 completed，保持类 failed 是 failed 不是 partial）；提交前检查只要有一条不是
  verified / not_applicable 就不提交，提交前最后再复查一次取消与期限。契约未知字段一律拒绝、验证渲染恒为 safe。
  **Phase 1 的 Task Core 不写用户文件**（声明写入即 `contract_writes_unsupported`）：写已有脚本只经 AI 桥的会话，那是 Phase 3
  的接入面（届时进程内仍只有 `SESSIONS` 一张会话表）。生产 `ADAPTERS` 只放取得资格的适配器。看护 `tests/test_ai_tasks.py`。
- **Figure Verifier（`engine/figureverify.py`）与数据指纹**：判据只读 Tavotto worker 的 manifest（`editable` 当前值、`bbox`、
  只读的 `data_fingerprint`），断言闭集与 FigureBench 同名（`figureverify.KINDS` == 基准的 `ASSERTION_KINDS`，用例钉住）；
  manifest 说不出的事实列在 `NOT_OBSERVABLE`、判 unknown，对象改前在改后没了是 failed、改前就不在是 unknown。
  `manifest.data_fingerprint` 只给数据类元素（线 / 3D 线、集合、柱组「底 + 高」、误差棒「点 + 误差线」、图像、stem），
  规则 `canonical_digest`（形状 + 小端 float64、掩码 = NaN）与基准探针 / 标准库镜像逐字同一份；每张图 `FINGERPRINT_BUDGET`
  个数值，超出的元素记 `over_budget`（不截断、不扣配额）。指纹不进 `editable`、不参与 gid / identity（ADR 0083）、不进文档。
  L3 新版式问题只复用 `interference.detect` + `preflight.run`，只算改后新出现的。看护 `tests/test_figure_verifier.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 「支持哪些 Agent」只在 `AGENT_REGISTRY`，通用层不许 `if agent == "codex"`
- CLI 子进程一律 `spawn_env()`
- 就绪检查只跑官方本地状态命令、`claude auth status` 只取 `loggedIn`
- `path_override` 必须 realpath + 指向该 Agent
- spawn 时注入、绝不改写用户的 settings / config.toml
- 模型名不写进源码
