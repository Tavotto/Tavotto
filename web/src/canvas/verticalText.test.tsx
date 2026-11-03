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
import { startResizeDrag } from './interactions'
import { dirsFor } from '@/lib/geometry'
import { FRESH_MS } from './measuredWriteback'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'

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

vi.mock('@/lib/mathtext', () => ({
  useMathBox: () => ({ ok: true, width: 2, ascent: 0.8, descent: 0.2, segments: [] }),
  mathPathData: () => 'M0 0',
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

describe('TextView 预览：公式在竖排里整块侧躺、沿列占公式宽度（ADR 0163）', () => {
  const formula = () => container.querySelector<HTMLElement>('[data-math-src]')!
  const sizePx = mmToWorld(10 * (25.4 / 72))

  it('竖排：公式盒 = 列宽 × 公式宽，SVG 顺时针转 90°，上升线在基线右侧', () => {
    act(() => root.render(<TextView obj={textObj({ writingMode: 'vertical', text: '弹性 $x^2$ 段', textFormat: 'latex' })} />))
    const box = formula()
    expect(box.hasAttribute('data-math-vertical')).toBe(true)
    expect(box.style.width).toBe(`${sizePx * 1.25}px`) // 占满一整列
    expect(parseFloat(box.style.height)).toBeCloseTo(2 * sizePx, 3) // 沿列方向 = 公式宽
    const svg = box.querySelector('svg')!
    expect(svg.style.transform).toBe('rotate(90deg)')
    expect(svg.style.transformOrigin).toBe('0 0')
    // 基线 x = 列中线 − 偏移（jsdom 无 canvas：退路 0.35 em）；左上角 = 基线 + 上升
    expect(parseFloat(svg.style.left)).toBeCloseTo((sizePx * 1.25) / 2 - 0.35 * sizePx + 0.8 * sizePx, 3)
  })

  it('横排：仍是零高、基线上的原样（旧文档预览逐字不变）', () => {
    act(() => root.render(<TextView obj={textObj({ text: '弹性 $x^2$ 段', textFormat: 'latex' })} />))
    const box = formula()
    expect(box.hasAttribute('data-math-vertical')).toBe(false)
    expect(box.style.height).toBe('0px')
    expect(box.querySelector('svg')!.style.transform).toBe('')
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


/* ------------------------------------------------------------------ 量完写回的历史规则 */
describe('量完写回：并进触发它的那条历史（measuredWriteback）', () => {
  const COL_MM = 10 * (25.4 / 72) * 1.25
  const stubColumns = (n: () => number) =>
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get: () => mmToWorld(COL_MM * n()),
    })
  afterEach(() => {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth
    vi.restoreAllMocks()
  })

  /** 订阅文档的活 TextView：文档变了它跟着重渲染（真画布里的样子） */
  function Live({ id = 't1' }: { id?: string }) {
    const o = useDocumentStore((st) => st.doc.objects.find((x) => x.id === id)) as TextObject | undefined
    return o ? <TextView obj={o} /> : null
  }
  const geo = (id = 't1') => {
    const o = byId(id)
    return { x: o.x, y: o.y, w: o.w, h: o.h, wm: o.writingMode, sizePt: o.sizePt }
  }
  const commitSwitch = () =>
    s().commit(literal('排向'), (d) => {
      writeCanvasText(d.objects.find((x) => x.id === 't1') as TextObject, 'writingMode', 'vertical')
    })

  it('切竖排 → 量出列宽（x 随右边缘挪）→ 撤销：x / y / w / h 与切换前逐值相等；重做：与撤销前逐值相等', async () => {
    stubColumns(() => 1)
    put(textObj({ x: 20, y: 10, w: 60, h: 8 }))
    await act(async () => root.render(<Live />))
    const before = geo()
    const depth = s().past.length
    await act(async () => commitSwitch())
    const vertical = geo()
    expect(vertical.wm).toBe('vertical')
    expect(vertical.w).toBeCloseTo(COL_MM, 2) // 量出来的一列
    expect(vertical.x).toBeCloseTo(20 + 8 - COL_MM, 2) // 右边缘（20 + 8）不动，x 随之挪
    expect(vertical.h).toBe(60)
    expect(s().past.length).toBe(depth + 1) // 写回并进切换这一条，不另起历史

    act(() => void s().undo())
    expect(geo()).toEqual(before) // 逐值相等（包括 x，旧实现停在 23.59）
    expect('writingMode' in byId()).toBe(false)
    act(() => void s().redo())
    expect(geo()).toEqual(vertical)
  })

  it('竖排里改字号导致列数变化 → 量出的 w 与 x 并进这条历史：撤销 / 重做逐值相等', async () => {
    // 字号 12 时折成 3 列（桩按当前字号给）；10 时 1 列
    stubColumns(() => (byId().sizePt >= 12 ? 3 : 1))
    put(textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 }))
    await act(async () => root.render(<Live />))
    const before = geo()
    const depth = s().past.length
    await act(async () =>
      s().commit(literal('字号'), (d) => {
        ;(d.objects.find((x) => x.id === 't1') as TextObject).sizePt = 12
      }),
    )
    const after = geo()
    expect(after.sizePt).toBe(12)
    expect(after.w).not.toBeCloseTo(before.w, 1)
    expect(s().past.length).toBe(depth + 1)
    act(() => void s().undo())
    expect(geo()).toEqual(before)
    act(() => void s().redo())
    expect(geo()).toEqual(after)
  })

  it('没有触发它的历史（打开项目、字体晚到）：静默写回，不进历史', async () => {
    stubColumns(() => 3)
    put(textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 }))
    // 那条放入的历史是很久以前的了
    const t0 = performance.now()
    vi.spyOn(performance, 'now').mockReturnValue(t0 + FRESH_MS + 1000)
    const last = s().past.at(-1)
    const patches = last?.patches.length
    await act(async () => root.render(<Live />))
    expect(byId().w).toBeCloseTo(COL_MM * 3, 2) // 写回了
    expect(s().past.at(-1)).toBe(last) // 但没动任何历史
    expect(s().past.at(-1)?.patches.length).toBe(patches)
  })

  it('手势事务开着：写回并进这条事务（一次撤销退回按下前）', async () => {
    stubColumns(() => 1)
    put(textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 }))
    await act(async () => root.render(<Live />))
    const before = geo()
    const depth = s().past.length
    stubColumns(() => 2)
    s().beginTxn(literal('拖柄'))
    s().txnUpdate((d) => {
      ;(d.objects.find((x) => x.id === 't1') as TextObject).h = 30
    })
    // 行长变了 → 列数变（桩）→ 重新量（用列宽变化触发 effect 重跑）
    await act(async () => root.render(<TextView obj={{ ...byId(), lineHeight: 1.3 }} />))
    expect(byId().w).not.toBeCloseTo(before.w, 1) // 确实写回了
    s().endTxn()
    expect(s().past.length).toBe(depth + 1)
    act(() => void s().undo())
    expect(geo()).toEqual(before)
  })
})

/* ------------------------------------------------------------------ 竖排的拖柄 */
describe('竖排文字的缩放柄：北 / 南改列长（h），东 / 西不给', () => {
  const down = () => ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
  const fire = (type: 'pointermove' | 'pointerup', mm: [number, number]) =>
    window.dispatchEvent(
      new MouseEvent(type, { clientX: mmToWorld(mm[0]), clientY: mmToWorld(mm[1]), bubbles: true }),
    )
  beforeEach(() => {
    useUiStore.setState({ tool: 'select', snapEnabled: false })
    useSelectionStore.getState().clear()
  })
  afterEach(() => useInteractionStore.getState().end())

  it('柄的方向：横排 w / e；竖排 n / s', () => {
    expect(dirsFor(textObj())).toEqual(['w', 'e'])
    expect(dirsFor(textObj({ writingMode: 'vertical' }))).toEqual(['n', 's'])
  })

  it('竖排拖南柄：h 随指针变、y 不动、x / w 不被碰；一条历史，撤销回原样', () => {
    put(textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 }))
    const before = { ...byId() }
    const depth = s().past.length
    startResizeDrag(down(), 't1', 's')
    fire('pointermove', [5, 12])
    fire('pointermove', [10, 25])
    // 拖动中 TextView 写回的宽度（桩一个）不被拖动覆盖
    s().txnUpdate((d) => {
      const o = d.objects.find((x) => x.id === 't1') as TextObject
      o.x = 22
      o.w = 6
    })
    fire('pointermove', [10, 30])
    fire('pointerup', [10, 30])
    const o = byId()
    expect(o.h).toBeCloseTo(60 + 30, 3)
    expect(o.y).toBe(10)
    expect([o.x, o.w]).toEqual([22, 6]) // 拖动没把量出的 x / w 盖回按下时的值
    expect(s().past.length).toBe(depth + 1)
    act(() => void s().undo())
    expect(byId()).toEqual(before)
  })

  it('竖排拖北柄：y 与 h 一起变（下沿不动）', () => {
    put(textObj({ writingMode: 'vertical', x: 20, y: 10, w: 8, h: 60 }))
    startResizeDrag(down(), 't1', 'n')
    fire('pointermove', [0, -5])
    fire('pointermove', [0, -10])
    fire('pointerup', [0, -10])
    const o = byId()
    expect(o.y).toBeCloseTo(0, 3)
    expect(o.h).toBeCloseTo(70, 3)
    expect(o.y + o.h).toBeCloseTo(70, 3)
    expect([o.x, o.w]).toEqual([20, 8])
  })

  it('横排仍是 x / w 变、y / h 还原（不被竖排改动连累）', () => {
    put(textObj({ x: 20, y: 10, w: 60, h: 8 }))
    startResizeDrag(down(), 't1', 'e')
    fire('pointermove', [5, 9])
    fire('pointermove', [10, 9])
    fire('pointerup', [10, 9])
    const o = byId()
    expect(o.w).toBeCloseTo(70, 3)
    expect([o.x, o.y, o.h]).toEqual([20, 10, 8])
  })
})
