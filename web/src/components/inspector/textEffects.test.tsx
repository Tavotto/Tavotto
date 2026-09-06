/**
 * 图内文字的背景 / 描边：**关着只给「＋添加」，开了才铺参数**（审计 T14，
 * 与画布文字 `TextSection` 同一种操作模式）。
 *
 * 要钉住的：
 *   1. 开关关着时「更多」里只有「添加背景」「添加描边」两条入口，从属字段不出现；
 *   2. 点「添加背景」= 一条 override（bbox_visible=true）、一条历史，从属字段随即铺开；
 *   3. 关掉 = 开关写 false **并清掉从属字段的 override**，仍是一条历史；参数收起；
 *   4. 用户改过的从属字段在开关关着时照旧可见（override 不因折叠而不可发现），
 *      关掉动作把它清掉之后才收起——两条规则各说各的，不互相压扁。
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

/** 与 engine/manifest.py 的 _text_fields 同形：背景七条 + 描边三条 */
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
    f('bbox_visible', 'bool', false, { group: '背景' }),
    f('bbox_facecolor', 'color', '#FFFFFF', { group: '背景' }),
    f('bbox_alpha', 'number', 1, { min: 0, max: 1, step: 0.05, group: '背景' }),
    f('bbox_edgecolor', 'color', '#000000', { group: '背景' }),
    f('bbox_linewidth', 'number', 0, { min: 0, max: 3, step: 0.25, unit: 'pt', group: '背景' }),
    f('bbox_pad', 'number', 0.3, { min: 0, max: 2, step: 0.05, group: '背景' }),
    f('bbox_rounded', 'bool', false, { group: '背景' }),
    f('stroke_enabled', 'bool', false, { group: '描边' }),
    f('stroke_color', 'color', '#FFFFFF', { group: '描边' }),
    f('stroke_width', 'number', 1, { min: 0.25, max: 6, step: 0.25, unit: 'pt', group: '描边' }),
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
const past = () => useDocumentStore.getState().past.length

let root: Root
let host: HTMLDivElement

function Harness() {
  const panel = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <ElementInspector panel={panel} />
    </TooltipProvider>
  )
}

async function mount() {
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: ['axes_0.title'] })
  // 「更多」按角色记忆：直接把标题那一档打开，不靠点按钮
  useInspectorPrefs.getState().setMoreOpen('title', true)
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

const text = () => host.textContent ?? ''
const addButtons = () =>
  Array.from(host.querySelectorAll<HTMLButtonElement>('[data-effect-add]')).map((b) =>
    b.textContent?.trim(),
  )
const switchOf = (label: string) =>
  host.querySelector(`[role="switch"][aria-label="${label}"]`) as HTMLButtonElement | null
const propRow = (prop: string) => host.querySelector(`[data-prop="${prop}"]`)

async function click(el: Element | null) {
  ;(document.activeElement as HTMLElement | null)?.blur()
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}

beforeEach(async () => {
  engineRender.mockReset()
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_text_effects')
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

describe('关着：只有「＋添加」入口，参数收起', () => {
  it('「更多」里是「添加背景」「添加描边」两条入口；背景色 / 描边色这些一条都不出现', async () => {
    await mount()
    expect(addButtons()).toEqual(['添加背景', '添加描边'])
    // 开关关着时不画成开关
    expect(switchOf('背景')).toBeNull()
    expect(switchOf('描边')).toBeNull()
    for (const dep of ['bbox_facecolor', 'bbox_alpha', 'bbox_edgecolor', 'bbox_linewidth', 'bbox_pad', 'bbox_rounded', 'stroke_color', 'stroke_width']) {
      expect(propRow(dep), dep).toBeNull()
    }
    expect(text()).not.toContain('背景色')
    expect(text()).not.toContain('描边色')
  })
})

describe('添加：一条 override、一条历史，参数随即铺开', () => {
  it('点「添加背景」→ bbox_visible=true，背景的六条参数出现，描边仍收着', async () => {
    await mount()
    const [addBg] = Array.from(host.querySelectorAll('[data-effect-add]'))
    await click(addBg)
    expect(overrideOf('bbox_visible')).toBe(true)
    expect(past()).toBe(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
    // 开了就是一个真开关
    expect(switchOf('背景')).not.toBeNull()
    expect(switchOf('背景')!.getAttribute('aria-checked')).toBe('true')
    for (const dep of ['bbox_facecolor', 'bbox_alpha', 'bbox_edgecolor', 'bbox_linewidth', 'bbox_pad', 'bbox_rounded']) {
      expect(propRow(dep), dep).not.toBeNull()
    }
    expect(propRow('stroke_color')).toBeNull()
    expect(addButtons()).toEqual(['添加描边'])
  })

  it('点「添加描边」只动描边那一组', async () => {
    await mount()
    const [, addStroke] = Array.from(host.querySelectorAll('[data-effect-add]'))
    await click(addStroke)
    expect(overrideOf('stroke_enabled')).toBe(true)
    expect(overrideOf('bbox_visible')).toBeUndefined()
    expect(propRow('stroke_color')).not.toBeNull()
    expect(propRow('stroke_width')).not.toBeNull()
    expect(propRow('bbox_facecolor')).toBeNull()
  })
})

describe('关掉：开关写 false + 从属字段的 override 一并清掉，一条历史', () => {
  it('改过内边距再关背景 → bbox_visible=false、bbox_pad 的 override 没了、参数收起', async () => {
    await mount()
    await act(async () => {
      setOverride('p1', 'axes_0.title', 'bbox_visible', true)
      setOverride('p1', 'axes_0.title', 'bbox_pad', 1.2)
    })
    expect(propRow('bbox_pad')).not.toBeNull()
    // 内容框在挂载时自动聚焦、开着一条「修改内容」事务，上面两条 setOverride 都
    // 落在它里面；先把它收掉再数历史，否则数到的是那条事务的收尾
    ;(document.activeElement as HTMLElement | null)?.blur()
    const before = past()
    await click(switchOf('背景'))
    expect(overrideOf('bbox_visible')).toBe(false)
    expect(overrideOf('bbox_pad')).toBeUndefined()
    expect(past()).toBe(before + 1)
    // 参数收起、入口回来
    expect(propRow('bbox_pad')).toBeNull()
    expect(propRow('bbox_facecolor')).toBeNull()
    expect(addButtons()).toEqual(['添加背景', '添加描边'])
  })

  it('关掉背景不碰描边那一组的 override', async () => {
    await mount()
    await act(async () => {
      setOverride('p1', 'axes_0.title', 'bbox_visible', true)
      setOverride('p1', 'axes_0.title', 'stroke_enabled', true)
      setOverride('p1', 'axes_0.title', 'stroke_width', 2)
    })
    await click(switchOf('背景'))
    expect(overrideOf('stroke_enabled')).toBe(true)
    expect(overrideOf('stroke_width')).toBe(2)
    expect(propRow('stroke_width')).not.toBeNull()
  })

  it('开关关着而从属字段被改过：那一条照旧可见（override 不因折叠而不可发现）', async () => {
    await mount()
    await act(async () => {
      setOverride('p1', 'axes_0.title', 'bbox_pad', 1.2)
    })
    expect(propRow('bbox_pad')).not.toBeNull()
    expect(propRow('bbox_facecolor')).toBeNull()
    expect(addButtons()).toEqual(['添加背景', '添加描边'])
  })
})
