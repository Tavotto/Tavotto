import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, ManifestElement } from '@/lib/api'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { resizePatch } from '@/store/patchResizeAction'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { PATCH_GID, PATCH_SVG, resizeElement, resizeManifest, resizePanel } from '@/test/patchResizeFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { TooltipProvider } from '../ui/Tooltip'
import { ElementInspector } from './ElementInspector'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) => engineRender(id, patches, opts),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

let root: Root
let host: HTMLDivElement
let element: ManifestElement
const livePanel = () => useDocumentStore.getState().doc.objects[0] as PanelObject
const input = (dimension: 'width' | 'height') => host.querySelector<HTMLInputElement>(`[data-inspector-prop="patch_${dimension}_mm"]`)!
function Scene() {
  const panel = useDocumentStore((s) => s.doc.objects[0]) as PanelObject
  if (!panel) return null
  return <TooltipProvider><ElementInspector panel={panel} /></TooltipProvider>
}

async function setup(extra: Partial<PanelObject> = {}, el = resizeElement()) {
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  useRenderStore.getState().clear()
  useSelectionStore.getState().clear()
  useUiStore.setState({ elementPanelId: 'p1', selectedGids: [PATCH_GID], snapEnabled: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_patch_size_controls')
  useDocumentStore.getState().commit(literal('Add panel'), (d) => { d.objects.push(resizePanel(extra)) })
  useDocumentStore.setState({ past: [], future: [] })
  element = el
  seedExactRender(livePanel(), resizeManifest(element), { svg: PATCH_SVG })
}

beforeEach(async () => {
  await setup()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Scene />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  resetPreview()
  useInteractionStore.getState().end()
})

const type = async (el: HTMLInputElement, value: string) => {
  await act(async () => {
    el.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
}
const fire = (event: string, x = 10) => act(async () => {
  window.dispatchEvent(new MouseEvent(event, { clientX: x, bubbles: true }))
})
const startScrub = async () => {
  const prefix = input('width').parentElement!.querySelector<HTMLElement>('[data-number-scrub]')!
  Object.assign(prefix, { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() })
  await act(async () => prefix.dispatchEvent(new MouseEvent('pointerdown', { clientX: 0, button: 0, bubbles: true })))
}

describe('W/H 精确输入与恢复', () => {
  it('页面 mm 的宽高各一处，修改高度固定顶边且不缩文字', async () => {
    expect(input('width').value).toBe('80')
    expect(input('height').value).toBe('25')
    expect(host.querySelectorAll('[data-inspector-prop="size_frac"]')).toHaveLength(0)
    await type(input('height'), '40')
    expect(livePanel().overrides).toEqual([
      { gid: PATCH_GID, prop: 'size_frac', value: [0.4, 0.4] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.2, 0.65] },
    ])
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(useDocumentStore.getState().past).toHaveLength(1)
    await act(async () => runUndoRedo(false))
    expect(input('height').value).toBe('25')
  })

  it('面板缩放后字段是页面上实际宽高，输入与手柄共用 figure 分数', async () => {
    await act(async () => setup({ w: 100, h: 50 }))
    expect(input('width').value).toBe('40')
    expect(input('height').value).toBe('12.5')
    await type(input('width'), '50')
    expect(livePanel().overrides[0].value).toEqual([0.5, 0.25])
    expect(livePanel().overrides[1].value).toEqual([0.2, 0.5])
  })

  it('裁剪 / 旋转 / 翻转不把可见窗的宽度错当整图宽度', async () => {
    await act(async () => setup({ crop: { x: 0.1, y: 0.1, w: 0.5, h: 0.5 }, rotation: 90, flipH: true, w: 50, h: 100 }))
    expect(input('width').value).toBe('80')
    expect(input('height').value).toBe('25')
    await type(input('width'), '100')
    expect(livePanel().overrides[0].value).toEqual([0.5, 0.25])
  })

  it('恢复只移除尺寸覆盖，已移动的位置与其它元素编辑保留且可撤销', async () => {
    const overrides = [
      { gid: PATCH_GID, prop: 'size_frac', value: [0.5, 0.3] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.2, 0.7] },
      { gid: 'axes_0.texts_0', prop: 'fontsize', value: 14 },
    ]
    await act(async () => setup({ overrides }, resizeElement([0.2, 0.4, 0.5, 0.3])))
    const reset = host.querySelector<HTMLButtonElement>('[data-patch-size-reset] button')!
    expect(reset).not.toBeNull()
    await act(async () => reset.click())
    expect(livePanel().overrides).toEqual(overrides.slice(1))
    expect(useDocumentStore.getState().past).toHaveLength(1)
    await act(async () => runUndoRedo(false))
    expect(livePanel().overrides).toEqual(overrides)
  })

  it('没改数的 Tab 与无效输入不提交', async () => {
    await act(async () => { input('width').focus(); input('width').blur() })
    await type(input('width'), 'not a number')
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('后端拒绝的旧尺寸覆盖不能盖过权威外框，控件从实际画出的框继续编辑', async () => {
    await act(async () => setup({ overrides: [
      { gid: PATCH_GID, prop: 'size_frac', value: [0.001, 0.001] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.8, 0.9] },
    ] }))
    // 模拟原始形状仍被画在 0.2/0.25，尺寸0.4/0.25；被拒绝的请求留在文档中。
    expect(input('width').value).toBe('80')
    expect(input('height').value).toBe('25')
    await type(input('width'), '100')
    expect(livePanel().overrides).toEqual([
      { gid: PATCH_GID, prop: 'size_frac', value: [0.5, 0.25] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.2, 0.5] },
    ])
  })
})

describe('尺寸控件的几何权威与连续编辑', () => {
  it('权威失效收起尺寸而保留样式，旧闭包也不能写', async () => {
    const basis = livePanel()
    await act(async () => useRenderStore.getState().patch(renderKeyOf(basis), { stale: true }))
    expect(host.querySelector('[data-patch-size-block]')).toBeNull()
    expect(host.querySelector('[data-prop="linewidth"]')).not.toBeNull()
    expect(resizePatch(basis, element, [0.2, 0.25, 0.5, 0.25])).toBe(false)
    expect(livePanel().overrides).toEqual([])
  })

  it('连续拖数字保持输入与预览，松手前零文档写入 / 零渲染，松手一条历史', async () => {
    await startScrub()
    await fire('pointermove', 10)
    await fire('pointermove', 20)
    expect(input('width').value).toBe('82')
    expect(livePanel().overrides).toEqual([])
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().elementPreview?.boxes[PATCH_GID]).toEqual([0.2, 0.25, 0.41, 0.25])
    await fire('pointerup', 20)
    expect(livePanel().overrides[0].value).toEqual([0.41, 0.25])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
  })

  it.each(['pointercancel', 'lostpointercapture', 'Escape'])('%s 取消数字预览，后续 pointerup 不写', async (end) => {
    await startScrub()
    await fire('pointermove', 20)
    if (end === 'Escape') await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })))
    else await fire(end)
    await fire('pointerup', 20)
    expect(input('width').value).toBe('80')
    expect(livePanel().overrides).toEqual([])
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().elementPreview).toBeNull()
  })

  it('几何卡卸载作废数字预览，事件监听不会随后补写', async () => {
    await startScrub()
    await fire('pointermove', 20)
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.texts_0'))
    await fire('pointerup', 20)
    expect(livePanel().overrides).toEqual([])
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('同一元素收到新权威几何后，离散收尾丢弃基于旧清单的草稿', async () => {
    await startScrub()
    await fire('pointermove', 20)
    await act(async () => seedExactRender(livePanel(), resizeManifest(resizeElement([0.1, 0.2, 0.6, 0.4])), { svg: PATCH_SVG }))
    await act(async () => finishActiveGesture())
    await fire('pointerup', 20)
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(input('width').value).toBe('120')
    expect(input('height').value).toBe('40')
  })

  it('离散动作先收尾连续尺寸手势，随后撤销只撤这一条', async () => {
    await startScrub()
    await fire('pointermove', 20)
    await act(async () => finishActiveGesture())
    expect(livePanel().overrides[0].value).toEqual([0.41, 0.25])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    await act(async () => runUndoRedo(false))
    expect(livePanel().overrides).toEqual([])
    await fire('pointerup', 20)
    expect(livePanel().overrides).toEqual([])
  })
})
