/**
 * 撤销 / 重做 / 序列化往返的属性测试（fast-check，参数见 `test/prop.ts`）。
 *
 * 走**真实的 documentStore**（commit / beginTxn / txnUpdate / endTxn / undo / redo），随机生成
 * 对象集合与操作序列（增、删、平移、缩放、旋转、对齐、多选拖动事务、被丢弃的事务），断言：
 *  1. 主语 = 文档几何，时刻 = 每一步之后：撤销到第 k 步 = 当时那一步的快照；全部撤销 = 初始文档；
 *  2. 撤销再重做 = 直接做（重做回到撤销前那一刻，逐条历史）；撤销之后再提交清空 future；
 *  3. 一次多选拖动 = 一条历史；每个被选对象看得见的框 = 起点 + 累计位移，没选的不动；
 *  4. 序列化 → JSON → 反序列化 → 读档，几何逐字段不变。
 */
import fc from 'fast-check'
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { applyPatches, enablePatches, produceWithPatches } from 'immer'
import { useDocumentStore } from './documentStore'
import { resetRestoreStateForTests } from './documentRestore'
import { applyAlign, visualBounds, type AlignMode } from '@/lib/geometry'
import { compress, accumulate, type PatchPair } from '@/lib/history'
import {
  canvasToDoc,
  emptyProject,
  migrateToProject,
  type CanvasObject,
  type FigureDocument,
  type PanelObject,
  type ProjectDocument,
} from '@/types/document'
import { near, propParams } from '@/test/prop'

enablePatches()

globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch

const S = () => useDocumentStore.getState()

/* ------------------------------ 生成器 ------------------------------------ */

// 数值取 0.01 mm 的整数倍：真实输入就是这个量级（属性栏 / 拖动取整），次正规数（5e-324）
// 不是产品声称支持的输入——x + 5e-324 === x 会让「位移非零」与「文档真变了」对不上，是生成器的伪反例
const q = (min: number, max: number) => fc.integer({ min: min * 100, max: max * 100 }).map((v) => v / 100)
const mm = q(-200, 400)
const len = q(3, 200)
const rotDeg = fc.integer({ min: -1800, max: 1800 }).map((v) => v / 10)

type Kind = 'text' | 'shape' | 'arrow' | 'panel'
interface Spec {
  kind: Kind
  x: number
  y: number
  w: number
  h: number
  rot: number
  panelRot: 0 | 90 | 180 | 270
  flipH: boolean
  flipV: boolean
}
const spec: fc.Arbitrary<Spec> = fc.record({
  kind: fc.constantFrom<Kind>('text', 'shape', 'arrow', 'panel'),
  x: mm,
  y: mm,
  w: len,
  h: len,
  rot: rotDeg,
  panelRot: fc.constantFrom<0 | 90 | 180 | 270>(0, 90, 180, 270),
  flipH: fc.boolean(),
  flipV: fc.boolean(),
})

let seq = 0
function build(s: Spec): CanvasObject {
  const id = `o${++seq}`
  const base = { id, x: s.x, y: s.y, w: s.w, h: s.h }
  switch (s.kind) {
    case 'text':
      return { ...base, type: 'text', text: 't', sizePt: 9, bold: false, color: '#000', align: 'left', ...(s.rot ? { rotationDeg: s.rot } : {}) }
    case 'shape':
      return { ...base, type: 'shape', shape: 'rect', strokePt: 1, color: '#000', fill: null, ...(s.rot ? { rotationDeg: s.rot } : {}) }
    case 'arrow':
      return {
        ...base,
        type: 'arrow',
        start: { rx: 0, ry: 0.5 },
        end: { rx: 1, ry: 0.5 },
        strokePt: 1,
        color: '#000',
        head: 'end',
        ...(s.rot ? { rotationDeg: s.rot } : {}),
      }
    default:
      return {
        ...base,
        type: 'panel',
        fileId: 'f.pdf',
        fileKind: 'pdf',
        nativeW: 100,
        nativeH: 60,
        overrides: [],
        figureFrame: 1,
        ...(s.panelRot ? { rotation: s.panelRot } : {}),
        ...(s.flipH ? { flipH: true } : {}),
        ...(s.flipV ? { flipV: true } : {}),
      } as PanelObject
  }
}

type Op =
  | { t: 'add'; s: Spec }
  | { t: 'remove'; i: number }
  | { t: 'move'; i: number; dx: number; dy: number }
  | { t: 'resize'; i: number; w: number; h: number }
  | { t: 'rotate'; i: number; deg: number }
  | { t: 'align'; mode: AlignMode }
  | { t: 'drag'; sel: number[]; steps: [number, number][] }
  | { t: 'dragDiscard'; sel: number[]; steps: [number, number][] }

const step = fc.tuple(q(-60, 60), q(-60, 60))
const idx = fc.nat(50)
const op: fc.Arbitrary<Op> = fc.oneof(
  { weight: 3, arbitrary: spec.map((s): Op => ({ t: 'add', s })) },
  { weight: 1, arbitrary: idx.map((i): Op => ({ t: 'remove', i })) },
  { weight: 3, arbitrary: fc.tuple(idx, step).map(([i, [dx, dy]]): Op => ({ t: 'move', i, dx, dy })) },
  { weight: 2, arbitrary: fc.tuple(idx, len, len).map(([i, w, h]): Op => ({ t: 'resize', i, w, h })) },
  { weight: 1, arbitrary: fc.tuple(idx, rotDeg).map(([i, deg]): Op => ({ t: 'rotate', i, deg })) },
  {
    weight: 1,
    arbitrary: fc
      .constantFrom<AlignMode>('left', 'hcenter', 'right', 'top', 'vcenter', 'bottom', 'hdist', 'vdist')
      .map((mode): Op => ({ t: 'align', mode })),
  },
  {
    weight: 2,
    arbitrary: fc
      .tuple(fc.array(idx, { minLength: 1, maxLength: 4 }), fc.array(step, { minLength: 1, maxLength: 6 }))
      .map(([sel, steps]): Op => ({ t: 'drag', sel, steps })),
  },
  {
    weight: 1,
    arbitrary: fc
      .tuple(fc.array(idx, { minLength: 1, maxLength: 4 }), fc.array(step, { minLength: 1, maxLength: 4 }))
      .map(([sel, steps]): Op => ({ t: 'dragDiscard', sel, steps })),
  },
)
const ops = fc.array(op, { minLength: 1, maxLength: 30 })
const initialSpecs = fc.array(spec, { minLength: 0, maxLength: 6 })

/* ------------------------------ 执行器 ------------------------------------- */

const pick = (i: number): string | null => {
  const o = S().doc.objects
  return o.length ? o[i % o.length].id : null
}

/** 真实手势的形状（见 canvas/interactions.startMoveDrag）：起手快照 + 每帧 txnUpdate 设「起点 + 累计位移」。 */
function drag(sel: number[], steps: [number, number][], discard: boolean): void {
  const ids = [...new Set(sel.map(pick).filter((x): x is string => !!x))]
  if (!ids.length) return
  const origin = new Map(S().doc.objects.filter((o) => ids.includes(o.id)).map((o) => [o.id, { x: o.x, y: o.y }]))
  S().beginTxn(literal('drag'))
  let cx = 0
  let cy = 0
  for (const [dx, dy] of steps) {
    cx += dx
    cy += dy
    S().txnUpdate((d) => {
      for (const o of d.objects) {
        const org = origin.get(o.id)
        if (org) {
          o.x = org.x + cx
          o.y = org.y + cy
        }
      }
    })
  }
  S().endTxn(discard ? { discard: true } : undefined)
}

function run(o: Op): void {
  switch (o.t) {
    case 'add':
      S().commit(literal('add'), (d) => {
        d.objects.push(build(o.s))
      })
      return
    case 'remove': {
      const id = pick(o.i)
      if (id) S().commit(literal('rm'), (d) => void (d.objects = d.objects.filter((x) => x.id !== id)))
      return
    }
    case 'move': {
      const id = pick(o.i)
      if (id)
        S().commit(literal('mv'), (d) => {
          const t = d.objects.find((x) => x.id === id)!
          t.x += o.dx
          t.y += o.dy
        })
      return
    }
    case 'resize': {
      const id = pick(o.i)
      if (id)
        S().commit(literal('rs'), (d) => {
          const t = d.objects.find((x) => x.id === id)!
          t.w = o.w
          t.h = o.h
        })
      return
    }
    case 'rotate': {
      const id = pick(o.i)
      if (id)
        S().commit(literal('rot'), (d) => {
          const t = d.objects.find((x) => x.id === id)!
          if (t.type !== 'panel') t.rotationDeg = o.deg
        })
      return
    }
    case 'align':
      S().commit(literal('al'), (d) => {
        applyAlign(d.objects, o.mode, d.page)
      })
      return
    case 'drag':
      drag(o.sel, o.steps, false)
      return
    case 'dragDiscard':
      drag(o.sel, o.steps, true)
  }
}

const snapshot = (): FigureDocument => S().doc

async function boot(init: Spec[]): Promise<FigureDocument> {
  localStorage.clear()
  resetRestoreStateForTests()
  const pd = emptyProject()
  pd.canvases[0].objects = init.map(build)
  await S().switchDocument(pd, 'd_prop')
  return snapshot()
}

/** fast-check 的异步属性：每次运行都是一次全新启动 */
const asyncProp = <T extends unknown[]>(arbs: { [K in keyof T]: fc.Arbitrary<T[K]> }, f: (...a: T) => Promise<void>, runs: number) =>
  fc.assert(fc.asyncProperty(...(arbs as [fc.Arbitrary<unknown>]), f as never), propParams(runs))

/* ------------------------------ 性质 --------------------------------------- */

describe('历史栈（真实 documentStore）：撤销 / 重做与文档几何', () => {
  beforeEach(() => {
    seq = 0
  })

  it('逐步撤销：撤到第 k 步等于当时那一步的快照；撤完等于初始文档（主语：文档几何，每一步之后）', async () => {
    await asyncProp<[Spec[], Op[]]>([initialSpecs, ops], async (init, list) => {
      seq = 0
      const initial = await boot(init)
      const snaps: FigureDocument[] = [initial]
      for (const o of list) {
        run(o)
        // 只有真的进了历史的操作才产生新快照（无变化的提交 / 被丢弃的事务不进历史）
        if (S().past.length === snaps.length) snaps.push(snapshot())
        else expect(S().past.length).toBe(snaps.length - 1)
        expect(S().txn).toBeNull()
      }
      // 事务期间的每一帧都不丢：past 条数 = 快照数 - 1
      expect(S().past.length).toBe(snaps.length - 1)
      for (let k = snaps.length - 1; k > 0; k--) {
        expect(S().doc).toEqual(snaps[k])
        expect(S().undo()).not.toBeNull()
        expect(S().doc).toEqual(snaps[k - 1])
      }
      expect(S().doc).toEqual(initial)
      expect(S().canUndo()).toBe(false)
      expect(S().undo()).toBeNull() // 栈空：什么都不发生
      expect(S().doc).toEqual(initial)
    }, 1200)
  })

  it('撤销再重做 = 直接做：撤到任意深度再重做回来，每一步都逐字段等于当时的快照；future 条数守恒', async () => {
    await asyncProp<[Spec[], Op[], number]>([initialSpecs, ops, fc.nat(40)], async (init, list, depthRaw) => {
      seq = 0
      await boot(init)
      const snaps: FigureDocument[] = [snapshot()]
      for (const o of list) {
        run(o)
        if (S().past.length === snaps.length) snaps.push(snapshot())
      }
      const n = snaps.length - 1
      const depth = n ? depthRaw % (n + 1) : 0
      for (let i = 0; i < depth; i++) S().undo()
      expect(S().future.length).toBe(depth)
      for (let i = 0; i < depth; i++) {
        expect(S().redo()).not.toBeNull()
        expect(S().doc).toEqual(snaps[n - depth + i + 1])
      }
      expect(S().doc).toEqual(snaps[n])
      expect(S().future.length).toBe(0)
      expect(S().redo()).toBeNull()
      expect(S().doc).toEqual(snaps[n])
    }, 1200)
  })

  it('撤销之后落下新提交：future 清空、新状态 = 基于撤销后文档的直接结果；旧未来不可再重做', async () => {
    await asyncProp<[Spec[], Op[], Op]>([initialSpecs, ops, op], async (init, list, extra) => {
      seq = 0
      await boot(init)
      for (const o of list) run(o)
      if (!S().canUndo()) return
      S().undo()
      const futureBefore = S().future.length
      const pastBeforeExtra = S().past.length
      expect(futureBefore).toBe(1)
      run(extra)
      if (S().past.length > pastBeforeExtra) {
        expect(S().future.length).toBe(0)
        expect(S().redo()).toBeNull()
      } else {
        expect(S().future.length).toBe(futureBefore)
      }
    }, 960)
  })

  it('多选拖动：一次手势 = 一条历史；每个被选对象看得见的框 = 起点 + 累计位移，没选的逐字段不动；一次撤销全部回去', async () => {
    await asyncProp<[Spec[], number[], [number, number][]]>(
      [fc.array(spec, { minLength: 2, maxLength: 7 }), fc.array(idx, { minLength: 1, maxLength: 5 }), fc.array(step, { minLength: 1, maxLength: 8 })],
      async (init, sel, steps) => {
        seq = 0
        const initial = await boot(init)
        const ids = new Set(sel.map(pick).filter((x): x is string => !!x))
        let cx = 0
        let cy = 0
        for (const [dx, dy] of steps) {
          cx += dx
          cy += dy
        }
        const pastBefore = S().past.length
        drag(sel, steps, false)
        const moved = cx !== 0 || cy !== 0
        expect(S().past.length).toBe(pastBefore + (moved ? 1 : 0))
        for (const o of S().doc.objects) {
          const o0 = initial.objects.find((x) => x.id === o.id)!
          if (ids.has(o.id)) {
            const v0 = visualBounds(o0)
            const v1 = visualBounds(o)
            expect(near(v1.x, v0.x + cx, 1e-9)).toBe(true)
            expect(near(v1.y, v0.y + cy, 1e-9)).toBe(true)
            expect(near(v1.w, v0.w, 1e-9)).toBe(true)
            expect(near(v1.h, v0.h, 1e-9)).toBe(true)
          } else expect(o).toEqual(o0)
        }
        if (moved) {
          S().undo()
          expect(S().doc).toEqual(initial)
          S().redo()
          expect(near(S().doc.objects.find((x) => ids.has(x.id))!.x, initial.objects.find((x) => ids.has(x.id))!.x + cx, 1e-9)).toBe(true)
        }
      },
      1200,
    )
  })

  it('被丢弃的事务（endTxn discard）：文档回到事务开始前、历史与 future 不变', async () => {
    await asyncProp<[Spec[], Op[], number[], [number, number][]]>(
      [initialSpecs, ops, fc.array(idx, { minLength: 1, maxLength: 4 }), fc.array(step, { minLength: 1, maxLength: 5 })],
      async (init, list, sel, steps) => {
        seq = 0
        await boot(init)
        for (const o of list) run(o)
        if (S().canUndo()) S().undo()
        const doc = snapshot()
        const past = S().past.length
        const future = S().future.length
        drag(sel, steps, true)
        expect(S().doc).toEqual(doc)
        expect(S().past.length).toBe(past)
        expect(S().future.length).toBe(future)
      },
      960,
    )
  })
})

describe('lib/history 纯算法：补丁压缩与栈', () => {
  const rec = fc.record({ a: fc.integer({ min: -100, max: 100 }), b: fc.integer({ min: -100, max: 100 }), c: fc.integer({ min: -100, max: 100 }) })

  it('同一路径多次 replace 压缩后：正向 apply 得到最终值、反向 apply 回到最初值（主语：事务补丁，压缩前后效果等价）', () => {
    fc.assert(
      fc.property(rec, fc.array(fc.tuple(fc.constantFrom('a', 'b', 'c'), fc.integer({ min: -100, max: 100 })), { minLength: 1, maxLength: 20 }), (start, edits) => {
        let doc = start
        let txn: PatchPair = { label: 't', patches: [], inverse: [] }
        for (const [k, v] of edits) {
          const [next, p, inv] = produceWithPatches(doc, (d) => {
            ;(d as Record<string, number>)[k] = v
          })
          txn = accumulate(txn, p, inv)
          doc = next
        }
        const [fwd, inv] = compress(txn.patches, txn.inverse)
        expect(applyPatches(start, fwd)).toEqual(doc)
        expect(applyPatches(doc, inv)).toEqual(start)
        // 未压缩的完整序列同样成立（压缩只是瘦身）
        expect(applyPatches(start, txn.patches)).toEqual(doc)
        expect(applyPatches(doc, txn.inverse)).toEqual(start)
      }),
      propParams(2400),
    )
  })
})

/* ------------------------------ 序列化往返 --------------------------------- */

describe('序列化往返：buildProject → JSON → migrateToProject → 读档，几何不变', () => {
  const geom = (o: CanvasObject) => ({
    id: o.id,
    type: o.type,
    x: o.x,
    y: o.y,
    w: o.w,
    h: o.h,
    rotationDeg: o.rotationDeg,
    ...(o.type === 'panel' ? { rotation: o.rotation, flipH: o.flipH, flipV: o.flipV, crop: o.crop } : {}),
    v: visualBounds(o),
  })

  it('任意操作序列之后：序列化再读档，每个对象的 x/y/w/h/旋转/翻转 与看得见的框逐字段相等（主语：存盘那一刻的文档几何）', async () => {
    await asyncProp<[Spec[], Op[]]>([initialSpecs, ops], async (init, list) => {
      seq = 0
      await boot(init)
      for (const o of list) run(o)
      const before = S().doc.objects.map(geom)
      const wire = JSON.stringify(S().buildProject())
      const back = migrateToProject(JSON.parse(wire)) as ProjectDocument
      expect(back).not.toBeNull()
      const active = back.canvases.find((c) => c.id === back.activeCanvasId)!
      expect(active.objects.map(geom)).toEqual(before)
      // 再经 store 读档：热态 = 文件
      await S().switchDocument(back, 'd_prop_2')
      expect(S().doc.objects.map(geom)).toEqual(before)
      expect(S().past.length).toBe(0)
      // 二次序列化幂等
      expect(JSON.stringify(S().buildProject().canvases[0].objects)).toBe(JSON.stringify(active.objects))
    }, 960)
  })

  it('schema 2 单画布文档经 migrateToProject：几何逐字段保留（含没有 figureFrame 记号的老面板）', () => {
    fc.assert(
      fc.property(fc.array(spec, { maxLength: 8 }), (specs) => {
        seq = 0
        const objects = specs.map(build).map((o) => {
          if (o.type !== 'panel') return o
          const { figureFrame: _drop, ...rest } = o as PanelObject
          return rest as PanelObject
        })
        const legacy: FigureDocument = { schema: 2, name: 'n', page: { w: 150, h: 100 }, objects, guides: [] }
        const pd = migrateToProject(JSON.parse(JSON.stringify(legacy)))!
        expect(pd.canvases[0].objects.map(geom)).toEqual(objects.map(geom))
        // canvasToDoc 是 docToCanvas 的逆
        expect(canvasToDoc(pd.canvases[0]).objects).toBe(pd.canvases[0].objects)
      }),
      propParams(1600),
    )
  })
})
