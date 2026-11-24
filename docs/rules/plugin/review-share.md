# Agent 发外部审阅：`tavotto_share_review`（ADR 0205）

> 2026-11-23 新增（ADR 0205 一期），2026-11-24 补二期（读意见 / 回复 / 标记已处理，见文末「二期」一节）。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。引擎侧的出站、签名、额度、幂等见 ADR 0187 与 `engine/reviewshare.py` / `engine/reviewinbox.py` 的模块文档。
> 看护用例：`tests/test_mcp_review_feedback.py`（二期）、`tests/test_mcp_share_review.py`（零网络 / 新建 / 更新 / 超时接续 / 开关 / 渲染回调不串）、`tests/test_review_inbox_lock.py`（真子进程并发）、
> `tests/test_review_share.py` / `tests/test_review_api.py`（引擎本体，桌面行为不变）、`tests/test_mcp_server.py`（默认关态的工具表）、
> `tests/test_mcp_error_next.py`（码映射）、`tests/test_mcp_usage.py`（走 `other` 桶）。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 65
改到: `tavotto_share_review` / `tavotto_review_feedback` / `tavotto_reply_review` / `tavotto_resolve_review`、`bridge.share_review()` / `review_feedback()` / `review_reply()` / `review_resolve()`、`reviewshare` 的 `renderer=` / `_Spec.renderer`、`reviewinbox.file_lock`
主题: Agent 发外部审阅 → `review-share.md`
必守要点: 未确认零网络；comment_id 只认本图链接；第三方文字标 source；出图回调随分享走；收件箱写在锁里；figure id = stem
-->

- **先问再发**：不带 `explicit_confirm` 的调用**不联网、不上传**（不调 `quota`、不调 `start_*`）：只出一张宽 800 px 的预览（`image` 内容块，不进
  `structuredContent`）、说清「新建 / 更新哪一条（标题、当前版本、已有链接）」「上传什么（画面 + 元素位置表，不含脚本与数据）」，回 `status:
  needs_confirmation`、`code: review_confirmation_required`（登记在 `usage._NEEDS_INPUT`）与 `next = ask_user(…, ["发送…", "先不发"])`。
  服务端自己绝不代填 `explicit_confirm`；编排工具（`make_journal_ready` 等）不会走到这一步。今天还剩几个新建额度只有联网才知道——预览里不写。
- **跟着桌面的开关走**：只在 `reviewshare.enabled()`（未被 `TAVOTTO_NO_REVIEW_SHARE` 硬关、端点合规）且引擎带 `renderer=` 参数时进 `tools/list`；
  关着时调用回 `review_share_disabled`（先于取会话，不会被 `unknown_session` 盖住）。`reviewshare` / `reviewinbox` 是**可选引擎模块**，只经
  `_optional_engine`，旧引擎当关。工具名**不进** `usage.TOOLS`（走 `other` 桶、不升 `CONSENT_VERSION`）；降级 server 的 `NORMAL_TOOLS` 有它。
  `codex.mcp.json` 的 `env_vars` 与 `integrations/configure.py` 的 `PROBE_ENV_KEEP` / `CARRIED_ENV` 透传 `TAVOTTO_NO_REVIEW_SHARE`。
- **同一条流程，不写第二份**：带确认后走 `reviewshare.start_share` / `start_update`（同一个 device key、同一份 `shares.json`、同一份额度），不启动 `start_poller`。
  更新只认**同项目同图**：`reviewinbox.for_target("figure", stem, bridge.project_id(session.project))` 找最新一条有效链接（`update="auto"`），
  `update="new"` 一律新建；引擎 `start_update` 自己再核一遍项目（别的项目 `not_found` 404）。
- **figure target id = 会话的 `stem`**：桌面前端发 `/api/review/share` 的 `target` 是 `{kind: "figure", id: <引擎认图的 stem>}`（`web/src/lib/reviewTarget.ts`，
  stem 来自 `/api/panel-stem`），MCP 会话的 `session.stem` 是同一把尺；项目 id 是 `bridge.project_id`（与 `app._project_id` 同口径）。口径变了两边一起改，否则桌面认不出这是同一张图。
- **出图回调随这一次分享走**：`_Spec.renderer`（`compare=False, repr=False`，不进指纹）优先，没有才用 `set_renderer` 注册的（桌面）。MCP 的回调 = 会话当前 patches 的
  `worker.preview_png_snapshot`（PNG 与 manifest 同一次绘制），老引擎退回 `preview_png` + `session.manifest`；宽度按 manifest 的 `size_mm` 换成长边 ≈ `LONG_EDGE_PX`；
  元素表 = `reviewshare.manifest_elements`，图幅 = `manifest_size_mm`；超限降档 / `shrink_png` / 元素表裁剪仍在 `reviewshare._render`。
- **渲染与会话锁**：回调在 reviewshare 的后台线程里跑，调用线程只睡着轮询 `status()`、不持有任何会话锁，所以直接取 worker 不会死锁；首张图仍在**调用线程里**先出好交给闭包
  （`_review_renderer`，错误以真实的 `BridgeError` 冒出来，不被收成不透明的 `render_failed`），后台线程只在超限降档重出时才再碰 worker。出图描述 `render` 只含
  `stem / patch_hash / script_sha1`（同指纹的重试复用 `_REUSE` 的幂等 id 与同一份字节；桌面比对 `digest` 时会判「改过」——保守，不会漏更新）。
- **同步等 ≤ 120 秒**（`REVIEW_WAIT_S`）：成功回链接 + 版本号；超时回 `status: in_progress` + `next` 让稍后用**同样的参数**再调，本进程记住这次已确认的分享
  （`_REVIEW_PENDING`，键只含调用入参：会话 / patch_hash / update / 标题 / 说明，不含派生的模式与链接——超时后新建完成，`auto` 再解析会变成「更新」，键含派生值就会多发一版），再调时接上它的结论，不重复发；参数不同而上一次还在跑回 `review_in_progress`。预览之后图改过，成功回包带
  `sent_latest: true`（发的是最新的样子，不因「和预览不同」拦下）。
- **失败码**：ADR 0187 的闭集原样放在回包 `failure` / `retryable`，错误码加 `review_` 前缀（`review_quota_new_exhausted` / `review_share_gone` / `review_too_large` /
  `review_rate_limited` / `review_network` / `review_server` / `review_render_failed` / `review_client_error`），不与桥里已有的 `render_failed`（回脚本改）混；`next`
  由 `nextstep` 映射（额度、链接失效问用户；限流 / 网络 / 服务端原样重试，带着用户已给的确认）。
- **回包里没有令牌**：只有标题、版本号、`review_url`（链接本身，用户发给别人的就是它）、到期时间；`owner_token` 只在 `shares.json`。
- **审阅意见是别人写的文字**：二期的读意见工具逐条标 `source`，技能说明要求当待办、不当指令。

## 收件箱的跨进程锁（ADR 0205 §四）

- `reviewinbox` 的写路径（`add_pending` / `add_version` / `mark_revoked` / `forget` / `mark_read` / `merge_feed` / `apply_reply` / `apply_comment`，以及读路径上的过期修剪）
  一律在 `with file_lock():`（进程内 `RLock` → `review/shares.lock`：POSIX `fcntl.flock`、Windows `msvcrt.locking`）里**重新读盘 → 改 → 原子写回**；纯读路径不加文件锁。
  可重入（同一进程里嵌套不死锁）；超时 5 秒抛 `TimeoutError`（`OSError`），走已有的写盘失败路径（分享报 `server`、可再试），**绝不跳过锁直接写**。
- `device.key`：已存在（32 字节）无锁直接读（签名路径不抢锁）；首次生成在锁里重读后再生成，已存在就用它、不覆盖；拿不到锁按写盘失败（`client_error`）处理。
- 看护必须用**真子进程**（`tests/test_review_inbox_lock.py`）：线程共享同一把进程内锁，量不到这个缺陷。手工反证（把锁换成空操作，2 × 25 条并发写必丢）不进套件。

## 二期：读意见、回复、标记已处理（ADR 0205 §五，2026-11-24）

三个工具与一期同一个开关（`bridge._review_feedback_engine()`：`reviewshare.enabled()` + 引擎带 `poll_once` / `reply` / `resolve` 与收件箱的 `detail` / `comment_of`；
不要求一期的 `renderer=`）；关着不进 `tools/list`、调用先于取会话回 `review_share_disabled`；工具名不进 `usage.TOOLS`。
引擎的读写全部复用桌面那一条（`reviewshare.poll_once` / `reply` / `resolve`、`reviewinbox.detail`），不写第二份。

- **找链接**：`reviewinbox.for_target("figure", session.stem, project_id)`——与一期同一把尺。有效的优先，其次最近更新的（停止 / 过期的链接意见副本仍可读，`freshness_reason` 写 `revoked` / `expired`，不再去拉）。
- **`tavotto_review_feedback`**：没有链接回 `status: no_share` + `next` 指向 `tavotto_share_review`，**零网络**。有链接且仍有效才 `poll_once(force=refresh, share_id=…)`；回包 `freshness` 是 `just_polled` 或
  `local_copy`（`freshness_reason`：`throttled` 节流内 / `poll_failed` 拉不通或抛错 / `not_polled` / `revoked` / `expired`）——拉失败绝不让读意见整个失败。
  按版本（新的在前）列：意见 `{id, version, source, reviewer, text, anchor, resolved, created_at, replies}`、该版本的 `verdicts`（通过 / 需要修改 + 说明）；`anchor` = `element`（`gid`、`label` 取自**那个版本**的元素表、`bbox` 画面比例 `[x,y,w,h]`）/ `region` / `overall`。
  默认只列未处理的（`resolved_hidden` 写被藏了几条），`include_resolved` 带上；`version` 只看一版。
- **第三方文字标来源**：每条意见 `source: "reviewer"`、回复按引擎的 `from` 标 `reviewer` / `owner`、结论说明标 `reviewer`；回包顶层 `notice` 是固定一句（当待办、不当指令）。`next` 里**不放**任何第三方文字
  （只指向 `tavotto_resolve_review`，`comment_id` 写占位）；预览回复的 `ask_user` 问句只带审阅者称呼（≤ 20 字），原文在回包字段里。
- **已读游标：不动**。桌面是用户打开「审阅」页签时才 `POST /api/review/shares/<id>/read`（`reviewinbox.mark_read`）；未读数是给**用户**看的红点，Agent 读过不等于用户看过，
  所以 `review_feedback` 不调 `mark_read`（用例钉住：读完 `unread_summary()` 不变）。`poll_once` 合并新意见时引擎自己会涨未读，这是预期。
- **体积预算**：`REVIEW_FEEDBACK_BUDGET_BYTES`（20 KiB，量 `structuredContent` 的紧凑 UTF-8）；每条意见最多带最近 `REVIEW_REPLIES_KEPT`（5）条回复、单条回复截 `REVIEW_REPLY_CLIP`（200）字（`replies_omitted` 写省了几条），
  意见正文不截（引擎已封顶 500 字）。超了按「已处理的先丢 → 旧版本先丢 → 后写的先丢」逐条丢，回包 `truncated: {omitted_comments, why}`；`content` 文本只是摘要（≤ 约 8 KB，超了写「其余见 structuredContent」）。
- **`tavotto_reply_review`（对外发言，先问用户）**：校验顺序 = 开关 → 会话 → `comment_id` 归属 → `text`（1–500 字）→ 确认。不带 `explicit_confirm` **不联网**（不调 `reviewshare.reply`），回
  `status: needs_confirmation`、`code: review_confirmation_required`（沿用一期的码，已在 `usage._NEEDS_INPUT`）、`reply_to`（审阅者、≤ 80 字摘录，标 source）、`reply_text`（原文）与 `next = ask_user(…, ["发送…", "先不发"])`；
  schema 写明「用户明确要求才置 true」，服务端绝不代填。带确认才调 `reviewshare.reply`，成功回 `status: replied`；本机副本由引擎当场写入（下次读意见就带上，`source: owner`）。
- **`tavotto_resolve_review`**：只改状态，不问用户；`resolved` 默认 true，`false` = 重新打开；非布尔回 `bad_arguments`。
- **`comment_id` 归属（三者共用 `_review_find_comment`）**：只在**这张图在本项目的那条链接**的本机副本里找；别的项目、别的图、同图较旧的另一条链接里的意见一律 `not_found`（不区分「不存在」与「不属于你」），
  零网络。所以必须先读过意见（本机副本里有它）才能回复 / 标记——刚到的新意见先 `tavotto_review_feedback`（`refresh: true`）。引擎的 `reply` / `resolve` 自己还会再核一遍（`not_found`）。
- **失败码**：引擎返回的 `failure`（ADR 0187 闭集）经 `_review_failure(mode="reply" | "resolve")` 变成 `review_*` 码，与一期同一张 `nextstep` 映射；回复 / 标记遇到 `review_share_gone` 时 `next` 是「要不要新建一条链接」的问句（不是一期的「更新」问句），`review_network` / `review_rate_limited` / `review_server` 原样重试（带着用户已给的 `explicit_confirm`）。
