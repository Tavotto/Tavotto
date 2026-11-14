# 部署：期刊目录服务（中国站现有服务器）

> 本文是步骤说明，**未在任何服务器上执行过**。

## 为什么是 sqlite 单机，而不是 KV / D1

* 用户主要在大陆：Cloudflare D1/KV 的大陆访问慢且不稳；中国站服务器本来就在大陆、有备案。
* 计数是**强一致的单行事务**（「查三个窗口 + 插入首次读取」必须原子，否则并发能超额）。
  KV 是最终一致、没有事务，做不了；sqlite 的 `BEGIN IMMEDIATE` 天然满足。
* 规模小：每次首次读取一次小事务，单机 WAL 绰绰有余。
* 搬 D1 的路径：`schema.sql` 只用 D1/SQLite 共有语法，可直接 `wrangler d1 execute … --file schema.sql`；
  `store.py` 的方法（`issue_device` / `first_read` / `purge` / `has_device` 与规则读写 `load_rules` 等）换成 D1 的 `batch` 事务实现，
  `core.py` 不用动；DATA 改放 R2。除非大陆之外的流量成为主体，否则不必搬。

## 步骤

1. **代码与运行环境**：把 `services/` 里的 `journal_catalog/`（连同 `services/__init__.py`）拷到
   服务器，如 `/opt/tavotto-jc/app/services/...`。v2 服务端共享应用内的唯一事实契约：同时将
   `src/tavotto/__init__.py`、`src/tavotto/engine/__init__.py` 和
   `src/tavotto/engine/journalfacts_contract.py` 按包路径复制到 `/opt/tavotto-jc/app/tavotto/`；
   仅部署旧 services 目录不够，缺契约时服务拒绝启动。Python 3.10+，纯标准库；gunicorn 单独装：
   `python -m venv /opt/tavotto-jc/venv && /opt/tavotto-jc/venv/bin/pip install gunicorn`。
2. **目录与权限**：专用用户 `tavotto-jc`。`/opt/tavotto-jc/data`（DATA，服务账号**只读**）、
   `/var/lib/tavotto-jc/`（sqlite，服务账号可写）。
3. **环境文件** `/etc/tavotto-jc.env`（0600，root 持有）：
   ```
   TAVOTTO_JC_DATA=/opt/tavotto-jc/data
   TAVOTTO_JC_DB=/var/lib/tavotto-jc/catalog.sqlite
   TAVOTTO_JC_IP_SECRET=<openssl rand -hex 32>
   TAVOTTO_JC_IP_MODE=xff_rightmost
   ```
   缺 `TAVOTTO_JC_IP_SECRET` 进程起不来（拒绝启动）。
4. **WSGI 进程**（systemd 示意）：
   ```
   [Service]
   User=tavotto-jc
   WorkingDirectory=/opt/tavotto-jc/app
   EnvironmentFile=/etc/tavotto-jc.env
   ExecStart=/opt/tavotto-jc/venv/bin/gunicorn --workers 2 --threads 4 \
     --bind 127.0.0.1:8788 --access-logfile - --access-logformat '%(s)s' \
     services.journal_catalog.wsgi:application
   Restart=on-failure
   ```
   访问日志格式只留状态码，不记 IP / 路径。uwsgi 同理：`--module services.journal_catalog.wsgi:application`。
5. **nginx 反代**（加进 `tavotto.cn` 现有的 server 块，与诊断上报的 `/v1/reports` 并列；中国站入口就是 `https://tavotto.cn/v1/journal-catalog/`；海外入口 `tavotto.com` 见文末，反代带来的 `X-Tavotto-Proxy-Secret` / `X-Tavotto-Client-IP` 头 nginx 默认原样透传，不要改写或丢弃）：
   ```
   location /v1/journal-catalog/ {
       proxy_pass http://127.0.0.1:8788;
       proxy_set_header X-Forwarded-For $remote_addr;   # 覆盖而不是追加，客户端伪造无效
       proxy_set_header X-Tavotto-Device $http_x_tavotto_device;
       proxy_set_header X-Tavotto-Client $http_x_tavotto_client;
       proxy_set_header Host $host;
       access_log off;     # 或只记状态码，不记 IP / URI
   }
   ```
   只透传真实 IP 头：`X-Forwarded-For` 用 `$remote_addr` 覆盖；若前面还有 CDN，改用 CDN 的真实 IP 变量，
   并把 `TAVOTTO_JC_IP_MODE` 与该链路对齐。直连部署不放 nginx 时保持 `direct`。
6. **DATA 同步**：发布工具生成新目录后，用 `rsync -a --delete-after` 同步到一个临时目录，再**先拷 `r<rev>/` 与
   `snapshots/`，最后原子替换 `index.signed.json`**（`mv` 同文件系统内重命名），这样索引指向的 rev 一定已就位。
   旧 rev 目录保留一段时间，让持旧索引的客户端还能读到（缺了会收到 `stale_rev`）。
7. **每日 purge**（reads 保留 1 年、ip_events 48 小时）：
   ```
   17 3 * * * tavotto-jc  set -a; . /etc/tavotto-jc.env; cd /opt/tavotto-jc/app && /opt/tavotto-jc/venv/bin/python -m services.journal_catalog.wsgi --purge
   ```
   不跑也不会出错，但库会一直长，且保留期承诺失效——要把它当作隐私义务的一部分去监控（见下）。
8. **备份**：`sqlite3 catalog.sqlite ".backup '/backup/catalog-$(date +%F).sqlite'"` 每日一次（不要直接拷 WAL 活文件），
   保留 7 天。库里没有明文 IP 与令牌，但备份也要受 1 年保留期约束，别永久留存。
9. **监控**：
   * 429 比例（按 `code` 分，来自 nginx 状态码计数或 gunicorn `%(s)s`）：`ip_tokens` / `ip_day` 突增说明被刷或 NAT 后用户多（先 `top` 看出口，再决定是否 `exempt-cidr`，见上「调限流规则」），
     `quota_*` 偏高说明限额太紧。
   * 5xx、`index_unavailable`（503）、进程存活。
   * purge 任务最近一次成功时间（`reads` 里最早一条不应早于 1 年多几天）。
   * 磁盘与 sqlite 文件大小。

## 调限流规则（不用重新部署）

规则在 sqlite 里，服务约每 10 秒读一次，改完最多 10 秒生效；环境变量里的值是规则表没写时的缺省。管理命令在服务器上、
与服务同一个库（`TAVOTTO_JC_DB`）、用能写库的账号运行（改动写进审计表，操作者取 `--by` 或 `$USER`）：

```
cd /opt/tavotto-jc/app && set -a && . /etc/tavotto-jc.env && set +a
PY=/opt/tavotto-jc/venv/bin/python; A="$PY -m services.journal_catalog.admin"
$A rules list                                              # 看现状 + 最近审计
$A rules set ip_tokens_per_day 300                         # 改全局默认（也可 ip_reads_per_day / reads_per_hour / reads_per_day）
$A rules exempt-cidr 203.0.113.0/24 --note 某大学出口       # 该网段完全豁免 IP 维度（设备维度照常）
$A rules exempt-cidr 198.51.100.0/24 --tokens 1000 --reads 10000   # 或放宽到指定值
$A rules exempt-cidr 2001:db8:aa::/48 --tokens 500         # IPv6 前缀（限额桶仍是 /64）
$A rules clear 203.0.113.0/24                              # 删一条网段例外
$A rules clear ip_tokens_per_day                           # 删一条默认值覆盖，回到环境变量 / 内置默认
$A kill-switch on                                          # 紧急：所有 IP 维度限额一律不拦（设备维度照常）；off 恢复
$A top --n 10                                              # 当天不同设备最多的 10 个出口（ip_key 前 8 位）与被拒次数
```

典型场景：监控里 `ip_tokens` / `ip_day` 的 429 突增 → `top` 看是哪个出口、设备数是否合理（几十到几百台 = 校园 / 公司网络）→ 与对方确认后
`exempt-cidr` 放宽该网段；怀疑被刷则不加例外、必要时调低默认。`top` 里只有 ip_key 前 8 位，对不回具体 IP；要知道是哪段，
请对方提供出口网段，或在放宽前后观察 `top` 里该前缀的变化。网段判断用请求里的明文 IP 在内存里比对，明文 IP 不落库。
命令行拒收比 `/8`（IPv6 `/32`）更宽的网段；要整体放开用 `kill-switch on`。

## 回滚

停服务、换回旧代码、重启即可；`schema.sql` 是 `CREATE … IF NOT EXISTS`，表结构只增不改。

## 海外入口：tavotto.com 上的 Vercel 反代（一个后端、两个入口）

海外用户先走 `https://tavotto.com/v1/journal-catalog/`，它只是转到中国站（`tavotto.cn`）上的同一个服务；
**计数只在中国站那份 sqlite 里**，反代不存任何状态。代码在 `services/journal_catalog/vercel_proxy/`。

1. **共享密钥**：`openssl rand -hex 32` 生成一份，**同时**配在两边（都走环境变量，不进仓库、不进日志）：
   * 中国站服务：`/etc/tavotto-jc.env` 加 `TAVOTTO_JC_PROXY_SECRET=<密钥>`，重启服务。没配 = 后端不采信任何反代声明的 IP（海外用户会被算成反代出口 IP，限额互相挤占，**上线前必须配**）。
   * Vercel（tavotto.com 项目）：`TAVOTTO_JC_PROXY_SECRET=<同一密钥>`；`TAVOTTO_JC_ORIGIN=https://tavotto.cn`（可省，默认值）。
2. **把函数并进网站项目**：把 `vercel_proxy/api/journal_catalog.py` 拷到 tavotto.com 项目的 `api/` 下；把
   `vercel_proxy/vercel.json.fragment` 里的 `rewrites` 项**并进**现有 `vercel.json`（不要整份覆盖），`functions` 同理。
   路径靠 rewrite 的 `?p=:path*` 传给函数，不依赖函数看到的路径（见 `services/telemetry_proxy/README.md` 的 rewrite 教训）。
3. **先在 staging / preview 部署验 rewrite**（这一步没法在本仓库里测）：
   ```
   curl -si https://<preview>/v1/journal-catalog/index.signed.json            # 200，Cache-Control: public, max-age=300, s-maxage=300
   curl -si -X POST https://<preview>/v1/journal-catalog/device               # 200 {"token":...}
   curl -si https://<preview>/v1/journal-catalog/journals/<id>?rev=<rev> -H "X-Tavotto-Device: <token>"   # 200，且 `?rev=` 原样到了源站
   curl -si https://<preview>/v1/journal-catalog/admin                         # 404（白名单之外）
   ```
   若 `rev` 没带到源站（刊包返回 400 `bad_request`），说明 rewrite 没有保留原查询串，改 destination 写法后重验。
4. **验 IP 限额算的是真实客户端**：从同一台机器经 `tavotto.com` 连签 6 个令牌，第 6 个应回 429 `ip_tokens`（默认每 IP 每天 200 个，这一步要验的话先 `rules set ip_tokens_per_day 5`，验完 `rules clear ip_tokens_per_day`）；
   再从另一台出口不同的机器签一个，应成功。若第 2 台也被限，说明后端把反代出口当成了客户端——检查两边密钥是否一致。
5. **验伪造无效**：直接对 `https://tavotto.cn/v1/journal-catalog/device` 带 `X-Tavotto-Client-IP: 1.2.3.4` 但不带或带错密钥头，限额仍按连接 IP 算。
6. **缓存**：边缘只缓存 `index.signed.json`（5 分钟）；换版本发布后最多 5 分钟客户端才看到新索引，这期间旧索引指向的 `rev` 目录仍在源站（见上「DATA 同步」保留旧 rev），不会 404。
7. **监控**：Vercel 函数的 5xx / 502（`origin_unreachable`）比例；中国站 429 比例按入口看不出来（计数不区分入口，这是有意的）。
