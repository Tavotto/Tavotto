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
