/**
 * 路径锚点编辑的对拍样本（ADR 0212 第 4 期）：前端**真的 store 动作**（`store/pathActions` 的加 / 删 / 转换 / 剪断 / 连接 / 平均，
 * `store/pathfinderActions` 的反转 / 建立与释放复合路径；平移锚点与拖手柄是画布手势，没有 store 动作，走 `applyPathEdit` +
 * `pathEdit.moveAnchors / moveHandle` 同一条链）对一组固定的路径对象跑一遍，把改后的**文档形态**对象表写进
 * `tests/golden/path_edit_vectors.json`；后端 `tests/test_pathedit_golden.py` 用同一份输入、同一条补丁（Python 写法）跑
 * `engine/vectoredit.apply_ops`，逐数比（容差 1e-6；Python 保留 6 位小数、前端不取整）。
 *
 * 新长出来的对象 id 在前端是随机的：按在数组里出现的先后归一成 `NEW1`、`NEW2`…（两侧同一个规矩）。
 * 平时这里核「现在算出来的 = 金样」（改了前端几何就红）；`UPDATE_GOLDEN=1` 时重写金样。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import {
  addAnchorPoints,
  applyPathEdit,
  averageAnchorPoints,
  convertAnchorPoints,
  cutPathAtAnchors,
  deleteAnchorPoints,
  joinPaths,
} from '@/store/pathActions'
import { usePathEditStore } from '@/store/pathEditStore'
import { makeCompoundPath, releaseCompoundPath, reverseSelectedPaths } from '@/store/pathfinderActions'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PathObject } from '@/types/document'
import { insertAnchor, moveAnchors, moveHandle, unrotateDelta, unrotatePoint, type AnchorRef } from './pathEdit'

const GOLDEN = path.resolve(process.cwd(), '../tests/golden/path_edit_vectors.json')

type Ref = { sp: number; i: number }
interface Op {
  op: string
  id?: string
  ids?: string[]
  anchors?: Ref[]
  anchor?: Ref
  by_mm?: [number, number]
  which?: 'in' | 'out'
  to_mm?: [number, number]
  break_smooth?: boolean
  at?: { sp: number; seg: number; t: number }
  to?: 'smooth' | 'corner'
}

const P = (over: Partial<PathObject> & Pick<PathObject, 'id' | 'subpaths'>): PathObject => ({
  type: 'path', x: 10, y: 12, w: 40, h: 20, strokePt: 1.5, color: '#1b3a4b', fill: null, ...over,
})
/** 曲线开放路径：四个锚点，两个平滑 */
const curve = (id = 'curve', over: Partial<PathObject> = {}) =>
  P({
    id, name: '曲线',
    subpaths: [{ closed: false, nodes: [
      { p: [0, 1], out: [0.15, 0.2] },
      { p: [0.35, 0.3], in: [0.25, 0.3], out: [0.45, 0.3], smooth: true },
      { p: [0.7, 0.8], in: [0.6, 0.9], out: [0.8, 0.7], smooth: true },
      { p: [1, 0], in: [0.92, 0.1] },
    ] }],
    ...over,
  })
/** 闭合五个锚点（曲线 + 尖角），旋转 30° */
const blob = (id = 'blob', over: Partial<PathObject> = {}) =>
  P({
    id, x: 20, y: 15, w: 36, h: 24, rotationDeg: 30, fill: '#ffcc00', fillOpacity: 0.8,
    subpaths: [{ closed: true, nodes: [
      { p: [0.5, 0], in: [0.3, 0], out: [0.7, 0], smooth: true },
      { p: [1, 0.4], in: [1, 0.2], out: [1, 0.6], smooth: true },
      { p: [0.8, 1] },
      { p: [0.2, 1], out: [0.1, 1], in: [0.3, 1] },
      { p: [0, 0.4], in: [0, 0.6], out: [0, 0.2], smooth: true },
    ] }],
    ...over,
  })
/** 两条子路径：一个闭合方块 + 一条开放折线，旋转 -20° */
const multi = (id = 'multi', over: Partial<PathObject> = {}) =>
  P({
    id, x: 5, y: 40, w: 50, h: 30, rotationDeg: -20, headEnd: 'triangle',
    subpaths: [
      { closed: true, nodes: [{ p: [0, 0] }, { p: [0.4, 0] }, { p: [0.4, 0.5] }, { p: [0, 0.5] }] },
      { closed: false, nodes: [{ p: [0.6, 0.1] }, { p: [1, 0.5] }, { p: [0.7, 1] }] },
    ],
    ...over,
  })
/** 两条开放子路径（连接用），旋转 10° */
const twoOpen = (id = 'twoOpen') =>
  P({
    id, x: 8, y: 8, w: 30, h: 20, rotationDeg: 10,
    subpaths: [
      { closed: false, nodes: [{ p: [0, 0] }, { p: [0.3, 0.2], out: [0.4, 0.3], in: [0.2, 0.1] }, { p: [0.5, 0.5] }] },
      { closed: false, nodes: [{ p: [0.6, 0.6], out: [0.7, 0.6] }, { p: [1, 1] }] },
    ],
  })
/** 首尾重合的开放路径（闭合时合成一个锚点） */
const loopOpen = (id = 'loopOpen') =>
  P({
    id, x: 30, y: 6, w: 20, h: 20,
    subpaths: [{ closed: false, nodes: [{ p: [0, 0], out: [0.2, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }, { p: [0, 0], in: [0, 0.2] }] }],
  })
/** 带渐变的闭合路径，旋转 45°（改锚点改了框，渐变要跟着换算） */
const grad = (id = 'grad') =>
  P({
    id, x: 12, y: 10, w: 30, h: 16, rotationDeg: 45, fill: '#336699',
    fillGradient: { kind: 'linear', from: [0, 0], to: [1, 1], stops: [{ offset: 0, color: '#ff0000' }, { offset: 1, color: '#0000ff', opacity: 0.5 }] },
    strokeGradient: { kind: 'radial', center: [0.5, 0.5], radius: 0.6, stops: [{ offset: 0, color: '#00ff00' }, { offset: 1, color: '#000000' }] },
    subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0], in: [0.9, 0], out: [1.1, 0.1] }, { p: [0.5, 1] }] }],
  })
/** 水平直线：框的高退化到最小值（0.01 mm），三个锚点共线 */
const hline = (id = 'hline') =>
  P({
    id, x: 5, y: 5, w: 40, h: 0.01, strokePt: 1,
    subpaths: [{ closed: false, nodes: [{ p: [0, 0.5] }, { p: [0.5, 0.5] }, { p: [1, 0.5] }] }],
  })
/** 连接用的一对：甲在左旋转 15°，乙在右不旋转，中间有缝；乙的名字 / 样式与甲不同 */
const joinA = (id = 'joinA') =>
  P({ id, x: 5, y: 60, w: 20, h: 10, rotationDeg: 15, color: '#aa0000', name: '甲',
    subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [0.5, 0.6], out: [0.6, 0.6] }, { p: [1, 1], in: [0.9, 0.9] }] }] })
const joinB = (id = 'joinB') =>
  P({ id, x: 30, y: 58, w: 18, h: 14, color: '#0000aa', name: '乙', strokePt: 3,
    subpaths: [{ closed: false, nodes: [{ p: [0, 0.2], out: [0.1, 0.2] }, { p: [0.6, 0.9] }, { p: [1, 0] }] }] })
/** 乙的第一个端点与甲的最后一个端点在页面上重合（不旋转时） */
const joinC = (id = 'joinC') =>
  P({ id, x: 25, y: 60, w: 10, h: 10, color: '#00aa00',
    subpaths: [{ closed: false, nodes: [{ p: [0, 0], in: [0, 0], out: [0.2, 0.3] }, { p: [1, 1] }] }] })
const joinD = (id = 'joinD') =>
  P({ id, x: 5, y: 60, w: 20, h: 10, color: '#aa00aa',
    subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1], in: [0.8, 1] }] }] })
/** 在组里的路径 */
const grouped = (id = 'grouped') => ({ ...curve(id), groupId: 'g_1' }) as PathObject

const R = (sp: number, i: number): Ref => ({ sp, i })

interface Case { name: string; objects: PathObject[]; op: Op }
const CASES: Case[] = [
  // ---- 平移锚点（页面上的位移按旋转换到编辑空间）
  { name: 'move-open-two', objects: [curve()], op: { op: 'move_anchors', id: 'curve', anchors: [R(0, 1), R(0, 2)], by_mm: [3, -2] } },
  { name: 'move-rotated-closed', objects: [blob()], op: { op: 'move_anchors', id: 'blob', anchors: [R(0, 0)], by_mm: [5, 4] } },
  { name: 'move-multi-rotated', objects: [multi()], op: { op: 'move_anchors', id: 'multi', anchors: [R(0, 1), R(1, 2)], by_mm: [-6, 7.5] } },
  { name: 'move-gradient-rotated', objects: [grad()], op: { op: 'move_anchors', id: 'grad', anchors: [R(0, 2)], by_mm: [4, 9] } },
  { name: 'move-hline-collinear', objects: [hline()], op: { op: 'move_anchors', id: 'hline', anchors: [R(0, 1)], by_mm: [0, 6] } },
  // ---- 拖手柄
  { name: 'handle-smooth-collinear', objects: [curve()], op: { op: 'move_handle', id: 'curve', anchor: R(0, 1), which: 'out', to_mm: [28, 10] } },
  { name: 'handle-break-smooth', objects: [curve()], op: { op: 'move_handle', id: 'curve', anchor: R(0, 2), which: 'in', to_mm: [26, 30], break_smooth: true } },
  { name: 'handle-corner-rotated', objects: [blob()], op: { op: 'move_handle', id: 'blob', anchor: R(0, 2), which: 'out', to_mm: [60, 45] } },
  { name: 'handle-zero-length-mirror', objects: [blob()], op: { op: 'move_handle', id: 'blob', anchor: R(0, 0), which: 'in', to_mm: [30, 8] } },
  // ---- 加锚点
  { name: 'add-all-segments-open', objects: [curve()], op: { op: 'add_anchors', id: 'curve' } },
  { name: 'add-after-anchors-rotated', objects: [blob()], op: { op: 'add_anchors', id: 'blob', anchors: [R(0, 1), R(0, 4)] } },
  { name: 'add-multi-all', objects: [multi()], op: { op: 'add_anchors', id: 'multi' } },
  { name: 'add-at-seg-t', objects: [curve()], op: { op: 'add_anchors', id: 'curve', at: { sp: 0, seg: 1, t: 0.3 } } },
  { name: 'add-at-closing-seg', objects: [blob()], op: { op: 'add_anchors', id: 'blob', at: { sp: 0, seg: 4, t: 0.7 } } },
  { name: 'add-at-straight-seg', objects: [multi()], op: { op: 'add_anchors', id: 'multi', at: { sp: 1, seg: 0, t: 0.25 } } },
  // ---- 删锚点
  { name: 'delete-one-open', objects: [curve()], op: { op: 'delete_anchors', id: 'curve', anchors: [R(0, 1)] } },
  { name: 'delete-closed-stays-closed', objects: [blob()], op: { op: 'delete_anchors', id: 'blob', anchors: [R(0, 0), R(0, 3)] } },
  { name: 'delete-closed-turns-open', objects: [blob()], op: { op: 'delete_anchors', id: 'blob', anchors: [R(0, 0), R(0, 2), R(0, 4)] } },
  { name: 'delete-subpath-vanishes', objects: [multi()], op: { op: 'delete_anchors', id: 'multi', anchors: [R(1, 0), R(1, 1)] } },
  { name: 'delete-everything-removes-object', objects: [curve(), blob()], op: { op: 'delete_anchors', id: 'curve', anchors: [R(0, 0), R(0, 1), R(0, 2), R(0, 3)] } },
  { name: 'delete-gradient', objects: [grad()], op: { op: 'delete_anchors', id: 'grad', anchors: [R(0, 1)] } },
  // ---- 转换锚点
  { name: 'convert-toggle-to-smooth', objects: [curve()], op: { op: 'convert_anchors', id: 'curve', anchors: [R(0, 0), R(0, 1), R(0, 3)] } },
  { name: 'convert-toggle-to-corner', objects: [curve()], op: { op: 'convert_anchors', id: 'curve', anchors: [R(0, 1), R(0, 2)] } },
  { name: 'convert-explicit-corner', objects: [blob()], op: { op: 'convert_anchors', id: 'blob', anchors: [R(0, 0), R(0, 1)], to: 'corner' } },
  { name: 'convert-explicit-smooth', objects: [blob()], op: { op: 'convert_anchors', id: 'blob', anchors: [R(0, 0), R(0, 2)], to: 'smooth' } },
  { name: 'convert-closed-wraps', objects: [multi()], op: { op: 'convert_anchors', id: 'multi', anchors: [R(0, 0), R(0, 3)] } },
  // ---- 平均
  { name: 'average-two-ends', objects: [curve()], op: { op: 'average_anchors', id: 'curve', anchors: [R(0, 0), R(0, 3)] } },
  { name: 'average-rotated-three', objects: [blob()], op: { op: 'average_anchors', id: 'blob', anchors: [R(0, 1), R(0, 3), R(0, 4)] } },
  // ---- 剪断
  { name: 'cut-open-middle', objects: [curve()], op: { op: 'cut_path', id: 'curve', anchors: [R(0, 1)] } },
  { name: 'cut-open-segment', objects: [curve()], op: { op: 'cut_path', id: 'curve', anchors: [R(0, 1), R(0, 2)] } },
  { name: 'cut-open-endpoint-is-noop', objects: [curve()], op: { op: 'cut_path', id: 'curve', anchors: [R(0, 0)] } },
  { name: 'cut-closed-once', objects: [blob()], op: { op: 'cut_path', id: 'blob', anchors: [R(0, 1)] } },
  { name: 'cut-closed-twice', objects: [blob()], op: { op: 'cut_path', id: 'blob', anchors: [R(0, 1), R(0, 3)] } },
  { name: 'cut-closed-segment', objects: [blob()], op: { op: 'cut_path', id: 'blob', anchors: [R(0, 2), R(0, 3)] } },
  { name: 'cut-closed-wrap-segment', objects: [blob()], op: { op: 'cut_path', id: 'blob', anchors: [R(0, 4), R(0, 0)] } },
  { name: 'cut-multi-both', objects: [multi()], op: { op: 'cut_path', id: 'multi', anchors: [R(0, 2), R(1, 1)] } },
  { name: 'cut-keeps-group', objects: [grouped()], op: { op: 'cut_path', id: 'grouped', anchors: [R(0, 2)] } },
  { name: 'cut-gradient', objects: [grad()], op: { op: 'cut_path', id: 'grad', anchors: [R(0, 1)] } },
  // ---- 连接（同一个对象里）
  { name: 'join-close-with-gap', objects: [curve()], op: { op: 'join_paths', id: 'curve', anchors: [R(0, 0), R(0, 3)] } },
  { name: 'join-close-coincident', objects: [loopOpen()], op: { op: 'join_paths', id: 'loopOpen', anchors: [R(0, 4), R(0, 0)] } },
  { name: 'join-two-subpaths-end-start', objects: [twoOpen()], op: { op: 'join_paths', id: 'twoOpen', anchors: [R(0, 2), R(1, 0)] } },
  { name: 'join-two-subpaths-start-start', objects: [twoOpen()], op: { op: 'join_paths', id: 'twoOpen', anchors: [R(0, 0), R(1, 0)] } },
  { name: 'join-two-subpaths-end-end', objects: [twoOpen()], op: { op: 'join_paths', id: 'twoOpen', anchors: [R(1, 1), R(0, 2)] } },
  // ---- 连接（两个对象）
  { name: 'join-objects-gap', objects: [joinA(), joinB()], op: { op: 'join_paths', ids: ['joinA', 'joinB'] } },
  { name: 'join-objects-gap-keep-first-by-order', objects: [joinA(), joinB()], op: { op: 'join_paths', ids: ['joinB', 'joinA'] } },
  { name: 'join-objects-coincident', objects: [joinD(), joinC()], op: { op: 'join_paths', ids: ['joinD', 'joinC'] } },
  { name: 'join-objects-coincident-reversed', objects: [joinD(), joinC()], op: { op: 'join_paths', ids: ['joinC', 'joinD'] } },
  // ---- 反转 / 复合路径
  { name: 'reverse-open', objects: [curve()], op: { op: 'reverse_path', id: 'curve' } },
  { name: 'reverse-multi', objects: [multi()], op: { op: 'reverse_path', id: 'multi' } },
  { name: 'compound-make', objects: [blob('back'), multi('mid'), curve('front')], op: { op: 'make_compound', ids: ['front', 'back'] } },
  { name: 'compound-make-gradient-rotated', objects: [grad('g'), blob('b')], op: { op: 'make_compound', ids: ['b', 'g'] } },
  { name: 'compound-release', objects: [multi(), curve('after')], op: { op: 'release_compound', id: 'multi' } },
  { name: 'compound-release-grouped-gradient', objects: [{ ...multi('mg'), groupId: 'g_2', fillGradient: grad().fillGradient, fill: '#abcdef' } as PathObject], op: { op: 'release_compound', id: 'mg' } },
]

const key = (a: Ref) => `${a.sp}:${a.i}`
const refs = (l?: Ref[]): AnchorRef[] => (l ?? []).map((a) => ({ sp: a.sp, i: a.i }))
const objs = () => useDocumentStore.getState().doc.objects

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_pe_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useUiStore.getState().setTool('select')
}

/** 进锚点编辑、点上锚点，再跑 store 动作 */
function inEdit(id: string, anchors: Ref[] | undefined, run: () => void) {
  useSelectionStore.getState().set([id])
  usePathEditStore.getState().enter(id)
  usePathEditStore.getState().setAnchors((anchors ?? []).map(key))
  run()
}

function drive(c: Case) {
  const { op } = c
  const base = op.id ? (objs().find((o) => o.id === op.id) as PathObject) : undefined
  switch (op.op) {
    case 'move_anchors':
      applyPathEdit(op.id!, literal('移动锚点'), (subs) => ({ subs: moveAnchors(subs, refs(op.anchors), unrotateDelta(base!, op.by_mm!)) }))
      break
    case 'move_handle':
      applyPathEdit(op.id!, literal('拖手柄'), (subs) => ({
        subs: moveHandle(subs, refs([op.anchor!])[0], op.which!, unrotatePoint(base!, op.to_mm!), op.break_smooth === true),
      }))
      break
    case 'add_anchors':
      if (op.at) applyPathEdit(op.id!, literal('加锚点'), (subs) => ({ subs: insertAnchor(subs, op.at!.sp, op.at!.seg, op.at!.t) }))
      else inEdit(op.id!, op.anchors, addAnchorPoints)
      break
    case 'delete_anchors':
      inEdit(op.id!, op.anchors, deleteAnchorPoints)
      break
    case 'convert_anchors':
      inEdit(op.id!, op.anchors, convertAnchorPoints)
      break
    case 'average_anchors':
      inEdit(op.id!, op.anchors, averageAnchorPoints)
      break
    case 'cut_path':
      inEdit(op.id!, op.anchors, cutPathAtAnchors)
      break
    case 'join_paths':
      if (op.ids) {
        usePathEditStore.getState().exit()
        useSelectionStore.getState().set(op.ids)
        joinPaths()
      } else inEdit(op.id!, op.anchors, joinPaths)
      break
    case 'reverse_path':
      usePathEditStore.getState().exit()
      useSelectionStore.getState().set([op.id!])
      reverseSelectedPaths()
      break
    case 'make_compound':
      usePathEditStore.getState().exit()
      useSelectionStore.getState().set(op.ids!)
      makeCompoundPath()
      break
    case 'release_compound':
      usePathEditStore.getState().exit()
      useSelectionStore.getState().set([op.id!])
      releaseCompoundPath()
      break
    default:
      throw new Error(`没有这个操作：${op.op}`)
  }
}

/** 新 id（改前没有的）按出现先后归一；整份过 JSON（去掉 undefined） */
function normalize(before: CanvasObject[], after: CanvasObject[]): unknown[] {
  const known = new Set(before.map((o) => o.id))
  const map = new Map<string, string>()
  const out = after.map((o) => {
    if (known.has(o.id)) return o
    if (!map.has(o.id)) map.set(o.id, `NEW${map.size + 1}`)
    return { ...o, id: map.get(o.id)! }
  })
  // sin / cos / hypot 在不同 Node 版本上末位可能差 1 ulp：金样取到 1e-9（后端比对的容差是 1e-6）
  return JSON.parse(JSON.stringify(out, (_k, v) => (typeof v === 'number' ? Math.round(v * 1e9) / 1e9 : v)))
}

async function build() {
  const cases: { name: string; objects: unknown[]; op: Op; expect: unknown[] }[] = []
  for (const c of CASES) {
    await seed(c.objects)
    const before = JSON.parse(JSON.stringify(objs())) as CanvasObject[]
    drive(c)
    cases.push({ name: c.name, objects: before, op: c.op, expect: normalize(before, objs()) })
  }
  return cases
}

describe('路径锚点编辑的对拍样本', () => {
  beforeEach(() => {
    useSelectionStore.getState().clear()
  })
  it('现在算出来的就是金样（UPDATE_GOLDEN=1 重写）', async () => {
    const cases = await build()
    if (process.env.UPDATE_GOLDEN) {
      writeFileSync(
        GOLDEN,
        JSON.stringify(
          { note: '前端 lib/pathEdit + store/pathActions + pathfinderActions 的输出（文档形态）；后端 tests/test_pathedit_golden.py 经 vectoredit.apply_ops 逐数比', cases },
          null,
          1,
        ) + '\n',
      )
    }
    const golden = JSON.parse(readFileSync(GOLDEN, 'utf8')) as { cases: unknown[] }
    expect(JSON.parse(JSON.stringify(cases))).toEqual(golden.cases)
  })
  it('每个样本都真的改了东西（没有一条是空跑）', async () => {
    const cases = await build()
    const noop = new Set(['cut-open-endpoint-is-noop'])
    for (const c of cases) {
      if (noop.has(c.name)) continue
      expect(JSON.stringify(c.expect), c.name).not.toBe(JSON.stringify(JSON.parse(JSON.stringify(c.objects))))
    }
  })
})
