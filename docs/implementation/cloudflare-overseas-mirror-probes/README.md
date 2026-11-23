# Cloudflare 境外只读镜像与境外巡检（设计草案）

状态：**草案，待定 ADR 编号**（不占号；定稿后迁成 ADR）。日期：2026-11-23。
本文只是设计：**不部署、不建任何 Cloudflare / 腾讯云资源、不改 DNS、不碰服务器**。具体实现与部署等维护者看过本文、拍板「待决事项」之后再开。
本仓库是公开仓库，本文不写服务器地址、云账号标识、密钥位置；这些在 infra 私有仓库的运行手册里。

## 0. 为什么做、不做什么

起因（2026-11-23）：GitHub Actions 构建机在境外，读 `https://dl.tavotto.cn/index.json` 时通时不通。`dl.tavotto.cn` 是腾讯云 CDN，加速区域只设了中国境内，**境外读它本来就不在设计范围内**。infra #71 已让 `cn-site-build` 改用 COS 密钥直读桶；`release-mirror` 的 `lag-check` 只读公网、没有密钥，一直红。

硬约束（来自 `services/journal_catalog/DEPLOYMENT.md` 与产品定位）：大陆用户直接访问的服务一律留在国内；Cloudflare 在大陆慢且不稳，所以**只服务「访问方在境外」的场景**，大陆客户端的路径一律不变。

不做：不搬任何大陆用户会走的入口；不放安装包；不碰签名私钥；不改客户端（见 §1.5）。

### 与「不用 Cloudflare」的对照（第三路径）

「境外 runner 读不到公网 index」还有一条零新增基础设施的路：给 COS 桶里 `index.json` 与最新版 `manifest.json` 开匿名读（或让 `lag-check` 也带只读密钥，像 #71 那样）。代价：桶从「私有」变成「部分公开」，打破 `cos-cdn-setup.md` 里「匿名直连 403」的验收口径，且仍绑在腾讯云境外访问质量上。本文仍推荐 Cloudflare：公开面只剩两个小文件、与腾讯云解耦、读流量免费、顺带给 §2 的巡检一个落脚点。但这是真取舍，列在 §3 选项 A。

## 1. 境外只读镜像（R2 + Worker）

### 1.1 镜像什么、不镜像什么

| 对象 | 境外有人读吗 | 是否镜像 | 理由 |
|---|---|---|---|
| `index.json`（下载入口：`mirror_enabled` / `latest` / `valid_until`） | CI（`lag-check`、`cn-site-build`）；将来任何境外消费方 | **镜像** | 几 KB，不可变语义靠 `manifest_sha256` 绑定 |
| `releases/<最新版>/manifest.json`（及仍 ready 的旧版） | 同上 | **镜像** | 与 index 一起读；`index.manifest_sha256` 必须等于它的字节，所以**逐字节**复制 |
| `updater/latest.json`、`releases/<v>/latest.json`、`updater.json` | **没有** | 不镜像 | 客户端 `decide_updater` 第 5 条要求包地址在「镜像根」`/releases/<v>/` 下，且不跟随重定向；放在 R2 上的清单里写的仍是国内包地址，境外客户端不能用。境外客户端本来就是 GitHub 优先（ADR 0121 来源顺序） |
| 三个安装包、三个 updater 包与 `.sig` | 境外用户走 GitHub Release | **不镜像** | 见 §1.7 对比 |
| `cn-site` 站点包 feed | 只有国内服务器拉 | 不镜像 | 与境外无关 |
| 期刊目录：`index.signed.json`（v1 / v2） | 境外客户端经 `www.tavotto.com`（Vercel 反代，边缘缓存 5 分钟）读 | **本期不镜像**，列为 §3 选项 C | 现有境外入口已存在且带缓存，收益小；见 §1.8 |
| 期刊目录：刊包 `/journals/<id>` | 同上 | **绝不镜像** | 要设备令牌、计首次读取、`no-store`；计数只有中国站 sqlite 一份。公开对象会绕过限额；在 Worker 里再做一份计数则违反「计数只有一份」 |
| 期刊目录：快照 `/snapshots/*.webp` | 同上 | 本期不镜像 | 现协议要令牌（不计数），内容寻址；若做，随选项 C 一起 |

### 1.2 同步：推送，不回源

COS 桶是**私有桶**（CDN 用回源鉴权读它），Worker 回源拉取就得在 Cloudflare 里放腾讯云密钥——不要。所以用推送：

- `release-mirror` 工作流（境外 GitHub runner）每次写完 COS（`sync` / `discover` / `rollback` / `disable` / 每日 `verify` 标 unavailable 之后），**在同一个 job 末尾**跑一个幂等命令 `release_mirror replicate --to r2`：
  1. 读 COS 里的镜像集合（`index.json` + 仍 ready 版本的 `manifest.json`）；
  2. 与 R2 比对（对象 metadata 里存源字节的 SHA-256），不同才写；
  3. **先写 manifest，最后写 index.json**（与现有「对象先到、入口最后」同序）；index 里没有的版本 manifest 从 R2 删除；
  4. 回读核对：R2 的 `index.json` 字节 == COS 的字节。
- R2 写入凭据：只限该桶的对象读写 API 令牌，放进现有 `cn-mirror-verify` 环境的 secret（该环境已是「无审批人、仅 main」）。Worker 只持有只读绑定，**不持有任何写凭据、任何腾讯云凭据、任何签名私钥**。
- 一致性与延迟：同 job 内秒级；R2 步骤失败 = job 红、下一个整点重试。R2 副本可能比 COS 旧，但不会比它新：`index.valid_until`（7 天）到期会让旧副本自己失效，消费方按既有契约（§2 决策表）降级到 GitHub。
- 新增一条告警：R2 副本 `generation` 落后 COS 超过 2 小时（`replicate` 失败时 job 红即可，另在 §2 的巡检里按 R2 读到的 index 与 GitHub 最新版对比）。

### 1.3 签名与信任

镜像不增加新的信任级别：

- `index.json` 本来就**未签名**，完整性靠 TLS + `manifest_sha256` + 消费方的 schema / `valid_until` 校验（`docs/release-mirror/trust-model.md` 的口径）；R2 与 COS/CDN 同一档。
- 安装包与 updater 包的完整性不依赖镜像：Tauri 清单签名由客户端内置公钥验、安装包有构建来源证明。
- 镜像只存公开发行物元数据，没有私钥、令牌、用户数据。

### 1.4 访问方怎么选线路

| 访问方 | 现状 | 改后 |
|---|---|---|
| infra `lag-check` | 读 `dl.tavotto.cn/index.json`，境外读不到 → 一直红 | 读 `https://<境外镜像域名>/index.json`（infra 仓库变量 `MIRROR_PUBLIC_BASE_URL_GLOBAL`，只改这一处） |
| infra `cn-site-build` | #71 已用 COS 密钥直读桶 | 可保持不变（已通）；若想去掉对 COS 读密钥的依赖，改读境外镜像，但它同一 job 里本来就要 COS 写密钥，**收益小，不改** |
| infra `verify --public` | 每日经 `dl.tavotto.cn` HEAD 安装包 | **见 §1.9：这一条会出问题，需另决** |
| 国内服务器 `monitor.sh` 的镜像巡检 | 读 `dl.tavotto.cn` | 不变（它在大陆，读 CDN 才有意义） |
| 网站 `tavotto.com`（Vercel） | 下载页只链接 GitHub；`loadMirror` 只在中文站构建（`isCn`）里跑 | 不变 |
| 桌面客户端 | 大陆：GitHub → 国内节点 → 镜像；非大陆：GitHub 优先、镜像后备 | **不改**（§1.5） |

### 1.5 为什么客户端不改

非大陆客户端本来 GitHub 优先；`dl.tavotto.cn` 对它们只是后备。要让它们多一个 R2 后备，就要放包、改 ADR 0121 的来源顺序与 `docs/privacy.md` 的出网清单、重做 updater 向量、动 1.0 收敛纪律——代价远大于「GitHub 对境外用户本来就能用」带来的收益。本期不做；若以后境外用户从 GitHub 下载出现可测的问题，再单独立项。

### 1.6 域名与证书

- 提议 `dl-global.tavotto.com`（备选 `mirror.tavotto.com`）。`tavotto.com` 的权威 NS 已是 Cloudflare（`dig NS` 实测），`api.tavotto.com` 的联系作者 Worker 也是同一 zone 下的路由，所以 DNS 由 Cloudflare 管、同账号内建 Workers Custom Domain 即可，证书由 Cloudflare 通用证书自动签发和续期，**无需手工证书**。
- `tavotto.cn` 的 DNS 在国内 DNSPod，**不动**；境外镜像不使用 `.cn` 域名。
- Worker：只许 `GET` / `HEAD`；路径白名单（`/index.json`、`/releases/<semver>/manifest.json`），其余 404；不列目录；关 `workers.dev` 与预览地址（照 `services/cla-cloudflare/deployment/wrangler.disabled.json` 与 `workers/contact-relay` 的写法）；`Cache-Control` 沿用源对象（index 60 秒，manifest 不可变）；不设 CORS；不记访问日志（`observability` 关）。
- 大陆访问 `tavotto.com` 子域不是目标，也不会有大陆流量被引到这里（没有任何大陆客户端配置指向它）。

### 1.7 安装包要不要放 R2

R2 出网免费，技术上可行。不放的理由：（a）境外用户从 GitHub Releases 下载本来就快，且是构建来源证明的原生位置；（b）多一份大文件意味着多一条同步、核对、清理、防盗刷链路（每版约 1.2 GB 量级）；（c）安装包落在 R2 上不能解决任何已知故障。放的好处：与 GitHub 解耦，GitHub 故障时有后备；成本约 10 GB 免费额度内。结论：**本期不放**；作为 §3 选项 D 保留。

### 1.8 期刊目录的境外入口

现状：客户端写死两个入口（`tavotto.cn` 与 `www.tavotto.com`），后者是 Vercel 薄反代到中国站，并带 5 分钟边缘缓存。Cloudflare 若要「叠加」，是给 `index.signed.json` 多一个副本；若要「替换」，必须同时处理刊包。刊包不能镜像（§1.1），所以不存在「整体替换」。

若做选项 C（只镜像索引与快照）需注意的两点，写在这里避免日后踩：

1. **回滚地板**：客户端对 v1/v2 索引都维护单调的 `catalog_rev` 地板，副本比客户端见过的旧会被拒；v2 在签名 / 哈希 / 回滚 / 配额类失败时**不换主机掩盖错误**（只有网络 / 5xx 才换）。所以 R2 副本必须与中国站发布**原子对齐**（发布脚本里同一步先推 R2 再切源站索引，或索引副本保持更旧时仍可被接受——这点要看客户端地板实现，做 C 之前再核）。
2. **客户端要加第三个入口**才会用到它，这要改客户端、ADR 0155 与 v2 契约。

因此本期不做，收益（替掉一层已有缓存）不抵风险。

### 1.9 一个要提前说的问题：`verify --public`

infra 的每日自检 `verify --public` 也在境外 runner 上经 `dl.tavotto.cn` HEAD 安装包与读 index。实测代码行为：公网域名**不可达**时只往 `problems` 里记「暂不可达——不改变版本状态」（所以不会误标 unavailable），但整次自检因此不算「全部通过」；而 `index.valid_until` 的续期恰恰挂在「自检全部通过」上。结论：**只要境外 runner 持续读不到 CDN，`valid_until` 7 天内不再续期，到期后镜像对下载页自动降级为 GitHub**。这比 `lag-check` 红更急。

处理（需要在实施 PR 里做，也可先单独修）：把 `verify` 的「公网域名核对」改成**可选且不影响续期**，国内可达性由国内服务器的 `mirror_check.py` 负责（它本来就在每 5 分钟跑）；或把这一步迁到国内节点上跑。这条与 Cloudflare 无关，建议不等本方案先修，列为 §3 选项 B。

### 1.10 成本（2026-11-23 取自 Cloudflare 官方定价页）

| 项 | 额度 / 单价 | 本方案预估用量 | 费用 |
|---|---|---|---|
| Workers Paid | $5/月起；含 1000 万请求、3000 万 CPU ms | 读镜像 < 1 万次/月 | 已含在 $5 |
| R2 存储 | 免费 10 GB-月，之后 $0.015/GB-月 | < 1 MB（几个 JSON） | 0 |
| R2 Class A（写） | 免费 100 万次/月 | 每小时一次对比、仅变更才写，< 3000 次/月 | 0 |
| R2 Class B（读 / HEAD） | 免费 1000 万次/月 | < 5 万次/月 | 0 |
| R2 出网 | 免费 | — | 0 |

即镜像部分**没有额外费用**。用量告警：在 Cloudflare 账号的通知里建「用量计费」类告警（具体可选项以控制台为准，实施时核）；另在巡检 Worker 每日摘要里带上自己的请求计数（KV 里累加），超过预估 10 倍时在摘要里标出来。

### 1.11 回退

- 把 `MIRROR_PUBLIC_BASE_URL_GLOBAL` 指回原地址（或 COS 直读，#71 的做法）→ 立即回到今天的状态；
- 关 `replicate` 步骤（工作流里一行条件）；
- 删除 Worker 路由与 R2 桶即彻底撤出。**没有任何客户端依赖它**，所以回退不需要发版。

## 2. 境外定时巡检（Workers Cron Triggers）

### 2.1 范围与局限

- 只代表「Cloudflare 边缘看过去」。这个视角对**境外访问方**有意义（境外用户走 `.cn` 回退、境外 CI、`www.tavotto.com` 反代回源），对大陆用户体验**没有代表性**；大陆视角由 §2.7 补。
- Worker 的出口位置由 Cloudflare 决定，可能飘；跨境偶发抖动会产生误报，所以见 §2.3：连续失败才报。
- **Workers 的 `fetch` 读不到对端证书**（官方文档未提供；社区确认仅有 `request.cf` 描述入站请求）。所以「证书剩余天数」**不能**放在 Worker 里。放法见 §2.5。

### 2.2 探测清单（不带密钥，不产生计费调用）

默认每 5 分钟一次（最短可到 1 分钟；5 分钟与服务器 `monitor.sh` 同频）。每项设 10 秒超时；耗时阈值只告警「连续慢」，不单次告警。所有探测带专用 UA（`tavotto-probe/1`），方便在访问日志里过滤。下表的「预期」均须在实施时对着真实服务**只读验证一遍**再写进代码，以真实响应为准。

| 端点 | 请求 | 判健康 | 备注 |
|---|---|---|---|
| `license.tavotto.cn` | 无副作用的 `GET` 到一个已知不存在的路径 | 404 且体是约定的 JSON 错误形状（`{"error":{"code":...}}`）；非 JSON（网关 / 反代自己的错页）算故障 | 服务没有健康路由。每次探测会触发一次云函数调用（约 8.6k 次/月），不触碰台账与 Stripe |
| `ai.tavotto.cn` | 无令牌的 `POST /v1/task/close`（空体） | 4xx 且为网关的 JSON 错误体 | 公网 nginx 只放 POST、`/healthz` 只在回环，所以不能 GET 健康接口。该路由未带任务头即被网关拒绝，**不会走到上游模型**，无计费；实施前读 `_lookup` 确认状态码与不触发 license-api |
| `up.tavotto.cn` | `GET /updater/latest.json` | 200 且 JSON 合法、三个平台键齐全、`url` 都以 `https://up.tavotto.cn/releases/<version>/` 开头；**404 = 正常撤指针（流量闸 / 人工关停），只报「信息」不报故障** | **只探指针，不取包**；专用 UA，避免污染节点的指针计数统计 |
| `review.tavotto.cn` | 无令牌 `GET /v1/quota` | 401 JSON | 实施前读实现确认该路径在无令牌时无写入；同样每次一次云函数调用 |
| 期刊目录（中国站） | `GET https://tavotto.cn/v1/journal-catalog/index.signed.json` 与 `/v2/...` | 200；信封合法；用**仓库里已公开的生产公钥**在 Worker 里验 Ed25519 签名（WebCrypto 支持）；`catalog_rev` 不低于上次所见 | 签名失败是严重告警（可能被篡改 / 发布错误） |
| 期刊目录（境外入口） | 同上，主机换 `www.tavotto.com` | 同上，且 `catalog_rev` 与中国站一致或差不超过缓存窗口 | 测 Vercel 反代到中国站这一段 |
| 期刊目录（接口链路） | `GET /v1/journal-catalog/journals/<任一id>?rev=1`，**无令牌** | 401 `device_unknown` | 与服务器 `monitor.sh` 同口径；**不要探 `POST /device`**：每 IP 每天 200 个令牌，会耗尽额度 |
| 境外镜像（本方案 §1） | `GET /index.json` | 200；`mirror_enabled`、`valid_until` 未过期；`latest` 与 GitHub 最新正式版（`releases/latest` 的重定向 tag，不用 API，避免限频）一致，落后超 6 小时告警 | 这条把 `lag-check` 的职责复制一份到 Worker |
| `dl.tavotto.cn` | **不探** | — | CDN 仅加速境内，境外常红是设计内的（今天的故障正是它）。国内可达性交给国内服务器上已有的 `mirror_check.py` |
| 官网 `www.tavotto.com`、`api.tavotto.com`（联系作者 Worker） | 首页 `GET` / 已知不存在路径 | 200 / 约定的 JSON 404 | 可选 |

### 2.3 告警去重与恢复

状态机，每个端点一份：

- **连续 2 次失败（约 10 分钟）才转 `bad` 并发「异常」**；单次失败只记录。
- `bad` 期间**不重复发**；每 6 小时发一条「仍未恢复（已 X 小时）」。
- 恢复（连续 2 次成功）发一条「已恢复，持续 N 分钟」。
- **汇总**：同一轮里 3 个及以上端点同时转 `bad`，合并成一条，并先探一个对照目标（如 Cloudflare 自己的 `/cdn-cgi/trace`）；对照也失败则判为「探测侧问题」，不发业务告警，只在恢复后补一条说明。
- **耗时**：连续 3 次超过阈值才算「慢」，单独一类、降级为每天汇总，不进即时告警。
- 与现有 `monitor.sh` 口径一致（只在状态变化时发），文案前缀用 `[巡检·境外]` 与 `[tavotto-cn]` 区分。
- **Worker 自己停了谁来报**：每天固定时刻发一条「巡检正常，N/N 项」摘要，既是汇总也是死人开关——哪天没收到，就是巡检挂了。另设 Cloudflare 的 Worker 错误通知（控制台里配置）作第二道。摘要可在 §3 选项 E 关掉，但不建议。

### 2.4 企业微信 webhook

现有的发送方：国内服务器 `monitor.sh` 与诊断服务各读一个 webhook；review 函数有可选的举报通知；本机诊断自动化也有一个。**值不进仓库、不进本文。**

建议：在**同一个群**里为巡检**新建一个机器人**（每个机器人 key 独立，可单独吊销；企业微信机器人有频率上限，分开也避免互相挤占），key 以 **Worker secret**（`wrangler secret put`）存放，不进 `wrangler.toml` 的 `[vars]`、不进日志。是否复用现有 key 由用户选（§3 选项 F）；复用的代价是 key 的泄露面多一处、无法单独吊销。

出网的企业微信接口在境外 Worker 上可达性要在实施时验证（通常可达）；发送失败要有第二条通道（KV 里记「待发」，下次成功时补发并标注延迟）。

### 2.5 证书剩余天数放哪

Worker 做不到，三个落点（可叠加）：

1. 国内服务器 `monitor.sh` 已对中国站证书做 14 天检查——保留。
2. 新增一个**每天一次的 GitHub Actions 定时 job**（infra 仓库，境外 runner，用 `openssl s_client`）：覆盖 `license` / `ai` / `up` / `review` / `tavotto.cn` / `www.tavotto.com`，< 21 天告警，发同一个 webhook（secret 存 GitHub）。review 域名的证书是需要手工续的那类，最值得盯。
3. Worker 里只能间接发现「证书已过期 / 握手失败」（fetch 报错），这已含在连续失败告警里。

### 2.6 状态存哪

**KV**。状态极小（每端点一个键：`state`、`since`、`fails`、`lastAlertAt`）；一个 cron 周期（5 分钟）远大于 KV 的最终一致窗口；同一 Worker 的 cron 不并发；不需要事务。D1 与 Durable Object 都可以但更重。`DEPLOYMENT.md` 里「不用 KV / D1」的理由是**大陆用户的读写要强一致且访问慢**，这里读写方只有 Worker 本身，不适用。写入只在状态变化与每日摘要时发生，远低于用量（付费版含读 1000 万、写 100 万 / 月）。Worker 配 `ALARM_DISABLED` 变量与 KV 开关 `probes_enabled`，可随时静默而不用删 Worker。

### 2.7 大陆视角（可选，建议做）

在国内服务器上用现成的 `monitor.sh` 同样的机制加一个 cron：探 license / ai / up / review / 期刊 / **dl（经 CDN）**，状态变化走同一个企业微信。境外与大陆两个视角**分歧**本身就是有价值的信号（例如「境外红、大陆绿」= 国际链路问题；「大陆红、境外绿」= 大陆网络或备案 / 拦截问题）。这条在 infra 仓库里改，不在本方案范围，仅列为后续。

### 2.8 成本

- Cron 触发：5 分钟一次约 8.6k 次/月，计入 1000 万请求额度；每次约 10–15 个子请求（不计费，付费版单次上限 1 万）。
- CPU：每次几十毫秒量级，远低于 3000 万 ms / 月。
- KV：读写均远低于付费版含量。
- 合计：**不超出 $5/月基础费**（与 §1.10 同一个套餐，不叠加）。被探测的国内端点各多出约 8.6k 次/月请求，可忽略。

### 2.9 回退

停用 Cron Trigger（或 KV 开关）即可，无任何下游依赖；删除 Worker 与 KV 命名空间即撤出。

## 3. 待决事项（需要用户拍板）

| 编号 | 问题 | 选项 | 推荐 |
|---|---|---|---|
| A | 怎么让境外 CI 读到下载索引 | ① R2 + Worker 镜像（本文）② COS 开匿名读两个小对象，或 `lag-check` 也带只读密钥 | ① |
| B | `verify --public` 在境外读不到 CDN 会让 `valid_until` 不续期（§1.9） | ① 公网核对改为可选、不影响续期，国内核对交给国内服务器 ② 迁到国内节点跑 ③ 先不管 | ①，**不等本方案先修** |
| C | 期刊索引 / 快照是否也放 R2 | ① 不做（推荐，已有 Vercel 反代与缓存）② 只镜像索引 ③ 索引 + 快照；任何一项都要改客户端并核回滚地板 | ① |
| D | 安装包是否放 R2 | ① 不放（GitHub 对境外够用）② 放 updater 包 + 安装包作 GitHub 后备 | ① |
| E | 每日「巡检正常」摘要 | ① 要（兼作死人开关）② 不要 | ① |
| F | 企业微信 webhook | ① 同一群新建独立机器人（推荐）② 复用现有 key | ① |
| G | 大陆视角巡检 | ① 同步在国内服务器上加 ② 暂不 | ① |
| H | 证书到期检查 | ① 每日 GitHub Actions job ② 只靠服务器现有检查 | ① |
| I | 仓库归属 | 镜像 Worker 与巡检 Worker 放 infra 仓库的 `workers/`（与 `contact-relay` 同位，私有仓库、与 release-mirror 同处）；本文是设计，迁成 ADR 后留在主仓库 | 按此 |

## 4. 需要用户本人做的操作（待方案通过之后）

1. 在 Cloudflare 账号里创建 R2 桶（建议名 `tavotto-mirror-global`），并创建**只限该桶对象读写**的 API 令牌；令牌放进 GitHub `cn-mirror-verify` 环境的 secret。
2. 在 `tavotto.com` zone 绑定自定义域名 `dl-global.tavotto.com` 到镜像 Worker。
3. 为镜像 Worker 与巡检 Worker 各执行一次 `wrangler deploy`（巡检 Worker 先建 KV 命名空间）。
4. 在企业微信群里新建机器人，把 webhook 经 `wrangler secret put` 填给巡检 Worker。
5. 在 Cloudflare 通知里建用量告警与 Worker 错误通知。
6. 在 GitHub 里把 `MIRROR_PUBLIC_BASE_URL_GLOBAL` 变量填成镜像域名，合入 `lag-check` 改动。

## 5. 实施拆分（定稿后）

1. infra：`replicate --to r2` 与测试（本地目录当桶的离线演练）；
2. infra：镜像 Worker（只读、白名单）与 `wrangler` 配置（照 `workers/contact-relay`）；
3. infra：`lag-check` 改读境外镜像；`verify --public` 调整（§1.9）；
4. infra：巡检 Worker 与状态机的离线测试（假 `fetch`、假 KV、假 webhook）；
5. infra：每日证书检查 job；
6. 用户部署并只读验收：先 dry-run 与手动触发，一周后再开 Cron 的告警（前一周只写 KV、不发消息）。

## 6. 本文没核实的

- 各端点「预期响应」尚未对真实服务逐一实测，实施时逐项核；
- 企业微信接口从 Cloudflare 出口的可达性；
- Cloudflare 通知里可选的用量告警维度；
- 付费版单 Worker 子请求上限官方页给的是 1 万（见 Cloudflare Limits 页），未实测；
- 选项 C 所需的客户端回滚地板细节（只在做 C 之前核）。
