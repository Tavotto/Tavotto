/**
 * 图内文字的公式块在属性页文字框里排成真公式（ADR 0152 后续）：宿主把 manifest 记的 `math_face`
 * 作为 `face:<族>` 传给文字框；字体族有 override 时随 override。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'
import { __resetMathForTests } from '@/lib/mathtext'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const titleEl = (extra: Partial<ManifestElement> = {}): ManifestElement => ({
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.02, 0.3, 0.07],
  draggable: true,
  editable: [
    f('text', 'text', 'rate $k_{12}$'),
    f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
    f('fontfamily', 'enum', 'Times New Roman', { options: ['Times New Roman', 'Arial'] }),
  ],
  ...extra,
})
const manifestOf = (el: ManifestElement): Manifest => ({
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [{ gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false }, el],
})

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 101.6, h: 76.2, fileId: 'Fig1.pdf', fileKind: 'pdf',
    nativeW: 101.6, nativeH: 76.2, script: 'fig.py', overrides: [],
  }) as unknown as PanelObject
const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

let root: Root
let host: HTMLDivElement
let families: string[]

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount(el: ManifestElement, overrides: PanelObject['overrides'] = []) {
  const manifest = manifestOf(el)
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fig_math')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push({ ...panelOf(), overrides })
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig1.pdf', manifest, svg: MATPLOTLIB_SVG, rev: 1, status: 'ready', lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(livePanel()) } })
  useDocumentStore.setState({ past: [], future: [] })
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  host = document.createElement('div')
  document.body.appendChild(host)
  const svgHost = document.createElement('div')
  svgHost.setAttribute('data-element-svg', 'p1')
  svgHost.innerHTML = MATPLOTLIB_SVG
  document.body.appendChild(svgHost)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
  await act(async () => void (await new Promise((r) => setTimeout(r, 0))))
}

beforeEach(() => {
  engineRender.mockReset()
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  __resetMathForTests()
  families = []
  globalThis.fetch = (async (_u: string, init?: RequestInit) => {
    const items = (JSON.parse(String(init?.body ?? '{"items":[]}')) as { items: { src: string; family: string }[] }).items
    families.push(...items.map((i) => i.family))
    const ok = { ok: true, width: 2, ascent: 0.8, descent: 0.2, segments: [['M', 0, 0], ['L', 2, 0], ['Z']] }
    return new Response(JSON.stringify({ ok: true, results: items.map(() => ok) }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
  resetPreview()
})

const chip = () => host.querySelector('[data-testid="figure-text-content"] [data-rt-math] [data-math-state]')

describe('属性页图内文字的公式块', () => {
  it('按 manifest 的 math_face 排：math_face = STIXGeneral → face:STIXGeneral，画成轮廓', async () => {
    await mount(titleEl({ math_face: 'STIXGeneral' }))
    expect(families).toEqual(['face:STIXGeneral'])
    expect(chip()?.getAttribute('data-math-state')).toBe('ok')
  })

  it('manifest 没记 math_face：matplotlib 默认的 DejaVu Sans', async () => {
    await mount(titleEl())
    expect(families).toEqual(['face:DejaVu Sans'])
  })

  it('字体族有 override：公式字体集随它（比 manifest 新）', async () => {
    await mount(titleEl({ math_face: 'DejaVu Sans' }), [{ gid: 'axes_0.title', prop: 'fontfamily', value: 'Arial' }] as PanelObject['overrides'])
    expect(families).toEqual(['face:Arial'])
  })
})
