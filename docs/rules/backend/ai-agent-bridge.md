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
  **协议：Codex 只认 `wire_api = "responses"`**（新版 codex 读到 `"chat"` 直接整个启动失败）。
  `WIRE_APIS` 只剩 responses，`_clean` 默认 responses，`spawn_overrides` 结构上永远不产出 chat，
  预设里没有任何 chat 条目。**按协议分流，不是「国产一律走 Claude Code」**：只有 Anthropic 兼容端点的
  厂商（Kimi 开放平台 / 智谱 / MiniMax / 百炼按量 / 百炼 Coding Plan）经 Claude Code 接入；**原生支持
  Responses 的厂商可留在 Codex**——目前是 DeepSeek 与 Kimi Code 会员。DeepSeek（预设 `deepseek-codex`，base_url `https://api.deepseek.com`
  无结尾斜杠、Codex 其后拼 `/responses`，模型 `deepseek-flash` / `deepseek-v4-pro`；依据 DeepSeek 官方 Codex 集成文档，
  Claude 版 `deepseek` 预设并存）；Kimi Code（预设 `kimi-code-codex`，`https://api.kimi.com/coding/v1`，模型 `k3-256k` / `k3`；
  依据 Kimi Code 官方 Codex 集成文档，Claude 版 `kimi-code` 并存）。百炼的 Responses 地址带用户的 WorkspaceId、我们推不出，**不加 Codex 预设**。
  codex 侧预设只有官方、DeepSeek、Kimi Code 会员与「自定义（OpenAI Responses 兼容）」——**界面名不带执行器后缀**：预设按执行器分页列出（只列该执行器能接的），选中后名字原样成为接口名，带「（Codex）」只会多余又难看。预设的模型名会过时，界面可编辑。
  **已存的旧 codex + `wire_api="chat"` 记录既不静默坏、也不静默改执行器**：判据只在
  `ai_providers.incompatibility()`（`public()` 回 `incompatible: "codex_chat_removed"` + `migratable`），
  它被选为当前接口时 `ai_bridge.run` 在 spawn 之前以 409 `ai_endpoint_incompatible` 拒绝——**这个检查放在
  `client_request_id` 去重查找之后**（ADR 0132：已受理请求的重发得到既有会话，不能被事后的接口变化变成 409）；
  前端只消费这两个字段，行内一句话 + 一个主按钮。可迁移的由 `migration_kind()` 按主机名精确匹配分流，
  `POST /api/ai/endpoints/<id>/migrate`（统一入口 `ai_providers.migrate`）：`migratable == "codex_responses"`
  （`api.deepseek.com`）**原地**改成 Codex Responses（换 base_url、wire_api=responses、**保留 id / 密钥 / 作为
  codex 当前接口的激活状态**，已退役的 `deepseek-chat` / `deepseek-reasoner` 换成新型号）；`"claude"`
  （`dashscope.aliyuncs.com`）走 `migrate_to_claude`：新建 claude 记录（Anthropic 端点、**保留原密钥**、
  模型换成新预设，id 撞车则加 `-2`，绝不覆盖用户已有记录）、删旧记录（codex 当前接口随之清回官方登录），
  **不替用户激活 claude 那边**；对不上的自定义记录（`null`）只给「编辑」。看护：`tests/test_ai_endpoint_presets.py`。
- **codex 模型清单读 `$CODEX_HOME/config.toml`（默认 `~/.codex`）的 `model` +
  `[profiles.*].model`，绝不在源码里写模型名**——OpenAI 换代后旧名会被服务端
  直接拒收（400 `The 'gpt-5' model is not supported when using Codex with a
  ChatGPT account.`）；读不到就交白卷，让 CLI 用自己的默认值。强度档位
  minimal/low/medium/high/max 是 CLI 侧开关，与模型清单无关。
  **2026-10-22 起另并入 codex CLI 自己的模型目录缓存 `$CODEX_HOME/models_cache.json`**（只读）：
  `visibility == "list"` 的型号按 `priority` 排在 config 模型之后，`hide` 的不列；每个型号经加字段
  `model_details[]`（`id / display_name / description / efforts / default_effort`，与 `models` 并行）
  带显示名与**自己支持的**推理强度（`supported_reasoning_levels` / `default_reasoning_level`）。
  缓存缺失 / 损坏 / 字段变了一律退回只用 config.toml（不抛、不猜模型名）；第三方接口接管时
  `model_details` 为空（网关的型号名与官方目录无关）；Claude Code 侧仍是 sonnet/opus/haiku，只多带显示名。
  Agent 级 `efforts` 仍在，是各型号档位的并集（旧前端与 `prunePrefs` 用）。前端两级选择器
  （执行器家族 → 型号）与「档位跟着型号走」见 `web/src/components/ai/ModelPicker.tsx`、
  `aiStore.effortsForModel / resolveEffort`（`aiModelPicker.test.tsx`、`e2e/ai-model-picker.spec.ts`）。
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
  **读路径也收尾属主已死的 open 行**（#1113 的一半）：`api_ai_history?status=open` 与 `ai_bridge.get()` 先调 `ai_history.reap_dead_owners`
  （与启动清理共用 `_close_dead_owner_rows`；先无写锁探测，有死属主才 `_immediate` 持锁重读；**读路径不收本进程 pid 的行**——
  `record_start` 与进 `SESSIONS` 之间隔着一次 Popen）。看护 `tests/test_ai_owner_reap.py`。
  **跨进程取消（#1113 主体，`tests/test_ai_cross_process_cancel.py`）= 持久取消意图 + 属主执行，B 从不对 A 的子进程发信号**（pid / pgid 会被复用，
  不能按 pid 杀）。`sessions` 追加五列（`_ADDED_COLUMNS`，看列在不在，不建库、不动 `user_version`，老行全 NULL）：`owner_token`（`ai_history.OWNER_TOKEN`，
  进程启动随机生成、只在内存；`record_start` 登记本进程会话时写入，替别的进程登记不冒充）、`owner_beat_ms`、`cancel_requested_ms`、`cancel_token`
  （写意图时观察到的 owner_token）、`cancel_result`（`ok` / `refused_committing` / `gone` / `owner_gone`）。`POST /api/ai/sessions/<sid>/cancel` 现在**按请求者的项目隔离**
  （不属于当前项目的会话一律回 `{ok:false,reason:"not_found"}`，与不存在的 sid 同一个回答）；响应 `{ok, mode?, reason?}`：`ok` 含义不变（取消请求被接受），
  `mode: "local"` = 已在本进程终止，`mode: "owner"` = 已转交持有它的另一个服务进程（≤ `OWNER_TICK_S`，终态以会话详情的 `final` 为准；详情多一个 `cancel_requested`）。
  `ai_bridge.cancel_detailed(sid, project)`：在 `SESSIONS` 里 → 原 `_cancel_local`（先问 `ai_tasks.cancel`，进入提交后不接受——`ai_tasks` 仍是唯一权威）；不在 →
  `ai_history.request_cancel`，**一个 `_immediate` 事务**读-判-写：属主已死（pid 不在 / **心跳过期**）→ 不写意图、就地走 `_close_dead_owner_rows` 收尾（`reason: owner_gone`）；
  老行无 token 且属主活着 → `owner_unsupported`（对方不会轮询，写了只会悬挂）；`phase='committing'` → `committing`；已有意图 → 幂等不重写；否则
  `UPDATE … WHERE owner_token=<先前观察到的>` 做 **CAS**（pid 复用的新属主 / 换代的行不被写，回 `stale`）。属主侧：`ai_bridge.run` 在登记之前 `ensure_owner_loop()`，
  守护线程 `ai-owner-loop` 每 `OWNER_TICK_S`（2 s）调 `ai_history.owner_tick()`——对本进程 token 名下 open 行写 `owner_beat_ms`（无 open 行不拿写锁），并收取
  **`cancel_token` 等于本进程 token** 的意图，走进程内同一条 `_cancel_local`，结果记 `cancel_result`。**属主死活只有一个判据 `ai_history.row_owner_alive`**
  （忙碌判定 / 启动清理 / 读路径收尾 / `final` 共用）：pid 存活，且（有 token+beat 的行）心跳不超过 `OWNER_BEAT_DEAD_MS`（10 分钟，只兜 pid 复用：复用 pid 的新进程不会续心跳）；无 token / 无 beat 的老行按原语义
  （pid + 24 h）。**两个阈值分开**：`request_cancel` 另看 `OWNER_BEAT_STALE_MS`（30 s，tick 的 15 倍）——属主活着但心跳超过它 = `owner_unresponsive`（`ok:false`），不写会悬挂的意图、
  **不收尾、不改行**：高负载下心跳线程被饿住很常见，此时 pid 活着、CLI 可能仍在写脚本，不能把行收成终态。**登记与入内存之间的窗口**：`record_start` 提交之后、`SESSIONS[sid]` 放进去之前（隔着 Popen / `begin_registered`），别的进程已能写意图——tick 遇到"属本进程 token、仍 running、不在 `SESSIONS`"**不消费意图**（不写 `cancel_result`），`run()` 放进内存后立刻 `_execute_cancel_intent`（与 tick 同一函数；`_INTENT_LOCK` 只护"重查 + 标记执行中"，**取消在锁外做**——`_terminate` 会 spawn 子进程（Windows 的 taskkill 是一次 Popen），持锁取消会被任何重入自锁；重入 / 并发的第二个入口被 `_INTENT_RUNNING` 挡住，锁序 `_REQUEST_LOCK` → `_INTENT_LOCK`）补查一次；spawn 失败 / 会话自己先收尾时意图随终态记 `failed` / `finished`。属主死亡时尚未执行的意图：收尾把 `running` 行转 `cancelled`（用户要的是取消）而不是 `interrupted`。
  **盲区**：pid 复用后最长 `OWNER_BEAT_DEAD_MS` 内仍按活着算（忙碌判定会多挡一阵）；属主真卡死而 pid 活着时，行一直开到判死线；这两条与启动清理一样只改状态、不杀孤儿 CLI。
  取消延迟最坏 ≈ `OWNER_TICK_S`。
- **会话版本号与增量对账（#1115，`tests/test_ai_session_rev.py`）**：断线 / 切项目 / 刷新的间隙里结束的任务，`ai.done` 是一条连接上的瞬时事件，
  接受时待确认账本行已删（ADR 0132）、恢复查询又只取 open——本页没有任何锚点。事实放在库里、客户端按版本对账，不改账本生命周期、不给 SSE 做重放缓冲、不建新表：
  `sessions.rev`（`_ADDED_COLUMNS`，看列在不在；老行 NULL = 纪元之前，不进增量）。**凡改变对外可见状态的写**（`record_start` / `record_end` / `record_task_phase` /
  `update_status` / `_close_dead_owner_rows` / `request_cancel` 写意图）在**同一个 `BEGIN IMMEDIATE` 事务里**取版本计数器 `ai_rev_counter`（单行表，迁移时从现有 `MAX(rev)` 起算）自增后的值盖到该行（`_next_rev` / `_stamp`，只能在写锁内调；
  所以这几个写都走 `_immediate`，跨进程严格递增、不撞号；**计数器不随会话行删除回落**——取 `MAX(rev)` 的话删掉最新行后新会话会复用 ≤ 客户端水位的 rev，错过的终态被永久忽略）。心跳 `owner_beat_ms` / `cancel_result` / 置顶 / 删除不抬 rev。`GET /api/ai/sessions/changes?since=<rev>`
  （同 `/api/ai/*` 的会话认证，按当前项目隔离）回 `{head, more, sessions}`：先读全局计数器再读 `rev <= head` 的行，返回之后才提交的写不会被水位跳过；`head` 是全局水位
  （别的项目的写只让它变大、不泄露内容）；不带 `since` 只回此刻的 `head`（客户端取起点）；超 `CHANGES_PAGE` 行时 `more: true`、`head` 只推进到最后一行；
  先 `reap_dead_owners`（死属主的收尾也是变化）。每行是 `CHANGE_DTO_KEYS` 的精简快照（判终态 + 建占位的摘要列，**不含**转录 / diff / refresh——终态详情仍只有
  `GET /api/ai/sessions/<sid>` 一份）；`final` 与会话详情同一个判据，本进程内存里的会话认它自己的 `final`。`ai.done` 带终态那次写的 `rev`，**只是"去对账"的提示**。
  **已知限制**：旧版本进程（不写 rev）与新版本共用数据目录时，旧进程写的行不进增量；水位在前端（页面内存 + `sessionStorage`，见前端细则），后端不存。
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
- **TAF 改图（`engine/ai_figure_edit.py`，Phase 3a）**：只在 `config.ai_settings()["taf"]["enabled"] is True`（默认关、无界面开关）
  时由 `ai_bridge.run` 走到；开关关着时旧路径逐字不变（cwd 是项目目录、不写任务列、`ADAPTERS` 为空）。开着时：spawn 之前在
  `data_dir()/cache/ai_candidates/<sid>/` **字节复制**目标脚本（+ 项目根的 `paper_style.py`，绝不建链接），拍原项目有界指纹（跳过
  `tavottofile/`、注册表、版本库 / 缓存目录）与静态输出名；Agent 的 cwd 是候选目录、提示词附原项目只读路径。候选建不起来就整条
  拒绝（不静默退回原地修改）。进程起来之后由泵线程调 `ai_tasks.begin_registered` / `drive_registered`（会话表仍只有 `SESSIONS`、
  预留仍是同一次 `record_start`）：提交前 L1 = 候选里只有目标脚本变了（新建的辅助文件不提交、不算）、语法、静态输出名不变、项目里
  的目标脚本仍是起跑那一版、原项目指纹没变（超限 = unknown = 不提交）；提交 = `ai_figure_edit.commit` 持脚本锁再核一次基线 →
  `write_script` → 统一刷新一次（之后不再刷第二遍）。改前快照仍是 `run()` 起跑时拍的那份，回滚照旧走 `revert`。`cancel` /
  `interrupt_all` 先问 `ai_tasks.cancel`：进入提交后取消回 False、不改状态（`interrupt_all` 对该会话 `continue`，不计数）。
  原项目指纹对符号链接（文件 / 目录）记 lstat + `readlink` 目标、不进入；经链接写到项目外的内容不在指纹里（CLI 沙盒负责）。提交后渲染层验证（Phase 3b）：app 经 `task_hooks` 注入两个钩子——
  `before` 在**写入之前**从 `pool.peek` 到的、已 build 的 safe 会话读改前 manifest（不执行；没有 = 这次会话没渲染过这张图，或该会话 build
  **实际执行**的源码 sha256 与此刻磁盘脚本不符（旧版脚本建的）→ `render.figure` unknown `no_baseline`，**也不做改后渲染**，不为验证多跑脚本）；
  判据是「执行了哪一版源码」而不是「哪个进程 / 启动时采样」：worker 在 `build()` 里报摘要：按模块 import 的入口（entry 非 `__main__`）由**实际加载源码的 loader 报**——import 期间临时包 `SourceFileLoader.get_code`，入口脚本只 `get_data` 读源码、记 sha256、`source_to_code` 编译，不读任何 `.pyc`（恰好一次加载才报，否则 None）；`__main__` 入口（`runpy.run_path` 读源码本身、不查 `__pycache__`）沿用执行前后括号取 `(mtime_ns, size, inode, sha256)`，两端完全相同才报 sha，否则 None → 验证记 unknown；经 build 响应与
  `_stems_summary` 的 `source_sha256` 自报（同一进程只 build 一次，二次 build / workerd 去重回旧会话拿到的仍是当初执行的那一版；workerd 对 build 响应
  原样转发），池侧记为 `last_build_source_sha256`（失败清空）；`retire` 在写入之后、刷新之前 `invalidate(force=True)` 尽力同步退役旧会话，只是让
  after 少撞上旧会话、**不是正确性依据**（watcher 可能已先做过异步 invalidate，force 找不到键也算成功），确认不了 → unknown `session_not_retired`、不调
  `after`；`after(expected_sha256)` 在提交 + 刷新之后
（`enginesession.resolve`、safe、`runconfig.default_selection`）取会话并 build（已 built 的不再发请求）——就是前端随后要做的那次重渲染，会话执行的 sha256 须等于刚提交字节的，否则抛 `StaleSession` → unknown `stale_session`（不是 failed 也不是 verified）；剩余边界：`__main__` 括号路径上刻意 utime 回拨 mtime 的对抗性写入。前端来时会话
  已 built（`tests/test_ai_figure_edit_app.py` 用执行计数钉住）。比对两样：数据指纹（`figureverify.data_unchanged`：改前每个数据
  元素改后同 gid 且 identity 不冲突、逐部件相同）与目标范围以外的改动（`figureverify.changes_outside_target`，范围取自桥放进 `cand.extras["target"]` 的 `{stem, gid, scope}`，范围外有改动记 unknown `changes_outside_target`、结局最高 partial；派生改动的排除见 `05-verification-design.md` 第七节）。`before` 钩子给的是**无 override 重放**（`worker.override(stem, [])`，不执行用户脚本）而不是落盘 manifest——后者带着用户 override，改后 build 不带；坐标范围 / 版式的目标判定不比、列进
  not_covered；改后渲染失败或少了 stem 记 failed（已提交的改动不自动回滚）。没注入钩子（纯引擎调用方）仍是 `render_not_wired`。`ai.done` 与会话详情多一个 `task`（类型、结局、未满足项、文案键），终态与 `task_result` 同一条
  `record_end`。R-03（CLI 不写 cwd 外）是 CLI 的行为，指纹兜底只能发现、不能阻止。看护 `tests/test_ai_figure_edit.py`。

## 生成可编辑版本（`convert_to_editable`，ADR 0143，1.0 登记例外，默认开）

位图库（Pillow / OpenCV / plotly / ROOT）画的图，改图助手在候选工作区 `cache/ai_convert/<sid>/` 里**新建**一份 Matplotlib 版脚本
`<原名>_matplotlib.py`；入口仍是 `ai_bridge.run(..., convert={library, original_rel})`（同一张会话表、同一份历史、同一套取消 / 幂等），
适配器 `ai_convert.ADAPTER` 注册在 `ai_tasks.ADAPTERS`，不看 TAF 实验开关（逃生口 `ai.convert.enabled=false` /
`TAVOTTO_NO_CONVERT_TO_EDITABLE=1`）。**不变式**：原脚本字节不变（转换会话 `revert` 一律 `ai_revert_not_applicable`）；Agent 只写候选里的
那一个新文件（提交前：候选树其余逐字节不变、项目指纹不变、项目里原脚本不变）；任务 `commit` 只留存候选、`writes_user_files=False`；
写进项目只有 `ai_convert.adopt` → `scriptlock.create_script` → `atomicio.create_new_bytes`（O_EXCL，存在即失败，重名加序号，
永不覆盖），采用前再核原脚本 sha256 与候选摘要。校验 = 在用户同一路由下 probe 一遍（紧挨原脚本的 `tavotto_convert_probe_<sid>.py`
临时文件，后端建、跑完删），必须捕获到 Matplotlib 图。看护：`tests/test_ai_convert.py`、`tests/test_ai_convert_app.py`。

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
