/**
 * CanvasHud：画布左下角按需出现的读数浮层。
 *
 * 钉住的事实：
 *   1. 交互中有光标与选区时，光标坐标与选区尺寸各带自己的文字标签，两组读数分开；
 *   2. 交互中**既无光标也无选区**时什么都不渲染——不留一只空边框盒子
 *      （旧实现在这一刻仍画边框，参考稿专门列了「无有效读数」这一档）；
 *   3. 改尺寸类交互（resize / draw / crop）以尺寸为主读数，移动时以坐标为主；
 *   4. 非交互 + 非选择工具时只显示工具提示，且对读屏器隐藏；
 *   5. 标签跟界面语言走（英文界面一个汉字都不剩）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CanvasHud } from '@/components/StatusBar'
import { literal, setLocale } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ShapeObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const mount = () => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<CanvasHud />))
}

const rect = (): ShapeObject => ({
  id: 'r1',
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 10,
  w: 100,
  h: 8,
  strokePt: 1,
  color: '#111111',
  fill: null,
})

const addRectAndSelect = () => {
  useDocumentStore.getState().commit(literal('加'), (d) => {
    d.objects.push(rect())
  })
  useSelectionStore.getState().set(['r1'])
}

const drag = (kind: 'move' | 'resize' | 'draw' | 'crop', cursor?: { x: number; y: number }) =>
  act(() => {
    useInteractionStore.getState().begin(kind)
    useInteractionStore.getState().setCursor(cursor ?? null)
  })

const readings = () => container.querySelector('[data-hud-mode]')

beforeEach(async () => {
  localStorage.clear()
  useUiStore.setState({ tool: 'select' })
  useSelectionStore.getState().clear()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setCursor(null)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_hud')
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setCursor(null)
  await setLocale('zh-CN')
})

describe('CanvasHud 读数', () => {
  it('移动中：光标与尺寸各带标签，两组读数分开渲染', () => {
    addRectAndSelect()
    mount()
    drag('move', { x: 12, y: 16 })

    const box = readings()
    expect(box).not.toBeNull()
    const groups = box!.querySelectorAll(':scope > span')
    expect(groups).toHaveLength(2)
    expect(groups[0].textContent).toBe('光标12.0, 16.0 mm')
    expect(groups[1].textContent).toBe('尺寸100.0×8.0 mm')
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull()
  })

  it('交互中但既无光标也无选区：什么都不渲染，不留空盒子', () => {
    mount()
    drag('move')
    expect(container.innerHTML).toBe('')
  })

  it('只有光标没有选区：只渲染坐标一组', () => {
    mount()
    drag('move', { x: 1, y: 2 })
    const box = readings()
    expect(box!.querySelectorAll(':scope > span')).toHaveLength(1)
    expect(box!.textContent).toBe('光标1.0, 2.0 mm')
  })

  it('resize / draw / crop 以尺寸为主读数，move 以坐标为主', () => {
    addRectAndSelect()
    mount()
    drag('move', { x: 0, y: 0 })
    expect(readings()!.getAttribute('data-hud-mode')).toBe('cursor')
    for (const kind of ['resize', 'draw', 'crop'] as const) {
      act(() => useInteractionStore.getState().end())
      drag(kind, { x: 0, y: 0 })
      expect(readings()!.getAttribute('data-hud-mode')).toBe('size')
    }
  })

  it('非交互 + 绘制工具：只显示工具提示，且对读屏器隐藏', () => {
    useUiStore.setState({ tool: 'rect' })
    mount()
    expect(readings()).toBeNull()
    expect(container.textContent).toBe('拖动画出矩形；Esc 取消')
    expect(container.firstElementChild!.getAttribute('aria-hidden')).toBe('true')
  })

  it('非交互 + 选择工具：什么都不渲染', () => {
    mount()
    expect(container.innerHTML).toBe('')
  })

  it('英文界面：标签换成英文，一个汉字都不剩', async () => {
    addRectAndSelect()
    mount()
    drag('move', { x: 12, y: 16 })
    await act(async () => {
      await setLocale('en-US')
    })
    const text = readings()!.textContent ?? ''
    expect(text).toContain('Cursor')
    expect(text).toContain('Size')
    expect(/[一-鿿]/.test(text)).toBe(false)
  })
})
