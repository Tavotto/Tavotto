# 期刊目录服务（线协议 v1）

纯标准库、与平台无关的小服务：把「已签名的期刊目录」分发给 Tavotto 客户端与 Agent。
**不属于 Tavotto 包**：`pyproject.toml` 已把 `services/**` 排除出 wheel/sdist。
设计依据：`docs/adr/0155-journal-catalog.md` §4b。结构照 `services/telemetry_proxy/`。

```
services/journal_catalog/
  core.py      路由、令牌、限额口径、IP 哈希、路径校验（全部业务逻辑，时间可注入）
  store.py     sqlite3（WAL）存储；检查 + 写入在同一个 BEGIN IMMEDIATE 事务里
  schema.sql   建表（只用 SQLite 与 Cloudflare D1 共有的语法）
  wsgi.py      唯一入口：WSGI `application` + `--purge` + 本地开发服务器
```

数据目录 `DATA`（**只读**，由发布工具生成，不在仓库里）：

```
DATA/index.signed.json                      已签名索引信封
DATA/r<rev>/journals/<journal_id>.json      每本刊一个刊包（rev = catalog_rev）
DATA/snapshots/<sha256>.webp                高亮短图，内容寻址
```

## 协议

基址 `https://<host>/v1/journal-catalog/`。所有错误体都是 JSON `{"code": ...}`，不回显用户输入。

| 路由 | 令牌 | 计数 | 成功响应 |
|---|---|---|---|
| `GET /index.signed.json` | 不需要 | 不计 | 原样字节，`application/json`，`Cache-Control: public, max-age=300` |
| `POST /device` | 不需要 | 计 IP 签发数 | 200 `{"token": "..."}` |
| `GET /journals/<journal_id>?rev=<int>` | `X-Tavotto-Device` | 首次读取计 | 刊包原样字节，`Cache-Control: private, no-store` |
| `GET /snapshots/<sha256>.webp` | `X-Tavotto-Device` | 不计 | `image/webp`，`public, max-age=31536000, immutable` |

* `journal_id` 必须匹配 `^[a-z0-9][a-z0-9_.-]{0,95}$`；`rev` 为正整数；`sha256` 为 64 位小写十六进制。
* 路径只用校验过的 id / sha 拼接，并断言 resolve 后仍在 `DATA` 下。

### 错误码

| HTTP | code | 含义 |
|---|---|---|
| 400 | `bad_request` | id / rev / sha 不合法或缺 rev |
| 401 | `device_unknown` | 令牌缺失或服务端不认识 |
| 404 | `stale_rev` | 没有这个 rev 目录（客户端应重新拉索引） |
| 404 | `no_such_journal` | rev 存在但没有这本刊 |
| 404 | `no_such_snapshot` | 没有这张快照 |
| 405 | `method_not_allowed` | 方法不对 |
| 429 | `quota_hour` / `quota_day` / `ip_day` / `ip_tokens` | 超限，带 `retry_after_s`（同时给 `Retry-After` 头） |
| 503 | `index_unavailable` | 数据目录里还没有索引 |

### 客户端类型头

所有请求可带 `X-Tavotto-Client`：`app` 或 `agent-<宿主名>`（整体匹配
`^(app|agent-[a-z0-9][a-z0-9-]{0,31})$`）。缺省按 `app`；不合法的值记为 `unknown`，**不报错**。
`POST /device` 把它记进 `devices.client`，每次首次读取记进 `reads.client`。**只用于统计**，
限额不按它分桶（防止靠改头绕过）。

## 限额口径

只算「某令牌对某本刊的**首次读取**」。`reads(token_hash, journal_id, first_read_at)` 主键
`(token_hash, journal_id)`：已有行 = 重复读，直接放行不计数（`catalog_rev` 变了重拉同样放行；
种子里的刊客户端根本不会请求）。首次读取依次检查，全部是**滚动窗口**（无日历日，无时区问题）：

| 项 | 默认 | 超限 | 配置 |
|---|---|---|---|
| 令牌 1 小时内首次读取 | 10 本 | 429 `quota_hour` | `TAVOTTO_JC_READS_PER_HOUR` |
| 令牌 24 小时内首次读取 | 20 本 | 429 `quota_day` | `TAVOTTO_JC_READS_PER_DAY` |
| 同一 IP 24 小时内首次读取合计 | 2000 本 | 429 `ip_day` | `TAVOTTO_JC_IP_READS_PER_DAY` |
| 同一 IP 24 小时内签发令牌 | 200 个 | 429 `ip_tokens` | `TAVOTTO_JC_IP_TOKENS_PER_DAY` |

IP 级两项对 app 与 agent **合计**，不分客户端类型。

**这些数字都可以运行时调**（用户 2026-11-05：学校 / 公司共用一个出口 IP，原先每 IP 5 个令牌不够）：环境变量是缺省，
规则表（`rules` / `ip_cidr_rules`）覆盖它，服务约 10 秒内读到，不用重新部署；怎么改见 `DEPLOYMENT.md`「调限流规则」。
IP 维度默认 200 个令牌 / 2000 本 per 天 ≈ 一个出口背后约 200 台设备 × 日均约 10 本；设备维度（10 本 / 小时、20 本 / 天）不变。`retry_after_s` = 窗口内最早一条过期还要等的秒数（至少 1）。
三项都通过才写入 reads 行并返回刊包。

## 隐私与保留期

* 令牌：`secrets.token_urlsafe(24)`；库里只存 `sha256(令牌)` 与创建时间。
* IP 不存明文：`ip_key = HMAC-SHA256(服务端密钥, IPv4 全址 | IPv6 的 /64 前缀)`，密钥来自环境变量
  `TAVOTTO_JC_IP_SECRET`，**缺失则拒绝启动**。`ip_events` 只保留 **48 小时**。
* 客户端 IP 取法：默认直连 `REMOTE_ADDR`；`TAVOTTO_JC_IP_MODE=xff_rightmost` 时取 `X-Forwarded-For` 最右一段
  （只在前面确有受信任反代、且反代覆盖该头时使用）。
* 读取记录（`reads`：哪个令牌、哪本刊、何时、哪类客户端）保留 **1 年**，到期删除；用途仅「统计热门刊」。
  `devices` 在自身满 1 年且已无任何 reads 后清除。
* 服务不写访问日志；任何日志都不写令牌、IP、刊 id 的明文组合。
* **清理**：`purge(now)`（`Service.purge`）。用 cron 每日跑一次：
  `python -m services.journal_catalog.wsgi --purge`（需同样的环境变量；只打印删除数量）。

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `TAVOTTO_JC_DATA` | 是 | 数据目录 DATA |
| `TAVOTTO_JC_DB` | 是 | sqlite 文件路径（可写目录） |
| `TAVOTTO_JC_IP_SECRET` | 是 | IP 哈希密钥；缺失拒绝启动。轮换会让 48 小时内的 IP 计数重新开始，可接受 |
| `TAVOTTO_JC_IP_MODE` | 否 | `direct`（默认）或 `xff_rightmost` |
| `TAVOTTO_JC_READS_PER_HOUR` / `_READS_PER_DAY` / `_IP_READS_PER_DAY` / `_IP_TOKENS_PER_DAY` | 否 | 见上表 |
| `HOST` / `PORT` | 否 | 仅本地开发服务器，默认 `127.0.0.1:8788` |

## Agent 接入说明

给在用户机器上跑的 Agent（Codex / Claude Code 等宿主）。步骤：

1. **取索引**：`GET /v1/journal-catalog/index.signed.json`，无需令牌、不计数，可按 `max-age=300` 缓存。
2. **取令牌**：`POST /v1/journal-catalog/device`（空体），得到 `{"token": "..."}`。**取一次、存下来反复用**
   （建议 0600 权限的本地文件）；不要每次请求都新签——同一 IP 每天的新令牌名额有限（默认 200 个）。
3. **取刊包**：`GET /v1/journal-catalog/journals/<journal_id>?rev=<索引里的 catalog_rev>`，请求头
   `X-Tavotto-Device: <令牌>`。
4. **取快照**（可选）：`GET /v1/journal-catalog/snapshots/<sha256>.webp`，同样带令牌，不计数。
5. 所有请求带 `X-Tavotto-Client: agent-<宿主名>`（如 `agent-codex`），仅用于统计。

**验签与校验**（与客户端 `engine/journalremote.py` 一致）：索引是信封
`{alg:"ed25519", key_id, payload:<base64>, sig:<base64>}`。`payload` 是 base64 字节，
**Ed25519 签名覆盖解码后的原始字节**（不做规范化）；用内置公钥验签通过才能 `json.loads(payload)`。
索引里每本刊有 `bundle: {sha256, bytes}`；拿到刊包后要核对**字节数**与 `sha256(原始响应字节)` 都相符，
不符整包作废。快照的文件名就是其内容 sha256，下载后同样要核对。验签失败、`catalog_rev` 倒退都当作不可用。

**计数**：只算该令牌对该刊的**首次读取**；重复读、rev 更新后重拉、种子刊都不计。每令牌 10 本/小时、20 本/天；
同一 IP 合计 2000 本/天、200 个新令牌/天（app 与 agent 合计；服务端可调，以 429 为准）。

**超限怎么办**：收到 429 就等 `retry_after_s` 秒后再试，或告诉用户稍后再来。**不要换新令牌重试**——
IP 级限额（`ip_day`、`ip_tokens`）会照样拦住，还会白白消耗签发额度。401 `device_unknown`：重新 `POST /device`
一次并保存新令牌（旧令牌作废）。404 `stale_rev`：索引过期，重新拉索引再按新 rev 请求。


## 两个入口与反代信任

`https://tavotto.cn/v1/journal-catalog/`（本服务）与 `https://tavotto.com/v1/journal-catalog/`（Vercel 薄反代，代码在
`vercel_proxy/`）。反代转发时带 `X-Tavotto-Proxy-Secret`（共享密钥，`TAVOTTO_JC_PROXY_SECRET`）与
`X-Tavotto-Client-IP`（真实客户端 IP）；**只有密钥对得上，本服务才用后者算 IP 限额**，密钥错 / 缺、或 IP 头不是合法地址，
一律忽略声明、按连接 IP 算。Agent 与 app 走哪个入口都一样，计数、令牌、读取记录是同一份。
