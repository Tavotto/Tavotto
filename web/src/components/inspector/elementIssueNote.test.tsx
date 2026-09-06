/**
 * 被选元素上的问题就地出现在它旁边（审计 T14）。
 *
 * 要钉住的：
 *   1. 只有落在**这个元素**上的问题才出现——别的元素、别的面板一条都不进来；
 *      但**任何规则**都算（缺字、字号、超出图幅…），不只认字形那两条；
 *   2. 措辞是问题面板同一句成文（含逐字列出的那几个字），不另写第二套；
 *   3. 出口按规则给：能修 → 问题面板同一颗「修复」；字形 → 「更换字体」（焦点落到
 *      字体那一行）；说得出字段 → 「定位到字段」；说不出 → 「在问题面板查看」；
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
import { issuesForElement } from './ElementIssueNote'

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
  f('fontsize', 'number', 6, { min: 3, max: 36, step: 0.5, unit: 'pt' }),
  f('color', 'color', '#000000'),
  f('weight', 'enum', 'normal', { options: ['normal', 'bold'] }),
  ...(withFamily
    ? [f('fontfamily', 'enum', 'sans-serif', { options: ['serif', 'sans-serif', 'monospace'] })]
    : []),
]

const ENTRY = 'axes_0.legend.texts_0'
const legendText = (withFamily = true): ManifestElement => ({
  gid: ENTRY,
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
const overrideOf = (prop: string) =>
  livePanel().overrides.find((o) => o.gid === ENTRY && o.prop === prop)?.value

/** 与 `lib/validation.ts` 接出来的问题同形（只填这里读得到的字段） */
const baseIssue = (
  rule: string,
  gid: string,
  over: Partial<ValidationIssue> = {},
  objectId = 'p1',
): ValidationIssue => ({
  issueId: `${rule}|c|${objectId}|${gid}|${over.propertyPath ?? ''}`,
  ruleCode: rule,
  severity: 'warn',
  context: 'document',
  objectRef: {
    documentId: 'd_element_issues',
    canvasId: useDocumentStore.getState().activeCanvasId,
    objectId,
    gid,
  },
  subject: { kind: 'element', elementRole: 'legend_text', elementLabel: '图例项' },
  propertyPath: null,
  message: msg('preflight.fontTooSmall', { effective: '6.00', min: '8' }, 'errors'),
  technicalDetails: {},
  fixKind: 'none',
  ...over,
})

const glyphIssue = (rule: 'glyph-missing' | 'glyph-substituted', gid: string, chars: string, objectId = 'p1') =>
  baseIssue(rule, gid, {
    severity: rule === 'glyph-missing' ? 'error' : 'suggestion',
    propertyPath: 'fontfamily',
    message: msg(
      rule === 'glyph-missing' ? 'preflight.glyphMissing' : 'preflight.glyphSubstituted',
      { chars, count: String(chars.length) },
      'errors',
    ),
    technicalDetails: { chars: [...chars], family: 'sans-serif' },
  }, objectId)

/** 字号偏小：safe_auto，计划由 `planFontUp` 现算 */
const fontIssue = (gid: string) =>
  baseIssue('font-too-small', gid, {
    propertyPath: 'fontsize',
    technicalDetails: { effective_pt: 6, min_pt: 8 },
    fixKind: 'safe_auto',
  })

/** 同事 geom 正在加的那类：元素超出图幅，导出会被裁切——error、不能自动修、说不出字段 */
const clippedIssue = (gid: string) =>
  baseIssue('element-outside-figure', gid, {
    severity: 'error',
    message: literal('这个元素超出图幅，导出时会被裁掉'),
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
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [gid], leftOpen: false, leftTab: 'assets' })
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

const note = () => host.querySelector('[data-element-issues]')
const rows = () =>
  Array.from(note()?.querySelectorAll('[data-element-issue]') ?? []).map((r) => r.getAttribute('data-element-issue'))
const buttonsIn = (row: Element) => Array.from(row.querySelectorAll('button')).map((b) => b.textContent?.trim())
const rowOf = (rule: string) => note()!.querySelector(`[data-element-issue="${rule}"]`)!

async function click(el: Element | null) {
  ;(document.activeElement as HTMLElement | null)?.blur()
  await act(async () => {
    ;(el as HTMLElement).click()
  })
}

beforeEach(async () => {
  engineRender.mockReset()
  resetPreview()
  setHistoryMode('gesture')
  localStorage.clear()
  useInspectorPrefs.setState({ moreOpen: {}, advancedOpen: {} })
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useRenderStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_element_issues')
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

describe('issuesForElement：按对象 + gid 取，规则不限', () => {
  it('别的元素 / 别的面板不进来；缺字、字号、裁切三种规则都算', () => {
    const mine = [glyphIssue('glyph-missing', ENTRY, '我是'), fontIssue(ENTRY), clippedIssue(ENTRY)]
    const other = glyphIssue('glyph-missing', 'axes_0.title', '我')
    const otherPanel = glyphIssue('glyph-substituted', ENTRY, '是', 'p2')
    expect(issuesForElement([other, ...mine, otherPanel], 'p1', ENTRY)).toEqual(mine)
  })
})

describe('就地提示', () => {
  it('选中出问题的图例项：提示出现，逐字列出画不出来的字，带「更换字体」', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [glyphIssue('glyph-missing', entry.gid, '我是')])
    const n = note()
    expect(n).not.toBeNull()
    expect(n!.textContent).toContain('我是')
    expect(n!.textContent).toContain('画不出来')
    expect(rows()).toEqual(['glyph-missing'])
    expect(buttonsIn(rowOf('glyph-missing'))).toEqual(['更换字体'])
  })

  it('换脸画出来的是另一句；三条同时在就三行，各带各的出口', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [
      glyphIssue('glyph-missing', entry.gid, '我'),
      glyphIssue('glyph-substituted', entry.gid, '是'),
      clippedIssue(entry.gid),
    ])
    expect(rows()).toEqual(['glyph-missing', 'glyph-substituted', 'element-outside-figure'])
    expect(buttonsIn(rowOf('glyph-substituted'))).toEqual(['更换字体'])
    // 超出图幅：不能自动修、说不出字段 → 去问题面板
    expect(buttonsIn(rowOf('element-outside-figure'))).toEqual(['在问题面板查看'])
  })

  it('问题落在别的元素上时不出现', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [glyphIssue('glyph-missing', 'axes_0.title', '我')])
    expect(note()).toBeNull()
  })

  it('没有问题就什么都不画', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [])
    expect(note()).toBeNull()
  })

  it('「更换字体」把焦点送到字体那一行', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [glyphIssue('glyph-missing', entry.gid, '我是')])
    await click(rowOf('glyph-missing').querySelector('button'))
    const fontRow = host.querySelector('[data-prop="fontfamily"]')
    expect(fontRow).not.toBeNull()
    expect(fontRow!.contains(document.activeElement)).toBe(true)
  })

  it('元素没有字体字段：字形问题只提示、不给按钮', async () => {
    const entry = legendText(false)
    await mount(entry, entry.gid, [glyphIssue('glyph-missing', entry.gid, '我是')])
    expect(note()).not.toBeNull()
    expect(buttonsIn(rowOf('glyph-missing'))).toEqual([])
  })

  it('能自动修的（字号偏小）给问题面板同一颗「修复」，点下去真的写 override', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [fontIssue(entry.gid)])
    expect(buttonsIn(rowOf('font-too-small'))).toEqual(['修复'])
    await click(rowOf('font-too-small').querySelector('button'))
    // planFontUp：默认规范下限 8pt、边不含等号 → 8.5；面板 1:1 不换算
    expect(overrideOf('fontsize')).toBe(8.5)
  })

  it('「在问题面板查看」打开左侧问题页', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [clippedIssue(entry.gid)])
    await click(rowOf('element-outside-figure').querySelector('button'))
    const ui = useUiStore.getState()
    expect(ui.leftOpen && ui.leftTab === 'problems').toBe(true)
  })

  it('说得出字段但不能自动修的：给「定位到字段」', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [
      baseIssue('text-weight-policy', entry.gid, { propertyPath: 'weight', message: literal('字重不合规范') }),
    ])
    expect(buttonsIn(rowOf('text-weight-policy'))).toEqual(['定位到字段'])
  })

  it('提示排在内容框之后、字体那一行之前', async () => {
    const entry = legendText()
    await mount(entry, entry.gid, [glyphIssue('glyph-missing', entry.gid, '我是')])
    const content = host.querySelector('[data-prop="text"]')!
    const fontRow = host.querySelector('[data-prop="fontfamily"]')!
    const n = note()!
    expect(content.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(n.compareDocumentPosition(fontRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
