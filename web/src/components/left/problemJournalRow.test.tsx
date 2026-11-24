/**
 * 问题栏顶部的「期刊」一行：与设置里同一个选择器、同一条路（写当前画布的绑定，一次提交、可撤销），
 * 问题清单按新绑定重判；入口关着整行不出现；占位 / 被接管的只读文档下禁用。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../../../tests/golden/journal_vectors.json'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { loadCatalog } from '@/lib/journals'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { ProblemPanel } from './ProblemPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false

const ui = vi.hoisted(() => ({ on: true }))
vi.mock('@/lib/journalUi', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/lib/journalUi')>()), journalCatalogUi: () => ui.on }))
vi.mock('@/lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/lib/api')>()), engineRender: () => new Promise(() => {}) }))

const panel: PanelObject = { id: 'p1', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 80, nativeH: 60, overrides: [], x: 0, y: 0, w: 80, h: 60, script: 'fig1.py' }
const manifest = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    { gid: 'axes_0.xticks', role: 'ticks', label: 'X 刻度文字', bbox: [0.1, 0.9, 0.8, 0.05], draggable: false, editable: [{ prop: 'fontsize', type: 'number', value: 4 }] },
  ],
}

let container: HTMLDivElement
let root: Root
const q = <T extends Element = HTMLElement>(sel: string) => container.querySelector<T>(sel) ?? document.body.querySelector<T>(sel)
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const mount = async () => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<TooltipProvider><ProblemPanel /></TooltipProvider>)
  })
}
const ids = () => useValidationStore.getState().issues.map((i) => `${i.ruleCode}|${i.severity}`).sort()
const doc = () => useDocumentStore.getState()

beforeEach(async () => {
  ui.on = true
  localStorage.clear()
  useJournalCatalogStore.setState({ catalog: loadCatalog(vectors.catalog) })
  useUiStore.setState({ problemFilter: null, problemScope: null, problemView: 'figure', problemDrill: null, problemContext: null, leftTab: 'problems', leftOpen: true, layout: 'wide' })
  await doc().switchDocument(emptyProject(), 'd_issues_journal')
  doc().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifest as never)
  runValidation()
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  document.body.innerHTML = ''
  useJournalCatalogStore.setState({ catalog: loadCatalog() })
})

describe('问题栏顶部「期刊」一行', () => {
  it('在摘要条 / 清单之上，标签是已有的「期刊」；没选期刊时写「选择期刊」，用自己的锚点、不占设置里的 id', async () => {
    await mount()
    const row = q('[data-issues-journal]')!
    expect(row).not.toBeNull()
    expect(row.textContent).toContain('期刊')
    const trigger = q('[data-issues-journal-picker]')!
    expect(trigger.textContent).toContain('选择期刊')
    expect(trigger.id).toBe('')
    expect(q('#journal-picker')).toBeNull()
    expect(q('[data-journal-picker]')).toBeNull()
    const summary = q('[data-problem-summary]')
    if (summary) expect(row.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(row.className).toContain('shrink-0')
  })

  it('画布已按某本期刊判：触发器显示刊名', async () => {
    await mount()
    await click(q('[data-issues-journal-picker]'))
    await click(q('[data-journal-option="fx-zeta"]'))
    expect(q('[data-issues-journal-picker]')!.textContent).toContain('Fixture Zeta')
  })

  it('点选 = 改当前画布的绑定（一次提交、可撤销）；问题清单按新规范重判', async () => {
    await mount()
    const before = ids()
    expect(before).toEqual(['font-below-absolute-floor|error']) // 默认规范：4 pt 低于绝对下限
    const depth = doc().past.length
    await click(q('[data-issues-journal-picker]'))
    await click(q('[data-journal-option="fx-zeta"]'))
    expect(doc().doc.profile?.journalRef?.id).toBe('fx-zeta')
    expect(doc().past.length).toBe(depth + 1)
    await act(async () => runValidation()) // 应用里由校验订阅在文档变化后重跑；这里直接跑一遍
    const after = ids()
    expect(after).toEqual(['font-size-recommended|suggestion']) // 按 Fixture Zeta：同一处只是建议
    await act(async () => doc().undo())
    expect(doc().doc.profile?.journalRef).toBeUndefined()
    await act(async () => runValidation())
    expect(ids()).toEqual(before)
  })

  it('「不按期刊」退回之前的规范', async () => {
    await mount()
    await click(q('[data-issues-journal-picker]'))
    await click(q('[data-journal-option="fx-zeta"]'))
    await click(q('[data-issues-journal-picker]'))
    await click(q('[data-journal-none]'))
    expect(doc().doc.profile?.journalRef).toBeUndefined()
  })

  it('入口关着（journalCatalogUi() 假）：整行不出现', async () => {
    ui.on = false
    await mount()
    expect(q('[data-issues-journal]')).toBeNull()
    expect(q('[data-issues-journal-picker]')).toBeNull()
  })

  it('占位文档（恢复门没开）/ 被另一窗口接管的只读排版：选择器禁用', async () => {
    useDocumentStore.setState({ restoreGate: { state: 'needs_user' } as never })
    await mount()
    expect(q<HTMLButtonElement>('[data-issues-journal-picker]')!.disabled).toBe(true)
    await act(async () => useDocumentStore.setState({ restoreGate: { state: 'ready' } as never, windowHold: { kind: 'contested' } as never }))
    expect(q<HTMLButtonElement>('[data-issues-journal-picker]')!.disabled).toBe(true)
    await act(async () => useDocumentStore.setState({ windowHold: null }))
    expect(q<HTMLButtonElement>('[data-issues-journal-picker]')!.disabled).toBe(false)
  })

  it('还在检查 / 没有问题时一行照在原位', async () => {
    useValidationStore.setState({ issues: [], ready: false })
    await mount()
    expect(q('[data-issues-journal]')).not.toBeNull()
    await act(async () => useValidationStore.setState({ ready: true }))
    expect(q('[data-issues-journal]')).not.toBeNull()
  })
})
