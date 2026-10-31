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

## 第二期修订（2026-10-23）：本机留原文、回信可带 HTML 与图片

服务端 API 1.3.0（infra #32）让作者的回信可以带排版与附图。客户端相应改了三处，其余（线路、主机白名单、令牌、365 天保留、会话认证、不联网打开）不变。

1. **本机留一份用户写的原文。** `contactinbox.add()` 增存 `subject`（≤120）、`body`（≤5000）与 `attachment_names`（≤5 个、每个 ≤120 字，**只存文件名**，不存附件内容与回信邮箱）。「我的来信」把它放在每封信最上面（灰色引用块，长正文折前 4 行 +「展开」），回信在它下面。
   旧记录没有这些字段：读出来是空串 / 空表，界面不画引用块。保留、`forget`、`prune` 行为不变，原文随记录一起删。这**推翻**了第一期「不存用户写的任何东西」的承诺——`docs/privacy.md` 同步改写；理由是用户要在同一处对照「我写了什么 / 作者答了什么」，且这份数据本来就只在用户自己的数据目录里（0600，尽力而为）。
2. **回信 HTML 与附图由引擎取、本机存。** `apply_replies` 存服务端已 nh3 清洗的 `html`（≤256 KiB，超了只留纯文字 `body`）与 `assets` 元数据（≤20 张、类型 png/jpeg/gif/webp、单张 ≤10 MiB，元数据不合规的那张丢掉）。`poll_once` 取到回信后由 `contactsend._fetch_assets` 逐张下载 `GET /v1/messages/{id}/replies/{rid}/assets/{n}`：
   与问回信**同一套**线路（`CONTACT_HOSTS` 白名单、`tlstrust`、`_NoRedirect`、`User-Agent` + Bearer、API 版本头）；响应 `Content-Type` 必须等于元数据里的类型且在四种之内、`_sniff_ok` 魔数核对（与发信附件同一个函数）、字节数必须等于元数据且 ≤10 MiB，任何一项不符就丢这一张；
   落 `data_dir()/contact_assets/<message_id>/<reply_id>/<n>`（目录 0700、文件 0600、`atomicio` 原子写）；失败（含 404 / 5xx / 线路断）下一轮再试、不阻塞其它回信；每轮预算 64 MiB / 80 次请求防失控；已 `closed` 但图没下完的信只补图、不再问回信。
   `forget` / 保留期到期 / 条数上限挤掉记录时连带 `rmtree` 对应目录。路径只由 `_MESSAGE_ID` / `_REPLY_ID` / 数字下标三段正则合成。
   为此 `diagsend._send` 多一个 `limit` 参数（默认仍 64 KiB）：带 HTML 的回信列表取 8 MiB，附图取 10 MiB + 1 字节；错误体仍按默认封顶。
3. **信纸：沙盒 iframe 显示回信。** 本机端点 `GET /api/contact/replies/<mid>/<rid>/doc`（会话认证内、不联网、`no-store`）由引擎拼出完整文档：`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`、`<base target="_blank">`、一段基础样式（白底深字、图 / 表不超宽、`pre` 折行），然后是回信 HTML，其中 `asset:<n>` 换成本机文件的 `data:` URI（文件未下完的那张换成 1×1 透明 GIF，`alt` 保留）。没有 HTML 的回信 404，界面照旧显示纯文字。
   前端 `ReplyPaper` 把文档放进 `<iframe sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" srcDoc>`，白色圆角信纸（`bg-paper` + 1px `border-border`，不用阴影：常驻表面不投影，foundation 规则；两套主题同值，**深色模式也是白纸**，用户裁决）。

**取舍。**
- **无脚本 + 无网络，两道独立的墙。** `sandbox` 不含 `allow-scripts`（回信里混进脚本也不执行）；文档自带 CSP 禁一切网络（图只认 `data:`），所以即使服务端清洗有漏洞，也既跑不了脚本、也发不出请求（追踪像素 / 外链图都不会加载）——这正是图要先下载到本机再内联的原因，也是「打开回信不联网」的依据。
- **为什么要 `allow-same-origin`。** 只为父页面能读 `contentDocument.documentElement.scrollHeight` 自适应高度。没有脚本，同源在 iframe 内无从利用；CSP 禁网络；文档内容来自引擎自己拼的 `srcdoc`，不是第三方源。不用它就只能给固定高度 + 内部滚动条，一封长信读起来很差。
- **链接。** 服务器强制 `target=_blank`（我们再加 `<base target="_blank">` 兜底），`allow-popups` 让它走新窗口；桌面壳的 `on_new_window`（`src-tauri/src/main.rs`）是 WebView 级别的钩子，对任何 frame 里的 `target=_blank` 都触发，外部 http(s) / mailto 交给系统浏览器、其余一律 Deny。**这条路径只靠读代码判断，真机未验。**
- **`mailto:` 链接。** 作者回信里的签名可能带 `mailto:`；那是服务器内容，不是应用自己的链接，`test_contact_no_mailto` 扫的是应用源码，不受影响。
- **体积。** HTML ≤256 KiB / 张 ≤10 MiB / 每封 ≤20 张是契约上限的镜像，本机再封一次顶；一封信本机最坏约 200 MiB 附图，保留期内随记录删除。
- **评审后补的五个上限（PR #1344 Codex 评审）。**
  - **持久化配额**：每封来信附图落盘合计 ≤100 MiB、全部来信合计 ≤500 MiB（`contactinbox.MAX_ASSET_BYTES_PER_MESSAGE` / `_TOTAL`）。每轮 64 MiB 的下载预算每 6 小时重置，只能减慢写盘；配额按**已落盘字节**判（下载前：已落盘 + 本张元数据大小），超了不发请求，元数据标 `skipped: "quota"`（不再重试），信纸里这张是占位，`alt` 为中英双语短句。
  - **信纸聚合渲染上限**：一封信纸内联图片的原始字节合计 ≤24 MiB（`MAX_REPLY_DOC_IMAGE_BYTES`），按出现顺序内联，超出的用占位（`alt`「图片较大，未在这里显示 / Image too large to show here」）。否则 20×10 MiB 会经 base64、JSON、`srcDoc` 复制出数百 MiB 的 HTML。
  - **回信列表读取上限按最坏线缆编码推出**（服务端无分页；量的是 JSON 文本字节，不是原文字节）：`50 ×（2 ×（256 KiB HTML + 20000 字 × 4 字节）+ 1 KiB 固定开销 + 20 × 256 B 附图元数据）+ 1 MiB` ≈ 36 MB（`contactsend.REPLIES_MAX_BYTES`），多读 1 字节来识别超限。2 倍是 JSON 转义的最坏膨胀。**这个上界依赖服务端的两条保证**：每封来信最多 50 条回信（客户端 `MAX_REPLIES_PER_MESSAGE` = 50 同值，超过只存前 50 条）、回信 HTML 与正文存库前去掉除 `\n` `\t` 外的控制字符且响应 `ensure_ascii=False`（否则一个控制字符会膨胀成 6 字节、非 ASCII 字符也会）；任一条变了，这里必须同步改。超限或 JSON 不合法**不当成功**：这封信记 `poll_error: "too_large" | "bad_response"`（日志只记编号与原因），下一轮照常再试，成功即清；`public_messages` 带出该字段，界面在那封信上显示一句说明。
  - **轮次预算按实际读到的字节扣**（不论类型 / 魔数是否通过），单次读取上限 `min(10 MiB, 剩余预算) + 1`，预算耗尽即停本轮——否则白名单服务对每个小图返回 10 MiB+1 的垃圾，80 次请求都读完又丢弃，绕过 64 MiB 边界。故障转移时首选线路被换掉的 5xx 错误体同样实际读了，`_failover(…, spent)` 累计每次尝试的读取量，附图循环按累计值扣。
  - **附图目录删除失败由有界扫尾重新发现**：`rmtree` 不再 `ignore_errors`；失败只记日志（编号与异常类型），**不再记“待删队列”**（队列要截断，截断就会丢凭据；旧 JSON 里的 `pending_asset_deletes` 容忍并忽略）。磁盘本身就是凭据：每次 `_load_pruned_locked`（含 `poll_once`）列出 `contact_assets/`，名字合规却不属于现存记录的 `<mid>`、以及现存记录里已不存在的回信 `<mid>/<rid>`，每次最多尝试删 20 个（起点逐次轮转，持续失败的不会饿死其余）；名字不合规的不碰。`forget` 的返回与界面不变。

未验证（NOT_RUN）：真实服务器的附图端点与 Worker 放行；Tauri 真机里 srcdoc iframe 内 `target=_blank` 是否确实交给系统浏览器（macOS WKWebView / Windows WebView2）；真实 nh3 输出里 `asset:` 之外的写法。
