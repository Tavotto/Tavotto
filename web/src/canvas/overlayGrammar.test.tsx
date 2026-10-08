/**
 * 画布覆盖层语法（2026-10-07 设计审计 §10.1，token 在 index.css 的「画布覆盖层」那一段）。钉：
 *   1. 手柄 8px 视觉 + 16px 命中；命中层带稳定钩子，视觉层填 `--handle-fill`；
 *   2. 沿边有命中带：按在边上（不是边中点的手柄上）也起缩放；
 *   3. 覆盖层里没有写死的颜色（#fff / rgba(27,27,24,…)），也没有第二种虚线；
 *   4. 框选是实线；用户参考线静止 50%；吸附线满色 + × 帽；
 *   5. 裁剪：遮罩走 `--color-scrim`，八个位置是 L 角标 / 短横杠，命中区 16px。
 * 主语：渲染出来的 OverlaySvg 的 DOM（data 钩子、属性与内联 style），以及按下命中带之后 interactionStore 的 kind。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'
import { OverlaySvg } from './OverlaySvg'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const rect: ShapeObject = {
  id: 'r1', type: 'shape', shape: 'rect', x: 20, y: 20, w: 60, h: 30,
  strokePt: 1, color: '#111111', fill: null,
}

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  useUiStore.setState({ elementPanelId: null, cropTargetId: null, editingTextId: null, selectedGids: [] })
  useInteractionStore.getState().end()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_overlay_grammar')
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(rect)
    d.guides.push({ axis: 'x', pos: 5 })
  })
  useSelectionStore.getState().set(['r1'])
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<OverlaySvg />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
})

describe('覆盖层语法', () => {
  it('手柄：16px 命中层带钩子，8px 视觉层填 --handle-fill；中心重合', () => {
    const hit = container.querySelector<SVGRectElement>('[data-handle="se"]')!
    expect(hit.getAttribute('width')).toBe('16')
    expect(hit.getAttribute('fill')).toBe('transparent')
    const mark = hit.previousElementSibling as SVGRectElement
    expect(mark.style.fill).toBe('var(--handle-fill)')
    expect(Number(mark.getAttribute('width'))).toBeLessThanOrEqual(8)
    const cx = (r: Element) => Number(r.getAttribute('x')) + Number(r.getAttribute('width')) / 2
    expect(Math.abs(cx(mark) - cx(hit))).toBeLessThanOrEqual(1)
  })

  it('沿边的命中带：按在右边上就起缩放', () => {
    const strip = container.querySelector<SVGRectElement>('[data-edge-strip="e"]')!
    expect(strip).not.toBeNull()
    const down = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, clientX: 300, clientY: 130 })
    Object.assign(down, { pointerType: 'mouse', pointerId: 1 })
    act(() => {
      strip.dispatchEvent(down)
    })
    expect(useInteractionStore.getState().kind).toBe('resize')
    act(() => {
      window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 300, clientY: 130 }))
    })
  })

  it('没有写死的颜色、没有第二种虚线', () => {
    const html = container.innerHTML
    expect(html).not.toMatch(/#fff\b|#ffffff|rgba\(27/i)
    expect(container.querySelectorAll('[stroke-dasharray]')).toHaveLength(0)
  })

  it('用户参考线静止 50%；吸附线满色、两端 × 帽', async () => {
    const guide = container.querySelector('[data-user-guide="x"] line')!
    expect(guide.getAttribute('stroke-opacity')).toBe('0.5')
    await act(async () => useInteractionStore.getState().setSnap([40], []))
    const snap = container.querySelector('[data-snap-line="x"]')!
    expect(snap.querySelector('line')!.hasAttribute('stroke-opacity')).toBe(false)
    expect(snap.querySelector('path')!.getAttribute('d')!.match(/M/g)!.length).toBe(4)
  })

  it('框选是实线', async () => {
    await act(async () => useInteractionStore.getState().setMarquee({ x: 0, y: 0, w: 10, h: 10 }))
    const m = container.querySelector<SVGRectElement>('[data-marquee]')!
    expect(m.style.strokeDasharray).toBe('')
  })

  it('选中不着色：选框没有底色', () => {
    const outline = [...container.querySelectorAll('rect')].find((r) => r.getAttribute('stroke') === 'var(--color-sel)')!
    expect(outline.getAttribute('fill')).toBe('none')
  })
})
