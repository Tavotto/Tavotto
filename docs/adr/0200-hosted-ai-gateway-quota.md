# ADR 0200：内置 AI 额度——网关转发、按任务放行、按成本记账（功能键 `hosted-ai`）

日期：2026-11-21 · 状态：**Proposed（契约与计量口径已定，维护者拍板见 §十二；网关与引擎各一路实现，先在测试模式走通）**
**1.0 收敛纪律：新增产品能力，属于 [0193](0193-paid-edition-license.md) 已登记的功能键 `hosted-ai`；维护者 2026-11-21 确认豁免（「可以的，豁免」），登记在 `docs/1.0-release-readiness.md` §1.8。入口跟 0193 的上线开关走，另加本 ADR 自己的开关（§十一）。**
相关：[0193 高级版授权](0193-paid-edition-license.md)（令牌、设备签名、§六「服务端每次请求判权益」）、[0187 外部审阅](0187-external-review-share.md)（CloudBase、出站笼子、上线开关）、
[0151 执行追踪](0151-execution-trace.md)（用量记录、`ai_pricing` 估算）、[0148](0148-fix-router.md) / [0197](0197-ai-fix-experiments.md)（AI 修复）、[0138](0138-tavotto-ai-foundation.md) / [0175](0175-ai-attachments.md)（改图助手）、[0178](0178-data-to-figure-v2.md)（数据出图「理解列」）；
隐私声明 `docs/privacy.md` 新增一节（措辞待维护者点头，§九）。

## 问题

高级版的第一项权益是「内置 AI」：买了高级版的用户不用自己去申请模型的 API key，打开就能用改图助手、AI 修复、数据出图。
这件事要同时满足四条：

1. **模型密钥不能离开我们的服务器。** 仓库是 AGPL，客户端里的任何东西都会被拆出来；打包进应用的密钥迟早会被拿去白嫖。
2. **花多少要算得清、管得住。** 模型按 token 收费，各家价格差几十倍；一次改图从几万到几十万 token 不等，失败的那次往往最贵（§三实测）。
   收入是固定的（年度 ¥168），成本是浮动的，必须有上限、有熔断，而且要能不发版就调。
3. **所有用户当小白。** 额度用完、网关不通、令牌过期，都不许把用户卡在报错上；自带 key 的免费路径永远照旧可用。
4. **永久免费承诺不动。** 改图助手、AI 修复、数据出图本身都是免费功能（用户自带 key 或订阅登录）。`hosted-ai` 卖的只是「我们替你付模型的钱」，
   不许在这些功能里加任何权益判断（0193 §一）。

## 决定

### 一、范围

- **算进内置 AI 的**：Tavotto 自己发模型请求的路径（都经 `engine/agent_runner/wires.post_json`）：
  - 改图助手 `figure_edit`（`/api/ai/run`，含「按期刊修正」这一情景）；
  - AI 修复 `fix_environment`（`/api/ai/fix`，`fixsession` 进程内循环）；
  - 数据出图「理解列」（`/api/ai/data-figure/propose` → `ai_data_roles`，单轮结构化调用）；
  - 「转成可编辑」`convert`（`/api/ai/convert`、`/realign`）：**算进**（维护者 2026-11-21），单列单任务上限（自带 key 时单次上限 80 万 token，是其他任务的 2 倍；内置时按实测另定，§六）。
- **不算的**：Codex / Claude Code 订阅登录（`sub:codex` / `sub:claude`）、用户自填的任何连接——这些照旧走用户自己的账，网关不出现。
- 一键排版（0199）没有模型调用，与本 ADR 无关。
- **对用户的名字叫「Tavotto Agent」**（维护者 2026-11-21 定，隐私说明与界面同名；界面上由品牌常量 `PRODUCT_NAME` 拼出，不手写产品名）。
- **Tavotto Agent 是一条连接，不是一个开关**：在「连接 AI」列表里多一条「Tavotto Agent」（只有 `license.has("hosted-ai")` 时出现）。
  执行器路由照旧（ADR 0170 / 「界面不出现执行器」）：用户选了它就用它，选了自己的 key 就用自己的。功能代码里没有任何 `has()` 判断——判断只在这条连接是否出现。

### 二、计量单位：后台记成本，界面显示额度

- **账本单位 = 成本**，记「微元」（百万分之一人民币，整数）。每次请求结算：
  `成本 = 未命中缓存输入 × 输入价 + 命中缓存输入 × 缓存价 + 输出 × 输出价`（价格表在配置里，§七；美元计价的上游按配置里的固定汇率折算）。
  不用 token 记账：模型之间价格差几十倍，换模型、降价都只改价格表。
- **界面只显示比例**：设置里一条细进度条「Tavotto Agent 本期已用 37%」（分母 = 到今天为止已发放的额度），用到 80% 时一行灰字。不显示 token、不显示钱。
  （「大约还能改 N 次」需要按任务类型估，口径不稳，第一版不做。）

### 二之一、额度 = 实付金额 × 40%（维护者 2026-11-21 定）

- **每笔购买的模型预算 = 这笔的实付金额 × 40%**：取 Stripe 这笔订单的实收金额与币种（用了优惠就是优惠后的价），**不扣手续费**；
  **只算模型 token 费**，网关与服务器的固定开销另记，不占这 40%。比例写在配置里（`budget_ratio`，§七）。
- **怎么发**（按笔，叠加购买各管各的时间段）：
  - 付款即发**前 30 天的份额**（有效期 ≤ 30 天的通行证一次发全）；
  - 余下的在有效期剩余天数里**按天平均发放**；
  - **没用完的一直留到这笔购买结束**，不按月清零（研究者的用量是一阵一阵的，投稿前一周最凶）；这笔到期后余额作废；
  - 退款即作废这笔的全部余额（`charge.refunded`，0193）。
- **每天上限**：默认「这笔 30 天份额 ÷ 3」（配置）；付款后 24 小时内（无理由退款窗口）降为平时的 1/3，防「付款—狂用—退款」。
- **按现价的数**（DeepSeek Flash 改图 P90 约 ¥0.011 / 次）：

  | 档位 | 实付 | 40% 总预算 | 折合每 30 天 | 约可改图 / 30 天 |
  |---|---|---|---|---|
  | 年度 | ¥168 / $29 | ¥67.2 / $11.6 | ¥5.5 / $0.95 | 约 500 次 |
  | 投稿季 | ¥68 / $12 | ¥27.2 / $4.8 | ¥9.1 / $1.6 | 约 820 次 |
  | 冲刺月 | ¥35 / $6 | ¥14.0 / $2.4 | ¥14.0 / $2.4 | 约 1,270 次 |
  | 课题组 | 每份同年度 | | 每人同年度 | |

- **允许略超 40%**（维护者 2026-11-21：「可以透支一个任务」）：放行只看余额 ≥ 放行线，放行后的任务跑到底（§五.3），所以一笔购买的实际花费最多是 40% + 一个任务的上限。
- 账本按币种分开记；人民币订单的预算用人民币微元，美元订单用美元微元，上游价格按配置汇率折算后扣。

### 三、实测：每类任务花多少

（2026-11-21，开发机实测，全部是**服务端报的用量**，不是字符估算。主力候选 = DeepSeek 官方 API `deepseek-flash`（DeepSeek-V4.1-Flash-1017），
价格取官方定价页（2026-11-21 核对）：每百万 token 缓存命中 $0.006 / 未命中 $0.30 / 输出 $1.20（高峰），非高峰半价。下面的成本都按**高峰价**算。）

**改图助手（内置执行器 + DeepSeek Flash）**：`scripts/dev/taf_bench.py --real builtin --builtin-wire anthropic --base-url https://api.deepseek.com/anthropic --model deepseek-flash --repeat 3`，
12 个改图场景（`FB-S02/S04/S06/S09/S11`、`C01/C02`、`V01/V06`、`T05/T06/T07`）× 3 次 = 36 次，**36 次全部完成并通过图面核验**，无误改。

| | 中位数 | P90 | 最大 |
|---|---|---|---|
| 输入 token / 次 | 10.2k | 20.3k | 51.0k |
| 输出 token / 次 | 312 | 750 | 7,661 |
| 缓存命中（总体） | 92% | | |
| 耗时 | 10 s | 16 s | 56 s |
| **成本 / 次** | **$0.00075** | **$0.0015** | **$0.010** |

（3 个画图场景 `FB-D*` 不在 taf_bench 的改图流程里，没跑；数据出图另测。）

**AI 修复（内置循环 + DeepSeek Flash）**：`scripts/eval/fix_compat` A 组，`EVAL_MODEL=deepseek/deepseek-flash`，11 题各 1 次：**11 题全部出图**
（4 题当前 main 不叫模型就能出图；7 题由模型修好，8–12 轮）。叫了模型的 7 次里取到执行追踪的 6 次：输入 79k–105k（缓存命中约 96%）、输出 0.6k–1.6k，**成本 $0.0023–0.0062 / 次**。
同一题库 11-19 经 OpenCode Go 跑同一模型只修好 1/9、两次撞 60 万上限（$0.034–0.039）——差别至少部分来自其后合入的修复流程改进（ADR 0197；4 题现在不叫模型就出图），渠道差别没有单独排除，所以失败上限仍要设，见 §六。

**对照：经 Codex CLI 量的改图用量**（gpt-6.2-sol，medium，12 场景各 1 次）：输入中位 161k、P90 205k——比内置执行器多一个数量级，几乎全是 Codex 自己的系统提示与工具定义。
所以**内置 AI 只走内置执行器**，不经 Codex / Claude Code 转发（§一）。

**数据出图「理解列」**：没有实测，按代码上限给**上界**：每次最多 2 个请求，输入 ≤ 4 万字符（约 1–1.5 万 token），输出 ≤ 2000 token，即 DeepSeek Flash 下 ≤ $0.012；结果缓存 30 天。

**其他候选模型同口径折算**（只看价格，没测效果；价格取 `ai_pricing.TABLE`，2026-11-10 核对）：用改图 P90 的用量（输入 20.3k、其中缓存 18.7k、输出 750）算，
Haiku 5.5 约 $0.0007、gpt-5-mini 约 $0.0024、gpt-5 约 $0.012、Sonnet 5.5 约 $0.013。

由此得出的设计约束：

1. **成本不是瓶颈，失败上限才是**：DeepSeek Flash 下改图 P90 不到 1 分钱人民币；按年度档每 30 天 ¥5.5 的模型预算（§二之一），可以覆盖约 500 次改图。
   真正的风险是撞上限的失败任务（历史上单次 60 万 token），所以单任务上限（§六）比总额度更要紧。
2. **缓存命中决定成本**：DeepSeek Flash 未命中缓存的输入单价是命中的 50 倍，成本里六到八成来自未命中部分。网关必须原样透传请求前缀，不得插入、改写系统提示或工具定义。
3. **样本仍少**：改图 12 场景 × 3、修复 11 题 × 1，都是合成题。上线前在测试网关上加跑数据出图与 `convert`，并用 Guardian / 真实反馈包里的脚本补一批。

### 四、架构：客户端 → 网关 → 上游

```
引擎（内置执行器 / fixsession / ai_structured）
  └─ wires.post_json ──(授权令牌 + 设备签名 + task_id)──▶ AI 网关（我们的服务器，持上游密钥）
                                                           ├─ 验令牌、验设备签名、读授权台账（0193 §六）
                                                           ├─ 额度账本：任务放行 → 请求预扣 → 按实际用量结算
                                                           └─ 按配置路由到上游模型 ──▶ 模型服务商
```

- **上游密钥只在网关的环境变量里**，客户端拿不到任何上游密钥。
- **只认内置执行器**：网关不接受普通的 `Authorization: Bearer <key>`。每个请求必须带
  `X-Tavotto-License`（授权令牌）、`X-Tavotto-Task`（§五的任务号）、`X-Tavotto-Ts`、`X-Tavotto-Sig`（设备私钥对 `方法 | 路径 | sha256(请求体) | ts | task_id` 的 Ed25519 签名，同 0193 的设备密钥）。
  所以这条连接不能被塞进 Codex / Claude Code / 别的软件当通用模型代理用。
- **签名在哪做**：`ai_providers.builtin_conn` 对内置连接返回 `auth: "tavotto-device"`；`wires.post_json` 遇到这种 auth 时调用 `license` 模块的签名函数逐请求加头（今天 `post_json` 只发静态头，这是唯一要改的传输层代码）。
  子进程执行器（`tavotto-cli agent-run`）不拿设备私钥：签名经父进程的本地回调完成（与 `check` 工具走 stdin IPC 同一条路），私钥不出主进程。
- **协议**：网关对外只说一种协议（OpenAI Chat Completions 兼容 + `usage` 字段），模型名固定写 `tavotto-auto`；网关按配置选上游（§七）。客户端不知道、也不需要知道用的是哪家模型。
- **可达性**：`ai.tavotto.cn` 一个节点（边缘节点 tavotto-dl，§十），国内外用户都连它；账本在 CloudBase，与 `license.tavotto.cn` 同一份。以后海外加节点也**共用这一份账本**（只有一个写入点）。
- **网关回权威用量**：每个响应带 `X-Tavotto-Charged`（本请求结算的微元）、`X-Tavotto-Remaining`（可用余额 ÷ 已发放额度，0–1，两位小数）。
  客户端的执行追踪把网关回的成本当作权威值记下（`ai_pricing` 对网关地址本来就算不出价格）。

### 五、按任务放行，按请求结算

一次改图是一个**任务**：最多 24 轮 × 2 次 Agent 调用，几十个请求。只按请求扣，会在任务做到一半时余额见底、白花一半钱还没结果。所以：

1. **开任务**：功能开始前 `POST /v1/ai/task {kind}`（`kind ∈ figure_edit | fix | data_roles | convert`，闭集）。
   网关核权益、核余额 ≥ 该类任务的**放行线**（配置，默认 = 该类 P50 成本），回 `{task_id, ceiling}`。
   `ceiling` = 该类任务的**单任务上限**（配置，按 P90 定）。余额不足放行线 → 回 `quota_exhausted`（§八）。
2. **请求**：每个请求带 `task_id`。网关先按「请求体估算输入 + `max_tokens` 全额输出」预扣，转发，再按上游回的实际 `usage` 结算、多退。
3. **任务内透支**：放行之后，这个任务可以一直用到 `ceiling`，即使余额中途变成负数——**不在任务中途掐断**。负数从之后发放的额度里补；所以每笔购买最多被透支一个任务的 `ceiling`（维护者 2026-11-21 同意略超 40%）。
4. **任务到顶**：任务累计到 `ceiling` 时，网关对下一个请求回 `task_budget_exhausted`；引擎把它当作已有的「预算用完」结局处理（`fix_failed:budget` 同一条路），不报新错误。
5. **幂等**：同一 `task_id` + 请求序号重放不重复扣；任务 30 分钟无请求自动关闭。
6. **失败照扣**（维护者 2026-11-21 定）：模型的钱已经花了；靠单任务上限 `ceiling`（§六）控制失败的损失，不退额度。

### 六、单任务上限（内置 AI 专用，比自带 key 紧）

内置连接下，`Limits` 由网关在开任务时下发（`ceiling` 换算成 token 上限 + 轮数上限），引擎照用；自带 key 时完全照旧。
草案数字按 §三的实测待定，原则：**P90 的成功任务放得下，撞上限的失败任务尽早停**。

### 七、后台调控：一张配置表，一分钟内生效

网关每 60 秒读一次配置（CloudBase 数据库一个集合，只有维护者能写），不发版、不重新部署：

| 配置项 | 用途 |
|---|---|
| `budget`：`budget_ratio`（0.40）、前置发放天数（30）、每日上限比例（1/3）、退款窗口内的每日上限比例（1/3） | 调整模型成本占比与发放节奏 |
| `kinds`：每类任务的放行线、单任务上限、是否开放 | 收紧失败成本；临时关掉某一类 |
| `prices`：上游模型 → 输入 / 缓存 / 输出单价、币种、汇率 | 服务商调价 |
| `routes`：`tavotto-auto` → 上游（主 + 备），可按任务类型分 | 换便宜模型、上游故障切换 |
| `breaker`：全局每日花费两档阈值 | 超第一档：全部改走最便宜的上游；超第二档：内置 AI 暂停到次日 |
| `overrides`：按授权号单独调额度 / 封停 | 处理滥用、给反馈问题的用户补额度 |
| `kill`：总开关 | 出事时一键关停，客户端回到「用你自己的 key」 |

另外两道保险：每家上游在服务商后台设月度花费上限（我们的代码出 bug 也刷不爆）；每台设备每分钟请求数、每笔购买每天的上限见 §二之一（防一天刷光）。
报警：熔断第一档、单个授权单日花费异常、上游连续失败，发企业微信（沿用现有通道）。

### 八、额度用完、网关不通：不卡人

- `quota_exhausted` / `task_budget_exhausted` / 网关不可达 / 令牌失效：一律不弹窗、不报红字。改图助手和 AI 修复的失败卡上，
  用现有的胶囊给两条路：**「改用我自己的 key」**（打开连接设置，自带 key 永远免费）、**「明天再用」**（显示额度恢复的日期）。
- 已经放行的任务不会因为余额用完被掐断（§五.3）。
- 免费功能完全不受影响。

### 九、隐私

- 走内置 AI 时，用户的脚本片段、截图、数据摘要会经过我们的网关转给上游模型商。**网关不存请求与回复内容**，只记：
  授权号的哈希、任务类型、上游模型、token 数、成本、耗时、结果码。保留 90 天。
- 用自带 key 时一个字节都不经过我们。
- `docs/privacy.md` 新增「Tavotto Agent」一节（维护者 2026-11-21 定名），并改掉「Optional AI assistant」里「Tavotto never relays these requests through its own servers」这句
  （改成「除了 Tavotto Agent，……」）。草稿如下，维护者 2026-11-21 认可，随网关上线的 PR 写进 privacy.md：

  > **Tavotto Agent (paid edition, optional).** If you have the paid edition and pick "Tavotto Agent" in the "Connect an AI" dialog,
  > your AI requests go to Tavotto's own relay (`ai.tavotto.cn`), which forwards them to DeepSeek's official API under Tavotto's account.
  > What is sent is the same as for any other AI you connect (your instruction, and what the assistant reads from the project to do the job,
  > including scripts, rendered previews and short data summaries). The relay does not store requests or replies. For billing it keeps,
  > for 90 days: a one-way hash of your license, the kind of task, the model, token counts, cost, duration and the result code.
  > Each request is signed with a key that stays on your computer, so the relay can check it comes from your activated device.
  > DeepSeek's own privacy terms apply to what it receives. If you use your own API key or a subscription sign-in instead,
  > nothing goes through Tavotto's relay.

### 十、部署：网关放在边缘节点 tavotto-dl，账本留在 CloudBase

**2026-11-21 在 CloudBase 测试环境实测**（探针函数测完已删）：

| 项目 | 事件型云函数（经 HTTP 访问服务） |
|---|---|
| 单次请求时长 | 130 s 正常返回（函数超时 900 s） |
| JSON 请求体 | **约 100 KiB 以上被拒**（`EXCEED_MAX_PAYLOAD_SIZE`；64 KiB 过、128 KiB 拒） |
| 二进制请求体 | 2 MB 过、5 MB 拒 |

一轮请求带着对话历史，`check` 的图片一次就是 MB 级，所以**事件型云函数不能做 AI 网关**。HTTP 云函数要先绑自定义域名才能挂路由（默认域名不许手动加），没有测；云托管这个账号里没有现成环境。

**决定**（维护者 2026-11-21）：网关跑在**国内更新边缘节点 tavotto-dl**（腾讯云轻量，上海，118.89.121.57，4 核 4G，同时承担 `up.tavotto.cn`）：
- 实测空闲：负载 0.00、可用内存约 3 GB、本月出网约 5 GB（套餐每月 500 GB，更新下载的闸门 450 GB）；到 `api.deepseek.com` 建连 13–28 ms、TLS 40–70 ms。
- 部署形态与边缘节点一致：经 infra 仓库（与 `edge/` 同一套部署脚本），Python 3.12 的 systemd 服务监听本机回环，nginx 反代 `ai.tavotto.cn`（DNS 与证书上线前另批）；请求体上限、超时由我们自己的 nginx 定，不受云函数网关限制。
- 授权台账、额度账本、配置表**仍在 CloudBase**（与 `license-api` 同一份），网关经 CloudBase 服务端接口读写，只有一个写入点；海外用户同样连这个节点。
- 代价：出口带宽 5 Mbps 与更新下载共用。文字请求几十 KB 不受影响；带图请求 1 MB 约 1.7 s。上线后看 `vnstat` 与网关耗时，必要时让更新下载在 AI 请求多时让路，或给网关单独换机器。
- 网关挂了只影响付费用户的 Tavotto Agent，客户端回到「改用自己的 key」（§八），与更新下载互不牵连（独立进程、独立 nginx server 块）。

### 十一、上线开关与步骤

- `hosted_ai.PRODUCTION_LIVE = False`：在网关正式部署并验通之前，正式安装里看不到「Tavotto Agent」连接、零网络。另有 `TAVOTTO_NO_HOSTED_AI=1` 逃生口。
- 步骤（每步先报合并大队长，再由维护者本人批准）：
  1. 测试网关（tavotto-dl 回环端口，经 SSH 隧道访问，不开公网）+ Stripe 测试授权，跑通「开任务 → 请求 → 结算 → 到顶 → 额度用完 → 改用自己的 key」；
  2. 候选托管模型每类任务每题 ≥ 3 次实测，维护者定额度；
  3. 正式网关、正式上游密钥、服务商花费上限；
  4. 隐私声明这一节上线；
  5. 翻开关。

### 十二、待维护者拍板

维护者 2026-11-21 已定：
- 托管模型 = DeepSeek 官方 API `deepseek-flash`，**国内外用户都用它**（海外节点也转 DeepSeek，不另配上游）；
- 额度 = 实付金额 × 40%，只算模型 token 费，允许透支一个任务（§二之一）；
- 失败的任务照扣（§五.6）；
- 「转成可编辑」算进（§一）；
- 对用户的名字叫「Tavotto Agent」（§一、§九）。

- 豁免已确认并登记 §1.8；§九的隐私说明草稿维护者同日认可（「可以的」），随网关上线的那个 PR 改进 `docs/privacy.md`（功能没上线前不写进去）。

## 后果

- 引擎改动集中在三处：`ai_providers` 多一种内置连接、`wires.post_json` 逐请求签名、引擎处理两个新的结局码；功能代码零权益判断。
- 新的服务端组件（网关 + 账本 + 配置）有持续成本与运维面；熔断与服务商上限是硬兜底。
- 额度数字不进代码：调价、促销、补偿都是改配置。
