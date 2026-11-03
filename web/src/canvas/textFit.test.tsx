/**
 * 单行文字旋转前收框到贴字宽（ADR 0163）：字在页面上不动（按 align 锚住左 / 中 / 右），
 * 多行 / 正在折行的不收，回到 0 或取消手势连收框一起不留，一次手势一条历史。
 *
 * jsdom 没有排版：给文字 DOM 装一个 getBoundingClientRect——量法的契约是「临时 max-content + pre 读宽」，
 * 这里的桩正好按这两个样式回答自然宽 / 当前框宽。
 */
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type TextObject } from '@/types/document'
import { MM_PER_PT } from '@/lib/units'
import { FIT_MARGIN_EM, FIT_MARGIN_RATIO, fitBeforeRotate } from './textFit'
import { startRotateDrag } from './rotateDrag'

const text = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1', type: 'text', x: 20, y: 20, w: 40, h: 5, text: 'Hello', sizePt: 12, bold: false,
  color: '#000', align: 'left', ...over,
})
const CENTER_Y = 22.5
const px = (mm: number) => mmToWorld(mm)
const down = (x: number, y: number) =>
  ({ clientX: px(x), clientY: px(y), button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
const fire = (type: string, x: number, y: number) =>
  window.dispatchEvent(new MouseEvent(type, { clientX: px(x), clientY: px(y), bubbles: true }))
const byId = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 't1') as TextObject

let host: HTMLDivElement
/** 在 DOM 里放 [data-object-id] > [data-text-body]，getBoundingClientRect().width 按样式回答 */
function mount(o: TextObject, naturalMm: number) {
  host = document.createElement('div')
  host.setAttribute('data-object-id', o.id)
  const body = document.createElement('div')
  body.setAttribute('data-text-body', '')
  body.getBoundingClientRect = () =>
    ({ width: body.style.width === 'max-content' ? px(naturalMm) : px(o.w) }) as DOMRect
  host.appendChild(body)
  document.body.appendChild(host)
  useDocumentStore.getState().commit(literal('加'), (d) => { d.objects.push(o) })
}

beforeEach(async () => {
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fit')
})
afterEach(() => {
  host?.remove()
  useInteractionStore.getState().end()
})

/** 从框顶中点上方起拖到正右方：90° */
function rotate90(cx: number) {
  startRotateDrag(down(cx, 5), 't1')
  fire('pointermove', cx + 30, CENTER_Y)
  fire('pointerup', cx + 30, CENTER_Y)
}

describe('单行收框：字不动', () => {
  it.each([
    ['left', 20, 30],
    ['center', 31, 40],
    ['right', 42, 50],
  ] as const)('align=%s：收到字宽，锚点保持，x = %s', (align, wantX, newCenter) => {
    mount(text({ align }), 18)
    rotate90(newCenter) // 收框后的中心：旋转绕它
    const o = byId()
    expect(Math.abs(o.rotationDeg! - 90)).toBeLessThan(4) // 中心因余量偏 ≤1mm，角度差约 3°内
    const margin = 18 * FIT_MARGIN_RATIO + FIT_MARGIN_EM * 12 * MM_PER_PT // 字宽 × 1.04 + 0.5 em
    expect(o.w).toBeCloseTo(18 + margin, 3)
    expect(o.x).toBeCloseTo(wantX - (align === 'left' ? 0 : align === 'center' ? margin / 2 : margin), 3)
    expect([o.y, o.h]).toEqual([20, 5])
  })

  it('一条历史：撤销后框与角度一起回来', () => {
    mount(text({ align: 'center' }), 18)
    rotate90(40)
    useDocumentStore.getState().undo()
    const o = byId()
    expect([o.x, o.w, o.rotationDeg]).toEqual([20, 40, undefined])
  })

  it('取消手势（pointercancel）：连收框一起丢', () => {
    mount(text({ align: 'center' }), 18)
    startRotateDrag(down(40, 5), 't1')
    fire('pointermove', 70, CENTER_Y)
    expect(byId().w).toBeLessThan(40)
    fire('pointercancel', 70, CENTER_Y)
    const o = byId()
    expect([o.x, o.w, o.rotationDeg]).toEqual([20, 40, undefined])
  })

  it('拖回 0：框恢复原宽，不留字段', () => {
    mount(text({ align: 'right' }), 18)
    startRotateDrag(down(40, 5), 't1')
    fire('pointermove', 70, CENTER_Y)
    fire('pointermove', 40, 5)
    fire('pointerup', 40, 5)
    const o = byId()
    expect([o.x, o.w]).toEqual([20, 40])
    expect('rotationDeg' in o).toBe(false)
  })
})

describe('不收的情况', () => {
  it('正文含换行：保持原框宽', () => {
    mount(text({ text: 'a\nbb' }), 18)
    rotate90(40)
    expect([byId().x, byId().w, Math.round(byId().rotationDeg!)]).toEqual([20, 40, 90])
  })

  it('正在折行（自然宽度超过框宽）：保持原框宽', () => {
    mount(text({ text: 'a long sentence' }), 55)
    rotate90(40)
    expect([byId().x, byId().w, Math.round(byId().rotationDeg!)]).toEqual([20, 40, 90])
  })

  it('已经转过的不再收（只在 0 → 非 0 那一次）', () => {
    const o = text({ rotationDeg: 30 })
    mount(o, 18)
    expect(fitBeforeRotate(o)).toBeNull()
  })
})
