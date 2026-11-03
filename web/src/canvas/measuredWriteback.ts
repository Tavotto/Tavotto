/**
 * 「量完写回」的历史规则（ADR 0163 竖排一节）。
 *
 * 画布文字的框有一边由渲染撑出来（横排的高、竖排的宽），`TextView` 量完要写回文档。
 * 这笔写回有三种处境，**全路径只此一套规则**：
 *
 * 1. **手势事务开着**（拖柄、拖动……）：`txnUpdate`——并进这一条事务，撤销一次连它一起退；
 * 2. **刚刚有一条历史动过这个对象**（切排向、改字、改字号 / 行距……）：`amendLast`——并进**那一条**历史。
 *    竖排的写回会挪 `x`（右边缘不动），`x` 不是量一次就能重新推出来的值——写回留在历史之外，
 *    撤销后就回不到操作前的位置（切竖排再撤销，`x` 停在量出来的那个值上）；
 * 3. **没有触发它的历史**（打开项目、字体 / 公式晚到、窗口缩放）：`silent`——纯派生值，下次量还会一样。
 *
 * 「刚刚」= 最后一条历史是 `FRESH_MS` 内才落下的，且补丁真的碰过这个对象：
 * 量到的是那一次操作的后果，而不是很久以前某条历史的。
 */
import { useDocumentStore } from '@/store/documentStore'
import type { CanvasObject } from '@/types/document'

/** 一次操作落下历史之后，渲染量到结果的最长等待（同一帧 / 布局之后；留足一帧慢的余量） */
export const FRESH_MS = 600

let seen: unknown = null
let seenAt = 0
let seenDepth = 0

function noteHistory(): unknown {
  const { past } = useDocumentStore.getState()
  const last = past.at(-1)
  if (last !== seen) {
    // 撤销让栈变浅、露出更早的一条：那不是「刚刚落下的」，不记新鲜（撤销之后的重量只能静默）
    seenAt = past.length < seenDepth ? Number.NEGATIVE_INFINITY : performance.now()
    seen = last
  }
  seenDepth = past.length
  return last
}

// 历史一落下就记时（订阅里记，不等量的人来问——来问时可能已经过了很久）
useDocumentStore.subscribe(noteHistory)

function touches(entry: { patches: { path: (string | number)[] }[] }, id: string): boolean {
  const objs = useDocumentStore.getState().doc.objects
  return entry.patches.some((p) => {
    if (p.path[0] !== 'objects') return false
    if (p.path.length === 1) return true // 整个数组被换
    const at = p.path[1]
    return typeof at === 'number' && objs[at]?.id === id
  })
}

export function writeMeasured(id: string, apply: (o: CanvasObject) => void): void {
  const store = useDocumentStore.getState()
  const recipe = (d: { objects: CanvasObject[] }) => {
    const o = d.objects.find((x) => x.id === id)
    if (o) apply(o)
  }
  if (store.txn) {
    store.txnUpdate(recipe)
    return
  }
  const last = noteHistory() as Parameters<typeof touches>[0] & Parameters<typeof store.amendLast>[0]
  if (last && performance.now() - seenAt <= FRESH_MS && touches(last, id) && store.amendLast(last, recipe)) {
    return
  }
  store.silent(recipe)
}
