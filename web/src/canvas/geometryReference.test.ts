/**
 * 几何参考尺（QA 2026-09-24，GEO-01 / 02 / 04 / 06 / 07 的前端半）：
 * **屏幕位移 → 写进文档的 override 值**，由测试侧独立实现的坐标尺给出期望。
 *
 * 独立尺（不 import 任何生产换算：mmToWorld / contentDelta / flipY / resizeGroup 都不用）：
 *
 *   T(p)            = origin + pan + zoom · (panel.x_mm + p · W_mm) · 96/25.4     （CSS px，y 向下）
 *   T⁻¹(T(p) + Δs)  = p + Δs / (zoom · W_mm · 96/25.4)
 *
 * 常量 96/25.4 是 CSS 的定义（96 px / in），图幅 W_mm/H_mm 是夹具脚本里写死的 figsize。
 * Axes 的 `position` 是 bottom-origin：屏幕往下 Δs_y > 0 ⇒ position.y 变小。
 *
 * 夹具 `__fixtures__/geoReference.json` 是**真实 worker 输出**的 manifest（G1 / G2 / G2big）
 * 裁掉非几何字段，G1 / G2 附带压缩过的真实 SVG（id 与 transform 原样）。生成：
 * `docs/qa/2026-09-24/geo/repro/dump_geo_fixtures.py --web-fixture …`（确定性，sha256 记在 ledger）。
 *
 * 另一半——override 写进 worker 之后 matplotlib 真的把元素摆在那里——在
 * `tests/test_geometry_reference.py`。浏览器里整条链路（真 PanelView 的 layout + 真指针）
 * 在 `e2e/geometry-reference.spec.ts`。
 *
 * 量化预算：单元素拖动的 `pos_frac` 目前**不取整**，整组 / Axes 写 `round4`。判据容差统一取
 * round4 的半步（5e-5）+ 浮点余量，而不是更宽的 0.5 CSS px：实测误差 ≤ 1e-15（见 ledger 的校准
 * 记录），留出 round4 是因为那是**存储合同**允许的量化，不是为了容纳漂移。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import type { EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { alignEntries, groupOf } from '@/lib/elementGeom'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import fixture from './__fixtures__/geoReference.json'
import {
  startAxesDrag,
  startElementDrag,
  startElementGroupMove,
  startGroupResize,
} from './interactions'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

/* ------------------------------ 独立坐标尺 ------------------------------- */

/** CSS 的定义：96 px / in，1 in = 25.4 mm。 */
const CSS_PX_PER_MM = 96 / 25.4
/** 夹具脚本里写死的 figsize（英寸）→ mm。与 manifest 的 size_mm 无关。 */
const SIZE_MM = {
  G1: [5.0 * 25.4, 3.2 * 25.4],
  G2: [6.4 * 25.4, 3.0 * 25.4],
  G2big: [6.0 * 25.4, 6.0 * 25.4],
} as const
type Stem = keyof typeof SIZE_MM

/** T⁻¹(T(p)+Δs) − p：屏幕 CSS 像素位移 → figure 分数位移（y 向下）。 */
function fracDelta(stem: Stem, zoom: number, ds: [number, number]): [number, number] {
  const [w, h] = SIZE_MM[stem]
  return [ds[0] / (zoom * w * CSS_PX_PER_MM), ds[1] / (zoom * h * CSS_PX_PER_MM)]
}
/** 面板内容在世界坐标里的大小（PanelView 传给拖动入口的 layout），同样由独立常量算。 */
const layoutOf = (stem: Stem) => ({
  width: SIZE_MM[stem][0] * CSS_PX_PER_MM,
  height: SIZE_MM[stem][1] * CSS_PX_PER_MM,
})
/** round4 半步 + 浮点余量：存储合同允许的量化，见文件头 */
const Q = 5e-5 + 1e-12
const r4 = (v: number) => Math.round(v * 1e4) / 1e4

/* -------------------------------- 夹具 ----------------------------------- */

type Fx = { stem: string; size_mm: [number, number]; elements: ManifestElement[]; svg?: string }
const FX = fixture as unknown as Record<Stem, Fx>
const manifestOf = (stem: Stem): Manifest =>
  ({ stem, size_mm: FX[stem].size_mm, elements: FX[stem].elements }) as unknown as Manifest
const el = (stem: Stem, gid: string): ManifestElement => {
  const e = FX[stem].elements.find((x) => x.gid === gid)
  if (!e) throw new Error(`夹具里没有 ${stem}/${gid}`)
  return e
}

const ZOOMS = [0.75, 1, 1.5, 1.37] // 1.37：非整数视图倍率
const DIRS: [number, number][] = [
  [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1],
]
const STEP = 37 // 每个方向的屏幕 CSS 像素

/** G1 里执行前写定的可拖对象（与 tests/test_geometry_reference.py 同一张表） */
const G1_DRAGGABLE = [
  'axes_0.title_left',
  'axes_0.xlabel',
  'axes_0.ylabel',
  'axes_0.texts_0',
  'axes_0.patches_0',
  'axes_0.legend',
]

/* ------------------------------ 文档与指针 ------------------------------- */

let current: Stem = 'G1'

const panelOf = (stem: Stem): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    // 面板不在原点：T 里的平移项必须被真实地抵消，而不是碰巧为零
    x: 23.7,
    y: 11.3,
    w: SIZE_MM[stem][0],
    h: SIZE_MM[stem][1],
    fileId: `${stem}.pdf`,
    fileKind: 'pdf',
    nativeW: SIZE_MM[stem][0],
    nativeH: SIZE_MM[stem][1],
    script: 'geo_library.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overridesNow = () => livePanel().overrides
const valueOf = (gid: string, prop: string) =>
  overridesNow().find((o) => o.gid === gid && o.prop === prop)?.value as number[] | undefined

const down = (x: number, y: number) =>
  ({ clientX: x, clientY: y, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: 'pointermove' | 'pointerup' | 'pointercancel', x: number, y: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }))
/** 从 (sx,sy) 走 n 步到 (sx+dx, sy+dy)，每步真的发一次 move 并刷一帧 */
function walk(sx: number, sy: number, dx: number, dy: number, n: number) {
  for (let i = 1; i <= n; i++) {
    fire('pointermove', sx + (dx * i) / n, sy + (dy * i) / n)
    flushPreviewFrame()
  }
}

async function setup(stem: Stem, zoom = 1) {
  current = stem
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {})) // 权威渲染一直在路上：量的是提交那一刻
  resetPreview()
  localStorage.clear()
  useViewportStore.setState({ zoom, panX: 41.5, panY: -17.25, originX: 12, originY: 64, viewW: 1200, viewH: 800 })
  useUiStore.setState({ tool: 'select', snapEnabled: false, elementPanelId: 'p1', selectedGids: [] })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), `d_geo_${stem}`)
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf(stem))
  })
  useDocumentStore.setState({ past: [], future: [] })
  seedExactRender(panelOf(stem), manifestOf(stem), { svg: FX[stem].svg ?? '<svg/>' })
  document.body.innerHTML = `<div data-element-svg="p1">${FX[stem].svg ?? ''}</div>`
}

/** 回到干净基线：撤销到底 + 清预览 + 重摆原 SVG（每个方向独立起算） */
function rewind() {
  while (useDocumentStore.getState().past.length) useDocumentStore.getState().undo()
  expect(overridesNow()).toEqual([])
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = `<div data-element-svg="p1">${FX[current].svg ?? ''}</div>`
}

/** 屏幕上 (sx,sy) 处的起手点：用独立尺 T 从锚点算出来（只为让 clientX/Y 是真实可能的值） */
function screenOf(stem: Stem, p: [number, number]): [number, number] {
  const { zoom, panX, panY, originX, originY } = useViewportStore.getState()
  const panel = panelOf(stem)
  return [
    originX + panX + zoom * (panel.x + p[0] * SIZE_MM[stem][0]) * CSS_PX_PER_MM,
    originY + panY + zoom * (panel.y + p[1] * SIZE_MM[stem][1]) * CSS_PX_PER_MM,
  ]
}

const svgNode = (gid: string) => document.querySelector(`[data-element-svg="p1"] [id="${gid}"]`)
const vb = (): [number, number] => {
  const v = (document.querySelector('[data-element-svg="p1"] svg')?.getAttribute('viewBox') ?? '')
    .split(/\s+/)
    .map(Number)
  return [v[2], v[3]]
}
const translateOf = (gid: string): [number, number] | null => {
  const m = /^translate\(([^,]+),([^)]+)\)/.exec(svgNode(gid)?.getAttribute('transform') ?? '')
  return m ? [Number(m[1]), Number(m[2])] : null
}

beforeEach(() => setup('G1'))
afterEach(() => {
  resetPreview()
  useInteractionStore.getState().end()
  document.body.innerHTML = ''
})

/* ============================== GEO-01 单选 =============================== */

describe('GEO-01 单选八方向 × 四档视图倍率：落点 = 独立尺的 T⁻¹(T(p)+Δs)', () => {
  it('夹具自检：G1 的可拖对象与 drag_prop 是执行前写定的那一张表', () => {
    for (const gid of G1_DRAGGABLE) {
      const e = el('G1', gid)
      expect(e.draggable, gid).toBe(true)
      expect(e.anchor, gid).toHaveLength(2)
      expect(['pos_frac', 'loc_frac'], gid).toContain(e.drag_prop)
      expect(svgNode(gid), `${gid} 在真实 SVG 里有节点`).not.toBeNull()
    }
    expect(FX.G1.size_mm[0]).toBeCloseTo(SIZE_MM.G1[0], 6)
    expect(FX.G1.size_mm[1]).toBeCloseTo(SIZE_MM.G1[1], 6)
  })

  for (const zoom of ZOOMS) {
    it(`zoom=${zoom}：六类可拖对象 × 八个方向，预览与提交都落在独立尺上，只写一条、只动目标`, async () => {
      await setup('G1', zoom)
      const [vbW, vbH] = vb()
      for (const gid of G1_DRAGGABLE) {
        const e = el('G1', gid)
        const prop = e.drag_prop!
        const innerBefore = svgNode(gid)!.innerHTML
        for (const [ux, uy] of DIRS) {
          const where = `${gid} zoom=${zoom} dir=(${ux},${uy})`
          const ds: [number, number] = [ux * STEP, uy * STEP]
          const [dfx, dfy] = fracDelta('G1', zoom, ds)
          const [sx, sy] = screenOf('G1', e.anchor as [number, number])

          startElementDrag(down(sx, sy), livePanel(), e, layoutOf('G1'))
          walk(sx, sy, ds[0], ds[1], 12)
          // 预览：translate 以 SVG 用户单位计 = Δfrac · viewBox，且只挂在目标节点上
          const t = translateOf(gid)
          expect(t, where).not.toBeNull()
          expect(Math.abs(t![0] - dfx * vbW), where).toBeLessThan(1e-9)
          expect(Math.abs(t![1] - dfy * vbH), where).toBeLessThan(1e-9)
          expect(svgNode(gid)!.innerHTML, `${where} 原始子节点/transform 不得被改`).toBe(innerBefore)
          expect(document.querySelectorAll('[data-element-svg="p1"] [transform^="translate("]').length).toBeGreaterThan(0)
          fire('pointerup', sx + ds[0], sy + ds[1])

          const v = valueOf(gid, prop)
          expect(v, where).toBeDefined()
          expect(Math.abs(v![0] - (e.anchor![0] + dfx)), where).toBeLessThan(Q)
          expect(Math.abs(v![1] - (e.anchor![1] + dfy)), where).toBeLessThan(Q)
          // 只写了这一条，一条历史，一次定稿渲染
          expect(overridesNow(), where).toHaveLength(1)
          expect(useDocumentStore.getState().past, where).toHaveLength(1)
          expect(engineRender, where).toHaveBeenCalledTimes(1)
          engineRender.mockClear()
          rewind()
        }
      }
    })
  }

  it('Axes 整体移动（position 是 bottom-origin）：八个方向，y 分量方向正确', async () => {
    for (const zoom of [0.75, 1.5]) {
      await setup('G1', zoom)
      const axes = el('G1', 'axes_0')
      const pos0 = axes.editable.find((f) => f.prop === 'position')!.value as number[]
      for (const [ux, uy] of DIRS) {
        const where = `axes_0 zoom=${zoom} dir=(${ux},${uy})`
        // 小步长：不贴边（贴边钳位另有用例），落点应当是纯平移
        const ds: [number, number] = [ux * 11, uy * 11]
        const [dfx, dfy] = fracDelta('G1', zoom, ds)
        startAxesDrag(down(300, 200), livePanel(), axes, layoutOf('G1'), 'move')
        walk(300, 200, ds[0], ds[1], 8)
        fire('pointerup', 300 + ds[0], 200 + ds[1])
        const v = valueOf('axes_0', 'position')!
        expect(Math.abs(v[0] - (pos0[0] + dfx)), where).toBeLessThan(Q)
        expect(Math.abs(v[1] - (pos0[1] - dfy)), where).toBeLessThan(Q) // 屏幕向下 = y 变小
        expect(v[2], where).toBe(r4(pos0[2]))
        expect(v[3], where).toBe(r4(pos0[3]))
        rewind()
      }
    }
  })
})

/* ============================== GEO-02 多选 =============================== */

describe('GEO-02 多选整组平移：每个成员恰好一次、间距不变、未选中的不动', () => {
  for (const count of [2, 10, 100]) {
    it(`G2big：${count} 个文字同一屏幕位移`, async () => {
      await setup('G2big', 1.5)
      const texts = FX.G2big.elements
        .filter((e) => e.role === 'text' && e.drag_prop === 'pos_frac')
        .map((e) => e.gid)
      expect(texts).toHaveLength(100)
      const picked = texts.filter((_, i) => i % (100 / count) === 0).slice(0, count)
      expect(picked).toHaveLength(count)
      const entries = alignEntries(livePanel(), manifestOf('G2big'), picked)
      expect(entries.map((x) => x.key).sort()).toEqual([...picked].sort())

      const ds: [number, number] = [23, -41]
      const [dfx, dfy] = fracDelta('G2big', 1.5, ds)
      startElementGroupMove(down(500, 400), livePanel(), entries, layoutOf('G2big'))
      walk(500, 400, ds[0], ds[1], 20)
      fire('pointerup', 500 + ds[0], 400 + ds[1])

      const ov = overridesNow()
      // 恰好一次：每个成员一条，不多不少
      expect(ov).toHaveLength(count)
      expect(new Set(ov.map((o) => o.gid)).size).toBe(count)
      for (const gid of picked) {
        const a = el('G2big', gid).anchor!
        const v = valueOf(gid, 'pos_frac')!
        expect(Math.abs(v[0] - (a[0] + dfx)), gid).toBeLessThan(Q)
        expect(Math.abs(v[1] - (a[1] + dfy)), gid).toBeLessThan(Q)
      }
      // 两两间距（相对第一个成员）在 round4 量化内不变
      const ref = picked[0]
      for (const gid of picked.slice(1)) {
        for (const k of [0, 1]) {
          const before = el('G2big', gid).anchor![k] - el('G2big', ref).anchor![k]
          const after = valueOf(gid, 'pos_frac')![k] - valueOf(ref, 'pos_frac')![k]
          expect(Math.abs(after - before), `${gid}[${k}]`).toBeLessThan(2 * Q)
        }
      }
      // 未选中的一个都没写
      for (const gid of texts.filter((g) => !picked.includes(g))) {
        expect(valueOf(gid, 'pos_frac'), gid).toBeUndefined()
      }
      // 一条历史、一次定稿渲染
      expect(useDocumentStore.getState().past).toHaveLength(1)
      expect(engineRender).toHaveBeenCalledTimes(1)
    })
  }

  it('G2 混合类型（文字 + 图例 + 形状 + 另一面板的标题），跨两个 Axes', async () => {
    await setup('G2', 0.75)
    const picked = ['axes_0.texts_0', 'axes_0.legend', 'axes_1.patches_0', 'axes_1.title', 'axes_1.texts_0']
    const entries = alignEntries(livePanel(), manifestOf('G2'), picked)
    expect(entries.map((x) => x.key).sort()).toEqual([...picked].sort())
    const ds: [number, number] = [-19, 27]
    const [dfx, dfy] = fracDelta('G2', 0.75, ds)
    const [vbW, vbH] = vb()
    startElementGroupMove(down(400, 300), livePanel(), entries, layoutOf('G2'))
    walk(400, 300, ds[0], ds[1], 30)
    // 预览：每个成员的节点各挂一次同样的 translate
    for (const gid of picked) {
      const t = translateOf(gid)
      expect(t, gid).not.toBeNull()
      expect(Math.abs(t![0] - dfx * vbW), gid).toBeLessThan(1e-9)
      expect(Math.abs(t![1] - dfy * vbH), gid).toBeLessThan(1e-9)
    }
    fire('pointerup', 400 + ds[0], 300 + ds[1])
    expect(overridesNow()).toHaveLength(picked.length)
    for (const gid of picked) {
      const e = el('G2', gid)
      const v = valueOf(gid, e.drag_prop!)!
      expect(Math.abs(v[0] - (e.anchor![0] + dfx)), gid).toBeLessThan(Q)
      expect(Math.abs(v[1] - (e.anchor![1] + dfy)), gid).toBeLessThan(Q)
    }
    // 同名的另一面板对象（axes_0.title）没被选，不得被写
    expect(valueOf('axes_0.title', 'pos_frac')).toBeUndefined()
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
})

/* ============================ GEO-04 成组缩放 ============================ */

describe('GEO-04 成组缩放八手柄：固定边 / 基点守恒，成员按 pᵢ′ = c + S(pᵢ − c) 重映射', () => {
  /** 独立实现：top-origin 框 [x, y, w, h]；手柄 dir 对面的边 / 角是基点 c */
  function expectedGroup(g: number[], dir: string, dfx: number, dfy: number) {
    const [gx, gy, gw, gh] = g
    let sx = dir.includes('e') ? (gw + dfx) / gw : dir.includes('w') ? (gw - dfx) / gw : 1
    let sy = dir.includes('s') ? (gh + dfy) / gh : dir.includes('n') ? (gh - dfy) / gh : 1
    if (dir.length === 2) {
      // 角柄等比：取变化更大的那一轴（合同见 axesLayout.resizeGroup 的注释）
      const k = Math.abs(sx - 1) >= Math.abs(sy - 1) ? sx : sy
      sx = sy = k
    }
    const cx = dir.includes('w') ? gx + gw : gx
    const cy = dir.includes('n') ? gy + gh : gy
    return { sx, sy, cx, cy }
  }

  for (const dir of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const) {
    it(`手柄 ${dir}`, async () => {
      await setup('G2', 1)
      const entries = alignEntries(livePanel(), manifestOf('G2'), ['axes_0', 'axes_1'])
      const group = groupOf(entries)!
      expect(group).not.toBeNull()
      const ds: [number, number] = [dir.includes('e') ? 24 : dir.includes('w') ? -24 : 0, dir.includes('s') ? 15 : dir.includes('n') ? -15 : 0]
      const [dfx, dfy] = fracDelta('G2', 1, ds)
      const { sx, sy, cx, cy } = expectedGroup(group.box, dir, dfx, dfy)
      startGroupResize(down(600, 300), livePanel(), group, layoutOf('G2'), dir)
      walk(600, 300, ds[0], ds[1], 10)
      fire('pointerup', 600 + ds[0], 300 + ds[1])
      expect(useDocumentStore.getState().past).toHaveLength(1)
      for (const en of entries) {
        // 成员的 top-origin 框（独立地从 bottom-origin position 翻过来）
        const pos0 = el('G2', en.key).editable.find((f) => f.prop === 'position')!.value as number[]
        const top0 = [pos0[0], 1 - pos0[1] - pos0[3], pos0[2], pos0[3]]
        const want = [cx + sx * (top0[0] - cx), cy + sy * (top0[1] - cy), sx * top0[2], sy * top0[3]]
        const v = valueOf(en.key, 'position')!
        const got = [v[0], 1 - v[1] - v[3], v[2], v[3]]
        for (let k = 0; k < 4; k++) {
          expect(Math.abs(got[k] - want[k]), `${dir} ${en.key}[${k}]`).toBeLessThan(2 * Q)
        }
      }
      // 孪生轴 axes_2 没被选：前端不写它（matplotlib 的 twinned 关系由引擎带着走）
      expect(valueOf('axes_2', 'position')).toBeUndefined()
    })
  }
})

/* ========================== GEO-06 事件密度与往返 ========================== */

describe('GEO-06 事件密度与往返：同起终点同结果，反操作回到量化范围内', () => {
  const e = () => el('G1', 'axes_0.texts_0')

  it('2 次 move 与 200 次 move 到同一终点：写下的值逐位相同', async () => {
    const values: number[][] = []
    for (const n of [2, 200]) {
      await setup('G1', 1.37)
      startElementDrag(down(100, 100), livePanel(), e(), layoutOf('G1'))
      walk(100, 100, 63, -29, n)
      fire('pointerup', 163, 71)
      values.push(valueOf('axes_0.texts_0', 'pos_frac')!)
    }
    expect(values[1]).toEqual(values[0])
  })

  it('一次长拖 vs 分三段拖：落点在量化预算内一致（整组平移同样）', async () => {
    await setup('G1', 1.5)
    startElementDrag(down(0, 0), livePanel(), e(), layoutOf('G1'))
    walk(0, 0, 90, 45, 30)
    fire('pointerup', 90, 45)
    const single = valueOf('axes_0.texts_0', 'pos_frac')!

    await setup('G1', 1.5)
    for (const seg of [[30, 10], [45, 20], [15, 15]] as [number, number][]) {
      startElementDrag(down(0, 0), livePanel(), e(), layoutOf('G1'))
      walk(0, 0, seg[0], seg[1], 10)
      fire('pointerup', seg[0], seg[1])
    }
    const multi = valueOf('axes_0.texts_0', 'pos_frac')!
    expect(Math.abs(multi[0] - single[0])).toBeLessThan(Q)
    expect(Math.abs(multi[1] - single[1])).toBeLessThan(Q)
    expect(useDocumentStore.getState().past).toHaveLength(3) // 三段 = 三条历史

    // 整组平移：round4 每段都取整，三段累计误差仍在 3 个半步之内
    await setup('G2', 1)
    const picked = ['axes_0.texts_0', 'axes_0.texts_1', 'axes_1.texts_0']
    const one = () => alignEntries(livePanel(), manifestOf('G2'), picked)
    startElementGroupMove(down(0, 0), livePanel(), one(), layoutOf('G2'))
    walk(0, 0, 90, 45, 30)
    fire('pointerup', 90, 45)
    const groupSingle = picked.map((g) => valueOf(g, 'pos_frac')!)
    await setup('G2', 1)
    for (const seg of [[30, 10], [45, 20], [15, 15]] as [number, number][]) {
      startElementGroupMove(down(0, 0), livePanel(), one(), layoutOf('G2'))
      walk(0, 0, seg[0], seg[1], 10)
      fire('pointerup', seg[0], seg[1])
    }
    picked.forEach((g, i) => {
      const v = valueOf(g, 'pos_frac')!
      expect(Math.abs(v[0] - groupSingle[i][0]), g).toBeLessThan(3 * Q)
      expect(Math.abs(v[1] - groupSingle[i][1]), g).toBeLessThan(3 * Q)
    })
  })

  it('反向拖回：单元素回到原锚点；整组 20 次往返不随次数漂移', async () => {
    await setup('G1', 0.75)
    const a = e().anchor!
    startElementDrag(down(0, 0), livePanel(), e(), layoutOf('G1'))
    walk(0, 0, 57, 33, 20)
    fire('pointerup', 57, 33)
    startElementDrag(down(0, 0), livePanel(), e(), layoutOf('G1'))
    walk(0, 0, -57, -33, 20)
    fire('pointerup', -57, -33)
    const back = valueOf('axes_0.texts_0', 'pos_frac')!
    expect(Math.abs(back[0] - a[0])).toBeLessThan(Q)
    expect(Math.abs(back[1] - a[1])).toBeLessThan(Q)

    await setup('G2', 1.37)
    const picked = ['axes_0.texts_0', 'axes_1.patches_0']
    const drift: number[] = []
    for (let i = 0; i < 20; i++) {
      for (const sign of [1, -1]) {
        const entries = alignEntries(livePanel(), manifestOf('G2'), picked)
        startElementGroupMove(down(0, 0), livePanel(), entries, layoutOf('G2'))
        walk(0, 0, sign * 31.3, sign * -17.7, 5)
        fire('pointerup', sign * 31.3, sign * -17.7)
      }
      for (const g of picked) {
        const v = valueOf(g, 'pos_frac')!
        const a0 = el('G2', g).anchor!
        drift.push(Math.max(Math.abs(v[0] - a0[0]), Math.abs(v[1] - a0[1])))
      }
    }
    // 累计漂移的长期上限：不超过一次 round4 的整步（而不是 20 × 半步）
    expect(Math.max(...drift)).toBeLessThanOrEqual(1e-4 + 1e-12)
  })

  it('Axes 右边手柄拉宽再拉回（逆缩放）：宽度回到 round4 范围内', async () => {
    await setup('G1', 1.5)
    const axes = el('G1', 'axes_0')
    const pos0 = axes.editable.find((f) => f.prop === 'position')!.value as number[]
    startAxesDrag(down(0, 0), livePanel(), axes, layoutOf('G1'), 'e')
    walk(0, 0, -80, 0, 10)
    fire('pointerup', -80, 0)
    startAxesDrag(down(0, 0), livePanel(), axes, layoutOf('G1'), 'e')
    walk(0, 0, 80, 0, 10)
    fire('pointerup', 80, 0)
    const v = valueOf('axes_0', 'position')!
    for (let k = 0; k < 4; k++) expect(Math.abs(v[k] - pos0[k]), `[${k}]`).toBeLessThan(2 * Q)
  })
})

/* =========================== GEO-07 边界与 no-op ========================== */

describe('GEO-07 边界与 no-op：没有位移就什么都不产生；缩到最小不出 0 / NaN', () => {
  const noop = () => {
    expect(overridesNow()).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(svgNode('axes_0.texts_0')?.getAttribute('transform') ?? null).toBeNull()
  }

  it('只 pointerdown / up（含 1px 抖动，低于 2px 阈值）：单拖 / 整组 / Axes 移动 / Axes 缩放 / 成组缩放全是 no-op', async () => {
    await setup('G1', 1.37)
    const t = el('G1', 'axes_0.texts_0')
    const axes = el('G1', 'axes_0')
    startElementDrag(down(10, 10), livePanel(), t, layoutOf('G1'))
    fire('pointermove', 11, 10)
    fire('pointerup', 11, 10)
    noop()
    startAxesDrag(down(10, 10), livePanel(), axes, layoutOf('G1'), 'move')
    fire('pointerup', 10, 10)
    noop()
    startAxesDrag(down(10, 10), livePanel(), axes, layoutOf('G1'), 'se')
    fire('pointermove', 10, 11)
    fire('pointerup', 10, 11)
    noop()

    await setup('G2', 1)
    const group = alignEntries(livePanel(), manifestOf('G2'), ['axes_0.texts_0', 'axes_1.texts_0'])
    startElementGroupMove(down(10, 10), livePanel(), group, layoutOf('G2'))
    fire('pointerup', 10, 10)
    noop()
    const axesGroup = groupOf(alignEntries(livePanel(), manifestOf('G2'), ['axes_0', 'axes_1']))!
    startGroupResize(down(10, 10), livePanel(), axesGroup, layoutOf('G2'), 'se')
    fire('pointerup', 10, 10)
    noop()
  })

  it('Axes 连续缩到最小再放大：尺寸有下限、全程有限正数、可逆', async () => {
    await setup('G1', 0.75)
    const axes = el('G1', 'axes_0')
    for (let i = 0; i < 3; i++) {
      startAxesDrag(down(0, 0), livePanel(), axes, layoutOf('G1'), 'se')
      walk(0, 0, -5000, -5000, 5)
      fire('pointerup', -5000, -5000)
      const v = valueOf('axes_0', 'position')!
      for (const x of v) expect(Number.isFinite(x)).toBe(true)
      expect(v[2]).toBeGreaterThan(0)
      expect(v[3]).toBeGreaterThan(0)
      expect(v[2]).toBeGreaterThanOrEqual(0.05 - Q)
      expect(v[3]).toBeGreaterThanOrEqual(0.05 - Q)
    }
    startAxesDrag(down(0, 0), livePanel(), axes, layoutOf('G1'), 'se')
    walk(0, 0, 200, 150, 5)
    fire('pointerup', 200, 150)
    const v = valueOf('axes_0', 'position')!
    expect(v[2]).toBeGreaterThan(0.05)
    expect(v[3]).toBeGreaterThan(0.05)
    for (const x of v) expect(Number.isFinite(x)).toBe(true)
  })

  it('成组缩放缩到极小再放大：成员不塌成 0、不出 NaN', async () => {
    await setup('G2', 1)
    const pick = () => groupOf(alignEntries(livePanel(), manifestOf('G2'), ['axes_0', 'axes_1']))!
    startGroupResize(down(0, 0), livePanel(), pick(), layoutOf('G2'), 'se')
    walk(0, 0, -9000, -9000, 5)
    fire('pointerup', -9000, -9000)
    for (const g of ['axes_0', 'axes_1']) {
      const v = valueOf(g, 'position')!
      for (const x of v) expect(Number.isFinite(x), g).toBe(true)
      expect(v[2], g).toBeGreaterThan(0)
      expect(v[3], g).toBeGreaterThan(0)
    }
    startGroupResize(down(0, 0), livePanel(), pick(), layoutOf('G2'), 'se')
    walk(0, 0, 300, 200, 5)
    fire('pointerup', 300, 200)
    for (const g of ['axes_0', 'axes_1']) {
      const v = valueOf(g, 'position')!
      for (const x of v) expect(Number.isFinite(x), g).toBe(true)
      expect(v[2], g).toBeGreaterThan(0.01)
    }
    expect(useDocumentStore.getState().past).toHaveLength(2)
  })

  it('pointercancel 中断整组平移：不写、不进历史、不渲染，DOM 还原', async () => {
    await setup('G2', 1)
    const svgBefore = document.querySelector('[data-element-svg="p1"] svg')!.outerHTML
    const group = alignEntries(livePanel(), manifestOf('G2'), ['axes_0.texts_0', 'axes_1.texts_0'])
    startElementGroupMove(down(0, 0), livePanel(), group, layoutOf('G2'))
    walk(0, 0, 40, 40, 10)
    fire('pointercancel', 40, 40)
    expect(overridesNow()).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(document.querySelector('[data-element-svg="p1"] svg')!.outerHTML).toBe(svgBefore)
  })
})

it('渲染键：夹具面板的变体键就是空 overrides（seedExactRender 之后权威就位）', async () => {
  await setup('G1')
  expect(useRenderStore.getState().byKey[renderKeyOf(livePanel())]?.status).toBe('ready')
})
