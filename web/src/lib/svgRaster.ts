/**
 * SVG → PNG（ADR 0189 的最后一道兜底）：既拆不成可编辑对象、项目里的 SVG 素材检查也不收（滤镜、外链这类 RenderCore
 * 画不准的），就按浏览器画出来的样子栅格化成一张 PNG 放进来，不让用户粘了个空。
 *
 * 用 `<img>` 画：图片模式下的 SVG 不执行脚本、不取外部资源（浏览器的规定），所以不受信任的 SVG 在这里也是安全的。
 * 分辨率按 `dpi` 与 SVG 的物理尺寸，长边封顶 `MAX_PX`。画不出来（坏 SVG、环境没有 canvas）返回 null。
 */
export const RASTER_DPI = 600
export const MAX_PX = 8000

export async function rasterizeSvg(svg: string, sizeMm: [number, number], dpi = RASTER_DPI): Promise<Blob | null> {
  if (typeof document === 'undefined' || typeof Image === 'undefined') return null
  const [wMm, hMm] = sizeMm[0] > 0 && sizeMm[1] > 0 ? sizeMm : [100, 100]
  let w = Math.round((wMm / 25.4) * dpi)
  let h = Math.round((hMm / 25.4) * dpi)
  const k = Math.min(1, MAX_PX / Math.max(w, h))
  w = Math.max(1, Math.round(w * k))
  h = Math.max(1, Math.round(h * k))
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
  try {
    const img = new Image(w, h)
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('svg image'))
      img.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(img, 0, 0, w, h)
    return await new Promise<Blob | null>((resolve) => canvas.toBlob((b) => resolve(b), 'image/png'))
  } catch {
    return null
  } finally {
    URL.revokeObjectURL(url)
  }
}
