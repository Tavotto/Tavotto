/**
 * 路径查找器（ADR 0185 §五 M2）：真的 WASM 内核（提交进仓库的 `lib/vecops/vecops.wasm`）跑在 Node 里。
 *
 * 1. 面积对得上解析解：两圆合并 / 减去 / 相交 / 排除 / 分割、方框挖圆洞；旋转算进去了；
 * 2. 落位规则：结果放在最上面那个原对象原来的位置、沿用谁的样式就继承谁的 id、原对象全删、一次 commit、撤销逐字段回去；
 * 3. 算出来是空的 / 内核加载不了：什么都不删、不进历史、说一句，按钮灰掉有原因；
 * 4. 复合路径 / 释放、偏移、轮廓化描边、反转方向；
 * 5. 内核输出钉在 `tests/golden/pathfinder_vectors.json`：重建 .wasm 后结果逐数不变（字节稳定）。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import {
  blockedReason,
  makeCompoundPath,
  offsetSelected,
  outlineSelected,
  pathfinderCommand,
  pathfinderSelected,
  releaseCompoundPath,
  reverseSelectedPaths,
  simplifySelected,
} from '@/store/pathfinderActions'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, readPath, type CanvasObject, type PathObject, type ShapeObject, type TextObject } from '@/types/document'
import { encodeSubpaths, PATHFINDER_UI_OPS, worldSubpaths } from './pathfinder'
import { loadVecops, setVecopsSource, useVecopsStore } from './vecops/kernel'

// vitest 的 cwd 是 web/
const WASM = path.resolve(process.cwd(), 'src/lib/vecops/vecops.wasm')
const GOLDEN = path.resolve(process.cwd(), '../tests/golden/pathfinder_vectors.json')
const realSource = async () => {
  const b = readFileSync(WASM)
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}

/* ------------------------------ 夹具 ------------------------------------- */

const ellipse = (id: string, cx: number, cy: number, r: number, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'ellipse',
  x: cx - r,
  y: cy - r,
  w: 2 * r,
  h: 2 * r,
  strokePt: 0.5,
  // 描边居中：转成路径后的几何就是框本身（缺省的内侧对齐会把轮廓往里缩半个线宽），面积好对解析解
  strokeAlign: 'center',
  color: '#224466',
  fill: '#ffcc00',
  ...over,
})
const rect = (id: string, x: number, y: number, w: number, h: number, over: Partial<ShapeObject> = {}): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  x,
  y,
  w,
  h,
  strokePt: 0.5,
  strokeAlign: 'center',
  color: '#000000',
  fill: '#88aaff',
  ...over,
})
const text = (id: string): TextObject => ({ id, type: 'text', text: 'x', x: 0, y: 0, w: 10, h: 5, sizePt: 8, bold: false, color: '#000', align: 'left' })

async function seed(items: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pf_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(select)
}
const objs = () => useDocumentStore.getState().doc.objects
const paths = () => objs().filter((o): o is PathObject => o.type === 'path')

/** 路径的填充面积（mm²；每段三次贝塞尔采 64 点的多边形面积，子路径按各自的方向相加后取绝对值） */
function area(o: PathObject): number {
  let total = 0
  for (const sp of readPath(o).subpaths) {
    const at = (q: [number, number]): [number, number] => [q[0] * o.w, q[1] * o.h]
    const n = sp.nodes.length
    const pts: [number, number][] = []
    for (let i = 0; i < (sp.closed ? n : n - 1); i++) {
      const a = sp.nodes[i]
      const b = sp.nodes[(i + 1) % n]
      const [p0, c1, c2, p3] = [at(a.p), at(a.out), at(b.in), at(b.p)]
      for (let k = 0; k < 64; k++) {
        const t = k / 64
        const u = 1 - t
        pts.push([
          u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p3[0],
          u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p3[1],
        ])
      }
    }
    let a = 0
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i]
      const [x2, y2] = pts[(i + 1) % pts.length]
      a += x1 * y2 - x2 * y1
    }
    total += a / 2
  }
  return Math.abs(total)
}
const lens = (r: number, d: number) => 2 * r * r * Math.acos(d / (2 * r)) - (d / 2) * Math.sqrt(4 * r * r - d * d)
const near = (a: number, b: number, rel = 0.005) => expect(Math.abs(a - b)).toBeLessThanOrEqual(rel * Math.abs(b))

beforeEach(() => setVecopsSource(realSource))
afterEach(() => setVecopsSource(null))

/* ------------------------------ 面积 ------------------------------------- */

describe('两圆：六种形状模式的面积对得上解析解', () => {
  const r = 10
  const d = 12
  const disk = Math.PI * r * r
  const ov = lens(r, d)
  const cases: [Parameters<typeof pathfinderSelected>[0], number, number][] = [
    ['unite', 1, 2 * disk - ov],
    ['minusFront', 1, disk - ov],
    ['intersect', 1, ov],
    ['exclude', 1, 2 * (disk - ov)],
    ['divide', 3, 2 * disk - ov],
    // 修边：后方被盖住的那块删掉、前方整个留着——两个对象
    ['trim', 2, 2 * disk - ov],
  ]
  for (const [op, count, want] of cases) {
    it(op, async () => {
      await seed([ellipse('back', 30, 30, r), ellipse('front', 30 + d, 30, r, { fill: '#ff0000' })], ['back', 'front'])
      await pathfinderSelected(op)
      expect(paths()).toHaveLength(count)
      near(paths().reduce((s, p) => s + area(p), 0), want)
    })
  }
})

it('方框减去里面的圆：一个对象两条子路径（外框 + 洞）', async () => {
  await seed([rect('r', 10, 10, 40, 40), ellipse('c', 30, 30, 10)], ['r', 'c'])
  await pathfinderSelected('minusFront')
  expect(paths()).toHaveLength(1)
  expect(paths()[0].subpaths).toHaveLength(2)
  near(area(paths()[0]), 1600 - Math.PI * 100)
})

it('旋转算进去了：两个转了 45° 的方框合并，结果不旋转、外框按转过的形状算', async () => {
  await seed(
    [rect('a', 10, 10, 20, 20, { rotationDeg: 45, fill: '#000' }), rect('b', 40, 10, 20, 20, { rotationDeg: 45 })],
    ['a', 'b'],
  )
  await pathfinderSelected('unite')
  const [p] = paths()
  expect(p.rotationDeg).toBeUndefined()
  // 两个菱形互不相交：合并成一个对象、两条子路径；外框宽 = 第一个中心 20 到第二个中心 50 再各加半条对角线
  expect(p.subpaths).toHaveLength(2)
  near(p.w, 30 + 20 * Math.SQRT2, 0.002)
  near(area(p), 800, 0.002)
})

/* ------------------------------ 落位 ------------------------------------- */

describe('落位：位置、id、样式、历史', () => {
  it('结果放在最上面那个原对象的位置；中间夹着的文字不动；沿用样式的那个继承 id；一次 commit，撤销逐字段回去', async () => {
    await seed(
      [ellipse('back', 30, 30, 10), text('t1'), ellipse('front', 42, 30, 10, { fill: '#ff0000' }), text('t2')],
      ['back', 'front'],
    )
    const before = JSON.parse(JSON.stringify(objs()))
    await pathfinderSelected('unite')
    // 合并沿用最前面那个：结果的 id 是 front、填充是它的红；放在 front 原来的位置（t1 之上、t2 之下）
    expect(objs().map((o) => o.id)).toEqual(['t1', 'front', 't2'])
    const p = objs()[1] as PathObject
    expect(p.type).toBe('path')
    expect(p.fill).toBe('#ff0000')
    expect(useSelectionStore.getState().ids).toEqual(['front'])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    useDocumentStore.getState().undo()
    expect(JSON.parse(JSON.stringify(objs()))).toEqual(before)
  })

  it('减去顶层沿用最后面那个的样式与 id', async () => {
    await seed([ellipse('back', 30, 30, 10, { fill: '#00ff00' }), ellipse('front', 42, 30, 10)], ['back', 'front'])
    await pathfinderSelected('minusFront')
    expect(objs().map((o) => o.id)).toEqual(['back'])
    expect((objs()[0] as PathObject).fill).toBe('#00ff00')
  })

  it('分割：每块沿用盖住它的最前面那个；同一个原对象的第二块是新 id', async () => {
    await seed([ellipse('back', 30, 30, 10, { fill: '#00ff00' }), ellipse('front', 42, 30, 10, { fill: '#ff0000' })], ['back', 'front'])
    await pathfinderSelected('divide')
    const ps = paths()
    expect(ps.map((p) => p.fill).sort()).toEqual(['#00ff00', '#ff0000', '#ff0000'])
    expect(ps.filter((p) => p.id === 'back')).toHaveLength(1)
    expect(ps.filter((p) => p.id === 'front')).toHaveLength(1)
    expect(new Set(ps.map((p) => p.id)).size).toBe(3)
  })

  it('原对象都在同一个组里：结果留在组里；不在同一个组：不进组', async () => {
    await seed([ellipse('a', 30, 30, 10, { groupId: 'g' }), ellipse('b', 42, 30, 10, { groupId: 'g' })], ['a', 'b'])
    await pathfinderSelected('unite')
    expect(paths()[0].groupId).toBe('g')
    await seed([ellipse('a', 30, 30, 10, { groupId: 'g' }), ellipse('b', 42, 30, 10)], ['a', 'b'])
    await pathfinderSelected('unite')
    expect(paths()[0].groupId).toBeUndefined()
  })

  it('不重叠的两个对象求相交：结果是空的——什么都不删、不进历史、说一句', async () => {
    await seed([ellipse('a', 20, 20, 5), ellipse('b', 60, 60, 5)], ['a', 'b'])
    const before = JSON.parse(JSON.stringify(objs()))
    await pathfinderSelected('intersect')
    expect(JSON.parse(JSON.stringify(objs()))).toEqual(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('选区里有不能参与的（文字、直线）：命令不出现', async () => {
    await seed([ellipse('a', 20, 20, 5), text('t')], ['a', 't'])
    expect(pathfinderCommand('pathfinder-unite').available()).toBe(false)
    await seed([ellipse('a', 20, 20, 5), { ...rect('l', 0, 0, 10, 0), shape: 'line' }], ['a', 'l'])
    expect(pathfinderCommand('pathfinder-unite').available()).toBe(false)
    await seed([ellipse('a', 20, 20, 5), ellipse('b', 25, 20, 5)], ['a', 'b'])
    for (const op of PATHFINDER_UI_OPS) expect(op).toBeTruthy()
    expect(pathfinderCommand('pathfinder-unite').available()).toBe(true)
  })
})

/* ------------------------------ 内核加载不了 ------------------------------ */

describe('内核加载不了：按钮灰掉有原因，点了不动', () => {
  it('内嵌画布（字节来源给 null）：状态 unavailable、原因 embedded；跑命令文档不变', async () => {
    setVecopsSource(async () => null)
    await seed([ellipse('a', 30, 30, 10), ellipse('b', 42, 30, 10)], ['a', 'b'])
    expect(await loadVecops()).toBeNull()
    expect(useVecopsStore.getState()).toMatchObject({ status: 'unavailable', reason: 'embedded' })
    expect(blockedReason(pathfinderCommand('pathfinder-unite'))).not.toBeNull()
    // 复合路径不要内核：照样能用
    expect(blockedReason(pathfinderCommand('make-compound'))).toBeNull()
    const before = JSON.parse(JSON.stringify(objs()))
    await pathfinderSelected('unite')
    expect(JSON.parse(JSON.stringify(objs()))).toEqual(before)
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })

  it('字节坏了（编译失败）：原因 failed', async () => {
    setVecopsSource(async () => new Uint8Array([0, 1, 2, 3]).buffer)
    expect(await loadVecops()).toBeNull()
    expect(useVecopsStore.getState()).toMatchObject({ status: 'unavailable', reason: 'failed' })
  })
})

/* ------------------------------ 复合路径 / 偏移 / 轮廓化 / 反转 ------------ */

describe('复合路径 ⌘8 / 释放 ⌥⇧⌘8', () => {
  it('建立：子路径并进最后面那个（样式、id），填充规则奇偶；释放：每条子路径一个对象、第一条留 id', async () => {
    await seed([rect('r', 10, 10, 40, 40, { fill: '#123456' }), text('t'), ellipse('c', 30, 30, 10)], ['r', 'c'])
    makeCompoundPath()
    expect(objs().map((o) => o.id)).toEqual(['t', 'r'])
    const p = objs()[1] as PathObject
    expect(p.fillRule).toBe('evenodd')
    expect(p.fill).toBe('#123456')
    expect(p.subpaths).toHaveLength(2)
    near(area(p), 1600 + Math.PI * 100, 0.002) // 面积函数不看填充规则：两条子路径同向相加
    useSelectionStore.getState().set(['r'])
    releaseCompoundPath()
    expect(paths()).toHaveLength(2)
    expect(paths()[0].id).toBe('r')
    expect(paths().every((q) => q.fillRule == null)).toBe(true)
    expect(useDocumentStore.getState().past).toHaveLength(2)
  })
})

it('奇偶填充的复合路径参与运算：洞照样是洞', async () => {
  await seed([rect('r', 10, 10, 40, 40), ellipse('c', 30, 30, 10), ellipse('far', 80, 30, 5)], ['r', 'c'])
  makeCompoundPath()
  useSelectionStore.getState().set(['r', 'far'])
  await pathfinderSelected('unite')
  near(area(paths()[0]), 1600 - Math.PI * 100 + Math.PI * 25, 0.005)
})

it('偏移路径：原对象不动，紧挨着在上面多一个外扩的', async () => {
  await seed([ellipse('c', 30, 30, 10), text('t')], ['c'])
  await offsetSelected(2)
  expect(objs().map((o) => o.type)).toEqual(['shape', 'path', 'text'])
  const p = objs()[1] as PathObject
  near(p.w, 24, 0.005)
  near(area(p), Math.PI * 144, 0.01)
  expect(useSelectionStore.getState().ids).toEqual([p.id])
})

it('偏移奇偶填充的复合路径：洞跟着缩小，不会被填上（Codex 线程 4614423962）', async () => {
  await seed([rect('r', 10, 10, 40, 40), rect('h', 20, 20, 20, 20)], ['r', 'h'])
  makeCompoundPath()
  useSelectionStore.getState().set(['r'])
  await offsetSelected(2)
  const o = objs().find((x) => x.id !== 'r') as PathObject
  expect(o.subpaths).toHaveLength(2)
  // 外框 40 外扩 2（圆角拐角）、洞 20 内缩 2 → 洞边长 16
  const holeW = Math.min(...o.subpaths.map((sp) => Math.max(...sp.nodes.map((n) => n.p[0])) - Math.min(...sp.nodes.map((n) => n.p[0]))))
  near(holeW * o.w, 16, 0.02)
  // 非零填充下洞要与外轮廓反向绕：净面积 = 外框（圆角拐角）− 洞，而不是两者相加
  near(area(o), 44 * 44 - (4 - Math.PI) * 4 - 16 * 16, 0.01)
})

describe('轮廓化描边', () => {
  it('没有填充：轮廓换掉原对象（同 id），填充色 = 原描边色、无描边', async () => {
    await seed([rect('r', 10, 10, 20, 10, { fill: null, strokePt: 2, color: '#aa0000', strokeAlign: undefined })], ['r'])
    await outlineSelected()
    expect(objs()).toHaveLength(1)
    const p = objs()[0] as PathObject
    expect(p).toMatchObject({ id: 'r', type: 'path', fill: '#aa0000', stroke: false })
    // 矩形描边对齐缺省内侧：描边中线在框内缩半个线宽，轮廓是一圈宽 2 pt 的框
    const sw = (2 * 25.4) / 72
    near(area(p), 20 * 10 - (20 - 2 * sw) * (10 - 2 * sw), 0.01)
  })

  it('有填充：原对象留下、关掉描边；轮廓是新对象，紧挨着在上面', async () => {
    await seed([rect('r', 10, 10, 20, 10, { strokePt: 2 })], ['r'])
    await outlineSelected()
    expect(objs()).toHaveLength(2)
    expect(objs()[0]).toMatchObject({ id: 'r', stroke: false })
    expect(objs()[1]).toMatchObject({ type: 'path', stroke: false, fill: '#000000' })
  })

  it('虚线不能轮廓化（内核不画虚线，转出来会和画面不一样）：命令不出现', async () => {
    await seed([rect('r', 10, 10, 20, 10, { dash: 'dashed' })], ['r'])
    expect(pathfinderCommand('outline-stroke').available()).toBe(false)
  })
})

describe('简化路径', () => {
  // 一个方框，每条边上多插了 4 个共线的锚点：简化后只剩 4 个角
  const dense: PathObject = {
    id: 'd',
    type: 'path',
    x: 10,
    y: 10,
    w: 40,
    h: 20,
    rotationDeg: 30,
    strokePt: 1,
    color: '#000',
    fill: '#ccc',
    subpaths: [
      {
        closed: true,
        nodes: [
          ...[0, 0.2, 0.4, 0.6, 0.8].map((t) => ({ p: [t, 0] as [number, number] })),
          ...[0, 0.2, 0.4, 0.6, 0.8].map((t) => ({ p: [1, t] as [number, number] })),
          ...[1, 0.8, 0.6, 0.4, 0.2].map((t) => ({ p: [t, 1] as [number, number] })),
          ...[1, 0.8, 0.6, 0.4, 0.2].map((t) => ({ p: [0, t] as [number, number] })),
        ],
      },
    ],
  }
  it('共线的锚点删掉，同 id、旋转与框不变，一次 commit', async () => {
    await seed([dense], ['d'])
    await simplifySelected()
    const p = objs()[0] as PathObject
    expect(p.id).toBe('d')
    expect(p.rotationDeg).toBe(30)
    expect(p.subpaths[0].nodes.length).toBe(4)
    near(p.w, 40, 1e-6)
    near(p.h, 20, 1e-6)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
  it('已经最简：不进历史、说一句', async () => {
    await seed([{ ...dense, subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }] }], ['d'])
    await simplifySelected()
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})

it('反转路径方向：锚点倒序、手柄互换，曲线逐点不变', async () => {
  const p: PathObject = {
    id: 'p',
    type: 'path',
    x: 0,
    y: 0,
    w: 10,
    h: 10,
    strokePt: 1,
    color: '#000',
    fill: null,
    headEnd: 'triangle',
    subpaths: [{ closed: false, nodes: [{ p: [0, 0], out: [0.5, 0] }, { p: [1, 1], in: [1, 0.5] }] }],
  }
  await seed([p], ['p'])
  reverseSelectedPaths()
  const q = objs()[0] as PathObject
  expect(q.subpaths[0].nodes).toEqual([{ p: [1, 1], out: [1, 0.5] }, { p: [0, 0], in: [0.5, 0] }])
  // 端型不跟着换：终点那头的箭头现在画在原来的起点上
  expect(q.headEnd).toBe('triangle')
})

/* ------------------------------ 字节稳定 ------------------------------------ */

describe('内核输出钉在金样里（重建 .wasm 后结果逐数不变）', () => {
  const golden = JSON.parse(readFileSync(GOLDEN, 'utf8')) as {
    cases: { name: string; op: number; input: number[]; output: number[] }[]
  }
  it('有金样', () => expect(golden.cases.length).toBeGreaterThanOrEqual(5))
  for (const c of golden.cases) {
    it(c.name, async () => {
      const k = (await loadVecops())!
      expect(k.pathfinder(c.op, c.input)).toEqual(c.output)
    })
  }
  it('前端编码与金样的输入同一套（两个圆角方框经 worldSubpaths → encodeSubpaths）', () => {
    const r = rect('r', 0, 0, 25.4, 25.4)
    const enc = encodeSubpaths(worldSubpaths({ ...r, type: 'path', subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }] } as unknown as PathObject))
    // 1 英寸 = 72 pt：编码是 pt
    expect(enc.slice(0, 3 + 7 * 2)).toEqual([1, 1, 4, 0, 0, 0, 0, 0, 0, 0, 72, 0, 72, 0, 72, 0, 0])
  })
})
