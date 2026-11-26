/**
 * 推荐配色的界面与写回（ADR 0210 方向 A）：
 *
 * 1. 入口默认关：样式面板画布档里没有「配色」一节；开发键打开后才有。
 * 2. 选用一套方案 = 一次 commit：两张图里同名的组同色，色键表进文档，⌘Z 一次全部回去。
 * 3. 手改过颜色的系列不动，并说出来；画布样式写的颜色归配色接手，从样式登记里注销。
 * 4. 对照组只建议中性灰，点了才生效；同名不同身份的组第一次上色时问一次要不要并。
 * 5. 选过推荐配色的画布，样式的配色循环不再上色（`planStyle`）。
 * 6. 弹层里 ↑↓ 只改底部预览，不写文档；色觉预览只改显示。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { Manifest, ManifestElement } from '@/lib/api'
import { paletteById } from '@/lib/palette/catalog'
import { cvdFilterValues } from '@/lib/palette/cvd'
import { PALETTE_UI_DEV_KEY, RECOMMENDED_PALETTE_UI, paletteUi } from '@/lib/paletteUi'
import { colorForSlot } from '@/lib/palette/rank'
import { planStyle } from '@/lib/stylePresets'
import { useAssetStore } from '@/store/assetStore'
import { cvdFilterRef, useCvdPreviewStore } from '@/store/cvdPreviewStore'
import { useDocumentStore } from '@/store/documentStore'
import { applyPaletteScheme, canvasPaletteState, planPaletteSchemePreview, setPaletteGroupNeutral } from '@/store/paletteActions'
import {
  markPalettePreviewUnavailable,
  palettePreviewGen,
  palettePreviewHeld,
  resetPalettePreview,
} from '@/store/palettePreviewStore'
import { useProfileStore } from '@/store/profileStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject, type PanelOverride } from '@/types/document'
import { CanvasPaletteSection } from './CanvasPaletteSection'
import { CanvasStyleLayer } from './CanvasStyleLayer'

// 这里只看写回与界面；真去渲染会拿桩出来的空回复覆盖掉种好的 manifest
const { rendered } = vi.hoisted(() => ({ rendered: [] as string[] }))
vi.mock('@/store/renderScheduler', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/renderScheduler')>()),
  requestRender: (p: { id: string }) => void rendered.push(p.id),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

/** 假的身份摘要：同名同摘要、不含原文（真摘要是 sha256 的前 16 位） */
const id = (label: string) => {
  let h = 2166136261
  for (const ch of label) h = Math.imul(h ^ ch.codePointAt(0)!, 16777619) >>> 0
  return `l1:${h.toString(16).padStart(8, '0')}${(h ^ 0xa5a5a5a5).toString(16).padStart(8, '0')}`
}

const line = (gid: string, label: string): ManifestElement =>
  ({
    gid,
    role: 'line',
    label: gid,
    identity: id(label),
    bbox: [0, 0, 1, 1],
    draggable: false,
    editable: [
      { prop: 'color', type: 'color', value: '#1f77b4' },
      { prop: 'label', type: 'text', value: label },
    ],
  }) as unknown as ManifestElement

const manifest = (labels: string[]): Manifest =>
  ({
    stem: 'f',
    size_mm: [80, 60],
    elements: [
      { gid: 'axes_0', role: 'axes', label: 'ax', bbox: [0, 0, 1, 1], editable: [], draggable: false },
      ...labels.map((l, i) => line(`axes_0.lines_${i}`, l)),
    ],
  }) as unknown as Manifest

const panel = (pid: string, overrides: PanelOverride[] = []): PanelObject => ({
  id: pid,
  type: 'panel',
  fileId: `${pid}.pdf`,
  fileKind: 'pdf',
  script: `${pid}.py`,
  name: pid,
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  overrides,
})

const s = () => useDocumentStore.getState()
const panelOf = (pid: string) => s().doc.objects.find((o) => o.id === pid) as PanelObject
const colorOf = (pid: string, gid: string) =>
  panelOf(pid).overrides.filter((o) => o.gid === gid && o.prop === 'color').at(-1)?.value

/** 两张图：A 里 Control、Drug A；B 里顺序反过来（同名的组跨图同色，与顺序无关） */
async function seed(opts: { overridesB?: PanelOverride[]; labelsB?: string[] } = {}) {
  await s().switchDocument(emptyProject(), `d_palette_${Math.random().toString(36).slice(2)}`)
  const a = panel('A')
  const b = panel('B', opts.overridesB ?? [])
  s().commit(literal('准备'), (d) => {
    d.objects = [{ ...a }, { ...b }]
  })
  useAssetStore.setState({ byId: { 'A.pdf': { id: 'A.pdf', mtime: 1 }, 'B.pdf': { id: 'B.pdf', mtime: 1 } } } as never)
  seedExactRender(panelOf('A'), manifest(['Control', 'Drug A']))
  seedExactRender(panelOf('B'), manifest(opts.labelsB ?? ['Drug A', 'Control']))
}

let container: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]

beforeEach(() => {
  rendered.length = 0
  localStorage.removeItem(PALETTE_UI_DEV_KEY)
  useCvdPreviewStore.setState({ vision: 'normal' })
  useProfileStore.setState({ styles: [], loaded: true, error: null })
  useUiStore.setState({ confirm: null })
  resetPalettePreview()
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  localStorage.removeItem(PALETTE_UI_DEV_KEY)
})

describe('入口开关', () => {
  it('默认关：画布档里没有「配色」一节；开发键打开才有', async () => {
    expect(RECOMMENDED_PALETTE_UI).toBe(false)
    expect(paletteUi()).toBe(false)
    await seed()
    await mount(<CanvasStyleLayer />)
    expect(q('[data-palette-section]')).toBeNull()
    await act(async () => root.unmount())
    container.remove()

    localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
    expect(paletteUi()).toBe(true)
    await mount(<CanvasStyleLayer />)
    expect(q('[data-palette-section]')).not.toBeNull()
  })
})

describe('选用方案：一次 commit、同名同色', () => {
  it('两张图里同名的组同色，色键表进文档，撤销一次全部回去', async () => {
    await seed()
    const before = JSON.stringify(s().doc.objects)
    const r = applyPaletteScheme('okabe-ito')
    expect(r).toEqual({ written: 4, skipped: 0, uncolored: 0 })
    const okabe = paletteById('okabe-ito')!.colors
    // 槽按第一次见到的顺序分：Control = 0，Drug A = 1；B 里顺序反过来也同色
    expect(colorOf('A', 'axes_0.lines_0')).toBe(okabe[0])
    expect(colorOf('A', 'axes_0.lines_1')).toBe(okabe[1])
    expect(colorOf('B', 'axes_0.lines_1')).toBe(okabe[0])
    expect(colorOf('B', 'axes_0.lines_0')).toBe(okabe[1])
    const table = s().doc.colorKeys!
    expect(table.scheme).toBe('okabe-ito')
    expect(table.keys.map((k) => k.slot)).toEqual([0, 1])
    expect(table.owned).toHaveLength(4)
    expect([...new Set(rendered)].sort()).toEqual(['A', 'B'])
    // 文档里不存 label 原文
    expect(JSON.stringify(table)).not.toContain('Control')

    s().undo()
    expect(JSON.stringify(s().doc.objects)).toBe(before)
    expect(s().doc.colorKeys).toBeUndefined()
  })

  it('换方案只换颜色，组与槽不动；配色自己写的颜色不算手改', async () => {
    await seed()
    applyPaletteScheme('okabe-ito')
    const r = applyPaletteScheme('tol.bright')
    expect(r?.skipped).toBe(0)
    const tol = paletteById('tol.bright')!.colors
    expect(colorOf('B', 'axes_0.lines_1')).toBe(tol[0])
    expect(s().doc.colorKeys!.keys.map((k) => k.slot)).toEqual([0, 1])
  })

  it('手改过颜色的系列不动，并数出来', async () => {
    await seed({ overridesB: [{ gid: 'axes_0.lines_1', prop: 'color', value: '#ff00ff' }] })
    const r = applyPaletteScheme('okabe-ito')
    expect(r?.skipped).toBe(1)
    expect(colorOf('B', 'axes_0.lines_1')).toBe('#ff00ff')
  })

  it('画布样式写的颜色归配色接手，并从样式的登记里注销', async () => {
    await seed({ overridesB: [{ gid: 'axes_0.lines_1', prop: 'color', value: '#123456' }] })
    s().commit(literal('样式登记'), (d) => {
      d.style = {
        id: 's1',
        name: 'S',
        snapshot: {} as never,
        owned: { 'B@B.pdf': { 'axes_0.lines_1': { color: { value: '#123456' } } } },
      } as never
    })
    const r = applyPaletteScheme('okabe-ito')
    expect(r?.skipped).toBe(0)
    expect(colorOf('B', 'axes_0.lines_1')).toBe(paletteById('okabe-ito')!.colors[0])
    expect(s().doc.style?.owned).toBeUndefined()
  })
})

describe('样式登记的让渡不看颜色变没变', () => {
  it('样式写的颜色恰好就是这套方案那一格：不写 patch，但同样从样式的登记里注销（Codex #2231）', async () => {
    // 先算出 B 的 lines_1（Control）在这套方案里落到哪一格颜色
    await seed()
    const g = canvasPaletteState(s().doc).groups.find((x) =>
      x.series.some((r) => r.panelId === 'B' && r.gid === 'axes_0.lines_1'),
    )!
    const color = colorForSlot(g.slot, paletteById('okabe-ito')!)!
    await seed({ overridesB: [{ gid: 'axes_0.lines_1', prop: 'color', value: color }] })
    s().commit(literal('样式登记'), (d) => {
      d.style = {
        id: 's1',
        name: 'S',
        snapshot: {} as never,
        owned: { 'B@B.pdf': { 'axes_0.lines_1': { color: { value: color } } } },
      } as never
    })
    const linesOf = () => panelOf('B').overrides.filter((o) => o.gid === 'axes_0.lines_1').length
    const before = linesOf()
    const r = applyPaletteScheme('okabe-ito')
    expect(r?.skipped).toBe(0)
    expect(colorOf('B', 'axes_0.lines_1')).toBe(color)
    // 颜色没变：这一格不写 patch
    expect(linesOf()).toBe(before)
    // 归配色了：样式那边不再登记，脚本原色变了样式也不会让位删掉它
    expect(s().doc.style?.owned).toBeUndefined()
    expect(s().doc.colorKeys?.owned.some((o) => o.panel === 'B' && o.gid === 'axes_0.lines_1')).toBe(true)
    // 一步撤销：登记回来
    s().undo()
    expect(s().doc.style?.owned?.['B@B.pdf']?.['axes_0.lines_1']?.color).toBeTruthy()
  })
})

describe('柱形系列：单根柱手改过的不被清掉', () => {
  it('系列的填充色盖着各根柱；某根柱手改过，整条系列跳过，那根柱的颜色还在（Codex #2271）', async () => {
    await seed()
    const bars = (gid: string, label: string) =>
      ({
        gid,
        role: 'bar_series',
        label: gid,
        identity: line(gid, label).identity,
        bbox: [0, 0, 1, 1],
        draggable: false,
        editable: [
          { prop: 'facecolor', type: 'color', value: '#1f77b4' },
          { prop: 'label', type: 'text', value: label },
        ],
        alias_covers: { facecolor: { facecolor: [`${gid}.patch_0`, `${gid}.patch_1`] } },
      }) as unknown as ManifestElement
    const bar = (gid: string) =>
      ({ gid, role: 'bar', label: gid, bbox: [0, 0, 1, 1], draggable: false, editable: [{ prop: 'facecolor', type: 'color', value: '#1f77b4' }] }) as unknown as ManifestElement
    s().commit(literal('手改一根柱'), (d) => {
      const b = d.objects.find((o) => o.id === 'B') as PanelObject
      b.overrides = [{ gid: 'axes_0.bars_0.patch_1', prop: 'facecolor', value: '#ff00ff' }]
    })
    seedExactRender(panelOf('B'), {
      stem: 'f',
      size_mm: [80, 60],
      elements: [
        { gid: 'axes_0', role: 'axes', label: 'ax', bbox: [0, 0, 1, 1], editable: [], draggable: false },
        bars('axes_0.bars_0', 'Control'),
        bar('axes_0.bars_0.patch_0'),
        bar('axes_0.bars_0.patch_1'),
        bars('axes_0.bars_1', 'Drug A'),
        bar('axes_0.bars_1.patch_0'),
        bar('axes_0.bars_1.patch_1'),
      ],
    } as unknown as Manifest)
    const r = applyPaletteScheme('okabe-ito')
    expect(r?.skipped).toBe(1)
    const face = (gid: string) => panelOf('B').overrides.filter((o) => o.gid === gid && o.prop === 'facecolor').at(-1)?.value
    // 用户那根柱的颜色还在，它所在的系列没写
    expect(face('axes_0.bars_0.patch_1')).toBe('#ff00ff')
    expect(face('axes_0.bars_0')).toBeUndefined()
    // 另一条没人动过的柱形系列照常上色
    expect(face('axes_0.bars_1')).toBeTruthy()
  })
})

describe('中性对照与合并只建议', () => {
  it('Control 只是建议中性灰；点了才变灰，再点回到数字槽', async () => {
    await seed()
    applyPaletteScheme('okabe-ito')
    const st = canvasPaletteState(s().doc)
    const control = st.groups.find((g) => g.name === 'Control')!
    expect(control.suggestNeutral).toBe(true)
    expect(control.slot).toBe(0)
    setPaletteGroupNeutral(control.key, true)
    expect(colorOf('A', 'axes_0.lines_0')).toBe('#A6A6A6')
    expect(colorOf('B', 'axes_0.lines_1')).toBe('#A6A6A6')
    setPaletteGroupNeutral(control.key, false)
    expect(colorOf('A', 'axes_0.lines_0')).toBe(paletteById('okabe-ito')!.colors[0])
  })

  it('第一次上色时同名不同写法的组问一次；点「并成一组」就同色', async () => {
    localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
    await seed({ labelsB: ['Drug A', 'control'] })
    await mount(<CanvasPaletteSection />)
    expect(qa('[data-palette-group]')).toHaveLength(3)
    await act(async () => {
      q('[data-select-trigger]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {
      q('[data-select-option="okabe-ito"]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const req = useUiStore.getState().confirm
    expect(req, '问了一次').not.toBeNull()
    await act(async () => req!.resolve(true))
    expect(colorOf('B', 'axes_0.lines_1')).toBe(colorOf('A', 'axes_0.lines_0'))
    expect(qa('[data-palette-group]')).toHaveLength(2)
  })
})

describe('弹层预览与色觉预览只改显示', () => {
  it('↑↓ 只换底部预览，不写文档；回车才选用', async () => {
    localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
    await seed()
    await mount(<CanvasPaletteSection />)
    const before = JSON.stringify(s().doc)
    // 还没选方案：组只列出来、圆点是虚线（不拿一套没生效的颜色冒充现状），上方一行汇总
    expect(q('[data-palette-summary]')?.textContent).toBe('2 组 · 2 张图')
    expect(qa('[data-palette-group] [data-palette-dot]').every((d) => !d.style.background)).toBe(true)
    await act(async () => {
      q('[data-select-trigger]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(q('[data-palette-preview]')).not.toBeNull()
    expect(qa('[data-select-group]').map((e) => e.dataset.selectGroup)[0]).toBe('适合这张画布')
    // 每一套写出来源（右端、说明色）；键位提示一直在弹层底部
    expect(q('[data-palette-option="okabe-ito"] [data-palette-source]')?.textContent).toBeTruthy()
    expect(q('[data-palette-footer]')?.textContent).toContain('↑↓')
    const search = q<HTMLInputElement>('[data-select-search]')!
    await act(async () => {
      search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    expect(JSON.stringify(s().doc)).toBe(before)
    const activeId = q('[data-select-option][data-active]')!.getAttribute('data-select-option')!
    await act(async () => {
      search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(s().doc.colorKeys?.scheme).toBe(activeId)
    expect(q('[data-palette-verdict]')).not.toBeNull()
  })

  it('↑↓ 时两张图一起在画布上预览（选用会写的就是预览的那些格）；Esc 还原，回车后挂着等图', async () => {
    localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
    await seed()
    await mount(<CanvasPaletteSection />)
    await act(async () => {
      q('[data-select-trigger]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const search = q<HTMLInputElement>('[data-select-search]')!
    const key = (k: string) =>
      act(async () => {
        search.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))
      })
    await key('ArrowDown')
    const activeId = q('[data-select-option][data-active]')!.getAttribute('data-select-option')!
    expect(palettePreviewGen('A')).toBeGreaterThan(0)
    expect(palettePreviewGen('B')).toBeGreaterThan(0)
    // 预览的格子 = 选用会写的格子，每格带上 role（决定改描边还是填充）
    const plan = planPaletteSchemePreview(activeId)
    expect(plan.map((e) => e.panelId).sort()).toEqual(['A', 'B'])
    expect(plan.flatMap((e) => e.patches).every((p) => p.role === 'line' && p.prop === 'color')).toBe(true)
    // 一张图没有可画的矢量 SVG：弹层据实说出来，不假装画上了
    await act(async () => markPalettePreviewUnavailable('B'))
    expect(q('[data-palette-preview-missed]')?.textContent).toContain('2')
    await key('Escape')
    expect(palettePreviewGen('A')).toBe(0)
    expect(palettePreviewGen('B')).toBe(0)

    await act(async () => {
      q('[data-select-trigger]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const search2 = q<HTMLInputElement>('[data-select-search]')!
    await act(async () => {
      search2.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    await act(async () => {
      search2.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(s().doc.colorKeys?.scheme).toBeTruthy()
    // 测试里没有 PanelView 挂 SVG，没画上去的面板选用时直接放掉：不留一个挂空的预览
    expect(palettePreviewHeld()).toBe(false)
    expect(palettePreviewGen('A')).toBe(0)
  })

  it('色觉预览挂的是滤镜，与模拟同一个矩阵；离开这一节就回到正常色觉', async () => {
    expect(cvdFilterRef('normal')).toBeUndefined()
    expect(cvdFilterRef('protanomaly')).toBe('url(#cvd-preview-protanomaly)')
    const v = cvdFilterValues('protanomaly').split(' ').map(Number)
    expect(v).toHaveLength(20)
    expect(v.slice(0, 3)).toEqual([0.152286, 1.052583, -0.204868])
    expect(v.slice(15)).toEqual([0, 0, 0, 1, 0])

    localStorage.setItem(PALETTE_UI_DEV_KEY, '1')
    await seed()
    await mount(<CanvasPaletteSection />)
    await act(async () => useCvdPreviewStore.getState().setVision('deuteranomaly'))
    expect(q('[data-palette-vision-hint]')).not.toBeNull()
    await act(async () => root.unmount())
    expect(useCvdPreviewStore.getState().vision).toBe('normal')
  })
})

describe('样式的配色循环让位', () => {
  it('选过推荐配色的画布，样式的 palette 不再上色；没选过的照旧', async () => {
    await seed()
    const preset = { name: 'p', element: {}, palette: ['#111111', '#222222'] }
    const manifestOf = (p: PanelObject) => (p.id === 'A' ? manifest(['Control', 'Drug A']) : manifest(['Drug A', 'Control']))
    const plain = planStyle(preset, [panelOf('A')], manifestOf, s().doc, false)
    expect(plain.panels[0].patches.map((p) => p.value)).toEqual(['#111111', '#222222'])
    applyPaletteScheme('okabe-ito')
    const after = planStyle(preset, [panelOf('A')], manifestOf, s().doc, false)
    expect(after.panels[0].patches).toEqual([])
  })
})
