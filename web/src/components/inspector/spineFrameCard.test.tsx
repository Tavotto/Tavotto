/**
 * 子图边框卡的逐边重置（Codex #829 P2）。
 *
 * 逐边那一行只有一颗重置钮，作用是「这一边的颜色与线宽一起回到脚本值」。
 * 此前它连调两次 `clearOverride`（颜色一次、线宽一次）= 两条历史：撤销一次只回来一半。
 * 合同：点一下 → 两条 override 都没了、历史只多一条；撤销一次 → 两条都回来。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: vi.fn(() => new Promise(() => {})),
}))
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

import { literal } from '@/i18n'
import type { EditableField, ManifestElement } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { SpineFrameCard } from './controls/SpineFrameCard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const f = (prop: string, type: EditableField['type'], value: unknown, extra = {}): EditableField =>
  ({ prop, type, value, ...extra }) as EditableField

const axes: ManifestElement = {
  gid: 'axes_0',
  role: 'axes',
  label: '子图 1',
  bbox: [0.1, 0.1, 0.8, 0.8],
  draggable: false,
  editable: [
    f('spine_color', 'color', '#000000'),
    f('spine_linewidth', 'number', 0.8, { min: 0, max: 4, step: 0.1, unit: 'pt' }),
    f('spine_left_color', 'color', '#000000'),
    f('spine_left_linewidth', 'number', 0.8, { min: 0, max: 4, step: 0.1, unit: 'pt' }),
  ],
} as ManifestElement

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig.py',
  overrides: [
    { gid: 'axes_0', prop: 'spine_left_color', value: '#ff0000' },
    { gid: 'axes_0', prop: 'spine_left_linewidth', value: 2 },
  ],
} as unknown as PanelObject

const livePanel = (): PanelObject => {
  const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1')
  if (p?.type !== 'panel') throw new Error('测试面板没了')
  return p
}
const sideOverrides = () =>
  livePanel()
    .overrides.filter((o) => o.gid === 'axes_0' && o.prop.startsWith('spine_left_'))
    .map((o) => o.prop)
    .sort()

function Harness() {
  const p = useDocumentStore((s) => s.doc.objects.find((o) => o.id === 'p1')) as PanelObject
  return (
    <TooltipProvider>
      <SpineFrameCard panel={p} element={axes} />
    </TooltipProvider>
  )
}

let root: Root
let host: HTMLDivElement

beforeEach(async () => {
  document.body.innerHTML = ''
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_spine_frame')
  useDocumentStore.getState().commit(literal('加面板'), (d) => {
    d.objects.push(structuredClone(panel))
  })
  useDocumentStore.setState({ past: [], future: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Harness />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('逐边重置是一条历史（Codex #829）', () => {
  it('左边那一行的重置：颜色与线宽一起回到脚本值，历史只多一条，撤销一次两条都回来', async () => {
    expect(sideOverrides()).toEqual(['spine_left_color', 'spine_left_linewidth'])
    // 联动行没改过 → 不挂重置钮；唯一那颗是左边那一行的
    const resets = host.querySelectorAll<HTMLButtonElement>('[data-spine-frame] [data-reset-prop]')
    expect(resets).toHaveLength(1)

    const before = useDocumentStore.getState().past.length
    await act(async () => resets[0].click())
    expect(sideOverrides()).toEqual([])
    expect(useDocumentStore.getState().past.length).toBe(before + 1)

    await act(async () => runUndoRedo(false))
    expect(sideOverrides()).toEqual(['spine_left_color', 'spine_left_linewidth'])
  })
})

describe('联动行的颜色与线宽各自可恢复（Codex #829 P2）', () => {
  const linkedOverrides = () =>
    livePanel()
      .overrides.filter((o) => o.gid === 'axes_0' && (o.prop === 'spine_color' || o.prop === 'spine_linewidth'))
      .map((o) => o.prop)
      .sort()

  it.each([
    ['spine_color', '恢复边框颜色'],
    ['spine_linewidth', '恢复边框线宽'],
  ])('两条都改过：只恢复 %s，另一条留着', async (prop, itemText) => {
    await act(async () => {
      useDocumentStore.getState().commit(literal('改联动边框'), (d) => {
        const p = d.objects.find((o) => o.id === 'p1') as PanelObject
        p.overrides.push({ gid: 'axes_0', prop: 'spine_color', value: '#00ff00' })
        p.overrides.push({ gid: 'axes_0', prop: 'spine_linewidth', value: 1.5 })
      })
    })
    const trigger = host.querySelector<HTMLButtonElement>('[data-spine-frame] [data-reset-menu]')!
    expect(trigger).toBeTruthy()
    await act(async () => {
      trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    const items = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]'))
    expect(items.map((m) => m.getAttribute('data-reset-field'))).toEqual(['spine_color', 'spine_linewidth', '*'])
    expect(items[prop === 'spine_color' ? 0 : 1].textContent).toBe(itemText)
    await act(async () => items[prop === 'spine_color' ? 0 : 1].click())
    expect(linkedOverrides()).toEqual([prop === 'spine_color' ? 'spine_linewidth' : 'spine_color'])
    // 逐边那两条不受牵连
    expect(sideOverrides()).toEqual(['spine_left_color', 'spine_left_linewidth'])
  })
})
