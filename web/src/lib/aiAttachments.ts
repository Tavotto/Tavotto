/**
 * 改图助手的附件（ADR 0175）：把用户给的文件整理成后端愿意收的样子。图片给模型「看」，其余文件（CSV / XLSX / TXT / JSON ……）原样附上。
 *
 * 用户都是小白，**这里没有「报错」这一档**：图片格式认得就原样用，太大 / 像素太多就悄悄缩小重编码，浏览器解不了的图片（多半是 HEIC）
 * 当普通文件附上；只有单个文件超过上限才回 `tooLarge`（调用方给一句话、不卡住别的文件）。上限与后端 `engine/ai_attachments.py`
 * 对齐：个数 `ATTACH_MAX` = `MAX_FILES`，图片字节 `ATTACH_MAX_IMAGE_BYTES` = `MAX_IMAGE_BYTES`（= 内置执行器 `read_image` 的 4 MiB），
 * 文件 `ATTACH_MAX_FILE_BYTES` = `MAX_FILE_BYTES`，合计 `ATTACH_MAX_TOTAL_BYTES` = `MAX_TOTAL_BYTES`。
 * 前端把图缩得比后端的像素上限更狠（边长 ≤ 2048）：模型端本来就会把大图缩小，传大图只是白白占带宽与额度。
 */

export const ATTACH_MAX = 6
export const ATTACH_MAX_IMAGE_BYTES = 4 * 1024 * 1024
export const ATTACH_MAX_FILE_BYTES = 25 * 1024 * 1024
export const ATTACH_MAX_TOTAL_BYTES = 50 * 1024 * 1024
/** 重编码的目标字节（留一点余量，别贴着上限） */
export const ATTACH_TARGET_BYTES = Math.floor(3.5 * 1024 * 1024)
/** 重编码后的最长边（像素） */
export const ATTACH_MAX_SIDE = 2048
/** 超过这个像素数就一定重编码（不管字节多小：几千万像素的纯色图字节很小、解码却很重） */
export const ATTACH_MAX_PIXELS = 16_000_000
const THUMB_SIDE = 96

export type ImageMime = 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp'

/** 按魔数认格式（不信扩展名与 `File.type`：截图粘贴 / 拖拽来的 type 常是空的） */
export function sniffImageMime(b: Uint8Array): ImageMime | null {
  const at = (i: number, s: string) => s.split('').every((c, k) => b[i + k] === c.charCodeAt(0))
  if (b.length >= 8 && b[0] === 0x89 && at(1, 'PNG') && b[4] === 0x0d && b[5] === 0x0a && b[6] === 0x1a && b[7] === 0x0a)
    return 'image/png'
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg'
  if (b.length >= 6 && (at(0, 'GIF87a') || at(0, 'GIF89a'))) return 'image/gif'
  if (b.length >= 12 && at(0, 'RIFF') && at(8, 'WEBP')) return 'image/webp'
  return null
}

/** 等比缩到最长边 ≤ `maxSide`（只缩不放大），取整且至少 1 像素 */
export function fitWithin(w: number, h: number, maxSide = ATTACH_MAX_SIDE): { w: number; h: number } {
  const k = Math.min(1, maxSide / Math.max(w, h))
  return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) }
}

/** 这张图能不能原样交给后端：格式认得、字节不超、像素不多（量不出像素时按「多」处理不了，交给解码结果） */
export function canPassThrough(mime: ImageMime | null, bytes: number, w: number | null, h: number | null): boolean {
  if (!mime || bytes > ATTACH_TARGET_BYTES) return false
  if (w !== null && h !== null && (w * h > ATTACH_MAX_PIXELS || Math.max(w, h) > 4096)) return false
  return true
}

/** 从一批文件里取前 `room` 个：→ 取用的、因为超过个数被丢掉的个数（任何文件都收，不按类型挑） */
export function pickFiles(files: readonly File[], room: number): { take: File[]; overflow: number } {
  const take = files.slice(0, Math.max(0, room))
  return { take, overflow: files.length - take.length }
}

/** 文件名的扩展名（小写、不带点、≤ 5 个字符，给文件卡的角标用）；没有就空串 */
export function extOf(name: string): string {
  const m = /\.([A-Za-z0-9]{1,5})$/.exec(name)
  return m ? m[1].toLowerCase() : ''
}

export type AttachKind = 'image' | 'file'

export interface PreparedAttachment {
  kind: AttachKind
  blob: Blob
  /** 缩略图（data URL，≤ 96px 边长，几 KB）；只有图片有，画布不可用时退回 object URL */
  thumb: string
  name: string
  ext: string
  size: number
}
export type PrepareOutcome = { ok: true; item: PreparedAttachment } | { ok: false; reason: 'tooLarge' }

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
async function reencode(src: Decoded, preferPng: boolean): Promise<{ blob: Blob; mime: ImageMime } | null> {
  let { w, h } = fitWithin(src.w, src.h)
  for (let round = 0; round < 5; round++) {
    const c = canvasOf(w, h)
    if (!c) return null
    if (preferPng && round === 0) {
      src.draw(c.ctx, w, h)
      const png = await toBlob(c.canvas, 'image/png')
      if (png && png.size <= ATTACH_TARGET_BYTES) return { blob: png, mime: 'image/png' }
    }
    // JPEG 没有透明：先铺白底，别让透明处变黑
    c.ctx.fillStyle = '#fff'
    c.ctx.fillRect(0, 0, w, h)
    src.draw(c.ctx, w, h)
    for (const q of [0.9, 0.78, 0.62]) {
      const jpg = await toBlob(c.canvas, 'image/jpeg', q)
      if (jpg && jpg.size <= ATTACH_TARGET_BYTES) return { blob: jpg, mime: 'image/jpeg' }
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

/** 把一个文件整理成可以上传的附件。永不抛：只有单个文件超限回 `{ ok: false }`，由调用方说一句话 */
export async function prepareAttachment(file: File): Promise<PrepareOutcome> {
  const head = new Uint8Array(await file.slice(0, 16).arrayBuffer())
  const mime = sniffImageMime(head)
  const imageish = mime !== null || file.type.startsWith('image/')
  const plain = (): PrepareOutcome =>
    file.size > ATTACH_MAX_FILE_BYTES
      ? { ok: false, reason: 'tooLarge' }
      : { ok: true, item: { kind: 'file', blob: file, thumb: '', name: file.name, ext: extOf(file.name), size: file.size } }
  if (!imageish) return plain()
  const dec = await decode(file)
  try {
    if (canPassThrough(mime, file.size, dec?.w ?? null, dec?.h ?? null)) {
      const blob = file.type === mime ? file : new Blob([file], { type: mime! })
      return { ok: true, item: { kind: 'image', blob, thumb: await makeThumb(dec, blob), name: file.name, ext: extOf(file.name), size: file.size } }
    }
    if (!dec) return plain() // 浏览器解不了的图片（多半是 HEIC）：当普通文件附上，不卡住
    const out = await reencode(dec, mime === 'image/png' || mime === 'image/gif')
    if (!out) return plain()
    return { ok: true, item: { kind: 'image', blob: out.blob, thumb: await makeThumb(dec, out.blob), name: file.name, ext: extOf(file.name), size: out.blob.size } }
  } finally {
    dec?.close()
  }
}
