/**
 * 数据系列类角色的属性面板（审计 P2 T11 / T15 / T16 / T19 / T20 / T21）：
 * 整张图、曲线、散点、柱形、误差棒、填充区域。字段形状与 engine/manifest.py
 * 各 `_*_fields` 同形；写入经真实的 documentStore，断言落在 override 上。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'

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

/* -------------------------------- 测试数据 -------------------------------- */

export const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const num = (prop: string, value: number, extra: Record<string, unknown> = {}) =>
  f(prop, 'number', value, { min: 0, max: 8, step: 0.1, unit: 'pt', ...extra })
const alpha = (value: number) => f('alpha', 'number', value, { min: 0, max: 1, step: 0.05 })

/** 与 `_line_fields` 同形 */
export const lineFields = (over: { marker?: string } = {}): EditableField[] => [
  f('label', 'text', 'Linear fit'),
  f('color', 'color', '#c0562a'),
  num('linewidth', 1.1, { min: 0.1 }),
  f('linestyle', 'enum', '-', { options: ['-', '--', ':', '-.'] }),
  alpha(0.75),
  f('visible', 'bool', true),
  f('marker', 'enum', over.marker ?? 'None', {
    options: ['None', 'o', 's', 'D', '^', 'v', '<', '>', 'x', '+', '*', '.'],
    group: '线条与标记',
  }),
  num('markersize', 6, { max: 20, step: 0.5, group: '线条与标记' }),
  f('markerfacecolor', 'color', '#c0562a', { group: '线条与标记' }),
  f('markeredgecolor', 'color', '#c0562a', { group: '线条与标记' }),
  f('zorder', 'number', 2, { min: -5, max: 50, step: 1, group: '排列' }),
]

/** 与 `_collection_fields(label=True)` 的散点形状同形 */
export const scatterFields = (over: { marker?: string; hint?: string } = {}): EditableField[] => [
  f('label', 'text', 'Observed'),
  f('facecolor', 'color', '#1b3a6b'),
  f('size', 'number', 12, { min: 1, max: 400, step: 1, unit: 'pt²' }),
  f('marker', 'enum', over.marker ?? 'original', {
    options: ['original', 'o', 's', 'D', '^', 'v', '<', '>', 'x', '+', '*', '.', 'p', 'h'],
    ...(over.hint ? { hint: over.hint } : {}),
  }),
  f('edgecolor', 'color', '#1b3a6b'),
  num('linewidth', 1.1),
  f('linestyle', 'enum', '-', { options: ['-', '--', '-.', ':'], group: '线条与填充' }),
  alpha(0.75),
  f('visible', 'bool', true),
  f('hatch', 'enum', '', { options: ['', '/', '\\\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx'], group: '线条与填充' }),
]

/** 与 `_bar_series_fields` 同形 */
export const barSeriesFields = (): EditableField[] => [
  f('label', 'text', 'Measurements'),
  f('facecolor', 'color', '#47749e'),
  f('edgecolor', 'color', '#000000'),
  num('linewidth', 1, { max: 5 }),
  f('bar_width', 'number', 0.8, { min: 0.01, max: 5, step: 0.02, unit: '数据单位' }),
  alpha(1),
  f('visible', 'bool', true),
  f('zorder', 'number', 2, { min: -5, max: 50, step: 1, group: '排列' }),
]

/** 与 `_errorbar_fields` 同形 */
export const errorbarFields = (): EditableField[] => [
  f('color', 'color', '#222222'),
  num('linewidth', 1.5, { min: 0.1, max: 5 }),
  num('capsize', 8, { max: 15, step: 0.5 }),
  num('cap_thickness', 1, { min: 0.1, max: 5 }),
  alpha(1),
  f('visible', 'bool', true),
]

/** 与 `_collection_fields(label=False)` 的填充区域形状同形 */
export const fillFields = (): EditableField[] => [
  f('facecolor', 'color', '#1f77b4'),
  f('edgecolor', 'color', '#000000'),
  num('linewidth', 1),
  f('linestyle', 'enum', '-', { options: ['-', '--', '-.', ':'], group: '线条与填充' }),
  alpha(0.25),
  f('visible', 'bool', true),
  f('hatch', 'enum', '', { options: ['', '/', '\\\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx'], group: '线条与填充' }),
]

/** 与 `_fields_for(figure)` 同形 */
export const figureFields = (over: { transparent?: boolean } = {}): EditableField[] => [
  f('size_mm', 'pair', [80, 57.6], { unit: 'mm' }),
  f('facecolor', 'color', '#ffffff', { group: '背景' }),
  f('transparent', 'bool', over.transparent ?? false, { group: '背景' }),
]

export const elementOf = (
  gid: string,
  role: string,
  label: string,
  editable: EditableField[],
  extra: Partial<ManifestElement> = {},
): ManifestElement =>
  ({ gid, role, label, bbox: [0.2, 0.2, 0.6, 0.6], draggable: false, editable, ...extra }) as ManifestElement

export const makeManifest = (elements: ManifestElement[]): Manifest =>
  ({
    rev: 1,
    size_mm: [80, 57.6],
    elements: [
      { gid: 'figure', role: 'figure', label: '整张图', bbox: [0, 0, 1, 1], editable: figureFields(), draggable: false },
      { gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.1, 0.8, 0.8], editable: [], draggable: true },
      ...elements,
    ],
  }) as unknown as Manifest

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 57.6,
    fileId: 'Fig2.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    script: 'fig.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
export const overrideOf = (gid: string, prop: string) =>
  livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value

/* --------------------------------- 挂载 ---------------------------------- */

let root: Root
let host: HTMLDivElement
let manifest: Manifest = makeManifest([])

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

export function seedRender(m: Manifest) {
  manifest = m
  engineRender.mockResolvedValue({ rev: 2, manifest: m, svg: MATPLOTLIB_SVG, warnings: [] })
  useRenderStore.getState().patch(renderKeyOf(panelOf()), {
    fileId: 'Fig2.pdf',
    manifest: m,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig2.pdf': renderKeyOf(panelOf()) } })
}

export async function mount(gids: string[]) {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: gids })
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
}

const textOf = () => host.textContent ?? ''
const buttons = () => Array.from(host.querySelectorAll('button'))
const byText = (text: string) => buttons().find((b) => b.textContent?.trim() === text)
const row = (prop: string) => host.querySelector<HTMLElement>(`[data-prop="${prop}"]`)
const inputIn = (prop: string) => row(prop)?.querySelector<HTMLInputElement>('input') ?? null

async function typeNumber(input: HTMLInputElement, text: string) {
  await act(async () => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
}

async function openMore() {
  const more = byText('更多')
  if (more && more.getAttribute('aria-expanded') !== 'true') {
    await act(async () => {
      more.click()
    })
  }
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  document.body.innerHTML = ''
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_series')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  resetPreview()
  useUiStore.setState({ selectedGids: [] })
})

/* ------------------------------ 透明度百分比 ------------------------------ */

describe('透明度按百分比显示与输入（T16 / T20）', () => {
  it('曲线的透明度显示 75 与 %，输入 40 写回 override 0.4', async () => {
    seedRender(makeManifest([elementOf('axes_0.lines_0', 'line', '曲线 “Linear fit”', lineFields())]))
    await mount(['axes_0.lines_0'])
    await openMore()
    const input = inputIn('alpha')!
    expect(input).toBeTruthy()
    expect(input.value).toBe('75')
    expect(row('alpha')!.textContent).toContain('%')
    // 界面上不出现 0.75 这种小数
    expect(row('alpha')!.textContent).not.toContain('0.75')
    await typeNumber(input, '40')
    expect(overrideOf('axes_0.lines_0', 'alpha')).toBe(0.4)
  })

  it('批量改两条曲线的透明度也是百分比，写回 0–1', async () => {
    seedRender(
      makeManifest([
        elementOf('axes_0.lines_0', 'line', '曲线 “a”', lineFields()),
        elementOf('axes_0.lines_1', 'line', '曲线 “b”', lineFields()),
      ]),
    )
    await mount(['axes_0.lines_0', 'axes_0.lines_1'])
    const input = host.querySelector<HTMLInputElement>('input[aria-label="透明度"]')!
    expect(input).toBeTruthy()
    expect(input.value).toBe('75')
    await typeNumber(input, '30')
    expect(overrideOf('axes_0.lines_0', 'alpha')).toBe(0.3)
    expect(overrideOf('axes_0.lines_1', 'alpha')).toBe(0.3)
  })
})

export { textOf, byText, row, inputIn, typeNumber, openMore, host as hostRef }
