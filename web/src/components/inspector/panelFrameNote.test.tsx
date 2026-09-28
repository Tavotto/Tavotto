/**
 * 属性里「改用原图的图幅」（ADR 0098 §三）：裁过的面板若原来的可见范围与脚本图幅不相交，
 * 换过去什么都不剩、算出的裁剪框会让 RenderCore 拒掉整份排版（#688）——按钮禁用、就地说明原因，
 * 点了也不改文档；对照：部分相交时照常可点、一次提交。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LEGACY_FRAME_OVERRIDE, type ManifestFrame } from '@/lib/figureFrame'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type CropRect, type PanelObject } from '@/types/document'
import { PanelFrameNote } from './PanelSection'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// 脚本图幅是 figsize 的左半边
const HALF: ManifestFrame = { source: 'savefig', active: false, figsize_mm: [80, 57.6], savefig_mm: [0, 0, 40, 57.6] }

const croppedPanel = (crop: CropRect): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 57.6,
  x: 10,
  y: 10,
  w: 80 * crop.w,
  h: 57.6 * crop.h,
  crop,
  script: 'fig1.py',
  overrides: [{ ...LEGACY_FRAME_OVERRIDE }],
  figureFrame: 1,
})

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  useRenderStore.getState().clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

async function mount(panel: PanelObject) {
  const doc = emptyProject()
  doc.canvases[0].objects = [panel]
  await act(async () => {
    await useDocumentStore.getState().switchDocument(doc, 'd-frame-note')
  })
  const p = useDocumentStore.getState().doc.objects[0] as PanelObject
  seedExactRender(p, { stem: 'Fig1', size_mm: [80, 57.6], elements: [], frame: HALF })
  await act(async () => root.render(<PanelFrameNote panel={p} />))
  return host.querySelector<HTMLButtonElement>('[data-frame-adopt]')!
}

describe('改用原图的图幅：旧裁剪与脚本图幅不相交', () => {
  it('按钮禁用、就地说明原因（读屏经 aria-describedby 读到），点了不改文档', async () => {
    const button = await mount(croppedPanel({ x: 0.6, y: 0.2, w: 0.3, h: 0.5 }))
    expect(button.disabled).toBe(true)
    const reason = host.querySelector('[data-frame-adopt-blocked]')
    expect(reason?.textContent).toBeTruthy()
    expect(button.getAttribute('aria-describedby')).toBe(reason!.id)
    const before = structuredClone(useDocumentStore.getState().doc)
    await act(async () => button.click())
    expect(useDocumentStore.getState().doc).toEqual(before)
  })

  it('对照：部分相交时可点，一次提交去掉 figsize 那条、裁剪收到交集', async () => {
    const button = await mount(croppedPanel({ x: 0.4, y: 0.2, w: 0.3, h: 0.5 }))
    expect(button.disabled).toBe(false)
    expect(host.querySelector('[data-frame-adopt-blocked]')).toBeNull()
    expect(button.getAttribute('aria-describedby')).toBeNull()
    await act(async () => button.click())
    const after = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(after.overrides).toEqual([])
    expect(after.crop?.x).toBeCloseTo(0.8, 9)
  })
})
