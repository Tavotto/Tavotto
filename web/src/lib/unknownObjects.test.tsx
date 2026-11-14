/**
 * 更新的构建写进文档的对象类型（第一例：自由路径 `path`，ADR 0185），本构建读到时的兜底。
 *
 * 修之前（旧构建打开带 `type: 'path'` 的文档）：读盘、保存都原样保留——但图层树 `iconFor` 拿到 undefined，
 * `<Icon/>` 抛错让整个界面落进错误边界；导出时 `toExportObject` 回 undefined → 载荷里是 null → 后端静默丢掉，
 * 导出图上少一块且没人知道。
 *
 * 现在：判据只有 `isKnownObject` 一处——
 *   1. 图层树用通用图标、名字回「新版对象」，不崩；
 *   2. 画布上只画占位框、不接指针；全选与框选不收它（所以拖不动、删不掉、复制不了，文档里原样留着）；
 *   3. 导出载荷里没有空洞，`newerObjects` 报出个数，界面据此不许开始。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import { ObjectView } from '@/canvas/ObjectView'
import { useDocumentStore } from '@/store/documentStore'
import { selectAllIds } from '@/store/actions'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyDocument, emptyProject, isKnownObject, objectLabel, type CanvasObject } from '@/types/document'
import { buildExportRequest, type ExportRequestInput } from './exportRequest'
import { newerObjectCount, toExportObjects } from './exportPayload'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

/** 更新的构建写进来的对象：本构建的类型里没有它，只能经 unknown 造出来 */
const newer = (over: Record<string, unknown> = {}): CanvasObject =>
  ({
    id: 'n1', type: 'path', x: 10, y: 10, w: 30, h: 20,
    subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [1, 1] }] }],
    ...over,
  }) as unknown as CanvasObject
const text = (over: Partial<CanvasObject> = {}): CanvasObject =>
  ({ id: 't1', type: 'text', text: 'a', x: 0, y: 0, w: 20, h: 8, sizePt: 8, bold: false, color: '#000', align: 'left', ...over }) as CanvasObject

let container: HTMLDivElement
let root: Root
beforeEach(() => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700, spaceDown: false })
  useUiStore.setState({ tool: 'select', editingTextId: null, cropTargetId: null, elementPanelId: null })
  useSelectionStore.getState().set([])
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  document.body.innerHTML = ''
})

describe('判据', () => {
  it('四种已知类型认得，别的都不认得', () => {
    for (const type of ['panel', 'text', 'arrow', 'shape']) expect(isKnownObject({ type })).toBe(true)
    expect(isKnownObject({ type: 'path' })).toBe(false)
    expect(isKnownObject({ type: '' })).toBe(false)
  })

  it('名字不回 undefined（图层树、版本差异要显示它）', () => {
    expect(objectLabel(newer())).toBe(t('objectType.unknown'))
    expect(objectLabel(newer({ name: '我的路径' }))).toBe('我的路径')
  })
})

describe('图层树', () => {
  it('有新版对象时照常渲染，那一行带名字', async () => {
    useDocumentStore.setState({
      doc: { ...emptyProject(), objects: [text(), newer()] }, past: [], future: [], txn: null,
    } as never)
    await act(async () => {
      root.render(<TooltipProvider><LayerTree /></TooltipProvider>)
    })
    const rows = Array.from(document.querySelectorAll<HTMLElement>('li[data-layer]'))
    expect(rows).toHaveLength(2)
    expect(rows.some((r) => r.textContent?.includes(t('objectType.unknown')))).toBe(true)
  })
})

describe('画布', () => {
  it('只画占位框，不接指针', async () => {
    await act(async () => root.render(<ObjectView obj={newer()} />))
    const div = container.querySelector('[data-object-id]') as HTMLDivElement
    expect(div.style.pointerEvents).toBe('none')
    expect(container.querySelector('[data-unknown-object]')).not.toBeNull()
  })

  it('已知对象不画占位框（反证：判据不是恒真）', async () => {
    const rect = { id: 's1', type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#000', fill: null } as CanvasObject
    await act(async () => root.render(<ObjectView obj={rect} />))
    expect(container.querySelector('[data-unknown-object]')).toBeNull()
    expect((container.querySelector('[data-object-id]') as HTMLDivElement).style.pointerEvents).toBe('')
  })

  it('全选不收它', () => {
    expect(selectAllIds([text(), newer()])).toEqual(['t1'])
  })
})

describe('导出', () => {
  const req = (objects: CanvasObject[]) => {
    const d = emptyDocument()
    return buildExportRequest({
      scope: 'canvas', formats: ['pdf'], filename: 'f', ppi: 600, documentId: 'd',
      doc: { ...d, objects },
    } as ExportRequestInput)
  }

  it('载荷里没有空洞（不再序列化出 null）', () => {
    const out = toExportObjects([text(), newer()])
    expect(out).toHaveLength(1)
    expect(out.every((o) => o != null)).toBe(true)
  })

  it('看得见的新版对象报个数；隐藏的不算', () => {
    expect(newerObjectCount([text(), newer(), newer({ id: 'n2', hidden: true })])).toBe(1)
    expect(req([text(), newer()]).newerObjects).toBe(1)
    expect(req([text()]).newerObjects).toBe(0)
  })

  it('单图范围数的是这次带上的标注', () => {
    const built = buildExportRequest({
      scope: 'original', formats: ['pdf'], filename: 'f', ppi: 600, documentId: 'd',
      doc: emptyDocument(), figureId: 'a.pdf', annotations: [text(), newer()],
    } as ExportRequestInput)
    expect(built.newerObjects).toBe(1)
  })
})
