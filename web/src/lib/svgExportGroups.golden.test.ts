/**
 * SVG 导出的组树与名字（ADR 0208 §七之三）的前后端对拍样本。
 *
 * 这里把几份「文档」（画布对象，带组链 / 名字 / 剪切蒙版）交给前端**真实的** `toExportObjects`，把发出的载荷与
 * 「导出的 SVG 里 `<g>` 树应该长什么样」一起写进 `tests/golden/svg_export_groups.json`；后端
 * `tests/test_svg_export_groups.py` 读同一份载荷，走真实的 `plan.compile_page` → `svgwriter.write_svg`，
 * 再解析写出来的 SVG，断言 `<g>` 树、名字（`id`）与剪切蒙版的个数。
 *
 * 期望的树有两个出处：能用产品自己的图层树（`layerTree`）表示的文档，一律与它相比（独立于下面的 `expectedTree`）；
 * 组成员在 z 序里不连续的文档（图层树把它们并成一个组，SVG 为了不挪绘制顺序拆成几段）只用 `expectedTree`。
 *
 * 平时这里核「现在算出来的 = 金样」（改了载荷就红）；`UPDATE_GOLDEN=1` 时重写金样。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CanvasObject } from '@/types/document'
import { groupMasks, groupPathOf, isActiveMask, layerTree, type LayerNode } from './groupTree'
import { toExportObjects } from './exportPayload'

const GOLDEN = path.resolve(process.cwd(), '../tests/golden/svg_export_groups.json')

type Spec = Record<string, unknown>
const rect = (id: string, over: Spec = {}): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x: 10, y: 10, w: 20, h: 12, strokePt: 1, color: '#333333', fill: '#E69F00', ...over }) as unknown as CanvasObject
const ellipse = (id: string, over: Spec = {}): CanvasObject => rect(id, { shape: 'ellipse', fill: '#56B4E9', ...over })
const text = (id: string, t: string, over: Spec = {}): CanvasObject =>
  ({ id, type: 'text', text: t, x: 10, y: 30, w: 30, h: 6, sizePt: 8, bold: false, color: '#000000', align: 'left', ...over }) as unknown as CanvasObject
const arrow = (id: string, over: Spec = {}): CanvasObject =>
  ({ id, type: 'arrow', x: 10, y: 40, w: 30, h: 6, start: { rx: 0, ry: 0.5 }, end: { rx: 1, ry: 0.5 }, strokePt: 1, color: '#000000', head: 'end', ...over }) as unknown as CanvasObject
const mask = (id: string, gid: string, over: Spec = {}): CanvasObject =>
  ({
    id, type: 'path', x: 12, y: 10, w: 16, h: 12, strokePt: 1, color: '#000000', fill: '#888888', groupId: gid,
    clipMask: true, hidden: true,
    subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }, { p: [0, 1] }] }],
    ...over,
  }) as unknown as CanvasObject
const inGroup = (path: string[]): Spec => ({ groupId: path.at(-1), ...(path.length >= 2 ? { groupPath: path } : {}) })

interface Case {
  name: string
  objects: CanvasObject[]
  /** 图层树能表示（组成员在 z 序里连续）= 与 `layerTree` 对照 */
  contiguous: boolean
}

const CASES: Case[] = [
  {
    name: '两层嵌套组、重名与带空格 / 标点 / 数字开头的名字',
    contiguous: true,
    objects: [
      rect('bg', { name: '背景' }),
      rect('r1', { name: '细胞膜', ...inGroup(['gA']) }),
      ellipse('r2', { name: '受体 EGFR', ...inGroup(['gA', 'gB']) }),
      ellipse('r3', { name: '受体 EGFR', ...inGroup(['gA', 'gB']) }),
      text('t1', 'Ca^{2+} 内流', { name: '标签 钙离子', ...inGroup(['gA']) }),
      arrow('a1', { ...inGroup(['gA']) }),
      rect('n1', { name: '1 号样品' }),
      rect('n2', { name: '激活箭头 EGFR→RAS' }),
    ],
  },
  {
    name: '一个剪切组（三个对象），蒙版只写一次',
    contiguous: true,
    objects: [
      rect('pre', { name: '底图' }),
      mask('m', 'gClip'),
      rect('c1', { name: '放大 细胞核', ...inGroup(['gClip']) }),
      ellipse('c2', { name: '染色质', ...inGroup(['gClip']) }),
      arrow('c3', { name: '指向', ...inGroup(['gClip']) }),
      rect('post', { name: '图例' }),
    ],
  },
  {
    name: '外层与内层都是剪切组',
    contiguous: true,
    objects: [
      mask('mo', 'gOut'),
      rect('o1', { name: '外层成员', ...inGroup(['gOut']) }),
      mask('mi', 'gIn', { groupPath: ['gOut', 'gIn'], x: 14, w: 10 }),
      ellipse('i1', { name: '内层成员 1', ...inGroup(['gOut', 'gIn']) }),
      ellipse('i2', { name: '内层成员 2', ...inGroup(['gOut', 'gIn']) }),
    ],
  },
  {
    name: '组成员在 z 序里不连续：拆成几段、绘制顺序不挪',
    contiguous: false,
    objects: [
      rect('x1', { name: '甲', ...inGroup(['gA']) }),
      rect('y', { name: '夹在中间的' }),
      rect('x2', { name: '乙', ...inGroup(['gA']) }),
      rect('x3', { name: '丙', ...inGroup(['gA']) }),
    ],
  },
  {
    name: '组有名字：名字在隐藏的成员上 / 同组不一致取 z 序第一个 / 内层另有名字',
    contiguous: false, // 隐藏的成员图层树照列、导出不发，两边的树对不上，只用 expectedTree
    objects: [
      rect('h', { name: '隐藏的', hidden: true, ...inGroup(['gA']), groupNames: { gA: '机理图' } }),
      rect('r1', { name: '细胞膜', ...inGroup(['gA']) }),
      ellipse('r2', { name: '受体', ...inGroup(['gA', 'gB']), groupNames: { gA: '不一致的名字', gB: '受体簇' } }),
      rect('free', { name: '游离' }),
    ],
  },
  {
    name: '没有组也没有名字：载荷不多一个键',
    contiguous: true,
    objects: [rect('p1'), ellipse('p2'), arrow('p3'), text('p4', 'Hello')],
  },
]

/** 期望的 `<g>` 树：叶子 = `{ o: 名字 | null }`，组 = `{ g: [子节点], clip: 是否剪切组 }`（组名不进树；名字落在 `<g id>` 上由后端 `test_group_names.py` 另核） */
type Tree = { o: string | null } | { g: Tree[]; clip: boolean }

/** 按 z 序把「连续、共享同一组链前缀」的对象装进同一个组（后端 `plan._svg_children` 的独立一份，用来盖住图层树表示不了的文档） */
function expectedTree(objects: readonly CanvasObject[]): Tree[] {
  const masks = groupMasks(objects)
  const root: Tree[] = []
  const stack: { gid: string; node: { g: Tree[]; clip: boolean } }[] = []
  for (const o of objects) {
    if (o.hidden || isActiveMask(o, masks)) continue
    const p = groupPathOf(o)
    let k = 0
    while (k < stack.length && k < p.length && stack[k].gid === p[k]) k++
    stack.length = k
    for (let i = k; i < p.length; i++) {
      const node = { g: [] as Tree[], clip: masks.has(p[i]) }
      ;(stack.length ? stack[stack.length - 1].node.g : root).push(node)
      stack.push({ gid: p[i], node })
    }
    ;(stack.length ? stack[stack.length - 1].node.g : root).push({ o: o.name ?? null })
  }
  return root
}

/** 产品自己的图层树（顶 → 底，组出现在最上面成员的位置）→ 同一种树（底 → 顶）；剪切路径对象导出时不画，拿掉 */
function fromLayerTree(nodes: LayerNode[], isMask: (o: CanvasObject) => boolean): Tree[] {
  const out: Tree[] = []
  for (const n of [...nodes].reverse()) {
    if (n.kind === 'object') {
      if (!isMask(n.obj)) out.push({ o: n.obj.name ?? null })
    } else {
      out.push({ g: fromLayerTree(n.children, isMask), clip: n.mask !== null })
    }
  }
  return out
}

const plain = (v: unknown): unknown => JSON.parse(JSON.stringify(v))

function build() {
  return CASES.map(({ name, objects }) => ({
    name,
    page_mm: [120, 90],
    payload: plain(toExportObjects(objects)),
    tree: expectedTree(objects),
  }))
}

describe('SVG 导出的组树 / 名字 / 蒙版：前后端对拍样本', () => {
  it('现在算出来的就是金样（UPDATE_GOLDEN=1 重写）', () => {
    const cases = build()
    if (process.env.UPDATE_GOLDEN) {
      writeFileSync(
        GOLDEN,
        JSON.stringify(
          { note: '前端 lib/exportPayload.toExportObjects 发出的载荷 + 期望的 <g> 树；后端 tests/test_svg_export_groups.py 读同一份写 SVG 再解析', cases },
          null,
          1,
        ) + '\n',
      )
    }
    const golden = JSON.parse(readFileSync(GOLDEN, 'utf8')) as { cases: unknown[] }
    expect(cases).toEqual(golden.cases)
  })

  it.each(CASES.filter((c) => c.contiguous))('$name：期望的树与图层树一致', ({ objects }) => {
    const masks = groupMasks(objects)
    expect(expectedTree(objects)).toEqual(fromLayerTree(layerTree(objects), (o) => isActiveMask(o, masks)))
  })

  it('没有组、没有名字的文档：载荷里一个新键都没有', () => {
    const c = CASES.find((x) => x.name.startsWith('没有组'))!
    for (const e of toExportObjects(c.objects)) {
      expect(Object.keys(e)).not.toEqual(expect.arrayContaining(['name']))
      expect(e).not.toHaveProperty('group_path')
      expect(e).not.toHaveProperty('name')
    }
  })

  it('组名按全集读：发给每个可见成员，与 group_path 同长；没名字的层是空串', () => {
    const c = CASES.find((x) => x.name.startsWith('组有名字'))!
    const out = Object.fromEntries(toExportObjects(c.objects).map((e) => [e.name, e]))
    expect(out['细胞膜'].group_names).toEqual(['机理图'])
    expect(out['受体'].group_names).toEqual(['机理图', '受体簇'])
    expect(out['游离']).not.toHaveProperty('group_names')
  })

  it('剪切组的每个被裁对象都带上「这个蒙版属于哪个组」', () => {
    const c = CASES.find((x) => x.name.startsWith('外层与内层'))!
    const out = toExportObjects(c.objects)
    expect(out.map((e) => e.clip?.map((m) => m.group))).toEqual([['gOut'], ['gOut', 'gIn'], ['gOut', 'gIn']])
  })
})
