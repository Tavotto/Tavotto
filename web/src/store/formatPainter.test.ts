/**
 * 格式刷补全（ADR 0185 M7）：文字带上字体；路径带上描边对齐；路径文字能刷；描边一族（箭头 / 形状 / 路径）互刷线宽、虚线、线端、
 * 不透明度，箭头 ↔ 路径带端型；形状 ↔ 路径的描边对齐按实际生效的值换（两边缺省不同）；文字 ↔ 路径文字互刷字号、粗斜、颜色、字体。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type ArrowObject, type CanvasObject, type PathObject, type PathTextObject, type ShapeObject, type TextObject } from '@/types/document'
import { copySelectionStyle, pasteSelectionStyle } from './actions'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fp_' + Math.random())
  useDocumentStore.getState().commit(literal('放'), (d) => void d.objects.push(...items))
  useDocumentStore.setState({ past: [], future: [] })
}
const brush = (from: string, to: string[]) => {
  useSelectionStore.getState().set([from])
  copySelectionStyle()
  useSelectionStore.getState().set(to)
  pasteSelectionStyle()
}

const arrow = (over: Partial<ArrowObject> = {}): ArrowObject => ({
  id: 'a', type: 'arrow', x: 0, y: 0, w: 10, h: 10, start: { rx: 0, ry: 0 }, end: { rx: 1, ry: 1 }, strokePt: 1, color: '#000000', head: 'end', ...over,
})
const path = (over: Partial<PathObject> = {}): PathObject => ({
  id: 'p', type: 'path', x: 0, y: 0, w: 10, h: 10, subpaths: [{ closed: false, nodes: [{ p: [0, 0] }, { p: [1, 1] }] }], strokePt: 1, color: '#000000', fill: null, ...over,
})
const shape = (over: Partial<ShapeObject> = {}): ShapeObject => ({
  id: 's', type: 'shape', shape: 'rect', x: 0, y: 0, w: 10, h: 10, strokePt: 1, color: '#000000', fill: null, ...over,
})
const text = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't', type: 'text', x: 0, y: 0, w: 20, h: 5, text: 'x', sizePt: 10, bold: false, color: '#000000', align: 'left', ...over,
})
const ptext = (over: Partial<PathTextObject> = {}): PathTextObject => ({
  id: 'pt', type: 'pathText', x: 0, y: 0, w: 20, h: 5, text: 'x', sizePt: 10, bold: false, color: '#000000',
  subpaths: [{ closed: false, nodes: [{ p: [0, 1] }, { p: [1, 1] }] }], ...over,
})

beforeEach(() => useUiStore.setState({ status: null }))

describe('格式刷补全', () => {
  it('文字 → 文字带上字体与科学文本解释', async () => {
    await seed([text({ fontFamily: 'sans-serif', interpretation: 'scientific', italic: true }), text({ id: 't2' })])
    brush('t', ['t2'])
    expect(byId<TextObject>('t2')).toMatchObject({ fontFamily: 'sans-serif', interpretation: 'scientific', italic: true })
  })

  it('文字 ↔ 路径文字：字号、粗斜、颜色、字体互刷；对齐这类各自独有的不动', async () => {
    await seed([text({ sizePt: 14, bold: true, color: '#aa0000', fontFamily: 'monospace', align: 'center' }), ptext({ textAlign: 'end' })])
    brush('t', ['pt'])
    expect(byId<PathTextObject>('pt')).toMatchObject({ sizePt: 14, bold: true, color: '#aa0000', fontFamily: 'monospace', textAlign: 'end' })
    await seed([ptext({ sizePt: 7, italic: true, flip: true, baselineOffsetPt: 3 }), ptext({ id: 'pt2' }), text()])
    brush('pt', ['pt2', 't'])
    expect(byId<PathTextObject>('pt2')).toMatchObject({ sizePt: 7, italic: true, flip: true, baselineOffsetPt: 3 })
    expect(byId<TextObject>('t')).toMatchObject({ sizePt: 7, italic: true })
    expect('flip' in byId('t')).toBe(false)
  })

  it('箭头 → 路径：线宽、虚线、线端、不透明度、端型（旧 head 字段按实际生效的读）', async () => {
    await seed([arrow({ strokePt: 2, dash: 'dashed', lineCap: 'square', strokeOpacity: 0.5, head: 'both' }), path()])
    brush('a', ['p'])
    expect(byId<PathObject>('p')).toMatchObject({ strokePt: 2, dash: 'dashed', lineCap: 'square', strokeOpacity: 0.5, headStart: 'triangle', headEnd: 'triangle' })
  })

  it('路径 → 箭头：端型写成新字段并维护旧 head；路径没有端型 = 两端都无', async () => {
    await seed([path({ headEnd: 'open', strokePt: 3, color: '#123456' }), arrow({ id: 'a2', head: 'both' }), path({ id: 'p0' }), arrow({ id: 'a3' })])
    brush('p', ['a2'])
    expect(byId<ArrowObject>('a2')).toMatchObject({ headStart: 'none', headEnd: 'open', head: 'end', strokePt: 3, color: '#123456' })
    brush('p0', ['a3'])
    expect(byId<ArrowObject>('a3')).toMatchObject({ headStart: 'none', headEnd: 'none', head: 'none' })
  })

  it('形状 ↔ 路径的描边对齐：按实际生效的值换（形状缺省内侧、路径缺省居中）', async () => {
    await seed([shape(), path({ id: 'p1', strokeAlign: 'outside' }), shape({ id: 's2', strokeAlign: 'outside' })])
    brush('s', ['p1']) // 形状缺省 = 内侧 → 路径写 inside
    expect(byId<PathObject>('p1').strokeAlign).toBe('inside')
    brush('p', ['s2']) // 路径缺省 = 居中 → 形状写 center
    await seed([path(), shape({ id: 's2', strokeAlign: 'outside' })])
    brush('p', ['s2'])
    expect(byId<ShapeObject>('s2').strokeAlign).toBe('center')
  })

  it('填充与渐变、圆角：形状 → 形状照搬；描边一族之间箭头没有填充、不碰', async () => {
    const g = { kind: 'linear' as const, from: [0, 0] as [number, number], to: [1, 0] as [number, number], stops: [{ offset: 0, color: '#ff0000' }, { offset: 1, color: '#0000ff' }] }
    await seed([shape({ fill: '#ffcc00', fillGradient: g, cornerRadius: 2, fillOpacity: 0.4 }), shape({ id: 's2' }), arrow()])
    brush('s', ['s2', 'a'])
    expect(byId<ShapeObject>('s2')).toMatchObject({ fill: '#ffcc00', fillGradient: g, cornerRadius: 2, fillOpacity: 0.4 })
    expect('fill' in byId('a')).toBe(false)
  })

  it('来源还在选区里：跳过它，「已粘贴到 N 个对象」只数真正改了的', async () => {
    await seed([path({ color: '#2f6fe0', strokePt: 1.7 }), arrow({ strokePt: 1, color: '#1b1b18', head: 'end' })])
    useSelectionStore.getState().set(['p', 'a'])
    copySelectionStyle() // 来源 = 最后选中的箭头
    pasteSelectionStyle()
    expect(byId<PathObject>('p')).toMatchObject({ color: '#1b1b18', strokePt: 1, headEnd: 'triangle' })
    expect(useUiStore.getState().status?.values).toMatchObject({ count: 1 })
    // 只选了来源自己：没有可刷的对象
    useSelectionStore.getState().set(['a'])
    pasteSelectionStyle()
    expect(useUiStore.getState().status?.key).toBe('status.stylePasteNoTarget')
  })

  it('跨族不刷：文字样式刷不到形状上', async () => {
    await seed([text(), shape()])
    brush('t', ['s'])
    expect(byId<ShapeObject>('s')).toEqual(shape())
  })
})
