# 桌面感知与更新

> 原文出自 `web/AGENTS.md`「桌面感知与更新」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条更新通道（桌面归 Tauri，
  浏览器归 `/api/update/*`）。壳侧细节见 `src-tauri/AGENTS.md`。
- **查到新版怎么说出口**（2026-09-12）：启动时弹一次 `components/UpdateNoticeDialog.tsx`
  （与 `TelemetryConsentDialog` 同一层挂载，项目选择页与工作区都有）；「该不该弹、弹什么」
  只在 `lib/updateNotice.ts` 判——桌面只看 `desktopUpdate`（桌面模式 `status` 可能一直
  是 null）、pip/pipx 给「立即更新」、源码检出只把命令写在框里。**每个版本只问一次**：
  「稍后」与 × / Esc 都按版本记（localStorage `tavotto.update.dismissed`），更新的版本
  才再弹；「⋯」上的圆点不受它影响。**让位给更急的框**：遥测同意在问、`tavotto run`
  交接确认在等时不弹。框里**没有**第二套升级逻辑，三个通道的按钮全部落到 `updateStore`
  既有的 action 上，下载 / 升级中 `busy` 锁住关闭。
