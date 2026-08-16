export const PT_PER_MM = 72 / 25.4
export const MM_PER_PT = 25.4 / 72
/** 100% 缩放时 1mm 对应的 CSS 像素（96dpi） */
export const BASE_PX_PER_MM = 96 / 25.4
/** paper_style.py 的正文字号，用于面板等效字号估算 */
export const BASE_FONT_PT = 9
/** 后端 /api/render 支持的渲染宽度档位 */
export const RENDER_BUCKETS = [200, 400, 800, 1600, 3200] as const

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
export const round1 = (v: number) => Math.round(v * 10) / 10
export const round2 = (v: number) => Math.round(v * 100) / 100

export const mmToPt = (mm: number) => mm * PT_PER_MM
export const ptToMm = (pt: number) => pt * MM_PER_PT

export function pickBucket(neededPx: number): number {
  return RENDER_BUCKETS.find((b) => b >= neededPx) ?? RENDER_BUCKETS[RENDER_BUCKETS.length - 1]
}

/** 面板等效正文字号 / 等效 DPI —— 论文排版最关键的两个体检指标 */
export function effectivePt(widthMm: number, nativeWMm: number): number {
  if (!nativeWMm) return BASE_FONT_PT
  return BASE_FONT_PT * (widthMm / nativeWMm)
}

export function effectiveDpi(pxW: number, widthMm: number): number {
  if (!pxW || !widthMm) return 0
  return Math.round(pxW / (widthMm / 25.4))
}

export function formatMm(v: number): string {
  return round1(v).toFixed(1)
}

export function formatCm(v: number): string {
  return round1(v / 10).toString()
}
