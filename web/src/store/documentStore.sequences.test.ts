/**
 * 撤销历史的不变式，推广成**操作序列**（审计任务书 §4.2）。
 *
 * 单条用例各摆一个场景；这里让一个带种子的随机序列把 commit / 事务 / 撤销重做 / 加画布 /
 * 切画布 交错起来跑，每一步之后量五条不变式（不是「mock 被调了」，是可观察结果）：
 *
 *   1. `canUndo / canRedo` 与栈长度一致；
 *   2. 从任何状态撤销到底 → 回到这张画布的**初始文档**；再重做到底 → 回到撤销前的文档；
 *      而且撤销前后 past / future 的长度对得上（撤销不吞条目、重做不造条目）；
 *   3. 切走再切回，这张画布的文档与两个栈原样（栈按画布隔离）；
 *   4. endTxn 之后没有开着的事务；discard 的事务把文档退回 beginTxn 之前那一份；
 *   5. 文档里对象 id 唯一。
 *
 * 失败时先做最小化（逐个去掉一步、能复现就留着去掉的），断言信息里给出种子与最小序列，
 * 复制进 `FIXED` 就成了固定回归。随机的是**合法**操作的顺序，不随机非法操作。
 */
import { literal } from '@/i18n'
import { beforeEach, describe, expect, it } from 'vitest'
import { emptyProject, type FigureDocument, type TextObject } from '@/types/document'
import { useDocumentStore } from './documentStore'

type Op =
  | { kind: 'add' }
  | { kind: 'edit' }
  | { kind: 'remove' }
  | { kind: 'begin' }
  | { kind: 'drag' }
  | { kind: 'end' }
  | { kind: 'discard' }
  | { kind: 'undo' }
  | { kind: 'redo' }
  | { kind: 'addCanvas' }
  | { kind: 'switch'; to: number }

const KINDS: Op['kind'][] = [
  'add', 'add', 'edit', 'edit', 'remove', 'begin', 'drag', 'drag', 'end', 'discard',
  'undo', 'undo', 'redo', 'addCanvas', 'switch',
]

/** mulberry32：够用、可复现、一行。 */
function prng(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function sequence(seed: number, length: number): Op[] {
  const rnd = prng(seed)
  const ops: Op[] = []
  for (let i = 0; i < length; i++) {
    const kind = KINDS[Math.floor(rnd() * KINDS.length)]
    ops.push(kind === 'switch' ? { kind, to: Math.floor(rnd() * 4) } : { kind })
  }
  return ops
}

const text = (id: string, t: string): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

/** 自动保存的槽位：PUT 一律成功、GET 一律 404（这里量的是历史，不是落盘）。 */
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  if (/\/api\/autosave\//.test(String(url)) && init?.method === 'PUT') {
    return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: 'r' }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const S = () => useDocumentStore.getState()
const snapshot = (): FigureDocument => JSON.parse(JSON.stringify(S().doc))
const same = (a: FigureDocument, b: FigureDocument) => JSON.stringify(a) === JSON.stringify(b)

/** 跑一条序列，返回 null（全部不变式成立）或第一条被违反的不变式。 */
async function run(ops: Op[]): Promise<string | null> {
  await S().switchDocument(emptyProject(), 'd_seq')
  let counter = 0
  const initial = new Map<string, FigureDocument>() // 每张画布进入时的初始文档
  const before = new Map<string, FigureDocument>() // beginTxn 之前的文档（discard 的判据）
  initial.set(S().activeCanvasId, snapshot())

  const check = (where: string): string | null => {
    const s = S()
    // 1. 栈与开关一致
    if (s.canUndo() !== s.past.length > 0 || s.canRedo() !== s.future.length > 0) {
      return `${where}: canUndo/canRedo 与栈长度不一致`
    }
    // 5. id 唯一
    const ids = s.doc.objects.map((o) => o.id)
    if (new Set(ids).size !== ids.length) return `${where}: 对象 id 重复 ${ids.join(',')}`
    return null
  }

  for (let i = 0; i < ops.length; i++) {
    const op = ops[i]
    const where = `#${i} ${op.kind}${'to' in op ? `(${op.to})` : ''}`
    const s = S()
    switch (op.kind) {
      case 'add': {
        const id = `t${counter++}`
        s.commit(literal('add'), (d) => {
          d.objects.push(text(id, id))
        })
        break
      }
      case 'edit': {
        const target = s.doc.objects[0]
        if (target) {
          s.commit(literal('edit'), (d) => {
            const o = d.objects.find((x) => x.id === target.id)
            if (o?.type === 'text') o.text += '!'
          })
        }
        break
      }
      case 'remove': {
        const target = s.doc.objects.at(-1)
        if (target) {
          s.commit(literal('remove'), (d) => {
            d.objects = d.objects.filter((x) => x.id !== target.id)
          })
        }
        break
      }
      case 'begin':
        if (!s.txn) {
          before.set(s.activeCanvasId, snapshot())
          s.beginTxn(literal('drag'))
        }
        break
      case 'drag':
        if (s.txn) {
          s.txnUpdate((d) => {
            for (const o of d.objects) o.x += 1
          })
        }
        break
      case 'end':
        if (s.txn) {
          s.endTxn()
          if (S().txn) return `${where}: endTxn 之后事务还开着`
        }
        break
      case 'discard':
        if (s.txn) {
          const expected = before.get(s.activeCanvasId)
          s.endTxn({ discard: true })
          if (S().txn) return `${where}: discard 之后事务还开着`
          if (expected && !same(S().doc, expected)) return `${where}: discard 没有退回 beginTxn 之前的文档`
        }
        break
      case 'undo':
      case 'redo': {
        // 2. 往返：先记下此刻，撤到底再重做到底要回到此刻
        if (s.txn) s.endTxn()
        const here = snapshot()
        const past = S().past.length
        const future = S().future.length
        let n = 0
        while (S().canUndo()) {
          S().undo()
          if (++n > past + 5) return `${where}: 撤销到底走不完（past=${past}，已走 ${n} 步还 canUndo）`
        }
        if (n !== past) return `${where}: 撤销到底走了 ${n} 步，past 有 ${past} 条`
        const init = initial.get(S().activeCanvasId)
        if (init && !same(S().doc, init)) return `${where}: 撤销到底没有回到这张画布的初始文档`
        let m = 0
        while (S().canRedo()) {
          S().redo()
          if (++m > past + future + 5) return `${where}: 重做到底走不完（已走 ${m} 步还 canRedo）`
        }
        if (m !== past + future) return `${where}: 重做到底走了 ${m} 步，应为 ${past + future}`
        // 回到「此刻」再真的执行这一步
        for (let k = 0; k < future; k++) S().undo()
        if (!same(S().doc, here)) return `${where}: 往返之后文档不是出发时那一份`
        if (op.kind === 'undo') S().undo()
        else S().redo()
        break
      }
      case 'addCanvas': {
        if (s.txn) s.endTxn()
        const id = s.addCanvas()
        initial.set(id, snapshot())
        break
      }
      case 'switch': {
        if (s.txn) s.endTxn()
        // zustand 的 state 对象是不可变快照：endTxn 之后要重新取，拿旧的 `s.past` 比就是拿
        // 事务落地前的栈长比落地后的——第一版就是这么给自己造了一条假红
        const cur = S()
        const ids = cur.canvases.map((c) => c.id)
        const target = ids[op.to % ids.length]
        // 3. 切走再切回原样
        const fromId = cur.activeCanvasId
        const doc = snapshot()
        const past = cur.past.length
        const future = cur.future.length
        cur.switchCanvas(target)
        S().switchCanvas(fromId)
        if (!same(S().doc, doc) || S().past.length !== past || S().future.length !== future) {
          return `${where}: 切走再切回，画布 ${fromId} 的文档或栈变了`
        }
        S().switchCanvas(target)
        break
      }
    }
    const bad = check(where)
    if (bad) return bad
  }
  return null
}

/** 逐个去掉一步、还能复现就留着去掉的——一轮不再缩短为止。 */
async function shrink(ops: Op[]): Promise<Op[]> {
  let current = ops
  let progress = true
  while (progress) {
    progress = false
    for (let i = 0; i < current.length; i++) {
      const candidate = [...current.slice(0, i), ...current.slice(i + 1)]
      if (await run(candidate)) {
        current = candidate
        progress = true
        break
      }
    }
  }
  return current
}

/** 固定回归：随机跑出来的最小失败序列贴进来（种子 + 序列），永远跟着跑。 */
const FIXED: { name: string; ops: Op[] }[] = [
  {
    name: '事务里加画布再撤销（手写起点）',
    ops: [
      { kind: 'add' }, { kind: 'begin' }, { kind: 'drag' }, { kind: 'drag' }, { kind: 'addCanvas' },
      { kind: 'add' }, { kind: 'switch', to: 0 }, { kind: 'undo' }, { kind: 'redo' }, { kind: 'discard' },
    ],
  },
]

describe('撤销历史的不变式：操作序列', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  for (const fixed of FIXED) {
    it(`固定回归：${fixed.name}`, async () => {
      expect(await run(fixed.ops)).toBeNull()
    })
  }

  it('200 条带种子的随机序列（每条 60 步），失败时给出种子与最小序列', async () => {
    for (let seed = 1; seed <= 200; seed++) {
      const ops = sequence(seed, 60)
      const bad = await run(ops)
      if (bad) {
        const minimal = await shrink(ops)
        expect.fail(
          `seed=${seed}: ${bad}\n最小序列（${minimal.length} 步，贴进 FIXED）:\n${JSON.stringify(minimal)}`,
        )
      }
    }
  })

  it('判据的前提：这套不变式真的会红——把 undo 换成不动的桩，序列当场失败', async () => {
    const real = S().undo
    useDocumentStore.setState({ undo: () => null })
    try {
      const bad = await run(sequence(7, 20))
      expect(bad).not.toBeNull()
    } finally {
      useDocumentStore.setState({ undo: real })
    }
  })
})
