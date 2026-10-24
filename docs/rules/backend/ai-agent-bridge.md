# 编码 Agent 桥

> 原文出自 `src/tavotto/AGENTS.md`「编码 Agent 桥」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_ai_agents.py`、`test_ai_login.py`、`test_ai_bridge.py`、`test_ai_capabilities.py`、`test_ai_refresh.py`、`test_ai_history.py`、`test_ai_idempotency.py`、`test_ai_revert_stale.py`、`test_codex_install_cli.py`
> `src/tavotto/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml；revert 只回滚本次 AI 那一版（细则见 `ai-agent-bridge.md`）

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 240
改到: `engine/ai_bridge.py`、`ai_agents.py`、`ai_providers.py`、`codexinstall.py`、`/api/ai/*`
主题: 编码 Agent 桥 → `ai-agent-bridge.md`
必守要点: 「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml
-->

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
- **登录按钮（设置页「用 Codex / Claude Code 自己的登录」那一行，`needs_auth` 时的唯一主按钮）只启动
  官方命令，不碰凭据**：命令由适配器的 `login_spec()`（`LoginSpec(argv_suffix, mode, account)`）写死——
  Codex = `login`（background，官方命令自己开浏览器、自己在本机起回调）、Claude = `auth login`
  （terminal，需要交互，只许在用户的系统终端里打开：macOS 写一个自删的 `.command` 交给
  `open -a Terminal`，Windows `CREATE_NEW_CONSOLE` 直接运行不经 cmd.exe，Linux 依次试
  x-terminal-emulator / gnome-terminal / konsole / xfce4-terminal / xterm，全没有就返回
  `reason: terminal_unavailable` + 要手动运行的命令，用已解析的真实 argv 引号化，不是 `command_names[0]`；macOS `.command` 与 Linux `sh -c` 脚本只 export 闭集 `_TERMINAL_ENV_NAMES`（PATH / HOME / CLAUDE_CONFIG_DIR / CODEX_HOME / 代理 / CA 等），**不含任何密钥**，已运行的 Terminal 不继承 `open` 传的 env）。设置页：支持第三方接口的 agent 登录按钮在「模型服务」首行，否则在「账号」区块，二者择一。**绝不发全局按键、不用 System Events**；默认实现
  `None` = 不支持，capabilities 的 `features.login` / `login_account` 据此给出，通用层
  （`start_login` / `login_status`）不写 `if agent == ...`（`tests/test_ai_login.py` 用 Fake Adapter 与
  源码扫描双重反证）。`POST|GET /api/ai/agents/<id>/login` 与 install 同组、同一会话认证；argv =
  探测通过的真实启动 argv + 写死的子命令，环境与探测同一份 `spawn_env()`，请求体里没有也不接受
  任何命令字段。后台命令的输出走管道，读线程（上限 64 KB，超出丢弃）**只取第一条主机精确命中适配器 `LoginSpec.url_hosts`（Codex = auth.openai.com）的 https 授权地址**存进登记、经 `GET /login` 的 `auth_url` 交给前端（浏览器没打开时给用户点；只含 state / PKCE，不是凭据），其余行一个字节都不存、不进日志。background 同一 Agent 同时只有一个进程（再点返回 `already_running`），守护线程在
  `LOGIN_TIMEOUT_S` 后结束**它自己起的那一个**；输出一律 DEVNULL，不读。**Tavotto 不读、不存、不刷新、
  不转发 Claude / Codex 的任何登录凭据**；登录成不成仍只看就绪检查（`codex login status` /
  `claude auth status`，一个字不变）——前端在进程结束 / 用户点「我已登录」/ 窗口重新聚焦时重新探测。
  新错误码 `ai_agent_login_unsupported`（400）/ `ai_agent_login_failed`（409）。测试里**绝不**对真实
  `~/.codex` / `~/.claude` 执行 login，一律桩 Popen。
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
  **预设的唯一权威是 `engine/ai_catalog.json`（厂商目录）**，`ai_providers.PRESETS` 只是 `ai_catalog.presets()`：
  每行 `{id, label_key, brand, region(cn|global|custom), agent, base_url, key_url, key_hint_key?, models_hint, oauth, source}`。
  **只收文档里明确写了 Anthropic Messages 兼容 base URL 的厂商**（Claude Code 接），外加官方登录态与两条用户自填；
  拿不准就不收，`source` 必须写出处。界面名 / key 提示只存 i18n 键（`dialogs:settings.agents.vendor.*` / `vendorKey.*`，
  中英两份），JSON 里不写中文界面串；`oauth` 本期恒为 `null`（预留位，界面不渲染「登录」）。加载失败 import 时抛
  `CatalogError`，不退成空列表。冻结产物靠 `packaging/tavotto.spec` 的 datas 带上这份 JSON。
  **型号以服务商实时列表为准**：`POST /api/ai/endpoints/models`（`ai_providers.live_models`）对接口
  `GET {base_url}/v1/models`（`x-api-key` + `Bearer` 两个头、`anthropic-version`，5 s 超时、不跟随重定向），
  只在用户点「从服务商获取」时才联网，设置页渲染不碰网络；成功结果按（接口 id, base_url, 密钥 sha256 前缀）
  缓存一天在 `config.data_dir()/ai_models_cache.json`；**密钥只发往已存地址同源（scheme + host + 端口，
  `ai_providers.live_models` 判，改了地址不带旧密钥 → `key_origin_mismatch`）、只走 https（回环 `localhost` /
  127.0.0.0/8 / `::1` 例外，字面判不解析 DNS，且回环 http **不经任何代理**（`ProxyHandler({})`，否则 HTTP_PROXY 会拿到明文密钥）；其他 http → `insecure_url`）**；任何失败只回稳定原因码（`no_key /
  key_origin_mismatch / insecure_url / timeout / unauthorized / bad_response / network`）、永远 200、不抛，界面保持预设 `models_hint` / 用户手填的列表。密钥只进请求头，
  日志只写接口 id 与原因码，缓存文件里没有密钥。看护：`tests/test_ai_catalog.py`、`EndpointDialog.test.tsx`。
- **账号登录（设备码 OAuth，`engine/ai_oauth.py`，ADR 0146，状态「提议」）**：只给**拿到 Tavotto 自有 client_id** 的厂商
  （目前只有 MiniMax 国内 / 海外；Kimi 走官方命令行，不在此列；流程按规格表可扩展）。client_id 只在 `ai_oauth.CLIENT_IDS`（出厂 `None`）与环境变量 `TAVOTTO_OAUTH_CLIENT_ID_MINIMAX`；
  `None` 时 capabilities 的 `oauth.<厂商>.oauth_available` 为 false、**界面不出现入口**（不放占位）。源码里不写别家的 client_id、
  请求头只如实标 Tavotto 与版本（`brand`/`__version__`），不冒充别家客户端。令牌写进一条 `agent=claude` 的接口记录
  （`auth:"oauth"` + `oauth:{access,refresh,expires_ms,token_endpoint,provider,region}`），与 api_key 一样只在用户配置（0700/0600）；
  `public()` 只回 `auth/signed_in/oauth_expires_ms/oauth_provider/oauth_region`，令牌一个字节都不回传，`save()` 剥掉请求体里的 `auth/oauth`，
  诊断包与日志不含令牌。**spawn 前刷新**：`ai_bridge.run` 在脚本锁 / 请求锁**之外**先调 `ai_oauth.ensure_fresh`（要联网，不能卡住别的请求），刷不动的结论等 `client_request_id` 去重查找之后、不兼容检查旁才抛（ADR 0132），剩余 < 10 分钟就回到**签发它的区域端点**
  （端点必须在已知表中）刷新并写回；刷不动 → 409 `ai_endpoint_needs_login`、不 spawn。注入只设 `ANTHROPIC_AUTH_TOKEN`（Bearer），
  `ANTHROPIC_API_KEY` 设空串（不发 x-api-key，也盖掉继承来的真密钥）。出站 HTTPS 的第七处（`ai_oauth._opener`，不跟随重定向、30 s、响应体上限），
  登记在 `test_outbound_https_trust.OUTBOUND` 与 `tlstrust` 模块头。端点：`POST /api/ai/oauth/<provider>/start`、`GET /api/ai/oauth/flows/<id>`、
  `POST /api/ai/oauth/flows/<id>/cancel`、`POST /api/ai/endpoints/<id>/logout`（只删令牌，不调撤销接口）。
  **原子性（Codex #1326）**：令牌落盘在 `ai_oauth._LOCK` 内先认领再写，`cancel()` 与之互斥（不会 cancelled 却留着令牌）；接口记录的所有读-改-写（`save/delete/set_active/migrate*/store_oauth/update_oauth`）都经 `config.update_ai_settings(fn)` 同一事务，不再读一份再整份覆盖。`_LOCK` 是进程内锁（后端只有一个进程），不做跨进程文件锁。
  看护：`tests/test_ai_oauth.py`（本机假授权服务器，绝不连真实服务器）。
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
  `model_details` 为空（网关的型号名与官方目录无关）；**Claude Code 侧同理读它自己的模型目录缓存**
  `$CLAUDE_CONFIG_DIR`（默认 `~/.claude`）`/cache/model-catalog/*.json`（只读）：多份取 `fetchedAt` 最新且可解析、
  main 段非空的一份；只列 `section == "main"`（`overflow` 旧型号不列），`model_details.id` 是完整 id（直接传 `--model`），
  `efforts` / `default_effort` 取 `runtime.effort_levels / default_effort`，派活时经 `--effort` 传入。默认型号 =
  `settings.json` 的 `model`（去 `[1m]` 后缀）依次按：完整 id → `provider_alias_targets` 别名 → 同 family 的 main 型号 → 目录第一项。
  缓存缺失 / 损坏 / 结构变了一律退回 sonnet/opus/haiku 三个别名且无档位（不抛、不猜）；旧记住的别名选择
  被 `prunePrefs` 丢弃后落到默认型号。第三方接口（网关）生效时 Claude 不暴露档位、`build_command` 不带 `--effort`
  （`effort_official_only`；网关后面的型号未必认这个档位）。
  `model_details[].efforts_known`（加字段）区分「未知」与「明确没有档位」：目录里给了该型号 runtime 但没有
  `effort_levels`（或为空）= `efforts_known: true` 且 `efforts: []`，前端不显示强度选择、发送不带 effort，Claude 与 Codex 的
  `build_command` 都兜底不传（请求档位不在该型号档位内同样不传）；只在 config.toml 里有、目录里没有的型号 = 未知
  （`false`/缺省），前端回落 Agent 级 `efforts`。
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
  原项目指纹对符号链接（文件 / 目录）记 lstat + `readlink` 目标、不进入；指纹不含 Python 环境目录（`pyvenv.cfg` / `conda-meta/` 标记，不按名字猜；自动装依赖会合法地写那里；盲区：往环境目录里写发现不了，由 CLI 沙盒 / 路径判据限制 Agent 只写候选目录）；经链接写到项目外的内容不在指纹里（CLI 沙盒负责）。提交后渲染层验证（Phase 3b）：app 经 `task_hooks` 注入两个钩子——
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

**R / MATLAB 来源（ADR 0143 修订，2026-10-23）**：`POST /api/ai/convert` 多一个可选 `source_language`（`python` 缺省 | `r` | `matlab`）。
r / matlab 时 `library` 必须缺省，后端用 `sourcelang.detect`（纯标准库；`.R` / `.r` / `.m` 纯文本、≤ 1 MB、UTF-8；`.m` 带 Objective-C 特征或
MATLAB 特征不足一律 None）复核文件与声明一致，对不上 400 `convert_source_language_mismatch`。**不运行 R / MATLAB**：对照只用请求给的 `path` 或脚本旁
同名位图（`ai_convert.sibling_original`），没有原图时 `comparison.reference=null`、`verification="unknown"`（界面说一句“请并排看一眼再采用”）。
提示词是 `ai_prompt_pack` convert 一节的变体（逐步搬运 + R→Python / MATLAB→Python 对照表）。Python 里没有可靠对应时 Agent 首行写
`TAVOTTO_NEEDS_INPUT: <原因>` 且不写新文件 → `ConvertExecutor` 抛 `ai_tasks.NeedsInput` → 结局 `needs_input`（原因清洗后限长 200 字，只进 `unmet[].detail`，
不进日志 / 遥测）；写了文件则按文件走，无标记照旧 `target_missing`。新脚本缺包：校验运行前 `convertdeps.ensure(project, rel, cancel)`（app 注入 `task_hooks["deps"]`，仅 r / matlab；`cancel` = `TaskRun.cancel_event`）
走 `deprepair.joint_plan_for` → `create_joint_plan` → `prepare`（不新写 pip 路径；取消经 `deprepair.cancel_status` 传到安装）。**名单**：只装 `engine/autoinstall_dists.txt`
（PEP 503 名；14 个种子 ∪ 30 个常见大包 ∪ PyPI 30 天下载前 1000（ClickHouse 公开 PyPI 数据集）− 排除名单（pip/setuptools/wheel 等管理环境与打包工具），`scripts/dev/refresh_autoinstall_list.py` 生成，spec datas 带上）里的包；名单外的包不自动装。**每个环境问一次**：
`project_venv` / 已存在的受管一代名单内直接装；系统 Python / 内置 runtime 永不装；其余环境（Conda / 指定 / 环境变量 / 用户采用）第一次出卡片，点主按钮才记
`ai.convert.auto_install_envs`（解释器路径 sha256 前 16 位 + 种类 + 名，无明文路径；设置页可撤销），装的动作是现有链条的标准动作（受管环境）。
**不自动装的分支都是真卡片**：`ConvertAdapter.hold` → `NeedsInput(reason_code="dependency_decision", data=card)`（`ai_tasks.NeedsInput` 带 `reason_code` / `data`，进
`unmet[]`），结局 `needs_input`、候选不删（`meta.json` `awaiting_dependencies`）；`POST /api/ai/convert/<sid>/dependencies {action:"install", impact_digest}` 起作业
（`ai_convert.prepare_resume` / `run_resume`：装 → 校验**同一个候选** → 留存，不重跑 Agent），`discard` 取消正在进行的安装。换了环境（系统 / 内置卡选完环境且解释器变了）前端发 `{action:"replan"}`：`run_resume(ensure=convertdeps.ensure)` 按此刻环境重判同一个候选（不带摘要，复用同一续跑作业与忙碌判定）。授权口径两种：点过卡片 = 前端回显卡片摘要（服务端不自回显，
对不上回新卡片）；已记过同意的环境 = 服务端回显同次调用算出的摘要（维护者 2026-10-23 裁决）。开关 `ai.convert.auto_install=false` / `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1`
（关着或计划不可执行时没有可执行动作，校验以 `missing_dependency` 失败，不修正）。
晚到的停止（ADR 0143 修订二）：`run_resume` 里依赖步骤返回 `installed`（过提交点，取消被拒）而取消标记已置位时，`run_validation` 在 `_ensure_dependencies` 处返回内部状态 `installed_stopped`，
续跑把候选留成 `meta.json` `deps_installed`（不删、不自动校验、`installed` 如实记录），`prepare_resume` 也接受这个状态；用户点继续 = `{action:"replan"}`。依赖步骤返回 `cancelled`（或没装过东西）才删候选。
前端续跑传输错误一律先 `GET /api/ai/convert/<sid>` 对账再决定，后端 POST 不做幂等（409 即对账触发点）。
会话元数据：sidecar 与 `meta.json` 记 `source_language`；`library` 在 r / matlab 时为 null；遥测不新增事件。看护：`tests/test_sourcelang.py`、
`tests/test_ai_convert_foreign.py`、`tests/test_ai_convert_foreign_app.py`、`tests/test_convertdeps.py`。

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

## 内置执行器（`engine/agent_runner/`，ADR 0147 提议，第一阶段：核心循环）

改图助手自己跑「模型 ↔ 工具」循环的独立子进程，不借 Codex / Claude Code CLI。**本阶段只有核心循环**，还没接进 `ai_bridge` /
`ai_agents` / 设置页（`AGENT_REGISTRY` 不变）。看护 `tests/test_agent_runner.py`、`tests/test_outbound_https_trust.py`。

- **边界**：纯标准库、3.10 可跑；不 import 科学栈，**不 import `ai_bridge` / `ai_agents` / `ai_tasks`**；内部依赖只有 `tlstrust` 与纯标准库的 `ai_prompt_pack`（convert 提示词的同一份文字）。
  只经 stdout 事件行与父进程说话。入口 `python -m tavotto.engine.agent_runner --cwd <候选目录> --target <相对路径> --wire <名> --base-url <url> --model <名>`。
- **协议**（`wires.py`）：`anthropic`（`{base}/v1/messages`，`x-api-key` 与 `Authorization: Bearer` 都给，`anthropic-version: 2023-06-01`，
  系统提示与最后一个工具定义带 `cache_control: ephemeral`）、`openai-chat`（`{base}/chat/completions`）、`openai-responses`
  （`{base}/responses`，`store: false`）、`demo`（剧本回放，不联网，端到端测试用）。anthropic 的思考由**调用方声明** `--thinking {adaptive,budget,none}`（默认 `none`，runner 不猜：第三方兼容网关各不相同）：`adaptive` 发 `thinking:{type:adaptive}` + 有 effort 才发 `output_config.effort`（新模型，不发已弃用的 `budget_tokens`）；`budget` 发旧式 `budget_tokens`（必须配 `--effort low|medium|high|max`，`max` 映射到 high 档；缺省 effort 起跑即 `internal/bad_args`、0 次请求，不静默不发 thinking）；`none` 都不发。thinking 块按原样回放，不做 assistant prefill，工具参数一律 `json.loads`。openai-responses 只在给了 `--effort` 时传 `reasoning.effort`。`--wire openai-responses` 配 `--effort max` 起跑即 `internal/bad_args`、0 次请求（Responses 没有 max 档，不静默不发 reasoning）。
  系统提示 + 工具定义在请求最前且逐字稳定（吃提示缓存）；变化的东西（目标名、用户指令）只在第一条用户消息里。
- **出站**：只许 https，或主机恰为字面量 `127.0.0.1` 的 http；URL 不带用户信息 / 查询 / 片段。https 走 `_opener()`（`build_opener(tlstrust.https_handler(tlstrust.client_context()))`
  + 不跟随重定向，代理沿用环境变量）；回环 http 用字面量 `HTTPConnection("127.0.0.1", …)` 直连、**不经代理**。响应体有上限；
  请求有超时；`tests/test_outbound_https_trust.py` 的 `OUTBOUND` 登记了 `engine/agent_runner/wires.py: _opener`。
- **密钥**：只读环境变量 `TAVOTTO_AGENT_API_KEY`（读后从本进程环境删除），绝不进 argv / stdout / stderr / 事件 / 日志 / repr。
  提示词只经 `--prompt-file` 或 stdin。服务端错误体不进任何输出（可能回显提示词），只留 `detail` 短代码。
  **模型的成功回复同样不可信**（服务端在请求头里拿到了密钥，可能回显）：`__main__` 的事件写出函数 `emit` 与 stderr 诊断函数 `diag` 是
  进程里**唯一**的输出出口，所有字符串（`text`、`tool` 摘要、`done.detail`、诊断）统一过 `redact.Redactor`，已知密钥（长度 ≥ 8 才参与）
  及其去空白 / JSON 转义变形换成 `[redacted]`。同一保证的另一出口：`edit_file` / `write_file` 的内容含密钥 → 工具错误 `secret_in_content`，拒写、文件零改动。
  提示词读不到或为空白 → 不调模型，`done{ok:false, reason:"internal", detail:"bad_prompt"}`、退出码 1。
- **工具与路径判据**（`tools.py`，ADR 0138 §四）：`read_file`（≤ 200 KB 的 UTF-8 文本）、`list_dir`（一层，符号链接只标注不跟随）、
  `edit_file`（`old` 必须在目标脚本里**恰好出现一次**，0 次 / 多次都是带说明的工具错误；本会话必须先 `read_file` 过目标）、
  `write_file`（整份覆盖，兜底）、`check`（调用方注入的回调，没注入 = `not_available` 工具错误）。**没有 shell、网络、MCP 工具。**
  路径判据只有一处 `FigureEditSession._resolve`：`realpath` 后须在候选目录内（符号链接先解开再判，链出界即越界）；写只许目标脚本，
  目标本身是符号链接也拒写。越界 / 写非目标 / 超大 / 非文本一律返回**工具错误结果**，不抛、不执行、文件零改动。
  换行：读出时统一成 `\n`，写回恢复文件原有的 `\r\n`（混合换行的文件会被统一成首见的那种）。
  将来 `data_to_figure` 的工具集**没有写文件工具**（只出结构化列映射，ADR 0138 §四）。
- **事件**（stdout 每行一条 JSON，ASCII 转义）：`thinking`（只有字符数）、`tool`（工具名、成败、一句话摘要，**不带文件内容 / 提示词 / 密钥**）、
  `text`（模型对用户说的话）、`usage`（每轮用量）、`done`（最后一条，恰好一条）。stderr 只写诊断级错误码。
- **结束条件与结局**：`done{ok, reason, detail}`，`reason` ∈ `completed | max_turns | budget | tool_error_loop | auth | quota | refused | network | cancelled`，
  外加兜底 `internal`（本模块自己的缺陷，`detail` 只有异常类型名）。失败**不抛**。429、5xx、408 与传输失败指数退避重试（有上限），401 / 403 → `auth`，
  402 或额度类 429 → `quota`，429 重试用尽 → `quota`，其余 4xx（含 409）不重试 → `network`。服务端过滤 / 拒答 → `refused`（chat `finish_reason=content_filter`、
  anthropic `stop_reason=refusal`、responses `status:incomplete` 且原因不是 `max_output_tokens`，`detail` 如 `incomplete_content_filter`）；
  responses 顶层 `status` 为 `failed`（`detail=failed_<error.code>`）或 `cancelled / queued / in_progress`（`status_<名>`）→ `network`，不再当作空的 end 轮。
  重试等待 = min(退避, Retry-After, 30 s, 距总时限的剩余)，剩余 ≤ 0 不再发请求 → `budget`（`detail=time`）；单次请求超时也不超过剩余时间。**等响应时也看总时限**：服务端以小于 socket 超时的间隔慢慢吐字节时每次 read 都成功，所以等待循环（与取消同一处、每 50 ms）同时判 deadline，到点即 `budget/time`，在途请求丢弃、回复不接受。
  **只有 `stop == end` 且无工具调用才算 `completed`**：其它停止值（未知 `stop_reason` / `finish_reason`，或 `tool_calls` / `tool_use` 结束却没有可用调用）→ `network`，`detail=bad_stop_<值>`（值只留小写字母数字下划线、≤ 24 字符）；`max_tokens` 仍是 `budget/output_truncated`。同一工具的同一错误码连续 3 次 → `tool_error_loop`。
  SIGTERM / SIGINT / Ctrl-Break → `cancelled`。
- **预算与账本**：轮数、token（input+output）、秒数三道上限。每轮 input / output / 耗时入账，结束时核「合计 == 各轮之和」，不等记
  `done.ledger_mismatch=true`（不抛）。每轮记账后立刻复查 token：**终止答复**（`end` 且无工具调用）累计 `>` 上限才拒，恰好用满上限仍 `completed`；带工具调用 / 要开下一轮的答复累计 `>=` 上限即 `budget/tokens`，工具不执行、不开新轮。估计上下文超阈值时把早先的 `read_file` 结果换成「已读过 X（第 a–b 行，sha 前缀）」。
- **`convert_to_editable` 工具集**（`tools.py` 的 `ConvertToEditableToolset` / `ConvertSession`，ADR 0147 §四；入口 `--task convert_to_editable --target <原脚本> --output <原名>_matplotlib.py`，
  默认任务仍是 `figure_edit`）。权限边界即 ADR 0143 §二：**原脚本永远不可写，Agent 只写候选目录顶层的那一个新文件**。
  - 工具只有 `read_file` / `list_dir`（候选内只读，与 figure_edit **同一个** `_resolve`，没有第二份路径判据）、`create_file(content)`、`edit_file(old, new)`、`check`；
    没有 `write_file`、没有 shell / 网络 / MCP。`create_file` 的可选 `path` 与 `edit_file` 的可选 `path` 只能是 `--output`：指向原脚本 → `source_readonly`，
    其它路径 → `not_output`，越界 → `path_outside`；一律工具错误、磁盘字节不变。第一次 `create_file` 用 `O_CREAT|O_EXCL` 新建（此刻已存在含悬空链接 → `output_exists`，不覆盖），
    之后允许整份覆盖这个新文件（模型自我修正）；`edit_file` 必须在本次运行里已 `create_file`（否则 `not_created`），`old` 恰好出现一次。含密钥的内容 → `secret_in_content`（新建 / 覆盖 / edit 三处）。
  - `--output` 起跑即校验：必须恰为 `<原名>_matplotlib.py`（原名 = `--target` 的 stem）、只有文件名（无目录、无反斜杠）、尚不存在（`lexists`）；不合格 → `done{ok:false, reason:"internal", detail:"bad_output"}`，**0 次模型请求**、磁盘不动。
  - `check`：同 figure_edit 的注入回调，语义是 ADR 0143 的 `run_validation`（用户同一路由下跑新脚本，必须捕获到 Matplotlib 图），回调返回 `(ok, 报告)`；**本阶段 CLI 入口没有注入方 → `not_available`**。
    新文件还没写出就 `check` → `not_created`（不调回调、不计数）。**回调最多被调 3 次**（ADR 0143「校验运行 ≤ 3」），第 4 次 `check_budget`。
  - 预算默认值由工具集给（`default_limits`；命令行不给 `--max-turns` 等就用它）：convert 为 20 轮 / 900 s / 400k token / 单轮输出 16384（整份脚本可能很长；20 轮 ≈ 读 1–3 + 写 1 + 至多 3 轮 check/修 + 收尾，留余量）；figure_edit 不变。
  - 系统提示是英文（要求用用户指令的语言回复）；产品要求三条（保留数据读取与计算 / 必须 Matplotlib 出图 / 图幅字号对齐）**引用 `ai_prompt_pack.convert_requirements()` 的同一份文字**（CLI 路径的 `convert_prompt` 也用它，不各抄一份），
    执行器特有的部分（只写一个新文件、不运行脚本、check ≤ 3 次）写在 `tools.py`。`ai_prompt_pack` 是纯标准库，所以 `agent_runner` 对它的 import 不破坏依赖方向（`test_import_architecture` 兜底）。
  - 看护 `tests/test_agent_runner_convert.py`（Demo 协议端到端 + 单元）。

## AI 修复工具箱（`engine/fixtools/`，ADR 0149 提议，D1：注册表 + 「看」「试」）

脚本跑不起来时让 AI 去查的工具集。**一张注册表、两种接法**：`REGISTRY: dict[str, FixTool]`（`__init__.py`）与接法无关，内置执行器经
`build_agent_tools(ctx)`，CLI 经 MCP（D3）都走同一个 `run_tool`（档位检查 → handler → 脱敏 → 审计）。看护 `tests/test_fixtools.py`。

- **边界**：本包 import `agent_runner.tools / wires`（取 `Tool` / `ToolResult`），**`agent_runner` 不 import 本包**（它是独立子进程，保持纯标准库）。
  不 import app / Flask：工具拿注入的 `FixContext`（`base.py`：项目根、主目录、环境 id → 解释器表、报告回调、环境清单回调、日志路径、脚本、执行器、`reads`）。
  测试里 `home` 一律给 `tmp_path` 假主目录。
- **档位**：`ToolSpec.tier ∈ observe | run | tavotto | listed | user_env | outward`（默认 `observe`）+ `waits_for_user`（默认 `False`），**不序列化给模型**
  （`tests/test_fixtools.py::test_tier_is_never_sent_to_the_model` 钉三种 wire 的请求体）。`FixContext.allowed_tiers` 之外的工具 `run_tool` 拒绝（`tier_not_allowed`）；裸构造默认只放行 `observe`，修复会话创建方用 `FixContext.for_fix_session(...)`（放行 `observe` + `run`）。
  D1 有两档：`observe` 看（`read_run_report` / `read_file` / `list_dir` / `find_files` / `grep_files` / `list_environments` / `read_logs` / `lookup_package`）；
  `run` 试（`try_import` / `try_run` / `inspect_environment`）——**运行用户代码，不承诺「什么都不改」**，保护范围与盲区见下条。
- **读取范围**（`readscope.py`，判据只有 `ReadScope.check`）：主目录 + 项目根 + 环境前缀内可读，其余 `outside_scope`；`DENY_HOME_RELATIVE` / `DENY_NAME_PATTERNS` 命中
  `denied`（**文件名表只列明确的凭据文件形状**：整名或固定后缀，不用 `*token*` / `*secret*` 子串通配；**目录不按名拦**，`ReadScope._path_denied` 对目录只比固定位置表）（词法与 `realpath` 后各判一次；大小写不敏感于 macOS / Windows；Tavotto 自己的 `config.json` 在内）。单次读 ≤ 256 KiB（**按 64 KiB 块读、自己切行，当前行只留前 256 KiB**，所以任何文件大小下内存都有界；单行超限就截断该行并说明；截断给说明，`start_line` / `max_lines` 续读），
  二进制只给类型与大小。项目外的读取进 `ctx.reads`；**跟随的文件符号链接按解析后的目标记**（`_walk` 与 `resolve_or_error` 都用 `ReadScope.check` 的结果：目标在项目外就记目标路径，在项目内不记）。
  **所有给模型的文本过 `readscope.scrub`**（`run_tool` 对返回内容再过一遍）；「键 + 值」类密钥只有**一条规则**（`_SECRET_ASSIGN`），由**键名后缀族**（`_SUFFIX_KEY`：键名以 key / token(s) / secret(s) / password(s) / passwd / pwd / credential(s) / auth / signature / sig 结尾，驼峰 / 下划线 / 连字符 / 点分、大小写不敏感）加旧表 `SENSITIVE_KEYS`（前缀形，如 `password_hint`）
  与值形状（裸值 / 单引号 / 双引号，含内部空格）驱动：命令行旗标后（空白或 `=`）的值整体换成 `***` 只留旗标，`key = value` / JSON 形保留引号换内容；`-p` 只在值带引号时当密码；非敏感旗标（`--output "my file.png"`）不动。**HTTP 凭据头**（`Authorization` / `Proxy-Authorization` / `Cookie` / `Set-Cookie` / `X-Api-Key` / `X-Auth-Token`）走单独一条 `_HTTP_CRED`：冒号后到行尾（JSON 形是那个字符串值，`curl -H '…'` 到包着它的引号）整体 `***`，因为值里有空格。非凭据的同尾词键名（`primary_key` / `sort_key` / `cache_key` / `monkey` / `max_tokens` …）只在 `_BENIGN_KEY` 里**整键**登记；光秃秃的 `key` 只在值带引号时抹（`sorted(x, key=len)` 不动）。键名匹配从词首起、前缀 ≤ 400 字符，保证单个超长词线性。`;` 分隔的连接串由同一条规则逐项处理；`scheme://user:password@host` 由 `_URL_USERINFO` 抹 password；最后一道 `_HIGH_ENTROPY` 兜底（≥ 32 字符、`[A-Za-z0-9+/=_-]`、含大写 + 小写 + 数字、且不是路径 / 标识符）。**试跑副本的文件符号链接**（`trial._copy_project(…, scope, skipped)`）：目标 `ReadScope.check` 可读才复制内容，否则跳过并在结果里写 `copy_incomplete`；目录链接不跟随。新增敏感位置：加进两张表并补 `test_fixtools.py::DENIED_REL`。
- **find / grep**：条数 + 墙钟（`SCAN_SECONDS`）上限；跳过 `SKIP_DIRS`、拦截区，`site-packages` 只下探两层，不跟随目录符号链接。
  **`grep_files` 是字面量搜索**（`in`，可选 `ignore_case`），不接受正则——没有正则就没有灾难性回溯；工具描述与 schema 如实告诉模型。
- **「试」的子进程**（`trial.run_python`）：与体检同一纪律——`runtime.probe_args`（`-B`）、`runtime.probe_env` + `launcher.activate_env`、`stdin=DEVNULL`、
  `creationflags=CREATE_NO_WINDOW`、UTF-8 解码（`errors="replace"`）、超时、cwd 为一次性临时目录。环境只认会话的**环境 id**（`list_environments` 登记），不接受解释器路径。
  模块名走 `fullmatch` 的点分标识符校验，并作为 `argv` 传入（不拼进 `-c`）。`try_run` 把脚本所在目录复制到临时目录（超 64 MiB / 5000 个文件只复制脚本）跑一次，
  MPLBACKEND=Agg；`FixContext.runner` 可注入执行器（接准备会话 / 运行器的现成路径留给后续 PR）。
  **fixtools 起的每个子进程只有 `run_python` 这一条执行路径，全部在 safe 守卫下跑**（`try_run` 的脚本、`try_import` 的 import 副作用、`inspect_environment` 的探测代码一视同仁；
  不再有系统临时目录里的 `tavotto-fixtool-*`，临时目录一律建在 `fix_trials/` 下，清理一律走 `_remove_tree` + `.cleanup_pending` + `sweep_stale_trials`，清不掉如实报 `cleanup_pending`）。
  **守卫的做法**：子进程按文件加载 `engine/writetripwire.py`（worker 的 safe 执行同一个写入根绊线，ADR 0141），写入根只有本次试跑目录
  `data_dir()/cache/fix_trials/<会话>-<随机>/`（`work/` 副本 + `tmp/` 专用 TMPDIR + `mpl/` + `out/`），读不限；越界写（`open` / `Path.write_text` / `os.remove` 等）当场 `PermissionError`，
  违规记进结果的 `blocked_writes`（脚本吞掉异常也在）。**绊线不是沙盒**，盲区见 `writetripwire` 头注。守卫装不上（握手文件没出现 / 退出码 97）→ `trial_unguarded`，不退化成裸跑。
  **操作系统沙盒（纵深）**：`trial._os_sandbox_prefix` 在 macOS 有 `/usr/bin/sandbox-exec`、Linux 有 `bwrap` 且探测能起时把子进程套进去（只许写试跑目录；macOS profile 原文在 `trial.macos_profile`，ADR 0149 §四）；找不到 / 探测失败退回绊线。工具结果带 `os_sandbox: macos-sandbox-exec|bwrap|none`（`inspect_environment` 是 JSON 里的 `os_sandbox` 字段）。
  `none`（含 Windows）时 C 层 / `ctypes` 的写不被拦；读与网络始终不受限。工具描述（给模型看）如实写这些，不得再写 "changes nothing"。
  `mode=native` 不属于 observe / run 档，直接 `native_trial_not_observe`（归 D2 的 `user_env` 档 + 用户确认）。
  **输出上限**：`run_python` 用 `Popen` + 两个读线程，每路只留 `OUTPUT_TAIL_BYTES`（64 KiB）尾部并数总字节，两路合计超 `OUTPUT_LIMIT_BYTES`（8 MiB）就结束子进程（POSIX 按自己的进程组，绝不按名字杀），结果 `output_truncated`。
  **副本清理**：删除带有限次重试（Windows 文件占用）；仍失败 → 结果如实报 `cleanup_pending`、目录留 `.cleanup_pending` 标记；`sweep_stale_trials()` 在每次 `run_python` 开头清标记目录与超过 1 小时的残留。
- **审计**（`audit.py`）：`data_dir()/ai/fix_audit.jsonl`，字段 `ts / session / step / tool / tier / args / approved_by / result{ok,code,summary}`；路径类参数保留、其余只记长度，
  内容不落盘；1 MiB 滚成 `.1`；失败不影响调用。

### D2a：「改」组（`change.py` / `snapshots.py` / `executors.py`，ADR 0149 §七）

六个工具：`install_packages`（`listed`）、`switch_execution` / `change_python` / `convert_script` / `rollback`（`tavotto`）、`select_environment`（`user_env`）。`run_command` 已拆出 D2a，由 #1374 落地（见下面「`run_command`」一节）。
看护 `tests/test_fixtools_change.py`。`FIX_SESSION_TIERS` 放行 observe / run / tavotto / listed / user_env / outward，**放行 ≠ 不问**：要不要问用户由各工具自己经 `FixContext.ask` 把关
（`change.approve`：没有 `ask`、回答不是 `yes`、`ask` 抛了、超时，一律 `needs_approval` 且审计记 `blocked`；通过则审计记 `approved_by=user`）。

- **执行都在既有出处里，本包不自己拼命令**：装 / 卸包 = `deprepair._pip_install` / `_pip_uninstall`（同一份 argv、只装 wheel、镜像回退）+ `pool.mutating_environment`（环境独占锁），
  需求串先过 `depresolve.parse_requirement`（`-r` / URL / 路径 / extras / 标记死在那里）；**名单** = `convertdeps.in_allowlist`（`autoinstall_dists.txt`，PyPI 公开下载量生成）：名单内不问、名单外 `ask`；
  `convertdeps.describe_env` 判为系统自带 / 内置 runtime 的解释器永不装（`env_not_installable`）。切运行方式 = `execpolicy.set_mode`（`safe` → `safe_only`，`native` → `compatible`；
  把用户明确选过的 `safe_only` 放宽要先问）。换环境 = `projectenv.remember(..., automatic=False, trigger="user_selected")`。
  执行器全部可经 `FixContext` 注入（`installer` / `uninstaller` / `freezer` / `python_changer` / `script_converter`）；**测试一律注入假的，不往真实环境装包**。
  `change_python` 缺省接 `executors._default_python_changer`（#1342 `pyversionfix` 的公开入口 `plan_python_change` → `apply_python_change`：只在 Tavotto 受管环境另建、不碰用户环境、私有 Python 锁里没有的版本不下载；本机关了 `TAVOTTO_AUTO_PYTHON_CHANGE` 或版本推不出就如实报错，不换；修复会话暂无进度事件通道，下载进度不推前端），可经 `python_changer` 注入替换；`convert_script` 的执行器（D2b 接转换任务）没注入时返回 `unavailable`。
- **快照**（`snapshots.py`）：除 `rollback` 外每个会改东西的工具执行前先 `snapshots.take`，存 `data_dir()/cache/fix_snapshots/<会话>/<id>.json`：项目设置里 `execution_policy` / `environment` 两个键的原值（含「没有」）+
  目标环境的 `pip freeze` 解析成 `{规范名: 版本}`（只留 `名==版本` 行，URL / 路径行不存）+ 环境目录顶层条目名。**打不出快照就不改**（`snapshot_failed`）；快照 id 进工具返回与审计行 `snapshot` 字段；每会话留最近 30 份。
  `rollback` 只认本会话 + 本项目的快照、id 形状固定（`snap_<时间>_<6 位十六进制>`）；设置**精确恢复**（原来没有的键清掉，并 `projectenv.reset_cache`）；包按 freeze 差异——快照后新装的卸掉
  （`pip` / `setuptools` / `wheel` 不卸），被改版本 / 被卸掉的装回快照里的版本，走同一条 `executors`。
- **`run_command`**：D2a 拆出、#1374 以两档落地，见 D2a 之后的「`run_command`」一节。
- **`convert_script`**：原脚本**绝不动**——只许写 `<原名>_matplotlib.py`（已存在则 `_matplotlib_2`…，不覆盖）；脚本必须在项目内（解析符号链接后）；转换器拿原脚本的**临时副本**（`data_dir()/cache/fix_convert/`），「读基线 → 转换器 → 核对 / 必要时恢复」整段持原脚本的 `script_guard`（单个脚本，没有枚举竞态），返回后核原脚本 sha256，变了用执行前读到的原字节经 `snapshots.restore_script` 写回并报 `original_modified`；副本用 `_remove_tree` 带重试清理（建 / 写副本失败时的清理结果同样如实报），删不掉报 `cleanup_pending`。
- **#1361 三条**：`_copy_project` 只复制普通文件（FIFO / socket / 设备及指向它们的链接跳过、记 `skipped["special"]`、结果 `copy_incomplete`，复制前再 `S_ISREG` 核一次防枚举后被换成 FIFO）；
  `list_environments` 列满 30 条时如实报 `[showing 30 of N …]`，本项目当前 / 选过的环境排前，`match` 参数按名字 / 来源 / 路径片段筛（被列出的才登记 id）；`_copy_project(…, reads)` 回传实际复制了内容的链接目标，`try_run` 逐个 `ctx.note_read(…, "try_run")`。
- **freeze 的「可用」按成败判（#1371 P1）**：`default_freezer` 成功回文本（全新空环境是 `""`）、失败回 `None`；快照的 `freeze_available` = 拿到了没有，不按输出是否为空。

- **转换器临时副本**（`data_dir()/cache/fix_convert/`）用 `trial._remove_tree` 带重试清理，删不掉如实报 `cleanup_pending` 并留 `.cleanup_pending` 标记，下次 `trial.sweep_stale_trials(root=…)` 清。

### `run_command`：任意命令，两档（`fixtools/command.py` / `fixtools/cmdsandbox.py`，#1374，ADR 0149 §七修订）

`tier=user_env`、`waits_for_user`。看护 `tests/test_fixtools_command.py`。参数：`argv`（列表）或 `command`（字符串，`shlex` 拆，**不经 shell**）二选一；`timeout_s`（默认 60、上限 300）；`outside_sandbox`（布尔，第②档开关，必须配 `reason`）；`reason`（一句给用户看的话，≤300 字）；`cwd`（`scratch` 默认 / `project`，`project` 只给第②档）。
先过**硬拦表**（`hard_block_reason`，命中**不问用户**直接 `hard_blocked`，审计 `blocked`，两档都适用）：sudo 类、磁盘 / 关机类、起 shell（先剥掉 `env` / `nohup` / `timeout` / `nice` 等包装器再判）、管道进 shell、shell 语法、`dd of=/dev/…`、对根 / 盘符 / 主目录的递归删改权限、
**宿主代理入口**（`osascript` `launchctl` `open` `systemd-run` `systemctl` `loginctl` `busctl` `gdbus` `dbus-send` `xdg-open` `xdotool` `docker` `podman` `nsenter` `crontab` `at` `schtasks` `reg` `wmic` …）、指向 `ReadScope` 硬拦区的参数。硬拦表是**第一道**，不是安全边界——沙盒才是（`env_contains_project` 之类的结构检查也在问用户之前）。

- **第①档（默认，加固沙盒内）**：`cmdsandbox.sandbox_kind()` 找 sandbox-exec / bwrap 并**探一次**（加固后的沙盒里真能跑 Python 才算可用）；没有 → `sandbox_unavailable`（**不问、不跑**，文字说明只能申请第②档）。可写清单 `command_writable_dirs`：命令自己的临时目录（`data_dir()/cache/fix_commands/<uuid>`，cwd 与 TMPDIR / TEMP / TMP 都是它，用完 `trial._remove_tree` 带重试清理）、
  目标 Python 环境前缀（`writable_envs`：`ctx.env_prefixes`（取前缀里的解释器）+ 已登记 env id 的解释器前缀，**所有候选统一**按 `convertdeps.describe_env` 判种类，系统自带 / 内置 runtime 一律不放；落在或盖住 Tavotto 安装目录 / 应用包 / 内置 runtime 的前缀、找不到解释器的前缀、浅到只有一两级的路径也不放；**项目内的 `.venv` 只放那个子目录**）、pip / conda 用户缓存（标准位置 `~/.cache/pip`、`~/Library/Caches/pip`、`~/.conda`，不读 pip.conf）。
  **可写前缀若是项目根或其上级 → `env_contains_project`**（问用户之前拒）。**允许联网**。凭据位置（`DENY_HOME_RELATIVE` 里实际存在的）在沙盒里连读都拒（bwrap 目录换空 tmpfs、文件盖 `/dev/null`；macOS `deny file-read*`）。
  环境：`cmdsandbox.clean_env` 去掉密钥类变量（名字带 KEY / TOKEN / SECRET / PASSWORD / AUTH …）与宿主代理入口变量（`DBUS_SESSION_BUS_ADDRESS` `DBUS_SYSTEM_BUS_ADDRESS` `XDG_RUNTIME_DIR` `DISPLAY` `WAYLAND_DISPLAY` `XAUTHORITY` `SSH_AUTH_SOCK` `GPG_AGENT_INFO` `DOCKER_HOST` …），`TMPDIR` 指向临时目录，`TAVOTTO_PROJECT` 给项目路径（项目只读）。
  卡：`{kind:"approve", tool:"run_command", summary, command(完整、已脱敏), cwd:"scratch", sandbox:"hardened"}`。沙盒拒写失败（退出码非 0 且输出含 `Operation not permitted` / `Read-only file system` / `PermissionError` …）→ 结果末尾加 `sandbox_denied:` 提示，说明可以申请第②档，**不替模型申请**。
- **第①档逃逸面与对策**（#1371 评审点名）：Linux：`/run` 空 tmpfs（user D-Bus 总线、`systemd-run --user`、docker.sock、`/run/dbus` 全不可见；`/var/run` 若是独立目录同样覆盖，常见情形是指向 `/run` 的链接）、`/tmp` 空 tmpfs（X11 路径套接字）、`--unshare-ipc/pid/uts`、`--new-session`（没有控制终端，无法 TIOCSTI 注入按键）、`--die-with-parent`；
  `/etc/resolv.conf` 常是指向 `/run/systemd/resolve/…` 的链接，所以 `/run` 清空后把那几个 resolv 文件只读放回（**只放文件**，不放带 varlink 套接字的目录）。项目恰在 `/tmp` 下时只读挂回。
  macOS：见下面的 profile 与白名单。
- **macOS profile**（`cmdsandbox.macos_profile`）：`allow default` 基线 + `deny file-write*` 只放清单内目录与 `/dev/null`（不放 `/dev/tty`）+ `deny appleevent-send` + `deny mach-lookup` 后**只放** `MACH_LOOKUP_ALLOW` + `deny process-exec` 指向 `EXEC_DENY` + `deny network-outbound (remote unix-socket)` 只放 `/private/var/run/mDNSResponder` + `(deny signal)` 只放 `self` / `children` / `same-sandbox`
  （写成 `(deny signal (target others))` 实测拦不住同用户的别的进程）。**mach-lookup 白名单来源**：Apple 自带 `/System/Library/Sandbox/Profiles/system.sb` 的基础块与 `(system-network)` 块，取其中只读查询的最小子集——DNS `com.apple.dnssd.service`；系统代理与网络配置 `com.apple.SystemConfiguration.configd` / `.DNSConfiguration` / `.SCNetworkReachability`、`com.apple.networkd`
  （Python 的 `_scproxy` 靠 configd 读系统代理，国内常见的系统代理 / TUN 环境下 pip 要它）；证书链校验 `com.apple.trustd` / `.agent`（curl / git 等系统 TLS 客户端）；用户与组查询 `com.apple.system.opendirectoryd.libinfo` / `.membership`、`com.apple.system.DirectoryService.libinfo_v1`。
  **刻意不放**：`cfprefsd`（能代写用户偏好 = 宿主代写）、`launchservicesd`、`pasteboard`、`securityd` / `SecurityServer`（钥匙串）、`tccd`、任何 XPC 应用服务。先严再按用例放。真机核实：Python 联网 / DNS、pip download、curl HTTPS、uv 在此 profile 下可用，`bootstrap_look_up` 对 launchservicesd 返回拒绝而 trustd 通过。
- **第②档（沙盒外执行，用户单独点头）**：`outside_sandbox: true` + `reason`。顺序固定：参数 / 硬拦表 → **规模预检**（`list_scripts`：项目内 `.py` / `.pyw` / `.R` / `.r` / `.m`，**按后缀保守纳入、读原始字节、不要求能解码**（cp1252 / GBK 声明编码的合法 `.py` 同样护着，判不出语言的 `.m` 也备份）；跟随目录链接按真实路径去重防环、不进 `ReadScope` 硬拦区 / 环境前缀 / `.git` `node_modules` `.venv` 等；
  **1000 个 / 32 MiB / 20 万目录项封顶，超限 `scripts_too_large`，不问用户**）→ 弹**单独的 danger 卡** → 点了才打快照 → `scriptlock.script_guards`（排序、非阻塞抢全、抢不齐退避，30 s 超时 `scripts_busy`，不起命令）→ **锁内读基线** → 起命令 → **锁内核对**，被改 / 被删的经 `snapshots.restore_script`（持锁、`write_script` 原子写、保留权限位）写回原字节。
  卡：`{kind:"approve", tool:"run_command", danger:true, summary:"…会在沙盒外运行，可以修改你电脑上的文件", command, reason, cwd, scripts:<备份个数>, sandbox:"outside", details}`；前端红色强调（`danger-tinted` 主按钮「在沙盒外运行」、次按钮「不了」，完整命令在可见区，理由与备份说明在折叠详情里以守「一句话读懂」）。只对这一条命令有效，不记成长期授权（再来一次照样问）。
  结果多 `scripts_backed_up: N` / `scripts_restored: N (…)` / `scripts_not_backed_up` / `scripts_restore_failed`（后者 `is_error`，`code=scripts_restore_failed`，让模型告诉用户）。**不自动升级**：第①档任何失败都不会让第②档无卡执行（`test_a_refused_write_in_the_sandbox_suggests_tier2_but_never_runs_it`）。
- **用户看到的 = 实际执行的**：命令（`argv` 拼成一行）超过 `CARD_COMMAND_CHARS`（8000）字符，或 `reason` 超 300 字，审批**之前**就拒（`command_too_long_to_show` / `bad_arguments`），两档都适用；卡上的 `command` 不截断（脱敏只会让它更短）。
- **进程树**：命令的直接进程退出（或超时被叫停）后，`_reap_tree` 结束并**确认**整棵受管进程树退出，才收输出、才核对 / 放锁。POSIX：命令在自己的新会话 / 进程组里，`killpg(SIGKILL)` 到 `ProcessLookupError`（组里一个不剩）才算干净；Windows：Job Object（`KILL_ON_JOB_CLOSE`）终止并等活动进程归零，起不来退回 `taskkill /T`（Windows 路径未在 Windows 上实测）。确认不了时结果带 `processes_left`。这样后台孙进程既绕不过时限，也不会在第②档核对、放锁之后才去改脚本。盲区：自己 `setsid` 逃出进程组的进程 POSIX 上追不到（Linux 沙盒内有 `--unshare-pid`，随 init 退出全被收掉）。
- **快照与可写清单同源**：快照覆盖 `writable_envs` 里**每一个**命令获准写入的环境（`change_python` 新建、只靠 `env_prefixes` 进来的都算；没登记 id 的现场登记 `cmd_env_<hash>`），不只会话创建时的前缀。
- **Windows / 无沙盒**：#1358 之前没有沙盒，第①档直接 `sandbox_unavailable`，只有第②档（备份 + 单独点头照旧）。
- **审计行**：多 `sandbox ∈ {hardened, outside, none}`（`none` = 没执行：被拒 / 没沙盒 / 未获同意）与 `command_tier ∈ {1, 2}`；命令、理由的文字**不进审计**（只记长度），`outside_sandbox` 布尔照记。执行前打快照（设置 + 目标环境 freeze）。
- **活动与看门狗**：前端把 `activity.tool=run_command` 映射成「正在运行命令…」；命令在跑是在途工具，不算卡住；等卡片也不算。
- **已知盲区（如实）**：
  - 第①档 Linux：**抽象命名空间 unix 套接字**仍可达（X11 在 Linux 上也走抽象地址；不 `--unshare-net` 才能联网装包）。seccomp-bpf 看不到 `connect` 的地址，不能「只拦抽象地址」；整个禁 `socket(AF_UNIX)` 会弄坏 asyncio / multiprocessing 且可用 `socketpair` 绕——不做。
  - 第①档：联网 + 能读项目与主目录 = 命令可以**外传它读得到的东西**；只有 `DENY_HOME_RELATIVE` 里的凭据位置被藏起来，项目里的 `.env` 之类靠硬拦表挡参数、挡不住命令自己去读。环境前缀与 pip / conda 缓存可写 = 命令能在用户环境里放任何东西（装包的本意），缓存可被投毒。
  - 第①档 macOS：sandbox-exec 是 Apple 标 deprecated 但仍可用的接口，`allow default` 基线只显式拒了上面几类；mach 白名单外的服务一律拒，个别系统工具可能因此异常，需要时走第②档或按用例放。沙盒内允许起子进程（包括 shell），硬拦表只看 argv[0]，所以 `python -c` 之类能做的事都在沙盒里。
  - 第②档的备份**只覆盖项目内的用户脚本**：其它文件、项目外、环境、数据文件不保护。命令期间用户在**外部编辑器**里保存同一份脚本（不走 `scriptlock`）会被当成命令的改动，结束后恢复成命令前的版本；命令把脚本换成同名目录 / 链接则恢复失败（如实报）；命令新建的文件不删；基线在内存里，Tavotto 在命令期间被杀就无法恢复。
  - 第②档持锁期间，界面里保存这些脚本会**阻塞等待**（不是失败），命令结束才落盘（最长 300 s 加快照时间）；锁只管走 `scriptlock` 的入口。审批卡开着期间的编辑不是盲区——基线在点头之后才取。

### D2b：「问与报」组 + 修复会话 + `POST /api/ai/fix`（`fixtools/ask.py`、`engine/fixsession.py`）

- **五个工具都 `waits_for_user=True`**：`propose_fix`（`approve` 卡，详情列 `ctx.reads` 的 `~/…` 写法与「没读：敏感目录」）、`ask_user`（`choice` 卡，回选项 id；`other` / `skip` 是保留 id）、
  `pick_file`（`pick` 卡；回答是路径，**先过 `ReadScope.grant` 的敏感硬拦**才加进本会话可读根，只放这一个路径，文件系统根不放）、
  `prepare_diagnostics`（`outward` 档，`send` 卡；工具**只弹卡**，打包与上传全走现成的「发送问题反馈」对话框，用户不点「发送」就不发，永不附源码——`include_source` 只是卡片上的提示）、
  `finish`（非阻塞，把结局交给会话）。`ctx.ask is None` / 回答畸形 / 抛异常 = 「未获同意」（`needs_approval`），超时 / 取消 = `no_answer`，绝不默认通过。
- **会话**（`fixsession.SESSIONS`）：同一时刻一个；id 是 `secrets.token_urlsafe`、比较 `compare_digest`、按项目 id 作用域（别的项目 404）。循环在**本进程**线程里跑（工具要碰应用状态），
  模型只有 `HttpModel` 一条出站路径；密钥只在内存，卡片文字过 `Redactor` + `readscope.scrub`，SSE `ai.fix` 只带 `{pj, fix_id}`（「重读」提示），状态以 `GET /api/ai/fix/<id>` 为准。
  卡片回答 `POST …/answer {card_id, answer, text?}` 按卡种类校验（坏回答 400、过期卡 409）；`POST …/cancel` 唤醒阻塞中的工具。预算：40 轮 / 30 分钟（含用户思考时间）/ 60 万 token；`finish` 之后不再请求模型。
- **连接**：`connection()` 只认当前选中的第三方 API 接口（有地址 / 密钥 / 型号、地址过 `check_base_url`）；只连了 CLI 时由 D3 的 MCP 接法接手（见下一节），`ai_fix_available` 随之为真。
- **验证**：会话结束后由前端重跑准备会话；`finish(fixed)` 只是 AI 的说法。诊断包 `detail-runs.json` 多 `fix_audit`（审计尾部，本来就不含内容）。看护 `tests/test_fixasks.py`、`tests/test_ai_fix_endpoint.py`。

### D3：CLI 经 MCP 修复模式（`engine/fixmcp.py`、`fixsession.py` 的 CLI 分支、`ai_agents` 的 `build_fix_command`）

只连了 Codex / Claude Code 时，同一个修复会话起那个 CLI；CLI 经 `tavotto fix-mcp`（`engine/cli.py` 的子命令，冻结产物里用同目录 console 版 `tavotto-cli`，`fixsession.mcp_launcher`）调同一张 `REGISTRY`。看护 `tests/test_fixcli.py`、`tests/test_fixmcp.py`、`tests/test_ai_fix_endpoint.py`。

- **选谁**：`POST /api/ai/fix` 先看 `fixsession.connection()`（API 连接，走内置循环，不变）；没有才取 `ai_bridge.fix_cli_agent()`（第一个 `usable` 且适配器覆写了 `build_fix_command` 的 Agent；通用层没有 `if agent == …`）。`ai_fix_available = connection() or cli_agent()`。
  命令由 `ai_bridge.fix_cli_command` 构造（与 `_cmd` 同一套：`resolve` 出的启动 argv、`spawn_overrides` 的第三方接口注入、`spawn_env` 的 PATH），再起进程用 `ai_bridge._popen`（自成进程组、stdin DEVNULL、UTF-8）。
- **启动参数契约**（令牌**不在**任何一个 argv 里，只在子进程环境 `TAVOTTO_FIX_TOKEN`）：
  - Codex：`codex exec -C <项目> --sandbox read-only --skip-git-repo-check -c mcp_servers.tavotto_fix.command=… -c …args=[…,"fix-mcp","--url",…,"--fix-id",…] -c …env_vars=["TAVOTTO_FIX_TOKEN"] -c …tool_timeout_sec=<卡片超时+120> -c …startup_timeout_sec=60 -c approval_policy="never" <提示词>`（`-c` 的值是 TOML；`env_vars` 只列变量名，由 Codex 从它自己的环境转给 MCP 子进程）。
  - Claude Code：`claude -p <提示词> --permission-mode dontAsk --allowedTools=mcp__tavotto_fix__* --settings <JSON> --mcp-config <JSON> --strict-mcp-config`；环境多一个 `MCP_TOOL_TIMEOUT`（毫秒，长于卡片超时）。MCP 配置里的 `env` 是 `{"TAVOTTO_FIX_TOKEN": "${TAVOTTO_FIX_TOKEN}"}`，由 Claude 从它自己的环境展开。
  - 提示词 = 内置循环**同一份** `SYSTEM_PROMPT` + 一段 CLI 补充（工具叫什么、结论只能经 `finish`）+ 首条消息，超 12000 字符截断（命令行长度）。
- **只读口径与已接受的风险（维护者裁决）**：两边的自带命令行都只许只读，所有改动只能经 MCP 工具。**Claude**：`--settings` 的 `permissions.deny` 里 `Read(~/<DENY_HOME_RELATIVE 每项>)` / `Read(~/<项>/**)`、`Read(**/<DENY_NAME_PATTERNS 每项>)` / `Read(//**/<项>)`、Tavotto 自己的 `config.json`
  （全部由 `readscope.deny_spec()` **每次现读表生成**，不手抄；Claude 把 Read 规则也用在 Grep / Glob 上）；另 deny `Edit` / `Write` / `MultiEdit` / `NotebookEdit` / `WebFetch` / `WebSearch` 与 `Bash(*>*)` / `Bash(*tee *)` / `Bash(*sed -i*)` / `Bash(*-exec*)`；
  `allow` 只有 MCP 工具、`Read` / `Grep` / `Glob`、与白名单里的只读命令（`ls` `pwd` `which` `file` `stat` `wc` `uname` `pip list|freeze|show` `conda list|env list`，及精确的 `python --version` 等）。
  **白名单故意不含 `cat` / `head` / `tail` / `grep`**：它们能绕过 `Read` 的 deny 读凭据位置，读内容走 `Read` / `Grep` / `Glob` 或 MCP 的 `read_file`。`id_*` 的 `.pub` 豁免在 Claude 的规则里表达不出来（`id_rsa.pub` 也被拦，偏严）。
  **Codex**：`--sandbox read-only`——写被沙盒拦，但它的自带命令行**读不受限**，`DENY` 管不到它；这是维护者「接受 Codex 这边的风险，两边都开放」的裁决，不是遗漏。Codex 的自带命令行不经 `run_tool`，所以不进 `fix_audit.jsonl`。
- **回调**：`GET /api/ai/fix/<id>/tools`（`[{name, description, inputSchema}]`，来自 `REGISTRY`，**不含 tier / waits_for_user**）与 `POST /api/ai/fix/<id>/tool {name, arguments}` → `{content, is_error}`。
  令牌头 `X-Tavotto-Fix-Token`；`security._session_guard` 在 cookie / 本机凭据之后多认这一种 scoped token，**只认这两个路径 + 对应方法 + 对应会话**（`SESSIONS.token_valid`）；Host / Origin 校验照旧在前。错令牌 / 别的会话 / 已结束 / 已取消 = 401（guard）或 403 `fix_tool_forbidden`（处理器）。
  `call_tool` 在 `s.tool_lock` 里串行执行 `run_tool(tool, s.ctx, args, s.audit)`；等用户点卡片的上限仍是 `ASK_TIMEOUT`，取消立即返回（`ctx.ask` 的取消）。
- **会话**：`SESSIONS.start(cli=构造器)`；`_work_cli` 起进程、读掉 stdout 丢弃（不入日志、不当结论）、轮询：取消 → 结束；`finish` 之后给 `FINISH_GRACE`（20 秒）收尾，到点结束；超 `FIX_LIMITS.max_seconds` → `budget`。
  结束只用 `ai_bridge._terminate` / `_await_exit`（POSIX `killpg` 自己的进程组、Windows `taskkill /T /PID`），**绝不按名字杀**。没调 `finish` 就退出 = `failed`，`reason ∈ {cli_no_finish, cli_exit, cli_start, budget}`（前端落到「AI 这次没能修好」那句，无需新文案）。
- **卫生**：MCP 配置只在本次启动参数里，**不写 `~/.codex`、`~/.claude`**（用例用假主目录断言）；Tavotto 不读、不存 CLI 的登录凭据；回环请求显式绕开系统代理；`fix-mcp` 的 stdout 归协议独占，没有令牌或 URL 不是 `http://127.0.0.1:` 就退出码 2。
- **真机核实（2026-10-24，Codex 0.163.0 / Claude Code 2.1.310，隔离数据目录 + 假执行器）**：两边整链都走通（CLI → `tavotto fix-mcp` → 回调 → `run_tool` → 弹卡 → `finish`），`env_vars` 转令牌、`${VAR}` 展开、`--strict-mcp-config`、`dontAsk` 均生效，`~/.codex` / `~/.claude` 未被写。真机发现并已修：Codex exec 模式下 MCP 工具默认要它自己的审批、没人点就一直挂着 → 本服务加 `default_tools_approval_mode="approve"`（要用户点头的改动仍由 Tavotto 弹卡）；Claude Code 2.1 已没有 `MultiEdit`（deny 里写它会报 matches no known tool）。仍知：Codex 没有 `--strict-mcp-config` 的对应项，用户自己在 `~/.codex/config.toml` 配的其它 MCP 服务也会出现在修复会话里（它们的工具仍走 Codex 默认审批，exec 模式下不会自动执行）。
- **没有可用环境的路（D3 真机后）**：`SYSTEM_PROMPT` 的 3b 条（内置循环与 CLI 共用）；`change_python` 的 `version` 可省，`FixContext.python_changer` 由应用注入 `executors.default_python_changer`
  （`pyversionfix.plan_python_change` 推范围 + `apply_python_change` 在受管环境另建一代并按联合计划补脚本依赖，建好后 `managedenv.python_of` 给新解释器、登记成环境 id）；`list_environments` 为空时文字里点名 `change_python`。看护 `tests/test_fixflow_noenv.py`。
- **进度与卡住**（`fixsession.py`，看护 `tests/test_fixstall.py`）：公开状态 `activity = {tool, phase: start|end|denied, detail, at_ms}`（`detail` 只取 packages / package / module / name、脱敏、≤60 字，路径与其它参数不进）、
  `stalled = {reason, detail}`（`reason ∈ cli_waiting_approval | cli_tool_not_delivered | stalled`）。来源：内置循环包装工具 handler 的开始 / 结束；CLI 每次 `/tool` 回调；CLI 结构化输出
  （Codex `exec --json`、Claude `-p --output-format stream-json --verbose`）经适配器的 `fix_event_parser()` 逐行解析成 `mcp_begin / mcp_end / approval / denied / output`——**只用于进度与卡住线索，从不当结论**。
  看门狗线程（每会话一个，`_watch`）：`DELIVER_WAIT`=20 s 内 CLI 报告的调用没收到回调 → `cli_tool_not_delivered`；出现 CLI 审批请求 → `cli_waiting_approval`（回调一到即解除）；`STALL_IDLE`=120 s 无工具在跑、无 CLI 输出 / 模型事件 → `stalled`；
  等卡片（`state=waiting`）与在途工具都不算。卡住持续 `STALL_HARD`=300 s 自动取消：`stall_code` 记下，`_settle` 判 `failed` + 该原因码；内置循环的模型请求挂住、循环线程回不来时 `FORCE_SETTLE`=10 s 后看门狗强行收尾（`_settle` 幂等，迟到的循环线程不再改结局）。
  卡住与自动取消、Claude 的权限拒绝（`cli_permission_denied`）都写 `fix_audit.jsonl`（`tool=_session`）。
- **Codex 的取舍**：`--ignore-user-config` 仅在 `endpoint_args` 非空（Tavotto 显式给了 provider）时加并带 `-m <接口型号>`；官方登录态不加。**没核实的**：Codex `--json` 里 MCP 调用 / 审批事件的确切字段名（解析器按 `item.*` 的 `type` 含 `mcp` / `approval` 写，且对未知事件一律当「有输出」）。
- **`change_python` 的旧代租约与回滚（Codex #1383 P2）**：换到新一代后 `deprepair._run_generation` 会 `managedenv.retire_unused` 删掉没人占着的旧代，所以 `change_python` 动手前给项目当前用的受管环境那一代
  （`managedenv.python_of` 与设置里记着的受管解释器）加 **保留型**租约 `envlease.acquire_retain`（`fix:<会话>`；与 native 占用在同一模块、两种用途：保留型只让 `deprepair._generation_in_use` 不清这一代，**不**被 `envlease.mutating()` 当成 native 会话拒绝——用 native 租约会把换代事务自己挡死；真正的 native 会话仍照旧拒绝换代），换成功则持到会话收尾（`_settle` → `ctx.release_leases()`，finish / 取消 / 失败 / 自动取消都走），没换成立即放（`release_retain`）。
  快照多存 `managed = {generation}`；`rollback` 先核旧代解释器真的在（不在 = `rollback_target_gone`，**一个字不改**），再恢复设置、`managedenv.activate` 指回旧代并让池 / 缓存放弃新一代，恢复后核设置里记着的解释器在不在（不在同样 `rollback_target_gone`，不报成功）。
  `FixContext.cancel`（= 会话的 `s.cancel`）传给缺省换 Python 执行器 `_default_python_changer(req, cancel)`，规划完、动手前先看一眼（与 #1381 同一件事）。看护 `tests/test_fixrollback_managed.py`。

