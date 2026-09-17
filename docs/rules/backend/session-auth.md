# 会话认证（ADR 0008，勿破坏）

> 原文出自 `src/tavotto/AGENTS.md`「会话认证（ADR 0008，勿破坏）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

**会话认证在 `src/tavotto/security.py`，桌面与浏览器模式共用一道边界**
（2026-08-21，1.0 审计的 P0 修复）：一次性 nonce →
`POST /api/session/bootstrap` → HttpOnly + SameSite=Strict cookie，
Host 只认 `127.0.0.1:<port>`、带 Origin 必须同源，`/`、`/assets/*`、
`/api/version`、bootstrap/relaunch 之外全部 401 兜底。浏览器模式的 nonce
在落地 URL 的 fragment（`#dnonce=`），另写 0600 的本机凭据文件
（`engine/session_client.py`，**纯标准库**——Flask 父进程与 handoff 都
import 它）：本机 CLI/冒烟凭 `X-Tavotto-Auth` 头直连，二次启动/交接凭
`/api/session/relaunch` 换新 nonce（实例复用 = 安全的 token 交接）。
**旁路只有三个**：pytest 的 test_client（无状态天然旁路）、
`--insecure-no-auth` / `TAVOTTO_INSECURE_NO_AUTH=1`（vite dev proxy、
e2e、手工 curl；启动时打印警告）。看护 `tests/test_browser_auth.py` +
smoke_app 的「未认证必须 401」硬断言——**别再让任何新端点绕过 guard**。
