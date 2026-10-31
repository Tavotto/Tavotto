/**
 * 主语：用户拖一个流程图框的手柄时，松手前的文档 / 渲染请求与松手后的完整 overrides。
 * 线框坐标按页面变换验证；真 matplotlib 的 padding / 圆角 / 写回由后端与 E2E 看护。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, ManifestElement } from '@/lib/api'
import { round4 } from '@/lib/axesLayout'
import { panelFullRect } from '@/lib/elementGeom'
import { contentToPageVec, panelContentTransform } from '@/lib/panelTransform'
import { runUndoRedo, useKeyboard } from '@/hooks/useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { cancelActivePointerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { flushPreviewFrame, resetPreview } from '@/store/svgPreviewStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { seedExactRender } from '@/test/renderFixtures'
import { PATCH_GID, PATCH_SVG, resizeElement, resizeManifest, resizePanel } from '@/test/patchResizeFixtures'
import { emptyProject, type PanelObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'
import { startElementDrag, startPatchResize } from './interactions'
import { resetNudge } from './nudge'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) => engineRender(id, patches, opts),
}))
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let element: ManifestElement
let root: Root
let host: HTMLDivElement
const livePanel = () => useDocumentStore.getState().doc.objects[0] as PanelObject
const layout = () => {
  const full = panelFullRect(livePanel())
  return { width: mmToWorld(full.w), height: mmToWorld(full.h) }
}
const down = () => ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as React.PointerEvent
const fire = (type: string, x = 0, y = 0) => {
  window.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true }))
  flushPreviewFrame()
}
function Scene() { useKeyboard(); return <OverlaySvg /> }

async function setup(extra: Partial<PanelObject> = {}) {
  engineRender.mockReset()
  engineRender.mockReturnValue(new Promise(() => {}))
  resetPreview()
  useRenderStore.getState().clear()
  useSelectionStore.getState().clear()
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 1000, viewH: 700 })
  useUiStore.setState({ tool: 'select', elementPanelId: 'p1', selectedGids: [PATCH_GID], snapEnabled: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_patch_resize')
  useDocumentStore.getState().commit(literal('Add panel'), (d) => { d.objects.push(resizePanel(extra)) })
  useDocumentStore.setState({ past: [], future: [] })
  element = resizeElement()
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
  await act(async () => {
    cancelActivePointerGesture()
    resetNudge()
    root.unmount()
  })
  host.remove()
  resetPreview()
  useInteractionStore.getState().end()
})

describe('图框手柄：单次提交与取消', () => {
  it('支持的单选图框有八个独立手柄，子图既有 resizable 不被接管', async () => {
    expect(host.querySelectorAll('[data-patch-resize]')).toHaveLength(8)
    await act(async () => useUiStore.getState().setSelectedGid('axes_0'))
    expect(host.querySelectorAll('[data-patch-resize]')).toHaveLength(0)
    expect(host.querySelectorAll('[data-element-handle]')).toHaveLength(8)
  })

  it.each(['拖框', '方向键'])('%s 预览期间八个尺寸手柄与真实轮廓走同一段位移', async (mode) => {
    const handlePositions = () => [...host.querySelectorAll('[data-patch-resize]')].map((h) => [
      Number(h.getAttribute('x')), Number(h.getAttribute('y')),
    ])
    const before = handlePositions()
    const [dx, dy] = mode === '拖框' ? [30, 20] : [mmToWorld(0.5), mmToWorld(0.5)]
    await act(async () => {
      if (mode === '拖框') {
        startElementDrag(down(), livePanel(), element, layout())
        fire('pointermove', dx, dy)
      } else {
        for (const key of ['ArrowRight', 'ArrowDown']) {
          window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
        }
        flushPreviewFrame()
      }
    })
    expect(livePanel().overrides).toEqual([])
    expect(engineRender).not.toHaveBeenCalled()
    const path = host.querySelector('[data-element-outline="selected"]')!.getAttribute('d')!
    expect(path).toContain(`M${(mmToWorld(40) + dx).toFixed(2)},${(mmToWorld(25) + dy).toFixed(2)}`)
    const after = handlePositions()
    expect(after).toHaveLength(8)
    after.forEach(([x, y], i) => {
      expect(x - before[i][0]).toBeCloseTo(dx, 6)
      expect(y - before[i][1]).toBeCloseTo(dy, 6)
    })
  })

  it('连续拖动只画目标框，松手一次提交尺寸与锚点，不动文字和连线', async () => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'se')
      for (let i = 1; i <= 20; i++) fire('pointermove', layout().width * 0.1 * i / 20, layout().height * 0.1 * i / 20)
    })
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().elementPreview?.boxes[PATCH_GID]).toEqual([0.2, 0.25, 0.5, 0.35])
    expect(host.querySelector('[data-element-box="selected"]')).not.toBeNull()
    await act(async () => fire('pointerup'))
    expect(livePanel().overrides).toEqual([
      { gid: PATCH_GID, prop: 'size_frac', value: [0.5, 0.35] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.2, 0.6] },
    ])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-patch-resize]')).toBeNull()
  })

  it.each(['pointercancel', 'lostpointercapture', 'Escape'])('%s 作废后松手也不提交', async (end) => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'nw')
      fire('pointermove', 50, 20)
      if (end === 'Escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      else fire(end)
      fire('pointerup', 50, 20)
    })
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
    expect(useInteractionStore.getState().elementPreview).toBeNull()
  })

  it('只点手柄或拖出去又拖回来均不补写原始值', async () => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'e')
      fire('pointerup')
      startPatchResize(down(), livePanel(), element, layout(), 'e')
      fire('pointermove', 100, 0)
      fire('pointermove', 0, 0)
      fire('pointerup')
    })
    expect(livePanel().overrides).toEqual([])
    expect(useDocumentStore.getState().past).toHaveLength(0)
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('撤销与重做将尺寸和位置作为一次编辑，渲染在途不丢选区', async () => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'se')
      fire('pointermove', 80, 40)
      fire('pointerup')
    })
    const resized = structuredClone(livePanel().overrides)
    await act(async () => runUndoRedo(false))
    expect(livePanel().overrides).toEqual([])
    expect(useUiStore.getState().selectedGids).toEqual([PATCH_GID])
    expect(host.querySelectorAll('[data-patch-resize]')).toHaveLength(8)
    await act(async () => runUndoRedo(true))
    expect(livePanel().overrides).toEqual(resized)
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })
})

describe('几何权威与页面变换', () => {
  it('权威缺席时没有手柄，直接调用也不能把旧框写进去', async () => {
    await act(async () => useRenderStore.getState().patch(renderKeyOf(livePanel()), { stale: true }))
    expect(host.querySelector('[data-patch-resize]')).toBeNull()
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'se')
      fire('pointermove', 50, 30)
      fire('pointerup')
    })
    expect(livePanel().overrides).toEqual([])
    expect(useUiStore.getState().selectedGids).toEqual([PATCH_GID])
  })

  it('拖动途中脚本变更使依据过期，松手不写旧几何', async () => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'se')
      fire('pointermove', 50, 30)
      useRenderStore.getState().patch(renderKeyOf(livePanel()), { stale: true })
      fire('pointerup')
    })
    expect(livePanel().overrides).toEqual([])
    expect(engineRender).not.toHaveBeenCalled()
  })

  it.each([90, 180, 270])('旋转 %s° 且水平翻转时，屏幕拖动仍改正确的内容边', async (rotation) => {
    await act(async () => setup({ rotation: rotation as 90 | 180 | 270, flipH: true, w: rotation === 180 ? 200 : 100, h: rotation === 180 ? 100 : 200 }))
    const l = layout()
    const [dx, dy] = contentToPageVec(panelContentTransform(livePanel()), l.width * 0.1, l.height * 0.05)
    await act(async () => {
      startPatchResize(down(), livePanel(), element, l, 'se')
      fire('pointermove', dx, dy)
      fire('pointerup')
    })
    expect(livePanel().overrides).toEqual([
      { gid: PATCH_GID, prop: 'size_frac', value: [0.5, 0.3] },
      { gid: PATCH_GID, prop: 'pos_frac', value: [0.2, 0.55] },
    ])
  })

  it('拖动中改变视口倍率按增量重建基准，不重算已走过的位移', async () => {
    const l = layout()
    await act(async () => {
      startPatchResize(down(), livePanel(), element, l, 'e')
      fire('pointermove', l.width * 0.1, 0)
      useViewportStore.setState({ zoom: 2 })
      fire('pointermove', l.width * 0.2, 0)
      fire('pointerup')
    })
    expect(livePanel().overrides.find((o) => o.prop === 'size_frac')?.value).toEqual([0.55, 0.25])
  })

  it('缩放后整体拖框仍带文字和连接端点，尺寸保持上次结果', async () => {
    await act(async () => {
      startPatchResize(down(), livePanel(), element, layout(), 'e')
      fire('pointermove', layout().width * 0.05, 0)
      fire('pointerup')
    })
    element = resizeElement([0.2, 0.25, 0.45, 0.25])
    await act(async () => seedExactRender(livePanel(), resizeManifest(element), { svg: PATCH_SVG }))
    const l = layout()
    await act(async () => {
      startElementDrag(down(), livePanel(), element, l)
      fire('pointermove', l.width * 0.1, l.height * 0.1)
      fire('pointerup')
    })
    const props = (gid: string, prop: string) => livePanel().overrides.find((o) => o.gid === gid && o.prop === prop)?.value
    expect(props(PATCH_GID, 'size_frac')).toEqual([0.45, 0.25])
    expect((props(PATCH_GID, 'pos_frac') as number[]).map(round4)).toEqual([0.3, 0.6])
    expect(props('axes_0.texts_0', 'pos_frac')).toEqual([0.4, 0.45])
    expect(props('axes_0.texts_1.arrow', 'endpoints_frac')).toEqual([0.7, round4(0.475), 0.75, 0.375])
  })
})
