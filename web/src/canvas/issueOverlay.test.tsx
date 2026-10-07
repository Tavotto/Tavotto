/**
 * 问题面板在画布上的两样东西（2026-10-07 设计审计 §9.4）：悬停轮廓（`uiStore.issueHover`）与
 * 等级标记（`uiStore.problemPins`，点它 = `openProblemAt`）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ValidationIssue } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type CanvasObject } from '@/types/document'
import { IssueOverlay } from './IssueOverlay'

const openProblemAt = vi.fn((..._args: unknown[]): { ok: boolean } => ({ ok: true }))
vi.mock('@/lib/issueFocus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/issueFocus')>()),
  openProblemAt: (...args: unknown[]) => openProblemAt(...args),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const objects = [
  { id: 'p1', type: 'panel', fileId: 'a.pdf', fileKind: 'pdf', nativeW: 40, nativeH: 30, overrides: [], x: 0, y: 0, w: 40, h: 30 },
  { id: 'p2', type: 'panel', fileId: 'b.pdf', fileKind: 'pdf', nativeW: 40, nativeH: 30, overrides: [], x: 50, y: 0, w: 40, h: 30 },
] as CanvasObject[]
const t = { zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 }

const issue = (id: string, objectId: string, severity: ValidationIssue['severity']) =>
  ({
    issueId: id,
    ruleCode: 'font-below-absolute-floor',
    severity,
    objectRef: { documentId: 'd', canvasId: useDocumentStore.getState().activeCanvasId, objectId, gid: null },
  }) as unknown as ValidationIssue

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  useUiStore.setState({ issueHover: null, problemPins: false })
  useValidationStore.setState({ issues: [] })
  await act(async () => {
    root.render(
      <svg>
        <IssueOverlay objects={objects} t={t} />
      </svg>,
    )
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  openProblemAt.mockClear()
})

describe('问题面板的悬停轮廓', () => {
  it('问题面板指着哪个对象，画布上就给它描一道轮廓；撤掉就没了', async () => {
    expect(container.querySelector('[data-issue-hover]')).toBeNull()
    await act(async () => useUiStore.getState().setIssueHover({ objectId: 'p2', gid: null }))
    const rect = container.querySelector('[data-issue-hover]')!
    expect(rect.getAttribute('data-issue-hover')).toBe('p2')
    // 轮廓不吃指针：它只是「我在看它」
    expect(rect.getAttribute('pointer-events')).toBe('none')
    await act(async () => useUiStore.getState().setIssueHover(null))
    expect(container.querySelector('[data-issue-hover]')).toBeNull()
  })
})

describe('画布上的等级标记', () => {
  it('默认关；打开后每张有问题的图一枚，颜色按最要紧的那条，点它 = openProblemAt', async () => {
    await act(async () =>
      useValidationStore.setState({
        issues: [issue('a', 'p1', 'warn'), issue('b', 'p1', 'error'), issue('c', 'p2', 'suggestion')],
      }),
    )
    expect(container.querySelector('[data-issue-pin]'), '没打开时一枚都不画').toBeNull()
    await act(async () => useUiStore.getState().setProblemPins(true))
    const pins = [...container.querySelectorAll<SVGGElement>('[data-issue-pin]')]
    expect(pins.map((p) => p.dataset.issuePin)).toEqual(['p1', 'p2'])
    expect(pins[0].dataset.issuePinSeverity).toBe('error')
    expect(pins[0].textContent).toContain('2')
    await act(async () => {
      pins[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(openProblemAt).toHaveBeenCalledTimes(1)
    expect((openProblemAt.mock.calls[0][0] as ValidationIssue).issueId).toBe('b')
  })
})

describe('点标记开的是被点那张图的清单', () => {
  it('一张画布两张图、选中着 A，点 B 的标记：问题面板按「当前图」= B 开，不退成整份文档（Codex #832）', async () => {
    const real = await vi.importActual<typeof import('@/lib/issueFocus')>('@/lib/issueFocus')
    openProblemAt.mockImplementation((...args: unknown[]) =>
      real.openProblemAt(...(args as Parameters<typeof real.openProblemAt>)),
    )
    // 走真的 openProblemAt：问题要是完整的一条（清单要按 subject / propertyPath 分组）
    const full = (id: string, objectId: string, severity: ValidationIssue['severity']) =>
      ({
        ...issue(id, objectId, severity),
        context: 'document',
        subject: { part: null },
        propertyPath: null,
        technicalDetails: {},
        fixKind: 'safe_auto',
      }) as unknown as ValidationIssue
    try {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd')
      useDocumentStore.getState().commit(literal('准备'), (d) => {
        d.page = { w: 100, h: 60 }
        d.objects = objects.map((o) => ({ ...o }))
      })
      useWorkspaceStore.getState().clear()
      useUiStore.setState({ problemScope: null, elementPanelId: null })
      useSelectionStore.getState().set(['p1'])
      await act(async () => {
        useValidationStore.setState({ issues: [full('a', 'p1', 'warn'), full('b', 'p2', 'error')] })
        useUiStore.getState().setProblemPins(true)
      })
      const pin = container.querySelector<SVGGElement>('[data-issue-pin="p2"]')!
      await act(async () => {
        pin.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      })
      expect(openProblemAt.mock.calls[0][2], '传给 openProblemAt 的当前图是被点的那张').toBe('p2')
      expect(useSelectionStore.getState().ids).toEqual(['p2'])
      expect(useUiStore.getState().problemScope).toBe('figure')
      expect(useUiStore.getState().leftTab).toBe('problems')
    } finally {
      openProblemAt.mockImplementation(() => ({ ok: true }))
    }
  })
})
