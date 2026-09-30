/**
 * `composeTimelineThumb` 的两遍合成（Codex #679 P1 追加，评审 r4141856781）。
 *
 * 根因：「这条标注要不要画」不能在拍节点那一刻靠「面板此刻手里有没有 SVG 字段」猜——
 * 手里有 SVG 字段，到这里也可能解析 / 解码失败，退到合成时才现取的 render（那张图已经
 * 烙进标注了）。判据必须挂在**这个面板最终实际选中的图源**上，而那要等 `drawObject`
 * 真的画完那个面板才知道。所以 `composeTimelineThumb` 把 `bakedInto` 里的标注对象推迟到
 * 所有面板都画完之后，按各自面板的结局（`'svg'` 才叠、`'live'` / `'none'` 不叠）补画。
 *
 * jsdom 不实现 `HTMLCanvasElement.getContext()` / `toBlob()`，也不会真的解码图片——这里
 * 用一套最小的假 canvas / 假 Image 顶上，只关心**控制流**：`ctx.fillText` 有没有被调用、
 * 调用时的文字是什么，不关心真的画出来的像素长什么样（真实像素只有 e2e 才量得到，见
 * `e2e/layout-timeline.spec.ts`）。「SVG 解析失败」直接用非法 XML 字符串触发
 * `sizedSvgMarkup` 返回 `null`（`timelineThumb.test.ts` 已经在测这个纯函数本身）；
 * 「SVG 解码失败」在真浏览器里是另一条路（`Image.decode()` 拒绝），但落到 `drawObject`
 * 里是同一个分支（`loadFrom('svg', …)` 返回 `null`），控制流上不需要分开模拟。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { composeTimelineThumb, type BakedAnnotations, type ThumbSources } from './timelineThumb'
import { emptyDocument, type CanvasObject, type PanelObject, type TextObject } from '@/types/document'

const VALID_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
/** 标签不闭合：`DOMParser` 解析出 `parsererror`，`sizedSvgMarkup` 返回 `null`（见 timelineThumb.test.ts） */
const INVALID_SVG = '<svg><rect></svg>'

const panel = (id: string, x: number, overrides: Partial<PanelObject> = {}): PanelObject => ({
  id,
  type: 'panel',
  fileId: `${id}.pdf`,
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x,
  y: 0,
  w: 80,
  h: 60,
  ...overrides,
})

const text = (id: string, label: string, x: number): TextObject => ({
  id,
  type: 'text',
  text: label,
  sizePt: 9,
  bold: false,
  color: '#000',
  align: 'left',
  x,
  y: 10,
  w: 20,
  h: 8,
})

/** 记录每次 `fillText` 的第一个参数（文字内容）——够用来判「这条标注画没画」 */
let fillTextCalls: string[] = []
let blobCounter = 0

class FakeImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 100
  naturalHeight = 100
  private _src = ''
  decode(): Promise<void> {
    return Promise.resolve()
  }
  get src(): string {
    return this._src
  }
  set src(v: string) {
    this._src = v
    // 真实浏览器里 onload 也是异步到来的；用微任务模拟，不需要假计时器
    queueMicrotask(() => this.onload?.())
  }
}

function fakeCtx(el: HTMLCanvasElement): unknown {
  return {
    canvas: el,
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    globalAlpha: 1,
    font: '',
    textAlign: 'left',
    textBaseline: 'alphabetic',
    save() {},
    restore() {},
    translate() {},
    rotate() {},
    scale() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    fill() {},
    ellipse() {},
    fillRect() {},
    strokeRect() {},
    drawImage() {},
    fillText(t: string) {
      fillTextCalls.push(t)
    },
    // 全部当「画上了」（非透明）：`layerIsBlank` 不会把任何一路误判成白画
    getImageData(_x: number, _y: number, w: number, h: number) {
      const data = new Uint8ClampedArray(Math.max(1, w) * Math.max(1, h) * 4)
      for (let i = 3; i < data.length; i += 4) data[i] = 255
      return { data }
    },
  }
}

let origGetContext: typeof HTMLCanvasElement.prototype.getContext
let origToBlob: typeof HTMLCanvasElement.prototype.toBlob
let origImage: typeof Image
let origCreateObjectURL: typeof URL.createObjectURL
let origRevokeObjectURL: typeof URL.revokeObjectURL

beforeEach(() => {
  fillTextCalls = []
  blobCounter = 0
  origGetContext = HTMLCanvasElement.prototype.getContext
  origToBlob = HTMLCanvasElement.prototype.toBlob
  origImage = globalThis.Image
  origCreateObjectURL = URL.createObjectURL
  origRevokeObjectURL = URL.revokeObjectURL
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement) {
    return fakeCtx(this)
  } as typeof HTMLCanvasElement.prototype.getContext
  HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
    cb(new Blob(['x'], { type: 'image/webp' }))
  }
  globalThis.Image = FakeImage as unknown as typeof Image
  URL.createObjectURL = vi.fn(() => `blob:fake-${blobCounter++}`)
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = origGetContext
  HTMLCanvasElement.prototype.toBlob = origToBlob
  globalThis.Image = origImage
  URL.createObjectURL = origCreateObjectURL
  URL.revokeObjectURL = origRevokeObjectURL
})

/** 拼一份最小文档 + 跑一次合成；断言只看 `fillTextCalls` */
async function compose(objects: CanvasObject[], sources: ThumbSources, bakedInto?: BakedAnnotations) {
  const doc = { ...emptyDocument(), objects }
  const out = await composeTimelineThumb(doc, sources, bakedInto)
  expect(out, '合成失败（fakeCtx / FakeImage 没顶上，看 getContext / toBlob 是否被跳过）').not.toBeNull()
  return fillTextCalls
}

describe('composeTimelineThumb：写回烙进面板的标注按面板实际选中的图源判定（Codex #679 P1）', () => {
  it('a) 面板 SVG 成功：标注画一次（叠在 SVG 上）；普通标注不受影响', async () => {
    const objects = [panel('pA', 0), text('tA', 'A-note', 0), text('tPlain', 'plain', 40)]
    const sources: ThumbSources = new Map([['pA', { svg: VALID_SVG }]])
    const bakedInto: BakedAnnotations = new Map([['tA', 'pA']])
    const drawn = await compose(objects, sources, bakedInto)
    expect(drawn.filter((t) => t === 'A-note')).toHaveLength(1)
    expect(drawn).toContain('plain')
  })

  it('b) 面板有 SVG 但解析失败、退回 render：带 bakedInto 的标注不画；普通标注仍照画', async () => {
    const objects = [panel('pB', 0), text('tB', 'B-note', 0), text('tPlain', 'plain', 40)]
    const sources: ThumbSources = new Map([['pB', { svg: INVALID_SVG, url: 'test://render/pB' }]])
    const bakedInto: BakedAnnotations = new Map([['tB', 'pB']])
    const drawn = await compose(objects, sources, bakedInto)
    expect(drawn).not.toContain('B-note')
    expect(drawn).toContain('plain')
  })

  it('c) 面板只有 render（没有 SVG 字段）：带 bakedInto 的标注不画；普通标注仍照画', async () => {
    const objects = [panel('pC', 0), text('tC', 'C-note', 0), text('tPlain', 'plain', 40)]
    const sources: ThumbSources = new Map([['pC', { url: 'test://render/pC' }]])
    const bakedInto: BakedAnnotations = new Map([['tC', 'pC']])
    const drawn = await compose(objects, sources, bakedInto)
    expect(drawn).not.toContain('C-note')
    expect(drawn).toContain('plain')
  })

  it('d) 同一节点里混合 a 和 b：各面板的标注各自按各自的结局判定，不串', async () => {
    const objects = [
      panel('pA', 0),
      text('tA', 'A-note', 0),
      panel('pB', 100),
      text('tB', 'B-note', 100),
    ]
    const sources: ThumbSources = new Map([
      ['pA', { svg: VALID_SVG }],
      ['pB', { svg: INVALID_SVG, url: 'test://render/pB' }],
    ])
    const bakedInto: BakedAnnotations = new Map([
      ['tA', 'pA'],
      ['tB', 'pB'],
    ])
    const drawn = await compose(objects, sources, bakedInto)
    expect(drawn).toContain('A-note')
    expect(drawn).not.toContain('B-note')
  })

  it('对照：没有 bakedInto（普通节点）——所有标注一律照画，行为不变', async () => {
    const objects = [panel('pA', 0), text('tA', 'A-note', 0)]
    const sources: ThumbSources = new Map([['pA', { svg: VALID_SVG }]])
    const drawn = await compose(objects, sources)
    expect(drawn).toContain('A-note')
  })
})
