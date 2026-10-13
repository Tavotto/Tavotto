# ADR 0118：发送问题反馈——诊断包只在用户逐次确认后，由本机引擎直传给维护者

日期：2026-10-10 · 状态：**Accepted；2026-10-13 起发行版默认开启入口（G-DIAG 已过，见文末「修订（2026-10-13）」），每次发送仍须用户确认**
相关：[0016 诊断 V2](0016-diagnostics-v2-frontend-state-tracing.md)、[0008 会话认证](0008-unified-local-session-auth.md)；规则全文 `docs/rules/backend/diagnostics.md`
「发送问题反馈」一节、`docs/rules/frontend/frontend-diagnostics.md`；隐私声明 `docs/privacy.md`「Diagnostics bundle」；
服务端契约（`Tavotto/infra`，v1.0.0）：`docs/diagnostics-api/{README.md,openapi.json,versioning.md,acceptance-cos.md,threat-model.md}`

## 问题

诊断包（ADR 0016）一直是「用户导出 → 自己发给维护者」。国内网络下这一步经常断在「怎么发给你们」：GitHub Issue 上传大文件要登录和翻墙，
群里发文件会带上用户的全部聊天上下文。维护者已经在国内节点部署了匿名诊断接收服务（`diagnostics-api`：发编号、签单对象上传授权、
核实摘要、保存 30 天），主仓库这一侧缺客户端。同时仓库的底线不能动：**诊断包不自动上传，遥测同意不等于诊断同意。**

## 决策

### 一、上传由本机 Python 引擎进程做，WebView 只遥控

`engine/diagsend.py`（纯标准库，Flask 父进程 import 链）实现契约：`init` → multipart 直传 COS（不经 Tavotto VPS）→ `complete`，
取消调 `cancel`。前端不碰远程：只有本机 `/api/diagnostics/send*` 六个端点（备包 / 看包 / 发送 / 状态 / 取消 / 丢弃），这样桶不需要 CORS，
前端也拿不到令牌与上传表单。

### 二、逐次授权：备包与发送是两步，只有「发送」出网

`prepare()` 在本机生成并保管**一份**包（内存里，≤ 10 MiB；超限不保管，让用户导出到本地，**不裁剪**），回「包里有哪几类内容」。用户可以把这份
**同一字节**存成文件。`start(confirmed=True)` 才发：请求体里 `confirm` 必须是字面 `true`；发送的就是备好的那份（SHA-256 与 prepare 时一致）。
没有任何「记住选择」，没有后台线程在启动时补发；状态只在内存里——取消、关窗、失败、进程退出之后没有任何东西自己继续。
一次发送内的有界重试只有：`complete` 丢了响应（网络错误 / 超时）与对端 `in_progress` / `upload_missing`（带 `Retry-After`，最多 4 次含第一次），
用同一份令牌幂等重放；其余失败（含 429 / 503）一律交还用户去点。用户再点 = 又一次明确授权：内容没变且服务端会话还开着就复用
`client_request_id`（幂等），内容变了 / 已取消 / 已过期则换新的 UUIDv4。`client_request_id` 是随机 UUIDv4，**绝不**复用遥测 `install_id`。

### 三、目的地由代码钉死，不由响应决定

- 功能开关：`TAVOTTO_DIAG_UPLOAD=1` **且** 没有硬开关 `TAVOTTO_NO_DIAG_UPLOAD=1` **且** `TAVOTTO_DIAG_ENDPOINT` 的主机 ∈ `ENDPOINT_HOSTS`。
  `ENDPOINT_HOSTS` 在 G-DIAG 通过前是**空集**：发行版里没有办法打开，界面入口（`GET /api/diagnostics/send` 回 `enabled:false`）也就不出现。
  开发 / 验收用本机回环：另设 `TAVOTTO_DIAG_DEV_LOOPBACK=1` 才放行 `127.0.0.1`（仍是 HTTPS 与证书校验）。
- `upload.url` 必须是 `https://<桶>-<APPID>.cos.<区域>.myqcloud.com/`（无用户信息 / 端口 / 查询串 / IP 字面量；回环开发模式只许本端点的 `/v1/local-upload`），
  表单字段名只许 token 字符、数量与长度有界；不合规 = 不上传并通知服务端作废（`upload_url_rejected`）。
- **不跟随任何重定向**：`urllib` 默认会把 301/302/303 的 POST 改成 GET 跟过去，所以专门的 `_NoRedirect`（order 400，抛 `HTTPError` 终止处理链；
  返回 None 会让默认处理器接手）。证书只经 `tlstrust.client_context()`，出站 HTTPS 的第五处（`tlstrust` 模块头与 `test_outbound_https_trust` 的清单同步）。
- 已知的盲点：不判解析后的 IP 是否内网——TUN / fake-ip 代理下合法域名会解析到 `198.18.0.0/15`，判了反而误杀。防线是主机名白名单 + 证书 + 不跟随重定向。

### 四、秘密与日志

`complete_token`、上传 URL / 表单字段、用户说明文字只活在 `SendSession` 的私有字段里；`public()` 状态、日志、诊断包、遥测里都没有。日志模板是字面量，
参数只有闭集里的阶段名与结果码（`logsafe.known`），异常文本不进日志（里面可能有地址）。本模块不调 `telemetry.capture`，只借 `_platform/_arch/_distribution`
三个枚举探测函数——**用户文本不可能进 PostHog**（`tests/test_diag_send.py` 用 AST 钉）。上传元数据全是白名单枚举（`os` / `arch` / `distribution` / 版本串 /
`bundle_schema` / 精确 `size` / 本机算的 `sha256`），说明文字 ≤ 1000 字符（openapi 硬顶是 4000，服务端默认 1000，取严的）。

### 五、失败码

引擎把 (HTTP 状态, 服务端 code, 阶段) 归并成 25 个**客户端**失败码（`diagsend.FAILURES`，值 = 能不能由用户再点一次）；界面按码翻译（`settings.diagnostics.send.failure.*`，
中英文，`DiagnosticsSendDialog.test.tsx` 钉两边键集相等）。码一旦发布不改名。

## 后果

- 正式用户看不到入口；打开它是 G-DIAG 的人工动作（真实 COS C1–C16、运维门禁、填 `ENDPOINT_HOSTS`、批准）。
- 真实 COS、真机 Tauri WebView 联调未做（模拟服务端 `tests/support/diag_fakes.py` 只证明模拟契约自洽）。
- 服务端以后加请求字段：先部署服务端再发客户端（请求模型 `extra=forbid`，见 infra `versioning.md`）；客户端忽略不认识的响应字段与 code。
- 删除报告靠维护者的管理 CLI（`delete TVD-…`），用户侧入口是在公开 issue 里引用报告编号请求删除（`docs/privacy.md`）。

## 补充（2026-10-10）：入口与确认框

故障卡（导出失败、准备失败、脚本运行失败等）里可以直接打开「发送问题反馈」：卡片没有可执行修复动作时它是主按钮，已有修复类主按钮时它在折叠详情里；开关关着时完全不出现，
与设置页入口是同一个判据；打开只是本机备包，逐次确认不变。确认框默认只露一句话与「发送」，细节折叠。实现与看护见 `docs/rules/frontend/frontend-diagnostics.md`。

## 修订（2026-10-12，#927 #928，G-DIAG 前置修复）

设计不变，补齐 #923 评审留下的四处缺口（竞态表与不变量全文在 `docs/rules/backend/diagnostics.md`「失败后重试 / 丢弃 / 迟到 start 的对账」）：

- **会话按项目分键**：「同一时刻一个会话」改为「同一项目同一时刻一个会话」。注册表 `_SESSIONS[scope]`，`scope` 取请求的项目上下文；
  端点按项目路由，跨项目的 id 一律 404。理由：受支持的「两个窗口、两个项目」用法下全局单例会让一个项目的对话框使另一个失效（只影响可用性，不会混包——前端项目代次守卫仍在）。
- **改参数重试先对账**（I1）：complete 发出过的失败会话改参数重试时，先问服务端首份的下落，再决定 done / 换新 id / unknown，不再孤立首份报告。
- **丢弃不吞送达结果**：complete 发出过的会话丢弃前走 settle，丢弃后留一份只含状态与编号的结果快照，`status(pid)` 可取回。这是对「状态只在内存」的小补充：快照同样只在内存、每项目一份、随进程退出消失，不新增磁盘或网络面。
- **迟到 start 的 409 是取消流程的一部分**：前端据此继续轮询到 cancelled 并重新备包。

功能开关默认关闭、`ENDPOINT_HOSTS` 仍为空集；真实 COS 验收 C1–C16 仍未做（NOT_RUN）。

## 修订（2026-10-13，G-DIAG 开放）

维护者批准：发行版里「发送问题反馈」**默认开启入口**，每次发送仍须用户在确认框点「发送」。上文各节里「默认关闭 / `ENDPOINT_HOSTS` 空集 / `TAVOTTO_DIAG_UPLOAD=1` 才开」是历史决定，被本节取代；其余（两步、逐次授权、目的地钉死、秘密不落地、失败码）不变。

- **出处。** 真实 COS 验收 C1–C16 全过（`Tavotto/infra#19`，2026-10-13）；#927 / #928 已由 #955 修复；服务端公网路由 `https://tavotto.cn/v1/reports/{init,complete,cancel}` 由 infra 另一 PR 上线（客户端先于它合入时，发送会以网络类失败收场并留保存退路，不影响本机功能）。
- **白名单与内置端点。** `ENDPOINT_HOSTS = {"tavotto.cn"}`；`PRODUCTION_ENDPOINT = "https://tavotto.cn"`，其主机 ∈ `ENDPOINT_HOSTS`（`test_diag_send.py` 钉）。`upload.url` 仍只认 COS 桶域名，存储为腾讯云 COS 上海桶 `tavotto-diag-1399072425`。
- **判据（`resolve_target()`）。** `TAVOTTO_NO_DIAG_UPLOAD=1` → 关；`TAVOTTO_DIAG_UPLOAD=0` → 关；未设 / 空 `TAVOTTO_DIAG_ENDPOINT` → 内置端点；设了就只认它（白名单主机，或另设 `TAVOTTO_DIAG_DEV_LOOPBACK=1` 的 `127.0.0.1`），不合规 → 关，**绝不回退内置端点**（误配不会悄悄发到生产）。
  `TAVOTTO_DIAG_UPLOAD=1` 不再需要，也没有效果；只有 `=0` 起作用。
- **默认开启不等于自动发送。** 开启的含义只是界面出现入口（`GET /api/diagnostics/send` 回 `enabled:true`）；备包 / 看包不出网，`POST .../send` 仍要字面 `confirm: true`；没有记住的选择、没有后台补发。遥测同意与诊断发送互不蕴含。
- **测试不连生产。** 测试进程默认 `TAVOTTO_NO_DIAG_UPLOAD=1`（`tests/conftest.py`；e2e `fixtures.ts` 同），诊断发送用例在 fixture 里摘掉它并指向回环模拟端；`conftest` 的会话级探针拦下任何发往生产主机的 `OpenerDirector.open` 并判红。
- **服务端限制（`Tavotto/infra`）。** 每 IP 每小时 6 次 `init`、每日最多 500 份 / 2 GiB；报告保留 30 天后删除。客户端不复述数字，只把 429 / 503 交还用户去点。
- **隐私声明。** `docs/privacy.md` 的「Diagnostics bundle」一节已按本行为改写（去向、30 天、关闭方式、删除方式）。
