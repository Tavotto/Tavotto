/**
 * 自动拖动测试（ADR 0075）在**用户真实的文档**上跑，它的第一条承诺是：
 * **跑完文档一个字都没变、历史一条都没多**——收尾走 pointercancel 的取消语义。
 *
 * 判据的主语：documentStore 的 doc 与 past（撤销栈），测试前后各取一次。
 * 反证：把 synthetic.ts 收尾的 `pointercancel` 换成 `pointerup`，
 * 「对象没挪、历史没多」两条必须红（提交前手工跑过一次）。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { startMoveDrag } from '@/canvas/interactions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { perfStart, perfStop } from './core'
import { runSyntheticPass } from './synthetic'

const rect: ShapeObject = {
  id: 's1',
  type: 'shape',
  shape: 'rect',
  x: 20,
  y: 20,
  w: 10,
  h: 10,
  strokePt: 1,
  color: '#111111',
  fill: null,
}

let host: HTMLDivElement

beforeEach(async () => {
  localStorage.clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ tool: 'select', snapEnabled: false })
  useSelectionStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_perf_synth')
  useDocumentStore.getState().commit(literal('加对象'), (d) => {
    d.objects.push({ ...rect })
  })
  useSelectionStore.getState().set(['s1'])

  // 按下即起拖：与 ObjectView 的 onPointerDown 同一个入口
  host = document.createElement('div')
  host.addEventListener('pointerdown', (e) => startMoveDrag(e as unknown as ReactPointerEvent, 's1'))
  document.body.appendChild(host)
  document.elementFromPoint = () => host
}, 20000)

afterEach(() => {
  perfStop()
  host.remove()
})

describe('自动拖动测试', () => {
  it('拖过了（走了 txnUpdate），但文档与撤销栈原样', async () => {
    const before = useDocumentStore.getState()
    const pastBefore = before.past.length
    perfStart()
    const r = await runSyntheticPass(100, 100, { label: 't', movesPerFrame: 2, durationMs: 250 })
    const raw = perfStop()!
    expect(r).toBe('ok')

    const seg = raw.segments.find((s) => s.kind === 'move')
    expect(seg, '应当记下一次画布对象移动').toBeTruthy()
    expect(seg!.source).toBe('synthetic')
    expect(seg!.label).toBe('t')
    expect(seg!.spans['doc.txn_update']?.count ?? 0).toBeGreaterThan(0)

    const after = useDocumentStore.getState()
    const obj = after.doc.objects.find((o) => o.id === 's1')!
    expect([obj.x, obj.y]).toEqual([20, 20])
    expect(after.past.length).toBe(pastBefore)
    expect(after.txn).toBeNull()
  })

  it('按下没有接住任何拖动：回 not_draggable，不留下半截事务', async () => {
    const empty = document.createElement('div')
    document.body.appendChild(empty)
    document.elementFromPoint = () => empty
    perfStart()
    const r = await runSyntheticPass(10, 10, { label: 't', movesPerFrame: 1, durationMs: 400 })
    perfStop()
    empty.remove()
    expect(r).toBe('not_draggable')
    expect(useDocumentStore.getState().txn).toBeNull()
  })
})
