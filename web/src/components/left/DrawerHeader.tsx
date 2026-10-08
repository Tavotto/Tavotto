import { useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { DrawerSlotsContext, type DrawerSlots } from './drawerSlots'

/**
 * 抽屉标题行的两个槽（2026-10-07 设计审计 §10.3「LeftPanel 增 headerActions 槽」）。
 *
 * `LeftPanel` 的标题行只有一行 36px：名字 · 计数 · **meta 槽**（问题面板的范围胶囊）……
 * **actions 槽**（「+」「刷新」「⋯」）· 钉住。抽屉自己把东西放进槽里——搜索行于是只剩搜索框，
 * 每个抽屉的「新建 / 刷新」也都落在同一个位置。
 *
 * 槽是 portal：内容仍在抽屉组件自己的 React 树里（状态、事件、store 订阅都不变），只是画在标题行上。
 * **没有 `LeftPanel` 包着时**（用例单独挂一个抽屉、或别处借用）内容就地渲染——槽是外壳的东西，
 * 不是抽屉能不能用的前提。
 */
function SlotPortal({ slot, children }: { slot: keyof DrawerSlots; children: ReactNode }) {
  const slots = useContext(DrawerSlotsContext)
  if (!slots) return <>{children}</>
  const el = slots[slot]
  // 外壳刚挂上、槽节点还没回到 state 里的那一帧：什么都不画，下一帧落进槽
  return el ? createPortal(children, el) : null
}

/** 标题行右侧（钉住钮之前）的动作：新建、刷新、⋯ */
export function DrawerHeaderActions({ children }: { children: ReactNode }) {
  return <SlotPortal slot="actions">{children}</SlotPortal>
}

/** 紧跟标题名的一小段：计数、范围胶囊 */
export function DrawerTitleMeta({ children }: { children: ReactNode }) {
  return <SlotPortal slot="meta">{children}</SlotPortal>
}
