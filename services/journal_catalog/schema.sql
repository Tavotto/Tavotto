-- 期刊目录服务的库结构。只用 SQLite 与 Cloudflare D1 都支持的语法
-- （PRAGMA / WAL 等 SQLite 专属设置放在 store.py，不放这里）。
-- 时间一律是 Unix 秒（整数）。

-- 匿名设备令牌：只存 sha256(令牌) 与创建时间，不存令牌本身。
CREATE TABLE IF NOT EXISTS devices (
    token_hash TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    -- 令牌创建时的客户端类型：app / agent-<宿主> / unknown，仅用于统计
    client TEXT NOT NULL DEFAULT 'app'
);

-- 首次读取记录：主键 (token_hash, journal_id)；已有行 = 重复读，不再计数。
-- 保留 1 年（purge 删除），用途仅「统计热门刊」。
CREATE TABLE IF NOT EXISTS reads (
    token_hash TEXT NOT NULL,
    journal_id TEXT NOT NULL,
    first_read_at INTEGER NOT NULL,
    -- 本次首次读取的客户端类型，仅用于统计；限额不按它分桶
    client TEXT NOT NULL,
    PRIMARY KEY (token_hash, journal_id)
);
CREATE INDEX IF NOT EXISTS idx_reads_time ON reads (first_read_at);

-- IP 维度的滚动窗口事件。ip_key = HMAC-SHA256(服务端密钥, IPv4 全址 / IPv6 的 /64)，
-- 不存明文 IP；只保留 48 小时。kind 取 'token'（签发令牌）或 'read'（首次读取）。
CREATE TABLE IF NOT EXISTS ip_events (
    ip_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ip_events ON ip_events (ip_key, kind, ts);

-- 限流规则（运行时可调，不用重新部署；core.Service 每 ~10 秒读一次）。键值表：
--   default.<名>  全局默认值覆盖（名 ∈ ip_tokens_per_day / reads_per_hour / reads_per_day / ip_reads_per_day），整数
--   kill_switch   '1' = 紧急开关打开：所有 IP 维度限额（ip_tokens / ip_day）一律不拦；设备维度照常
-- 表里没写的键，用环境变量（TAVOTTO_JC_*）或代码内置默认。
CREATE TABLE IF NOT EXISTS rules (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 按网段例外：管理员写的 CIDR（IPv4 段 / IPv6 前缀），命中（最长前缀优先）后放宽 IP 维度；
-- exempt=1 = 完全豁免 IP 维度（设备维度照常）。判断在 HMAC 之前、用请求里的明文 IP 在内存里做，明文 IP 不落库。
CREATE TABLE IF NOT EXISTS ip_cidr_rules (
    cidr TEXT PRIMARY KEY,
    exempt INTEGER NOT NULL DEFAULT 0,
    ip_tokens_per_day INTEGER,
    ip_reads_per_day INTEGER,
    note TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

-- 规则审计：谁、什么时候、改了什么。detail 里只会有规则键、旧值 → 新值、管理员写的 CIDR；没有任何客户端 IP。
CREATE TABLE IF NOT EXISTS rule_audit (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);

-- 可观测（保留 48 小时，与 ip_events 同）：每个 ip_key 当天出现过多少个不同设备、被拒了多少次（按 code）。
-- dev_tag = HMAC(服务端密钥, token_hash) 的前 16 位十六进制：只够数「不同设备」，不能回连 devices / reads 表。
CREATE TABLE IF NOT EXISTS ip_devices (
    ip_key TEXT NOT NULL,
    day INTEGER NOT NULL,
    dev_tag TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (ip_key, day, dev_tag)
);
CREATE INDEX IF NOT EXISTS idx_ip_devices_ts ON ip_devices (ts);
CREATE TABLE IF NOT EXISTS ip_denials (
    ip_key TEXT NOT NULL,
    day INTEGER NOT NULL,
    code TEXT NOT NULL,
    n INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (ip_key, day, code)
);
CREATE INDEX IF NOT EXISTS idx_ip_denials_ts ON ip_denials (ts);
