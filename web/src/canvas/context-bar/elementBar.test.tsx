/**
 * 图内文字的浮动快捷编辑（`ElementQuickActions` 的文字分支）：
 * **与属性页、右键弹层同一个适配器**（ADR 0032）。
 *
 * 要钉住的：
 *   1. 控件按适配器给的能力出——字体 / 字号 / 加粗 / 斜体 / 颜色都在，
 *      不再只有字号 / 加粗 / 颜色三件（那是绕过适配器的第三份实现留下的形状）；
 *   2. 加粗 / 斜体的写入落在 override 上、走离散动作（一条历史）；
 *   3. 显示读的是适配器的值：属性页改了，浮动栏当场就是新值。
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
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementQuickActions } from './ElementBar'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.02, 0.3, 0.07],
  draggable: true,
  editable: [
    f('text', 'text', 'Title here'),
    f('fontsize', 'number', 12, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
    f('color', 'color', '#000000'),
    f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
    f('style', 'enum', 'normal', { options: ['normal', 'italic'] }),
    f('fontfamily', 'enum', 'serif', { options: ['serif', 'sans-serif', 'monospace'] }),
  ],
}

const manifest: Manifest = {
  stem: 'Fig1',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
  ],
}

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const overrideOf = (prop: string) =>
  livePanel().overrides.find((o) => o.gid === 'axes_0.title' && o.prop === prop)?.value

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementQuickActions panel={panel} gid="axes_0.title" />
    </TooltipProvider>
  )
}

async function mount() {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<Harness />)
  })
}

const byLabel = (label: string) =>
  document.querySelector(`[aria-label="${label}"]`) as HTMLElement | null
async function click(label: string) {
  await act(async () => {
    byLabel(label)!.click()
  })
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_element_bar')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig1.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(livePanel()) } })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
  resetPreview()
})

describe('图内文字的浮动栏走排版适配器', () => {
  it('字体 / 字号 / 加粗 / 斜体 / 颜色五件都在——不再是只有三件的第三份实现', async () => {
    await mount()
    expect(byLabel('字体')).not.toBeNull()
    expect(byLabel('加粗')).not.toBeNull()
    expect(byLabel('斜体')).not.toBeNull()
    expect(document.querySelector('input[type="color"], [data-color-field]')).not.toBeNull()
    const size = byLabel('字号') as HTMLInputElement | null
    expect(size).not.toBeNull()
    expect(size!.value).toBe('12')
  })

  it('加粗 / 斜体各是一条离散动作，落成 override', async () => {
    await mount()
    await click('加粗')
    expect(overrideOf('weight')).toBe('bold')
    await click('斜体')
    expect(overrideOf('style')).toBe('italic')
    expect(useDocumentStore.getState().past.length).toBe(2)
    await click('加粗')
    expect(overrideOf('weight')).toBe('normal')
  })

  it('显示读的是同一份值：属性页写的加粗，浮动栏当场就是按下态', async () => {
    await mount()
    expect(byLabel('加粗')!.getAttribute('aria-pressed')).toBe('false')
    await act(async () => {
      setOverride('p1', 'axes_0.title', 'weight', 'bold')
    })
    expect(byLabel('加粗')!.getAttribute('aria-pressed')).toBe('true')
  })
})
