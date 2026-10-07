/**
 * 贴着选区的尺寸芯片（2026-10-07 设计审计 §10.1）。此前读数在画布左下角，移动时报的是**指针**坐标；
 * 现在贴在被改的那个框下面，说的是那个框：
 *   - 移动 → 对象的 X, Y（不是指针）；
 *   - 缩放 / 画框 → W × H；
 *   - 方向键微调 → Δ；
 *   - 没有交互 / 没有可报的框 → 不渲染。
 * 主语：`data-measure-chip` 的取值与它的文字；位置按视口变换算（zoom 1、pan 0 时 1 mm = mmToWorld(1) px）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { MeasureChip } from './MeasureChip'
import { literal, setLocale } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type ShapeObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const rect = (): ShapeObject => ({
  id: 'r1', type: 'shape', shape: 'rect', x: 10, y: 20, w: 100, h: 8,
  strokePt: 1, color: '#111111', fill: null,
})

const chip = () => container.querySelector<HTMLElement>('[data-measure-chip]')

beforeEach(async () => {
  useInteractionStore.getState().end()
  useInteractionStore.getState().setNudge(null)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_chip_measure')
  useDocumentStore.getState().commit(literal('加'), (d) => {
    d.objects.push(rect())
  })
  useSelectionStore.getState().set(['r1'])
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, viewW: 800, viewH: 600 })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<MeasureChip />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setNudge(null)
  await setLocale('zh-CN')
})

describe('MeasureChip', () => {
  it('没有交互：不渲染', () => {
    expect(chip()).toBeNull()
  })

  it('移动：报对象的 X, Y（不是指针坐标），贴在框下方居中', () => {
    act(() => {
      useInteractionStore.getState().begin('move')
      useInteractionStore.getState().setCursor({ x: 99, y: 99 })
    })
    expect(chip()!.dataset.measureChip).toBe('position')
    expect(chip()!.textContent).toBe('10.0, 20.0 mm')
    expect(chip()!.style.left).toBe(`${mmToWorld(10) + mmToWorld(100) / 2}px`)
    expect(chip()!.style.top).toBe(`${mmToWorld(28) + 8}px`)
  })

  it('缩放：W × H', () => {
    act(() => useInteractionStore.getState().begin('resize'))
    expect(chip()!.dataset.measureChip).toBe('size')
    expect(chip()!.textContent).toBe('100.0 × 8.0 mm')
  })

  // Codex #833：旋转 90° 的 100×8 横条转出来是竖条（中心 60, 24：x 56–64、y −26–74）；读数仍是逻辑的 W × H，
  // 芯片贴在看得见的外接框下面——按逻辑盒（底边 y=28）摆会压在竖条中段
  it('旋转对象：读数是逻辑 W × H，位置按看得见的外接框（visualBounds）', () => {
    act(() => {
      useDocumentStore.getState().commit(literal('转'), (d) => {
        ;(d.objects[0] as ShapeObject).rotationDeg = 90
      })
      useViewportStore.setState({ viewH: 2000 })
      useInteractionStore.getState().begin('resize')
    })
    expect(chip()!.textContent).toBe('100.0 × 8.0 mm')
    expect(chip()!.style.left).toBe(`${mmToWorld(56) + mmToWorld(8) / 2}px`)
    expect(chip()!.style.top).toBe(`${mmToWorld(74) + 8}px`)
  })

  it('方向键微调：Δ 带正负号', () => {
    act(() => useInteractionStore.getState().setNudge({ dx: 0.5, dy: -1 }))
    expect(chip()!.dataset.measureChip).toBe('offset')
    expect(chip()!.textContent).toBe('Δ +0.5, −1.0 mm')
  })

  it('框贴到视口底：芯片翻到框上面', () => {
    act(() => {
      useViewportStore.setState({ viewH: mmToWorld(28) + 10 })
      useInteractionStore.getState().begin('resize')
    })
    expect(parseFloat(chip()!.style.top)).toBeLessThan(mmToWorld(20))
  })

  // Codex #833：选区几乎占满视口（y 2–102 mm，视口只比它高一点）——下面放不下、翻上去落到负坐标被舞台裁掉。
  // 夹回舞台里：上沿不出界，下沿也不出界
  it('选区占满视口：上下都放不下，芯片夹在舞台里', () => {
    const viewH = mmToWorld(102) + 10
    act(() => {
      useDocumentStore.getState().commit(literal('拉高'), (d) => {
        Object.assign(d.objects[0], { y: 2, h: 100 })
      })
      useViewportStore.setState({ viewH })
      useInteractionStore.getState().begin('resize')
    })
    const top = parseFloat(chip()!.style.top)
    expect(top).toBeGreaterThanOrEqual(0)
    expect(top + 22).toBeLessThanOrEqual(viewH)
  })

  // 横向同理：框中心平移到视口左外 / 右外，芯片（桩宽 80px，按中心定位）整条留在舞台里
  it.each([
    ['左外', -mmToWorld(200)],
    ['右外', mmToWorld(200)],
  ])('框中心在视口%s：芯片横向夹在舞台里', (_side, panX) => {
    const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')!
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.hasAttribute('data-measure-chip') ? 80 : 0
      },
    })
    try {
      act(() => {
        useViewportStore.setState({ panX })
        useInteractionStore.getState().begin('move')
      })
      const left = parseFloat(chip()!.style.left)
      expect(left - 40).toBeGreaterThanOrEqual(0)
      expect(left + 40).toBeLessThanOrEqual(800)
    } finally {
      Object.defineProperty(HTMLElement.prototype, 'offsetWidth', desc)
    }
  })
})
