/**
 * 版本列表的缩略图与画布列表的缩略图是**同一份绘制组件**（fu-thumbs）。
 *
 * 「复用」这件事光看 import 判不出来：谁都可以在版本抽屉里另画一份长得差不多
 * 的 SVG，代码评审也未必看得出来，而两份实现会各自演进到用户能看出差别为止。
 * 所以判据落在**产物**上——同样的内容，两处画出来的 DOM 逐字相同。
 *
 * **两侧必须是两条真实路径**，不能都直接 render 那个组件：那样的话对比的是
 * 同一份实现的两次调用，任何改动都同时改两边，diff 恒等为空，而空看起来就像
 * 「没有差别」。所以左边跑 `CanvasList`（喂内存里的 `CanvasObject`），右边跑
 * `VersionDrawer`（喂列表端点发来的草图），中间隔着组件挑选、页面尺寸传递、
 * 草图字段三件真会写错的事。
 *
 * 这条判据顺带把「草图带够了没有」钉住了：右边只有 type/x/y/w/h/fileId/
 * fileKind/text，左边是完整的文档对象——产物相同才说明草图没漏东西。
 *
 * 它**不覆盖**组件自己画得对不对（改了组件两边一起变）。那一档在
 * `left/canvasList.test.tsx` 与 `VersionDialog.test.tsx` 的内容判据里。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchTimeline: vi.fn(),
  fetchVersionDoc: vi.fn(),
}))

import { fetchVersionDoc, fetchTimeline, type LayoutVersionMeta } from '@/lib/api'
import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { CanvasThumb } from '@/components/CanvasThumb'
import { CanvasList } from '@/components/left/CanvasList'
import { TimelinePanel } from '@/components/VersionDialog'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { isTimelineOpen, useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** 右栏只在「时间线」页签亮着时才挂时间线面板（同 `Inspector`） */
function TimelineHost() {
  const open = useUiStore(isTimelineOpen)
  return open ? <TimelinePanel /> : null
}
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as typeof fetch

const PAGE = { w: 150, h: 100 }

/** 内存里的文档对象（画布列表那一侧喂的东西） */
const docObjects = [
  {
    id: 'p1',
    type: 'panel',
    fileId: 'a.pdf',
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    overrides: [{ gid: 'g1', prop: 'pos_frac', value: [0.1, 0.2] }],
    x: 4,
    y: 6,
    w: 40,
    h: 30,
  },
  {
    id: 't1',
    type: 'text',
    text: '第一张版上的说明',
    x: 5,
    y: 50,
    w: 30,
    h: 6,
    sizePt: 8,
    bold: false,
    color: '#000000',
    align: 'left',
  },
] as CanvasObject[]

/**
 * 同样这两个对象在**后端草图**里的样子（`app.py::_version_sketch`）：
 * 没有 overrides、没有 nativeW/H、没有字号颜色，只有画得出来所需的那几个字段。
 */
const version: LayoutVersionMeta = {
  id: 'v1',
  name: '09-06 21:30',
  ts: 1_757_000_000_000,
  auto: true,
  description: '',
  objects: 2,
  page: PAGE,
  canvasId: 'c_t',
  canvasName: 'Figure 1',
  sketch: {
    page: PAGE,
    objects: [
      { type: 'panel', x: 4, y: 6, w: 40, h: 30, fileId: 'a.pdf', fileKind: 'pdf' },
      { type: 'text', x: 5, y: 50, w: 30, h: 6, text: '第一张版上的说明' },
    ],
  },
}

let container: HTMLDivElement
let root: Root

async function render(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  await act(async () => {
    await Promise.resolve()
  })
  const svg = container.querySelector('[data-canvas-thumb]')
  expect(svg, '这一侧根本没画缩略图').not.toBeNull()
  return svg as SVGElement
}

async function teardown() {
  if (root) await act(async () => root.unmount())
  container?.remove()
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.clearAllMocks()
  vi.mocked(fetchTimeline).mockResolvedValue({ versions: [version] })
  vi.mocked(fetchVersionDoc).mockResolvedValue({ ...version, doc: emptyProject().canvases[0] as never })
  useAssetStore.setState({ byId: { 'a.pdf': { id: 'a.pdf', mtime: 7 } } } as never)
  useUiStore.setState({ rightOpen: true, rightTab: 'timeline' })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_thumbpair')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { ...d.page, ...PAGE }
    d.objects = docObjects
  })
})

afterEach(async () => {
  await teardown()
  useUiStore.setState({ rightTab: 'properties' })
})

describe('两处缩略图是同一份组件', () => {
  it('同样的内容，画布列表与版本列表画出来的 DOM 逐字相同', async () => {
    const fromCanvas = (await render(<CanvasList />)).outerHTML
    await teardown()
    const fromVersion = (await render(<TimelineHost />)).outerHTML
    expect(fromVersion).toBe(fromCanvas)
  })

  it('面板挂的是素材库同一条预览链路（草图只带素材身份，URL 由组件拼）', async () => {
    const svg = await render(<TimelineHost />)
    const href = svg.querySelector('[data-thumb-panel]')?.getAttribute('href') ?? ''
    expect(href).toContain('a.pdf')
    // mtime 来自素材库、不在草图里 —— 草图带上它才是把 /api/panels 的职责抄一份
    expect(href).toContain('m=7')
  })
})

describe('透明背景（导出对话框用）：默认行为一字不变', () => {
  const objs = [{ id: 't', type: 'text', x: 2, y: 2, w: 20, h: 6, text: '标题' }] as never
  const draw = async (el: React.ReactElement) => {
    await teardown()
    return render(el)
  }

  it('不传 transparent、传 false：DOM 逐字相同；没有棋盘格图案，页面仍是纸白', async () => {
    const plain = (await draw(<CanvasThumb page={PAGE} objects={objs} />)).outerHTML
    const off = (await draw(<CanvasThumb page={PAGE} objects={objs} transparent={false} />)).outerHTML
    expect(off).toBe(plain)
    expect(plain).not.toContain('pattern')
    expect(plain).not.toContain('data-thumb-transparent')
    expect(plain).toContain('fill="var(--color-paper)"')
  })

  it('transparent：页面填棋盘格图案，图案只用纸白与纸墨（两套主题同值）；对象照画', async () => {
    const svg = await draw(<CanvasThumb page={PAGE} objects={objs} transparent />)
    const page = svg.querySelector('[data-thumb-page]')!
    const id = svg.querySelector('pattern')!.id
    expect(page.getAttribute('fill')).toBe(`url(#${id})`)
    expect(svg.querySelector('pattern')!.innerHTML).toContain('var(--color-paper-ink)')
    expect(svg.querySelector('pattern')!.innerHTML).not.toMatch(/#[0-9a-f]{3,6}/i)
    expect(svg.textContent).toContain('标题')
  })
})

describe('flush / pxWidth（导出预览的缩放舞台用）：默认行为一字不变', () => {
  const panelObjs = [{ id: 'p', type: 'panel', fileId: 'Fig1.pdf', x: 0, y: 0, w: PAGE.w / 2, h: PAGE.h }] as never
  const draw = async (el: React.ReactElement) => {
    await teardown()
    return render(el)
  }

  it('不传、传 false / undefined：DOM 逐字相同；面板取缩略图档 200', async () => {
    const plain = (await draw(<CanvasThumb page={PAGE} objects={panelObjs} />)).outerHTML
    const off = (await draw(<CanvasThumb page={PAGE} objects={panelObjs} flush={false} pxWidth={undefined} />)).outerHTML
    expect(off).toBe(plain)
    expect(plain).toContain('w=200')
  })

  it('flush：页面顶满盒子（viewBox 就是页面本身），不画外框与圆角', async () => {
    const svg = await draw(<CanvasThumb page={PAGE} objects={[]} flush />)
    expect(svg.getAttribute('viewBox')).toBe(`0 0 ${PAGE.w} ${PAGE.h}`)
    const page = svg.querySelector('[data-thumb-page]')!
    expect(page.getAttribute('stroke')).toBe('none')
    expect(page.getAttribute('rx')).toBe('0')
  })

  it('flush 与带圆角的缩略图同在一页（导出对话框：页签 + 大预览）：大预览的裁切解析到无圆角的那一份', async () => {
    await teardown()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => {
      root.render(
        <>
          <CanvasThumb page={PAGE} objects={panelObjs} />
          <CanvasThumb page={PAGE} objects={panelObjs} flush />
        </>,
      )
    })
    const [tab, big] = [...container.querySelectorAll('[data-canvas-thumb]')]
    const clipOf = (svg: Element) => {
      const ref = svg.querySelector('g[clip-path]')!.getAttribute('clip-path')!
      // 浏览器按 id 解析 url(#…)：同一个 id 有两份时取文档里的第一份
      return document.getElementById(/url\(#(.+)\)/.exec(ref)![1])!.querySelector('rect')!
    }
    expect(clipOf(big).getAttribute('rx')).toBe('0')
    expect(Number(clipOf(tab).getAttribute('rx'))).toBeGreaterThan(0)
  })

  it('pxWidth：按每张面板自己的显示宽度挑档（放大后更清晰）', async () => {
    // 面板占页宽一半；页面显示 3000 设备像素 → 面板 1500 → 1600 档
    const svg = await draw(<CanvasThumb page={PAGE} objects={panelObjs} flush pxWidth={3000} />)
    expect(svg.querySelector('[data-thumb-panel]')!.getAttribute('href')).toContain('w=1600')
  })
})

describe('exportedText（导出预览用）：文字按导出出来的样子画；不给时一字不变', () => {
  const label = {
    id: 'l',
    type: 'text',
    text: '(a)',
    x: 2,
    y: 3,
    w: 8,
    h: 5,
    sizePt: 9,
    bold: true,
    color: '#112233',
    align: 'left',
  } as CanvasObject
  const asText = (o: unknown) => ((o as CanvasObject).type === 'text' ? (o as never) : null)
  const draw = async (el: React.ReactElement) => {
    await teardown()
    return render(el)
  }
  const textOf = (svg: SVGElement) => svg.querySelector('[data-thumb-text] text') as SVGTextElement

  it('不传、或对这个对象返回 null：DOM 逐字相同（画布列表 / 版本列表的示意画法）', async () => {
    const plain = (await draw(<CanvasThumb page={PAGE} objects={[label]} />)).outerHTML
    const off = (await draw(<CanvasThumb page={PAGE} objects={[label]} exportedText={() => null} />)).outerHTML
    expect(off).toBe(plain)
    expect(plain).not.toContain('data-thumb-text')
  })

  it('给了：字号按 pt 折成页面 mm，粗细、颜色、字体族都取对象自己的（没设过字体族 = 文档衬线）', async () => {
    const svg = await draw(<CanvasThumb page={PAGE} objects={[label]} exportedText={asText} />)
    const t = textOf(svg)
    expect(Number(t.getAttribute('font-size'))).toBeCloseTo((9 * 25.4) / 72, 9)
    expect(t.getAttribute('font-weight')).toBe('700')
    expect(t.getAttribute('font-style')).toBe('normal')
    expect(t.getAttribute('fill')).toBe('#112233')
    expect(t.getAttribute('style')).toContain('var(--font-doc)')
    expect(t.textContent).toBe('(a)')
    const sans = await draw(
      <CanvasThumb page={PAGE} objects={[{ ...label, fontFamily: 'sans-serif', italic: true } as CanvasObject]} exportedText={asText} />,
    )
    expect(textOf(sans).getAttribute('style')).toContain('Helvetica')
    expect(textOf(sans).getAttribute('font-style')).toBe('italic')
  })

  it('对齐与多行：左对齐锚在左边 + 内边距，居中锚在框中线，右对齐锚在右边 − 内边距；每行一个 tspan，行盒按 lineHeight 排', async () => {
    const lines = { ...label, text: '第一行\n第二行', padding: 1, lineHeight: 1.5 } as CanvasObject
    const size = (9 * 25.4) / 72
    const at = async (align: string) => {
      const svg = await draw(<CanvasThumb page={PAGE} objects={[{ ...lines, align } as CanvasObject]} exportedText={asText} />)
      const t = textOf(svg)
      return { anchor: t.getAttribute('text-anchor'), spans: [...t.children].map((s) => [Number(s.getAttribute('x')), Number(s.getAttribute('y')), s.textContent]) }
    }
    const left = await at('left')
    expect(left.anchor).toBe('start')
    expect(left.spans).toEqual([
      [3, expect.closeTo(3 + 1 + 0.5 * 1.5 * size, 9), '第一行'],
      [3, expect.closeTo(3 + 1 + 1.5 * 1.5 * size, 9), '第二行'],
    ])
    expect((await at('center')).anchor).toBe('middle')
    expect((await at('center')).spans[0][0]).toBe(6)
    expect((await at('right')).anchor).toBe('end')
    expect((await at('right')).spans[0][0]).toBe(9)
  })

  it('上下标与画布同一份 richText 解析：^{…} 画成小号抬高的一段，记号本身不画出来', async () => {
    const svg = await draw(
      <CanvasThumb page={PAGE} objects={[{ ...label, text: 'h^{-1}' } as CanvasObject]} exportedText={asText} />,
    )
    const t = textOf(svg)
    expect(t.textContent).toBe('h-1')
    const sup = t.querySelector('tspan tspan')!
    expect(sup.textContent).toBe('-1')
    expect(Number(sup.getAttribute('font-size'))).toBeCloseTo(((9 * 25.4) / 72) * 0.62, 9)
    expect(Number(sup.getAttribute('baseline-shift'))).toBeGreaterThan(0)
  })
})

describe('标注里的公式（ADR 0152 §七）：缩略图 / 导出预览不再画 `$…$` 源码', () => {
  const formula = {
    id: 'm',
    type: 'text',
    text: 'k = $x^2$ ok',
    textFormat: 'latex',
    x: 2,
    y: 3,
    w: 60,
    h: 5,
    sizePt: 9,
    color: '#112233',
    align: 'left',
  } as CanvasObject
  const asText = (o: unknown) => ((o as CanvasObject).type === 'text' ? (o as never) : null)
  const okBox = { ok: true, width: 2, ascent: 0.8, descent: 0.2, segments: [['M', 0, 0], ['L', 2, 0], ['Z']] }
  const stub = (result: unknown) =>
    vi.stubGlobal('fetch', async () =>
      new Response(JSON.stringify({ ok: true, results: [result] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )

  beforeEach(async () => {
    const { __resetMathForTests } = await import('@/lib/mathtext')
    __resetMathForTests()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('排好后：公式是 <path>（轮廓与画布同一份），两侧文字各一段，源码不再出现', async () => {
    stub(okBox)
    await teardown()
    const svg = await render(<CanvasThumb page={PAGE} objects={[formula]} exportedText={asText} />)
    await act(async () => {
      await vi.waitFor(() => expect(svg.querySelector('path[data-thumb-math]')).not.toBeNull())
    })
    const path = svg.querySelector('path[data-thumb-math]')!
    expect(path.getAttribute('d')).toBe('M 0 0 L 2 0 Z')
    expect(path.getAttribute('fill')).toBe('#112233')
    const size = (9 * 25.4) / 72
    expect(path.getAttribute('transform')).toMatch(/scale\(/)
    expect(Number(/scale\(([\d.]+)\)/.exec(path.getAttribute('transform')!)![1])).toBeCloseTo(size, 6)
    const texts = [...svg.querySelectorAll('[data-thumb-math-line] text')].map((t) => t.textContent)
    expect(texts).toEqual(['k = ', ' ok'])
    expect(svg.textContent).not.toContain('$')
  })

  it('公式落在前一段文字之后，后一段文字紧接公式之后（宽度累计）', async () => {
    stub(okBox)
    await teardown()
    const svg = await render(<CanvasThumb page={PAGE} objects={[formula]} exportedText={asText} />)
    await act(async () => {
      await vi.waitFor(() => expect(svg.querySelector('path[data-thumb-math]')).not.toBeNull())
    })
    const size = (9 * 25.4) / 72
    const [a, b] = [...svg.querySelectorAll<SVGTextElement>('[data-thumb-math-line] text')]
    const mx = Number(/translate\(([\d.]+) /.exec(svg.querySelector('path[data-thumb-math]')!.getAttribute('transform')!)![1])
    expect(Number(a.getAttribute('x'))).toBeCloseTo(2, 6)
    expect(mx).toBeGreaterThan(Number(a.getAttribute('x')))
    expect(Number(b.getAttribute('x'))).toBeCloseTo(mx + 2 * size, 6)
  })

  it('还没排好：照源码画（$…$）', async () => {
    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}))
    await teardown()
    const svg = await render(<CanvasThumb page={PAGE} objects={[formula]} exportedText={asText} />)
    expect(svg.querySelector('[data-thumb-math-line]')!.textContent).toBe('k = $x^2$ ok')
    expect(svg.querySelector('path[data-thumb-math]')).toBeNull()
  })

  it('排不出来：照源码画（$…$），与画布同一个退路', async () => {
    stub({ ok: false, error: 'bad' })
    await teardown()
    const svg = await render(<CanvasThumb page={PAGE} objects={[formula]} exportedText={asText} />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30))
    })
    expect(svg.querySelector('path[data-thumb-math]')).toBeNull()
    expect(svg.querySelector('[data-thumb-math-line]')!.textContent).toBe('k = $x^2$ ok')
  })

  it('小缩略图：画读出来的字，不是存着的串（`\\textit{}` 命令去掉，公式不带 $）；旧写法原样', async () => {
    await teardown()
    const small = { ...formula, text: 'a \\textit{b} $x^2$' } as CanvasObject
    expect((await render(<CanvasThumb page={PAGE} objects={[small]} />)).querySelector('text')!.textContent).toBe('a b x^2')
    // 草图里没有 textFormat 的旧标注：原样
    await teardown()
    const legacy = { ...formula, textFormat: undefined, text: '\\textit{b}' } as CanvasObject
    expect((await render(<CanvasThumb page={PAGE} objects={[legacy]} />)).querySelector('text')!.textContent).toBe('\\textit{b}')
  })
})
