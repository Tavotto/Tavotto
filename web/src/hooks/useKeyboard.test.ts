import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { runUndoRedo, undoRedoBlocked } from './useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore, type DragKind } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { canvasToDoc, emptyProject } from '@/types/document'
import type { TextObject } from '@/types/document'

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const s = () => useDocumentStore.getState()

/** 画布上第一个对象的文本（不是文字对象就给 null） */
const firstText = () => {
  const o = s().doc.objects[0]
  return o.type === 'text' ? o.text : null
}

/** 一个对象在原点、无历史、无进行中拖动的干净画布 */
const reset = () => {
  useInteractionStore.getState().end()
  useDocumentStore.setState({
    doc: { ...canvasToDoc(emptyProject().canvases[0]), objects: [text('t1', 'A')] },
    past: [],
    future: [],
    txn: null,
  })
}

/** 把 store 上的 undo/redo 换成间谍（zustand 的方法就在 state 上，getState() 会取到新的） */
function spyOnHistory() {
  const real = { undo: s().undo, redo: s().redo }
  const undo = vi.fn(real.undo)
  const redo = vi.fn(real.redo)
  useDocumentStore.setState({ undo, redo })
  return { undo, redo, restore: () => useDocumentStore.setState(real) }
}

describe('undoRedoBlocked', () => {
  beforeEach(reset)

  it('没有拖动时不拦截', () => {
    expect(undoRedoBlocked()).toBe(false)
  })

  it('任何一种拖动进行中都拦截，松手后恢复', () => {
    const kinds: DragKind[] = [
      'move', 'resize', 'marquee', 'pan', 'guide', 'draw', 'crop', 'endpoint', 'element',
    ]
    for (const kind of kinds) {
      useInteractionStore.getState().begin(kind)
      expect(undoRedoBlocked(), kind).toBe(true)
      useInteractionStore.getState().end()
      expect(undoRedoBlocked(), kind).toBe(false)
    }
  })
})

describe('拖动中途按 ⌘Z', () => {
  beforeEach(reset)
  afterEach(() => useUiStore.getState().setStatus(null)) // 顺手清掉 4.5s 的状态计时器

  it('拖动进行中根本不会调到 documentStore.undo/redo', () => {
    s().commit(literal('改文字'), (d) => {
      const o = d.objects[0]
      if (o.type === 'text') o.text = 'B'
    })

    // pointerdown：interactions.ts 先 begin('move') 再 beginTxn
    useInteractionStore.getState().begin('move')
    s().beginTxn(literal('移动对象'))
    s().txnUpdate((d) => {
      d.objects[0].x = 2
    })

    const spies = spyOnHistory()
    runUndoRedo(false)
    runUndoRedo(true)
    expect(spies.undo).not.toHaveBeenCalled()
    expect(spies.redo).not.toHaveBeenCalled()
    spies.restore()

    // 事务原封不动，拖动照常继续
    expect(s().txn?.label).toBe('移动对象')
    expect(s().past).toHaveLength(1)
    expect(s().doc.objects[0].x).toBe(2)

    // pointermove 继续 → pointerup 收尾：整次拖动仍折叠成一条可撤销的历史
    s().txnUpdate((d) => {
      d.objects[0].x = 3
    })
    useInteractionStore.getState().end()
    s().endTxn()
    expect(s().past).toHaveLength(2)
    expect(s().past.at(-1)?.label).toBe('移动对象')

    // 松手后再按 ⌘Z，撤的就是这次拖动本身，不是更早那条
    runUndoRedo(false)
    expect(s().past).toHaveLength(1)
    expect(s().past.at(-1)?.label).toBe('改文字')
    // 整段拖动一次退回，中途那次 ⌘Z 没留下残留位移。
    // 「回到 0 而不是倒数第二步」由 documentStore.compress() 的反向补丁方向保证——
    // 这里红了先看那儿，不是本文件的拦截逻辑坏了
    expect(s().doc.objects[0].x).toBe(0)
  })

  it('绕过拦截直接 undo()：事务被结算+撤销，此后无事务的 txnUpdate 一律落空', () => {
    // 按键层的拦截（undoRedoBlocked）仍是第一道防线，但它拦不住桌面菜单
    // 加速键这类入口。第二道防线在 documentStore.txnUpdate：没有进行中的
    // 事务就丢弃更新——丢一帧拖动无害，绕过历史写文档是数据损坏
    // （真实撞见过：成组文字回到原位、图片停在新位、撤销无能为力）。
    s().commit(literal('改文字'), (d) => {
      const o = d.objects[0]
      if (o.type === 'text') o.text = 'B'
    })
    s().beginTxn(literal('移动对象'))
    s().txnUpdate((d) => {
      d.objects[0].x = 2
    })

    expect(s().undo()).toBe('移动对象') // 结算成历史 + 立刻撤销，一次调用里连着发生
    expect(s().past).toHaveLength(1) // past 净变化为 0
    expect(s().txn).toBeNull()
    expect(s().doc.objects[0].x).toBe(0)

    // trackPointer 的 pointermove 还挂在 window 上：无事务的 txnUpdate 必须落空，
    // 绝不能把位移静默写进文档
    s().txnUpdate((d) => {
      d.objects[0].x = 3
    })
    expect(s().doc.objects[0].x).toBe(0)
    expect(s().past).toHaveLength(1)
    s().endTxn() // 松手时 txn 早已是 null，空转
    expect(s().past).toHaveLength(1)

    // 再按 ⌘Z 撤的是「改文字」，没有任何残留位移
    expect(s().undo()).toBe('改文字')
    expect(firstText()).toBe('A')
    expect(s().doc.objects[0].x).toBe(0)
  })
})
