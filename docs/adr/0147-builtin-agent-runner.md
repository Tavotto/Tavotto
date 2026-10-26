# ADR 0147：改图助手的内置执行器——自己跑「模型 ↔ 工具」循环

日期：2026-10-23 · 状态：**第一阶段（核心循环）已落地；第二阶段（注册进改图执行路径）已接入改图**（2026-10-24，「连接 AI」改造；1.0 豁免已由维护者给出）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)（§三 默认只改表现形式、§四 执行面不扩大、§六 Harness² 只借思想）、
[0015 Agent 注册表](0015-coding-agent-registry-and-settings.md)、[0143 生成可编辑版本](0143-convert-to-editable.md)。

## 问题

改图助手今天只能借用户本机的 Codex / Claude Code CLI：用户没装、没登录、或只有一个第三方模型服务的密钥时用不了；
CLI 的写入面（任意 shell、网络、绝对路径写）也比「改一个脚本的外观」大得多（04-security-model §三 的 R-03 只能事后发现）。

## 决定

### 一、一个独立子进程，只有四样工具

`python -m tavotto.engine.agent_runner` 在候选目录里跑一个循环：系统提示 + 用户指令 → 模型 → 工具调用 → 结果回填 → 直到模型给出最终文字。
`figure_edit` 的工具集只有 `read_file` / `list_dir` / `edit_file` / `write_file` / `check`；**没有 shell、网络、MCP**。路径一律 `realpath` 判在
候选目录内，写只许目标脚本；`edit_file` 的原文必须恰好出现一次（照 Claude Code 的 Edit 语义）。执行面比 CLI 小，是这条路的主要理由。
`data_to_figure` 将来的工具集没有写文件工具（0138 §四）。

### 二、三种线上协议 + 一个确定性 Demo

`anthropic` / `openai-chat` / `openai-responses`，纯标准库 urllib，出站 HTTPS 只经 `tlstrust`，不跟随重定向，响应体有上限。
`demo` 协议按剧本回放，不联网，端到端测试用（借 Harness²，0138 §六）。密钥只经环境变量，提示词只经文件 / stdin，事件不带内容。

### 三、失败不抛，结局写进 `done`

`reason` ∈ `completed | max_turns | budget | tool_error_loop | auth | quota | refused | network | cancelled`（外加兜底 `internal`）。`refused` = 服务端过滤 / 拒答。
预算恒等式（每轮入账之和 == 合计）不成立时记 `ledger_mismatch`，不抛。

### 四、第二套工具集：`convert_to_editable`

同一个循环、第二套任务工具集（`--task convert_to_editable --target <原脚本> --output <原名>_matplotlib.py`）。权限边界逐条对应
[0143 §二](0143-convert-to-editable.md)：原脚本永远不可写；Agent 只能在候选目录顶层**新建**那一个文件（`create_file` / `edit_file` / 整份覆盖这个新文件，
别的路径一律工具错误）；`--output` 起跑即校验、不合格 `done internal bad_output`（0 次请求）；`check` 是注入回调（语义 = 0143 的 `run_validation`），
最多 3 次（0143 的「校验运行 ≤ 3」），本阶段入口没有注入方。系统提示的产品要求引用 `ai_prompt_pack.convert_requirements()` 的同一份文字，不复制。
没有 `write_file`、没有 shell / 网络。

### 五、第二阶段：已接入改图（2026-10-24）

`ai_agents.BuiltinAgent`（id `builtin`）以**内部**执行器的身份接进 `ai_bridge.run`：不在 `AGENT_REGISTRY`、不出现在 capabilities 的 `agents[]`，
界面上也没有「执行器」——用户只看到「连接」，`builtin` 只经连接的 `route` 体现。命令是 `tavotto agent-run`（冻结产物同目录 `tavotto-cli`，源码 `-m tavotto`，与 D3 的 `fix-mcp` 同一启动器出处），
复用候选工作区 / TAF 校验与修正 / 事件流 / 取消 / 历史库，与 Codex / Claude Code 同一套会话生命周期；密钥只经环境变量，提示词经 stdin。

**路由规则**（用户决策 2026-10-26，视图见 `engine/ai_connections.py`）：

1. 订阅类连接（ChatGPT / Claude 的官方登录）→ 对应 CLI；
2. Key 类连接：该连接家族的 CLI 装着且可用，并且显式设置 `ai.builtin_default=false` → CLI + 注入 provider；否则 → 内置执行器。没装任何 CLI 的用户因此直接可用；
3. `ai.builtin_default` 缺省为 true（2026-10-26 用户授权切换）。保留显式 false 的 CLI 偏好和 `TAVOTTO_AI_BUILTIN=0|1` 强制开关，不迁移用户已存偏好。
   验收分别记录真实服务协议、完整应用转换与修复、确认、取消及卡住后的终止；一个协议或 FigureBench 跑分不代表所有任务通过。
4. 账号登录（OAuth 访问令牌，如 MiniMax）类也遵循 `builtin_default` 与强制开关；显式 false 且 CLI 装了、启用时走 CLI（凭据由我们注入，不看 CLI 自己的官方登录）；没有可用 CLI 或偏好内置时走内置执行器，
   anthropic wire 只发 `Authorization: Bearer`（`--auth bearer`，不发 `x-api-key`），令牌在 spawn 之前由 `ai_oauth.ensure_fresh` 刷新、只进子进程环境变量。
   内置循环不能用的记录（如缺型号）退回可用的 CLI。
5. 明文 http 只放本机回环与私网（2026-10-25 起放行局域网：实验室常有自建模型服务器）：`127.0.0.1` / `::1` / `localhost`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、
   `100.64.0.0/10`（CGNAT / Tailscale）、`fc00::/7`；链路本地（`169.254/16` 含云元数据、`fe80::/10`）、`0.0.0.0/8`、组播 / 保留段、公网 http 一律拒绝。主机名在判定时解析，
   **所有**解析结果都在放行段才放行（混了公网 / 解析失败都拒）。判据只在 `engine/netpolicy.py`，`agent_runner.wires.check_base_url`（内置执行器）与 `ai_catalog.url_refusal`
   （保存连接 / 取型号清单）都问它；CLI 路由同一判据（它们自己能否连 http 由 CLI 决定，我们不额外拦）。连接时**钉住判定那次解析出的 IP 直连**（`Host` 头保留原主机名），
   DNS 重绑定到不了别处。云厂商元数据地址（`169.254.169.254` / `169.254.170.2` / `fd00:ec2::254` / `100.100.100.200` / `168.63.129.16` / `192.0.0.192` 等，及 `metadata.google.internal` / `metadata.tencentyun.com` 等主机名）**先于放行段排除**（有的恰落在 `fc00::/7`、`100.64/10` 里）。connections 上的可选 `insecure_lan: true` 标出放行的非回环 http 网关，前端据此提示「这个地址不加密」。

AI 修复（ADR 0149）的内置循环同样改为取连接视图里第一个可用的 Key 连接。

这里的 CLI 回退发生在派发前；任务中途失败不会自动换执行器重放。`openai-chat` 是 runner 层协议，当前应用连接只开放 `anthropic` 与 `openai-responses`；验收必须分别标注 runner 测试、应用连接测试和真实服务测试。当前证据及定向试用记录见 [协议验收报告](../implementation/agent-external-corpus-2026-10-25/PROTOCOL-ROLLOUT.md)。

### 六、仍然不做

流式回复。

### 七、转换任务与思考强度（2026-10-25 用户裁决）

* **转换任务走内置执行器**：`--task convert_to_editable --target <候选里的原脚本副本> --output <原名>_matplotlib.py --check-ipc`。`check` 工具经 stdout/stdin
  回调父进程——runner 发 `check_request`，`ai_bridge` 在候选工作区里跑 ADR 0143 的同一套校验（`ai_convert.agent_check`：新文件在不在 → 语法 → 静态用了 Matplotlib →
  `run_validation` 真跑，写入根只有候选目录），把 `{ok, report}` 写回 runner 的 stdin；提示词因此是 stdin 的第一行 JSON。其余（候选 / 提交前检查 / 修正 / 留存待采用 / adopt）
  与 CLI 路径是同一条流水，ADR 0143 一条不放松：原脚本永远不可写、只新建一个文件、R / MATLAB 来源同样可走。
* **改图任务也有 `check`（2026-10-25，FigureBench 真跑发现：此前改图路径没装回调，`check` 恒 `not_available`，模型改完看不到结果，只能靠提交后的 TAF 验证兜底）**：
  有 TAF 候选工作区的内置改图同样带 `--check-ipc`（同一条 stdout `check_request` / stdin 回复），回调是 `ai_figure_edit.agent_check`——**提交前同一套检查前移到 Agent 手里**，不另写判据：
  `FigureEditAdapter._precommit`（改过没有 / 语法 / 静态输出名 / 候选里别的文件 / 原项目没被动）→ 都过了在用户同一路由下真渲染候选（`ai_convert.run_validation`：候选字节写成紧挨原脚本的临时文件、写入根绊线只开候选目录、
  跑完即删，不运行原脚本本身、不写用户文件）→ 数据指纹（`figureverify.data_unchanged`，提交后 L2 用的同一个函数）对基线（池里已 build 会话落盘的 manifest，只读；没有基线就如实说“没量”，不阻止通过）。
  次数上限与转换同一处（`tools.MAX_CHECKS = 3`，第 4 次 `check_budget` 且回调不再被调）。范围外有没有被改（`changes_outside_target`）需要无 override 的重放基线、会动用户的热会话，所以仍只在提交后 L2 量。
  Codex / Claude Code 两条 CLI 路径没有这个入口（Agent 看不到校验结果），被提交前检查拦下时才由 TAF 有界修正再跑一轮；没有候选目录（TAF 关）的内置改图不装 check（`not_available`，与旧行为一致）。
* **思考强度**：目录声明「来源 + 型号」确认支持——行级 `efforts`（该行所有型号）或顶层 `effort_origins[].models`（型号前缀，先中先得，`efforts: []` = 明确不支持；出处写在条目 `source`）；现有（2026-10-25 对照官方文档核对）：官方 Anthropic 的 5 系列（fable / mythos / opus / sonnet / haiku-5-5）与 `claude-opus-4-7` / `claude-opus-4-8` 给 `low/medium/high/xhigh/max`，
`claude-opus-4-6` / `claude-sonnet-4-6` / `claude-mythos-preview` 给 `low/medium/high/max`（没有 xhigh），默认档位 opus-5-5 与 haiku-5-5 为 `medium`、其余 `high`；Haiku 4.5 及更早、`claude-opus-4-5`（只配合 budget_tokens 的 extended thinking，runner 不支持）不给。
发 effort 时一并带 `thinking:{type:adaptive}`（这些型号都是自适应思考，合法；不发 `disabled`）。官方 OpenAI 的 gpt-5* / gpt-6* / o* 兜底只给 `low/medium/high`（文档里 effort 取值按型号不同且没有完整清单，逐型号核实前不给 none/minimal/xhigh/max），`gpt-5-chat` / `o1-mini` / `o1-preview` 不给。
**这两张文档表只是兜底**：官方来源的连接先查 CLI 自己的型号目录缓存（`CodexAgent._catalog()` 的 `supported_reasoning_levels` / `default_reasoning_level`、`ClaudeAgent._catalog()` 的 `effort_levels` / `default_effort`；现有代码已在只读这两份型号元数据，不涉及凭据、不新读别的文件），型号命中就原样用（含 minimal / xhigh；目录明确给空 = 没有档位），读不到 / 型号不在目录里才用兜底表；最后按线上协议能发的取值过滤（anthropic `low/medium/high/xhigh/max`，Responses `minimal/low/medium/high/xhigh`）。连接视图与发送前核对共用 `ai_providers.EffortResolver`；第三方网关一律不给。`ai_catalog.efforts_for(agent, base_url, model)` 是唯一出处；connections 的每个型号自带 `model_details[].efforts`。
  内置路由的连接才给 `efforts`（默认 `medium`），其余（含所有第三方网关）为空；改图请求带 effort 也只在目录确认支持时才传给 runner
  （anthropic：`output_config.effort` + `thinking:{type:adaptive}`；Responses：`reasoning.effort`，无 max 档）——宁可不发，也不发对方不认的字段。

## 后果

* 用户有了不依赖 CLI 的改图路径；代价是我们自己维护三种协议的字段细节，需要用真实服务做兼容性回归（单独、显式计费的命令，不进 CI）。
* 模型生成 / 影响的文件改动仍不当作可信：候选目录 + 指纹 + 提交前检查（0138 §四）一个不少。
* 规则全文：`docs/rules/backend/ai-agent-bridge.md`「内置执行器」与「连接」两节；看护 `tests/test_agent_runner.py`、`tests/test_ai_connections.py`。

### 八、按厂商合并接入方式（2026-10-25 用户裁决）

界面上不再同时出现「OpenAI」和「Codex / ChatGPT」两个条目：`vendors` 每家一条，订阅登录、账号登录、API Key 作为该厂商的 `methods`，同时支持几种就出二级卡片让用户自己选。
OpenAI = ChatGPT 订阅（codex 登录）+ 官方 API Key；Claude = Claude 订阅 + Anthropic 官方 API Key；Kimi / 百炼各自合并两种 Key；MiniMax（国内）= 账号登录 + API Key。
官方 API Key 经路由可由内置执行器直接跑（有 CLI 且 `builtin_default` 为 false 时仍走 CLI + 注入），且带目录确认的思考强度档位（§七）。

### 修订：可核对的视觉证据与分块写入（2026-10-25）

`figure_edit` / `convert_to_editable` 增加候选范围内的 `read_image`（PNG/JPEG/GIF/WebP ≤ 4 MiB）；转换的 `check` 将同次 live figure 渲染的 PNG 经 IPC 回传（≤ 16 张且合计 ≤ 4 MiB，不完整时明确说明）。三种 wire 发送各自原生图像内容；事件与审计不带图像字节。旧图像随历史压缩移除，图像估算计入预算。该证据只供模型检查，不把运行通过升级成保真证明。

长转换可先 `create_file` 再 `append_file(content, expected_sha256)`；重复/过期追加拒绝，仍只写那个新文件、核完整文件大小和秘密串。不新增 shell、网络或默认路由。

Conversion output recovery remains within the existing budget: create/append should prefer blocks around 6,000 characters; one truncated response can be discarded and retried as a smaller section. The whole incomplete response is withheld from execution and protocol replay; its usage stays in the ledger. A second truncation or exhausted budget stops the task. Other toolsets do not opt into this recovery.

### 修订：参数纠错与显式最终校验（2026-10-26）

真实转换暴露两种循环浪费：模型把工具参数 `new` 写成别的名称，收到笼统错误后继续重试；候选已实际出图，却还需额外请求模型说“完成”，最后一轮因而失败。
参数拒绝仍以工具 handler 为权威，循环只从同一份公开 schema 生成缺失键与可用参数提示，不猜测未声明的键或回显参数值。
文本替换的 schema 与取值共用 `_EDIT_TEXT_ARGUMENTS`：`old` / `old_string` 表示原文，`new` / `new_string` 表示替换文本，短名和完整名称都明确声明；
同组给出不同值或非字符串时拒绝，未知名称不推断；有效 JSON 对象在协议历史中不改写，非对象参数的回放修复见下。figure_edit 与转换复用同一契约，路径、唯一匹配、秘密串与文件大小限制仍由原 handler 检查。
工具 schema 使用平铺属性，不在顶层使用 anyOf / oneOf / allOf（Anthropic 真服务以 HTTP 400 拒绝）；必填替换文本的等价名称组由同一个 handler 验证。
转换的 create / append / edit 回执均给当前文本 SHA-256，使编辑之后可继续有界追加；旧摘要仍拒绝。

转换的既有 `check` 增加可选 `final`（布尔，默认 false）与 `summary`（字符串）。模型明确声明整份候选可交付后，**同次真实校验**返回 true 才直接完成 runner；
报告文字不能替代结果，同批后续调用不再执行，summary 走正常脱敏事件出口。中间校验通过不自动交付，最终校验失败仍可修正；所有校验共用原有 3 次上限。
取消与配置的轮数 / token / 900 s 上限仍生效，失败任务不自动抢救。父进程仍独立执行 Task Core 校验、完整性检查、待采用留存及用户确认采用；
可运行候选与视觉保真是分开的验收维度，不能因一次出图就宣称数据、完整输出或像素全部通过。看护 `test_agent_runner_convert`、`test_ai_convert_app`。

转换同时提供语义明确的 `finish(summary)`，内部调用同一个 `check(final=true)`，模型无需先检查再请求一轮纯文字收尾；两者仍共用 3 次校验预算。
只有转换工具集启用运行期预算提示：轮数、累计 token 或秒数用到 50% / 75% 时，各追加一次剩余资源与收尾指导，下次正常请求即可看到。
提醒自身不增加请求次数、不延长预算、不把中间检查变成成功，也不允许省略真实数据 / 语义 / 输出。累计 token 仍按实际 input + output 记账（包括缓存输入）。

完整接收且解析成功的代码块不再因超过 6,000 字符而丢弃。该值只是减少输出截断的建议，实际文件仍受同一 `READ_LIMIT_BYTES`（200 KiB UTF-8 字节）上限约束；
追加核累计文件大小，Unicode 按字节计算，路径、原子创建、秘密串及完整性门禁均保留。真模型的 6,268 字符块曾因这道额外阈值被拒，造成重写与重复输入计费；
复用实际文件边界可接受该完整块，不能接受服务端标记截断的半截调用，也不能绕过轮数、token 或时间预算。

### 修订：按完整转换流程校准预算（2026-10-26）

同一代码与输入、模型、运行时下，只放宽轮数和 token 的对照完成了 Pillow（22 轮 / 420,040 token / 158 s）与 MATLAB（16 轮 / 676,836 token / 305 s）的转换和采用，随后新应用进程均可出图。
旧 20 轮 / 400k 上限会截断这些成功路径，900 s 时限尚有余量。因此仅把转换默认值调整为 **32 轮 / 800k token**，保留 **900 s**、单响应 32768、3 次工具校验及连续错误终止。
总 token 仍含全部输入、缓存输入与输出，不改账本口径；最坏每次转换的 token 上限翻倍。预算提示、显式收尾、父进程校验及用户采用仍适用，改图/修复预算与默认路由不变。
此对照说明具体复杂任务需要更多有效步骤，不能作为所有模型、脚本或科学/视觉保真均通过的证明；逐次失败和不同预算实验分开记录。


### 修订：共享恢复循环与默认内置路由（2026-10-26）

旧实现收到 `max_output_tokens` 后丢弃整轮模型状态，只允许一次重试；R sinc / plotmath 的新基线再次连续耗尽两轮 32768 输出，约 6 分钟时失败，尚未达到 900 秒期限。
这是供应商单响应输出限制与客户端丢弃续写状态共同作用，不能只归因于总时限。环境修复的 `_FinishAware` 包装器同样转交协议续写与压缩策略，保留签名历史约束。共享 loop 向所有任务提供「观察工具结果 → 纠正参数或候选 → 再验证」规范：

- 截断轮的文字与所有工具调用均不接受；只将协议可回放的 reasoning / reasoning_content / 已签名 thinking 状态保留在模型历史中，并提醒模型接着做一个具体操作。
  无签名的 Anthropic thinking 不回填；思考内容不进事件、诊断包或日志。连续三轮截断终止，有正常响应进展后重新计连续次数。
  成功执行后以实际工具结果接替临时续写状态，释放该状态，避免每轮重复收费；普通工具历史仍保留。Anthropic manual/budget 保留签名前缀，其他 Anthropic 模式在改历史后清除失效签名。
- 非 JSON 对象的工具参数仍由 handler 前置拒绝；下一轮回放只把该调用的参数置为空对象，配对原 call ID 与 `bad_arguments`、同源 schema 提示，避免接口在模型看到反馈之前拒绝畸形历史。
  这不是替模型猜参数或执行原调用；有效对象（包括含错误键的对象）仍原样回放，未知键不推断。
- 缺失或重复调用 ID 时整批不执行，作为协议错误反馈让模型重新发出有效调用，连续三次仍错误则终止。重复失败判据是同一工具、同一参数、同一错误码；不同纠错尝试可继续，但仍受全局预算限制。
- 既有轮数、累计 token、秒数、校验次数、路径权限、取消、用户确认和采用条件均不放宽；不因 401/402、拒答或任意 400 错误自动切换接口或重放副作用。

协议依据：[DeepSeek Responses 的无状态历史](https://api-docs.deepseek.com/guides/responses_api/)、[输出限制含思考](https://api-docs.deepseek.com/api/create-response/)、
[OpenAI reasoning 状态回传](https://developers.openai.com/api/docs/guides/reasoning)。看护 `test_agent_runner`、`test_agent_runner_convert` 与 `test_ai_connections`；真实服务证据独立记录，合成接收端测试不冒充真模型结果。

转换写入守卫反馈同时保留原错误的有界现场与权限指导，不能把所有写入失败只解释成 mkdir。实际文件写入与目录创建的真实 worker 反例验证模型能看到失败位置；没有放宽写入根。
