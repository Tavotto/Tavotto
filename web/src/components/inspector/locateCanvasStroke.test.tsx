/**
 * 「线条太细」的画布对象那一条（ADR 0208 §四 第 7 条）：问题清单里它指向那支箭头的 `strokePt`，
 * 点它（`focusObject`，问题面板整行点击与「定位」按钮共用）会选中那支箭头、焦点落到属性栏的「线宽」输入框上。
 * 数据线与画布对象一起太细时，各自是独立的一行（每处命中一条 occurrence），互不顶掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { focusObject } from '@/lib/issueFocus'
import { loadProfile } from '@/lib/profile'
import { validateCanvas } from '@/lib/validation'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ArrowObject } from '@/types/document'
import { literal } from '@/i18n'
import { ArrowSection } from './StrokeSection'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const thin: ArrowObject = {
  id: 'a_thin0001',
  type: 'arrow',
  x: 10,
  y: 10,
  w: 30,
  h: 10,
  start: { rx: 0, ry: 1 },
  end: { rx: 1, ry: 0 },
  color: '#000000',
  strokePt: 0.1,
  head: 'end',
}
const ok: ArrowObject = { ...thin, id: 'a_fine0001', y: 40, strokePt: 1 }

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  document.body.innerHTML = ''
  useSelectionStore.getState().clear()
  useUiStore.setState({ fieldLocate: null, issueHighlight: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_locate_stroke')
  useDocumentStore.getState().commit(literal('加箭头'), (d) => {
    d.objects.push(structuredClone(thin), structuredClone(ok))
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
  document.body.innerHTML = ''
})

describe('画布线条太细：定位到那支箭头的线宽', () => {
  it('问题清单里那一条指向细箭头的 strokePt；粗的那支不报', () => {
    const s = useDocumentStore.getState()
    const profile = loadProfile('journal-base-v1', { min_line_width_pt: 0.5 })
    const res = validateCanvas(
      { canvasId: s.activeCanvasId, canvasName: 'c', doc: s.doc, profile },
      'd_locate_stroke',
      {},
      { byKey: {}, latest: {} },
    )
    const hits = res.issues.filter((i) => i.ruleCode === 'line-width-below-min')
    expect(hits.map((i) => [i.objectRef.objectId, i.objectRef.gid, i.propertyPath])).toEqual([
      ['a_thin0001', null, 'strokePt'],
    ])
  })

  it('点它：选中那支箭头，焦点落到「线宽」输入框上', async () => {
    const s = useDocumentStore.getState()
    const profile = loadProfile('journal-base-v1', { min_line_width_pt: 0.5 })
    const issue = validateCanvas(
      { canvasId: s.activeCanvasId, canvasName: 'c', doc: s.doc, profile },
      'd_locate_stroke',
      {},
      { byKey: {}, latest: {} },
    ).issues.find((i) => i.ruleCode === 'line-width-below-min')!
    await act(async () => {
      root.render(
        <TooltipProvider>
          <ArrowSection objs={[thin]} />
        </TooltipProvider>,
      )
    })
    let outcome: ReturnType<typeof focusObject> | undefined
    await act(async () => {
      outcome = focusObject(issue.objectRef, issue.propertyPath)
    })
    expect(outcome).toMatchObject({ ok: true, field: 'focused' })
    expect(useSelectionStore.getState().ids).toEqual(['a_thin0001'])
    const field = host.querySelector('[data-prop="strokePt"]')
    expect(field).not.toBeNull()
    expect(field!.contains(document.activeElement)).toBe(true)
  })
})
