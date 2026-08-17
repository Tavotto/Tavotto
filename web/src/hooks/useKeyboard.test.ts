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
  afterEach(() => useUiStore.getState().setStatus('')) // 顺手清掉 4.5s 的状态计时器

  it('拖动进行中根本不会调到 documentStore.undo/redo', () => {
    s().commit('改文字', (d) => {
      const o = d.objects[0]
      if (o.type === 'text') o.text = 'B'
    })

    // pointerdown：interactions.ts 先 begin('move') 再 beginTxn
    useInteractionStore.getState().begin('move')
    s().beginTxn('移动对象')
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

  it('（根因存档）绕过拦截直接 undo()：结算+撤销一次发生，之后的位移静默写入', () => {
    // 这条用例不测修复，测的是「为什么必须在按键层拦」：只要 undo() 能在
    // txn 非空时被调到，下面这条时间线就必然重演。documentStore.undo() 的语义
    // 若哪天改了（拒绝执行 / discard 当前事务），这里会红，届时按新语义重写。
    s().commit('改文字', (d) => {
      const o = d.objects[0]
      if (o.type === 'text') o.text = 'B'
    })
    s().beginTxn('移动对象')
    s().txnUpdate((d) => {
      d.objects[0].x = 2
    })

    expect(s().undo()).toBe('移动对象') // 结算成历史 + 立刻撤销，一次调用里连着发生
    expect(s().past).toHaveLength(1) // past 净变化为 0
    expect(s().txn).toBeNull()
    expect(s().doc.objects[0].x).toBe(0)

    // trackPointer 的 pointermove 还挂在 window 上：没有 txn 的 txnUpdate 直接
    // set({doc})，绕过 pushHistory
    s().txnUpdate((d) => {
      d.objects[0].x = 3
    })
    expect(s().doc.objects[0].x).toBe(3)
    expect(s().past).toHaveLength(1)
    s().endTxn() // 松手时 txn 早已是 null，空转
    expect(s().past).toHaveLength(1)

    // 再按一次 ⌘Z，撤掉的是更早、与这次拖动毫不相干的一步；3mm 残留位移撤不回来
    expect(s().undo()).toBe('改文字')
    expect(firstText()).toBe('A')
    expect(s().doc.objects[0].x).toBe(3)
  })
})
