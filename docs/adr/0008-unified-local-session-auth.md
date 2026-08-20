# ADR 0008：浏览器模式与桌面模式共享同一道本地会话认证

日期：2026-08-21　状态：已实施

## 背景（1.0 发布门禁审计确认的 P0）

桌面 sidecar 自 ADR 0002 起就有完整认证（一次性 nonce → HttpOnly cookie +
Host/Origin 校验 + 401 兜底）；浏览器/PyPI 模式却是**无认证的 localhost 应用**，
而它暴露的不是只读预览，而是目录浏览、项目打开/创建、布局与自动保存写入、
**写回源文件**、AI CLI 调用这类高权限能力。ADR 0002 定义的威胁模型（本机
其他页面/进程访问 loopback、drive-by localhost、DNS rebinding）对两种模式
同样成立——「桌面有认证、浏览器明确旁路」是审计确认的 P0 发布阻断项。

## 决定

**浏览器模式与桌面模式共享同一个安全中间件（`src/tavotto/security.py`），
差别只是「谁负责拉起窗口、nonce 怎么交接」，而不是「是否需要认证」。**

- `SessionState`（原 `desktop.DesktopState`，后者现为别名）泛化为：
  多枚一次性 nonce（relaunch 交接用，带 TTL）+ 有界的多 token 并存
  （两个浏览器上下文不互顶）+ 可选的本机 API secret。
- bootstrap 端点泛化为模式无关的 `POST /api/session/bootstrap`
  （`/api/desktop/bootstrap` 保留为同一处理器的兼容别名）；nonce 只允许
  兑换一次，随后签发 `HttpOnly; SameSite=Strict` cookie。
- 所有 `/api/*`、素材、导出、SSE、渲染图片路径默认拒绝未认证请求
  （401 `session_auth_required`）；公共面收敛到首屏 HTML、`/assets/*`、
  `/favicon.ico` 与 `/api/version`（实例探测的判据，只回版本号）。
- Host 只认 `127.0.0.1:<port>` 一种写法（堵 DNS rebinding 与 localhost
  花式拼写）；带 Origin 的请求必须严格同源。
- 浏览器模式启动：`main()` 生成 256-bit nonce，落地 URL 以 fragment 携带
  （`#dnonce=…`，fragment 不进 HTTP 请求行与访问日志）；前端
  `bootstrapDesktopSession()` 本来就是模式无关的，原样复用。
- cookie 参数按模式定：桌面是会话级（窗口即进程）；浏览器带
  Max-Age=30d（服务器常驻、浏览器会整个重开）。token 只在进程内存里，
  服务器一重启即全部作废——Max-Age 不是把有效期放宽的许可。

### 本机进程凭据（`engine/session_client.py`，纯标准库）

浏览器模式启动时把随机 secret 写进 `data_dir()/session/port-<端口>.json`
（目录 0700、文件 0600，退出删除）。**能读这份文件的进程本来就能读用户的
任何文件；网页（含 DNS rebinding 页面）读不到**——这是「同一用户的本机
进程」的身份证明，安全边界没有放宽。持有者两条路：

1. 请求头 `X-Tavotto-Auth: <secret>`——CLI 冒烟、`tavotto open` 交接的
   `/api/projects/open`、开发者手工 curl 都走这条（`handoff._http_json`
   自动附带）。
2. `POST /api/session/relaunch {"secret"}` 换一枚新的一次性 nonce——
   「已有实例在跑，把浏览器指过去」因此是一次**安全的 token 交接**
   （审计要求第 10 条），不是裸探测端口后直接开地址。

### 桌面模式的差异（全部保留）

- nonce 经 stdin 管道交接（环境变量对同用户进程可见），**不写**磁盘凭据
  文件——桌面的实例复用由壳的单实例 argv 转发负责，不需要 relaunch。
- `/api/session/relaunch` 在桌面模式回 404。

### 显式旁路（开发专用）

`--insecure-no-auth` flag / `TAVOTTO_INSECURE_NO_AUTH=1`：不装
`SessionState`，guard 全放行，启动时打印明显警告。用途只有三个：
vite dev proxy（Origin 是 `localhost:5173`，严格同源过不了）、Playwright
e2e（测界面行为，多标签页用例与 cookie 手续互相绞）、手工 curl 调试。
pytest 的 `test_client` 天然无状态即旁路，行为与从前零差异。

## 与审计最低验收标准的对照

审计 §4.3 的 10 条中 9 条照单实现；唯一刻意偏离是第 2 条的「动态 loopback
端口」：浏览器模式**保留首选端口 5089 +占用顺延**。理由：安全性质由认证
交付（每个请求都要 256-bit token 的 cookie 或本机凭据，Host/Origin 钉死，
端口可猜与否不再影响攻击面）；固定首选端口是实例复用（`resolve_port` /
`tavotto open` 交接探测）与既有工作流的基础，换成动态端口要把发现机制
整个重写、收益为零。fragment 一次性 nonce（第 2 条后半）照常。

## 验收

- `tests/test_browser_auth.py`：默认 deny（读写端点各一组）、DNS rebinding
  Host 矩阵、跨源拒绝、nonce 重放、伪造 cookie、本机凭据头、relaunch 交接
  一次性与多会话并存、凭据文件 0600 与退出清理、旁路档回归。
- `tests/test_desktop_sidecar.py`：桌面路径原有护栏全部保留（更名后同参）。
- `scripts/smoke_app.py`：真产物上**先断言未认证 401、再凭凭据文件继续**
  ——打包链路里认证没生效会当场红，而不是绿着发出去。
- e2e / bench 显式走 `TAVOTTO_INSECURE_NO_AUTH=1`（各自注释了理由）。
