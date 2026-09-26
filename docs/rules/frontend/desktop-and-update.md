# 桌面感知与更新

> 原文出自 `web/AGENTS.md`「桌面感知与更新」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- **菜单转发只有一个分派点 `hooks/menuActions.ts` 的 `runMenuAction()`**（2026-09-26）：
  每一条都落到键盘 / 顶栏 / 命令面板 / 属性页已经在调的那个函数上，不新增能力。
  菜单加速键可能先于 keydown 截获按键（Windows 一定如此），所以挂了加速键的几条与
  `useKeyboard` 问**同一个**让位判据 `yieldsCanvasShortcuts()`——焦点在输入框 / 对话框里
  时 ⌘D / ⌘0 / ⌘1 / ⌘± 什么都不做，⌘S 照存。不带修饰键的前端键位（Delete、?、工具字母）
  **不挂**菜单加速键：挂上等于输入框里打不了那个字。看护 `hooks/menuActions.test.tsx`
  （逐键比「按键」与「菜单转发」的效果，画布那一格必须真的有效果）。
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

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 每个版本只问一次、让位给更急的框
