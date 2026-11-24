import { useArrangeStore } from './arrangeStore'
import { useSelectionStore } from './selectionStore'

/**
 * 关键对象（ADR 0185 M6，Illustrator 的 key object）：多选之后**再点一下**其中一个对象（按下松开、没拖），它就成为关键对象——
 * 选区的主选（末位）+ 排列参照切到「主选对象」，对齐、等宽等高、按间距分布都以它为准、它自己不动；再点一下关键对象取消（参照回到选区）。
 * 拖动不算点（拖的是整个选区），按着 ⇧ 的点是加选减选，也不算。状态只在会话里（`arrangeStore`），不进文档、不进历史。
 */
const CLICK_SLOP_PX = 3

export function trackKeyClick(id: string, start: { clientX: number; clientY: number }): void {
  const sel = useSelectionStore.getState()
  const wasKey = useArrangeStore.getState().alignRef === 'primary' && sel.ids.at(-1) === id
  const up = (ev: PointerEvent) => {
    window.removeEventListener('pointerup', up, true)
    if (Math.hypot(ev.clientX - start.clientX, ev.clientY - start.clientY) > CLICK_SLOP_PX) return
    const now = useSelectionStore.getState()
    if (now.ids.length < 2 || !now.ids.includes(id)) return
    if (wasKey) {
      useArrangeStore.getState().setAlignRef('selection')
      return
    }
    if (now.ids.at(-1) !== id) now.set([...now.ids.filter((x) => x !== id), id])
    useArrangeStore.getState().setAlignRef('primary')
  }
  window.addEventListener('pointerup', up, true)
}

/** 现在有没有关键对象（多选、参照是主选对象）；有就是那个 id */
export function keyObjectId(): string | null {
  const ids = useSelectionStore.getState().ids
  return ids.length > 1 && useArrangeStore.getState().alignRef === 'primary' ? (ids.at(-1) ?? null) : null
}
