# 画布对象的右键菜单（2026-09-02，ADR 0037）

> 原文出自 `web/AGENTS.md`「画布对象的右键菜单（2026-09-02，ADR 0037）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0037-quickedit-context-menu.md`，改动前先读。

* **两种外壳一个开关**：画布对象 → `canvas/ObjectContextMenu.tsx`（Radix 菜单，
  `ui/Menu.PointMenu` 外壳：零尺寸锚 + `modal={false}` + 键盘不外泄 + 焦点归还）；图内元素 →
  `QuickEdit.tsx` 里的 `role="dialog"` 弹层（含控件，不是菜单）。开合都在 `quickEditStore`。
* **五份清单只发意图**（`data-quick-menu` = `panel` / `panel-layout-only` / `text` / `mark` /
  `multi`）：排列 / 成组走 `alignSelectedTo` / `groupSelected` / `ungroupSelected`，readiness 走
  `projectReadinessStore.focusPanel`，类型切换走 `switchObjectKind`，其余走既有 action。
  菜单里**不许**出现几何、`!!script` 之外的状态判断、第二份按钮表或参照。
* **「更改为 ›」**（2026-09-07 修订，见 ADR 0037 末节）：`mark` 与同族 `multi` 多一个类型切换
  子菜单，子项是 `role="menuitemradio"`（当前那种带勾，多选取值不一致时一个都不勾）。
  判据与写入都不在这里——见下面「画布标注的类型切换」。
* **右键的选区规则在 `ObjectView.onContextMenu`**：已在选区里一个字不动；不在 → 换成它 / 整组，
  并与左键一样退出图内编辑态（shift 混排进来的标注除外）。
* **`rebuildPanel`** = `POST /api/engine/invalidate`（与 `panel.file_changed` 同一个
  `pool.invalidate`）→ `markStale` → immediate 渲染；不改文档、不进历史；`invalidated: false`
  （native / 内嵌画布）照常重画但 toast 说「源脚本没有重跑」。**`resetOverridesConfirmed`** 就是
  属性页的 `resetOverrides`，只多问一句（写回过的面板换一句话）。批量锁定 / 隐藏收目标状态、
  一条历史（`setObjectsLocked` / `setObjectsHidden`）。
* **Esc 要在 document 捕获层止步**（根菜单与子菜单各一个 `onEscapeKeyDown`）：真浏览器在监听器
  之间有微任务检查点，Radix 关掉菜单后 React 已把节点卸掉，冒泡层的 `onKeyDown` 跑不到；
  **jsdom 没有这个检查点，删掉捕获层守卫照样全绿**——这类判据只有真浏览器抓得到
  （`e2e/quick-menu.spec.ts`）。
* **「把这些修改用到同脚本的其他图…」**（2026-10-01，ADR 0037 末节）：`panel` 形态，只在「这张图有图内修改且有同脚本
  兄弟图」时摆（不做 disabled）；点它只调 `openSyncOverrides`，窗口是画布舞台上常驻的 `SyncOverridesHost`。
  目标图不在画布上时「同步并写回」走标准 `WriteBackDialog`（`detached`），不许在这里再长出第二条写回路径。
* 不可用的项用 `MenuItem.reason` 常驻原因，不用 tooltip（禁用项收不到指针）。
* **剪贴板组**（2026-10-07 设计审计 §10.1）：公共尾巴的第一组是「复制 / 粘贴 / 创建副本」（`data-quick-item="copy" / "paste" /
  "duplicate"`）。复制 / 粘贴调属性页按钮那一对 `copySelectedObjects` / `pasteObjects`（菜单点击是用户手势）；
  菜单没有原生 paste 事件可接，粘贴只能走异步 `readText`——WebKit（Safari / 桌面壳）不给非编辑区读、Firefox 默认没有，
  这两类引擎上**两份菜单都不提供「粘贴」**（判据只有 `lib/clipboard.canPasteFromMenu`，Codex #833），用 ⌘V；
  ⌘C / ⌘V 的主路径仍是原生剪贴板事件，这里只是同一件事的菜单入口，不长第二套剪贴板。键位一律 `lib/keymap.keyOf`。
* **空白画布的右键菜单**（2026-10-07）：`canvas/CanvasContextMenu.tsx`，同一份 `PointMenu` 外壳；`CanvasStage.onContextMenu`
  只接空白处（落在 `[data-object-id]` 上的冒泡不接——文字编辑 / 裁剪中留给浏览器自己的菜单）。只放调既有函数的入口：
  粘贴 / 全选 / 适应画布 / 标尺·网格·安全区开关 / 画布设置（`data-canvas-menu-item`），离散动作过 `runDiscreteAction`。
  「全选」可不可用与 `selectAll` 读同一个判据 `store/actions.isSelectAllTarget`（看得见且没锁）——只有隐藏 / 锁定对象时置灰（Codex #833）。
  快速编辑（`fast_edit`）里不开：这一屏只画那一张图，菜单全是版面级动作；原生菜单照样拦下，开着时切进快速编辑当场收起
  （收起不是藏起，回排版不在旧落点上重新冒出来，Codex #833）。
  看护 `canvas/canvasContextMenu.test.tsx`。
* 看护：`canvas/objectContextMenu.test.tsx` / `store/quickEditActions.test.ts` /
  `components/inspector/syncOverrides.test.tsx` / `components/inspector/writeBackRecords.test.tsx` /
  `e2e/sync-overrides.spec.ts` / `tests/test_engine_invalidate.py` / `e2e/quick-menu.spec.ts`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 「更改为」的判据在 `lib/shapeSwitch.ts`
- Esc 在 document 捕获层止步（jsdom 抓不到）
- 不可用项用 `reason` 不用 tooltip
