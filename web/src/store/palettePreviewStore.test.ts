/**
 * 配色的画布预览平面（ADR 0210 §六）：多张面板一起预览、换一套先还原、关弹层还原、
 * 选用后挂着等图、新图一到退场、撤销了就地还原、画不了的据实计数。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import {
  clearPalettePreview,
  holdPalettePreview,
  markPalettePreviewUnavailable,
  palettePreviewGen,
  palettePreviewHeld,
  palettePreviewMissed,
  reattachPalettePreview,
  resetPalettePreview,
  settleUnbackedPalettePreview,
  showPalettePreview,
  type PalettePreviewEntry,
} from './palettePreviewStore'
import { findGidNode, findPanelSvg } from './svgPreviewStore'

/** 两张面板，各一份同样的 matplotlib SVG（PanelView 的那层包装） */
function mount(...ids: string[]) {
  document.body.innerHTML = ids.map((id) => `<div data-element-svg="${id}">${MATPLOTLIB_SVG}</div>`).join('')
}

const stroke = (panel: string, gid = 'axes_0.lines_0') =>
  (findGidNode(findPanelSvg(panel), gid)!.querySelector('path') as SVGElement).style.getPropertyValue('stroke')

const line = (value: string, gid = 'axes_0.lines_0') => ({ gid, role: 'line', prop: 'color', value })
const entries = (color: string, ...ids: string[]): PalettePreviewEntry[] =>
  ids.map((panelId) => ({ panelId, patches: [line(color)] }))

let base = ''

beforeEach(() => {
  resetPalettePreview()
  mount('p1', 'p2')
  base = stroke('p1')
})

afterEach(() => resetPalettePreview())

describe('预览：两张面板一起画，只动 DOM', () => {
  it('show 只登记、不碰 DOM；PanelView 挂好 SVG 后的 reattach 才画上去，两张都画', () => {
    showPalettePreview(entries('#123456', 'p1', 'p2'))
    expect(stroke('p1')).toBe(base)
    expect(palettePreviewGen('p1')).toBeGreaterThan(0)
    expect(palettePreviewGen('p3')).toBe(0)
    reattachPalettePreview('p1', 'k1')
    reattachPalettePreview('p2', 'k2')
    expect(stroke('p1')).not.toBe(base)
    expect(stroke('p2')).not.toBe(base)
    expect(palettePreviewMissed()).toBe(0)
  })

  it('换一套：先还原再画，base 仍是 matplotlib 给的那份（关弹层能回到原样）', () => {
    showPalettePreview(entries('#123456', 'p1'))
    reattachPalettePreview('p1', 'k1')
    const first = stroke('p1')
    showPalettePreview(entries('#abcdef', 'p1'))
    expect(stroke('p1')).toBe(base)
    reattachPalettePreview('p1', 'k1')
    expect(stroke('p1')).not.toBe(first)
    clearPalettePreview()
    expect(stroke('p1')).toBe(base)
    expect(palettePreviewGen('p1')).toBe(0)
  })

  it('React 只是重跑组件（同一版、节点没换）：不重画，不把预览色当成 base', () => {
    showPalettePreview(entries('#123456', 'p1'))
    reattachPalettePreview('p1', 'k1')
    reattachPalettePreview('p1', 'k1')
    reattachPalettePreview('p1', 'k1')
    clearPalettePreview()
    expect(stroke('p1')).toBe(base)
  })

  it('同一版被重新插进 DOM：在新节点上重画', () => {
    showPalettePreview(entries('#123456', 'p1'))
    reattachPalettePreview('p1', 'k1')
    const painted = stroke('p1')
    mount('p1', 'p2')
    expect(stroke('p1')).toBe(base)
    reattachPalettePreview('p1', 'k1')
    expect(stroke('p1')).toBe(painted)
  })
})

describe('画不了的据实计数', () => {
  it('gid 在 SVG 里找不到 / role 没有适配器：计入 missed，其余照画', () => {
    showPalettePreview([
      {
        panelId: 'p1',
        patches: [line('#123456'), line('#123456', 'axes_0.no_such'), { gid: 'axes_0.lines_1', role: 'legend', prop: 'color', value: '#123456' }],
      },
    ])
    reattachPalettePreview('p1', 'k1')
    expect(stroke('p1')).not.toBe(base)
    expect(palettePreviewMissed()).toBe(2)
  })

  it('面板没有确切的矢量 SVG：整张面板计入 missed；还没回话的不算', () => {
    showPalettePreview(entries('#123456', 'p1', 'p2'))
    expect(palettePreviewMissed()).toBe(0)
    markPalettePreviewUnavailable('p2')
    expect(palettePreviewMissed()).toBe(1)
  })
})

describe('选用：挂着等图，新图到了退场', () => {
  it('hold 后关弹层不还原；同一版的 reattach 不退场；新一版到了退场且不动新 DOM', () => {
    showPalettePreview(entries('#123456', 'p1', 'p2'))
    reattachPalettePreview('p1', 'k1')
    // p2 还没挂上 SVG：选用时没东西可挂，直接放掉
    holdPalettePreview()
    expect(palettePreviewHeld()).toBe(true)
    expect(palettePreviewGen('p2')).toBe(0)
    const painted = stroke('p1')
    clearPalettePreview()
    expect(stroke('p1')).toBe(painted)
    reattachPalettePreview('p1', 'k1')
    expect(palettePreviewGen('p1')).toBeGreaterThan(0)
    // 新一版（引擎按正式颜色画好的）换上来
    mount('p1', 'p2')
    reattachPalettePreview('p1', 'k1-new')
    expect(palettePreviewGen('p1')).toBe(0)
    expect(palettePreviewHeld()).toBe(false)
    expect(stroke('p1')).toBe(base)
  })

  it('等图期间撤销（文档里没有这几条了）：就地还原；还在的不动', () => {
    showPalettePreview(entries('#123456', 'p1'))
    reattachPalettePreview('p1', 'k1')
    holdPalettePreview()
    settleUnbackedPalettePreview('p1', [{ gid: 'axes_0.lines_0', prop: 'color', value: '#123456' }])
    expect(palettePreviewGen('p1')).toBeGreaterThan(0)
    settleUnbackedPalettePreview('p1', [])
    expect(palettePreviewGen('p1')).toBe(0)
    expect(stroke('p1')).toBe(base)
  })

  it('没在等图（只是预览中）的 settle 不动它：预览本来就不在文档里', () => {
    showPalettePreview(entries('#123456', 'p1'))
    reattachPalettePreview('p1', 'k1')
    settleUnbackedPalettePreview('p1', [])
    expect(palettePreviewGen('p1')).toBeGreaterThan(0)
  })
})
