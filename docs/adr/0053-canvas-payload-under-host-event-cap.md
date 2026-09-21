# ADR 0053：内嵌画布的负载不走工具结果——open 结果守宿主 1 MiB 事件上限，画布经 `tavotto_session_state` 取件

日期：2026-09-21 · 状态：**Accepted**（issue #457）
相关：[0006 Codex 里的 MCP server / MCP App 画布](0006-codex-mcp-app-and-publication-profile.md)
第 4 节、[0022 复杂度感知的编辑预览](0022-complexity-aware-editor-preview.md)（不变量 5：manifest 与 SVG /
位图同一次响应）。

## 问题

ADR 0006 让内嵌画布从「带出它的那次 `tavotto_open_figure` 的结果」里拿一切：host 把
`CallToolResult` 经 `ui/notifications/tool-result` 推给 iframe，`structuredContent`
里有 session_id、manifest、SVG、预检。这条路在 08-24 的真实验收里通过了——用的是一张
28 个元素的冒烟图。

2026-09-21 用户在 Codex Desktop 26.915 上报告（#457）：一张 220 × 250 mm、422 个可编辑
元素的图，`tavotto_open_figure` 返回成功、模型看到完整的 `structuredContent`、随后的
`tavotto_preflight` 也正常，右侧画布却永远停在「正在等待 tavotto_open_figure 的结果」。
关掉重开、换 `script_path` 入口都一样。

根因在宿主那一侧，而且是设计如此：codex-rs 把每个 MCP 工具结果送给桌面 UI / 写进
rollout 的**事件副本**封顶在 1 MiB（`core/src/mcp_tool_call.rs` 的
`MCP_TOOL_CALL_EVENT_RESULT_MAX_BYTES = DEFAULT_OUTPUT_BYTES_CAP`，PR #20260，
2026-04-30，动机是 rollout 里出现过几百 MB 的 MCP 结果）。序列化后超过它，那份副本的
`structuredContent` 与 `_meta` 整个置空、`content` 换成一段截断的文本预览；桌面 UI 就是
从这份副本重建 `McpToolCall` item 再喂给 iframe 的。给模型的那份不受影响。于是症状是
**分裂的**——一切都好，只有画布没有。

我们那一侧把它推过线的是两件事：manifest 本身每个元素约 2 KB（`editable` 表带着每个
属性的类型 / 范围 / 选项），422 个元素就是 880 KB；再加上 `server.py` 把整份
`structuredContent` **又复制了一份**进 `_meta.widgetData`（ChatGPT 侧一个并不需要的
约定），体积翻倍。同量级的图实测：不带画布产物时 1.33 MB，带画布产物 2.67 MB；
28 元素的冒烟图 85 KB。阈值粗算下来约 200 个元素——多面板带注释的科研图很容易撞到。

同一份源码里还量到一条与此相关的事实：codex 在 `structuredContent` 非空时**只把它序列化
给模型，`content` 文本整段丢弃**（`protocol/src/models.rs` 的
`as_function_call_output_payload`）。这一条不在本 ADR 的裁决范围内，记在这里是因为
它决定了「省略了什么」必须写进 `structuredContent` 而不是文字。

## 裁决

1. **`tavotto_open_figure` 的结果自己守预算**：`server.HOST_EVENT_RESULT_CAP_BYTES`
   （1 MiB，宿主常量的镜像）与 `CANVAS_INLINE_BUDGET_BYTES`（768 KiB，留 1/4 余量给
   序列化差异）。量的对象与宿主相同：整个 `CallToolResult`（content + structuredContent
   + `_meta`），按 serde_json 的紧凑 UTF-8 算（`_serialized_bytes`）。超预算按
   `INLINE_ELISION_STEPS` 的顺序省：svg（模型读不了）→ manifest → 位图 → 预检的
   warn / suggestion / not_verifiable 清单 → errors 清单；每省一步量一次，够了就停；
   计数与阻断布尔永远留着。省过东西就写 `structuredContent.elided`（省了什么、
   为什么、去哪儿取）并在文字里提一句。**没超预算的结果一个字段都不动**——小图走的
   还是 08-24 验收过的那条路。
2. **`_meta` 只挂资源元数据，不再复制 `widgetData`**。MCP Apps 标准路径下 iframe 拿到
   的就是整份 `CallToolResult`，ChatGPT 侧读 `window.openai.toolOutput`
   （= structuredContent）——两条路都用不着第二份。
3. **新工具 `tavotto_session_state { session_id }`**：会话此刻的完整状态（manifest /
   SVG 或位图 / 当前 patches / patch_hash / render_revision / 预检），**只读、不重渲染**
   ——全部来自会话对象上最近一次 `_render` 留下的东西，manifest 与 SVG / 位图仍是同一次
   响应（ADR 0022 不变量 5）。预检复用会话上缓存的默认结果（`Session.preflight_cache`，
   键 = patch_hash + 规范印章，任一变了就重算）。它走的是画布自己发的 `tools/call`——
   宿主直接代理（`codex_thread.call_mcp_tool` → `mcp_runtime.latest_call_tool`），
   原样返回、不进模型上下文、不受事件上限约束。模型只在 open 结果标了 `elided`
   且确实要逐元素 gid 时才需要它。
4. **画布启动按来货分三路**（`web/src/mcp/boot.ts`）：完整的 open 结果 → 直接种（零
   往返）；只有把手（`session_id` 在、不是完整 open 结果——server 省略过，或 host 用
   apply 的结果起了新 iframe）→ 经 `tavotto_session_state` 取件再种；空壳
   （`structuredContent` 为 null / 缺失 / 没有 session_id）→ **当场**显示可诊断的错误，
   把形状（structuredContent / _meta 是否为空、content 预览开头）原样摆出来。握手成功
   30 秒还没等到 tool-result 也把「还在等、可能等不来」摆出来——不是放弃，之后到的结果
   照常收。三路都**不猜、不自己发起 open**。
5. **取件回来的 `patches` 原样种进账本**（`seedEmbeddedSession` 的 `overrides`）：账本
   空着而画面是改过的，用户下一次编辑就把模型已应用的修改静默还原了。
6. **只对 open 守预算，不对 apply**：apply 的结果要原样回给画布自己发的 `tools/call`
   （那条路不截断，画布靠它拿新 manifest / SVG）。模型发起的 apply 在大图上仍会撞上限
   ——那块 iframe 会走第 4 条的「空壳」一路，当场说出口。

## 后果

* 大图的画布多一次取件往返（内存快照，实测 20 ms / 1.2 MB）；小图零变化。
* 模型在大图上拿到的 open 结果反而更可用：以前是一段被 token 预算截断的 JSON，现在是
  完整的把手 + 计数 + 阻断清单 + 一句「manifest 在哪」。
* 「画布永远等待」这一族症状从此有两个明确的出口：空壳 → 立刻报形状；没结果 → 30 秒
  后报「等不来」。之前两者都是无限等待。
* 真实 Codex Desktop 的验收（`docs/acceptance/codex-desktop-canvas.md`）加一条大图
  用例：#457 的 422 元素量级，open 后 iframe 必须从 waiting 进 ready。

## 看护

* `tests/test_mcp_server.py` 末节「issue #457」：预算低于宿主上限且留余量、量的口径、
  `_meta` 无 `widgetData`、小图原样、超预算按序省略、预检清单只在必要时才省、
  `tavotto_session_state` 不重渲染且复用缓存、apply 之后带 patches、缓存按 hash / 规范
  失效、未知会话结构化报错；`tests/test_mcp_resolver.py` 降级名单镜像真 server 工具表。
* `web/src/mcp/boot.test.ts`：三种来货的分类与解析；`web/src/mcp/session.test.ts`：
  带 patches 的 seed；`web/e2e/mcp-canvas.spec.ts`：真 iframe 里把手取件与空壳报错。
