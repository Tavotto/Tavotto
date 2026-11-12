/**
 * 竖排里侧躺字的基线位置（ADR 0163）。
 *
 * 侧躺字的 em 方框中点落在列中线上（CSS 竖排用中线基线；导出 `vertical.layout`：`side_x = cx − 字号 ×（上升 + 下降）/ 2`）。
 * 预览里要把公式放在**同一条**基线上，需要这张脸的上升 / 下降——浏览器自己量（canvas 的 `fontBoundingBox`，
 * 与它排侧躺字用的是同一组度量），不另抄一份字体表。
 */

/** 没有 canvas（jsdom 等）时的退路：批准字体的典型值（上升 0.9、下降 0.2 → 0.35 em） */
const FALLBACK_SHIFT_EM = 0.35

const cache = new Map<string, number>()

/** 基线相对列中线向左偏多少 px：`(上升 − 下降) / 2 × 字号`。 */
export function sidewaysBaselineShift(stack: string, sizePx: number): number {
  return shiftEm(stack) * sizePx
}

function shiftEm(stack: string): number {
  const hit = cache.get(stack)
  if (hit !== undefined) return hit
  let em = FALLBACK_SHIFT_EM
  let measured = false
  try {
    if (typeof document !== 'undefined' && !/jsdom/i.test(navigator.userAgent)) {
      const ctx = document.createElement('canvas').getContext('2d')
      if (ctx) {
        ctx.font = `100px ${stack}`
        const m = ctx.measureText('x')
        if (m.fontBoundingBoxAscent > 0) {
          em = (m.fontBoundingBoxAscent - m.fontBoundingBoxDescent) / 2 / 100
          // 字体还没载入时量到的是回退字体：不缓存，下次再量
          measured = document.fonts?.check?.(`100px ${stack}`) ?? true
        }
      }
    }
  } catch {
    /* 退路 */
  }
  if (measured) cache.set(stack, em)
  return em
}
