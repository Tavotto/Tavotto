# `tavotto run` 的桌面面（2026-08-28，Compatibility Bridge Session 9B；ADR 0021）

> 原文出自 `web/AGENTS.md`「`tavotto run` 的桌面面（2026-08-28，Compatibility Bridge Session 9B；ADR 0021）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

CLI 拥有用户的 Python，**桌面只是渲染面与那一道闸**。前端能提交的只有一个
不透明的 `native_id`——host / port / token / 完整命令一律由后端从那份 0600 的
descriptor 文件读。

- **交接 ID 有两条入口，两条都必须带**：首启走落地 URL 的 `?native=`（壳的
  `landing_query()`），二次交接走 `tavotto:open` 事件的 `native`。**漏掉哪
  一条，那一条上的 CLI 就一直挂到 attach 超时（300s），而两边都不报错**
  ——首启这条曾经真的漏过一轮，每条门禁都是绿的。看护
  `test_run_beta_claims.py`（跨 Rust / TS / Python 的连线）+ `main.rs` 的
  `landing_query` 四条 + `openRequest.test.ts`。
- **确认屏是闸不是提示**（`NativeConfirmDialog`）：CLI 此刻阻塞着，用户的
  Python 一行都还没跑。所以 `blockDismiss`——点外面和 Esc **不算回答**。
  展示 descriptor 里那条 invocation；参数只报**个数**（值不经过界面）。
  `□ 记住此项目和此 Python` 默认不勾；记住过的直接批准，不再问。
  **两个终端各跑一条 → 排队**，不是留一个丢一个。
- **`nativeSessionStore` 的四条纪律**（vitest 看护）：事件按 `sequence`
  判序（终态不回头是它的推论）——SSE 断线重连的补发与新事件之间没有次序
  保证，照单全收的表现是脚本已经退出了、卡片却又变回「正在运行」；项目
  代际（与 scriptRunStore / runtimeAssetStore 同一条）；每条会话上的动作
  互斥（单 reader 传输上，连点两次的第二条响应没有人等）；错误存
  **code + params**，不存成品字符串。
- **屏障处的 build 由界面显式发**，后端不在收到 barrier 事件时自己发——
  那条事件在 reader 线程里，而 build 的响应要由**同一个** reader 读回来
  （ADR 0021 §5.2，自己等自己）。一张图直接进画布、多张开
  `FigurePickerDialog`（native 只是换了个数据源）。
- **面板角标只在需要说话时说话**（`nativePanelState`）：停在屏障上 → 无；
  脚本正在跑 → 「停下来才能编辑」；出自 native 但没有活会话 → 「会话已
  结束」；停在屏障上但这张图**与文档不一致**（引擎撤掉的改动还原不回去，
  `renderStore.inconsistent`：v1 render 的 `unrestored > 0` 或被
  `native_figure_inconsistent` 拒绝时置位，文件级、不按变体，下一次渲染报 0 即解除）
  → 「有改动没能还原，重新运行原命令可继续编辑」（ADR 0080 / 0021 §9.3）。后两句都是**在用户点进图内编辑之前**说的——不说的话他撞到的是
  一条 409，而那两句话描述的是**正常状态**、不是故障。判据**按描述符里的
  asset id 认领，不按 stem 猜**（同名 stem 在两个项目里到处都是）；
  「这张图出自哪一档」来自 `/api/runtime/status` 的 `execution_profile`
  （出处是 `enginesession.profile_of`，与渲染路由同一份判据）。
  **未知不等于 native**：老后端不给这个字段时按 safe，反过来会给每个普通
  runtime 面板都挂上「会话已结束」。
- **`clear()` 不杀用户的脚本**：那些进程是他自己在终端里起的，切个项目不该
  杀掉它们。切回去时 `refresh()` 重新对账（同样按 sequence，不覆盖 SSE 已经
  送到的更新状态）。
- 看护：`nativeSessionStore.test.ts` / `NativeConfirmDialog.test.tsx` /
  `openRequest.test.ts` / `runtimeAssetStore.test.ts`。
