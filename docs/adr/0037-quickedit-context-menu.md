# ADR 0037：画布对象的右键菜单按对象与选区给动作

状态：**Accepted**
日期：2026-09-02
相关：[0036 多选浮动 Context Bar](0036-multi-selection-context-bar.md)（排列动作、按钮表与参照的唯一出处，本菜单直接消费）、
[0027 接入就绪度](0027-panel-readiness-fact-model.md)（「为什么不能编辑？」的事实与入口）、
[0017 显示回退 ≠ 几何权威](0017-display-fallback-vs-geometry-authority.md)（「重新构建」用 `markStale` 清掉的正是那份权威）、
[0021 `tavotto run` 桌面面](0021-tavotto-run-product-contract.md)（native 会话不杀），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 右键菜单是什么 | 画布对象的右键从自制的「按钮列表」换成**真正的上下文菜单**（Radix DropdownMenu，锚在光标处的零尺寸触发器）：`role="menu"` / `menuitem`、方向键 / Home / End / 首字母跳转、子菜单、越界自动翻转、Esc、点外部关闭全由 Radix 负责。外壳是 `ui/Menu.PointMenu`，任何「贴着一个点打开的菜单」都可复用 |
| 图内元素的弹层 | **原样保留**为 `role="dialog"` 的弹层（它含文字框 / 样式条 / 缩放 / 图例位置这些**控件**，不是菜单）。只补了「恢复此元素修改（N）」——与属性页同一个 `clearOverrides`、同一条历史标签。两种外壳共用 `quickEditStore` 一个开关 |
| 右键的选择逻辑 | `ObjectView.onContextMenu`：目标已在选区里（单选或多选）→ **选区一个字不动**；不在 → 换成它 / 它所在的整组（与左键「点谁都是整组」同一条）。图内编辑态里右键了**不在选区里的**别的对象 → 与左键同一条路退出编辑态；shift 混排进选区的标注右键不退。锁定对象照旧不吃指针（从图层树解锁） |
| 菜单形态 | 五份清单，按对象与选区派生，`data-quick-menu` 标明：`panel`（可编辑面板）/ `panel-layout-only` / `text` / `mark`（箭头与形状）/ `multi`（选区 ≥ 2 且右键对象在选区里，组即多选）。顶层只放高频项，层级四项收进「排列层级 ›」子菜单（快捷键文本保留），多选的六向对齐 + 两分布收进「对齐与分布 ›」 |
| 它是不是第二套动作 | **不是。** 每一项只发意图：排列 / 成组走 `alignSelectedTo` / `groupSelected` / `ungroupSelected`（按钮表 `arrangeButtons.ts`、参照 `arrangeStore` 同源，T-91 / T-92）；复制 / 层级 / 删除走既有 action；readiness 走 `projectReadinessStore.focusPanel`；裁剪 / 适应走 `setCropTarget` / `fitPanels`。菜单里没有一行几何、没有一处 `!!script` 之外的状态判断 |
| 「重新构建」 | 新 action `rebuildPanel`：`POST /api/engine/invalidate`（与脚本文件变更**同一个** `pool.invalidate`，只让热会话过期、不起 worker）→ `renderStore.markStale([fileId])` → 按**当前 overrides** 一次 immediate 渲染。**不改源脚本、不写回、不清 override、不进历史**（文档一个字节不动）。同文件多实例共享会话：其余实例经跟踪位各自重画。后端说作废不了（native 会话是用户自己终端里的进程，不杀；内嵌画布 / playground 没有作废通道）时照常重画，但 toast **说出「源脚本没有重跑」**。渲染失败落在该变体上（画布角标 + 属性页错误块），不叠 toast |
| 「恢复图内修改」 | `resetOverridesConfirmed`：**与属性页「重置到脚本原始」同一个 `resetOverrides`**——清掉**这个面板实例**的全部 override，回到源脚本**当前**生成的状态；源脚本、原始文件、同文件的其他实例都不动；一条历史可撤销。只在 `overrides.length > 0` 时出现，出现即先问一句（`askConfirm`）。写回过的面板（override == 磁盘基线，`isJustBakedBaseline`）换一句话：「这些修改已经写回到原始文件里……原始文件保持现状」——不说的话用户会以为文件被改回去了 |
| 「为什么不能编辑？」/「连接源脚本」 | 两者都只是**入口**：打开接入中心并聚焦这张图（`focusPanel(fileId)`），选择不动、脚本不跑、不进裁剪态。出现条件与浮动栏 / 属性页同一判据：`!script && capability && status !== 'editable'`；「连接」还要 `can_probe || can_manual_link`。`capability` 缺席 = 这一轮还不知道，**什么都不说**（5b 合同）。conflict / source_missing / needs_probe / auto_linkable 统一叫「为什么不能编辑？」，措辞差异在接入中心里 |
| 批量锁定 / 隐藏 | 新 action `setObjectsLocked(ids, locked)` / `setObjectsHidden(ids, hidden)`：收**目标状态**不收 toggle，已经是那个状态的不动，**一条历史一次 commit**。混合状态菜单给两项（「锁定全部」「解锁全部」），文案带数量。锁定后选区不动（锁的是能不能挪，与单个对象的 `toggleLocked` 同一条产品语义） |
| 裁剪在旋转面板上 | 与双击面板同一条规则：不进裁剪态。菜单项 `disabled` + **常驻原因**（第二行文字，不是 tooltip——禁用项收不到指针，tooltip 也不能是唯一说明） |
| 与画布的三条相处纪律 | ① `modal={false}`：点菜单外面 = 关掉，事件照常落到画布（在另一个对象上右键直接开它的菜单）；② **键盘事件不出菜单**：Esc 在 Radix 的 document 捕获层（`onEscapeKeyDown`）就止步，根菜单与子菜单各一处；首字母跳转不切换绘制工具；③ 关闭后焦点还给打开前的元素（还活着才还），不落在零尺寸锚上 |
| 什么时候关 | Esc、点外部、选一项、目标对象消失（撤销 / 删除）、滚轮 / 平移（锚点失效了，关掉比跟随更诚实）、窗口失焦。菜单开着时 ContextBar 让位（既有判据） |
| 磁盘格式 | **不升版**，不新增文档字段。后端新增一个端点，无新错误码 |

## 1. 背景

右键菜单一直有，但它是一份写死的按钮列表：不分对象类型、不分单选多选、四个层级动作平铺、
没有子菜单、没有 disabled 原因，也到不了 Prompt 17 刚建好的排列动作与 Prompt 08 的接入状态入口。
普通用户在光标附近找不到「这张图为什么不能编辑」「按当前修改重跑一遍脚本」这类真正高频的事。

## 2. 结构

```text
web/src/components/ui/Menu.tsx
  PointMenu          贴着一个点打开的菜单外壳（零尺寸锚 + modal=false + 键盘不外泄 + 焦点归还）
  MenuSub            子菜单（SubTrigger + SubContent，越界翻转，Esc 同样止步）
  MenuItem           +reason（常驻原因）/ icon / data-* 透传
  MenuHeading        不大写的说明行（对象名 / 「已选 N 个」）
web/src/canvas/QuickEdit.tsx        开关 + 两种外壳的分发；图内元素的 dialog 弹层留在这里
web/src/canvas/ObjectContextMenu.tsx  五份清单；每一项只调 store/actions
web/src/canvas/ObjectView.tsx       右键的选择逻辑
web/src/store/actions.ts            rebuildPanel / resetOverridesConfirmed / setObjectsLocked / setObjectsHidden / triStateOf
web/src/lib/api.ts                  engineInvalidate
src/tavotto/app.py                  POST /api/engine/invalidate
```

稳定锚点（Prompt 21 的引导挂这里，别改名）：菜单根 `data-quick-menu="<形态>"` +
`data-quick-menu-count`；每一项 `data-quick-item="<键>"`（`edit-elements` / `rebuild` / `crop` /
`fit` / `reset-overrides` / `open-inspector` / `why-not-editable` / `connect-source` / `edit-text` /
`arrange` / `align-<mode>` / `group` / `ungroup` / `open-arrange` / `duplicate` / `lock` / `unlock` /
`hide` / `z-order` / `z-<move>` / `delete`）；对齐子菜单的参照行 `data-quick-arrange-ref="<ref>"`；
图内元素弹层里的「恢复此元素修改」`data-quick-item="reset-element"`。

## 3. 不做的事

* 不给右键菜单塞属性控件（字体 / 字号 / 颜色 / 线宽）：那是 ContextBar 与属性页的事。
* 不在菜单里算对齐、不另存参照、不判就绪度（三个「唯一出处」原样）。
* 不做键盘打开菜单（Shift+F10 / ContextMenu 键）：本轮只保证打开之后键盘可用。写进限制。
* 不改其它入口的裁剪规则（浮动栏 / Enter 键上旋转面板仍能进裁剪态）：那是既有的不一致，
  记进遗留表，不在本轮顺手改。
* 不动 `useKeyboard` 的全局 Esc：让它按 `defaultPrevented` 让路会改变所有 Esc 消费者的语义。

## 4. 看护

`canvas/objectContextMenu.test.tsx`（62）、`store/quickEditActions.test.ts`（18）、
`tests/test_engine_invalidate.py`（4）、既有 `canvas/hitTest.test.tsx` 的右键一条；
真浏览器 `e2e/quick-menu.spec.ts`（子菜单上的 Esc / 越界翻转 / 重新构建真跑脚本 / 多选对齐——
前两件 jsdom 量不到）。变异反证 22 条：19 红、3 存活且成因说得清（`TEST_MATRIX.md`）。
