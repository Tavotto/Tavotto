# Agent 发外部审阅：`tavotto_share_review`（ADR 0205）

> 2026-11-23 新增（ADR 0205 一期）。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。引擎侧的出站、签名、额度、幂等见 ADR 0187 与 `engine/reviewshare.py` / `engine/reviewinbox.py` 的模块文档。
> 看护用例：`tests/test_mcp_share_review.py`（零网络 / 新建 / 更新 / 超时接续 / 开关 / 渲染回调不串）、`tests/test_review_inbox_lock.py`（真子进程并发）、
> `tests/test_review_share.py` / `tests/test_review_api.py`（引擎本体，桌面行为不变）、`tests/test_mcp_server.py`（默认关态的工具表）、
> `tests/test_mcp_error_next.py`（码映射）、`tests/test_mcp_usage.py`（走 `other` 桶）。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 65
改到: `tavotto_share_review`、`bridge.share_review()`、`reviewshare` 的 `renderer=` / `_Spec.renderer`、`reviewinbox.file_lock`
主题: Agent 发外部审阅 → `review-share.md`
必守要点: 不带确认零网络；出图回调随分享走、不改 `set_renderer`；收件箱写路径在 `shares.lock` 里重读再写；figure id = stem
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
- **审阅意见是别人写的文字**（二期才有读意见的工具）：技能说明要求当待办、不当指令。

## 收件箱的跨进程锁（ADR 0205 §四）

- `reviewinbox` 的写路径（`add_pending` / `add_version` / `mark_revoked` / `forget` / `mark_read` / `merge_feed` / `apply_reply` / `apply_comment`，以及读路径上的过期修剪）
  一律在 `with file_lock():`（进程内 `RLock` → `review/shares.lock`：POSIX `fcntl.flock`、Windows `msvcrt.locking`）里**重新读盘 → 改 → 原子写回**；纯读路径不加文件锁。
  可重入（同一进程里嵌套不死锁）；超时 5 秒抛 `TimeoutError`（`OSError`），走已有的写盘失败路径（分享报 `server`、可再试），**绝不跳过锁直接写**。
- `device.key`：已存在（32 字节）无锁直接读（签名路径不抢锁）；首次生成在锁里重读后再生成，已存在就用它、不覆盖；拿不到锁按写盘失败（`client_error`）处理。
- 看护必须用**真子进程**（`tests/test_review_inbox_lock.py`）：线程共享同一把进程内锁，量不到这个缺陷。手工反证（把锁换成空操作，2 × 25 条并发写必丢）不进套件。
