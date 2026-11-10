/**
 * 改图助手的参考图（ADR 0175）：把用户给的文件整理成后端愿意收、模型看得了的图。
 *
 * 用户都是小白，**这里没有「报错」这一档**：格式认得就原样用，太大 / 像素太多就悄悄缩小重编码，HEIC 之类浏览器能解码就转、
 * 解不了才回一个 `undecodable`（调用方给一句话、不卡住别的图）。上限与后端 `engine/ai_refimages.py` 对齐：
 * 张数 `REF_IMAGE_MAX` = `MAX_IMAGES`，字节 `REF_IMAGE_MAX_BYTES` = `MAX_BYTES`（= 内置执行器 `read_image` 的 4 MiB）。
 * 前端缩得比后端的像素上限更狠（边长 ≤ 2048）：模型端本来就会把大图缩小，传大图只是白白占带宽与额度。
 */

export const REF_IMAGE_MAX = 4
export const REF_IMAGE_MAX_BYTES = 4 * 1024 * 1024
/** 重编码的目标字节（留一点余量，别贴着上限） */
export const REF_IMAGE_TARGET_BYTES = Math.floor(3.5 * 1024 * 1024)
/** 重编码后的最长边（像素） */
export const REF_IMAGE_MAX_SIDE = 2048
/** 超过这个像素数就一定重编码（不管字节多小：几千万像素的纯色图字节很小、解码却很重） */
export const REF_IMAGE_MAX_PIXELS = 16_000_000
const THUMB_SIDE = 64

export type RefMime = 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp'

/** 按魔数认格式（不信扩展名与 `File.type`：截图粘贴 / 拖拽来的 type 常是空的） */
export function sniffImageMime(b: Uint8Array): RefMime | null {
  const at = (i: number, s: string) => s.split('').every((c, k) => b[i + k] === c.charCodeAt(0))
  if (b.length >= 8 && b[0] === 0x89 && at(1, 'PNG') && b[4] === 0x0d && b[5] === 0x0a && b[6] === 0x1a && b[7] === 0x0a)
    return 'image/png'
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg'
  if (b.length >= 6 && (at(0, 'GIF87a') || at(0, 'GIF89a'))) return 'image/gif'
  if (b.length >= 12 && at(0, 'RIFF') && at(8, 'WEBP')) return 'image/webp'
  return null
}

/** 等比缩到最长边 ≤ `maxSide`（只缩不放大），取整且至少 1 像素 */
export function fitWithin(w: number, h: number, maxSide = REF_IMAGE_MAX_SIDE): { w: number; h: number } {
  const k = Math.min(1, maxSide / Math.max(w, h))
  return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) }
}

/** 这张图能不能原样交给后端：格式认得、字节不超、像素不多（量不出像素时按「多」处理不了，交给解码结果） */
export function canPassThrough(mime: RefMime | null, bytes: number, w: number | null, h: number | null): boolean {
  if (!mime || bytes > REF_IMAGE_TARGET_BYTES) return false
  if (w !== null && h !== null && (w * h > REF_IMAGE_MAX_PIXELS || Math.max(w, h) > 4096)) return false
  return true
}

/** 从一批文件里挑出能当图的前 `room` 张：→ 取用的、因为超过张数被丢掉的个数、不是图片的个数 */
export function pickImageFiles(files: readonly File[], room: number): { take: File[]; overflow: number; notImage: number } {
  const imgs = files.filter((f) => f.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|heic|heif|avif|bmp|tiff?)$/i.test(f.name))
  const take = imgs.slice(0, Math.max(0, room))
  return { take, overflow: imgs.length - take.length, notImage: files.length - imgs.length }
}

export interface PreparedRefImage {
  blob: Blob
  mime: RefMime
  /** 小缩略图（data URL，≤ 64px 边长，几 KB）；画布不可用时退回 object URL */
  thumb: string
  name: string
}
export type PrepareOutcome = { ok: true; image: PreparedRefImage } | { ok: false; reason: 'undecodable' }

type Decoded = { w: number; h: number; draw: (ctx: CanvasRenderingContext2D, dw: number, dh: number) => void; close: () => void }

async function decode(blob: Blob): Promise<Decoded | null> {
  if (typeof createImageBitmap !== 'function') return null
  try {
    const bmp = await createImageBitmap(blob)
    return {
      w: bmp.width,
      h: bmp.height,
      draw: (ctx, dw, dh) => ctx.drawImage(bmp, 0, 0, dw, dh),
      close: () => bmp.close?.(),
    }
  } catch {
    return null // 浏览器解不了（多半是 HEIC / 损坏的图）
  }
}

function canvasOf(w: number, h: number): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D } | null {
  if (typeof document === 'undefined') return null
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  return ctx ? { canvas, ctx } : null
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), type, quality))
}

/** 缩小并重编码到 ≤ 目标字节：先试 PNG（截图 / 带透明，仅当原图是 PNG / GIF），不行再 JPEG 降质量、再不行缩小尺寸 */
async function reencode(src: Decoded, preferPng: boolean): Promise<{ blob: Blob; mime: RefMime } | null> {
  let { w, h } = fitWithin(src.w, src.h)
  for (let round = 0; round < 5; round++) {
    const c = canvasOf(w, h)
    if (!c) return null
    if (preferPng && round === 0) {
      src.draw(c.ctx, w, h)
      const png = await toBlob(c.canvas, 'image/png')
      if (png && png.size <= REF_IMAGE_TARGET_BYTES) return { blob: png, mime: 'image/png' }
    }
    // JPEG 没有透明：先铺白底，别让透明处变黑
    c.ctx.fillStyle = '#fff'
    c.ctx.fillRect(0, 0, w, h)
    src.draw(c.ctx, w, h)
    for (const q of [0.9, 0.78, 0.62]) {
      const jpg = await toBlob(c.canvas, 'image/jpeg', q)
      if (jpg && jpg.size <= REF_IMAGE_TARGET_BYTES) return { blob: jpg, mime: 'image/jpeg' }
    }
    w = Math.max(1, Math.round(w * 0.75))
    h = Math.max(1, Math.round(h * 0.75))
  }
  return null
}

async function makeThumb(src: Decoded | null, fallback: Blob): Promise<string> {
  if (src) {
    const { w, h } = fitWithin(src.w, src.h, THUMB_SIDE)
    const c = canvasOf(w, h)
    if (c) {
      src.draw(c.ctx, w, h)
      try {
        return c.canvas.toDataURL('image/jpeg', 0.7)
      } catch {
        /* 画布被污染 / 不可导出：退回 object URL */
      }
    }
  }
  try {
    return URL.createObjectURL(fallback)
  } catch {
    return ''
  }
}

/** 把一个文件整理成可以上传的参考图。永不抛：解不了回 `{ ok: false }`，由调用方说一句话 */
export async function prepareRefImage(file: File): Promise<PrepareOutcome> {
  const head = new Uint8Array(await file.slice(0, 16).arrayBuffer())
  const mime = sniffImageMime(head)
  const dec = await decode(file)
  try {
    if (canPassThrough(mime, file.size, dec?.w ?? null, dec?.h ?? null)) {
      const blob = mime && file.type === mime ? file : new Blob([file], { type: mime! })
      return { ok: true, image: { blob, mime: mime!, thumb: await makeThumb(dec, blob), name: file.name } }
    }
    if (!dec) return { ok: false, reason: 'undecodable' }
    const out = await reencode(dec, mime === 'image/png' || mime === 'image/gif')
    if (!out) return { ok: false, reason: 'undecodable' }
    return { ok: true, image: { blob: out.blob, mime: out.mime, thumb: await makeThumb(dec, out.blob), name: file.name } }
  } finally {
    dec?.close()
  }
}
