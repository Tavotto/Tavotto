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

## 修订（2026-10-13，诊断包扩充）

维护者裁决：诊断包以「能帮维护者详细定位问题」为准，**不再因为报错或文件里可能带着用户文字就丢掉**；发送前用户在确认框里能看到包里有哪些文件、能把同一份 ZIP 存到本地，逐次确认是最后一道关。本节取代上文「包的内容与脱敏只此一份」「不含报错原文 / 脚本源码」的说法——**仅对发送包**。

- **两种包。** 导出到文件（`GET/POST /api/diagnostics/bundle`、「复制诊断」）保持严格脱敏，一个字节不变——那份可能被贴进公开 issue。发送包（`POST /api/diagnostics/send/prepare`）在其上多一批扁平条目 `detail-…` / `optional-…`（`engine/diagdetail.py` 组装，`app._build_diagnostics_bundle(detail=…)` 才传），老三件的名字与语义不动。bundle schema 升到 **8**（manifest 加 `contains_detail` / `detail_files` / `detail_options` / `truncated`）。
- **默认收十二类**（优先级即编号）：报错原文与完整调用栈、app / sidecar / worker 日志尾巴原文、会话状态文件、最近后端请求环（方法 / 路由规则 / 状态码 / 耗时 / 错误码）、样式改动、渲染环境（一次 ≤10 s 的只读子进程）、运行记录、桌面壳与安装信息、存储坑、前端 console 环、显示环境、网络概况。
- **三个勾选项**（默认不勾、不记忆，变化即重新备包）：A 脚本源码、B 系统崩溃报告、C 会话损坏副本内容。B 勾选时 **Windows 崩溃转储（.dmp，CrashDumps 与 WER ReportArchive / ReportQueue，属于 Tavotto / sidecar / 渲染 python 进程、7 天内）原样收**（用户裁决）：那是程序内存快照，可能含正在打开的文件内容与路径，**无法脱敏**，确认框与隐私声明都写明；按文件整份取舍、新的优先、不截半个文件，放不下的记进索引与 manifest `truncated`；没勾时零字节。没勾 A 时包里一个字节源码都没有（连日志里 traceback 的源码行也剥掉）。**契约措辞：默认收集的内容（日志、console、state、overrides 等）按秘密形状矩阵严格清理；勾选的源码与崩溃报告是用户逐次确认的原样附件，清理是尽力而为、不作保证**（任意源码里的密钥无法靠规则穷尽；确认框说明为「脚本会原样附上，我们会尽量抹掉能认出的密钥，但不能保证；脚本里有密钥请不要勾」）。
- **始终处理**：家目录 → `~`、用户名 → `<user>`、秘密去掉；项目文件夹名、文件名、图里的文字保留。
- **体积**：上限由 10 MiB 改为 **64 MiB**（2026-10-13 起单份 64 MiB、每日 8 GiB，服务端已部署，infra#25；`MAX_BYTES` 与各类份额相应放大，截断预算总额 63 MiB）；按优先级从低往高截断（C < B < A，之后默认项从末尾几类），写进 manifest `truncated`，核心文件不截，截后仍超限走 `too_large`。
- **名字解析。** 网络概况对 `tavotto.cn` / `github.com` 各做一次系统名字解析（不建立连接，线程里做、≤2 s，超时记 unknown），这是 prepare「零网络」里唯一的 DNS 查询；用来判断 TUN 把域名改写进 `198.18.0.0/15`。
- **ZIP 形状（服务端（infra 诊断服务）对上传 ZIP 的检查：条目名匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`（无目录 / 斜杠）、整包 ≤ 32 条、单条目 `file_size > 1024 且 > 200 × compress_size` 被拒、单条目解压后 ≤ 128 MiB、总解压后 ≤ 256 MiB、`manifest.json` ≤ 64 KiB）。** 所以条目名一律扁平（`detail-errors.json`、`detail-app-log.txt`……），多文件的类别（会话状态、样式改动、源码、崩溃报告、损坏副本）各打成**一个内层 zip**（内层可有目录与 `index.json`），整包最大配置约 25 条；出包后 `ratio_safe` 把压缩比超限的条目改存 STORED，再用 `diagsend.zip_problem`（同一组判据）自检，不合规当内部错误 `bundle_invalid`、不发送。`bundle_schema` 取值范围 1–99，8 无问题。
- **已知局限。** 默认收集的自由文本只按「秘密词 + 分隔符」「PEM / JWT / 长十六进制 / 服务商令牌前缀」等形状清理（fail-closed：命中后从值起点抹到行尾，不确定抹到输入末尾），日志里没有标注成密钥的口令无法自动识别；缓解是逐次确认、确认框可展开看清单、可先保存同一份 ZIP 检查。逐条局限与取舍见 `docs/rules/backend/diagnostics.md`。

## 补充（2026-10-14）：意外错误入口

目的：用户遇到「界面出错」时不必自己想到去设置页找入口——自动提示可以出现，**上传仍只在确认框里点「发送」**。仅在「发送问题反馈」开着（`diagSendStore.capability !== null`）时才有任何新界面；关着时与改动前逐元素一致，/try 不受影响。

- **来源只有四个，汇聚点只有一个**（`lib/unexpectedErrors.ts`）：window `error` / `unhandledrejection`（订阅 `diagnostics/detail.ts` 已有的那一份监听，不另装）、ErrorBoundary 的 `componentDidCatch`、`lib/api.ts` `jsonFetch` 里 `status >= 500 && code === 'internal_error'`（只认兜底码，具体业务码各有各的卡）。
- **诊断环事件 `ui.unexpected_error`**（进 `frontend_state` 同源的 trace，导出路径也带）：字段闭集 `source`（`boundary` / `window_error` / `unhandled_rejection` / `engine_500`）、`error_name`（内建错误名，其余 `other`）、`message_hash`（`err:` 前缀的 `diagnosticHash`）。**导出路径的老隐私规则不变：trace 里没有 message / stack 原文。** 原文与调用栈（含 ErrorBoundary 的 componentStack、引擎 500 的 `engine internal_error <方法> <路由>`）进 #1003 的未捕获错误环，只随发送包的 `detail-errors.json` 走（用户裁决：发送包要原文）。不升 bundle schema——只是新增一种事件类型。
- **分类靠结构，不靠关键词猜严重度**：`ApiError` 与一切业务自己的错误类（原型不是内建错误原型的，如 `EngineError` / `PendingConflict` / `ArtifactValidationError`）、AbortError、`ResizeObserver loop…`、跨域 `Script error.`、pyodide / web worker 的 onerror、非 Error 的拒绝（取消令牌）一律忽略（连 trace 也不记）。用户脚本运行错误、缺依赖、业务校验失败、取消本来就是结构化结果，不经这里。
- **去重与风暴上界**（常量 `MAX_FINGERPRINTS = 64` / `MAX_PROMPTS_PER_SESSION = 5`）：指纹 = `source + error_name + message_hash`（引擎 500 用 `source + 方法 + 路由规则`，路由里非纯小写词的段规整成 `:id`）；同一指纹每会话最多提示一次，关掉后不再提示；去重集合 LRU；同时最多 1 张卡在轨上；总数超限只记环不弹。
- **防递归**：诊断 / 发送流程自身抛的错（发送对话框开着、或栈指回 `diagnostics/` / `DiagnosticsSendDialog` / `diagSend`）照记 trace 与环，**不触发提示**。
- **卡片**在通知轨（`NotificationRail`，绝不进 `BannerStack`）：error 语气、一句固定的话 + 主动作「发送问题反馈」+ ×，不带错误原文；点主动作只是 `diagSendStore.setOpen(true)`（打开 = 本机备包，沿用既有确认框，不新增 prepare / start 以外的路径）。最多两条的仲裁：低于「刚为编辑加入」、高于全部提示类（已改用环境 / 已用上次的答案 / 操作提示）。
- **崩溃页**：主按钮仍是「重新加载」；「发送问题反馈」放进折叠的「详情」（secondary）。App 树已崩，崩溃页自己渲染一份 `DiagnosticsSendHost`（外套 `TooltipProvider` 与内层 `SendHostBoundary`——对话框出错时退回空，崩溃页不二次崩）。这是对「崩溃页只依赖最朴素原语」的**有意放宽，范围仅限 `diagSendStore` 与发送对话框**。ErrorBoundary 来源不登记通知轨卡片（轨已随 App 树消失），只记 trace 与环。
