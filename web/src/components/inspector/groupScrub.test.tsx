/**
 * 整组 X / Y / W / H 拖动数字（scrub）：起手时的 onChange 闭包会被反复调用，
 * 而整组变换是对「当前文档」施加增量——必须按此刻的范围算，不能累加（Codex 线程 4614452837）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { emptyProject, type CanvasObject } from '@/types/document'
import { TransformSection } from './TransformSection'

const captured = vi.hoisted(() => ({ fns: {} as Record<string, (v: number) => void> }))
vi.mock('../ui/Input', async (orig) => {
  const mod = await orig<typeof import('../ui/Input')>()
  return {
    ...mod,
    NumberField: (p: { dataProp?: string; onChange: (v: number) => void }) => {
      if (p.dataProp) captured.fns[p.dataProp] = p.onChange
      return null
    },
  }
})

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const rect = (id: string, x: number): CanvasObject =>
  ({ id, type: 'shape', shape: 'rect', x, y: 10, w: 10, h: 10, strokePt: 1, color: '#000000', fill: '#ff0000', groupId: 'g' }) as CanvasObject

let root: Root
let host: HTMLDivElement
const objs = () => useDocumentStore.getState().doc.objects

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_scrub_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(rect('a', 10), rect('b', 20))
  })
  useSelectionStore.getState().set(['a', 'b'])
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <TransformSection objs={objs()} />
      </TooltipProvider>,
    )
  })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('整组 scrub 不累加', () => {
  it('X：同一个起手闭包连发 15、20 → 整组停在 X=20', () => {
    const onX = captured.fns['transform.x']
    // 参考点缺省为左上：起手 X = 10
    act(() => onX(15))
    act(() => onX(20))
    expect(objs().find((o) => o.id === 'a')!.x).toBeCloseTo(20, 6)
    expect(objs().find((o) => o.id === 'b')!.x).toBeCloseTo(30, 6)
  })

  it('W：连发 30、30 → 整组宽 30，不是 90', () => {
    const onW = captured.fns['transform.w']
    // 整组 x 10…30（宽 20）；缺省不锁比例
    act(() => onW(30))
    act(() => onW(30))
    const a = objs().find((o) => o.id === 'a')!
    const b = objs().find((o) => o.id === 'b')!
    expect(b.x + b.w - a.x).toBeCloseTo(30, 6)
  })
})
