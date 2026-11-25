/**
 * 填充与描边补齐（ADR 0185 第二批）在前端的接线：外观命令（D / ⇧X / / / I）一次 commit、选区里不适用的不动；
 * 吸管的颜色解析与上色规则；导出载荷与裁切认新字段（缺省不发）；格式刷带上新字段。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { useKeyboard } from '@/hooks/useKeyboard'
import { copySelectionStyle, pasteSelectionStyle } from '@/store/actions'
import { applyEyedropper } from '@/store/appearanceActions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ArrowObject, type CanvasObject, type PathObject, type ShapeObject, type TextObject } from '@/types/document'
import { applyColorSample, parseCssColor } from './appearance'
import { visibleContentBounds } from './exportCrop'
import { toExportObjects } from './exportPayload'
import { MM_PER_PT } from './units'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const rect = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 'r',
  type: 'shape',
  shape: 'rect',
  x: 10,
  y: 10,
  w: 30,
  h: 20,
  strokePt: 2,
  color: '#aa0000',
  fill: '#00aa00',
  ...over,
})
const arrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a',
  type: 'arrow',
  x: 0,
  y: 0,
  w: 20,
  h: 10,
  start: { rx: 0, ry: 0 },
  end: { rx: 1, ry: 1 },
  strokePt: 3,
  color: '#0000aa',
  head: 'end',
  ...over,
})
const text: TextObject = { id: 't', type: 'text', text: 'x', x: 0, y: 0, w: 10, h: 5, sizePt: 8, bold: false, color: '#123456', align: 'left' }

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past.length

async function seed(list: CanvasObject[], sel: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_appearance_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...list)
  })
  useDocumentStore.setState({ past: [], future: [] })
  useSelectionStore.getState().set(sel)
}

function Host() {
  useKeyboard()
  return null
}
let root: Root
let host: HTMLDivElement
beforeEach(async () => {
  useUiStore.setState({ tool: 'select', editingTextId: null, elementPanelId: null, cropTargetId: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<Host />))
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
const key = (k: string, init: KeyboardEventInit = {}) => {
  const ev = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init })
  act(() => {
    window.dispatchEvent(ev)
  })
  return ev
}

describe('外观命令', () => {
  it('D：回到新画对象的样子——无填充、1 pt 墨色实线、描边附加属性清掉；文字不动；一次 commit', async () => {
    await seed(
      [rect({ dash: 'dashed', dashPattern: [4, 2], miterLimit: 3, strokeAlign: 'outside', stroke: false, fillOpacity: 0.5 }), arrow({ strokeOpacity: 0.4 }), text],
      ['r', 'a', 't'],
    )
    key('d')
    const r = byId<ShapeObject>('r')
    expect(r).toMatchObject({ color: '#1B1B18', strokePt: 1, fill: null })
    for (const k of ['dash', 'dashPattern', 'miterLimit', 'strokeAlign', 'stroke', 'fillOpacity'] as const) expect(k in r).toBe(false)
    expect(byId<ArrowObject>('a')).toMatchObject({ color: '#1B1B18', strokePt: 1 })
    expect('strokeOpacity' in byId<ArrowObject>('a')).toBe(false)
    expect(byId<TextObject>('t')).toEqual(text)
    expect(past()).toBe(1)
  })

  it('⇧X：互换填色和描边色（连同不透明度）；没填充 → 换完没描边；箭头不动', async () => {
    await seed([rect({ fillOpacity: 0.3, strokeOpacity: 0.8 }), rect({ id: 'r2', fill: null }), arrow()], ['r', 'r2', 'a'])
    key('X', { shiftKey: true })
    expect(byId<ShapeObject>('r')).toMatchObject({ fill: '#aa0000', color: '#00aa00', fillOpacity: 0.8, strokeOpacity: 0.3 })
    expect(byId<ShapeObject>('r2')).toMatchObject({ fill: '#aa0000', stroke: false })
    expect(byId<ArrowObject>('a').color).toBe('#0000aa')
    expect(past()).toBe(1)
  })

  it('/：无填充；没有可清的填充时不消费', async () => {
    await seed([rect()], ['r'])
    key('/')
    expect(byId<ShapeObject>('r').fill).toBeNull()
    expect(key('/').defaultPrevented).toBe(false)
    expect(past()).toBe(1)
  })

  it('没选能上色的东西：D / ⇧X 不消费', async () => {
    await seed([text], ['t'])
    expect(key('d').defaultPrevented).toBe(false)
    expect(key('X', { shiftKey: true }).defaultPrevented).toBe(false)
  })

  it('I：切到吸管；Esc 回到选择', async () => {
    await seed([rect()], ['r'])
    key('i')
    expect(useUiStore.getState().tool).toBe('eyedropper')
    key('Escape')
    expect(useUiStore.getState().tool).toBe('select')
  })
})

describe('吸管', () => {
  it('parseCssColor：rgb / rgba / #hex / none / 全透明', () => {
    expect(parseCssColor('rgb(255, 0, 16)')).toBe('#ff0010')
    expect(parseCssColor('rgba(1, 2, 3, 0.5)')).toBe('#010203')
    expect(parseCssColor('rgba(1, 2, 3, 0)')).toBeNull()
    expect(parseCssColor('#ABC')).toBe('#aabbcc')
    expect(parseCssColor('none')).toBeNull()
    expect(parseCssColor('url(#g)')).toBeNull()
  })

  it('两样都吸到：填充给填充、描边给描边；只吸到填充（一根柱子）给不能填充的箭头 → 给描边', () => {
    const r = rect()
    applyColorSample(r, { fill: '#111111', stroke: '#222222' })
    expect(r).toMatchObject({ fill: '#111111', color: '#222222' })
    const a = arrow()
    applyColorSample(a, { fill: '#333333', stroke: null })
    expect(a.color).toBe('#333333')
    const off = rect({ stroke: false })
    applyColorSample(off, { fill: null, stroke: '#444444' })
    expect(off.color).toBe('#444444')
    expect('stroke' in off).toBe(false) // 吸到了描边色：把描边打开
  })

  it('applyEyedropper：给选区一次 commit；没选 / 没吸到不进历史', async () => {
    await seed([rect(), arrow()], ['r', 'a'])
    expect(applyEyedropper({ fill: '#abcdef', stroke: '#fedcba' })).toBe(true)
    expect(byId<ShapeObject>('r')).toMatchObject({ fill: '#abcdef', color: '#fedcba' })
    expect(byId<ArrowObject>('a').color).toBe('#fedcba')
    expect(past()).toBe(1)
    expect(applyEyedropper({ fill: null, stroke: null })).toBe(false)
    useSelectionStore.getState().clear()
    expect(applyEyedropper({ fill: '#000000', stroke: null })).toBe(false)
    expect(past()).toBe(1)
  })
})

describe('导出载荷 / 裁切 / 格式刷认新字段', () => {
  it('缺省不发；改过的发 snake_case', () => {
    const [legacy] = toExportObjects([rect()])
    for (const k of ['miter_limit', 'dash_pattern', 'stroke_align']) expect(k in JSON.parse(JSON.stringify(legacy))).toBe(false)
    const [p] = toExportObjects([rect({ miterLimit: 4, dashPattern: [3, 1], strokeAlign: 'center' })])
    expect(p).toMatchObject({ miter_limit: 4, dash_pattern: [3, 1], stroke_align: 'center' })
    // 只有描边的形状不发对齐；读不成数的虚线不发
    const [line] = toExportObjects([rect({ shape: 'line', strokeAlign: 'outside', dashPattern: [-1] })])
    expect(JSON.parse(JSON.stringify(line))).not.toHaveProperty('stroke_align')
    expect(JSON.parse(JSON.stringify(line))).not.toHaveProperty('dash_pattern')
  })

  it('裁切：外侧对齐的描边越出框一整个线宽', () => {
    const b = visibleContentBounds([rect({ strokeAlign: 'outside' })])!
    expect(b.x).toBeCloseTo(10 - 2 * MM_PER_PT, 9)
    const c = visibleContentBounds([rect({ strokeAlign: 'center' })])!
    expect(c.x).toBeCloseTo(10 - MM_PER_PT, 9)
    const d = visibleContentBounds([rect()])!
    expect(d.x).toBeCloseTo(10, 9)
  })

  it('格式刷：形状 → 形状带上描边的全部外观；形状 → 路径带上斜接上限与自定义虚线，对齐按实际生效的值换（ADR 0185 M7）', async () => {
    const path: PathObject = {
      id: 'p',
      type: 'path',
      x: 0,
      y: 0,
      w: 10,
      h: 10,
      strokePt: 1,
      color: '#000',
      fill: null,
      subpaths: [{ closed: true, nodes: [{ p: [0, 0] }, { p: [1, 0] }, { p: [0, 1] }] }],
    }
    await seed([rect({ miterLimit: 3, dashPattern: [5, 1], strokeAlign: 'outside', lineJoin: 'miter', stroke: false }), rect({ id: 'r2' }), path], ['r'])
    copySelectionStyle()
    useSelectionStore.getState().set(['r2', 'p'])
    pasteSelectionStyle()
    expect(byId<ShapeObject>('r2')).toMatchObject({ miterLimit: 3, dashPattern: [5, 1], strokeAlign: 'outside', lineJoin: 'miter', stroke: false })
    const p = byId<PathObject>('p')
    expect(p).toMatchObject({ miterLimit: 3, dashPattern: [5, 1], lineJoin: 'miter', stroke: false })
    // 形状写的是「外侧」：路径也是外侧（两边缺省不同，按实际生效的值换，不再丢掉）
    expect(p.strokeAlign).toBe('outside')
  })
})
