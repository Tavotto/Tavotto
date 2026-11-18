# ADR 0193：高级版授权基建——Stripe 收款 → 签发授权令牌 → 应用内激活

日期：2026-11-18 · 状态：**Proposed（本 ADR 只定契约；服务端、引擎、前端各一路实现。全部先在 Stripe 测试模式里走通，正式收款未开）**
**1.0 收敛纪律：新增产品能力。维护者 2026-11-18 裁决立项并给豁免，登记在 `docs/1.0-release-readiness.md` §1.8；入口在服务端验通前默认藏起来（§七「上线开关」）。**
相关：[0187 外部审阅](0187-external-review-share.md)（CloudBase、设备签名、出站笼子、上线开关的出处）、[0155 期刊标准库](0155-journal-catalog.md)（Ed25519 验签与 PyNaCl 延后 import 的出处）、
[0118 发送问题反馈](0118-diagnostics-send-report.md)（`diagsend` 出站笼子）、[0008 会话认证](0008-unified-local-session-auth.md)（新端点不得绕过）；
隐私声明 `docs/privacy.md` 新增一节（措辞待维护者点头，§九）。

## 问题

Tavotto 要有收入来抵持续的开销（模型调用、CloudBase、CDN），而且要尽快验证付费需求。约束有四条：

1. **永久免费承诺**（官网 FAQ，维护者 2026-11-10 公开）：已经上线的功能永远免费，付费只能买**新增**的东西。
2. **仓库是 AGPL-3.0**：本地任何「锁」别人都能改掉。真正的价值必须放在服务端或授权才能拿到的内容上，本地判断只管界面怎么显示。
3. **所有用户当小白**：激活、断网、过期、验签失败都不许报错卡住，一律静默回到免费版。
4. **大陆用户占多数**：激活接口必须在大陆稳定可达（中国站不用 Vercel 就是因为这个）。

商业细则（权益表、价格、名额、AI 额度）维护者 2026-11-18 定为「先接通基建，细则再谈」。所以本 ADR 只定**管道**，任何价格和权益都不写进代码。

## 决定

### 一、付费只能拦新功能（不变量）

- 权益是一个**闭集的功能键**：本 ADR 只登记 `hosted-ai`（内置 AI 额度）和 `review-plus`（外部审阅加量）两个键，具体内容由它们各自的 ADR 定。以后加键要改本节。
- **凡随任何一个正式版免费发布过的能力，代码里都不许出现权益判断**（不绑版本号：以后免费发布的能力同样一经发布即锁定）。外部审阅的免费额度（每台设备每天新建 1 个链接、更新不限、30 天顺延，ADR 0187 §三）随 0.21.3 发布即锁定，只能上调。
- 期刊库全量免费，不进权益（维护者 2026-11-18：按期刊收费等于随机筛用户）。以后的权益只能按「用得多 / 要省事」切，不能按「覆盖了谁」切。
- 看护：引擎里只有 `license.has(feature)` 一个入口；一个测试列出所有调用点，调用点只许出现在登记过的新功能模块里。

### 二、收款：Stripe Checkout（托管付款页）

- 账户是维护者的 Stripe 香港个人账户。付款方式在 Stripe 后台配置：**银行卡 + 微信支付**。支付宝在该账户上开不了。Checkout Session 不再收 `payment_method_types`，以后台配置为准。
- **只做一次性付款**（`mode=payment`）：微信支付不支持订阅，Checkout 的订阅模式、Billing、客户门户在微信下都不可用。年付 = 买一次给一段时长；再买一次在原到期日上顺延。卡用户的自动续费以后再说，不在本 ADR。
- **价格和权益只在 Stripe 里**：服务端只认带 `lookup_key` 的 Price，权益从 Price 的 metadata 读：`tavotto_features`（逗号分隔的功能键，必须都在 §一 的闭集里）、`tavotto_term_days`（时长）。metadata 缺失或含未登记的键，这个 Price 就不能下单。改价、加档、限量都在 Stripe 后台做，不发版。
- 官网要有价格页和可用的结账入口：这是 Stripe 审核微信支付的硬要求，正式收款前先上线（官网仓库另开 PR）。

### 三、服务端：腾讯云 CloudBase（维护者 2026-11-18 选定）

- 代码在 `Tavotto/infra` 的 `serverless/license/`，与 `serverless/review/` 分开。部署在外部审阅同一个环境（维护者 2026-11-18 同意），但**函数、集合、密钥都独立**（集合前缀 `lic_`），不读写审阅的任何数据，也不改环境级设置。
- 域名：`license.tavotto.cn`（客户端钉死，见 §五 `LICENSE_HOSTS`）。绑定是生产动作，先报合并大队长、维护者批准。
- **测试模式与正式模式彻底隔离**：
  - 两套函数各带 `STRIPE_MODE=test|live`，`event.livemode` 与之不符的事件一律 400 拒收；
  - 两套集合（`lic_test_*` / `lic_*`）；
  - 两把签名密钥：测试密钥的公钥**不进**正式版的 `PUBLIC_KEYS`，只在设了 `TAVOTTO_LICENSE_ENDPOINT` 的开发 / 验收环境里经 `TAVOTTO_LICENSE_TEST_PUBKEY` 认。测试卡 4242 换出来的令牌，正式安装永远不认。
- 2026-11-18 实测（上海出站，临时函数测完即删）：`api.stripe.com` 16/16 可达，热连接约 70–100 ms；`api.resend.com` 16/16 可达，热连接约 300 ms，偶发 1.6 s。所以发信必须异步并带重试。
- 接口（v1，**本节是唯一出处**）：

| 方法 · 路径 | 用途 |
| --- | --- |
| `POST /v1/checkout` | `{lookup_key, locale, email?, renew?}` → `{url}`：建 Checkout Session，返回付款页地址。新购时付款页上数量可调（1–20 份）；`renew = {license_id, device_pub, ts, sig}` 时是给本机这一份续费，数量固定 1。`lookup_key` 不在 Stripe 里或 metadata 不合规 → `price_unavailable` |
| `POST /v1/claim` | `{session_id}` → `{codes: [...]}`：付款成功页（`success_url` 带 `{CHECKOUT_SESSION_ID}`）直接显示这次买到的每一份的激活码，填错邮箱也拿得到；只在付款后 24 小时内、只对已开通的购买有效 |
| `POST /v1/stripe/webhook` | Stripe 事件入口，见下 |
| `POST /v1/activate` | `{code, device_pub, ts, sig}` → `{token}`：激活码换授权令牌，绑定这台设备 |
| `POST /v1/refresh` | `{license_id, device_pub, ts, sig}` → `{token}` 或 `{state: "revoked" \| "expired"}` |
| `POST /v1/recover` | `{email}`：把激活码重新发到这个邮箱；不论邮箱是否存在，回答一样 |
| `POST /v1/devices/release` | `{license_id, device_pub, ts, sig}`：本机解绑，腾出名额 |

- **webhook 处理**：
  - 用 Stripe 官方库对**原始请求体**验签（`constructEvent`），验不过回 400。
  - **幂等的主语是「这一笔购买」，不是事件**：以 Checkout Session id 为键写一条购买记录，「不存在才写入」与开通在同一步完成（先占 `_tx_lock` 再读再写，照 `serverless/review/` 在 PG 模式下的做法；该环境的事务本身不防并发覆盖）。处理中途失败就回 5xx，让 Stripe 重试；重试与 `completed` / `async_payment_succeeded` 两个事件都到，都只算一次。
  - `checkout.session.completed` 且 `payment_status == "paid"`，或 `checkout.session.async_payment_succeeded` → 开通这笔购买。
  - `charge.refunded` 且全额退款、`charge.dispute.created` → 作废**对应的那一笔购买**（经 PaymentIntent 找到 Session）；部分退款不改权益。
  - 其它事件一律回 200 不处理。
- **一份 = 一个授权 = 一个激活码**（维护者 2026-11-18 定）：一次新购买了 n 份，就开 n 个互相独立的授权，各有自己的激活码、设备名额和到期日。同一个邮箱可以持有任意多份；想在更多电脑上用就多买几份，也可以把其中一份的激活码给同门用。
- **每份授权 = 购买台账**：一份授权下挂若干笔购买（首购 + 各次续费），每笔记 `{session_id, payment_intent, lookup_key, features, term_days, paid_at, state: paid|void}`。权益**由未作废的购买算出**：按付款时间顺序把各笔时长首尾相接得到到期日，功能键取并集。
- **续费**只顺延指定的那一份（`checkout` 带 `renew`，由已激活的设备发起，所以知道是哪一份）；不带 `renew` 的购买一律是新的份数，不会自动挂到老授权上。
- **退款**：全额退款或争议 → 这笔购买作废，它开出的那几份授权（续费则是被续的那一份）按台账重算，没有剩余有效购买的就失效；部分退款不改权益。
- **激活码**：每份一个，`TVK-XXXX-XXXX-XXXX`，Crockford base32、60 位随机。服务端只存 HMAC-SHA256（服务端密钥），常数时间比较。明文只存在两处、都有期限：待发邮件记录（发出即删，最多保留 24 小时）、`claim` 用的显示槽（付款后 24 小时删除）——两处都用服务端密钥 AES-GCM 加密保存。之后谁也拿不回明文：`recover` 给这个邮箱名下每一份**换发新码**，旧码随即失效，已激活的设备不受影响。
- **发信异步**：开通时写一条待发邮件记录，由定时函数 `license-mailer`（每分钟）发出，失败指数退避重试，最多 24 小时；超时只删明文，用户可用 `claim` 或 `recover` 拿码。webhook 本身不等发信。
- **设备名额**：每份授权最多 2 台设备（维护者 2026-11-18 定；服务端配置项，改它不用发版），满了以后 `activate` 回 `device_limit`，邮件里附自助解绑链接（等价于 `devices/release`）。
- 失败码闭集：`invalid_code`、`device_limit`、`revoked`、`expired`、`price_unavailable`、`rate_limited`、`network`、`server`、`client_error`（与前端 `license.failure.*` 对拍）。
- 限流：`activate` / `recover` / `claim` 按来源 IP 每小时 20 次、按激活码 / Session 每小时 10 次；`checkout` 按来源 IP 每小时 30 次。

### 四、授权令牌

- 格式与远程期刊目录同形：`{"payload": base64(JSON), "sig": base64(Ed25519(payload)), "kid": "<密钥号>"}`。
- payload 字段：

| 字段 | 含义 |
| --- | --- |
| `v` | 1 |
| `lid` | 授权号（随机，不含邮箱） |
| `features` | 功能键数组（§一 闭集） |
| `exp` | 权益到期时刻（UTC 秒） |
| `iat` | 签发时刻 |
| `device` | 绑定设备公钥的 sha256 |
| `refresh_after` | `iat + 1 天`：过了这个时刻客户端才去刷新 |
| `offline_until` | `min(exp, iat + 断网宽限)`：断网时最多用到这里。宽限 14 天（维护者 2026-11-18 定；服务端配置项，改它不用发版） |
| `email_hint` | 打码后的邮箱（`m***@gmail.com`），只用于界面显示 |

- 签名密钥是**新的一对**，不与期刊目录共用。私钥只在函数环境变量里，由维护者本人放进去。公钥按 `kid` 内置在应用里（`license.PUBLIC_KEYS`），换密钥 = 发版加新 `kid`，旧 `kid` 保留到它签出的令牌全部过期。
- 令牌里不放邮箱明文、价格、付款信息。

### 五、客户端

- `engine/license.py`：
  - 存储在 `config.data_dir()/license/`：`token.json` + `device.key`（Ed25519 私钥 32 字节），文件权限 0600，锁 + 原子写。不写进 `config.json`，也不写进项目文件。
  - 验签走 PyNaCl，延后到用的时候才 import（与 `journalremote._ed25519_verify` 同一个函数，提出来共用，不复制）。
  - `has(feature)` 成立的条件：签名有效、`kid` 已知、`device` 是本机、`now < offline_until`、`now < exp`。任何一项不满足都当作免费版，不报错。
- 联网时机：只有用户点「激活」，或本机已有令牌且过了 `refresh_after` 时（每次启动最多一次，之后每天最多一次）。**没有令牌时一次网络都不发。** 出站全走 `diagsend._send`，目的地钉死在 `LICENSE_HOSTS = {"license.tavotto.cn"}`。
- 作者端请求（`activate` / `refresh` / `release`）带设备签名：对 `(时间戳, 请求体哈希)` 用本机设备私钥签名，时间戳偏差 > 10 分钟服务端拒收（与 ADR 0187 §三 同形，但是**另一把**设备密钥，不与审阅共用）。
- `/api/license/*`（会话认证照旧）：`status`、`activate`、`refresh`、`release`、`checkout`（返回付款页地址，由前端在系统浏览器打开）。
- 编辑器经 MCP 接入时与桌面版共用同一个数据目录，所以应用里激活一次，编辑器那边也生效。
- 诊断包与遥测：`license/` 目录整个排除；激活码、令牌、`email_hint` 列入脱敏规则。
- 逃生口：`TAVOTTO_NO_LICENSE=1` 时整个功能不出现、不联网，`has()` 恒为假。
- 开发 / 验收：`TAVOTTO_LICENSE_ENDPOINT` 指向测试模式的服务端；设了但不合规则，功能关闭，不回退到线上。

### 六、服务端用户的权益判断

- 消费权益的服务（以后的内置 AI 转发、审阅加量）**每次请求都在服务端判断**：
  - 验令牌签名与 `exp`；
  - 验请求带的**设备签名**，且该设备公钥在授权的设备名单里（令牌本身不是不记名凭证，拷到别的电脑上没用，3 台上限才有意义）；
  - 由授权的购买台账算出当前权益（读 `lic_` 集合），作废立即生效。
  改客户端绕不过。
- 这些服务各自的 ADR 定额度和计量；本 ADR 只保证令牌和授权记录可供它们读。

### 七、上线开关与上线步骤

- `license.PRODUCTION_LIVE = False`：服务端在正式模式部署并验通之前，正式安装里看不到任何入口、零网络（与 ADR 0187 同做法）。
- 下面每一步都先报合并大队长，再由维护者本人批准：
  1. 测试模式部署服务端，走通「付款 → webhook → 开通 → 发信 → 激活 → 刷新 → 退款撤销」；
  2. 官网价格页上线、Stripe 微信支付审核通过；
  3. 正式模式配 webhook 端点、放正式密钥；
  4. 维护者本人真付第一笔、走完全程；
  5. 另开 PR 把 `PRODUCTION_LIVE` 改为 `True`。

### 八、界面（截图稿另给维护者过目后定）

- 入口放在设置「关于与更新」里，一行。没有横条、弹窗、角标，不撑大原有卡片。
- 激活：粘贴激活码 → 一句话结果。失败按失败码给一句人话（例如「这台电脑已经是第 4 台了，先在别的电脑上解绑」）+ 一个按钮。
- 新功能未解锁时：照导出灰格式气泡（#1820）的做法，置灰 + 悬停一句话 +「了解高级版」，不弹窗。

### 九、隐私（措辞待维护者点头）

- 新增一条出机数据流：用户在 Stripe 付款页填的邮箱与付款信息由 Stripe 处理；服务端只存邮箱、授权号、功能键、到期时间、Stripe 的 Session / PaymentIntent 号、绑定设备公钥的哈希。
- 应用侧只在激活和刷新时发送：激活码或授权号、设备公钥、签名、时间戳。不发项目、脚本、数据、路径、本机信息。
- `docs/privacy.md` 新增「Paid edition and license activation」一节，具体措辞由维护者本人确认后才合入。

### 十、维护者 2026-11-18 定的参数

| 项 | 定为 | 改它要不要发版 |
| --- | --- | --- |
| 每份授权的设备数 | 2 | 不要（服务端配置） |
| 断网宽限 | 14 天 | 不要（服务端配置，写进令牌） |
| 一份对应什么 | 一份 = 一个激活码；一次可买多份，同一邮箱可持有多份 | 不要（服务端逻辑） |
| 部署环境 | 与外部审阅同一个 CloudBase 环境，函数 / 集合 / 密钥独立 | 不要 |
| 价格、档位、名额、各档含哪些功能键、时长 | 未定；不在代码里，全在 Stripe Price | 不要 |

## 后果

- 正面：付款、开通、发信全自动；价格和档位改在 Stripe 后台，不发版；大陆可达；改客户端绕不过服务端的权益判断。
- 代价：多一条出机数据流和一个付费链路；激活码可以被转给别人（靠 3 台设备上限与服务端判断兜底）；微信付款只能一次性，续费靠用户再买。
- 未做（NOT_RUN）：服务端实现；测试模式全流程；官网价格页；Stripe 微信支付审核；正式模式；Windows / macOS 真机；卡用户自动续费；发票；内置 AI 转发与审阅加量本身。
