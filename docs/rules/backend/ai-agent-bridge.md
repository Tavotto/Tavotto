# 编码 Agent 桥

> 原文出自 `src/tavotto/AGENTS.md`「编码 Agent 桥」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_ai_data_recipe.py`、`tests/test_ai_data_template.py`、`tests/test_ai_data_propose.py`、`tests/test_ai_data_merge.py`、`tests/test_ai_structured.py`、`tests/test_ai_structured_cli.py`、`tests/test_ai_data_model.py`、`tests/test_data_figure_bench.py`、`tests/test_ai_data_roles.py`、`tests/test_ai_data_text_tables.py`、`tests/test_changeplan.py`、`tests/test_changeplan_endpoints.py`、`tests/test_ai_agents.py`、`test_ai_login.py`、`test_ai_bridge.py`、`tests/test_ai_probe.py`、`test_ai_capabilities.py`、`test_ai_refresh.py`、`test_ai_history.py`、`test_ai_idempotency.py`、`test_ai_revert_stale.py`、`test_codex_install_cli.py`、`test_managed_cli.py`
> `src/tavotto/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml；revert 只回滚本次 AI 那一版（细则见 `ai-agent-bridge.md`）

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 240
改到: `engine/ai_bridge.py`、`ai_agents.py`、`ai_providers.py`、`ai_structured.py`、`ai_data_*.py`、`changeplan.py`、`codexinstall.py`、`managedcli.py`、`resources/managed_cli_lock.json`、`/api/ai/*`、`/api/change-plans*`
主题: 编码 Agent 桥 → `ai-agent-bridge.md`
必守要点: 「支持哪些 Agent」只在 `AGENT_REGISTRY`；CLI 子进程一律 `spawn_env()`；绝不改写用户的 settings / config.toml
-->

- `POST /api/ai/run` → spawn 本机的编码 Agent CLI（`codex exec` / `claude -p`），
  cwd=figures 目录；面板 id 是 runtime asset id（ADR 0013）时经 `runtimeasset.resolve` 正向解析到脚本，解析不到回 `runtime_asset_unknown`，
  不走 `safe_resolve`（磁盘文件找不到才是 `ai_panel_file_missing`）；修改前快照到 `cache/ai_snapshots/`，结束后 diff 经 SSE
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
- **改图收尾的跟进建议（`task.suggestions`，闭集 `kind: legend_label`）**：内置执行器的 `check`（`ai_figure_edit.agent_check`）渲染后，按 stem × gid 比 manifest `editable` 的 `label` 值（`label_changes`，**不看 identity / 数据指纹**，与数据检查的结论互不依赖），记进 `cand.extras["label_findings"]`；提交后那次渲染补最终值；`_settle_task` 在成功（`completed`）+ 已提交 + 最终回到原文 + 图例项正显示该文字（`label_visible_in_legend`）时才写 `{kind, stem, gid, legend_gid, from, to}`（`legend_gid` = 最终版里 `legend_entry.source_gid` 绑定该曲线的可见图例项；可见性也只认这个绑定，别处同名文字不算），只含这两段已画在图上的文字。CLI 路径（Codex / Claude Code）提交前不渲染，看不到被改过的 label，没有这项。前端点「一起改」走画布 `setOverride(panel, legend_gid, 'text', to, true)`（写曲线 `label` 不会动图例），不经 AI、不写脚本。看护 `tests/test_ai_legend_label_followup.py`、`tests/test_ai_edit_check_app.py`、`web/src/components/ai/legendLabelNotice.test.tsx`。
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
  **协议（限定到 Codex）：Codex 只认 `wire_api = "responses"`**（新版 codex 读到 `"chat"` 直接整个启动失败）。
  `WIRE_APIS` 只剩 responses，`_clean` 默认 responses，**Codex 的注入** `spawn_overrides` 结构上永远不产出 chat，
  预设里没有任何 Codex chat 条目。**这条只约束 Codex**：内置循环另有 `openai-chat` 协议（记录的 `protocol` 字段，见下「协议探测与 openai-chat 连接」），它**只走内置、绝不注入 Codex**。**按协议分流，不是「国产一律走 Claude Code」**：只有 Anthropic 兼容端点的
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
  `GET {base_url}/v1/models`（`x-api-key` + `Bearer` 两个头、`anthropic-version`、`User-Agent: Tavotto/<版本>`——urllib 默认的 `Python-urllib` 会被 OpenCode Go 的 Cloudflare 403 掉、读成密钥不对，`test_models_probe_identifies_itself_not_as_python_urllib`；5 s 超时、不跟随重定向），
  只在用户点「从服务商获取」时才联网，设置页渲染不碰网络；成功结果按（接口 id, base_url, 密钥 sha256 前缀）
  缓存一天在 `config.data_dir()/ai_models_cache.json`；**密钥只发往已存地址同源（scheme + host + 端口，
  `ai_providers.live_models` 判，改了地址不带旧密钥 → `key_origin_mismatch`）、只走 https（回环 `localhost` /
  127.0.0.0/8 / `::1` 例外，字面判不解析 DNS，且回环 http **不经任何代理**（`ProxyHandler({})`，否则 HTTP_PROXY 会拿到明文密钥）；其他 http → `insecure_url`）**；任何失败只回稳定原因码（`no_key /
  key_origin_mismatch / insecure_url / timeout / unauthorized / bad_response / network`）、永远 200、不抛，界面保持预设 `models_hint` / 用户手填的列表。密钥只进请求头，
  日志只写接口 id 与原因码，缓存文件里没有密钥。看护：`tests/test_ai_catalog.py`、`EndpointDialog.test.tsx`。
  **地址少写 `/v1`（TVD-J5QG）**：型号清单自己补 `/v1`，OpenAI 系协议却直接拼 `{base}/responses`——中转站地址漏写 `/v1` 时型号拿得到、
  请求打到网站首页。`ai_catalog.api_root_fix` 是唯一判据：`GET {base}/models` 回网页且 `GET {base}/v1/models` 回 JSON（含 401 这类 JSON 错误）
  才改成 `{base}/v1`，其余不猜；`ai_providers.ensure_api_root` 每个地址只核一次（结论记 `api_root_ok`，改了地址自然失效；联网失败不记），
  只动 `base_url` / `api_root_ok` 两个字段，不碰账号登录 / Anthropic 系 / 旧协议记录。调用点三处：保存 Key 连接后、`ai_bridge.run` spawn 前
  （与 `ensure_fresh` 同在锁外）、AI 修复挑连接 `refresh=True` 时。内置执行器收到 2xx 网页 → `network/html_response` → 会话错误
  `agent_html_response`，失败卡主按钮「检查连接」。测试进程默认把 `_probe_kind` 桩成判不了（`tests/conftest.py`），测它的用例自己注入。
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
- **数据出图 v2 的画法配方（`engine/ai_data_recipe.py`，ADR 0178，实验开关 `ai.taf.enabled` 后面）**：被执行的脚本出自 Tavotto 模板，模型只交一份
  **闭集配方**（`FigureRecipe.from_dict` 严格：未知键 / 未知取值 / 层数 / `why` 不合格一律拒收，码是 `RecipeError.code`）；纯标准库、不执行、不写文件、
  不渲染（模板是后续 PR）。**唯一权威是三张表**：`MARK_RULES`（图元、汇总要求、是否露原始点、一句含义）、`REJECTIONS`（每个拒绝条件一个稳定码 + 一句话，
  `ERROR_CODES` 就是它的键，前端中英文文案与 `test_error_codes.py` 同步）、`FAMILIES`（十二类科学问题：列角色与类型、允许的图元、批次、专属拒绝码、「何时用 / 何时不用」）。
  `validate_recipe(profile, recipe, confirmed=, values=)`：结构规则只看 `FolderProfile`，取值规则（每组 n、非负、重复 x、dose > 0……）只在给了带行数据的摘要时才判，
  不给就不判（`eligible()` 把它们列在 `unchecked`，不假装判过）；`unit` / `paired` 只认 `Confirmed`（用户在对话里点选确认过的设计与个体编号列），
  `DataSummary.id_like` / `repeats` 只是线索、永不当已确认；`enforce_order` 在模型排序之后再强制一遍（柱加误差线不排在含原始点的画法前、不合格剔除带原因、剩不足 2 个如实返回）。
  `renderable(family)` 的出处是 `ai_data_recipe.RENDERABLE`（现在是批次 1 的四族 comparison、trend、relationship、distribution；`ai_data_template` import 时核它与 `RENDERERS` 逐项一致）。选图准则文字 `ai_prompt_pack.data_figure_guidance()` **由这三张表渲染**，不许另抄第二份规则清单
  （`tests/test_ai_data_recipe.py` 钉每个码都有句子、准则不出现表里没有的族 / 图元）。阈值全是 `ai_data_recipe` 的具名常量。
- **数据出图 v2 的大表（ADR 0178 修订·大表）**：超过 `adf.SMALL_BYTES` / `SMALL_ROWS`（5 MiB / 10 万行）的文本表格是**大表**，上限 `MAX_BYTES` 500 MiB / `MAX_ROWS` 6000 万；小表整条链逐字不变。
  大表读法 `read_text_table`→`_read_large_text_table`（流式：一遍校验 sha256 / 编码 / 二进制，一遍解析统计，摘要 `rows == []`、`large` 有整表统计；同一个 `_detect`、同一组错误码；`iter_table` / `iter_records` 是读法的唯一实现，`table_from_text` / `delimited_records` 是它们的整份文本版）。
  XLSX、v1、合画永远只认小表（`inspect_data_source` 对大表默认抛 `data_too_large`，只有传 `allow_large=True` 的出备选 / 提交路径认）。**`values.rows` 为空 ≠ 规则判过了**：`validate_recipe` 对大表只放行 `LARGE_MARKS`（趋势的折线 / 阶梯 / 点、关系的散点），汇总类图元一律 `mark_not_allowed`；数据相关的资格（折线 x 重复 / 没排序、系列 x 个数）在缩减那一遍判（`LargeRuleError`，码同小表）。
  画法：脚本读 `read_reduced`（趋势按 x 值分箱留包络、关系等距抽样 + 极值），之后与小表同一份画图代码；父进程 `large_reduce` 与脚本是同一份源码，并与概览扫描的整表统计对账。看护 `tests/test_ai_data_large.py`。
- **大表的进度与取消（ADR 0178 修订，`engine/ai_data_progress.py`）**：`tick` / `check` 是读表循环里的检查点（`_stream_check` / `_scan_summary` / 候选副本复制 / `reduce_file(progress=)`），`Cancelled` 是 `BaseException` 子类——新增的读表循环不许用 `except Exception` 之外的方式吞它，也不许在检查点之外自造取消；条目只有大表才有、有界、只含阶段名与数字。端点 `GET /api/ai/data-progress/<rid>` 与 `POST …/cancel`（幂等；缩略图与提交不可取消）。看护 `tests/test_ai_data_progress.py`。
- **数据出图 v2 的模板与期望指纹（`engine/ai_data_template.py`，ADR 0178）**：`render_recipe_script(entry, recipe, data_rel=, stem=, style=)` 只接受通过
  `validate_recipe` 的配方，不认识的组合抛 `TemplateError`、不画近似。**一份实现两处用**：读表 / 单元格解析 / 汇总 / 抖动 / 箱 / 核密度 / 拟合都是模块里的普通函数
  （`RUNTIME_FUNCTIONS`），这些函数的源码（`inspect.getsource`）与 `xlsxread` 的源码原文被收进**旁边的公共模块** `tavotto_plot_<sha256 前 8 位>.py`（`runtime_module()`，内容寻址、对所有图逐字节相同、不含数据，
  落盘只新建、同字节复用、异字节按损坏报错不覆盖——落盘见下一条），脚本只剩 import + 常量 + `main()`（`render_recipe_script` 返回 `Rendered`）；父进程的 `expected_fingerprints` 调用的是同一批函数；
  列名 / 表名 / 文件名只以 `repr()` 字面量进脚本（来源注释是 ASCII JSON，不含 `why`），脚本 AST 的标识符集合与数据无关。脚本不改 `sys.path`（两条入口都把脚本目录放在 `sys.path[0]`，`tests/bridge/test_bridge_generated_figure_script.py` 钉 native）。每个数据 artist 的 label 在整张图里唯一
  （`make_label`），验证器按 label 匹配。**可核 = 散点（points / jitter）、Line2D（line / step / hline_summary / fit_line / density / ecdf）、柱组（bar / hist）；不核 = errorbar / band / box / violin**
  （误差棒容器与 `fill_between` 在 manifest 里没有 label 字段）——`fingerprint_coverage` 如实列出，不折算成 verified；没有任何可核层 = 这张图的数据没法核。
  relationship / distribution 的规则（Freedman–Diaconis 分箱、Scott 带宽、ECDF 阶梯、在画出来的坐标里拟合的最小二乘线）各只有运行时里一份实现，可画的图元组合由 `validate_recipe` 与模板同口径拒绝（ADR 0178）。样式数字取自 `profiles.load()`（`style_from_profile`，取不到才用具名默认）；分类色是 Okabe–Ito（公开色盲安全色板）。看护 `tests/test_ai_data_template.py`（真 safe worker 对拍）与 `tests/bridge/test_bridge_generated_figure_script.py`。
- **图表脚手架如何消费 0178（`engine/chartrecipe.py`，ADR 0182 / #1877）**：单元格判据只认 `ai_data_figure.parse_cell` / `parse_date`——`chartdata` 不再自带数值正则；`NaN` 记缺失、`1_000` 记 1000、`Infinity` / 溢出记 `data_non_finite`，表格与网格上限 100,000 行 / 64 列（超出整表拒绝，`data_too_long` / `data_too_wide`）。合法性把 `ChartModel` 反推成 `FigureRecipe` 再跑 `validate_recipe`；表达不了的模型（image / 混合图元 / 多 x）记 `model_not_expressible`，不伪造配方。绘图值期望按脚手架实际画出的数组用 `ai_data_figure.digest` 算，gid 只取 `render_scaffold` 的 `roles[...]["primary_gid"]`（bar+yerr 是 `barseries_1`，不预测原生 index）；`errorbar` / `band` / `box` / `violin` 仍不可核（`ai_data_template.UNCHECKED_MARKS`），绝不折算成 verified。三类摘要（读到的字节 / 画出来的数值 / 模型结构身份）各留一个权威，不合并。看护 `tests/test_chart_data_cell_rules.py` / `tests/test_chart_recipe_bridge.py`。
- **数据出图 v2 的出备选与落盘（`engine/ai_data_recipe.default_candidates`、`ai_data_propose.py`、`ai_data_task.py` 下半、`/api/ai/data-figure/*`，ADR 0178，实验开关后面）**：
  不调模型的整条流水（模型路径复用它）。`default_candidates(profile, table=, sheet=, confirmed=, hint=, values=, picks=)` → `Candidates | NeedsChoice`：列角色规则只有 `pick_table` / `_resolve_roles` 两处文字定义，
  有歧义问、不猜，每个「不确定」有安全默认；`id_like` 永不当角色、没确认就没有 unit / 配对 / 轨迹；行号列（`DataSummary.row_index`：名字在小闭集里**并且**取值恰是从 0 / 1 起逐行 +1，两条缺一不可，不是 `id_like`）默认不当 x / y 但 `options` 里仍可选（列名在 `_REPLICATE_NAMES` 闭集里的重复号列同样处理，且不算「像条件」的线索）；大表默认备选全被「大表不支持」拒掉时，`_large_fallback_x` 带一个确定的横轴重算一次（只出趋势 / 关系类，仍全经校验；用户对横轴表过态则不走）；默认横轴只从「像条件 / 顺序」的数值列（`DataSummary.design`：块内递增块间重置 / 少量重复水平（含按组排好序的），只看取值结构、不看列名；整列单调本身不当线索，线索打架不用）里挑、默认纵轴只从「像测量」的列里挑，用户点名与 `options` 不受影响——ADR 0178 修订 2026-11-27；文案只走稳定码（`CANDIDATE_CODES` / `QUESTION_CODES` / `NOTHING_REASONS` / `DROP_REASONS`，前端 `ai:dataFigureV2.*`）。
  `ai_data_propose.propose` 把每个备选的脚本 + 公共模块 + 数据字节副本放进 `data_dir()/cache/ai_data_candidates/<session>/`（独立小项目，safe worker 真渲染，`write_roots` 只开候选目录；**用户文件夹零写入**，
  不用影子脚本是因为公共模块要在显示目录里 import，见 ADR 0178 对照；解释器先用项目已定下的（`peek_worker_python` + `pinned_interpreter`），失败静默回退默认链条并留 `interpreter_fallback`），候选数据指纹与 `expected_fingerprints` 对拍，单个失败只少一张卡、全失败回 `{kind:"nothing"}`；会话内存里、TTL 30 分钟、≤ 6 个、放弃即删（提交后保留到 TTL，同一备选重复提交靠 rid 幂等、换请求号建 `_2`）、同会话一份数据副本、
  删除不用 `ignore_errors`；会话属于项目。落盘 = 任务类型 `data_to_figure_v2`（`TemplateExecutorV2`）：只回传 `{session, id}`，项目锁内按「目录 → 公共模块（新建 / 字节相同复用 / 不同则 `common_module_conflict` 什么都不写）→ 脚本」
  **只新建**（`scriptlock.create_script`），再 `discover.register` → 统一刷新 → safe 渲染 → `figureverify.data_matches_expected`；登记失败撤回本次新建的脚本 / 公共模块 / 目录，复用的不删。
  数值列夹着 ≤ 20% 文字（`numeric_like`）：**先问用户**（`{kind:"ask", question_code:"mixed_cells"}`：预览 ≤ 5 行带文件行号、选项 skip / extract / describe），回答放 `answers.cells`（`skip` / `extract` / `map`，声明式；`describe` 不可执行 → 409 `propose_cells_need_rule`；规则不合格 → 400 `propose_cells_invalid`），规则进 `FigureRecipe.cells`，脚本读表后 `apply_cells` 套规则（内存副本，用户文件不动）、图下方写说明、期望指纹按处理后的数据算，用户已决定的格子不再算 partial 理由；幂等键含请求内容摘要。**没有任何可核层 = `data.verifiable` unknown（`data_check: "unverifiable"`），绝不写成 verified**。端点：`propose` / `thumbnail/<session>/<id>` / `commit` / `discard`，`/api/ai/data-files` 带 xlsx 工作表清单；
  懒渲染（ADR 0178 2026-11-16 修订）：`propose(render="lazy")` 不渲染、不提问（默认答案继续并在响应里写明），`GET thumbnail` 才画（`ensure_thumbnail`：项上的锁保证同一项只画一次、`_RENDER_SLOTS` 限 `MAX_PARALLEL`），画不出 422 `propose_thumbnail_failed`、提交该项 404；不带 `render` 的旧行为逐字不变——两条路径共用 `_render_planned`，不许各写一份渲染 / 指纹对拍；
  请求路径只经 `ai_data_propose.normalize_rel` + `projectenv.contained_path`。看护 `tests/test_ai_data_propose.py`（真 app + 真 safe worker）。
  合画（ADR 0178 2026-11-18 修订，`ai_data_merge.py`）：`propose(tables=[{path, sheet?}])`（2 到 `MAX_TABLES` = 8 份，与 `table` 互斥）把几份**列名与顺序都相同**的数据拼成一张逻辑表、末尾加一列合成的分组列（`file`，重名加数字后缀；值 = 唯一的文件标签），分组角色默认是它，其余走同一条流水；错误码 `tables_columns_differ` / `tables_limit_exceeded`。**拼表只有一份实现**——公共模块里的 `merge_tables`（脚本与父进程同源，不许另写一份），拼好的摘要仍经 `adf._summarize`；每份各按单份读法读、各管各的表头行；夹文字格的规则拼后套；用户文件只读、脚本落在最近公共父文件夹的 `tavotto_figures/`（只新建）、每份的 sha256 提交时各核一遍（`propose_data_changed`）、`input_refs` 每份一条；合画脚本的来源注释 `generator` 是 `data_to_figure/v2-merge`，严格解析器不放宽。脚本名由 `ai_data_merge.merged_stem` 在提交时按所选备选定（`<公共前缀>_<纵轴列>_by_file`，`STEM_SUGGESTER` 是留给模型的接缝）；文件里原本的分组列在每个文件内只有一个取值 → 忽略，有多个取值 → 现状与「只按文件比较」成对给（`items[].variant`）。单份请求的请求 / 响应 / 脚本逐字不变（改 `RUNTIME_FUNCTIONS` 会换公共模块哈希，同步 `PINNED_RUNTIME_NAME`）。看护 `tests/test_ai_data_merge.py`。
  提交的请求号占位（ADR 0178 2026-11-18）：`/api/ai/data-figure/commit` 在取会话 / `prepare_v2` 之前就对 `(项目, 请求号)` 占进程内在途表（`app._COMMIT_INFLIGHT`），同号的第二个请求回「未完成」（200，`deduped: true, final: false, outcome: null`）、不取会话；`finally` 一定清，所以不会永远在途；占位发生在任何耗时步骤之前，别把它挪到 `run_task` 之后。看护 `tests/test_ai_data_commit_inflight.py`。
- **数据出图 v2 的数据概览与脚本来源（`engine/ai_data_overview.py`，ADR 0178「数据概览与脚本来源」，实验开关后面）**：`GET /api/ai/data-overview?path=&sheet=&header_row=`
  只读：状态（`ok` / `unsure` / `unreadable` + 稳定码，读不了是 200 不是 4xx）、行列数、`fields`（`ColumnSummary.kind` 口径）、前 20 行单元格原文、表头行前的说明行；**读表必须是 propose 读表的那两个函数**
  （`read_text_table` / `inspect_data_source(v2=True)`），`header_row` 经 `ai_data_propose.parse_header_row`（与 `header_rows` 一份判据）——别在这里另写读法。路径只经 `normalize_rel` + `contained_path` +
  与 `discover.walk_files` 结果逐字比对，打开的是遍历结果里的那一项。响应一律有上界、控制字符换空格（表里的文字不可信）。读不了但能靠指定表头行补救的文本表（`data_ragged` 等）概览照带 `raw_lines` 与猜的行号（`raw_lines` 非空 = 前端可让用户点一行，二进制 / 散文为空），别在概览里提前丢掉 `read_text_table` 给的预览；XLSX 每个工作表的行列数在 `/api/ai/data-files?formats=all` 的 `sheets` 里
  （有预算，超出 `null` = 没读）。`/api/registry` 的 `all_scripts[]` 对 v2 生成的脚本加 `generated_from: {data, sheet}`：解析只有 `ai_data_template.parse_source_comment` 一处（严格：tokenize 真注释、第一条、固定键集合；
  只外传路径与工作表名，不给配方与哈希），开关关着整个不读、响应逐字不变。不写文件、不发遥测。看护 `tests/test_ai_data_overview.py`。
- **修改方案的脚本修改（ADR 0206 PR 6，`ai_bridge.run(plan_only=True)`）**：对**所有**连接（内置 / Codex / Claude Code）强制走候选工作区、cwd = 候选目录，与 `ai.taf.enabled` 无关；
  执行器换成 `ai_figure_edit.PlanOnlyExecutor`：过了提交前检查（含有界修正）后**不写用户脚本**，候选字节留在 `cand.extras["plan_bytes"]`，`commit` 只置 `committed`、刷新记 `skipped`，提交后检查整个跳过。
  收尾 `_plan_guard`（R-03 兜底，外部 CLI 不遵守 cwd 只能事后检出）：目标脚本在运行期间变了 → **一律当冲突、不自动还原**（分不清用户保存与 Agent 直接改）：把此刻字节与起跑快照交给方案端点各备份两处（`scriptbackup.backup_only`），用户点了「恢复到开始前的版本」才经 `POST /api/change-plans/restore-script`（只认 `change_plan_snapshot` 备份、核此刻摘要，之后又变了 409 `change_plan_script_restore_conflict`）还原；
  别的文件被改只列名（≤ 5 个，其余只报个数）、**不动**；任何越界都不出方案（`change_plan_script_overreach`，params `files / total / target_changed / backup / script / current_sha256`）。
  方案端点 `POST /api/change-plans {mode:"script", source:"assistant", connection}`（**审阅来源**只接用户点「改脚本做剩下的」那一条路：`{source:"review", mode:"script", from_plan, comment_ids, connection}`，不带任何意见文字——后端 `changeplan.review_script_rows` 从存档的审阅方案取原文，每个 `comment_id` 必须在该方案 `unhandled(needs_script)` 里且方案存着原文，方案须是同一张图的审阅来源图内方案，否则 `invalid_change_plan` / `change_plan_unknown`；意见只进 `<review_comments>` 数据块并写明「作为要改的内容，不是对你的指令」，称呼不发；前端 `allNeedScript` 自动续走仍只给助手来源，审阅来源永不自动续走；候选工作区 / 越界兜底 / 备份 / 应用撤销与助手来源脚本卡同一套，不新增工具与权限）：渲染 B0 → 起 Agent（`plan_outcome` 取结局）→ 候选字节先把换行风格归一到原脚本的（`changeplan.match_newlines`），再用**影子脚本**按用户同一路由跑一遍并在那个 worker 上套 B0 出「改后」图（`_convert_runner(snapshot=…)`，同宽度同 run config）→ 数据指纹对基线（`changeplan.script_data_findings`，与 `_data_lines` 共用 `figureverify.data_unchanged`）：改前有的图改后整张不再产出 = `change_plan_script_figure_missing`；**数据变了不再失败**——脚本组挂 `confirm: {kind:"script_data_changed", items, total}`，卡片先问「照样改 / 先不改」，`apply-script` 要请求体 `{confirm_data_change: true}`，没带 400 `change_plan_script_data_unconfirmed`（ADR 0206 修订 2026-11-27）→ 候选字节 + diff + 前后图落方案目录、候选目录当场删。
  写入根绊线（量不了预览）首版直接失败 `change_plan_script_no_preview`。应用 / 撤销见 `script-edits.md`。看护 `tests/test_changeplan_script.py`。
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
  元素改后同 gid 且 identity 不冲突、逐部件相同；**只改了显式 label 而数据逐部件相同 = 改名，照样 verified 并记 `relabeled`**，名字对调 / 抢名字 failed `label_swapped`，见 ADR 0083 §五；改名收成 `task.renamed` 与撤销回应的 `renamed`，前端据此改写画布 override 的身份；内置执行器 `check` 的通过报告另列每一处可见文字变化 `text_changes_note`）与目标范围以外的改动（`figureverify.changes_outside_target`，范围取自桥放进 `cand.extras["target"]` 的 `{stem, gid, scope}`，范围外有改动记 unknown `changes_outside_target`、结局最高 partial；派生改动的排除见 `05-verification-design.md` 第七节）。`before` 钩子给的是**无 override 重放**（`worker.override(stem, [])`，不执行用户脚本）而不是落盘 manifest——后者带着用户 override，改后 build 不带；坐标范围 / 版式的目标判定不比、列进
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
候选工作区（含 `project_context/` 里按项目相对路径复制的文件）在 Windows 上可能超 260 字符：Tavotto 自己的 mkdir / 读写 / 遍历 / 删除一律经 `engine/longpath.py` 的 `long()`（`\\?\` 前缀，非 Windows 原样）；交给 Codex 的 `-C` 与提示词里的路径保持普通形式。仍放不下（`WinError 206` / `ENAMETOOLONG`）时 `create_candidate` 回闭集码 `workspace_path_too_long`（400，前端一句话 + 「关闭」），不是 `internal_error`。

校验的写入绊线错误沿 `probe.error.params.worker_code` 传给转换层，不能被通用
`script_probe_failed` 丢掉原因。转换层统一提示候选 / 临时目录的写入边界，删除仅为输出准备的
目录创建，保留正常 Matplotlib `savefig` 捕获；不沿用「改写到项目里」的通用 MCP 指导，也不按错误文本猜原因。

**项目依赖上下文（2026-10-25，ADR 0143）**：`convertcontext` 只读静态提名 helper/字体/数据，按预算复制到
`project_context/`；`context.json` 记原项目相对路径、来源/视图各自完整 SHA256 与未覆盖项，不扫描拷贝整个项目、
不跟随链接、不读敏感路径或环境；文本视图经同一 `readscope` 脱敏并标注。内置 `list_dir(path)` 可浏览子目录，写仍只有一个候选；check/留存/续跑/采用均核
起跑资源与快照摘要。生成代码使用原项目资源位置；看护 `tests/test_convert_context.py`（含读时换链接、资源漂移与删缓存后重开）。
`input_read_policy` 说明是否有已确认的项目读取映射，不暴露目标路径、不复制外部数据；映射只覆盖缺失的只读打开，
不覆盖 exists/glob。生成代码保持原输入路径串，让读取失败进入校验修复，不因快照缺项新增静默跳过分支。
同一 `inputremap.fingerprint` 绑定起跑读取策略；检查、续跑与采用拒绝映射漂移。旧清单无字段仍按旧契约处理。

转换运行检查与采用后重开共用发现逻辑的首个入口；主入口失败或不出图，不能改跑辅助函数取得部分图后放行。
真实错误交回既有有界修正流程。看护 `tests/test_ai_convert_app.py`（真 worker 主入口失败、辅助函数可出图、修正后采用）。

**输出完整性线索**：转换检查用 `convertcontext.file_reference_hints` 列原源码的静态文件名字面量，
再列本次 worker 的改道写入尝试；两份都只保留经 `readscope` 脱敏的 basename、去重、限量并标记截断。
前者可能是输入，后者可能失败或之后被删除；动态路径、helper、临时目录、C 扩展和外部程序也可能漏记。
没有 worker 记录是 `null`，不是空列表；完整性始终 `unknown`，不能从出图或名单推断所有输出 / 内容等价。
线索随 comparison 留存并交给 check 与采用前界面，项目原件仍不受校验写入影响；看护
`tests/test_convert_output_evidence.py`（真实 worker / 暂存恢复 / 删除反例）。

**缺输出判定（#1809）**：R / MATLAB 来源里**字面量路径、位于顶层普通语句**的写出调用（`convertsrcwrites.certain_writes`：R 的
`write.csv` / `saveWorkbook` / `write_xlsx` / `saveRDS` / `cat(file=)` 等，MATLAB 的 `writetable` / `xlswrite` / `fopen(..,'w')` 等；
图形输出与读取不算，变量 / 拼接 / 分支 / 循环 / 函数体 / 绝对路径 / `..` 一律不算）才是「能确定的写出」。每次校验运行前清空改道区，
运行后以改道区里**最后还在**的文件名为准（写了又删、写失败都算没写出）；有缺项时 `run_validation` 回 `status: "missing_outputs"`
（其余字段同 ok），不当 ok。`ConvertAdapter._render` 只在「编排器接着真会修正」（`_correction_pending`：`max_corrections` 与
`corrections` / `agent_runs` 余量）时记 `output.source` / `source_outputs_missing` 失败，进有界修正，提示只含缺的文件名；
修正之后仍缺**不判失败、不挡采用**，缺项随 `comparison.output_evidence` 带出。该对象新增字段：`source_writes` / `missing_outputs`
（`null` = 没法判定：非 R / MATLAB、没有能确定的写出、无改道记录、沙盒 cwd 下 worker 没回报或回报被截断；`[]` = 都写出了；否则
`[{name, line}]`）/ `missing_truncated` / `output_check`（`written` | `missing` | `unknown`）/ `corrected`。`completeness` 仍恒 `unknown`：
文件名在不证明内容和计算一致。沙盒 cwd 下合并改道区与 worker 回报的 `sandbox_files`（任一里有就算写出）。内置执行器的 `check` 对同一组缺项只报一次，再次出现照常放行。看护 `tests/test_convert_missing_outputs.py`
（静态抽取正反例 / 真 worker 判定 / 成功、补齐、仍缺、无法确认四种终态）。

**内容核对（#1809 第二层）**：候选写出了的「能确定的写出」，若项目里已有同名旧结果（脚本旁优先、其次项目根），`convertcompare`
在校验窗口内（候选文件还在改道区；沙盒 cwd 的文件由 worker 先拷进改道区 `.tavotto-sandbox/`，各 ≤ 8 MiB、合计 ≤ 64 MiB）逐个比：
csv / tsv 按单元格（数值相对 1e-9 / 绝对 1e-12，文本原样，行列数须一致），xlsx 用标准库读（逐表顺序、逐格值、**不信 `<dimension>`**，
不比样式，不依赖 openpyxl），txt / log 逐行原样；不比原始字节。每个文件 `files[]` 一行：`state` = `missing` | `match` | `differs` |
`incomparable`；`differs` 带 `diff_count` 与至多 5 处坐标（`{sheet, cell}` / `{row, col}` / `{line}` / `{kind: shape | sheets}`，**没有值**）；
`incomparable` 带 `reason` ∈ `no_previous` / `stale`（旧结果 mtime 早于候选这次读过的任一输入，输入 mtime 来自 worker 回报的
`inputs_read`）/ `format` / `too_large`（超大小 / 时间 / 单元格预算）/ `inputs_unknown`（观察记录缺失、截断或为空）/ `compare_failed`
（比较器自身异常——任何异常都只是 incomparable，不是校验失败）。内容差异**不**自动进修正：「让 AI 再对一次」是用户点的一次有界修正
（`POST /api/ai/convert/<sid>/realign` → `prepare_realign` / `run_realign`，提示只有文件名、坐标和计数；修正前整份备份，没产出新的
可采用候选就原样还原，仍 `ready`；用户中途放弃才删）。`status.realigning` 为真时采用 409 `convert_resume_busy`。
采用 `POST /adopt` 可带 `specified_outputs: [{name, file}]`：只认清单里 `missing` 的名字，`file` 只留 basename，登记进 meta 与采用回执，
被指定的文件一个字节都不动。看护 `tests/test_convert_output_content.py`。

**R / MATLAB 来源（ADR 0143 修订，2026-10-23）**：`POST /api/ai/convert` 多一个可选 `source_language`（`python` 缺省 | `r` | `matlab`）。
r / matlab 时 `library` 必须缺省，后端用 `sourcelang.detect`（纯标准库；`.R` / `.r` / `.m` 纯文本、≤ 1 MB、UTF-8；`.m` 带 Objective-C 特征或
MATLAB 特征不足一律 None）复核文件与声明一致，对不上 400 `convert_source_language_mismatch`。**不运行 R / MATLAB**：对照只用请求给的 `path` 或脚本旁
同名位图（`ai_convert.sibling_original`），没有原图时 `comparison.reference=null`、`verification="unknown"`（界面说一句“请并排看一眼再采用”）。
提示词是 `ai_prompt_pack` convert 一节的变体（逐步搬运 + R→Python / MATLAB→Python 对照表）。Python 里没有可靠对应时 Agent 首行写
`TAVOTTO_NEEDS_INPUT: <原因>` → `ConvertExecutor` 抛 `ai_tasks.NeedsInput` → 结局 `needs_input`（完整导出指引清洗后限长 8,192 字，只进 `unmet[].detail`，
不进日志 / 遥测）；已写出可运行草稿也不能覆盖明确报告的数据/计算语义缺口，不进入采用。纯视觉未知项独立验收，见 ADR 0143「流程验收与视觉验收分开」；无标记照旧检查候选。新脚本缺包：校验运行前 `convertdeps.ensure(project, rel, cancel)`（app 对所有来源注入 `task_hooks["deps"]`；`cancel` = `TaskRun.cancel_event`）
走 `deprepair.joint_plan_for` → `create_joint_plan` → `prepare`（不新写 pip 路径；取消经 `deprepair.cancel_status` 传到安装）。**名单**：只装 `engine/autoinstall_dists.txt`
（PEP 503 名；14 个种子 ∪ 30 个常见大包 ∪ PyPI 30 天下载前 1000（ClickHouse 公开 PyPI 数据集）− 排除名单（pip/setuptools/wheel 等管理环境与打包工具），`scripts/dev/refresh_autoinstall_list.py` 生成，spec datas 带上）里的包；名单外的包不自动装。**每个环境问一次**：
`project_venv` / 已存在的受管一代名单内直接装；系统 Python / 内置 runtime 永不装；其余环境（Conda / 指定 / 环境变量 / 用户采用）第一次出卡片，点主按钮才记
`ai.convert.auto_install_envs`（解释器路径 sha256 前 16 位 + 种类 + 名，无明文路径；设置页可撤销），装的动作是现有链条的标准动作（受管环境）。
**不自动装的分支都是真卡片**：`ConvertAdapter.hold` → `NeedsInput(reason_code="dependency_decision", data=card)`（`ai_tasks.NeedsInput` 带 `reason_code` / `data`，进
`unmet[]`），结局 `needs_input`、候选不删（`meta.json` `awaiting_dependencies`）；`POST /api/ai/convert/<sid>/dependencies {action:"install", impact_digest}` 起作业
（`ai_convert.prepare_resume` / `run_resume`：装 → 校验**同一个候选** → 留存；装依赖这一步不重跑 Agent，但装好后校验才暴露出**可修正**失败〔`ai_convert.correction_prompt` 闭集，如写项目目录被绊线拦下〕时，`run_resume(correct=ai_bridge.correct_convert)` 让原会话同一连接〔`meta.json` 的 `ai`〕的 Agent 修正**恰好一次**：起一个 Task Core 会话〔`convert={"resume": …}`、`envelope_for(correction_round=True)` 预算 1 次 Agent / 0 次修正 / 1 次校验、`parent_task_id` 指回原任务〕在同一候选上改，由它的提交前检查校验并 `stage`；仍不过 → `failed`，不循环；不可修正 / 取消 / 仍缺依赖 / Agent 起不来 → 行为不变），`discard` 取消正在进行的安装或修正（取消传到修正会话，进程组一并结束）。换了环境（系统 / 内置卡选完环境且解释器变了）前端发 `{action:"replan"}`：`run_resume(ensure=convertdeps.ensure)` 按此刻环境重判同一个候选（不带摘要，复用同一续跑作业与忙碌判定）。授权口径两种：点过卡片 = 前端回显卡片摘要（服务端不自回显，
对不上回新卡片）；已记过同意的环境 = 服务端回显同次调用算出的摘要（维护者 2026-10-23 裁决）。开关 `ai.convert.auto_install=false` / `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1`
（关着或计划不可执行时没有可执行动作，校验以 `missing_dependency` 失败，不修正）。
运行时缺包（ADR 0143「运行时观察到的缺包」）：校验运行报 `missing_dependency` 且 `params.module` 点名时，`ai_convert._runtime_dependency_retry` 把模块名并入 `convertdeps.ensure(runtime_modules=)`（同一棵决定树，不新写 pip 路径），装好后只重跑校验一次；卡片 `runtime_modules` 存进 `dependency_card`，`run_resume` 取回并传给 `resolve/ensure` 重算计划。
晚到的停止（ADR 0143 修订二）：`run_resume` 里依赖步骤返回 `installed`（过提交点，取消被拒）而取消标记已置位时，`run_validation` 在 `_ensure_dependencies` 处返回内部状态 `installed_stopped`，
续跑把候选留成 `meta.json` `deps_installed`（不删、不自动校验、`installed` 如实记录），`prepare_resume` 也接受这个状态；用户点继续 = `{action:"replan"}`。依赖步骤返回 `cancelled`（或没装过东西）才删候选。
前端续跑传输错误一律先 `GET /api/ai/convert/<sid>` 对账再决定，后端 POST 不做幂等（409 即对账触发点）。
会话元数据：sidecar 与 `meta.json` 记 `source_language`；`library` 在 r / matlab 时为 null；遥测不新增事件。看护：`tests/test_sourcelang.py`、
`tests/test_ai_convert_foreign.py`、`tests/test_ai_convert_foreign_app.py`、`tests/test_convertdeps.py`。

### Codex 运行事实（TVD-BCZ0，`ai_agents.CliRunFacts`）

第三方 CLI 的内部步骤看不见（exec trace 的 `agent_run` 是 `opaque`），几分钟的会话判不了根因。`CodexAgent.run_facts()` 给出的
`CliRunFacts` 在 `ai_bridge._consume` 里与 `_classify` 读**同一条** stdout 逐行累计，**只有数字 / 布尔 / 闭集码**：事件数、
命令数 / 失败 / 被拒（`status=declined`）/ 起了没结束、文件改动数与工作区内外（`-C` 目录，只给计数，不给路径）、是否改到目标文件名、
`error` / `turn.failed` 次数、最后事件与最后 item 类型（`CLI_EVENT_TYPES` / `CLI_ITEM_TYPES`，集合外归 `other`）、首个事件延迟 /
最长无事件间隔 / 末尾空闲（毫秒）、接口是 `official` 还是 `relay`（只看 base_url 有没有，不带地址与标签）。判据只用事件里**结构化**字段，
不匹配自由文本（Codex 的 error 没有结构化 code，所以只计数）；唯一例外是 `fail_status`：最近一次 `error` / `turn.failed` 的 message **开头**是 Codex 自己拼的 `unexpected status <三位数>` 或 `exceeded retry limit, last status: <三位数>` 时只取那个 100–599 的数字（`codex_http_status`，原文不留；`merge_run_facts` 取最后一个非空的），进 `cli_fail_status`——第三方中转回 400 / 404 这类不在失败分类表里的状态时，诊断包靠它分清「协议不认」与「密钥 / 额度」（2026-11-14 一份用户反馈：中转每次几秒就失败、包里定不了根因）。写入两处：`agent_run` span 的 `cli_*` / `endpoint_kind` attributes
（登记在 `exectrace.ATTRIBUTES`，词表由 `ai_bridge` 用上述常量登记），与「AI 会话结束」日志（`_cli_log_facts`，多次进程用
`merge_run_facts` 合并；码类经 `logsafe.known` 放行）。新增事实先加常量集合 / ATTRIBUTES，再由 `tests/test_codex_run_facts.py` 钉住。
会话里的 `cli_runs` 是 `SESSION_INTERNAL_KEYS`，不进任何响应。

**「改到目标文件名」（`target_touched`）只看得见 `file_change` 事件**：模型把 `apply_patch` 当 shell 命令跑、或用重定向写文件时事件流里只有 `command_execution`（真模型复现时见过写成了而 `target_touched=False`）。所以进程结束时另记工作区根下目标文件此刻`target_on_disk`（普通文件、不是符号链接）与 `target_bytes`；判「到底写没写出来」以这两项为准。

### 转换任务的写入被拒 / 时限 / 长静默（TVD-BCZ0）

- **提示词**：`ai_prompt_pack.convert_prompt` 对所有来源加 `rule_write_fail`（写目标文件失败就说明原因并停，不换位置写）；R / MATLAB 来源另加
  `rule_foreign_no_search`（这台机器不运行 R / MATLAB，不查找安装或源码，不做全盘 / 主目录搜索，只读工作目录与原项目目录）。看护：
  `tests/test_ai_convert_foreign.py::test_convert_prompts_forbid_searching_…`。
- **失败码 `agent_write_denied`**（`ai_tasks.UNMET_REASON_CODES`）：提交前检查 `candidate.target` 判“目标缺失”时，若本次运行事实
  （桥在每个 CLI 进程结束时把 `merge_run_facts(cli_runs)` 放进 `cand.extras["run_facts"]`）显示 `file_changes_failed > 0` 或
  `commands_declined > 0`（`ai_convert.write_denied`），原因码是 `agent_write_denied`，否则仍是 `target_missing`。它**不可修正**
  （`convert_fix_text` 不收它，不再起一轮 Agent）。前端 `convert.fail.agent_write_denied` 一句话 + 「重试」，文案走既有 `convert.fail.<reason>` 映射。
  **第二种形状（Windows 沙盒起不来）**：命令在启动前就被策略拒绝，事件流里没有 `command_execution`，上面两个计数都是 0。
  `write_denied` 另判：`events > 0` 且 `last_event == "turn.completed"` 且 `commands == 0` 且 `file_changes == 0`（有命令没写入仍是 `target_missing`）。
- **Windows 上的 Codex 沙盒（TVD-BCZ0 根因）**：Codex 0.164.0 在 Windows 的默认沙盒起不来，模型自述“当前会话为只读沙箱，所有命令在启动时被策略拒绝”，
  连 `pwd` 都不行，正常退出 → 转换判 `target_missing` 或回 `TAVOTTO_NEEDS_INPUT`（Lab run `20261101T084752Z-227df5`）。同环境只加
  `-c windows.sandbox="unelevated"`（受限令牌，不需要管理员；`elevated` 要建沙盒用户，不采用）后两次转换分别跑了 39 / 59 条命令并写出目标文件
  （Lab run `20261101T090337Z-e765ae`）。`CodexAgent.build_command` / `build_fix_command` 在 `os.name == "nt"` 时追加
  `ai_agents.CODEX_WINDOWS_SANDBOX_ARGS`（本次调用级覆盖，**不写用户的 `~/.codex/config.toml`**）；`endpoint_args` 里已显式给了 `windows.sandbox`
  就不重复。非 Windows 不变。看护：`tests/test_ai_agents.py::test_codex_argv_*`、`tests/test_windows_regressions.py::test_codex_on_windows_runs_with_unelevated_sandbox`。
- **总时限**：`ai_convert.convert_timeout_s(lines) = min(1800, 900 + 2 × lines)`（`source_line_count` 数原脚本行数）。`envelope_for` 把它写进
  `budget.max_wall_s`，并把 `timeout_extra_s = 时限 − 900` 记进候选 `extras`；桥的进程看门狗用 `TIMEOUT_S + timeout_extra_s`。没有候选的任务
  （改图等）额外为 0，仍是 900 s。
- **长静默进度**：进行中的 `CliRunFacts` 挂在会话 `cli_live`（`SESSION_INTERNAL_KEYS`，进程结束摘掉）。`ai_bridge.get()` 对 running 的转换会话在
  `task.activity` 里给闭集值 `writing_long`，判据 `CliRunFacts.generating_silent(45 s)`：收到过事件、无未完成命令、最后事件不是
  `turn.completed` / `turn.failed` / `error`、最后 item 是 reasoning / agent_message / file_change / 已完成的 command_execution（或刚 `turn.started`），
  且静默 ≥ `LONG_WRITE_IDLE_MS`。只用结构化字段；事件一来静默清零，下一次轮询自然恢复。前端 `convertStore` 轮询时把它写进条目 `activity`，
  进度行只换文字（`convert.writingLong`），不加横条 / 弹窗。
- **转换的任务清单（2026-11-25，用户拍板）**：`ai_bridge.get()` 对 running 的转换会话另给 `task.stage = {step: read|write|verify, round}`
  （`_convert_stage`，闭集码 + 整数）：编排器在提交前 / 后的校验阶段（`TaskRun.phase` ∈ verifying / ready_to_commit / committing）= verify；
  否则候选里的新脚本有内容 = write，没有 = read；`round` = 1 + 已记账的自动修正次数（修正时回到 write、轮次 +1）。内置执行器写到一半自己要的
  `check` **不改** `TaskRun.phase`，所以仍算 write（用户裁决：它还在写，清单不来回跳）。`TaskRun.phase` 只观察、不参与判定。前端
  `convertStore` 轮询时写进条目 `stage`，准备卡显示「读懂原脚本 → 写 Python 版本 → 运行校验」（`ui/TaskChecklist`）；「和原图对照」是 ready
  之后用户自己做的事，不进清单；缺包仍是那张装依赖卡，装好继续时清单停在「运行校验」。看护：`test_ai_convert.py::test_convert_stage_reads_phase_target_and_corrections`、
  `test_ai_tasks.py::test_run_phase_follows_the_orchestrator_for_progress_ui`。
- **改图的任务清单（2026-11-26，用户拍板）**：改图会话（非转换、非 `plan_only`、`task_type` 为空或 `figure_edit`）由 `_EditStage` 记
  `sess["stage"] = {step: read|edit|verify, round}`（进 `SESSION_DTO_KEYS`，刷新后接回）并推 `ai.stage`。判据只用文件事实：目标脚本（TAF / 内置是
  候选里那一份，否则是脚本本身）与开始时字节不同 = edit（每个输出行最多每秒读一次）；Agent 进程退出 = verify；有界修正起新进程 = edit、轮次 +1。
  只前进，emit / 读文件失败都不抛。不用事件流的 `file_change`（shell 写的改动看不见）。前端 `Transcript.editSteps`：进行中按它勾，改成功收尾全勾、
  清单留着；没改 / 失败不画。有清单时状态那句话在清单标题上，状态行只留执行器与耗时。看护：`test_ai_figure_edit.py::test_edit_stage_*`。

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

## CLI 失败归类（Codex / Claude，`ai_agents.codex_failure_class` / `claude_failure_class`）

非零退出默认写 `agent_exit_nonzero`；适配器的 `classify_event` 只凭**结构化字段**把失败归到 `RUNNER_ERRORS` 里的 `agent_auth` / `agent_quota` / `agent_network`，写进 `state["runner_error"]`（`ai_bridge` 对所有 Agent 取它）。
Codex 只认 `turn.failed.error` 的 `message` 开头 Codex 自拼的 `unexpected status <三位码>`（真机 0.163.0 无结构化码字段；401/403 auth、429 quota、502/503/504 network）；Claude 只认 `assistant.error`（`authentication_failed` / `rate_limit` / `billing_error`）与 `result.api_error_status`。
**不匹配任何自由文本、不保留原文**；字段没有或不认识就不判。失败卡「错误码 → 主按钮」的闭集表是前端 `AGENT_ERROR_ACTION`，与 `tests/golden/ai_error_action_vectors.json` 前后端对拍（`tests/test_ai_cli_error_classes.py`、`transcriptModel.test.ts`）。
Codex 的 **404** 归 `agent_model_unavailable`（只有 CLI 适配器产出的码在 `ai_agents.CLI_RUNNER_ERRORS`，不进与 `DONE_REASONS` 对齐的 `RUNNER_ERRORS`）：旧版 codex 被选了它不认识的新型号。**只在走 CLI 自己的登录时采纳**——桥在 `_consume` 起点把 `endpoint_backed`（`spawn_overrides` 真的产出了注入，与 `_agent_caps` 同一判据）放进分类状态，第三方接口下的 404 多半是地址错，不归这一类。没走 TAF 的会话失败时同样把 `runner_error` 写进会话 `error`（否则失败卡只剩「失败」、主按钮对不上）。失败卡主按钮 `update_cli`：能一键更新 →「更新 Codex」；不能 →「换回默认型号」，怎么更新进「详情」；都没有 →「重试」。

## CLI 比模型清单旧（`cli_update`，2026-11-04 用户反馈）

同一家族的 CLI 共用一份目录缓存（Codex 桌面版 / IDE 插件 / 命令行都写 `models_cache.json`），桌面版先升级会把新型号写进去，Tavotto 启动的旧 CLI 选到就 404。
判据：适配器 `catalog_client_version()`（Codex = 缓存顶层 `client_version`；默认 None = 不提示）严格新于已解析 CLI 的 `--version`（`ai_agents.older_than`，任一判不出就不提示）；第三方接口接管时不算。
capabilities 的 agent 与订阅连接加字段 `cli_update: {current, seen, source, action}`，`action` 闭集 `ai_bridge.CLI_UPDATE_ACTIONS`：
`switch` = 候选里另有一份版本 ≥ 清单的；`install` = 托管锁版本 ≥ 清单（锁追不上清单就不给，装了也没用）；`manual` = 都没有，前端按 `source` 说怎么更新。
来源是 `custom`（用户自己指定的路径）一律 `manual`：不替用户换掉他选的那份，托管安装在候选里排在 custom 后面、装了也顶不掉，下次照样 404。
`POST /api/ai/agents/<id>/update` 现算同一份计划：switch 经 `set_agent_path_override`（同一套 `--version` 验证）、install 走 `start_install`、其余 409 `ai_cli_update_unavailable`；请求体不接受路径或包名。看护 `tests/test_ai_cli_update.py`、`web/e2e/ai-cli-update.spec.ts`。

## 内置执行器（`engine/agent_runner/`，ADR 0147，第一阶段核心循环 + 第二阶段已接入改图）

改图助手自己跑「模型 ↔ 工具」循环的独立子进程，不借 Codex / Claude Code CLI。第二阶段（「连接 AI」改造）已把它注册进改图执行路径，
见下一节「连接」；`AGENT_REGISTRY` 仍只有两家 CLI。看护 `tests/test_agent_runner.py`、`tests/test_outbound_https_trust.py`、`tests/test_ai_connections.py`。

- **边界**：纯标准库、3.10 可跑；不 import 科学栈，**不 import `ai_bridge` / `ai_agents` / `ai_tasks`**；内部依赖只有 `tlstrust` 与纯标准库的 `ai_prompt_pack`（convert 提示词的同一份文字）。
  只经 stdout 事件行与父进程说话。入口 `python -m tavotto.engine.agent_runner --cwd <候选目录> --target <相对路径> --wire <名> --base-url <url> --model <名>`。
- **会话 id**：每个请求（所有协议）都带 `x-opencode-session: <本进程一次生成的随机 32 位十六进制>`——一个 runner 进程 = 一段对话；OpenCode Go 不带就 400 `MissingSessionID`，别家忽略。只是随机数，不含用户信息、不跨进程复用。看护 `test_every_request_carries_one_stable_session_id`。
- **协议**（`wires.py`）：`anthropic`（`{base}/v1/messages`，`x-api-key` 与 `Authorization: Bearer` 都给，`anthropic-version: 2023-06-01`，
  系统提示与最后一个工具定义带 `cache_control: ephemeral`）、`openai-chat`（`{base}/chat/completions`）、`openai-responses`
  （`{base}/responses`，`store: false`）、`demo`（剧本回放，不联网，端到端测试用）。anthropic 的思考由**调用方声明** `--thinking {adaptive,budget,none}`（默认 `none`，runner 不猜：第三方兼容网关各不相同）：`adaptive` 发 `thinking:{type:adaptive}` + 有 effort 才发 `output_config.effort`（新模型，不发已弃用的 `budget_tokens`）；`budget` 发旧式 `budget_tokens`（必须配 `--effort low|medium|high|max`，`max` 映射到 high 档；缺省 effort 起跑即 `internal/bad_args`、0 次请求，不静默不发 thinking）；`none` 都不发。thinking 块按原样回放，不做 assistant prefill，工具参数一律 `json.loads`。openai-chat 回复里的 `reasoning_content` 存进该轮 `native` 并在回放这一轮 assistant 消息时原样带回（DeepSeek 思考模式必需，报错原文见 wires.py；没有思考内容的轮次不加该键，不加空格占位——会话内始终同一模型）。openai-responses 只在给了 `--effort` 时传 `reasoning.effort`。`--wire openai-responses` 配 `--effort max` 起跑即 `internal/bad_args`、0 次请求（Responses 没有 max 档，不静默不发 reasoning）。
  系统提示 + 工具定义在请求最前且逐字稳定（吃提示缓存）；变化的东西（目标名、用户指令）只在第一条用户消息里。
  历史压缩是 thinking 原样回放的例外：`none` / `adaptive` 压缩改变签名前缀后，删除失效的 `thinking` / `redacted_thinking` 块，保留工具调用与结果；`none` 也可能收到服务端签名块。`budget` 不压缩、只追加，仍受总预算约束；不暗改调用方的模式或 effort。看护 `tests/test_agent_runner_media.py` 的真实 HTTP 签名前缀接收器。
- **出站**：只许 https，或明文 http 但主机属本机回环 / 私网（`10/8`、`172.16/12`、`192.168/16`、`100.64/10`、`fc00::/7`；链路本地 / 公网 / 保留段拒；云元数据地址与主机名先于放行段排除（`netpolicy.METADATA_IPS` / `METADATA_HOSTS`，出处 URL 写在注释里，含 GCP / Akamai 的 IPv6 端点 `fd20:ce::254` / `fd00:a9fe:a9fe::1`）；主机名要全部解析结果都在放行段）——判据只在 `engine/netpolicy.py`（`check_base_url` 与 `ai_catalog.url_refusal` 共用），连接时钉住判定那次解析出的 IP 直连、`Host` 头保留原主机名；URL 不带用户信息 / 查询 / 片段。https 走 `_opener()`（`build_opener(tlstrust.https_handler(tlstrust.client_context()))`
  + 不跟随重定向，代理沿用环境变量）；明文 http 走 `netpolicy.pinned_connection` 直连已校验的 IP、**不经代理**（出站门禁只认这一处变量主机的 `HTTPConnection`）。响应体有上限；
  请求有超时；`tests/test_outbound_https_trust.py` 的 `OUTBOUND` 登记了 `engine/agent_runner/wires.py: _opener`。
- **runner 层自救（ADR 0147 修订 2026-11-15，`agent_runner/fallback.py`）**：两个**闭集**，判据只看状态码与响应体里的闭集短语，体原文只在内存里匹配、不进事件 / 日志 / 诊断包 / 异常。
  ① **第一轮换协议**：`--fallback WIRE=BASE_URL`（可重复，按序试；`BASE_URL` 与 `--base-url` 同 scheme / 主机 / 端口，否则起跑即 `internal/bad_args`、0 次请求）。只在**从未收到过成功回复**时（没执行过工具、没有原生回放项）触发：404 / 405，或 400 / 404 / 503 且体里含 `fallback.WRONG_PROTOCOL_PHRASES` 之一（不区分大小写；JSON 体先解码，免得中文短语被 `\uXXXX` 转义漏掉）。401 / 403 / 429 / 其余 5xx 与任意其他 400 一律不换，不吞重试次数。
  ② **非必需字段降级**：400 / 422 明确点名我们发的 `include`（responses）、`max_output_tokens`（responses）或长度字段（chat 的 `max_tokens` ↔ `max_completion_tokens` 换名）为不被接受（`unsupported` / `unknown` / `unrecognized` / `not permitted` / `not allowed`；值非法不算；`store` / `include` 只认带引号的点名）→ 剥掉 / 换名重发，同一字段一次，之后每轮沿用；剥 `include` 时回放里的 `reasoning` 条目同时剥掉。**`store: false` 绝不自动剥**（提示词含用户未发表脚本）：responses 上 `store` 被点名拒绝按「协议不对」处理，有 `--fallback` 才换到 chat，没备选照常失败。
  **回报**只在 `done` 事件上、闭集字段（`loop.DONE_REPORT_FIELDS`）：`wire_used`（最后一次成功回复的协议名，没成功过 = `""`）、`wire_switched_from`（换过协议 = 最初协议名，否则 `""`）、`dropped_fields`（`fallback.DROPPABLE_FIELDS` 的子集：`include` / `max_output_tokens` / `max_tokens` / `max_completion_tokens`）、`token_field`（chat 最后实际用的长度字段，否则 `""`）。没有自救能力的模型对象（Demo、`fixsession._FinishAware` 包装）回空值。看护 `tests/test_agent_runner_fallback.py`。
- **密钥**：只读环境变量 `TAVOTTO_AGENT_API_KEY`（读后从本进程环境删除），绝不进 argv / stdout / stderr / 事件 / 日志 / repr。
  提示词只经 `--prompt-file` 或 stdin。服务端错误体不进任何输出（可能回显提示词），只留 `detail` 短代码。
  **模型的成功回复同样不可信**（服务端在请求头里拿到了密钥，可能回显）：`__main__` 的事件写出函数 `emit` 与 stderr 诊断函数 `diag` 是
  进程里**唯一**的输出出口，所有字符串（`text`、`tool` 摘要、`done.detail`、诊断）统一过 `redact.Redactor`，已知密钥（长度 ≥ 8 才参与）
  及其去空白 / JSON 转义变形换成 `[redacted]`。同一保证的另一出口：`edit_file` / `write_file` 的内容含密钥 → 工具错误 `secret_in_content`，拒写、文件零改动。
  提示词读不到或为空白 → 不调模型，`done{ok:false, reason:"internal", detail:"bad_prompt"}`、退出码 1。
- **工具与路径判据**（`tools.py`，ADR 0138 §四）：`read_file`（≤ 200 KB 的 UTF-8 文本）、`list_dir`（一层，符号链接只标注不跟随）、
  `edit_file`（`old` 必须在目标脚本里**恰好出现一次**，0 次 / 多次都是带说明的工具错误；本会话必须先 `read_file` 过目标）、
  `write_file`（整份覆盖，兜底）、`check`（调用方注入的回调，没注入 = `not_available` 工具错误；**最多 3 次**，第 4 次 `check_budget`，改图与转换同一处 `tools.MAX_CHECKS`）。**没有 shell、网络、MCP 工具。**
  路径判据只有一处 `FigureEditSession._resolve`：`realpath` 后须在候选目录内（符号链接先解开再判，链出界即越界）；写只许目标脚本，
  目标本身是符号链接也拒写。越界 / 写非目标 / 超大 / 非文本一律返回**工具错误结果**，不抛、不执行、文件零改动。
  大型只读资源可用 `read_file.byte_offset/byte_limit` 分段查看（单次 ≤ 64 KiB，默认 16 KiB）；返回完整 UTF-8 字符边界的 `next_byte_offset` / `eof`。打开前后核对文件身份与路径；分段读取不授予目标写权限，完整目标读取及写入的 200 KiB 限制不变。看护 `tests/test_agent_read_windows.py`。
  换行：读出时统一成 `\n`，写回恢复文件原有的 `\r\n`（混合换行的文件会被统一成首见的那种）。
  参数拒绝仍由各工具 handler 判定；`bad_arguments` 且未写入时，循环从该工具公开 schema 补充缺失必填键、可用键名及类型，提示原样重发一条修正调用。
  文本替换取值与 schema 共用 `_EDIT_TEXT_ARGUMENTS`：`old` / `old_string`、`new` / `new_string` 是明确声明的两组名称；同组重复提供时值必须相同。
  schema 使用平铺属性（Anthropic 拒绝顶层 anyOf / oneOf / allOf），名称组必填与重复值一致性由共同 handler 验证，不复制另一套参数判据。
  不猜测未知名称，不回显模型给出的键名或参数内容；冲突值或类型错误在写入前拒绝。figure_edit 与转换共用这条契约，原有路径与内容验证不变。
  将来 `data_to_figure` 的工具集**没有写文件工具**（只出结构化列映射，ADR 0138 §四）。
- **事件**（stdout 每行一条 JSON，ASCII 转义）：`thinking`（只有字符数）、`tool`（工具名、成败、一句话摘要，**不带文件内容 / 提示词 / 密钥**）、
  `text`（模型对用户说的话）、`usage`（每轮用量；另带 `ms` 这一轮的时长与 `reported` 用量是否服务端报的，失败轮带 `ok:false`——父进程的执行追踪据此记模型请求，ADR 0151）、`done`（最后一条，恰好一条）。stderr 只写诊断级错误码。
  `tool` 事件的工具名只出自 `agent_runner/tools.TOOL_NAMES`（闭集，新加工具同步它）。
- **结束条件与结局**：`done{ok, reason, detail}`，`reason` ∈ `completed | max_turns | budget | tool_error_loop | auth | quota | refused | network | cancelled`，
  外加兜底 `internal`（本模块自己的缺陷，`detail` 只有异常类型名）。失败**不抛**。429、5xx、408 与传输失败指数退避重试（有上限），401 / 403 → `auth`，
  402 或额度类 429 → `quota`，429 重试用尽 → `quota`，其余 4xx（含 409）不重试 → `network`。服务端过滤 / 拒答 → `refused`（chat `finish_reason=content_filter`、
  anthropic `stop_reason=refusal`、responses `status:incomplete` 且原因不是 `max_output_tokens`，`detail` 如 `incomplete_content_filter`）；
  responses 顶层 `status` 为 `failed`（`detail=failed_<error.code>`）或 `cancelled / queued / in_progress`（`status_<名>`）→ `network`，不再当作空的 end 轮。
  重试等待 = min(退避, Retry-After, 30 s, 距总时限的剩余)，剩余 ≤ 0 不再发请求 → `budget`（`detail=time`）；单次请求超时也不超过剩余时间。**等响应时也看总时限**：服务端以小于 socket 超时的间隔慢慢吐字节时每次 read 都成功，所以等待循环（与取消同一处、每 50 ms）同时判 deadline，到点即 `budget/time`，在途请求丢弃、回复不接受。
  **`stop == end` 且无工具调用，或转换任务的显式最终校验实际通过，才算 `completed`**；最终校验见下，不从模型文字推断成功。其它停止值（未知 `stop_reason` / `finish_reason`，或 `tool_calls` / `tool_use` 结束却没有可用调用）→ `network`，`detail=bad_stop_<值>`（值只留小写字母数字下划线、≤ 24 字符）；`max_tokens` 的有界续写规则见本节末，耗尽后为 `budget/output_truncated`。同一工具、同一参数、同一错误码连续 3 次 → `tool_error_loop`。
  SIGTERM / SIGINT / Ctrl-Break → `cancelled`。
- **预算与账本**：轮数、token（input+output）、秒数三道上限。每轮 input / output / 耗时入账，结束时核「合计 == 各轮之和」，不等记
  `done.ledger_mismatch=true`（不抛）。每轮记账后立刻复查 token：**终止答复**（`end` 且无工具调用）累计 `>` 上限才拒，恰好用满上限仍 `completed`；带工具调用 / 要开下一轮的答复累计 `>=` 上限即 `budget/tokens`，工具不执行、不开新轮。估计上下文超阈值时把早先的 `read_file` 结果换成「已读过 X（第 a–b 行，sha 前缀）」。
- **`convert_to_editable` 工具集**（`tools.py` 的 `ConvertToEditableToolset` / `ConvertSession`，ADR 0147 §四；入口 `--task convert_to_editable --target <原脚本> --output <原名>_matplotlib.py`，
  默认任务仍是 `figure_edit`）。权限边界即 ADR 0143 §二：**原脚本永远不可写，Agent 只写候选目录顶层的那一个新文件**。
  - 工具含 `read_image`、`read_file` / `list_dir`（候选内只读，与 figure_edit **同一个** `_resolve`，没有第二份路径判据）、`create_file(content)`、`append_file(content, expected_sha256)`、`edit_file(old, new)`、`check` / `finish(summary)`；
    没有 `write_file`、没有 shell / 网络 / MCP。`create_file` 的可选 `path` 与 `edit_file` 的可选 `path` 只能是 `--output`：指向原脚本 → `source_readonly`，
    其它路径 → `not_output`，越界 → `path_outside`；一律工具错误、磁盘字节不变。第一次 `create_file` 用 `O_CREAT|O_EXCL` 新建（此刻已存在含悬空链接 → `output_exists`，不覆盖），
    之后允许整份覆盖这个新文件（模型自我修正）；`edit_file` 必须在本次运行里已 `create_file`（否则 `not_created`），`old` 恰好出现一次。含密钥的内容 → `secret_in_content`（新建 / 覆盖 / edit 三处）。
    create / append / edit 的回执均含当前文本 SHA-256；编辑后继续追加使用最新回执，不用旧 create 摘要。追加前仍核内容，过期摘要拒绝。
  - `--output` 起跑即校验：必须恰为 `<原名>_matplotlib.py`（原名 = `--target` 的 stem）、只有文件名（无目录、无反斜杠）、尚不存在（`lexists`）；不合格 → `done{ok:false, reason:"internal", detail:"bad_output"}`，**0 次模型请求**、磁盘不动。外层修正续跑只接受父进程传入的上次候选字节 SHA-256，拒绝链接、非普通文件、字节变化及超读限文件；`checks_used` 继承已用次数，子进程重启不重置校验预算。
  - `check`：同 figure_edit 的注入回调，语义是 ADR 0143 的 `run_validation`（用户同一路由下跑新脚本，必须捕获到 Matplotlib 图），回调可返回 `(ok, 报告)` 或 `CheckResult`（报告 + 有界图像）；转换入口经 IPC 调用应用同一校验，实时渲染 PNG 后才退役 worker。未注入回调则 `not_available`。
    新文件还没写出就 `check` → `not_created`（不调回调、不计数）。**回调最多被调 3 次**（ADR 0143「校验运行 ≤ 3」），第 4 次 `check_budget`。
    可选 `final: boolean`（默认 false）与 `summary: string`：模型明确声明整份候选已可交付，且同次回调返回布尔 true，工具才标记本轮完成；报告文字不能代替布尔结果。
    最终校验通过后不再请求模型收尾，同批后续工具也不执行；summary 沿正常脱敏出口发给用户。普通检查仍非终态，最终检查失败可继续修正，参数错误不消耗检查次数。
    始终受配置的轮数 / token / 取消 / deadline 边界约束；父进程仍独立执行 Task Core 校验、完整性与待采用检查，不从失败任务抢救候选，也不自动采用。
    `finish(summary)` 是同一个 `check(final=true)` 的明确收尾入口，共用回调、次数和终态条件；它自己不执行文件写入或采用。
  - 预算默认值由工具集给（`default_limits`；命令行不给 `--max-turns` 等就用它）：convert 为 32 轮 / 900 s / 800k token / 单轮输出 32768（含模型思考）；figure_edit 不变。
    原 20 轮 / 400k 在同输入真模型对照中截断了可成功交付的流程：Pillow 需要 22 轮 / 420k，MATLAB 需要 677k。只增加转换的轮数/token 余量，保留 15 分钟截止、取消、文件/路径及 3 次校验边界。
    转换的 `budget_hint` 使循环在任一资源使用达到 50% / 75% 时各插入一次剩余轮数、累计 token、秒数和收尾指导，随下一次正常模型请求发送；无额外请求，不重置账本或预算。普通检查通过仍非终态。
  - 系统提示是英文（要求用用户指令的语言回复）；共同验收顺序及要求（数据计算 / 完整可编辑出图 / 视觉另验）**引用 `ai_prompt_pack.convert_requirements()` 的同一份文字**（CLI 路径的 `convert_prompt` 也用它，不各抄一份），
    执行器特有的部分（只写一个新文件、不运行脚本、check ≤ 3 次）写在 `tools.py`。`ai_prompt_pack` 是纯标准库，所以 `agent_runner` 对它的 import 不破坏依赖方向（`test_import_architecture` 兜底）。
  - 看护 `tests/test_agent_runner_convert.py`（Demo 协议端到端 + 单元）。

### SIWC 专用协议 `siwc-responses`（`agent_runner/siwc_wire.py`，ADR 0201 / 0209，默认开）

- **不是第四条普通协议**：不在 `wires.WIRES` / `ai_providers.PROTOCOLS` 里（保存时探测、`--fallback`、AI 修复 `build_model`、用户记录都写不出它），只有 `runner_cli` 的 `--wire` 多认这个名字；
  起跑硬约束（`siwc_wire.runner_args_ok`）：开关没被 `TAVOTTO_EXPERIMENTAL_SIWC=0` 关掉、`--auth bearer`、`--base-url` 恰为 `https://api.openai.com/v1`、无 `--fallback` / `--thinking`、`--effort` 只许 `SIWC_EFFORTS`，否则 `bad_args` 零请求。
- **请求体白名单**（`_ALLOWED_FIELDS`）：`model / instructions / input / tools / store:false / stream:true`；官方禁用清单 `FORBIDDEN_FIELDS`（含循环总会传入的 `max_output_tokens`）、`reasoning` / `include`、回放里的 `reasoning` 条目、`role:"system"` 条目一律不出现；函数工具包进 namespace `tavotto`。
- **SSE**：`Request.reader = compact_sse` → `wires.post_json(reader=…)` 对 2xx 边读边丢 `*.delta` 帧、终态后停读（总读入 64 MiB、留下 8 MiB 上限）；`HttpModel` 对带 `decode` 的协议用它折叠成最终响应再交 `OpenAIResponsesWire.parse`。只有 `response.completed` 是成功；无终态 = 可重试 `network/stream_incomplete`。三条原协议的 `post` 调用形状逐字不变（不传 `reader`）。
- **错误码闭集**（`failure_for_code`，预流与流中 `response.failed` / `error` 同一张表）：用量超限 → `quota/plan_usage_limit` 不重试；`…usage_unavailable` / `…user_unavailable` → 可重试 `network`；`…user_not_eligible` → `refused/plan_not_eligible`（**不是 auth**）；403 无码 → `refused/plan_forbidden`；
  `…unsupported_capability` / `…route_not_supported` / `chatpass_v2_*` → `internal`；`…invalid_user` → `auth`。带 `classify` 的协议**不走换协议与字段降级**（官方：不得静默切换计费方式）。
- 看护 `tests/test_agent_runner_siwc.py`（全 Mock）。2026-11-24 真服务核对过单轮 / 工具调用 / 多轮与 completed 带 output；仍未验证的项见模块说明，不得写成已验证。

### 连接（`engine/ai_connections.py`，ADR 0147 第二阶段）

界面不讲「执行器 / CLI / agent」，只讲「连接 AI」。`ai_connections` 在 `ai.providers[]` 与两家 CLI 的官方登录态之上给一份**视图**，**不迁移存储**
（`ai.active_provider` 只作旧字段兼容，连接的解析不看它）。依赖方向 `ai_agents ← ai_providers ← ai_connections ← ai_bridge`。

- **`capabilities` 新字段**（旧字段一个不动，`agents[]` 仍只有 codex / claude）：`vendors[]`（厂商目录）、`connections[]`（已连接 / 需重新登录）、`locale_region`。
  - `vendors` **按厂商合并接入方式**，每家一条，怎么接入放进 `methods[]`（订阅 / 账号登录在前，API Key 在后）：`{id, kind: key|login|oauth|siwc, label_key, catalog_id?, agent?, connection_id?, key_url?, key_hint_key?, needs_base_url?, available}`（`siwc` 只在 SIWC 开关开时出现，见下文「SIWC 登录、凭据与连接」）。
    login 方式 id `<厂商>:login`（`agent` = 对应 CLI，`connection_id` = `sub:codex` / `sub:claude`，`available` = CLI 已装）；oauth 方式 `<provider>:oauth`（`ai_oauth` 的 client_id 配了才出现，`connection_id` = `minimax-oauth`）；
    key 方式 `<catalog_id>:key`（`catalog_id` 就是 `POST /connections` 的 `vendor`）。旧字段保留：`connect` = `methods[0].kind`，`key_url` / `key_hint_key` / `needs_base_url` 取自第一个 Key 方式，`available` = 任一方式可用。
    合并来自目录：同 `brand` 的 Claude 行与 Codex 行成一个 Key 方式；行上的 `vendor` 把几个方式并进一家（OpenAI = `openai:login` + `openai-api:key`（`https://api.openai.com/v1`，Responses）；
    Claude（厂商 id `anthropic`）= `anthropic:login` + `anthropic-api:key`（`https://api.anthropic.com`）；Kimi（`moonshot`）= `moonshot:key` + `kimi-code:key`；百炼（`dashscope`）= `dashscope:key` + `dashscope-coding:key`；
    MiniMax（国内，`minimax`）= `minimax:oauth` + `minimax:key`）。**不跨 region 合并**（`minimax-global`、`zai` 各自一家）。官方 API Key 行带 `"preset": false`（不进旧版预设列表）。
    厂商显示名：行上的 `vendor_label`（OpenAI / Claude / Kimi / Alibaba Bailian），`vendor_label_key` 是它的 i18n 键（没有则 null，前端直接用 `label`）；方式的 `label_key`：`method.subscription` / `method.account` / `method.apiKey` / 行上的 `method_key`（`method.kimiCode` / `method.codingPlan`）。
    旧厂商 id → 现在：`chatgpt` → `openai`，`claude` → `anthropic`，`kimi-code` → `moonshot`（方式 `kimi-code:key`），`dashscope-coding` → `dashscope`，`minimax-oauth` → `minimax`（方式 `minimax:oauth`，连接 id 仍是 `minimax-oauth`）。
    排序：中文系统国内厂商在前，其后是有订阅登录的海外厂商（OpenAI / Claude），再是其余海外，最后 custom；非中文反之。
  - `connections`：订阅类 `sub:codex` / `sub:claude`（CLI 装了且启用才列；`needs_auth` 只看就绪检查的 `needs_auth`）+ 每条 provider 记录一条（Key 类 id = provider id；`auth: "oauth"` 类同）。
    明文 http 的局域网网关（`netpolicy` 放行、非回环）带可选 `insecure_lan: true`。
    每条带 `vendor`（合并后的厂商 id）与 `method`（接入方式 id）；logo 取它自己那一行的图标。多条可同时 `ready`，同厂商多条时 `label` 带用户起的名字 / 密钥尾四位；OpenAI / Claude 的官方 Key 连接名带「 API」后缀以便和订阅那条分开。没有地址的旧记录（官方 Key）不进视图。`state`：缺 Key → `needs_auth`；
    不兼容旧记录 / 保存时被服务商拒绝（`last_error: unauthorized` 落盘）/ 路由走不通 / 内置路由缺型号 → `error` 且带原因码 `error`。
  - **路由 `route_for`**（`route` 只给诊断与测试，界面不显示）：订阅 → `cli:<agent>`；Key 类：该家族 CLI 可用且显式 `ai.builtin_default=false` → `cli:<家族>` + 注入；缺省 true（2026-10-26 用户授权，ADR 0147 §五），不覆盖已存 false；
    否则 → `builtin`（内置循环必须能用该记录：`ai_providers.builtin_conn`，不行则在派发前退回可用的 CLI；不在任务中途自动重放）。账号登录类也受默认偏好与强制开关控制，其 CLI 可用性只看安装且启用（不看 CLI 自己的官方登录，凭据由我们注入）；anthropic wire 只发 Bearer（`--auth bearer`），令牌在 spawn 前 `ensure_fresh`、只进环境变量。
    `TAVOTTO_AI_BUILTIN=1` 强制内置、`=0` 强制不用（没 CLI 即走不通）。
- **协议探测与 openai-chat 连接（TVD-HN4B，ADR 0147 修订 2026-11-15；`engine/ai_probe.py`）**：自填中转站「能列型号、跑不了任务」——型号清单只证明地址通。保存 Key 连接后对 (地址, Key, 型号) 真发对话请求探测：
  - **记录字段**：`protocol`（闭集 `anthropic | openai-responses | openai-chat`，缺省 = 家族默认；**不是 `wire_api`**，旧 codex + `wire_api="chat"` 记录照旧 `codex_chat_removed`、不自动改）、`protocol_ok`（`{base_url, model, protocol, token_field?}` 指纹，`_clean` 只在地址与默认型号都没变时保留）、
    `last_error = endpoint_unusable` + `probe_reason`（探测全不通；Key 被拒仍写 `unauthorized`）。换 Key / 改名不丢 `protocol`；`_clean` 丢掉与家族对不上的协议（claude ↔ anthropic，codex ↔ openai-*）。chat 记录的 `agent` = `codex`（openai 家族），**只走内置**。
  - **判据只在 `ai_providers.builtin_only`**：`route_for` 对它的三个 CLI 出口（`TAVOTTO_AI_BUILTIN=0`、`builtin_default=false`、内置走不通退回 CLI）都不给 `cli:codex`——强制关内置 → `(None, "builtin_only")`；偏好 CLI 对它不适用（它没有别的路，仍走内置）；内置走不通 → 具体原因（`no_model` / `base_url_scheme` / `builtin_only`）；
    连接视图的 `route` 永不显示 `cli:codex`；`spawn_overrides("codex", chat 记录)` 返回空；`resolve("codex")` 不返回「当前选中」的 chat 记录；`ai_bridge.run("codex", endpoint_id=<chat>)` 在 spawn 之前 `ai_agent_not_usable`；`resolve_connection` 走不通时 params 带 `reason: "builtin_only"`。`builtin_conn` 给 `wire: "openai-chat"`，
    需要 `max_completion_tokens` 的（探测发现 / 官方 OpenAI o 系列）带 `token_field`，经 `--max-tokens-field` 传 runner，`fixsession.build_model` 同样建出 `OpenAIChatWire`。
  - **探测判据**：每种协议一条带一个工具定义、`max_tokens ≤ 16` 的最小请求，**复用 `agent_runner.wires` 的 `build` / `parse` / `post_json`**（不新增出站点、不新增 opener）；通过 = 解析出合法 `Turn`，**不是只看 2xx**（200 网页 / 200 错误对象判失败）。
    每协议结论是闭集 `ok | unauthorized | quota | no_model | wrong_protocol | network | web_page | bad_reply`；**响应体原文只在 `classify` 里被读，不进返回值 / 日志 / 诊断包**。地址按协议换算：OpenAI 系先试原样再试补 `/v1`，Anthropic 系先试原样再试去掉 `/v1`；换协议时记录的 `base_url` 跟着换。
    顺序与「第一个通过就停」：Claude 型号 → anthropic；api.openai.com 的 gpt / o 系列 → responses；其余 → chat（与 magpie `usable()` 一致）；用户明确给的协议先试，通过就尊重。预算：每条 ≤ 15 s、总 ≤ 30 s；同主机传输层失败就不再试别的。全是网络失败 = 没结论：记录不改、不记错误。
    目录里的已知厂商只核自己那一种协议（不换协议、不换地址），失败码同上。
  - **备选协议与自救回报（叠在 runner 的 `--fallback` 之上）**：自填地址（不在厂商目录里；目录厂商与账号登录不给）的 `builtin_conn` 带 `fallbacks`（`ai_providers.fallback_routes`：其余两种协议、按协议换算地址——OpenAI 系带 `/v1`、Anthropic 系去 `/v1`、同 scheme / 主机 / 端口，顺序同探测），
    改图经 `_builtin_overrides` 传 `--fallback WIRE=BASE_URL`（可重复），AI 修复经 `fixsession.build_model` 给 `HttpModel(fallbacks=…)`。运行**结束后**（不在执行中途）读 `done` 事件的 `wire_used` / `wire_switched_from` / `dropped_fields` / `token_field`：
    改图由 `BuiltinAgent.classify_event` 记进会话状态、`ai_bridge` 在进程退出后调 `ai_providers.apply_runtime_report`；AI 修复由 `FixSessions._persist_runtime_report`（`_FinishAware.report()` 把内层模型的回报交出）。
    `wire_used` ≠ 记录的协议 → 改 `protocol` / `agent` / `base_url` / `protocol_ok`；`token_field` 写进 `protocol_ok`（下次起跑带 `--max-tokens-field`）；`dropped_fields` 只记进 `protocol_ok`（runner 暂无「起跑即不发」参数）；有成功回复就清 `last_error` / `probe_reason`。目录厂商报来的换协议不信。
  - **出站**：探测请求发往用户连接的那个服务商地址（与正式任务同一个、同一套 `wires.post_json`），带用户 Key；隐私说明里写明「保存连接时会发一条很短的测试请求」。
  - **接口**：`POST /api/ai/connections` 的 `wire` 可选（缺省 / `"auto"` = 探测；`anthropic | openai-responses | openai-chat` 也探测，没通过就换探测到能通的），返回 `{ok, connection, probe}`；
    `POST /api/ai/connections/<id>/check` 重新探测，返回同形状（订阅 / 账号登录 / 不存在 → 404 `connection_not_found`）。`probe` = `{checked, protocol, requested, switched, reason, reasons}`；连接视图多 `protocol` / `protocol_checked` / `error_reason`。
    `first_key_connection`（AI 修复挑连接）跳过 `endpoint_unusable`（`quota` / `network` 是暂时性，不挡）。测试进程默认把 `ai_probe.detect` 桩成「没结论」（`tests/conftest.py`），测它的用例自己换回真 `detect` 并桩 `wires.post_json`。
- **管理接口**（照常过会话守卫与 Host/Origin 校验）：`POST /api/ai/connections`（`{vendor | method, api_key, base_url?, model?, label?, wire?, id?}` → `{ok, connection}`；Key 形状 = 可打印 ASCII 无空白 ≤ 4096；
  `base_url` 须 https 或回环 http；保存前 `live_models` 试取一次，**失败也保存**，返回的连接 `state:"error"` + `error` 原因码，只有 `unauthorized` 才落盘；`custom` 必须给 `base_url`、`wire` 可选
  （缺省 / `auto` = 探测，或 `anthropic` | `openai-responses` | `openai-chat`；见「协议探测」）；带已存在的 `id` = 换 Key / 型号并清掉错误）、`DELETE /api/ai/connections/<id>`（订阅类 `connection_not_deletable`）、
  `POST /api/ai/connections/<id>/login`（订阅类走 `start_login`；`<provider>-oauth` 走设备码流程，返回 `flow_id`，进度仍用 `/api/ai/oauth/flows/<id>`）。错 body 一律 400 `endpoint_invalid{reason}` 且什么都不保存、不碰网络；
  密钥不在任何响应 / 日志里。旧 `endpoints*` 接口保留。
- **改图请求**接受 `connection`（+ `model`）：`ai_bridge.resolve_connection(id) -> (agent_id, endpoint|None)`（= `ai_connections.resolve_connection` + 本机 CLI 探测结论，不 import 成环）直接解析到那条 provider 与路由（订阅 → `(codex|claude, None)`，请求里 `endpoint_id=""` = 明确用官方登录态）；
  放在去重**之后**。没带 `connection` 时旧的 `agent` / `endpoint` 字段照旧。
- **内置执行器进改图执行路径**：`ai_agents.BuiltinAgent`（id `builtin`，`internal=True`）在 `INTERNAL_AGENTS`——`get_agent` 找得到，**不在** `AGENT_REGISTRY` / `agents()` / `agent_ids()` / capabilities 的 `agents[]`
  （旧前端会把它当执行器显示）。命令 = `ai_agents.tavotto_launcher()`（与 `fixsession.mcp_launcher` 同一出处：冻结产物同目录 `tavotto-cli`，否则 `-m tavotto`）+ `agent-run`（`engine/cli.py` 的子命令，等价 `python -m tavotto.engine.agent_runner`）
  `--cwd <候选目录> --target <脚本> --wire --base-url --model`。**密钥只经环境变量** `TAVOTTO_AGENT_API_KEY`，提示词经 stdin（`_popen(stdin_text=…)`），argv / 日志 / 事件 / 历史库里都没有。
  `ai_providers.builtin_conn(rec)` 是「内置循环能不能用这条记录」的**唯一判据**（有 Key、有型号、`check_base_url` 合规、非账号登录、非不兼容旧记录、家族认得），改图的 `spawn_overrides("builtin")`、
  `fixsession.connection()`、路由三处共用。`require_usable("builtin")` = 有这样的 Key 连接且没被 `TAVOTTO_AI_BUILTIN=0` 关掉；`run("builtin")` 必须显式带 `endpoint_id`（没有「当前选中」）。
  会话生命周期（候选工作区 / TAF 校验与修正 / 事件流 / 取消 / 历史库 `provider="builtin"`）与 CLI 完全同一套；`classify_event` 把 runner 的 `tool/text/thinking/done` 事件翻成 `action/message/thinking`。
  **转换任务**（`convert`）同样走内置：`--task convert_to_editable --check-ipc`，`check` 经 stdout `check_request` / stdin 回复回调 `ai_convert.agent_check`（stdin 第一行是 `{"prompt":…}`）；
  **改图任务**有 TAF 候选时同样带 `--check-ipc`（`ai_agents.RunContext.check_ipc`，桥里的 `builtin_ipc`），回调 `ai_figure_edit.agent_check` = 提交前 L1（`FigureEditAdapter._precommit`）→ 候选在用户同一路由下真渲染（`ai_convert.run_validation`，写入根绊线只开候选目录；`write_outside_roots` 记“没量到”不判坏）→ 数据指纹（`figureverify.data_unchanged`，基线 = `task_hooks["check_before"]` 读已 build 会话落盘的 manifest，纯读）；hooks `runner` / `check_before` 由 `app._ai_task_hooks` 给，缺哪个就少量哪项并在报告里说明。无候选（TAF 关）不装 check。两家 CLI 没有这个入口。看护 `tests/test_ai_connections.py::test_builtin_edit_check_*`。
  **已知缺依赖的候选**：check 把 `missing_dependency` 与当时的候选字节 SHA-256 绑定；提交前只有字节仍相同才按同一个失败停住，不能以 partial 写回。依赖决定复用 `convertdeps` 与同一张授权卡；用户回显影响摘要后，在新的 AI 任务中保留跨进程脚本占用、重跑同一候选并核 worker 实际执行字节的摘要，通过才经原有提交 / 验证 / 回滚链写回。候选、原脚本或项目在期间改变就拒绝；重复点击同一张卡只接回同一个续跑。其他 partial 路径不变。看护 `tests/test_ai_figure_edit.py::test_dependency_*` 与 `tests/test_ai_connections.py::test_builtin_missing_dependency_holds_candidate_until_install_and_recheck`。
  `/api/ai/convert` 与 `/api/ai/run` 一样接受 `connection`。**思考强度**：`ai_catalog.efforts_for(agent, base_url, model)` 声明「来源 + 型号」确认支持（行级 `efforts` / 顶层 `effort_origins[].models` 型号前缀），内置路由才给 `efforts`（默认型号的档位；每个型号的在 `model_details[].efforts`），传 runner 前按实际型号再核一遍。AI 修复的 `first_key_connection` 跳过 `last_error: unauthorized` 的记录。遥测 `ai_assistant_invoked.agent` 对 `builtin` 落成 `other`。
- **AI 修复**：`fixsession.connection()` 改为 `ai_connections.first_key_connection()`——第一个内置循环能直接用的 Key 连接（老用户旧版「选中」的接口排最前，其余按保存顺序），不再只看各家族的 active provider。

### SIWC 令牌提供方（`engine/siwc_auth.py`，ADR 0201 §三，本阶段不实现登录）

- **接口** `TokenProvider`：`access_token(min_valid_s=MIN_VALID_S)` / `mark_rejected(reason)` / `status()`；`mark_rejected`（一次 401 / `plan_invalid_user`）只让下次先刷新、**不清凭据**——只有终态刷新错误才清（官方：确认撤销或终态刷新错误后才要求重新登录）；`status().public()` 是唯一能离开模块的形状（无令牌）。本阶段 `provider()` 恒 `DisabledProvider`（开关开着也一样）。
- **刷新状态机** `RefreshingProvider`：`MIN_VALID_S` = 最长任务 900 s + 300 s（**不复用** `ai_oauth` 的 10 分钟）；进程内串行刷新；`TokenSet` 四样整体替换、刷新没回 scope = 授权不变；
  终态刷新错误（`TERMINAL_REFRESH_ERRORS`）才清凭据，`invalid_client` 不清，暂时失败保留凭据（`TokenUnavailable`）；缺 `chatgpt.tokens.use.direct` → `PlanUsageDisabled`（已登录、不许用订阅额度、不换计费方式）；`earliest_refresh_at` 之前不刷新（含义官方未定义，未经真服务确认）。
- **不碰 Codex CLI 的令牌**、不 import 网络模块 / `ai_agents` / `ai_oauth` / `ai_providers`（看护 `tests/test_siwc_auth.py` 按 AST 钉）。

### SIWC 登录、凭据与连接（`engine/siwc_login.py` / `siwc_store.py`，ADR 0209，默认开、`TAVOTTO_EXPERIMENTAL_SIWC=0` 关）

- **登录**：授权码 + PKCE，前端用系统浏览器开 `authorize_url`；回调由**一次性**本机 `http.server`（只绑 127.0.0.1、只认 `GET /auth/callback` + 本次 `state`、收到一次 / 10 分钟 / 取消即关）接收——**不是 Flask 路由**，不提供任何 Tavotto 能力；
  端口先 1455、被占用换系统分配的。首次注册 `dynamic_agent_client` → 回调须带回 `oaiapp_…`（`dynamic_agent_client` 永不落盘为已签发 id）；再次登录用存下的 client_id + `id_token_hint`，回调 client_id / `sub` 对不上 → 拒绝、不替换。
  ID 令牌 RS256 验签（pycryptodomex，延后 import；import 不了 = 登录失败，不降级成不验）+ `iss` / `aud` / `exp`（5 分钟偏差）/ `nonce`。端点：`POST /api/ai/siwc/login`、`GET /api/ai/siwc/flows/<id>`、`POST /api/ai/siwc/flows/<id>/cancel`、`POST /api/ai/siwc/models/refresh`；
  `POST /api/ai/connections/siwc:chatgpt/login` 同 `/api/ai/siwc/login`，`DELETE /api/ai/connections/siwc:chatgpt` = 退出（撤销刷新令牌，回 `revoked`）。出站 HTTPS 只经 `siwc_login._opener`。
- **凭据**：`data_dir()/siwc/`（0700）：`host.json`（`ext_agent_host_id`，永不变）、`state.json`（当前账号）、`accounts/<client_id>.json`（0600 原子写；同一 client_id 换 `sub` 拒绝）。**不进 `ai.providers[]`**。
  刷新整段在 `refresh.lock` 跨进程锁里（`fcntl` / `msvcrt`；等不到 = `TokenUnavailable`，凭据不动）。退出 / 终态失效只清令牌，保留 client_id、身份、`id_token`（下次 `id_token_hint`）。
- **连接**：`siwc:chatgpt`（`ai_providers.SIWC_CONNECTION_ID`）只在开关开且有当前账号时出现；只走 `builtin`，`TAVOTTO_AI_BUILTIN=0` → `ai_agent_not_usable`；`efforts` 恒空；型号读登录时缓存的 `/v1/models`（连接视图不联网）。
  `ai_providers.resolve(builtin, "siwc:chatgpt")` 给**虚拟记录**（无令牌字段）；`builtin_conn` / `spawn_overrides` 对它走专用分支 `siwc_overrides`（`--wire siwc-responses --base-url https://api.openai.com/v1 --auth bearer --model …`，无 effort / fallback；令牌与开关只进环境变量），
  看护用例把父进程 argv 喂给 `siwc_wire.runner_args_ok`。`ai_bridge.run` 在锁外 `_siwc_preflight`（`ai_endpoint_needs_login` / `ai_siwc_plan_disabled` / `ai_siwc_token_unavailable`，去重之后才抛）；runner 报 `auth` → `mark_rejected("http_401")`（不清凭据）。
  开关开时 OpenAI 一家只有 `openai:siwc`（kind `siwc`）+ API Key，`openai:login`（Codex）与 `sub:codex` 连接行一律不列（老用户也直接切换，用户 2026-11-25；`resolve_connection("sub:codex")` 仍认）。探测 / `add_connection` / `check_connection` 碰不到它。
- **与 API 体验相同**（用户 2026-11-25「SIWC 与 API 的体验完全相同」，ADR 0209 §七）：`first_key_connection` 在所有 Key / 账号登录记录**之后**给 SIWC（`refresh=False` 不取令牌、`api_key` 为空；`refresh=True` 现取），
  所以 AI 修复、结构化调用、数据出图、改图方案都能用它。令牌在 `fixsession.build_model`（拿不到 → `RunnerFailure("auth","needs_login")`）/ `ai_structured.call_structured`（→ `no_connection/needs_login`）现取；
  `_wire_for` 认 `siwc-responses`；包 `post` 的中间层必须把 `reader` 往下传（SSE 读取器，漏了 = `transport/internal`）。小型号策略只给 `auth: key`，不碰它；`ai_pricing.vendor_of` 对它恒 None（订阅额度不按 API 价目估钱）。
  **思考强度**同 Key 连接：每个型号的档位 = `/v1/models` 的 `supported_reasoning_levels` ∩ `siwc_wire.SIWC_EFFORTS`（登录时缓存进账号记录，`minimal` 真服务拒），默认档 = `default_reasoning_level`；改图经 `siwc_overrides` 发 `--effort`、结构化调用经 `_effort_kwargs`，型号没声明的档一律不发；wire 发 `reasoning.effort` + `include: [reasoning.encrypted_content]`，回放只留带 `encrypted_content` 的 reasoning 条目。`earliest_refresh_at` 只记不拦（真服务提前刷新照样 200）。
  看护 `tests/test_siwc_login.py`、`tests/test_siwc_connection.py`、`tests/test_siwc_parity.py`（本机假服务器，绝不连真实服务器）。

## AI 修复工具箱（`engine/fixtools/`，ADR 0149 提议，D1：注册表 + 「看」「试」）

脚本跑不起来时让 AI 去查的工具集。**一张注册表、两种接法**：`REGISTRY: dict[str, FixTool]`（`__init__.py`）与接法无关，内置执行器经
`build_agent_tools(ctx)`，CLI 经 MCP（D3）都走同一个 `run_tool`（档位检查 → handler → 脱敏 → 审计）。看护 `tests/test_fixtools.py`。

- **边界**：本包 import `agent_runner.tools / wires`（取 `Tool` / `ToolResult`），**`agent_runner` 不 import 本包**（它是独立子进程，保持纯标准库）。
  不 import app / Flask：工具拿注入的 `FixContext`（`base.py`：项目根、主目录、环境 id → 解释器表、报告回调、环境清单回调、日志路径、脚本、执行器、`reads`）。
  测试里 `home` 一律给 `tmp_path` 假主目录。
- **档位**：`ToolSpec.tier ∈ observe | run | tavotto | listed | user_env | outward`（默认 `observe`）+ `waits_for_user`（默认 `False`），**不序列化给模型**
  （`tests/test_fixtools.py::test_tier_is_never_sent_to_the_model` 钉三种 wire 的请求体）。`FixContext.allowed_tiers` 之外的工具 `run_tool` 拒绝（`tier_not_allowed`）；裸构造默认只放行 `observe`，修复会话创建方用 `FixContext.for_fix_session(...)`（放行 `observe` + `run`）。
  D1 有两档：`observe` 看（`read_run_report` / `read_file` / `list_dir` / `find_files` / `grep_files` / `list_environments` / `read_logs` / `lookup_package`）；
  `outward` 档里只读的 `check_package_availability`（ADR 0221 §三，见下条）；
  `run` 试（`try_import` / `try_run` / `inspect_environment` / `run_snippet`）——**运行用户代码，不承诺「什么都不改」**，保护范围与盲区见下条。
- **查包事实 `check_package_availability`（ADR 0221 §三，`outward`，`fixtools/packagecheck.py` + `engine/packagecheck.py` / `packageindex.py`）**：
  「在这个环境里装 X 会得到哪一版、为什么不是最新版」。**会装哪一版 = 安装器自己的回答**：`deprepair.pip_install_argv(..., dry_run_report=True)`
  （同一份参数构造，只多 `--dry-run --report - --quiet`）经 `_run_pip_install(budget_s=)`（同一套用户自配源判据与镜像回退，总预算 180 s）在**目标环境**里跑，
  读报告里被请求包的版本；不另写一套 wheel 标签判断。**为什么不是最新版**：读安装器这次实际用的索引的 simple 页（PEP 691 JSON 优先、退回 PEP 503 HTML；
  只读 https，明文 http / `no-index` / `find-links` / 问不出地址一律不读 = `index_unreachable`；extra-index 不读），文件名在**父进程**用 `packaging.utils.parse_wheel_filename` 解析，
  与目标环境**自己**报出的兼容标签（子进程里 `pip._vendor.packaging.tags.sys_tags()`）对照——平台与 Python 版本取目标环境，不取父进程。
  `reason` 闭集 `up_to_date` / `no_wheel_for_python` / `no_wheel_for_platform` / `not_found` / `index_unreachable` + 实现补的 `held_back`（最新版有适用 wheel 而安装器没选，
  不硬归进别的原因）；最新版 = 最高的**正式、未撤回**版本；只有源码包算 `no_wheel_for_python`。`suggest` 只在 `no_wheel_for_python` 时给：Tavotto 支持区间
  （`projectenv.PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE`）里最高的、最新版有本平台 wheel 且 `pyversionfix.plan_python_change` 判得出（与 `change_python` 同一判据）的小版本。
  **出站只发包名；索引 URL 若带凭据只在 `packageindex` 里拆成 Basic 头发给那一个主机（不跟随重定向），不进结果 / 审计 / 日志 / 缓存键**，结果里只有闭集 `index` 标签（pypi / mirror / user_config / unknown）。
  索引事实（与环境无关）按（主机 + 路径、包名）缓存 1 小时于 `data_dir()/cache/package_availability/`，会装哪一版随环境变、不缓存。
  **每会话第一次**经 `change.approve` 弹卡（`FixContext.package_check_approved` 记住），参数 / 环境 id 校验在弹卡之前；只读，不作废验证结论（`base.READ_ONLY_OUTWARD`）。
  出站 HTTPS 第十三处：`packageindex._opener`（`test_outbound_https_trust.OUTBOUND` 与 `tlstrust` 模块头同步）。看护 `tests/test_fixtools_package_availability.py`。
- **小实验 `run_snippet`**（ADR 0197）：模型写的一小段 Python 经 `run_python(strict_deny=ctx.scope.deny_roots(), cwd=项目根)` 跑——
  在试跑的守卫之上**断网、凭据位置与凭据形状的文件名不可读**（进程内第二个 audit hook + 操作系统沙盒同一份位置清单；名字规则 `DENY_NAME_PATTERNS` 只在进程内那层；实验口径的沙盒单独探测），当前目录是项目根、项目根在 `sys.path` 上（经守卫前导，不改模型代码）。
  不弹卡（维护者 11-19 裁决）；≤ 30 s、≤ 20 000 字符、先 `compile`；输出各路尾部 6 000 字符过 `scrub`；结果带 `blocked_network` / `blocked_reads`。
  `run` 不在 `CHANGE_TIERS`，实验不解开「先动手才能起草缺陷」的闸。没有操作系统沙盒（Windows、无 bwrap 的 Linux）时进程内守卫只是尽力而为，普通 Python 也能绕开，维护者接受（ADR 0197「盲区」）；
  守卫前导在守卫装好之前只从标准库位置导入（`''` 与 PYTHONPATH 之后才还原、`''` 不还原），子进程一律从试跑目录起——试跑副本与探 import 同一条。
  看护：`tests/test_fixtools_snippet.py`。不带 `strict_deny` 的调用（试跑 / 探 import / 体检）守卫前导与沙盒参数逐字不变。
- **失败提示与试运行记录**（ADR 0197 第 1 层）：形状判据只有 `fixtools/hints.py` 一份（`ask` 的「用户侧失败先别报缺陷」闸门也取它）；
  提示每句声明点名的工具，工具调不到这句不说、主动作说不了整条不说；放在 `read_run_report` / `try_run` 返回最前面。
  应用试运行真跑完才记一条 `{n, before, after, hint, changes}`（`changes` 由 `run_tool` 在计入 `changes_attempted` 处收集），审计 `attempt` 字段只记这些码。
  看护：`tests/test_fixtools_hints.py`（含「提示点名的工具都在注册表里」）。
- **已知问题表**（ADR 0221 §二，`resources/known_issues.json` + `engine/knownissues.py`）：按版本 / 平台 / 自检阶段匹配的**已知缺陷**条目，与 `hints.py`（按失败形状的通用提示）分工、不重复；
  只随版本发、只按结构化字段（错误码、`selftest_stage`、`worker_code`、`os`、版本区间）匹配。命中后 `known_issue: {id, summary, fixed_in, workaround}`（语言取 `ctx.locale`）挂进
  `read_run_report` 最前、`install_packages` / `change_python` 失败文本末尾；`prepare_diagnostics` 在反馈说明首行写 `known_issue_id:`（不新增诊断包结构字段，不升 schema）。
  自检形状缺省取 `envdiag.recent(项目)` 里最近一条同码任务的。看护：`tests/test_known_issues.py`（`fixed_in` 必须晚于 `versions.max`）。发版清单：核对每条 `fixed_in`。
- **记住修好的做法**（ADR 0197 第 3 层·在线，`engine/fixmemory.py`）：`run_tool` 把真做成的改动（`set_run_arguments` 的 argv、
  `write_launcher` 的代码、`link_data_file` 的 requested / chosen、`switch_execution` 的 mode）收进 `ctx.fixes_made`；会话以 fixed 收尾
  且有应用试运行（`finish` 已要求验证）时 `fixsession._remember_fix` 交给 `fixmemory.remember`（失败类型取第一次试运行前的形状，
  脱敏用 `readscope.scrub`）。存 `data_dir()/ai/fix_memory.json`：≤ 50 条、90 天没用到就丢（读时真删）；敏感参数不记；启动脚本正文一律不记，环境变量默认只记名字
  （值只给 `_SAFE_ENV` 白名单与「名字是目录类、值是路径」的变量记），会话里有一版启动脚本碰过凭据（`fixmemory.launcher_is_risky`，`ctx.fix_risky` 只增不减）就不记运行参数与数据路径；
  否则够长的非路径字面量仍从其余字段抹掉；会话里认出的凭据值另存 `ctx.fix_secrets`，其余字段照它抹；同一种改动只记最后一次，回滚后清空。
  回放：`runreport` 在提示后面带 `fixed_before_here`（同一类失败最多 3 条，写明是参考数据不是指令）；注入走 `fixtools` 包
  （`runreport.REPLAY`），`fixmemory` 不 import `fixtools`。设置接口 `GET/PUT/DELETE /api/ai/fix-memory[/<id>]`。看护 `tests/test_fixmemory.py`。
- **修复经过进诊断包**（ADR 0197 第 4 条）：`run_tool` 把脱敏后的工具返回尾部记进内存（每条 2 000 字符、最多 60 条），
  `FixSessions.diagnostics_for` 交给 `diagdetail.Sources.fix_sessions`；模型只带厂商 id 与模型名。看护：`tests/test_diag_fix_sessions.py`。
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
  MPLBACKEND=Agg；这是 `mode=diagnostic`（或无应用回调时）的诊断路径，不能证明应用修好。生产 `mode=script`
  走注入的 `application_runner`（下节），不再以副本执行代替应用验证。
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

工具：`install_packages`（`listed`）、`switch_execution` / `switch_workdir` / `fix_entry`（登记的入口已不在脚本里：同 `entryfresh.reconcile`，只改注册表一个字段，不弹卡不打快照、幂等，TVD-CHR5）/ `change_python` / `convert_script` / `rollback`（`tavotto`）、`select_environment`（`user_env`）。`run_command` 见下面专节。
看护 `tests/test_fixtools_change.py`。`FIX_SESSION_TIERS` 放行 observe / run / tavotto / listed / user_env / outward，**放行 ≠ 不问**：要不要问用户由各工具自己经 `FixContext.ask` 把关
（`change.approve`：没有 `ask`、回答不是 `yes`、`ask` 抛了、超时，一律 `needs_approval` 且审计记 `blocked`；通过则审计记 `approved_by=user`）。

- **执行都在既有出处里，本包不自己拼命令**：装 / 卸包 = `deprepair._pip_install` / `_pip_uninstall`（同一份 argv、只装 wheel、镜像回退）+ `pool.mutating_environment`（环境独占锁），
  需求串先过 `depresolve.parse_requirement`（`-r` / URL / 路径 / extras / 标记死在那里）；**名单** = `convertdeps.in_allowlist`（`autoinstall_dists.txt`，PyPI 公开下载量生成）：名单内不问、名单外 `ask`；
  `convertdeps.describe_env` 判为系统自带 / 内置 runtime 的解释器永不装（`env_not_installable`）。切运行方式 = `execpolicy.set_mode`（`safe` → `safe_only`，`native` → `compatible`；
  把用户明确选过的 `safe_only` 放宽要先问）。换环境 = `projectenv.remember(..., automatic=False, trigger="user_selected")`。
  执行器全部可经 `FixContext` 注入（`installer` / `uninstaller` / `freezer` / `python_changer`）；**测试一律注入假的，不往真实环境装包**。
  `change_python` 缺省接 `executors._default_python_changer`（#1342 `pyversionfix` 的公开入口 `plan_python_change` → `apply_python_change`：只在 Tavotto 受管环境另建、不碰用户环境、私有 Python 锁里没有的版本不下载；本机关了 `TAVOTTO_AUTO_PYTHON_CHANGE` 或版本推不出就如实报错，不换；修复会话暂无进度事件通道，下载进度不推前端）。**依赖安装在途先等**（TVD-22EY）：`apply_python_change` 开头若 `deprepair.installing(project)`（读 `_active_jobs`）为真，等它结束（不设时钟上限，维护者裁决 2026-11-02：只要这个项目在途作业的进度记录还在变——`deprepair.project_progress`，别的项目的进展不算（Codex #1632 P2）——就一直等；连续 `pyversionfix.INSTALL_STALL_SECONDS`=600 没有进展才判卡住；`INSTALL_WAIT_POLL`=0.5，响应取消；开始等时报阶段 `waiting`，准备卡片显示「正在等另一个安装完成…」）；判卡住或竞态里撞上 `dependency_install_busy` → 执行器映射成 `env_busy`（与 `_default_installer` 同码）并在 message 里写明 Retryable、可再调；`ERROR_IN_USE_BY_NATIVE` 原样透传、不标可重试（要用户自己结束脚本）。工具结果契约没有额外字段，可重试只写在 message 里，可经 `python_changer` 注入替换；`convert_script` 没有执行器（见下，只记交接）。
- **快照**（`snapshots.py`）：除 `rollback` 外每个会改东西的工具执行前先 `snapshots.take`，存 `data_dir()/cache/fix_snapshots/<会话>/<id>.json`：项目设置里 `execution_policy` / `environment` 两个键的原值（含「没有」）+
  数据改指与工作目录设置、目标环境的 `pip freeze` 解析成 `{规范名: 版本}`（只留 `名==版本` 行，URL / 路径行不存）+ 环境目录顶层条目名。**打不出快照就不改**（`snapshot_failed`）；快照 id 进工具返回与审计行 `snapshot` 字段；每会话留最近 30 份。
  `rollback` 只认本会话 + 本项目的快照、id 形状固定（`snap_<时间>_<6 位十六进制>`）；设置**精确恢复**（原来没有的键清掉，并 `projectenv.reset_cache`）；包按 freeze 差异——快照后新装的卸掉
  （`pip` / `setuptools` / `wheel` 不卸），被改版本 / 被卸掉的装回快照里的版本，走同一条 `executors`。
- **`run_command`**：D2a 拆出、#1374 以两档落地，见 D2a 之后的「`run_command`」一节。
- **`convert_script`（交接，ADR 0149 §七 2026-10-24 修订）**：不写任何文件、不打快照、不弹卡，只在 `ctx.handoff` 记 `{kind:"convert", script:<项目相对 posix 路径>, library}`，让会话收尾后界面接上 ADR 0143 的「生成可编辑版本」流程（校验 / 对照 / 采用，原脚本不动）。
  脚本必须在项目内（解析符号链接后）、是 `.py` 文件；`library` 取 `rasterhint.LIBRARIES`，缺省取准备报告里 `no_figure_hint`（`raster_script`）指认的库，两者都没有 = `bad_arguments`；`ai_convert.enabled()` 为假 = `unavailable`。
  `finish` 的 outcome 多一个 `convert`：只有 `ctx.handoff` 已设置才合法（否则 `bad_arguments`）；会话公开状态在 outcome 为 `convert` 时带 `handoff`（只 `kind` / `script` / `library` 三键），审计照记 `finished: convert`。内置循环与 CLI 的 MCP 回调共用同一个 `finish`。
- **#1361 三条**：`_copy_project` 只复制普通文件（FIFO / socket / 设备及指向它们的链接跳过、记 `skipped["special"]`、结果 `copy_incomplete`，复制前再 `S_ISREG` 核一次防枚举后被换成 FIFO）；
  `list_environments` 列满 30 条时如实报 `[showing 30 of N …]`，本项目当前 / 选过的环境排前，`match` 参数按名字 / 来源 / 路径片段筛（被列出的才登记 id）；`_copy_project(…, reads)` 回传实际复制了内容的链接目标，`try_run` 逐个 `ctx.note_read(…, "try_run")`。
- **freeze 的「可用」按成败判（#1371 P1）**：`default_freezer` 成功回文本（全新空环境是 `""`）、失败回 `None`；快照的 `freeze_available` = 拿到了没有，不按输出是否为空。

### `run_command`：任意命令，两档（`fixtools/command.py` / `fixtools/cmdsandbox.py`，#1374，ADR 0149 §七修订）

`tier=user_env`、`waits_for_user`。看护 `tests/test_fixtools_command.py`。参数：`argv`（列表）或 `command`（字符串，`shlex` 拆，**不经 shell**）二选一；`timeout_s`（默认 60、上限 300）；`outside_sandbox`（布尔，第②档开关，必须配 `reason`）；`reason`（一句给用户看的话，≤300 字，没沙盒的机器上可不给）；`cwd`（`scratch` 默认 / `project`，`project` 只给第②档）。
先过**硬拦表**（`hard_block_reason`，命中**不问用户**直接 `hard_blocked`，审计 `blocked`，两档都适用）：sudo 类、磁盘 / 关机类、起 shell（先剥掉 `env` / `nohup` / `timeout` / `nice` 等包装器再判）、管道进 shell、shell 语法、`dd of=/dev/…`、对根 / 盘符 / 主目录的递归删改权限、
**宿主代理入口**（`osascript` `launchctl` `open` `systemd-run` `systemctl` `loginctl` `busctl` `gdbus` `dbus-send` `xdg-open` `xdotool` `docker` `podman` `nsenter` `crontab` `at` `schtasks` `reg` `wmic` …）、指向 `ReadScope` 硬拦区的参数。硬拦表是**第一道**，不是安全边界——沙盒才是（`env_contains_project` 之类的结构检查也在问用户之前）。

- **第①档（默认，加固沙盒内）**：`cmdsandbox.sandbox_kind()` 找 sandbox-exec / bwrap 并**探一次**（加固后的沙盒里真能跑 Python 才算可用）；没有 → 这台机器只有第②档，默认调用**直接弹第②档的卡**（见下「Windows / 无沙盒」）。可写清单 `command_writable_dirs`：命令自己的临时目录（`data_dir()/cache/fix_commands/<uuid>`，cwd 与 TMPDIR / TEMP / TMP 都是它，用完 `trial._remove_tree` 带重试清理）、
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
  参数开头的 `~/`（只读工具脱敏后的主目录路径）在政策检查、审批和执行之前统一展开；不启用 shell，也不展开变量、通配符或命令替换。
- **进程树**：命令的直接进程退出（或超时被叫停）后，`_reap_tree` 结束并**确认**整棵受管进程树退出，才收输出、才核对 / 放锁。POSIX：命令在自己的新会话 / 进程组里，`killpg(SIGKILL)` 到 `ProcessLookupError`（组里一个不剩）才算干净；Windows：Job Object（`KILL_ON_JOB_CLOSE`）终止并等活动进程归零，起不来退回 `taskkill /T`（Windows 路径未在 Windows 上实测）。确认不了时结果带 `processes_left`。这样后台孙进程既绕不过时限，也不会在第②档核对、放锁之后才去改脚本。盲区：自己 `setsid` 逃出进程组的进程 POSIX 上追不到（Linux 沙盒内有 `--unshare-pid`，随 init 退出全被收掉）。
- **快照与可写清单同源**：快照覆盖 `writable_envs` 里**每一个**命令获准写入的环境（`change_python` 新建、只靠 `env_prefixes` 进来的都算；没登记 id 的现场登记 `cmd_env_<hash>`），不只会话创建时的前缀。
- **Windows / 无沙盒**：#1358 之前没有沙盒，只有第②档（备份 + 单独点头照旧）：不带 `outside_sandbox` 的调用也直接弹第②档的卡，不返回错误让模型重试（TVD-V7R4，模型没重试时用户点了批准却什么都没发生）。没给 `reason` 时卡上没有 `reason` 字段，前端显示 `aiFix.outsideNoSandbox`。这不算自动升级：有沙盒的机器上第①档失败仍只提示。
- **Windows 的程序名**：不经 shell 时 CreateProcess 只补 `.exe`，所以审批之前由 `_windows_program` 解析不带路径的程序名：`conda` → 环境所属安装的 `Scripts\\conda.exe`（`launcher.find_conda_exe`），PATH 上的 `.bat` / `.cmd` → 全路径；卡上显示解析后的路径。盲区：批处理由 cmd.exe 解释，参数里的 `&` 等不经我们转义（同 `.bat` 全路径，用户在卡上看到完整命令，第②档本来就能跑任意程序）。
- **审计行**：多 `sandbox ∈ {hardened, outside, none}`（`none` = 没执行：被拒 / 没沙盒 / 未获同意）与 `command_tier ∈ {1, 2}`；命令、理由的文字**不进审计**（只记长度），`outside_sandbox` 布尔照记。执行前打快照（设置 + 目标环境 freeze）。
- **活动与看门狗**：前端把 `activity.tool=run_command` 映射成「正在运行命令…」；命令在跑是在途工具，不算卡住；等卡片也不算。
- **已知盲区（如实）**：
  - 第①档 Linux：**抽象命名空间 unix 套接字**仍可达（X11 在 Linux 上也走抽象地址；不 `--unshare-net` 才能联网装包）。seccomp-bpf 看不到 `connect` 的地址，不能「只拦抽象地址」；整个禁 `socket(AF_UNIX)` 会弄坏 asyncio / multiprocessing 且可用 `socketpair` 绕——不做。
  - 第①档：联网 + 能读项目与主目录 = 命令可以**外传它读得到的东西**；只有 `DENY_HOME_RELATIVE` 里的凭据位置被藏起来，项目里的 `.env` 之类靠硬拦表挡参数、挡不住命令自己去读。环境前缀与 pip / conda 缓存可写 = 命令能在用户环境里放任何东西（装包的本意），缓存可被投毒。
  - 第①档 macOS：sandbox-exec 是 Apple 标 deprecated 但仍可用的接口，`allow default` 基线只显式拒了上面几类；mach 白名单外的服务一律拒，个别系统工具可能因此异常，需要时走第②档或按用例放。沙盒内允许起子进程（包括 shell），硬拦表只看 argv[0]，所以 `python -c` 之类能做的事都在沙盒里。
  - 第②档的备份**只覆盖项目内的用户脚本**：其它文件、项目外、环境、数据文件不保护。命令期间用户在**外部编辑器**里保存同一份脚本（不走 `scriptlock`）会被当成命令的改动，结束后恢复成命令前的版本；命令把脚本换成同名目录 / 链接则恢复失败（如实报）；命令新建的文件不删；基线在内存里，Tavotto 在命令期间被杀就无法恢复。
  - 第②档持锁期间，界面里保存这些脚本会**阻塞等待**（不是失败），命令结束才落盘（最长 300 s 加快照时间）；锁只管走 `scriptlock` 的入口。审批卡开着期间的编辑不是盲区——基线在点头之后才取。

### `link_data_file`：缺数据时把 AI 找到的文件接上（`fixtools/link.py`，ADR 0106 修订 / ADR 0149 §七修订，2026-10-24）

看护 `tests/test_fixtools_link.py`（含真子进程的整链用例）、`tests/test_missing_input.py::test_the_endpoint_and_ai_repair_share_one_link_function`。

- **一律问**：approve 卡 = 模型给的一句话 `summary` + 折叠 `details`（脚本要的串、完整路径、`evidence`、规则含义）。没有 `ctx.ask` / 非 yes = `needs_approval`，规则与快照都不写。
- **判据都在弹卡之前**：默认 `mode=file` 的 `requested` 必须在 `inputremap.known_requests`（报告 `missing_input` 载荷 ∪ 脚本里以常量出现且此刻找不到的路径 ∪ 已经接上的：靠现有规则才读得到的常量与现有 `file` 规则的 `from`，TVD-6Z9R）里（按 `match_request` 规范化比对），否则 `requested_unknown`；其 `via` 不是 `open`（C++ 读取器：ovito `import_file`、h5py 等）→ `link_unreachable`（要改脚本，AI 不改）。Python 的探路（exists / isfile / isdir / stat / getsize / listdir / glob / os.walk）都查改指表，它们问的路径、「先检查、再自己 raise」认出的路径与 stat / getsize / listdir 落空直接到终局认出的路径都按 `open`，接得上；glob 与文件夹条目推文件夹规则（ADR 0106 修订 2026-11-09 / 2026-11-14）。
  `chosen` 过 `ctx.scope.check`（凭据位置 `denied`、范围外 `outside`）、存在、`inputremap.rule_for_choice` 当场推得出规则，才打扰用户。
- **同一套推导与落盘**：文件模式写入只经 `inputremap.link`（= `rule_for_choice` + `add_rule`；`POST /api/engine/input-remap` 调的也是它）。快照先于写入，`settings_keys()` 含 `input_remap`；
  回滚经 `inputremap.restore_raw`（项目锁 + 换代），**只在改指表真的变了才动**，别的回滚不让它换代。应用侧通知 `FixContext.on_input_remap_changed(state, "added"|"restored")` 由 `app._fix_remap_notifier` 在 `bound_project` 下做收会话 / 取消试运行 / SSE（工作线程没有请求上下文）。
- **`try_run` 带规则**：副本里的守卫进程按文件加载 `figcapture`（`TAVOTTO_TRIAL_ENGINE_DIR`，用完摘掉 `sys.path` 与模块）并 `install_input_remap(rules_for(项目))`；规则里落在被复制脚本目录内的绝对 `from` 另加一条换算到副本路径的（`trial_remap_rules`，脚本用 `__file__` 拼路径时才命中）。
  装不上如实报 `input_remap: …could not load…`，不静默当没有。自定义 runner 仅在有规则时多收一个 `input_remap` 关键字。
- **已知取舍**：同一处的旧 `file` 规则（`remap_target` 里 `file` 优先）会盖住后来的 `prefix` 规则——与弹窗行为一致，没有为此改规则优先级。重新指认已接上的一串时，`inputremap.link` 把会被旧 `file` 规则盖住的新 `prefix` 规则收窄成 `file` 规则（替换旧的），新选择不会白选；`read_run_report` 的 `data_links` 列出生效规则，供 AI 判断「读到的是用户指认的那份」；每条的 `requested` 是 `known_requests` 里经这条规则改道的具体串（`prefix` 规则的 `from` 只放 `applies_to`，拿它当 `requested` 会 `requested_unknown`）。
- **旧前缀没有具体文件名（TVD-DSSV）**：动态拼出来的文件可能没有留在本轮报告或规则里，不从目录前缀臆造文件。`read_run_report` 给现有 `prefix` 规则返回 `prefix_id`；`mode=prefix` 的 `requested` 只接受该标识，`chosen` 是存在且通过读取范围的目录。标识用本进程密钥绑定项目、完整规则与改指代次；重启 / 变代后必须重读报告。变更影响整条前缀，**即使用户刚选过目录也另弹确认卡**，写明所有匹配路径、较具体规则优先及 native 不应用改指；确认后重验目录、在项目锁内核对规则和代次、打快照并替换，竞争时拒绝。目标相同只报无变化。脚本和数据不改；`try_run` 走安全试运行的规则，不能据此声称 native 已修好。

- **诊断三缺口（覆盖率测量补）**：① 项目内同名包遮蔽——`engine/importshadow.py`（纯函数、只读文件系统）对 `No module named 'a.b'` / `cannot import name … (项目内路径)` 判「父包目录在脚本目录 / 项目根下、缺的子模块不在里面」（脚本目录在 `sys.path[0]`、先于已装的包；项目根被 worker 追加在末尾、排在 site-packages 之后，所以项目根里的同名包要 traceback 里有一帧落在它里面才算，`search_order=after_site`，装同名发行包能修）；`read_run_report` 与应用 `try_run` 共用 `runreport.project_run_report`，以当前错误为准、`project_package_shadow` 放最前，历史失败不覆盖当前判断；诊断副本 `try_run` / `try_import` 按自己的导入范围调用 `base.shadow_note`。`regular` 包装同名包不管用；无 `__init__.py` 或扩展包路径的 namespace 可由安装真包修复，提示词 3f 同此。② 安装失败只按安装器输出分类，并带脱敏后的 `ERROR:` 一两行（`detail`），由 `install_packages` 回给模型；不再另起 `pip index` 探测，以免忽略私有源设置并泄露包名。`dependency_not_found` 不断言包不存在，也可能仅没有当前 Python / 平台的可用版本；取消事件贯穿安装。③ `read_logs(worker)` 按 `diagnostics.owned_worker_logs(项目)` 取当前项目最近一份日志。看护 `tests/test_fixdiagnostics.py` / `tests/test_fixdiagnostics_report.py`。

### `ignore_import_folder`：脚本自带的私有依赖文件夹为别的 Python 版本装的（`fixtools/importpath.py`，ADR 0154，2026-10-31）

看护 `tests/test_fixtools_importpath.py`（工具 / 回滚 / 方向路由）、`tests/test_foreign_abi_path.py`（识别、过滤器、端到端）。

- **失败码 `import_folder_abi_mismatch`**（报告 `error.code` / `params.folder|abi|current`）：换 Python 版本救不了——那个目录永远是为别的版本装的（TVD-ZCR4 里 AI 两轮 `change_python` 要 3.12 都是 `python_change_unavailable`）。
  系统提示 3h 明令不换版本、不在它上面装包，改用本工具；`change_python` 在报告错误码是它时直接 `python_change_not_applicable`（不打快照、不建环境）；`pyversionfix.FAILURE_CODES` 不含它，`failure_from_report` / `required_span` 认不出。
- **失败码 `image_format_mismatch`**（TVD-3MWS；`params.claimed|actual|ext` + 可选 `file`）：图片内容与扩展名不符，直接用 Python 跑同样失败。系统提示 3i：不怀疑安全运行 / 环境、不改脚本、不动用户图片，请用户换成真正的文件；`change_python` 对它直接 `python_change_not_applicable`。
- **工具不收路径**：忽略哪个文件夹由失败那一刻 Tavotto 登记（`pathignore.latest_path`，随令牌签发），模型指认不了任意目录；没有登记 = `no_foreign_folder`，不打扰用户。
- **一律问**（approve 卡 = 一句话 `summary` + 折叠 `details`，如实说明这是对「与终端里一致」的偏离、脚本与文件夹都不动、可撤销）。没有 `ctx.ask` / 非 yes = `needs_approval`，名单与快照都不写。
- **同一条路径**：写入只经 `pathignore.remember`（失败卡主按钮的令牌端点调的也是它）；名单变了才调 `FixContext.on_ignored_paths_changed`（应用侧 = 收掉这个项目的会话，`--ignore-paths` 是 spawn 时交的）。
  快照 `settings_keys()` 含 `ignored_import_paths`，回滚精确恢复（含「原来没有」），名单变了同样通知。

### `set_run_arguments`：AI 填运行参数（`fixtools/change.py`，T03 运行配置，2026-11-19）

- 与界面「运行参数」框**同一份**运行配置（`runconfig.selection_for`）：原样 token、`validate_argv` 校验、不拆空格；空列表 = 不带参数。原脚本不动，`tavotto` 档。
- 应用侧（`app._fix_application_verification` 的 `on_run_arguments_changed`）把修复会话的验证换到「同一脚本目标 + 这组参数」的准备会话（`SESSIONS.check`）；
  修复会话持有的是 `{"sess": …}` 盒子，`try_run`、`read_run_report` 都跟着盒子走。快照记下当时的参数，`rollback` 换回去。
- 修好（`fixed`）且本会话改过参数：状态带 `run_argv`，前端（`aiFixStore`）把它写进这个脚本的「运行参数」草稿再按它重跑验证——用户看得见、改得了。
- 看护：`tests/test_fixtools_run_args.py`（工具 / 回滚 / 真准备会话登记表里 build 拿到参数且报告换到新会话 / 只有 fixed 且改过才带 `run_argv`）、`web/src/store/aiFixStore.test.ts`。
### 两道护栏：用户侧失败不当缺陷报、预算过半要动手（ADR 0149 修订，2026-11-19，兼容性评测的发现）

- `prepare_diagnostics` 带产品缺陷草稿（`issue`）时：本会话**一个改动都没试过**（`FixContext.changes_attempted == 0`，由 `run_tool` 计数：
  `tavotto` / `listed` / `user_env` 档、未被策略挡下、参数合法；报告 / 回滚不算），且失败属于用户侧（码在 `ask.USER_SIDE_CODES`，或 traceback 是
  `os.environ` 的 KeyError / 无父包的相对导入 / `input(` 的 EOFError）→ 回 `not_a_product_defect` 并指向对应动作，不弹「发送」卡。只交摘要（不带 `issue`）不拦。
- `FixToolset.budget_hint`：预算过半 / 过四分之三时 `loop.run` 追加「别再巡视，做最匹配原因的那一个改动再 try_run，没有可做的就 not_fixed」。
- 看护：`tests/test_fix_guardrails.py`。

### `write_launcher`：启动脚本（`engine/launchscript.py`、`fixtools/change.py`，ADR 0194 §二，2026-11-18）

- 启动脚本放 `tavottofile/launchers/<stem>-<路径摘要>.py`，每个脚本至多一份，只认普通文件（符号链接 / 链接目录一律当没有、拒写）；64 KiB 上限、写前本机 `compile` 校验。
- 有启动脚本的脚本：safe 经 `--script-source`、native 经 bridge 同名参数执行启动脚本的字节，身份（`__file__` / `argv[0]` / 描述符）仍是原脚本；入口恒 `__main__`、
  不做运行前路由、不换入口；`preparation._script_revision` 末尾加 `:L<摘要>`（改 / 删启动脚本 = 计划过期；第一段仍是原脚本摘要）。
- 工具 `tavotto` 档（只写 Tavotto 自己的目录），写前打快照，`rollback` 写回旧内容或删除。原脚本与用户代码文件夹一个字节不动。
- 看护：`tests/test_launchscript.py`、`tests/test_launchscript_e2e.py`（真 sidecar：无启动脚本没有图 / 有启动脚本出图且原文件不变 / safe 被拦 native 也执行启动脚本）。

### D2b：「问与报」组 + 修复会话 + `POST /api/ai/fix`（`fixtools/ask.py`、`engine/fixsession.py`）

- **五个工具都 `waits_for_user=True`**：`propose_fix`（`approve` 卡，详情列 `ctx.reads` 的 `~/…` 写法与「没读：敏感目录」）、`ask_user`（`choice` 卡，回选项 id；`other` / `skip` 是保留 id）、
  `pick_file`（`pick` 卡；回答是路径，**先过 `ReadScope.grant` 的敏感硬拦**才加进本会话可读根，只放这一个路径，文件系统根不放）、
  `prepare_diagnostics`（`outward` 档，`send` 卡；工具**只弹卡**，打包与上传全走现成的「发送问题反馈」对话框，用户不点「发送」就不发，永不附源码——`include_source` 只是卡片上的提示）、
  `finish`（非阻塞，把结局交给会话）。`ctx.ask is None` / 回答畸形 / 抛异常 = 「未获同意」（`needs_approval`），超时 / 取消 = `no_answer`，绝不默认通过。
- **会话**（`fixsession.SESSIONS`）：同一时刻一个；id 是 `secrets.token_urlsafe`、比较 `compare_digest`、按项目 id 作用域（别的项目 404）。循环在**本进程**线程里跑（工具要碰应用状态），
  模型只有 `HttpModel` 一条出站路径；密钥只在内存，卡片文字过 `Redactor` + `readscope.scrub`，SSE `ai.fix` 只带 `{pj, fix_id}`（「重读」提示），状态以 `GET /api/ai/fix/<id>` 为准。
  卡片回答 `POST …/answer {card_id, answer, text?}` 按卡种类校验（坏回答 400、过期卡 409）；`POST …/cancel` 唤醒阻塞中的工具。预算：40 轮 / 30 分钟（含用户思考时间）/ 60 万 token；`finish` 之后不再请求模型。
- **连接**：`connection()` 只认当前选中的第三方 API 接口（有地址 / 密钥 / 型号、地址过 `check_base_url`）；只连了 CLI 时由 D3 的 MCP 接法接手（见下一节），`ai_fix_available` 随之为真。
- **应用验证（2026-10-25，ADR 0149 修订）**：生产 `try_run` 的 `script` 模式经 `fixverification` 消费同一准备会话的
  check → 单次运行批准卡 → 原 action id/config_revision → launch/finalize/report；不自批安装或运行意图，不切解释器。
  卡片披露真实 cwd/写入范围/执行方式，旧临时副本授权不代替这次批准；取消只管本次 action，过期/被替代即拒。
  `switch_workdir` 经明确批准与快照走已有 `workdir.set_mode`，改动/回滚后退役旧 worker，守卫不变。
  `finish(fixed)` 须匹配本会话最后一次实际成功执行的 session/revision/plan/attempt；`execution_finished` 不能单独证明修好，
  还须是准备会话的 `completed/succeeded` 或 `partial/execution_finished_no_figure`。缺包待重跑、登记失败、部分图捕获失败
  均不算证据。任何可能改执行结果的工具（含失败后的部分改动）使证据与依赖缓存失效。`no_figures` 允许证明脚本已执行完，
  不证明可编辑；转写仍另行交接。
  `read_run_report` 保留 nested `result.error` 与实际 launch/workdir。前端收尾复查照旧；诊断包仍只记审计摘要。
  看护 `tests/test_fix_application_verification.py`、`tests/test_fixasks.py`、`tests/test_ai_fix_endpoint.py`。

### 转换目标由 Tavotto 代写（TVD-BCZ0 方案 A，`engine/convertwrite.py`、`fixmcp.py` 转换模式，ADR 0143 修订 2026-11-02）

- **根因**：Windows `windows.sandbox="unelevated"` 之下命令能跑，但 Codex 自带的 file_change / apply_patch **一律 `Failed to write file`**（Lab run `20261102T055146Z-8fc66e`，OpenAI 官方模型，Codex 0.164：读完源码与数据、写目标时 file_change `status=failed`，模型按提示词停下，目标缺失）。DeepSeek 那次成功只因为它恰好用 shell 写文件。
- **做法**：转换会话里由 Tavotto 在沙盒外写目标文件。复用修复模式的服务进程：`tavotto fix-mcp --url … --convert-id <会话>`（与 `--fix-id` 互斥）换一个工具集——`tools/list` 是 `fixmcp.CONVERT_TOOL` 写死的一条（不回调），
  `tools/call` → `POST /api/ai/convert/<id>/target {content}` → `convertwrite.write`。服务名 `tavotto_convert`，工具 `write_conversion_target`，参数只有 `content`（整份脚本，UTF-8 文本）；额外参数（如 `path`）在 MCP 层就被丢掉，**目标路径只有 Tavotto 定**（`Grant.target` = `cand.target`）。
- **鉴权**：每个转换会话一枚随机令牌（`convertwrite.open_grant`，`ai_bridge.run` 在 `_cmd` 之前开），**只经子进程环境变量 `TAVOTTO_CONVERT_TOKEN`**（Codex 的 `env_vars` 只列变量名），不进 argv、不进日志；头 `X-Tavotto-Convert-Token`；
  `security._session_guard` 只认「`POST /api/ai/convert/<该会话>/target` + 对应令牌」；会话终态（`_finish_session`）/ 被拒绝的尝试（`_drop_attempt_files`）即 `close`，另有 3 小时兜底寿命。令牌错 / 别的会话 / 已结束 = 401（guard）或 403 `convert_tool_forbidden`，不区分原因。
- **上限与失败码**：`MAX_BYTES` = 2 MiB（超限拒绝，不截断）；闭集失败码 `bad_content | empty | too_large | target_unsafe（目标是符号链接/目录）| write_failed`；写入走 `atomicio.write_bytes`。只记闭集码与字节数（`cand.extras["target_tool"] = {calls, ok, last_code, bytes}`）。
- **注入**：`CodexAgent.build_command` 在**所有平台**追加 `ai_agents._codex_convert_mcp_args(ctx.convert_mcp)`（本次调用级 `-c mcp_servers.tavotto_convert.{command,args,env_vars,tool_timeout_sec,startup_timeout_sec,default_tools_approval_mode="approve"}`，与修复会话同一写法）；Windows 上与 `windows.sandbox="unelevated"` 并存。
  适配器声明 `convert_write_tool = True` 才开（只有 Codex）。**Claude Code 不加**：它自带的 Write 不经 Codex 的 Windows 沙盒，没有失败证据，改它会动一条已验证的路径；内置执行器本来就由 Tavotto 写文件。
- **提示词**：`ai_prompt_pack.convert_prompt(write_tool=True)` 用 `rule_write_tool` 替换 `rule_write_fail`——“写目标文件只能调用 MCP 工具 write_conversion_target（参数 content 是整份脚本，一次写入）；不要用文件编辑工具、apply_patch 或命令写目标文件。如果这个工具返回失败，直接说明它给出的失败原因并停止，不要换别的方式或位置写。”；Windows 的 cmd.exe 一条保留。`prompt_for` 读 `cand.extras["write_tool"]`（桥按适配器声明写）。
- **判定**：写入之后的校验 / 写回流程不变。`ai_convert.write_denied` 增第三种形状：`target_tool.calls > 0` 且没有一次成功 → 目标缺失时判 `agent_write_denied`（不可修正）。运行事实多一项 `target_tool_ok`（布尔，权威来源是 `convertwrite.ok`，不看事件；`merge_run_facts` 取或，没有该工具的会话是 None），
  登记进 `exectrace.ATTRIBUTES` 的 `cli_target_tool_ok` 与「AI 会话结束」日志的 `cli_target_tool_ok=`。
- **看护**：`tests/test_convert_write_tool.py`（工具本体、令牌、HTTP guard、fixmcp 转换模式、argv 各平台、提示词、`agent_write_denied` 新形状、假 Codex 端到端）、`tests/test_windows_regressions.py::test_codex_convert_on_windows_has_unelevated_sandbox_and_tavotto_write_tool`。

### Windows 转换「不受沙盒限制」开关（TVD-BCZ0 方案 B，叠在方案 A 上）

- **语义**：设置里一个开关「转换时允许 AI 不受沙盒限制」（存 `config.json` 的 `ai.convert_unsandboxed_windows`，**默认关**，只认明确的 `True`；入口 `config.convert_unsandboxed_windows()` / `set_…`）。**只在 Windows 上显示与生效**：`/api/ai/capabilities` 只在 Windows 给 `convert_unsandboxed_windows` 这一键（前端据此决定显示），`PUT /api/ai/convert-unsandboxed` 非 Windows 返回 400。界面只有这一行开关，不加说明 / 警告横条（用户硬性要求）。
- **argv 规则**：`ai_agents.codex_sandbox_policy(ctx)`——只有 (Windows ∧ `ctx.convert_task` ∧ `ctx.convert_unsandboxed`) 才是 `--sandbox danger-full-access` 且**不再带** `windows.sandbox` 覆盖；其余（改图 / 修复会话、非 Windows、开关关）与方案 A 逐字一致。「是否转换」与设置值由 `ai_bridge` 构造转换命令处（`cmd_kw`，含修正轮）**显式**写进 `RunContext`，适配器不读全局配置。方案 A 的代写 MCP 工具照旧注入。
- **诊断**：闭集事实 `sandbox_mode`（`workspace_write` / `unelevated` / `unsandboxed`，`ai_agents.SANDBOX_MODES`），由**实际 argv** 反推（`sandbox_mode_of`），进「AI 会话结束」日志（`sandbox_mode=`，经 `logsafe.known` 明文）与 exectrace `agent_run` 的 `cli_sandbox_mode`。诊断包里据此看出用户开没开。
- **看护**：`tests/test_convert_unsandboxed.py`（四种组合 argv、关时与方案 A 逐字一致、配置默认值与读写、诊断事实、API）、`tests/test_windows_regressions.py::test_codex_windows_convert_switch_picks_danger_full_access_or_unelevated`、`web/src/components/settings/ConvertSandboxRow.test.tsx`。
  **待 Windows Lab 用官方模型验证**：真 Codex 0.164 在 unelevated 沙盒里能否拉起 MCP 子进程（`tavotto-cli.exe`）并调用该工具（修复会话的 MCP 在 Windows 上同样依赖这一点）。

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
  等卡片（`state=waiting`）不算；在途工具有独立上限（`TOOL_TIMEOUT` 与本会话 `limits.max_seconds` 的较小值），到点显示 `stalled` 并传取消，工具真正退出前不释放占位。卡住持续 `STALL_HARD`=300 s 自动取消：`stall_code` 记下，`_settle` 判 `failed` + 该原因码；内置循环的模型请求挂住、循环线程回不来时 `FORCE_SETTLE`=10 s 后看门狗强行收尾（`_settle` 幂等，迟到的循环线程不再改结局）。
  卡住与自动取消、Claude 的权限拒绝（`cli_permission_denied`）都写 `fix_audit.jsonl`（`tool=_session`）。
- **Codex 的取舍**：`--ignore-user-config` 仅在 `endpoint_args` 非空（Tavotto 显式给了 provider）时加并带 `-m <接口型号>`；官方登录态不加。**没核实的**：Codex `--json` 里 MCP 调用 / 审批事件的确切字段名（解析器按 `item.*` 的 `type` 含 `mcp` / `approval` 写，且对未知事件一律当「有输出」）。
- **`change_python` 的旧代租约与回滚（Codex #1383 P2）**：换到新一代后 `deprepair._run_generation` 会 `managedenv.retire_unused` 删掉没人占着的旧代，所以 `change_python` 动手前给项目当前用的受管环境那一代
  （`managedenv.python_of` 与设置里记着的受管解释器）加 **保留型**租约 `envlease.acquire_retain`（`fix:<会话>`；与 native 占用在同一模块、两种用途：保留型只让 `deprepair._generation_in_use` 不清这一代，**不**被 `envlease.mutating()` 当成 native 会话拒绝——用 native 租约会把换代事务自己挡死；真正的 native 会话仍照旧拒绝换代），换成功则持到会话收尾（`_settle` → `ctx.release_leases()`，finish / 取消 / 失败 / 自动取消都走），没换成立即放（`release_retain`）。
  快照多存 `managed = {generation}`；`rollback` 先核旧代解释器真的在（不在 = `rollback_target_gone`，**一个字不改**），再恢复设置、`managedenv.activate` 指回旧代并让池 / 缓存放弃新一代，恢复后核设置里记着的解释器在不在（不在同样 `rollback_target_gone`，不报成功）。
  `FixContext.cancel`（= 会话的 `s.cancel`）同样传给 pip 安装/卸载执行器（含逐项执行前复查），并传给缺省换 Python 执行器 `_default_python_changer(req, cancel)`，规划完、动手前先看一眼（与 #1381 同一件事）。看护 `tests/test_fixrollback_managed.py`。

### 2026-10-25 补充参考与会话期限

转换缺少输入时，用户在原因卡补充项目相对路径，显式重试提交 `reference_files`；原因中的多行代码完整保留在卡片正文，超限不截断命令；全流程见 ADR 0143。
`convertcontext.selected_paths` 是读策略的共同入口，不能绕过敏感路径、链接、脱敏和摘要校验。
修复会话的总期限从创建开始计时（包括等卡片确认），与单工具期限分开。总期限到达记 `budget` 并传播取消；
工具收尾期间保留占用，不把已有取消/期限结局重新标成 stalled。看护 `test_fixstall.py`、`test_fixmcp.py`。

### 转换的保真失败与协议回放

`convertfidelity.raster_drawing` 只报告候选 AST 中能静态指认的 Pillow 像素绘制调用；
字体度量与照片读取放行，存在重绑定的不明确接收者不据此判错。内置 `check` 与最终采用前检查共用判据；
`raster_drawing` 阻止把“Pillow 画完再 imshow”称为可编辑转写。没有命中不是全面保真证明。
看护 `test_convert_fidelity.py` 和 `test_ai_convert.py` 的真实会话收尾用例。

长转换允许单响应 32768 token（含模型思考），建议按约 6000 字符分块；完整接收的代码块不因超过建议值而丢弃，
实际创建 / 覆盖 / 累计追加仍核同一个 200 KiB UTF-8 文件上限。所有调用仍受配置的 token、时间、轮数预算约束。
共享 loop 截断后只保留协议可回放的思考状态，拒绝该轮文字与工具，反馈让模型继续；连续三次截断终止，正常响应重置连续计数。
成功工具结果接替临时续写状态；manual thinking 保留签名前缀，其余 Anthropic 历史变化后清失效签名。转换的写入拒绝保留有界原错误现场，不能只反馈 mkdir 建议。
错误参数经同源 schema 反馈；非对象参数仅在回放时换成空对象并配对原错误结果，不能执行。缺失/重复调用 ID 整批拒绝并反馈，连续三次终止。
恢复不能绕过取消、校验次数、权限与用户确认；思考只在模型历史中，不进日志。完整契约与看护见 ADR 0147「共享恢复循环」。
默认单次请求 300 秒，总期限仍会截断它，用户取消仍随时生效。
Anthropic adaptive thinking 的签名绑定历史前缀；客户端压缩工具结果后删除历史内全部 thinking / redacted_thinking 块，
保留工具调用及结果，不改用户推理强度。manual/budget 模式要求末轮思考块，保持追加历史并沿用总预算。
依据：[官方排错规则](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting)。
看护 `test_agent_runner_media.py` 的接收端前缀验证，不能只断言请求 JSON 形状。
内置失败只把闭集短码沿 Task Core 传给卡片；不传供应商错误体、模型思考或用户内容。

### 参考图尺寸与上下文预算

`imageheader.image_dimensions` 是 PNG / JPEG / GIF / WebP 画布尺寸的标准库只读入口，
转换对照与内置模型图片预算共用。WebP 的 VP8 / VP8L / VP8X 分别读取图像或扩展画布头；
不解码像素、不把头部识别当作完整文件校验，未知 / 截断头继续保守估算。
`read_image` 向模型交付实际像素宽高；不能把小 WebP 当作未知大图触发压缩、反复丢掉源码与参考。
Windows 的路径 stat 与句柄 fstat 可能给 ctime 不同含义，只在同一 API 内比较变化时间，
跨 API 仍比较设备、inode、大小和 mtime；读取前后的身份校验与拒绝替换不变。
看护 `test_image_header.py`、`test_agent_runner_media.py`；后者直接断言接收端后续请求仍带源码和图像。

- **应用内托管安装 Codex（`engine/managedcli.py`，锁 `resources/managed_cli_lock.json`，2026-10-29）**：`start_install("codex")` 在锁里有
  的平台（darwin arm64/x64、win32 x64、linux x64）走托管：registry.npmjs.org 的 `@openai/codex@<版本>-<平台>` 主 → registry.npmmirror.com
  上同一个 tgz（字节相同、同一个 sha256 / integrity；解开后整棵树 `tree_sha256`）。2026-11-04 起不再用 GitHub 的
  `codex-package-<triple>`：0.166.0 起它比 npm 包多一个 `bin/codex-exec-server`，两来源解不出同一棵树（#1716）；**大陆环境镜像在前**（`cli_for` 稳定重排；地区只读壳写进 `TAVOTTO_NETWORK_REGION` 的结论、经 `netregion.region_is_cn`，ADR 0121 唯一判据；#1744：大陆直连 npmjs 约 2 MB/s，慢不到换源门槛，每次白等约一分钟）；慢了换源（判据同私有 Python，ADR 0112）、hash 不符不重试不换源；装到 `data_dir()/cli/codex/<版本>-<树摘要>/`，
  `current.json` 原子切换；`search_locations` 首位 / `candidates` 在 PATH 之前，source=`managed`。**装好不登录**
  （登录由前端在 done 后调既有 login 接口）。其余平台与其它 Agent 回落 npm。升版本用 `scripts/managed_cli_lock.py`
  （`--update` / `--check [--online]` / `--latest`）；**每次发版都升到 npm latest**（`docs/RELEASING.md`「发一个新版本」第 2 步，
  用户 2026-11-04 拍板：锁落后于桌面版写进共用型号清单的版本时，`cli_update` 给不出「应用内装」，用户选到新型号只能自己更新）。install 状态 `{status, progress 0–1, phase, via, code}`；错误码见 `managedcli.ERROR_CODES`，
  文案在 `errors.json` 的 `engine.managedCliError.*`。传输层与 privatepython 同判据但独立实现（理由见模块头）。
- **应用内托管安装 Claude Code（同一套机制，2026-10-29）**：锁里 `clis.claude.sources` 逐项声明来源（`SOURCE_PREFIXES` 登记每个 CLI 允许的
  来源键与 URL 前缀）：registry.npmmirror.com 的 `@anthropic-ai/claude-code-<平台>`（tgz，`only` 只取 `package/<二进制>`，
  package.json / 许可证不进树）主 → `downloads.claude.ai/claude-code-releases/<版本>/<平台>/<二进制>` 裸文件（`raw`：下载物就是入口文件，
  sha256 取自官方 `manifest.json`）；两个来源的二进制**逐字节相同**（`--update --cli claude` 时核）。**条款红线**：只原样下载 + 校验 + 解包，
  不改、不重打包二进制；每个用户用自己的凭据；Tavotto 不做 Claude.ai 登录页、不收集 / 存储 / 转发 token——登录完全是 claude 自己的
  `claude auth login`（`LoginSpec` background，无 TTY 可用；授权地址只从输出里取主机精确命中 `claude.ai` / `claude.com` /
  `platform.claude.com` 的 https 地址）。托管那份的子进程环境带 `DISABLE_AUTOUPDATER=1`（`managedcli.child_env`，由 `ai_agents.spawn_env`
  统一加；版本只由锁控制）。就绪判定仍是 `claude auth status`（只取 `loggedIn`）。前端文案 `engine.managedCliError.*` 带 `{{product}}`，
  Codex / Claude Code 共用；订阅登录卡（`MethodList`）在 agent 没装时不置灰，点开走 `AgentStepAction`「安装并登录」。
  升版本：`python scripts/managed_cli_lock.py --update <版本> --cli claude`。

### 无输出看门狗（`ai.stalled`）

`ai_bridge._SilenceWatch`：每个 CLI 进程一个，只观察。stdout 静默达 `STALL_AFTER_S`（120）发一次 `ai.stalled{session, stalled: true, idle_s}`，再有输出发 `{stalled: false}` 复位；**不杀进程**（慢慢思考的模型会被误杀），总超时 `TIMEOUT_S` 不变。状态只在看门狗对象里、不进会话字典。前端 `aiStore.setStalled` 把它翻译成状态行「AI 已经 N 秒没有动静」+「取消 / 重试」（重试 = 取消、等收尾、再原样重发）。看护 `tests/test_ai_silence_watch.py`、`assistantStalled.test.tsx`。

### 外部 CLI 的沙盒与写入行为在目标平台真跑（错题本 L-130 升级，2026-11-09）

接外部 CLI 的写入、沙盒，或「失败后指望模型换参数重试」这类行为，目标平台用真 CLI 跑一次；假 CLI 绿只证明我方编排。
进不了 CI 就在 PR 写明未验，并排进 Windows Lab。按平台分档的能力，先列每个平台默认落在哪一档，再让那一档的完整流程在
该平台走一遍；恢复路径在工具控制流里接通明确授权，别只靠模型按提示自行重试。为什么：转换链路用例全用不带沙盒的假 Codex，Windows unelevated 沙盒里自带写入工具一律失败（#1604）；
写大文件时事件流静默、写入被拒后换位置写，都是真模型才复现（#1628）；2026-11-07 一份用户反馈：Windows 没有第①档
沙盒，命令一律返回 `sandbox_unavailable`，设计指望模型带 `outside_sandbox` 重试，真模型没重试，用户批准修复后什么都没
发生（#1793）。看护：只靠人记。

## 改图助手的附件（ADR 0175，`engine/ai_attachments.py`）

用户附的文件（图片或任意数据文件）经 `POST /api/ai/attachments?name=`（请求体 = 文件字节）暂存到 `data_dir()/cache/ai_attachments_staged/<id>__<安全文件名>`（1 h 过期、× 即删）。图片按魔数认 PNG / JPEG / GIF / WebP（≤ 4 MiB、≤ 4000 万像素），其余单个 ≤ 25 MiB、一次合计 ≤ 50 MiB、≤ 6 个。`/api/ai/run` 只带 `attachments` id（`valid_ids`）。`ai_bridge.run(attachments=)` 经 `_attach_attachments` 把文件**移**进工作目录（`materialize`，重名加序号，合计超限之后的静默跳过）：

- 有候选（TAF / 内置执行器）：`<候选>/_attachments/<名>`，提示词给相对路径；`ai_figure_edit._tree` 排除候选根下的这个目录（`ATTACHMENTS_DIRNAME` 唯一出处在 `ai_attachments`），所以不算「改了别的文件」，也不会被写回；随候选一起清。
- 无候选（外部 CLI 原地改）：`data_dir()/cache/ai_attachments/<sid>/`，提示词给绝对路径，Claude 经 `RunContext.read_dirs` 多一个 `--add-dir`（只在附文件时出现）；`_finish_session` / `_drop_attempt_files` 删，24 h 兜底。**绝不写用户项目目录。**
- 提示词段 `ai_prompt_pack.attachments_note` 不进 `V1` 闭集（同转换提示词一节）：「参考图」与「数据 / 其他附件」分别列路径；不附文件时整段一行都不执行，请求 / 提示词 / 工作区 / 历史行逐字节不变。
- `sessions.attachment_count` 只存个数；`SESSION_DTO_KEYS` 带 `attachment_count`。文件不进历史、遥测、诊断包。
- 能力清单的 `no_vision`（执行器与连接上的可选字段）来自 `ai_attachments.known_text_only`：只列**确定**看不了图的型号，判不出的不列、照常发送；前端不镜像名单。
- 看护：`tests/test_ai_attachments.py`。

## 无工具单轮结构化调用（`engine/ai_structured.py` + `ai_data_model.py`，ADR 0178 修订 2026-11-19，第 0 步地基，无调用点）

给「模型只回答一次、只交一个 JSON」的场景用（现在只有数据出图的配方）。看护 `tests/test_ai_structured.py`、`tests/test_ai_structured_cli.py`、`tests/test_ai_data_model.py`、`tests/test_data_figure_bench.py`。

- **不另写 HTTP**：`call_structured` 驱动内置执行器的 `loop.HttpModel.complete(tools=[], max_attempts=1)`（经 `fixsession.build_model(conn, **model_kwargs)`）。出站约束仍只在 `wires.post_json` / `_opener()`（`test_outbound_https_trust.py` 钉的那一处）；本模块不碰 opener、不 import 网络库。
- **永不抛**：任何异常落成 `transport/internal`；失败只有 `REASONS` 闭集（`no_connection` / `unsupported_connection` / `over_budget` / `timeout` / `refused` / `invalid_output` / `auth` / `quota` / `transport`），`detail` 只是短代码，**不含**响应原文、提示词、地址、密钥。`map_failure` 对 `loop.REASONS` 每个值都有结局（加新 reason 时 `test_failure_mapping_is_total_over_the_runner_reasons` 会红）。两种超时都要映射到 `timeout`：总时限是 `budget/time`，套接字读超时是 `network` + 异常类型名。
- **重试只有一种**：回复不是合法 JSON（含没有文字）→ 追加固定的一句话重发，至多 1 次；形状不过校验、传输失败、拒答、鉴权失败、被截断都**不重发**。总时限两次共用，从解析连接起算；挑连接不联网，只在剩余预算 ≥ `REFRESH_NEEDS_S` 时才对挑中的那一条刷新令牌 / 核地址（没法传时限，不放线程里超时，免得留半截写入）。**请求数在传输层数**（`StructuredResult.requests`；换协议 / 字段降级不消耗尝试，一次 `complete()` 可发多个请求），`max_requests` 硬拦、到顶 `over_budget/requests`；手动脚本的 `--max-calls` 按它算。`stop == max_tokens` 是 `over_budget`。
- **连接**：先用内置执行器能直接用的连接（`ai_providers.builtin_conn` 形状：API Key / 自填地址 / 账号登录令牌）；没有时看订阅登录：适配器覆写了 `AgentDefinition.build_structured_command`（Codex、Claude Code）→ CLI 连接描述 `{"kind": "cli", "agent", "model", "conn_id"}`，走 `_call_cli`；没覆写 / 解析不出型号 → `unsupported_connection`；什么都没有（含订阅需要重新登录）→ `no_connection`。**订阅登录路径**：每次尝试起一个新 CLI 进程（`ai_bridge._popen` / `_terminate` / `_await_exit`，无窗口、自成进程组、超时杀整棵树；stderr 丢弃），工作目录是新建的**空**临时目录、系统提示 / schema / 输出文件在另一个临时目录，用完删除；argv 里没有用户内容 / 令牌 / `--dangerously-*` / 可写沙盒（用户内容只走 stdin），不落会话历史，**不读用户自己的 CLI 配置**，型号显式（连接里的；没有就取适配器清单第一项；都没有 = 不支持，不写死型号名），思考强度只在 `_effort_confirmed`（目录明确列了该型号档位）时才带。Windows 沿用 `_codex_windows_sandbox_args`。命令与 Claude 不用 `--bare` 的原因见 ADR 0178「订阅登录连接」。失败码只认 CLI 自己的结构化字段（`codex_failure_class` / `claude_failure_class` 复用）：`agent_auth` → `auth`、`agent_quota` → `quota`，其余非零退出 `transport`，起不来 `no_connection`，超时 `timeout`，空 / 非 JSON / 不合格 `invalid_output`（非 JSON 重试一次，重试时只追加固定那句话、不带上一次回复）。CLI 拿不到用量：`usage_reported=False`，`requests` = 起的进程数。总时限 `CLI_TIMEOUT_S`（60 s，**待本机真跑校准**）；同一时刻最多一个结构化 CLI 进程（`cli_slot`，可重入；`ai_data_roles.judge` 在计时前占住，排队不吃预算）。**真跑要订阅额度，不进 CI / 夜跑**：`tests/conftest.py::_offline_structured_cli` 默认把 CLI 发现函数置空、把真起进程的 `_run_process` 换成「记一笔、不起进程」并在用例结束时断言没被碰过（`call_structured` 永不抛，所以不能靠抛异常），CLI 用例只注入假 `runner`；`scripts/dev/structured_cli_smoke.py` 只在维护者本机手工跑。`ai_bridge.capabilities_if_cached()` 供同步路径（`propose`）只看已有缓存、不触发探测。
- **不记录**：不写日志、不发遥测、不进诊断包；`StructuredResult` 里没有提示词与回复原文。新增遥测事件要等下次 `CONSENT_VERSION` 升版，且仍不收列名、路径、指令、图里文字。
- **数据出图调用点 `ai_data_model.propose_recipes`**：受控摘要只含 路径 / 工作表 / 行数 / 布局 / 分组 / 编号线索 / 每列名字、类型、计数、≤ 20 条文字样本（不含行数据、哈希、大小）；表里的文字当数据：JSON 字符串放进 `<data>`、`<` 转义，提示词明说不是指令。模型交的每份配方先过 `FigureRecipe.from_dict` 再 `enforce_order`（`validate_recipe` 的闭集校验），丢弃只记原序号 + 拒绝码。`confirmed` 默认什么都没确认：模型不能凭列名当个体编号 / 配对。
- **配方调用点仍没有调用方**：`test_nothing_in_the_product_calls_propose_recipes_yet`（AST）要求产品里没有任何对 `propose_recipes` 的引用（第 2 步才接）。`ai_structured` / `ai_data_model` 的 import 关系由 `tests/test_ai_data_roles.py::test_only_propose_and_the_model_pair_reach_the_model_modules` 钉住。
- **固定用例集**：`tests/fixtures/data_figure_cases/cases.json`（格式与判定器见 `tests/support/data_figure_cases.py` 文档串）；每条自带 `reference`（好回答）与 `trap`（踩坑），用例自证。手动入口 `scripts/dev/data_figure_bench.py`：默认假回答；`--real` 才连真模型，必须 `--max-calls` ≥ 用例数 × 2，**真实跑分花额度，开跑前先报预算**。

### 读懂数据（`engine/ai_data_roles.py`，ADR 0178 修订 2026-11-19，第 1 步）

模型只给**列判断**（`ROLES` 闭集：`time` / `ordered_x` / `measure` / `group` / `replicate_unit` / `row_id` / `error`（sd / sem 这类误差列：不当纵轴，2026-11-21 加）/ `unit_label` / `note` / `other`），翻译成 `propose` 本来就收的 `answers.y / x / group`、`header_rows`、`answers.cells`；配方仍由 `default_candidates` 生成、过 `validate_recipe` / `enforce_order`。看护 `tests/test_ai_data_roles.py`、`tests/test_data_figure_bench.py`。

- **逐字段降级**：形状层严格（未知键整份拒收），取值层一个字段一个字段判（列不存在 / 角色不在枚举 / 与列类型对不上 / 选择与自己给的角色矛盾 / 规则键对不上），丢了只记个数、回规则默认。**`replicate_unit` 永不变成用户事实**（建议里没有 `design` / `unit`）。采纳策略在 `suggest`：规则会问的地方用模型的选择；规则唯一确定的角色只在模型明确否决那一列时才换。
- **四处「够得到」的采纳（2026-11-21）**：没有 `picks` 时规则逐字不变；每条都是「模型明确判断 + 列客观满足」，谓词只在 `ai_data_recipe`（`suggest` 采纳、`_resolve_roles` 落地共用），不满足 = 丢该项回规则结果、不报错。① 多列纵轴：`picks.y` 数组 2–3 列（`MULTI_Y_MAX`），`multi_y_ok`＝互不相同 / 数值列 / 非编号列 / 单位至多一种，且模型对每列的角色只能是 `measure`，**有有序横轴时**每列一条线（趋势类，至多 3 列）；**没有横轴时**改为「把列当分组」（宽表转长表：`FigureRecipe.stack`，客观条件 `stack_ok`＝2–`STACK_MAX_COLUMNS`(8) 个纯数值列 / 同单位 / 非编号 / 每列 ≥ 2 个数值格；转换只有运行时函数 `adf.apply_stack` 一份，脚本与 `with_cells_applied` 同源，类别顺序 = 列顺序、标签 = 列名；`roles.y` = 这几列、只在这时多 `roles.stack: true`，前端发 `answers = {y: [列…], x: "none"}`；有文字分组列 / 点名分组 / 合画 / 大表回规则结果；HO-17 / HO-25..30，ADR 0178）；`answers.y` 因此可以是 1–8 个列名的数组（`parse_answers` 只对 `y` 接受数组，规则不认的数组当没给），只在规则要问「选哪个值」、选中的列被否决、或模型明确要把几列当分组时采纳。② 有分组时的横轴：模型判 `time` / `ordered_x`、该列是规则此刻的横轴候选（日期 / 数值、不是 y / 编号列）。③ 编号样文字列当分组：模型判 `group` 且 `id_group_ok`（在 `s.groups` 里，即文字、2–12 个取值、有重复）；`answers.group` 点名才进 `cats`，`roles.options.group` 带上它，且它不再兼作重复测量的个体编号（全部编号列都被当分组时不再问 `pick_design`）。④ 整数编码的数值列当分组（HO-20，`knockout` 0 / 1）：`FigureRecipe.numcat` 这条事实贯穿配方→`validate_recipe`（`numcat_view`）→脚本（`apply_numcat`、类别按数值升序）→落盘再校验→指纹核对；客观条件 `DataSummary.numcat`（`adf.numcat_levels`，≤ `NUMCAT_MAX_LEVELS`=6），用户选或模型判 `group`（`numcat_pick_ok`）才进，规则从不自己用；`roles.options.group` 会列出它；大表 / 合画暂不支持、回规则结果（ADR 0178）。
- **横轴是测量值就不连线（2026-11-22，准则 G-018 / G-052，ADR 0178 修订 2026-11-22）**：模型把规则（含它自己的选择）此刻选作有序横轴的那一列判成 `measure` → `Understanding.x_measure`（`ai_data_roles._x_measure`，只由已校验的列角色派生；`fixed.x` 用户已定则不派生）→ `answers.x_measure = 列名`（**不是** x / y / group 下拉的改动，不计「用户动过下拉」；`ai_data_propose._ANSWER_KEYS` / `confirmed_from` / `_user_took_over` 都认它）→ `default_candidates` 在 `roles.x_ord` 恰是该列（且是数值、关系图资格过）时不出趋势类、`scatter_fit` 与 `scatter` 排前，去掉的记 `dropped`（`roles_invalid`，`detail=x_is_measure`，不加新码）。**规则臂没有这条**：单凭数据事实分不出有序横轴与独立测量值（ADR 有逐条反例），没有模型时响应逐字不变；不要给规则加「不单调就当测量值」之类的启发式。绑列名是为了用户换横轴时自动失效。前端要把 `answers.x_measure` 并回 `propose` 才生效（现有 `suggestionFrom` 只认 x / y / group）。同一 x 有重复观测不逐点连线（G-052）一直由 `line_duplicate_x_unconfirmed` 管。看护 `tests/test_ai_data_x_measure.py`、`tests/test_data_figure_recommend_cases.py`（理想列角色回放）。
- **`roles.y` = 每个备选实际画的列（2026-11-21）**：`default_candidates` 里最终 `ys` 多于一列时（规则宽表 / 模型点名 / 用户点名一视同仁）只留画全这些列的备选（现在只有趋势类），其余记进 `dropped`（`roles_invalid`，`detail=multi_y_not_drawn`，不加新码）；全被去掉（如量纲不同退到第一列）就用 `picks.y = ys[0]` 重算，不出零备选。看护 `tests/test_ai_data_roles.py` 的 `*_draws_*` 两条。「把列当分组」（`stack`）不在此列：它的备选画的是转换后的值列，`roles.y` 报的是被当分组的那几列，由 `roles.stack` 标明。
- **显式不起任务（2026-11-26）**：请求带 `understand: false` 就不起（`ai_data_propose.propose(understand=False)`，响应没有 `understanding` 键、候选逐字不变；非布尔 `propose_input_invalid`；进幂等键）。给**不是用户在看数据**的重出用——前端「点选误差线换 SD / SEM / 95% CI」（`figureOriginStore`）一律带上，免得点一下误差线悄悄多发一次出站。看护 `tests/test_ai_data_no_understanding.py`。
- **数据摘要（2026-11-26）**：lazy 响应（单份）多 `data_sha256` = 这次读的数据文件原始字节的 sha256；`GET /api/ai/data-figure/data-digest?table=`（开关后面、只读）回同一口径的当前摘要，文件不在 / 出界 / 太大 / 读不了回 `{sha256: null}`。给「图的来历核对数据变没变」用（`PanelObject.dataOrigin.dataSha256`），看护 `tests/test_ai_data_digest.py`。
- **用户永远优先**：请求带 `answers.y / x / group / cells / x_measure` 或 `header_rows` 就不起任务（`ai_data_propose._user_took_over`，必须在懒模式循环往 `ans` 补默认答案**之前**记下）。
- **响应不变量**：没有可用连接（没连、订阅登录里适配器不支持 / 缓存里还没有、运维关内置）或非 lazy 时，响应里**没有** `understanding` 键；有连接时只多这一个键。后台线程不碰按项目绑定的状态（只读数据文件、联网、写缓存目录），所以没用 `bound_project`——将来加了任何按项目绑定的状态要补。
- **缓存 / 单飞 / 负缓存**：缓存只存模型的**原始判断**（`Judgement`），建议每次按当前完整提示 + 规则现算（`derive`）——别把派生结果缓存起来；`data_dir()/cache/ai_data_roles/`（原子写、TTL 30 天、≤ 200 条）；key 含协议版本 + 系统提示哈希 + 数据字节哈希 + 表头行 + 模型看到的提示 + 连接身份（不含密钥 / 地址）；单飞只在 pending 期间、键含完整提示摘要；失败只做 60 s 内存负缓存；同 `(项目, key)` 在途单飞。缓存里有列名：**不进诊断包、不进遥测**（测试钉住）。
- **外发清单**见 ADR 0178 本节：只发受控摘要 + 本地聚合（不同值个数 / 单调 / 全整数 / min / max）+ 仅 `unsure` 表的 ≤ 8 行 × 120 字预览；不发行数据、哈希、绝对路径。隐私说明文档开关打开前要补。
- **时限**：总时限 25 s（含解析连接，挑连接不联网，预算 ≥ 35 s 才对挑中的那一条刷新）；订阅登录（CLI）连接取 `ai_structured.CLI_TIMEOUT_S`（60 s，待校准），比前端轮询上限（30 次 × 1 秒，若仍是这个值）长，前端要配合；缓存键的连接身份对 CLI 是 `cli:<agent>|<型号>`；`call_structured(effort=)` 只在 `builtin_effort_args` 确认支持时才发，`max_requests` 按传输层实际请求数硬拦。
- **手动基准**：`scripts/dev/data_figure_bench.py --suite roles`（默认假回答；`--arm rules` 只跑规则（与 `--real` / `--key-file` 同用直接 exit 2，在解析任何凭据之前）；`--real` 要 `--max-calls` ≥ 用例数 × 2 且硬拦实际请求数）。**真实跑分花额度，开跑前先报预算。** `--real --key-file PATH --model NAME [--base-url] [--effort low|medium|high]` 用一把单独存的 key 组**临时**连接（不读不写应用配置与 provider 存储，数据 / 配置目录指到临时目录；key 文件必须仅本人可读写，比 600 宽就拒绝；只能和 `--real` 同用、仍强制 `--max-calls`）；key 不出现在 stdout / stderr / 报告里（输出流与报告统一替换，`tests/test_data_figure_bench.py` 钉住），报告写型号 / 强度 / 实际请求数 / 输入输出（含推理）token / 按定价表的估算花费。

- **小型号 + 最低档（2026-11-21）**：`ai_catalog.json` 顶层 `structured_small` 按「官方端点 + 协议」登记结构化调用用的 `(型号, effort)`（现只有 OpenAI 官方 + Responses → `gpt-6-luna` + `none`，出处是 2026-11-21 实测 + API 回的档位）；`call_structured(auto_small=True)` 才生效（默认关，只有 `ai_data_roles` 开，`ai_data_model` 不开），不登记的连接请求逐字不变，改图 / 修复路径不受影响。小型号失败为 `http_400/404/422`（transport）或 `http_403`（auth）→ 同一总时限 / `max_requests` 内改用用户原型号与原强度再试一次并记 10 分钟；其余失败码不换。`wires.RESPONSES_WIRE_EFFORTS` 比用户可选的 `RESPONSES_EFFORTS` 多 `none`；目录 `hidden_efforts` 放界面不露出的 none（`ai_catalog.verified_efforts`）。缓存键带实际型号与强度。看护 `tests/test_ai_structured_small.py`、`tests/test_ai_data_roles.py`（小型号三条）、`tests/test_data_figure_bench.py`（`--auto-small` / `--effort none`）。**只作用于 Key 连接：订阅登录（CLI）连接走 `_call_cli`，不受影响**（`small_plan` 对 CLI 连接返回 None）。全文见 ADR 0178 修订 2026-11-21。

## 修改方案（`engine/changeplan.py` + `/api/change-plans*`，ADR 0206 §一，只算不写）

- 模型通道就是上面的 `call_structured`（不新增执行器工具）；`changeplan.py` 是它第二个调用方（`tests/test_ai_data_roles.py::test_only_propose_and_the_model_pair_reach_the_model_modules` 列着调用方名单）。纯标准库，Flask 父进程 import。
- **谁的话算数**：审阅意见是外来文本，只进用户内容的 `<comments>` 数据块（`<` 转义），称呼不发；模型的 `gid / prop / value` 只是提议，`validate_proposal` 逐条对 manifest 的 `editable` 核；卡上的改前 / 改后 / 框一律从 B0 与候选两份**真实渲染的 manifest** 现算（`effective_changes`），值没变的进 `unhandled(no_effect)`。每条输入意见必须落在某个组或 `unhandled`。
- **渲染**：B0 / 候选的图与 manifest 走状态中立的 `preview_png_snapshot`；它不回 warnings，所以另走 `override(B0)`（基线 warning）与 `override(候选)`：候选比基线多出来的 warning = 不出方案。**只要对热 worker 做过 `override`，任务结束（成败都是）就 `_retire_hot_worker`、不还原 B0**（B0 是请求时的列表，期间用户可能已改过图），回包与 plan 带 `replay_required: true`，前端按自己的全量列表重放。全程 `bound_project(ctx)`、`safe_only=True`、每次渲染前 `_require_route_unchanged`，模型调用前后各算一次脚本修订（变了 = `change_plan_figure_changed`）。native 图 409 `change_plan_native_unsupported`，不渲染。
- **落盘**：只写 `data_dir()/cache/change_plans/<项目id>/<方案id>/`，按项目隔离、TTL + 条数上限、`sweep()`；删除不用 `ignore_errors`；方案 id 过 `^[0-9a-f]{24}$` 才拼路径，图名只收 `before | after`。
- **长任务**：POST 回 202 + 方案 id、后台线程、GET 轮询；同一张图同一时刻一个任务（进程内表，**不持 `script_guard`**——方案任务不写脚本；该脚本有改图助手会话在跑 = `ai_bridge.script_busy` → `change_plan_busy`），同一 `client_request_id` 回同一任务。稳定码闭集在 `changeplan.ERROR_CODES`（登记 `tests/test_error_codes.py` 与两份 `errors.json`）。
- **助手来源（ADR 0206 PR 4）**：`source: "assistant"` + `instruction` / `target`，`changeplan.assistant_comments` 把指令包成唯一一条意见（id `a1`）；系统提示 `system_prompt("assistant")` 与审阅那份分开（指令是用户意图，元素表文字是内容）；单组改动上限 60、超出记 `over_limit`；同一条指令可同时在 `groups` 与 `unhandled(needs_script)`。开关 `changeplan.enabled()`（`ai.change_plan.enabled`，**默认开**，只认显式 `False` 才关）并入 `/api/ai/capabilities` 的 `change_plan_enabled`；设置页的一行开关走 `PUT /api/ai/change-plan`。
- **对数轴下限 / 整条交给脚本（ADR 0206 修订 2026-11-26）**：`changeplan.require_log_lower_limits` 在候选之前把「改成对数轴而下限 ≤ 0」的组补上正数下限（`suggest_log_lower`：上限 / 100 取 10 的幂）并标 `confirm`，候选带着它渲染；`effective_changes` 只在下限那条真生效时保留 `confirm`；`validate_proposal` 把既有图内修改又 `needs_script` 的意见只留 `needs_script`。改后新增 / 加重出图幅的元素由 `changeplan.make_room`（复用 `normalize.adapt_margins`）腾边距，补出的 `position` 经 `attach_room` 以 `auto: "room"` 并进造成出界的组（取消该组边距一起不应用）。看护 `tests/test_changeplan.py`、`test_changeplan_endpoints.py`、`test_changeplan_real.py`。
- **整版预览**：`POST /api/change-plans/layout-preview`（`{canvas: {page_w_mm, page_h_mm, objects}}` → PNG）只给一键排版卡出图，经 `_export_png_bytes`（与外部审阅出排版图共用）；不写导出目录、不登记、不提交。
- **矢量编辑方案（ADR 0212 第 6a 期，草案实现）**：`POST /api/vector-edit/plan` + `GET /api/vector-edit/plan/<id>`，只算不写、**不读盘**（后端读不到桌面内存里的文档，输入是前端给的画布 `{canvas}`；写回是前端自己的一次提交）。两种请求：`{canvas, ops}` 同步回方案（`vectorplan.plan` → `vectoredit.propose_memory`，即读盘那条 `propose` 的同一个 `_plan`，子图量不了只给外框）；`{canvas, instruction, selection?, connection?…}` 回 202，后台线程里 `changeplan.ask_vector_edits`（与 `ask_model` 共用 `_structured`：同一条连接解析、同一组稳定码，不新增执行器工具、不新增调用 `ai_structured` 的模块）问一次无工具结构化调用，输出 `{ops, unhandled}`，**每条操作连同前面收下的先过 `vectoredit.apply_ops` 干跑**，不合格的整条进 `unhandled`（`{reason, code?, op?}`）不拖垮整次；结果只放进程内存（`vectorplan.keep_result`，TTL + 条数上限）。补丁词汇说明与错误用语只有一份（`vectoredit.OPS_DOC` / `ERROR_WORDS`，插件 `tavotto_layout_edit_*` 与助手系统提示共用；加操作改这里与 `apply_ops`）。**组名（ADR 0208 前置 6.0）**：`group {ids, name?}` 把名字存进每个成员的 `groupNames`（去首尾空白）、`rename_group {group, name}` 同组全员一次写（空串 = 去掉，写到组里的子图上也算）；场景 `vector_objects[]` 在 `group` 旁带 `group_names`（`{组 id: 名字}`，读法 `agentobjects.group_name_of`，没有名字的组不列）；`groupNames` 是字段表保留字段，Agent 新增对象时不能直接写（`reserved_field`），Agent 在 `groupPath` 里写的组名链由 `agentobjects._mint` 留成 `groupNames`。坏补丁 = 200 + `ok:false` + `errors:[{code, index?, field?}]`（码见 `vectoredit.ERROR_CODES`），请求形状不对 = 400 `invalid_change_plan`。会话认证走全局 guard。看护 `tests/test_vector_edit_plan.py`。

- **矢量核对量画出来的面，「改前就有」的豁免带上位置并按补丁后状态重判（错题本 L-181 升级，2026-11-28）。** 出页、遮挡、
  包含都按真实几何量：线宽、箭头头、旋转、Bézier 手柄、多行文字高度都算进去，外框相同不等于包住（空心椭圆的边会穿过子图）；
  豁免要比对象、检查项与位置，补丁落地后重算余量。为什么：求解只量中心线、多行按一行、旋转不换算锚点，「改前就有」只比检查项，
  另一侧出页也被放过（#2201、#2220、#2254，六条 P1）；2026-11-28 「框住子图的外框」只比外框就豁免子图文字遮挡，评审报 P1（#2336）。
  看护 `tests/test_vectorcheck.py`、`tests/test_vector_edit_style_check.py`。

## 数据准备（`data_prepare`，ADR 0215 第一期·后端内核，2026-11-27，无调用点）

- **文件**：`engine/ai_data_prepare.py`（候选、`PrepareAdapter` / `PrepareExecutor`、契约核对 `Checker`、`candidate_table`、`adopt`）、`engine/agent_runner/prepare_tools.py`（工具集，导入即登记进 `TOOLSETS`）。
  `trial.run_python` 多两个参数：`read_allow`（进程内读白名单）与 `tamper`（闭集 `full` 默认 = 实验现状 / `stdlib` 数据准备用：只收窄 `namedtuple` 的 `__defaults__` 赋值与试跑目录内的文件夹打开，gc 与 `__code__` 照拦；没有全关，见模块说明「防篡改的收窄」）；`trial.scratch_dir` 是公开的专用试跑目录。
- **工具**：`list_dir` / `read_file` / `sample_file`（`source/<标签>` 只读窗口，单次 ≤32 KiB、累计 ≤384 KiB）、`create_file` / `append_file` / `edit_file`（只写 `prepare.py`，复用 `ConvertSession`）、
  `run_python`（≤6 次，`sample_bytes` 只给输入前缀）、`check` / `finish`（共享 ≤3 次）。`run_python` / `check` 是父进程回调（`RunEnvelope.hooks`，`PrepareHooks`）；**没有 hooks 的运行启动即 `internal/no_hooks`、零模型请求**。第一期不动 IPC，接桥那一期把两个回调映射到泛化的 IPC。
- **写入面**：任务契约 `allowed_writes=[]`、`writes_user_files=False`，`run_task` 的放行条件不动；失败 / 取消 / 超限候选与试跑目录都删；`adopt` 之前项目文件夹逐字节不变。
- **发给模型的内容**：见 `prepare_tools` 模块说明的逐项清单（改它要同步 ADR 0215 与隐私文字）。
- **盲区**：读白名单与断网的进程内钩子靠防篡改三条守着（`stdlib` 收窄版），C 层 / ctypes（导入不拦）绕得过、Windows 只有这一层且未验证；取消只在两次运行之间生效；输入的 sha256 只在第一次 `check` 算一次，之后比文件身份。
