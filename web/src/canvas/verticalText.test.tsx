/**
 * 画布文字「横排 / 竖排」（ADR 0163）：字段、能力层、控件、预览框语义、收框、导出载荷。
 *
 * 磁盘约定：`writingMode?: 'vertical'` 可选，缺席 = 横排，回到横排删字段——旧文档与载荷逐字不变。
 * 框语义：竖排里 `h` 是行长（用户定）、`w` 由列数撑开（画布上 `TextView` 量出来写回，右边缘不动）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { TypographyControls } from '@/components/inspector/controls/TypographyControls'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { TextQuickControls } from '@/canvas/context-bar/textQuick'
import { useCanvasTypography, type TypographyAdapter } from '@/components/inspector/typographyAdapter'
import { toExportObjects } from '@/lib/exportPayload'
import { visualBounds } from '@/lib/geometry'
import {
  coerceTypography,
  commonSupport,
  propertyPathOf,
  propOfPath,
  readCanvasText,
  supportsTypography,
  TYPOGRAPHY_PROPERTY_PATHS,
  WRITING_MODE_VERTICAL,
  writeCanvasText,
} from '@/lib/typography'
import { useDocumentStore } from '@/store/documentStore'
import { resetGestureCoordinator } from '@/store/gestureCoordinator'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { emptyProject, migrateToProject, type TextObject } from '@/types/document'
import { TextView } from './TextView'
import { fitBeforeRotate } from './textFit'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

vi.mock('@/lib/sourceFontCache', () => ({
  sourceFontState: () => 'unknown',
  useSourceFonts: () => 0,
  subscribeSourceFonts: () => () => {},
  previewFamilyOf: () => undefined,
}))

const textObj = (over: Partial<TextObject> = {}): TextObject => ({
  id: 't1',
  type: 'text',
  text: '弹性段 E = 210 GPa',
  sizePt: 10,
  bold: false,
  color: '#111111',
  align: 'left',
  x: 20,
  y: 10,
  w: 60,
  h: 8,
  ...over,
})

const s = () => useDocumentStore.getState()
const byId = (id = 't1') => s().doc.objects.find((o) => o.id === id) as TextObject

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
  localStorage.clear()
  useUiStore.setState({ editingTextId: null })
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
  resetGestureCoordinator()
  await s().switchDocument(emptyProject(), 'd_vertical')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

const put = (...objs: TextObject[]) =>
  s().commit(literal('放入'), (d) => {
    for (const o of objs) d.objects.push(o)
  })

/* ------------------------------------------------------------------ 能力层 */
describe('typography 能力层：writingMode 三张表一起改', () => {
  it('画布文字支持、图内文字不支持；混选两类时交集里没有它', () => {
    expect(supportsTypography('canvasText', 'writingMode')).toBe(true)
    expect(supportsTypography('figureText', 'writingMode')).toBe(false)
    expect(commonSupport(['canvasText']).has('writingMode')).toBe(true)
    expect(commonSupport(['canvasText', 'figureText']).has('writingMode')).toBe(false)
  })

  it('property path 只有一份：画布是 writingMode，图内没有；反查与锚点集合同源', () => {
    expect(propertyPathOf('canvasText', 'writingMode')).toBe('writingMode')
    expect(propertyPathOf('figureText', 'writingMode')).toBeNull()
    expect(propOfPath('canvasText', 'writingMode')).toBe('writingMode')
    expect(TYPOGRAPHY_PROPERTY_PATHS.has('writingMode')).toBe(true)
  })

  it('校验：只认两个取值，别的不进事务', () => {
    expect(coerceTypography('writingMode', 'vertical')).toEqual({ ok: true, value: 'vertical' })
    expect(coerceTypography('writingMode', 'horizontal')).toEqual({ ok: true, value: 'horizontal' })
    expect(coerceTypography('writingMode', 'diagonal')).toEqual({ ok: false, reason: 'not_an_option' })
    expect(coerceTypography('writingMode', 1)).toEqual({ ok: false, reason: 'not_an_option' })
  })
})

describe('字段增删：缺席 = 横排，回到横排删字段', () => {
  it('写 vertical 加字段、写 horizontal 删字段（不留 horizontal / undefined）', () => {
    const o = textObj()
    writeCanvasText(o, 'writingMode', 'vertical')
    expect(o.writingMode).toBe(WRITING_MODE_VERTICAL)
    writeCanvasText(o, 'writingMode', 'horizontal')
    expect('writingMode' in o).toBe(false)
  })

  it('切换时 w / h 对调、左上角不动（行长跟着走）；重复写同一个值不动几何', () => {
    const o = textObj({ x: 20, y: 10, w: 60, h: 8 })
    writeCanvasText(o, 'writingMode', 'vertical')
    expect([o.x, o.y, o.w, o.h]).toEqual([20, 10, 8, 60])
    const snap = { ...o }
    writeCanvasText(o, 'writingMode', 'vertical')
    expect(o).toEqual(snap)
    writeCanvasText(o, 'writingMode', 'horizontal')
    expect([o.x, o.y, o.w, o.h]).toEqual([20, 10, 60, 8])
  })

  it('读：没设过是 undefined（inherit），不是 horizontal', () => {
    expect(readCanvasText(textObj(), 'writingMode')).toBeUndefined()
    expect(readCanvasText(textObj({ writingMode: 'vertical' }), 'writingMode')).toBe('vertical')
  })

  it('序列化往返：存盘形态带字段、读回一致；旧文档（没有字段）读回仍没有', () => {
    put(textObj({ id: 'a', writingMode: 'vertical' }), textObj({ id: 'b' }))
    const raw = JSON.parse(JSON.stringify(s().doc))
    const back = migrateToProject({ ...raw, schema: 2 })!
    const objs = back.canvases[0].objects as TextObject[]
    expect(objs.find((o) => o.id === 'a')!.writingMode).toBe('vertical')
    expect('writingMode' in objs.find((o) => o.id === 'b')!).toBe(false)
  })
})

describe('导出载荷', () => {
  it('竖排发 writing_mode: vertical；横排不发键的值（缺席 = 横排）', () => {
    const [v, h] = toExportObjects([textObj({ id: 'a', writingMode: 'vertical' }), textObj({ id: 'b' })])
    expect((v as { writing_mode?: string }).writing_mode).toBe('vertical')
    expect((h as { writing_mode?: string }).writing_mode).toBeUndefined()
  })
})

/* ------------------------------------------------------------------ 适配器 */
let adapter: TypographyAdapter
function Harness() {
  const objs = useDocumentStore((st) => st.doc.objects).filter((o): o is TextObject => o.type === 'text')
  adapter = useCanvasTypography(objs)
  return null
}

describe('适配器：四档与撤销重做', () => {
  beforeEach(() => act(() => root.render(<Harness />)))

  it('没设过 = inherit(horizontal)；都竖排 = uniform；一横一竖 = mixed', () => {
    act(() => put(textObj({ id: 'a' }), textObj({ id: 'b' })))
    expect(adapter.valueOf('writingMode')).toEqual({ kind: 'inherit', value: 'horizontal' })
    act(() => adapter.writeOnce('writingMode', 'vertical'))
    expect(adapter.valueOf('writingMode')).toEqual({ kind: 'uniform', value: 'vertical' })
    act(() =>
      s().commit(literal('改一个'), (d) => {
        const b = d.objects.find((o) => o.id === 'b') as TextObject
        writeCanvasText(b, 'writingMode', 'horizontal')
      }),
    )
    expect(adapter.valueOf('writingMode')).toEqual({ kind: 'mixed' })
  })

  it('多选一起写；一次一条历史；撤销回横排（字段删掉、框回原样），重做再竖排', () => {
    act(() => put(textObj({ id: 'a' }), textObj({ id: 'b', x: 5, y: 40, w: 30, h: 6 })))
    act(() => adapter.writeOnce('writingMode', 'vertical'))
    expect(byId('a').writingMode).toBe('vertical')
    expect(byId('b').writingMode).toBe('vertical')
    act(() => s().undo())
    for (const id of ['a', 'b']) expect('writingMode' in byId(id)).toBe(false)
    expect([byId('a').w, byId('a').h]).toEqual([60, 8])
    expect([byId('b').x, byId('b').y, byId('b').w, byId('b').h]).toEqual([5, 40, 30, 6])
    act(() => s().redo())
    expect(byId('a').writingMode).toBe('vertical')
    expect([byId('a').w, byId('a').h]).toEqual([8, 60])
  })

  it('非法值：不写、不进历史', () => {
    act(() => put(textObj()))
    const before = s().doc
    act(() => adapter.writeOnce('writingMode', 'diagonal'))
    expect(s().doc).toBe(before)
  })

  it('能力：画布文字有字段，pathOf 与 unsupportedReason 一致', () => {
    act(() => put(textObj()))
    expect(adapter.fieldOf('writingMode')?.options).toEqual(['horizontal', 'vertical'])
    expect(adapter.pathOf('writingMode')).toBe('writingMode')
    expect(adapter.unsupportedReason('writingMode')).toBeNull()
  })
})

/* ------------------------------------------------------------------ 控件 */
describe('控件：属性页的两段与浮动栏的两段', () => {
  const radios = (el: ParentNode) =>
    [...el.querySelectorAll<HTMLElement>('[data-prop="writingMode"] [role="radio"]')]

  function Host({ children }: { children: (a: TypographyAdapter) => React.ReactNode }) {
    const objs = useDocumentStore((st) => st.doc.objects).filter((o): o is TextObject => o.type === 'text')
    return <>{children(useCanvasTypography(objs))}</>
  }

  it('属性页：两段（横排 / 竖排），点竖排写字段；选中态跟值走', () => {
    act(() => put(textObj()))
    act(() => root.render(<TooltipProvider><Host>{(a) => <TypographyControls adapter={a} />}</Host></TooltipProvider>))
    const rs = radios(container)
    expect(rs.map((r) => r.textContent)).toEqual(['横排', '竖排'])
    expect(rs.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false'])
    act(() => rs[1].click())
    expect(byId().writingMode).toBe('vertical')
    expect(radios(container).map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true'])
    act(() => radios(container)[0].click())
    expect('writingMode' in byId()).toBe(false)
  })

  it('多选一横一竖 = mixed：两段都不选中（不拿第一个冒充全部）', () => {
    act(() => put(textObj({ id: 'a' }), textObj({ id: 'b', writingMode: 'vertical' })))
    act(() => root.render(<TooltipProvider><Host>{(a) => <TypographyControls adapter={a} />}</Host></TooltipProvider>))
    expect(radios(container).map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'false'])
  })

  it('图内文字不支持：这一行不画（adapter.fieldOf 回 undefined 就整条不画）', () => {
    const fake = {
      fieldOf: () => undefined,
      valueOf: () => ({ kind: 'unsupported', reason: 'kind_unsupported' }),
      overrideStateOf: () => 'none',
      pathOf: () => null,
      unavailableOptions: () => [],
    } as unknown as TypographyAdapter
    act(() => root.render(<TooltipProvider><TypographyControls adapter={fake} /></TooltipProvider>))
    expect(container.querySelector('[role="radio"]')).toBeNull()
  })

  it('浮动文字工具条：同样两段，点击写字段', () => {
    act(() => put(textObj()))
    act(() =>
      root.render(
        <TooltipProvider>
          <Host>{(a) => <TextQuickControls a={a} compact={false} familyWidth="w-24" />}</Host>
        </TooltipProvider>,
      ),
    )
    const rs = [...container.querySelectorAll<HTMLElement>('[data-inspector-prop="writingMode"] [role="radio"]')]
    expect(rs.map((r) => r.textContent)).toEqual(['横排', '竖排'])
    act(() => rs[1].click())
    expect(byId().writingMode).toBe('vertical')
  })
})

/* ------------------------------------------------------------------ 预览 */
describe('TextView 预览：竖排 = CSS vertical-rl，框高是行长、框宽由列数撑开', () => {
  const body = () => container.querySelector<HTMLElement>('[data-text-body]')!

  it('横排：不带任何竖排样式（旧文档预览逐字不变）', () => {
    act(() => root.render(<TextView obj={textObj()} />))
    expect(body().style.writingMode).toBe('')
    expect(body().style.width).toBe('')
  })

  it('竖排：vertical-rl + mixed；贴框右边、宽度自动、高度撑满', () => {
    act(() => root.render(<TextView obj={textObj({ writingMode: 'vertical' })} />))
    const st = body().style
    expect(st.writingMode).toBe('vertical-rl')
    expect(st.textOrientation).toBe('mixed')
    expect(st.right).toBe('0px')
    expect(st.left).toBe('auto')
    expect(st.width).toBe('auto')
    expect(st.height).toBe('100%')
  })

  it('列数撑出的宽度写回 w，右边缘不动（x 同步挪）；不进撤销', async () => {
    const o = textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 })
    put(o)
    // jsdom 没有排版：offsetWidth 桩成 3 列（行距 1.25 × 10pt ≈ 4.41 mm）
    const colMm = 10 * (25.4 / 72) * 1.25
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get: () => mmToWorld(colMm * 3),
    })
    try {
      const depth = s().canUndo?.() ?? undefined
      await act(async () => root.render(<TextView obj={byId()} />))
      const after = byId()
      expect(after.w).toBeCloseTo(colMm * 3, 2)
      expect(after.x + after.w).toBeCloseTo(28, 2) // 原右边缘 20 + 8
      expect(after.h).toBe(60) // 行长不被碰
      if (depth !== undefined) expect(s().canUndo()).toBe(depth)
    } finally {
      delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth
    }
  })
})

/* ------------------------------------------------------------------ 选框 / 命中 */
describe('命中与选框：x/y/w/h 仍是未旋转框，旋转叠加在它之上', () => {
  it('竖排 + 旋转 90°：visualBounds 是 w/h 对调后的外接盒；0° 就是自己', () => {
    const v = textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 })
    expect(visualBounds(v)).toEqual({ x: 20, y: 10, w: 8, h: 60 })
    const r = visualBounds({ ...v, rotationDeg: 90 })
    expect(r.w).toBeCloseTo(60)
    expect(r.h).toBeCloseTo(8)
    expect(r.x + r.w / 2).toBeCloseTo(24)
    expect(r.y + r.h / 2).toBeCloseTo(40)
  })
})

/* ------------------------------------------------------------------ 收框 */
describe('竖排单行开始旋转时收的是高度（贴字高），同一公式与余量', () => {
  let host: HTMLDivElement
  /** [data-object-id] > [data-text-body]；竖排的「长度」是 height：按 `height: max-content` 回自然长度 */
  function mount(o: TextObject, naturalMm: number) {
    host = document.createElement('div')
    host.setAttribute('data-object-id', o.id)
    const bodyEl = document.createElement('div')
    bodyEl.setAttribute('data-text-body', '')
    bodyEl.getBoundingClientRect = () =>
      ({
        width: o.writingMode ? mmToWorld(8) : bodyEl.style.width === 'max-content' ? mmToWorld(naturalMm) : mmToWorld(o.w),
        height: bodyEl.style.height === 'max-content' ? mmToWorld(naturalMm) : mmToWorld(o.h),
      }) as DOMRect
    host.appendChild(bodyEl)
    document.body.appendChild(host)
  }
  afterEach(() => host?.remove())

  it.each([
    ['left', 10],
    ['center', 10 + (60 - 18) / 2],
    ['right', 10 + 60 - 18],
  ] as const)('align=%s（上 / 中 / 下）：收 y / h，锚点保持', (align, _y) => {
    const o = textObj({ writingMode: 'vertical', align, x: 20, y: 10, w: 8, h: 60, text: '弹性段' })
    mount(o, 18)
    const fit = fitBeforeRotate(o) as { y: number; h: number }
    const margin = 18 * 0.04 + 0.5 * 10 * (25.4 / 72)
    expect('x' in fit).toBe(false)
    expect(fit.h).toBeCloseTo(18 + margin, 3)
    const want = align === 'left' ? 10 : align === 'right' ? 10 + 60 - fit.h : 10 + (60 - fit.h) / 2
    expect(fit.y).toBeCloseTo(want, 3)
  })

  it('正在折行（自然长度超过框高）或含换行：不收', () => {
    const wrapped = textObj({ writingMode: 'vertical', w: 8, h: 60, text: '很长的一句话' })
    mount(wrapped, 90)
    expect(fitBeforeRotate(wrapped)).toBeNull()
    host.remove()
    const multi = textObj({ writingMode: 'vertical', w: 8, h: 60, text: 'a\nb' })
    mount(multi, 18)
    expect(fitBeforeRotate(multi)).toBeNull()
  })

  it('横排仍收 x / w（不被竖排改动连累）', () => {
    const o = textObj({ w: 40, h: 5, text: 'Hello' })
    mount(o, 18)
    const fit = fitBeforeRotate(o) as { x: number; w: number }
    expect('y' in fit).toBe(false)
    expect(fit.w).toBeLessThan(40)
  })
})
