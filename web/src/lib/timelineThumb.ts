import { panelSrc } from '@/lib/api'
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

/** 把一份 SVG 串变成能 `drawImage` 的图：根元素补上像素宽高（去掉宽高的那份没有固有尺寸）。 */
function svgImageSource(svg: string, w: number, h: number): string {
  const sized = svg.replace(
    /<svg\b/,
    `<svg width="${Math.max(1, Math.round(w))}" height="${Math.max(1, Math.round(h))}" preserveAspectRatio="none"`,
  )
  return URL.createObjectURL(new Blob([sized], { type: 'image/svg+xml' }))
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image()
    const timer = window.setTimeout(() => resolve(null), IMAGE_TIMEOUT_MS)
    img.onload = () => {
      window.clearTimeout(timer)
      resolve(img)
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
type PanelSource = { svg: string } | { url: string } | null

function panelSource(o: PanelObject): PanelSource {
  const render = panelRender(useRenderStore.getState(), o)
  if (render?.svg) return { svg: render.svg }
  const mtime = useAssetStore.getState().byId[o.fileId]?.mtime
  const url = panelSrc(o.fileId, o.fileKind, 400, mtime)
  return url ? { url } : null
}

async function panelImage(
  source: PanelSource,
  wPx: number,
  hPx: number,
): Promise<{ img: HTMLImageElement; revoke?: string } | null> {
  if (!source) return null
  if ('svg' in source) {
    const url = svgImageSource(source.svg, wPx, hPx)
    const img = await loadImage(url)
    if (img) return { img, revoke: url }
    URL.revokeObjectURL(url)
    return null
  }
  const img = await loadImage(source.url)
  return img ? { img } : null
}

async function drawObject(
  ctx: CanvasRenderingContext2D,
  o: CanvasObject,
  scale: number,
  source: PanelSource,
): Promise<void> {
  const x = o.x * scale
  const y = o.y * scale
  const w = o.w * scale
  const h = o.h * scale
  if (o.type === 'panel') {
    const rot = o.rotation ?? 0
    // 旋转 90/270 时内容的显示尺寸与落位包围盒互换
    const [cw, ch] = rot === 90 || rot === 270 ? [h, w] : [w, h]
    const crop = o.crop
    const fullW = cw / (crop?.w ?? 1)
    const fullH = ch / (crop?.h ?? 1)
    const got = await panelImage(source, fullW * 2, fullH * 2)
    if (!got) return
    const { img, revoke } = got
    const nw = img.naturalWidth || fullW
    const nh = img.naturalHeight || fullH
    ctx.save()
    ctx.globalAlpha = o.opacity ?? 1
    ctx.translate(x + w / 2, y + h / 2)
    if (rot) ctx.rotate((rot * Math.PI) / 180)
    ctx.drawImage(
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
    ctx.restore()
    if (revoke) URL.revokeObjectURL(revoke)
    return
  }
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

/**
 * 合成一张画布缩略图；任何失败都是 `null`。
 *
 * 调用方要在**拍节点的同一个同步段里**调它（见 `panelSource`）：这个函数在第一个
 * await 之前就把各面板的图源取完了。
 */
export async function composeTimelineThumb(doc: FigureDocument): Promise<TimelineThumb | null> {
  const sources = new Map<string, PanelSource>()
  try {
    for (const o of doc.objects) if (o.type === 'panel' && !o.hidden) sources.set(o.id, panelSource(o))
  } catch {
    return null
  }
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
    // 按文档里的顺序画（= 叠放次序），一个一个等：面板图的加载顺序不能改变叠放
    for (const o of doc.objects) {
      if (o.hidden) continue
      await drawObject(ctx, o, scale, sources.get(o.id) ?? null)
    }
    return await encode(canvas)
  } catch {
    return null
  }
}
