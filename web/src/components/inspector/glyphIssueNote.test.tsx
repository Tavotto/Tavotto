/**
 * 缺字提示就地出现在被选文字旁边（审计 T14）。
 *
 * 要钉住的：
 *   1. 只有落在**这个元素**上的 `glyph-missing` / `glyph-substituted` 才出现——
 *      别的元素、别的规则一条都不进来；
 *   2. 措辞是问题面板同一句成文（含逐字列出的那几个字），不另写第二套；
 *   3. 「更换字体」把焦点送到字体那一行（`data-prop="fontfamily"`）；
 *      元素没有字体字段时只提示、不给按钮；
 *   4. 提示排在内容框之后、文字样式行之前。
 */
import { literal, msg } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import type { EditableField, EngineRenderOptions, Manifest, ManifestElement } from '@/lib/api'
import type { ValidationIssue } from '@/lib/validation'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { resetPreview, setHistoryMode } from '@/store/svgPreviewStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ElementInspector } from './ElementInspector'
import { glyphIssuesFor } from './GlyphIssueNote'

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

const textFields = (withFamily: boolean) => [
  f('text', 'text', '我是'),
  f('fontsize', 'number', 9, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  ...(withFamily
    ? [f('fontfamily', 'enum', 'sans-serif', { options: ['serif', 'sans-serif', 'monospace'] })]
    : []),
]

const legendText = (withFamily = true): ManifestElement => ({
  gid: 'axes_0.legend.texts_0',
  role: 'legend_text',
  label: '图例项 “我是”',
  bbox: [0.3, 0.2, 0.3, 0.05],
  draggable: false,
  editable: textFields(withFamily),
})

const titleEl: ManifestElement = {
  gid: 'axes_0.title',
  role: 'title',
  label: '标题',
  bbox: [0.3, 0.02, 0.3, 0.07],
  draggable: true,
  editable: textFields(true),
}

const manifestOf = (entry: ManifestElement): Manifest => ({
  stem: 'Fig2',
  size_mm: [101.6, 76.2],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    titleEl,
    entry,
  ],
})

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 101.6,
    h: 76.2,
    fileId: 'Fig2.pdf',
    fileKind: 'pdf',
    nativeW: 101.6,
    nativeH: 76.2,
    script: 'fig2.py',
    overrides: [],
  }) as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}

/** 与 `lib/validation.ts` 接出来的一条 glyph 问题同形（只填这里读得到的字段） */
const issueOf = (
  rule: 'glyph-missing' | 'glyph-substituted',
  gid: string,
  chars: string,
  objectId = 'p1',
): ValidationIssue => ({
  issueId: `${rule}|c|${objectId}|${gid}|fontfamily`,
  ruleCode: rule,
  severity: rule === 'glyph-missing' ? 'error' : 'suggestion',
  context: 'document',
  objectRef: {
    documentId: 'd_glyph_note',
    canvasId: useDocumentStore.getState().activeCanvasId,
    objectId,
    gid,
  },
  subject: { kind: 'element', elementRole: 'legend_text', elementLabel: '图例项' },
  propertyPath: 'fontfamily',
  message: msg(
    rule === 'glyph-missing' ? 'preflight.glyphMissing' : 'preflight.glyphSubstituted',
    { chars, count: String(chars.length) },
    'errors',
  ),
  technicalDetails: { chars: [...chars], family: 'sans-serif' },
  fixKind: 'none',
})

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

async function mount(entry: ManifestElement, gid: string, issues: ValidationIssue[]) {
  const manifest = manifestOf(entry)
  useRenderStore.getState().patch(renderKeyOf(livePanel()), {
    fileId: 'Fig2.pdf',
    manifest,
    svg: MATPLOTLIB_SVG,
    rev: 1,
    status: 'ready',
    lastPatches: '[]',
  })
  useRenderStore.setState({ latest: { 'Fig2.pdf': renderKeyOf(livePanel()) } })
  engineRender.mockResolvedValue({ rev: 2, manifest, svg: MATPLOTLIB_SVG, warnings: [] })
  useValidationStore.setState({ issues, ready: true, failed: false })
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid] })
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

const note = () => host.querySelector('[data-glyph-note]')

beforeEach(async () => {
  engineRender.mockReset()
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_glyph_note')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(panelOf())
  })
  useDocumentStore.setState({ past: [], future: [] })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  useValidationStore.setState({ issues: [], ready: false, failed: false })
  document.body.innerHTML = ''
  resetPreview()
})

describe('glyphIssuesFor：只认落在这个元素上的两条字形规则', () => {
  it('别的元素 / 别的面板 / 别的规则都不进来', () => {
    const mine = issueOf('glyph-missing', 'axes_0.legend.texts_0', '我是')
    const other = issueOf('glyph-missing', 'axes_0.title', '我')
    const otherPanel = issueOf('glyph-substituted', 'axes_0.legend.texts_0', '是', 'p2')
    const notGlyph = { ...mine, ruleCode: 'font-too-small', issueId: 'x' }
    expect(glyphIssuesFor([other, notGlyph, mine, otherPanel], 'p1', 'axes_0.legend.texts_0')).toEqual([mine])
  })
})

describe('就地提示', () => {
  it('选中出问题的图例项：提示出现，逐字列出画不出来的字，带「更换字体」', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [issueOf('glyph-missing', entry.gid, '我是')])
    const n = note()
    expect(n).not.toBeNull()
    expect(n!.textContent).toContain('我是')
    expect(n!.textContent).toContain('画不出来')
    expect(n!.querySelector('[data-glyph-rule="glyph-missing"]')).not.toBeNull()
    expect(Array.from(n!.querySelectorAll('button')).map((b) => b.textContent)).toEqual(['更换字体'])
  })

  it('换脸画出来的是另一句、另一种语气；两条同时在就两行', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [
      issueOf('glyph-missing', entry.gid, '我'),
      issueOf('glyph-substituted', entry.gid, '是'),
    ])
    const rules = Array.from(note()!.querySelectorAll('[data-glyph-rule]')).map((p) =>
      p.getAttribute('data-glyph-rule'),
    )
    expect(rules).toEqual(['glyph-missing', 'glyph-substituted'])
  })

  it('问题落在别的元素上时不出现', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [issueOf('glyph-missing', 'axes_0.title', '我')])
    expect(note()).toBeNull()
  })

  it('没有问题就什么都不画', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [])
    expect(note()).toBeNull()
  })

  it('「更换字体」把焦点送到字体那一行', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [issueOf('glyph-missing', entry.gid, '我是')])
    const btn = note()!.querySelector('button') as HTMLButtonElement
    await act(async () => {
      btn.click()
    })
    const fontRow = host.querySelector('[data-prop="fontfamily"]')
    expect(fontRow).not.toBeNull()
    expect(fontRow!.contains(document.activeElement)).toBe(true)
  })

  it('元素没有字体字段：只提示、不给按钮', async () => {
    const entry = legendText(false)
    await mount(entry, entry.gid, [issueOf('glyph-missing', entry.gid, '我是')])
    expect(note()).not.toBeNull()
    expect(note()!.querySelector('button')).toBeNull()
  })

  it('提示排在内容框之后、字体那一行之前', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [issueOf('glyph-missing', entry.gid, '我是')])
    const content = host.querySelector('[data-prop="text"]')!
    const fontRow = host.querySelector('[data-prop="fontfamily"]')!
    const n = note()!
    // compareDocumentPosition：FOLLOWING = 4
    expect(content.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(n.compareDocumentPosition(fontRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
