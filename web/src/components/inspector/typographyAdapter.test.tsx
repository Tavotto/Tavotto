/**
 * 属性能力层的**写入面**（ADR 0032）：事务、mixed、继承、恢复、撤销。
 *
 * 这一组用例的主语是「适配器」，不是某个界面：图内文字与画布文字共用一个
 * 接口，所以「多选之后行为不一样」「工具条和属性页写出不同的东西」这类
 * 缺陷要在这一层就被挡住，而不是每加一个入口再补一遍界面用例。
 */
import { literal } from '@/i18n'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator, finishActiveGesture } from '@/store/gestureCoordinator'
import { emptyProject, type TextObject } from '@/types/document'
import { useCanvasTypography, type TypographyAdapter } from './typographyAdapter'

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const textObj = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: 'H2O',
  sizePt: 9,
  bold: false,
  color: '#000000',
  align: 'left',
  x: 0,
  y: 0,
  w: 20,
  h: 8,
  ...over,
})

const s = () => useDocumentStore.getState()
const texts = () => s().doc.objects.filter((o): o is TextObject => o.type === 'text')
const byId = (id: string) => texts().find((t) => t.id === id)!

let container: HTMLDivElement
let root: Root
let adapter: TypographyAdapter

/** 把适配器捞出来直接驱动：这一层的判据是数据与历史，不是某个控件的 DOM */
function Harness() {
  const objs = useDocumentStore((st) => st.doc.objects).filter(
    (o): o is TextObject => o.type === 'text',
  )
  adapter = useCanvasTypography(objs)
  return null
}

async function mount(objects: TextObject[]) {
  resetGestureCoordinator()
  await s().switchDocument(emptyProject(), 'd_typography')
  s().commit(literal('放入对象'), (d) => {
    for (const o of objects) d.objects.push(o)
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<Harness />))
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  resetGestureCoordinator()
})

describe('读值：四档不压扁', () => {
  it('多选取值不同时报 mixed，绝不拿第一个对象的值冒充全部', async () => {
    await mount([textObj({ id: 't1', sizePt: 9 }), textObj({ id: 't2', sizePt: 12 })])
    expect(adapter.valueOf('sizePt')).toEqual({ kind: 'mixed' })
    // 一致的那条仍然是 uniform——mixed 不是「多选就 mixed」
    expect(adapter.valueOf('color')).toEqual({ kind: 'uniform', value: '#000000' })
  })

  it('谁都没设过字体 = inherit（显示继承来的族，但不算「已修改」）', async () => {
    await mount([textObj({ id: 't1' }), textObj({ id: 't2' })])
    expect(adapter.valueOf('fontFamily')).toEqual({ kind: 'inherit', value: 'serif' })
    expect(adapter.overrideStateOf('fontFamily')).toBe('none')
  })

  it('设过之后是 uniform 且算「已修改」；只有一部分设过时是 some', async () => {
    await mount([
      textObj({ id: 't1', fontFamily: 'monospace' }),
      textObj({ id: 't2', fontFamily: 'monospace' }),
    ])
    expect(adapter.valueOf('fontFamily')).toEqual({ kind: 'uniform', value: 'monospace' })
    expect(adapter.overrideStateOf('fontFamily')).toBe('all')

    act(() => adapter.reset('fontFamily'))
    expect(adapter.overrideStateOf('fontFamily')).toBe('none')
  })

  it('一个设过一个没设过，且生效值不同 → mixed（继承来的那个也要参与比较）', async () => {
    await mount([textObj({ id: 't1', fontFamily: 'sans-serif' }), textObj({ id: 't2' })])
    expect(adapter.valueOf('fontFamily')).toEqual({ kind: 'mixed' })
    expect(adapter.overrideStateOf('fontFamily')).toBe('some')
  })

  it('必填字段没有「继承」这一档，也就没有恢复按钮', async () => {
    await mount([textObj()])
    // `bold: false` 与「没设过加粗」在磁盘上分不开——拿「有没有值」当判据的话
    // 每一条都会永远挂着一颗按了没反应的恢复按钮
    for (const p of ['sizePt', 'weight', 'color', 'halign'] as const) {
      expect(adapter.overrideStateOf(p), p).toBe('none')
    }
  })

  it('mixed 时字段带的是**第一个目标的真实值**，不是一个谁都不是的默认值', async () => {
    await mount([
      textObj({ id: 't1', color: '#ff0000' }),
      textObj({ id: 't2', color: '#0000ff' }),
    ])
    expect(adapter.valueOf('color')).toEqual({ kind: 'mixed' })
    // 控件在 mixed 时拿 `fieldOf(...).value` 当色块的显示值。不带真实值的话
    // 它会退回硬编码黑——而那块颜色谁都不是，用户会以为「它们本来是黑的」
    expect(adapter.fieldOf('color')?.value).toBe('#ff0000')
  })

  it('不支持的属性说得出为什么，而不是安静地消失', async () => {
    await mount([textObj()])
    expect(adapter.fieldOf('valign')).toBeUndefined()
    expect(adapter.unsupportedReason('valign')).toBe('kind_unsupported')
    expect(adapter.valueOf('valign')).toEqual({ kind: 'unsupported', reason: 'kind_unsupported' })
    expect(adapter.unsupportedReason('sizePt')).toBeNull()
  })
})

describe('写入：事务与历史', () => {
  it('多对象一次修改 = 一条历史，撤销一次两个一起回去', async () => {
    await mount([textObj({ id: 't1' }), textObj({ id: 't2' })])
    const before = s().past.length
    act(() => adapter.writeOnce('fontFamily', 'sans-serif'))
    expect(byId('t1').fontFamily).toBe('sans-serif')
    expect(byId('t2').fontFamily).toBe('sans-serif')
    expect(s().past.length).toBe(before + 1)

    act(() => s().undo())
    expect(byId('t1').fontFamily).toBeUndefined()
    expect(byId('t2').fontFamily).toBeUndefined()

    act(() => s().redo())
    expect(byId('t1').fontFamily).toBe('sans-serif')
  })

  it('连续输入合并成一条历史——**不先喊 beginGesture 也要合并**', async () => {
    await mount([textObj({ sizePt: 9 })])
    const before = s().past.length
    // 这条路是「在字号框里打字」：NumberField 只有 onChange，没有
    // onScrubStart。第一版用例自己先调了 beginGesture，于是把「write 会不会
    // 自己开一轮」这件事挡在了判据外面——变异反证里那条改动活了下来。
    act(() => {
      for (const v of [10, 11, 12, 13, 14]) adapter.write('sizePt', v)
    })
    expect(byId('t1').sizePt).toBe(14)
    act(() => adapter.endGesture())
    // 五次输入一条历史；不合并的话撤销要按五次才回到 9pt
    expect(s().past.length).toBe(before + 1)
    act(() => s().undo())
    expect(byId('t1').sizePt).toBe(9)
  })

  it('拖字号（onScrubStart 已经开了一轮）也是一条历史，不会开出两条', async () => {
    await mount([textObj({ sizePt: 9 })])
    const before = s().past.length
    act(() => {
      adapter.beginGesture()
      for (const v of [10, 11, 12]) adapter.write('sizePt', v)
      adapter.endGesture()
    })
    expect(s().past.length).toBe(before + 1)
  })

  it('别处的离散动作会先把这一轮收干净（不会被静默并进上一条历史）', async () => {
    await mount([textObj({ sizePt: 9 })])
    act(() => {
      adapter.beginGesture()
      adapter.write('sizePt', 12)
    })
    // 对齐 / 撤销 / 版本恢复这类动作点下去时会喊这一声
    act(() => finishActiveGesture())
    const after = s().past.length
    act(() => adapter.writeOnce('color', '#ff0000'))
    // 上一轮已经收掉了，这一次是**新的一条**
    expect(s().past.length).toBe(after + 1)
  })

  it('invalid 输入一个字都不写：不进文档、不进历史', async () => {
    await mount([textObj({ sizePt: 9 })])
    const before = s().past.length
    act(() => {
      adapter.writeOnce('sizePt', Number.NaN)
      adapter.writeOnce('sizePt', 9999)
      adapter.writeOnce('color', 'red')
      adapter.writeOnce('fontFamily', 'Times New Roman')
      adapter.writeOnce('weight', 'bolder')
    })
    expect(byId('t1').sizePt).toBe(9)
    expect(byId('t1').color).toBe('#000000')
    expect(byId('t1').fontFamily).toBeUndefined()
    expect(byId('t1').bold).toBe(false)
    expect(s().past.length).toBe(before)
  })

  it('字重 / 字形写的是规范枚举，落盘仍是 TextObject 的 boolean', async () => {
    await mount([textObj()])
    act(() => adapter.writeOnce('weight', 'bold'))
    expect(byId('t1').bold).toBe(true)
    act(() => adapter.writeOnce('style', 'italic'))
    expect(byId('t1').italic).toBe(true)
    act(() => adapter.writeOnce('style', 'normal'))
    // 回到默认删字段，不写一个 `italic: false` 进去
    expect('italic' in byId('t1')).toBe(false)
  })

  it('恢复 = 删字段回到继承，不是写一个等于默认值的显式值', async () => {
    await mount([textObj({ fontFamily: 'monospace' })])
    act(() => adapter.reset('fontFamily'))
    expect('fontFamily' in byId('t1')).toBe(false)
    expect(adapter.valueOf('fontFamily')).toEqual({ kind: 'inherit', value: 'serif' })
  })

  it('property path 与检查报的字段名同源', async () => {
    await mount([textObj()])
    expect(adapter.pathOf('sizePt')).toBe('sizePt')
    expect(adapter.pathOf('fontFamily')).toBe('fontFamily')
    expect(adapter.pathOf('valign')).toBeNull()
  })
})
