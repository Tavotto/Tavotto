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
* 不可用的项用 `MenuItem.reason` 常驻原因，不用 tooltip（禁用项收不到指针）。
* 看护：`canvas/objectContextMenu.test.tsx` / `store/quickEditActions.test.ts` /
  `tests/test_engine_invalidate.py` / `e2e/quick-menu.spec.ts`。
