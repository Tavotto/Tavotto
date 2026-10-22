# ADR 0145：联系作者——在应用里写信，由本机引擎经 Tavotto 服务器代发，用户不需要自己的邮箱

日期：2026-10-22 · 状态：**Proposed（实现与测试已在分支内；服务端 `Tavotto/infra` 的 `/v1/messages/*` 由另一路同时实现，真实服务器 / COS / 真机未验）**
**1.0 收敛纪律：这是新增产品能力，不是 correctness / safety / compatibility / release blocker，须由维护者按 `docs/1.0-release-readiness.md` 明确豁免后才能合入。**
编号说明：起草时 0143 已被在飞的 #1277 占用、0144 已在 main，故取 0145。
相关：[0118 发送问题反馈](0118-diagnostics-send-report.md)（形状的出处）、[0121 更新来源顺序与回退](0121-app-update-source-order-and-fallback.md)（「大陆环境」判据的出处）、
[0008 会话认证](0008-unified-local-session-auth.md)；规则全文 `docs/rules/backend/diagnostics.md`「联系作者」、`docs/rules/frontend/frontend-diagnostics.md`「联系作者」；
隐私声明 `docs/privacy.md`「Contacting the maker」；接口契约（唯一出处）：diagnostics-api `/v1/messages/*`，v1。

## 问题

用户有话要对作者说（问题、想要的功能、使用中的困惑），今天的路是：去 GitHub 开 issue（要账号、国内常常打不开）、扫企业微信二维码（仅中文界面）、或自己想办法发邮件。
最后一条尤其不通：**很多用户没有配置系统邮件程序**，一个 `mailto:` 链接点下去要么什么都不发生、要么弹出一个陌生的邮件客户端向导。用户拍板：不需要登录、不需要配置自己的邮箱，应用内直接写信，由服务器代发。

## 决策

### 一、形状照抄 ADR 0118，只写差异

`engine/contactsend.py`（发信 + 取回回信）+ `engine/contactinbox.py`（本机收件记录），纯标准库、Flask 父进程 import 链。流程：`POST /v1/messages/init` →（有附件时）按服务端给的 `upload` 把每个附件以 multipart 直传 COS →
`POST /v1/messages/complete`（没有附件也要调，通知只在 complete 时发）；取消 `POST /v1/messages/cancel`；回信 `GET /v1/messages/{id}/replies`（`Authorization: Bearer <reply_token>`）。

沿用 0118 的全部笼子：**逐次授权**（只有一次用户点击 `start()` 才联网，没有记住选择 / 后台补发，取消 / 关窗 / 失败之后什么都不自己继续）；**本机引擎直传**（WebView 不碰远程，只有本机 `/api/contact/*`）；
**目的地由代码钉死**（见三）；**不跟随任何重定向**、证书只经 `tlstrust`（出站复用 `diagsend._send`）；**秘密不落地**（附件在 `POST /api/contact/send` 里用自带的内存流工厂解析、不碰 `request.files`，超过 Werkzeug 500 KiB 阈值也不会滚进磁盘临时文件，且无 Content-Length 的请求不收；`reply_token` / `complete_token` / 上传表单 / 主题 / 正文 / 邮箱只在内存会话私有字段里，状态对象与日志里没有；日志模板字面量、参数只有闭集里的阶段名与结果码）；
失败码闭集翻译（`contactsend.FAILURES`，前端 `contact.failure.*` 对拍）。与 0118 不同之处：附件字节只在发送那一次的内存里（结束即丢，重试由前端重新带稿）、同内容失败后重发复用 `client_message_id`（服务端幂等回同一个 `reply_token`，不会变成两封信）。

### 二、用户不需要登录或配置邮箱：全产品没有邮件协议链接

不用 `mailto:`、不唤起系统邮件程序。发送失败时界面只给三样：「再试一次」「复制内容」「复制地址 `feedback@tavotto.com`」，由用户自己决定怎么发。
`tests/test_contact_no_mailto.py` 扫 `web/src` 与 `src/tavotto` 钉住这个协议字面量一个都没有；`contact.test.tsx` 另在失败态量整页 HTML。收件地址 `FEEDBACK_EMAIL` 是 `engine/brand.py` ↔ `web/src/lib/brand.ts` 的严格同源对，只展示。

### 三、线路、选路与「大陆环境」判据（复用，不复制）

线路只有两个主机：`tavotto.cn`（国内节点，诊断接收服务同一进程）与 `api.tavotto.com`（Cloudflare Worker，只转发到 `tavotto.cn`，不存不记日志）。白名单 `contactsend.CONTACT_HOSTS`；
校验与诊断发送同一份 `diagsend.parse_endpoint`（加 `hosts` 参数）。开发 / 验收：`TAVOTTO_CONTACT_ENDPOINT`（逗号分隔，每个都要合规或属回环开发模式），**设了但不合规就整个功能关闭，不回退内置线路**。

选路：大陆环境先 `tavotto.cn` 后 `api.tavotto.com`，否则反之；传输失败（连不上 / 超时 / 证书）或 5xx 换另一条（init 凭 `client_message_id`、complete 凭 `complete_token`，都可安全重放），
429 / 4xx 是服务端明确答复、不换线路绕限流。回信轮询先问记录里成功的那条线路。

**「大陆环境」的判据只有一处：壳 `src-tauri/src/app_update.rs::source_order`（ADR 0121 §二）。** 它在 Rust 里，引擎是 Python 子进程，没法直接调用，又不许在 Python 里再抄一份时区 / 语言表（两份判据迟早漂）。
做法：壳拉起引擎时把结论写进环境变量 `TAVOTTO_NETWORK_REGION=cn|global`（`sidecar.rs`，用 `detect_order(None)` 的第一项是不是镜像），引擎读它。该判据的第三条（界面语言）壳在拉起时还不知道，
所以没有壳的安装（pip / 源码）与壳没算到的情形，引擎在「界面语言是 `zh-CN`」时同样按大陆处理——这是 ADR 0121 判据 3 在产品只有 `zh-CN` / `en-US` 两种界面语言时的直接读法，不是新的判据表。
两线路永远都在列表里，所以判错的代价只是多一次 ≤15 s 的快速失败。

COS 上传地址复用 `diagsend.validate_upload_url`（同一条 `_COS_HOST` 正则：`<桶>-<APPID>.cos.<区域>.myqcloud.com`）。契约给的就是这个形状，**不另放行 `cos.accelerate.myqcloud.com` 一类裸域名**（带桶名的 `<桶>-<APPID>.cos.accelerate.myqcloud.com` 因 `accelerate` 恰好符合区域段形状而被同一条正则接受，
那仍是该桶自己的腾讯云域名，不是新放行）。

### 四、应用内回信

发送成功（或 complete 已发出）后 `contactinbox` 把 (message_id, reply_token, route, sent_at) 存进 `config.data_dir()/contact_messages.json`（原子写、权限 0600 尽力而为），连同取回的回信与已读状态；**用户写的内容一样都不存**。若这份记录写不下去（数据目录不可写 / 盘满），信已送达的事实不撤回，但发送结果带 `inbox_saved: false`，成功态换一句话如实说「应用里收不到回信」（留了邮箱则说作者会回到邮箱；没留则请留邮箱再发一次），不再承诺回信出现在「我的来信」。
引擎启动时与每 6 小时问一次服务器（`start_poller`；**没有任何已发的信就一次网络都不发**）；有未读回信时：入口按钮上一个小圆点 + 通知轨里一枚胶囊（`StatusBar.NotificationRail` 的 Toast 形式，× 收起后只有未读数增加才重现）——
**不加挤页面的横条 / BannerStack**。对话框里「我的来信」列出信与回信，进页即已读。365 天后本机记录也删（与服务端同期）；没完成投递的记录只留 2 小时，轮询证实后才转正。
用户填的回信邮箱「上次填写」只记在前端 localStorage（可在窗里忘记），不进引擎、诊断、遥测。

### 五、「附上诊断信息」

勾选 = 现有诊断发送的 prepare（本机、零远程）→ 窗里预览内容类别 → 点「发送」才 `startDiagSend`，轮询到 `report_id` 后才把信发出去，信里只带编号。诊断包失败则信不发。复用 0118 的全部脱敏与确认语义，**不改诊断包生成与脱敏**。

### 六、遥测这次不加事件

遥测范围攒齐后一次升同意版本（`docs/rules/backend/telemetry.md`）；本模块不调 `telemetry.capture`，只借 `_platform/_arch/_distribution` 三个枚举探测函数；信里不带遥测 `install_id`。

### 七、开关

硬开关 `TAVOTTO_NO_CONTACT=1` 让入口消失（`GET /api/contact` → `{"enabled": false}`，其余 404，前端不渲染按钮与对话框、也不联网）。测试进程由 conftest 硬关，生产主机被会话级探针兜底。

## 与既有承诺的关系

| 位置 | 原承诺 | 现状 |
| --- | --- | --- |
| `docs/privacy.md` | 联网活动清单（更新、包查找、AI、统计、逐次确认的诊断包） | 加「Contacting the maker」一节与概览里的一句：发什么、发给谁、两个线路主机只转发、不自动发送、不与遥测关联、只用于回信、365 天删除、可邮件要求提前删 |
| 应用内隐私摘要 | 「发送问题反馈」一句（能力开着才出现） | 同形再加「联系作者」一句 `settings.about.privacyContact` |
| ADR 0118「诊断包不自动上传」 | 唯一例外是逐次确认的发送 | 不变；联系作者是**另一条**同样逐次确认的用户主动发信通道，附诊断包时仍走 0118 的流程 |
| 1.0 收敛纪律 | 禁止扩大产品能力 | **这是扩大**；需要维护者豁免（见页首） |
| 服务端 | — | `Tavotto/infra`：`/v1/messages/*`、新迁移、SMTP 代发、企业微信提醒、admin CLI（契约 v1，另一路实现） |

## 后果与未决

- 部署顺序：先部署服务端 `/v1/messages/*` 与 Worker，再发客户端；服务端不认的请求字段 = 422，客户端按 `client_error` 说，不崩。
- 以服务端实际实现为准：错误体顶层 `{ok:false, code, error}`；`reply_email` 只收可打印 ASCII（拒引号 / 分号 / 括号，域名含「.」）；已 complete 的信 init 重放回空票据、complete 重放回当前 state；`api.tavotto.com` 与 `tavotto.cn` 是同一后端的两个入口，回信令牌跨线路通用。
- 未验证（NOT_RUN）：真实服务器 / COS / SMTP；Worker 转发；真机 Tauri WebView 里的拖放附件与窗口拖动区点击；Windows 真机（窗口按钮让位、0600 在 Windows 只管只读位）；
  `src-tauri/src/sidecar.rs` 的一行环境变量在本机没法 `cargo check`（缺 `dist/Tavotto` 资源，build.rs 先挂），靠 `tests/test_contact_send.py` 的结构断言兜底，需要 CI 的 cargo 一轮确认。
- 图标：按图标集流程补画了 `Mail`（`defs.ts` / `index.ts` / `ICONOGRAPHY.md` 三处）。
