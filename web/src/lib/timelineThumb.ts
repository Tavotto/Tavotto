import { panelSrc } from '@/lib/api'
import { applyPanelTransform, panelContentTransform } from '@/lib/panelTransform'
import { useAssetStore } from '@/store/assetStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'

/**
 * 时间线节点的缩略图（ADR 0101）：拍节点**那一刻**画布的样子，合成成一张小位图。
 *
 * 为什么不是列表草图（`CanvasThumb`）：草图画的是**当前磁盘上的素材**，两个图内
 * 布局完全不同的节点长得一模一样；而这里取的是 renderStore 里当时各面板**带着
 * overrides** 的那份 SVG——用户在编辑器里看到的就是它。为什么不是后端出图：那是
 * 每个节点一轮 matplotlib，2 分钟一次的自动节点付不起。
 *
 * 这是**近似**，不是导出预览：文字按页面字号画在同一个位置、形状画轮廓，面板的
 * 裁剪 / 旋转 / 透明度照做，别的细节（箭头头部、虚线）不画。一个面板既没有 SVG
 * 也拿不到素材图时留白，不报错。
 *
 * 任何一步失败（解码不了、canvas 被 taint、编码不出）都返回 `null`——没有缩略图
 * 的节点在列表里退回草图，拍节点这件事本身绝不因为缩略图失败。
 */

/** 缩略图的长边（px）。列表行画 56×40，预览不用它；2× 屏上够清楚、体积几 KB。 */
export const TIMELINE_THUMB_PX = 240
/** 一张图最多等多久（素材图要走网络）；超时就用已经画上去的部分。 */
const IMAGE_TIMEOUT_MS = 2500
const MM_PER_PT = 25.4 / 72

export interface TimelineThumb {
  blob: Blob
  type: 'image/webp' | 'image/png'
}

/**
 * 把一份 SVG 串变成能当图片画的那份：根元素的 width / height 给成像素（去掉宽高的那份没有
 * 固有尺寸）、`preserveAspectRatio="none"`，拿掉按容器铺满的 style。
 *
 * **解析后在根元素上 setAttribute，不拼字符串**（Codex #679）：renderStore 里的 SVG 经
 * `prepareSvg` 已经带着 `preserveAspectRatio`，再往 `<svg` 后面拼一份，根元素上就有两个同名
 * 属性——XML 里这是错误，浏览器整份拒收，面板于是一律退回素材图，带 overrides 的样子全丢了。
 * 解析不了（不是合法 XML）就返回 `null`，由调用方换素材图那一路。
 */
export function sizedSvgMarkup(svg: string, w: number, h: number): string | null {
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const root = doc.documentElement
  if (!root || root.nodeName !== 'svg' || doc.getElementsByTagName('parsererror').length) return null
  root.setAttribute('width', String(Math.max(1, Math.round(w))))
  root.setAttribute('height', String(Math.max(1, Math.round(h))))
  root.setAttribute('preserveAspectRatio', 'none')
  root.removeAttribute('style')
  return new XMLSerializer().serializeToString(doc)
}

function svgImageSource(svg: string, w: number, h: number): string | null {
  const sized = sizedSvgMarkup(svg, w, h)
  return sized == null ? null : URL.createObjectURL(new Blob([sized], { type: 'image/svg+xml' }))
}

/**
 * 载入并**解码完**再交出去：`onload` 只说字节到了，不保证解码好了——内存缓存里的图
 * `onload` 立刻就来，WebKit 上紧接着 `drawImage` 可能什么都画不上（Windows 的 WebKit
 * 在 CI 上量到过：同一个素材图，前一个节点画上了、下一个节点只剩文字）。`decode()` 等到
 * 真的能画；不支持或解码失败就照旧交出去，由画完之后的「白画了」判据兜底。
 */
function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image()
    const timer = window.setTimeout(() => resolve(null), IMAGE_TIMEOUT_MS)
    img.onload = () => {
      window.clearTimeout(timer)
      void (img.decode ? img.decode() : Promise.resolve()).catch(() => undefined).then(() => resolve(img))
    }
    img.onerror = () => {
      window.clearTimeout(timer)
      resolve(null)
    }
    img.src = src
  })
}

/**
 * 一个面板**此刻**从哪儿取图：renderStore 里带 overrides 的 SVG，没有就退回素材图。
 *
 * **同步取、一次取完**（在任何 await 之前）：关项目 / 切项目那一刻打的节点，
 * 合成还没画完 renderStore 就被清空、`apiUrl` 就换成了下一个项目——晚一步取，
 * 画进去的是另一个项目的同名素材，或者什么都没有。
 */
/**
 * 两路图源**都取**：SVG 在前（带 overrides，用户看到的就是它），素材图兜底。只取一路的话，
 * SVG 解码失败或画成空白（Windows 的 WebKit 上量到过：同一份排版前一个节点有面板、下一个
 * 节点只剩文字，CI run 36510136403）时这一格就空着——而素材图明明就在。
 */
type PanelSource = { svg?: string; url?: string } | null

function panelSource(o: PanelObject): PanelSource {
  const render = panelRender(useRenderStore.getState(), o)
  const mtime = useAssetStore.getState().byId[o.fileId]?.mtime
  const url = panelSrc(o.fileId, o.fileKind, 400, mtime) || undefined
  if (!render?.svg && !url) return null
  return { svg: render?.svg ?? undefined, url }
}

/**
 * e2e 的诊断（只在 e2e 注入了 `__TAVOTTO_THUMB_TRACE__` 数组时记）：每个面板走了哪条路、
 * 结果如何。缩略图画不出面板时，用例把它带进失败信息——量不到的维度先让它说出口。
 */
function trace(entry: Record<string, unknown>): void {
  const sink = (window as unknown as { __TAVOTTO_THUMB_TRACE__?: unknown[] }).__TAVOTTO_THUMB_TRACE__
  if (Array.isArray(sink)) sink.push(entry)
}

async function loadFrom(
  kind: 'svg' | 'url',
  source: NonNullable<PanelSource>,
  wPx: number,
  hPx: number,
  retry = false,
): Promise<{ img: HTMLImageElement; revoke?: string } | null> {
  if (kind === 'svg') {
    if (!source.svg) return null
    const url = svgImageSource(source.svg, wPx, hPx)
    if (!url) return null
    const img = await loadImage(url)
    if (img) return { img, revoke: url }
    URL.revokeObjectURL(url)
    return null
  }
  if (!source.url) return null
  const img = await loadImage(retry ? `${source.url}${source.url.includes('?') ? '&' : '?'}thumbRetry=1` : source.url)
  return img ? { img } : null
}

/**
 * 这一路是不是**白画了**：在一张单独的透明图层上画，量这一层有没有任何一个像素不透明。
 * 不能拿「画完之后主画布变没变」来判：两张不透明、内容相同的面板完全重叠时，上层画上去
 * 主画布一个像素都不变，会被误判成白画、退回素材图（Codex #679）。量不了（图层被 taint）
 * 就当画上了。
 */
function layerIsBlank(layer: HTMLCanvasElement): boolean {
  try {
    const px = layer.getContext('2d')!.getImageData(0, 0, layer.width, layer.height).data
    for (let i = 3; i < px.length; i += 4) if (px[i] !== 0) return false
    return true
  } catch {
    return false
  }
}

/**
 * 一个面板缩略图最终**实际画出来**用的是哪一路图源：
 * - `'svg'`——写回前就已经冻结在手里的那份（renderStore 里的，不含标注写回烙进去的内容）；
 * - `'live'`——合成这一刻才现取的兜底（素材图 / render URL），`'svg'` 没能用上时才会退到这里；
 * - `'none'`——两路都没画上（无来源、载入失败、白画）。
 *
 * 「有没有 SVG 字段」判断不出这个结局——手里有 SVG 字段但解析 / 解码失败，一样会退到
 * `'live'`（Codex #679 P1 追加，见 `composeTimelineThumb` 的 `bakedInto`）。
 */
export type PanelDrawOutcome = 'svg' | 'live' | 'none'

/**
 * 把一个面板预渲染到一张与缩略图同尺寸的透明图层上（不透明度留到合成时再乘），并报告
 * 实际用了哪一路图源。这一步**不碰主画布**：合成器先把全部面板预渲染完（结局全齐），
 * 再按 `doc.objects` 的原顺序把图层 / 标注 / 形状叠上去（Codex #679 P1 第三轮）。
 */
async function renderPanelLayer(
  o: PanelObject,
  canvas: HTMLCanvasElement,
  scale: number,
  source: PanelSource,
): Promise<{ layer: HTMLCanvasElement | null; outcome: PanelDrawOutcome }> {
  const x = o.x * scale
  const y = o.y * scale
  const w = o.w * scale
  const h = o.h * scale
  // 旋转 / 翻转与画布同一份（`lib/panelTransform`）：先在内容空间翻转，再旋转落位
  const transform = panelContentTransform(o)
  const rot = transform.rotate
  // 旋转 90/270 时内容的显示尺寸与落位包围盒互换
  const [cw, ch] = rot === 90 || rot === 270 ? [h, w] : [w, h]
  const crop = o.crop
  const fullW = cw / (crop?.w ?? 1)
  const fullH = ch / (crop?.h ?? 1)
  if (!source) {
    trace({ panel: o.id, result: 'no-source' })
    return { layer: null, outcome: 'none' }
  }
  const steps: string[] = []
  let outcome: PanelDrawOutcome = 'none'
  let result: HTMLCanvasElement | null = null
  // SVG → 素材图 → 素材图再取一次（绕开内存缓存里那张画不上的，新发一个请求）
  const attempts = [['svg', false], ['url', false], ['url', true]] as const
  for (const [kind, retry] of attempts) {
    // 再取一次只在素材图那一路没画上（白画了 / 载入失败）时
    if (retry && !steps.some((st) => st === 'url:blank' || st === 'url:load-failed')) break
    const got = await loadFrom(kind, source, fullW * 2, fullH * 2, retry)
    if (!got) {
      if (source[kind]) steps.push(`${kind}${retry ? '-retry' : ''}:load-failed`)
      continue
    }
    const { img, revoke } = got
    const nw = img.naturalWidth || fullW
    const nh = img.naturalHeight || fullH
    const layer = document.createElement('canvas')
    layer.width = canvas.width
    layer.height = canvas.height
    const lctx = layer.getContext('2d')
    if (!lctx) {
      if (revoke) URL.revokeObjectURL(revoke)
      break
    }
    lctx.translate(x + w / 2, y + h / 2)
    applyPanelTransform(lctx, transform)
    lctx.drawImage(
      img,
      (crop?.x ?? 0) * nw,
      (crop?.y ?? 0) * nh,
      (crop?.w ?? 1) * nw,
      (crop?.h ?? 1) * nh,
      -cw / 2,
      -ch / 2,
      cw,
      ch,
    )
    if (revoke) URL.revokeObjectURL(revoke)
    // 这一层一个不透明像素都没有 = 这一路白画了（解码出了一张空图）：换下一路
    if (layerIsBlank(layer)) {
      steps.push(`${kind}${retry ? '-retry' : ''}:blank`)
      continue
    }
    steps.push(`${kind}${retry ? '-retry' : ''}:ok`)
    // 只有冻结在手里的 SVG 算 `'svg'`；素材图 / render 兜底一律 `'live'`——哪怕
    // `source.svg` 本来就有，这一路是从「svg 解析 / 解码失败之后落到这里」的
    outcome = kind === 'svg' ? 'svg' : 'live'
    result = layer
    break
  }
  trace({ panel: o.id, steps })
  return { layer: result, outcome }
}

/** 画一个非面板对象（文字 / 形状 / 箭头）到主画布；面板走 `renderPanelLayer` */
function drawObject(ctx: CanvasRenderingContext2D, o: Exclude<CanvasObject, PanelObject>, scale: number): void {
  const x = o.x * scale
  const y = o.y * scale
  const w = o.w * scale
  const h = o.h * scale
  if (o.type === 'text') {
    const px = Math.max(1, o.sizePt * MM_PER_PT * scale)
    ctx.save()
    ctx.fillStyle = o.color || '#000'
    ctx.font = `${o.bold ? 'bold ' : ''}${px}px ${o.fontFamily ?? 'serif'}`
    ctx.textBaseline = 'top'
    const align = o.align === 'center' ? 'center' : o.align === 'right' ? 'right' : 'left'
    ctx.textAlign = align
    const ax = align === 'center' ? x + w / 2 : align === 'right' ? x + w : x
    o.text.split('\n').forEach((line, i) => ctx.fillText(line, ax, y + i * px * 1.2, w || undefined))
    ctx.restore()
    return
  }
  ctx.save()
  ctx.strokeStyle = o.color || '#000'
  ctx.lineWidth = Math.max(0.5, o.strokePt * MM_PER_PT * scale)
  const ends = o.type === 'arrow' || o.shape === 'line' ? { start: o.start, end: o.end } : null
  if (ends?.start && ends.end) {
    ctx.beginPath()
    ctx.moveTo(x + ends.start.rx * w, y + ends.start.ry * h)
    ctx.lineTo(x + ends.end.rx * w, y + ends.end.ry * h)
    ctx.stroke()
  } else if (o.type === 'arrow') {
    // 没有端点的旧箭头：不猜方向
  } else if (o.shape === 'ellipse') {
    ctx.beginPath()
    ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2)
    if (o.fill) {
      ctx.fillStyle = o.fill
      ctx.fill()
    }
    ctx.stroke()
  } else {
    if (o.fill) {
      ctx.fillStyle = o.fill
      ctx.fillRect(x, y, w, h)
    }
    ctx.strokeRect(x, y, w, h)
  }
  ctx.restore()
}

function encode(canvas: HTMLCanvasElement): Promise<TimelineThumb | null> {
  return new Promise((resolve) => {
    try {
      canvas.toBlob(
        (blob) => {
          if (!blob) return resolve(null)
          // WKWebView 编不出 webp，会静默交回 png：**按实际类型**报，不按请求的
          resolve({ blob, type: blob.type === 'image/webp' ? 'image/webp' : 'image/png' })
        },
        'image/webp',
        0.82,
      )
    } catch {
      // canvas 被 taint（某张 SVG 带了跨源引用）：没有缩略图，节点照拍
      resolve(null)
    }
  })
}

/** 一份文档此刻的各面板图源（`panelSource` 的结果）；取不出来 = `null`（没有缩略图） */
export type ThumbSources = ReadonlyMap<string, PanelSource> | null

/**
 * 取各面板**此刻**的图源——只读 renderStore，同步、便宜。关键时刻在操作**发起**那一刻取
 * （`captureMoment`），完成时再合成：那时用户可能已经改了面板，现取的话缩略图画的是
 * 之后的样子，与节点里那份文档对不上（Codex #679）。
 */
export function captureThumbSources(doc: FigureDocument): ThumbSources {
  const sources = new Map<string, PanelSource>()
  try {
    for (const o of doc.objects) if (o.type === 'panel' && !o.hidden) sources.set(o.id, panelSource(o))
  } catch {
    return null
  }
  return sources
}

/**
 * 标注对象 id → 被写回烙进的面板 id：这条标注的内容这次写回已经烙进了那张图（Codex #679 P1
 * 追加）。只登记「这次写回带着标注」的那些标注，普通画布标注不在里面。
 *
 * 画不画由合成器**这一刻实际选中的图源**判定，不能在拍节点那一刻靠「此刻有没有 SVG」猜——
 * SVG 可能到合成器手里才发现解析 / 解码失败，退到合成时才现取的 render，那张图已经烙好了。
 */
export type BakedAnnotations = ReadonlyMap<string, string>

/**
 * 合成一张画布缩略图；任何失败都是 `null`。
 *
 * 图源：给了 `sources` 就用它（发起那一刻取好的）；没给就在**同一个同步段里**现取
 * （见 `panelSource`）——这个函数在第一个 await 之前就把各面板的图源取完了。
 *
 * `bakedInto` 给的话，登记在里面的标注对象按各自面板的结局决定画不画（只有冻结 SVG 才画），
 * 但**始终留在文档原来的叠放位置**（先预渲染全部面板、再按原顺序叠，见函数体）。
 */
export async function composeTimelineThumb(
  doc: FigureDocument,
  given?: ThumbSources,
  bakedInto?: BakedAnnotations,
): Promise<TimelineThumb | null> {
  const sources = given === undefined ? captureThumbSources(doc) : given
  if (!sources) return null
  await Promise.resolve()
  try {
    const { w: pw, h: ph } = doc.page
    if (!(pw > 0 && ph > 0) || typeof document === 'undefined') return null
    const scale = TIMELINE_THUMB_PX / Math.max(pw, ph)
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(pw * scale))
    canvas.height = Math.max(1, Math.round(ph * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    if (!doc.page.transparent) {
      ctx.fillStyle = doc.page.bg || '#FFFFFF'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }
    // 两遍：先把**全部面板**预渲染成图层（面板图的加载一个一个等；结局全齐），再**按文档里的
    // 顺序**（= 叠放次序）把图层 / 标注 / 形状叠到主画布上。
    //
    // `bakedInto` 里的标注画不画，要等**它所属面板这一刻实际选中的图源**才能判定（面板手里的
    // SVG 可能解析 / 解码失败、退到合成时才现取的 render——那张图已经烙进标注了），而结局只有
    // 预渲染完才知道。预渲染与叠放分开之后，标注留在自己原来的叠放位置上：只决定画不画，
    // 不挪位置——挪到最上面会盖住写回后画布里本来盖住它的重叠面板（Codex #679 P1 第三轮）。
    const layers = new Map<string, HTMLCanvasElement | null>()
    const panelOutcome = new Map<string, PanelDrawOutcome>()
    for (const o of doc.objects) {
      if (o.hidden || o.type !== 'panel') continue
      const { layer, outcome } = await renderPanelLayer(o, canvas, scale, sources.get(o.id) ?? null)
      layers.set(o.id, layer)
      panelOutcome.set(o.id, outcome)
    }
    for (const o of doc.objects) {
      if (o.hidden) continue
      if (o.type === 'panel') {
        const layer = layers.get(o.id)
        if (!layer) continue
        ctx.save()
        ctx.globalAlpha = o.opacity ?? 1
        ctx.drawImage(layer, 0, 0)
        ctx.restore()
        continue
      }
      if (bakedInto?.has(o.id)) {
        const outcome = panelOutcome.get(bakedInto.get(o.id) ?? '')
        // 面板结局明确、且不是冻结的 SVG：这张图已经烙进了这条标注，不再叠一遍；
        // 查不到结局（面板被隐藏、或不在这份文档里）按「照画」兜底，不能因为查不到就悄悄丢一条
        if (outcome && outcome !== 'svg') continue
      }
      drawObject(ctx, o, scale)
    }
    return await encode(canvas)
  } catch {
    return null
  }
}
