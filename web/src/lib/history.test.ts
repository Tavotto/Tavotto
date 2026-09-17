/**
 * `lib/history` 的纯算法，不起 store。行为层（事务合并成一条历史、拖动撤销回到起点、
 * 含增删不压缩、discard 路径）由 `store/documentStore.test.ts` 继续在真 store 上看护；
 * 这里量的是算法本身的四条边界：时序（反向数组末尾是最早的）、压缩只对 replace、
 * 栈上限、坏补丁不让栈与文档错位。
 */
import { enablePatches, produceWithPatches } from 'immer'
import { describe, expect, it } from 'vitest'
import { HISTORY_LIMIT, accumulate, compress, push, redoStep, rollback, undoStep } from './history'

enablePatches()

type Doc = { a: number; items: string[] }
const base: Doc = { a: 0, items: [] }

function step(doc: Doc, recipe: (d: Doc) => void) {
  const [next, patches, inverse] = produceWithPatches(doc, recipe)
  return { next, patches, inverse }
}

describe('accumulate：正向追加、反向前插', () => {
  it('三次更新后反向数组末尾是最早那条，按序全量应用回到起点', () => {
    let doc = base
    let txn = { label: 'drag', patches: [], inverse: [] } as ReturnType<typeof accumulate<string>>
    for (const v of [1, 2, 3]) {
      const s = step(doc, (d) => {
        d.a = v
      })
      txn = accumulate(txn, s.patches, s.inverse)
      doc = s.next
    }
    expect(txn.patches.map((p) => p.value)).toEqual([1, 2, 3])
    expect(txn.inverse.map((p) => p.value)).toEqual([2, 1, 0])
    expect(rollback(doc, txn)).toEqual(base)
  })
})

describe('compress：同一路径只留一条', () => {
  it('正向留最新、反向留最早（各取数组里最后出现的）', () => {
    let doc = base
    let txn = { label: 'drag', patches: [], inverse: [] } as ReturnType<typeof accumulate<string>>
    for (const v of [1, 2, 3]) {
      const s = step(doc, (d) => {
        d.a = v
      })
      txn = accumulate(txn, s.patches, s.inverse)
      doc = s.next
    }
    const [fwd, inv] = compress(txn.patches, txn.inverse)
    expect(fwd).toHaveLength(1)
    expect(inv).toHaveLength(1)
    expect(fwd[0].value).toBe(3)
    expect(inv[0].value).toBe(0) // 最早的那条：撤销要回到事务开始前，不是倒数第二帧
  })

  it('含增删（add / remove）的批次原样返回，不压缩', () => {
    const s1 = step(base, (d) => {
      d.items.push('x')
    })
    const s2 = step(s1.next, (d) => {
      d.a = 5
    })
    const txn = accumulate(accumulate({ label: 'l', patches: [], inverse: [] }, s1.patches, s1.inverse), s2.patches, s2.inverse)
    const [fwd, inv] = compress(txn.patches, txn.inverse)
    expect(fwd).toBe(txn.patches)
    expect(inv).toBe(txn.inverse)
  })
})

describe('push：上限与 future 清空', () => {
  it('超过上限丢最老的；任何新历史都清空 future', () => {
    const past = Array.from({ length: HISTORY_LIMIT }, (_, i) => i)
    const out = push(past, HISTORY_LIMIT)
    expect(out.past).toHaveLength(HISTORY_LIMIT)
    expect(out.past[0]).toBe(1)
    expect(out.past.at(-1)).toBe(HISTORY_LIMIT)
    expect(out.future).toEqual([])
  })

  it('自定义上限', () => {
    expect(push([1, 2], 3, 2).past).toEqual([2, 3])
  })
})

describe('undoStep / redoStep', () => {
  const s = step(base, (d) => {
    d.a = 7
  })
  const entry = { label: 'set', patches: s.patches, inverse: s.inverse }

  it('往返一次回到起点，栈随之搬家', () => {
    const u = undoStep(s.next, [entry], [])
    expect(u.ok).toBe(true)
    expect(u.doc).toEqual(base)
    expect(u.past).toEqual([])
    expect(u.future).toEqual([entry])
    const r = redoStep(u.doc, u.past, u.future)
    expect(r.ok).toBe(true)
    expect(r.doc).toEqual(s.next)
    expect(r.past).toEqual([entry])
    expect(r.future).toEqual([])
  })

  it('栈空：ok=false、文档与栈原样、entry 为 null', () => {
    const u = undoStep(base, [], [])
    expect(u).toMatchObject({ ok: false, doc: base, past: [], future: [], entry: null })
    const r = redoStep(base, [], [])
    expect(r).toMatchObject({ ok: false, doc: base, past: [], future: [], entry: null })
  })

  it('坏补丁：文档原样、那一条被丢弃、不进对面的栈', () => {
    const broken = {
      label: 'bad',
      patches: [{ op: 'replace' as const, path: ['nope', 'deep', 3], value: 1 }],
      inverse: [{ op: 'replace' as const, path: ['nope', 'deep', 3], value: 0 }],
    }
    const u = undoStep(base, [entry, broken], [])
    expect(u.ok).toBe(false)
    expect(u.doc).toBe(base)
    expect(u.past).toEqual([entry])
    expect(u.future).toEqual([])
    expect(u.entry).toBe(broken)
    const r = redoStep(base, [], [broken, entry])
    expect(r.ok).toBe(false)
    expect(r.doc).toBe(base)
    expect(r.past).toEqual([])
    expect(r.future).toEqual([entry])
  })
})
