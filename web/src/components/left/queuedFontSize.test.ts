import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture, resetGestureCoordinator } from '@/store/gestureCoordinator'
import { queueFontSize } from './queuedFontSize'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
  String(input).includes('/api/engine/render')
    ? new Promise<Response>((_, reject) =>
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
      )
    : Promise.resolve(new Response('{}', { status: 200 }))) as typeof fetch

const panel: PanelObject = {
  id: 'p', type: 'panel', fileId: 'figure.pdf', fileKind: 'pdf', script: 'figure.py',
  nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides: [], figureFrame: 1,
}
const manifest = (gids: string[]) => ({
  stem: 'figure', size_mm: [80, 60],
  elements: gids.map((gid) => ({
    gid, role: 'axis_label', label: gid, bbox: [0, 0, 1, 1], draggable: false,
    editable: [{ prop: 'fontsize', type: 'number', value: 9, min: 1, max: 100 }],
  })),
}) as unknown as Manifest
const live = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'p') as PanelObject

beforeEach(async () => {
  useRenderStore.getState().clear()
  useUiStore.setState({ status: null })
  const project = emptyProject()
  project.canvases[0].objects = [panel]
  await useDocumentStore.getState().switchDocument(project, 'queued-font-test')
  seedExactRender(live(), manifest(['x', 'y']))
  setOverride('p', 'x', 'fontsize', 18, true)
})

afterEach(() => {
  resetGestureCoordinator()
  useRenderStore.getState().clear()
})

describe('字号等待当前版本清单', () => {
  it('合并同一角色的最新值，并只在新清单的目标上写一次', async () => {
    const before = useDocumentStore.getState().past.length
    const a = queueFontSize(live(), 'axis_label', 20)
    const b = queueFontSize(live(), 'axis_label', 22)
    // 新版本增删目标时，以新清单的角色为准，不能把旧 gid 再写回去。
    seedExactRender(live(), manifest(['x', 'new-y']))
    await Promise.all([a, b])
    expect(live().overrides.filter((p) => p.prop === 'fontsize')).toEqual([
      expect.objectContaining({ gid: 'x', value: 22 }),
      expect.objectContaining({ gid: 'new-y', value: 22 }),
    ])
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
  })

  it('撤销原编辑会取消排队值，不在旧版本上补写', async () => {
    const done = queueFontSize(live(), 'axis_label', 22)
    await useDocumentStore.getState().undo()
    await done
    expect(live().overrides).toEqual([])
    expect(useUiStore.getState().status?.key).toBe('stylePanel.pendingSizeCancelled')
  })

  it('当前变体失败时取消排队值，其他变体失败不触发取消', async () => {
    let settled = false
    const done = queueFontSize(live(), 'axis_label', 22).then(() => { settled = true })
    useRenderStore.getState().patch('other', { status: 'error' })
    await Promise.resolve()
    expect(settled).toBe(false)
    useRenderStore.getState().patch(renderKeyOf(live()), { status: 'error' })
    await done
    expect(live().overrides).toEqual([expect.objectContaining({ gid: 'x', value: 18 })])
    expect(useUiStore.getState().status?.key).toBe('stylePanel.pendingSizeCancelled')
  })

  it('不会把到达的字号写入另一条正在进行的事务', async () => {
    useDocumentStore.getState().beginTxn(literal('other gesture'))
    let settled = false
    const done = queueFontSize(live(), 'axis_label', 22).then(() => { settled = true })
    seedExactRender(live(), manifest(['x', 'y']))
    await Promise.resolve()
    expect(settled).toBe(false)
    expect(live().overrides).toEqual([expect.objectContaining({ gid: 'x', value: 18 })])
    useDocumentStore.getState().endTxn()
    await done
    expect(live().overrides).toEqual([
      expect.objectContaining({ gid: 'x', value: 22 }),
      expect.objectContaining({ gid: 'y', value: 22 }),
    ])
  })

  it('没有事务的连续手势收尾后也会唤醒队列', async () => {
    const unregister = registerGesture(() => {})
    let settled = false
    const done = queueFontSize(live(), 'axis_label', 22).then(() => { settled = true })
    seedExactRender(live(), manifest(['x', 'y']))
    await Promise.resolve()
    expect(settled).toBe(false)
    unregister()
    await done
    expect(live().overrides).toContainEqual(expect.objectContaining({ gid: 'y', value: 22 }))
  })
})
