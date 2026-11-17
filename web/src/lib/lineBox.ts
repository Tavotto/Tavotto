/**
 * 横排画布文字的行盒（ADR 0073 修订，2026-11-16）：文字框里中文、拉丁、单行公式都上下居中，预览 = 导出。
 *
 * 导出（`rendercore/typography.line_box`）把基线放在行盒中线下固定 `TEXT_BASELINE_BELOW_CENTER_PER_MILLE`，与字体
 * 度量无关；浏览器按这张脸自己的度量放（中线下约 `(上升 − 下降) / 2`，再按整像素取整半行距）。`TextView` 把整段
 * 内容往下挪二者之差，公式的布局盒错开同一个量（`MathFormula` 的 `lineBox`），撑出来的行盒与导出逐行一致。
 *
 * 浏览器那条基线**量出来**，不按字体度量推算：Chromium 把上升 / 下降与半行距取整到像素，推算在小字号上差出
 * 近 1 px（`tests/test_horizontal_css_parity.py` 实测）。量法：屏幕外一行同字体、同字号、同行距的字，行首放一个
 * 0×0 的行内块——它的下边缘就是基线。本文件只依赖 `typography` 的常数（真浏览器探针把它转译后装进页面）。
 */
import { TEXT_BASELINE_BELOW_CENTER_PER_MILLE } from '@/lib/typography'

const cache = new Map<string, number>()

/**
 * 要往下挪多少 px（负 = 往上）。`stack` / 粗斜体 / 字号 / 行距与 `TextView` 外层那一份相同；
 * 没有排版引擎（jsdom）时为零。字体还没载入时量到的是回退字体：不缓存，下次渲染再量。
 */
export function horizontalBaselineFixPx(
  stack: string,
  sizePx: number,
  lineHeight: number,
  style: { bold?: boolean; italic?: boolean } = {},
): number {
  const weight = style.bold ? '700' : '400'
  const slant = style.italic ? 'italic' : 'normal'
  const key = `${slant}|${weight}|${sizePx}|${lineHeight}|${stack}`
  const hit = cache.get(key)
  if (hit !== undefined) return hit
  const browser = browserBaselinePx(stack, sizePx, lineHeight, weight, slant)
  if (browser === null) return 0
  const fix = sizePx * (lineHeight / 2 + TEXT_BASELINE_BELOW_CENTER_PER_MILLE / 1000) - browser
  let loaded = true
  try {
    loaded = document.fonts?.check?.(`${slant} ${weight} ${sizePx}px ${stack}`) ?? true
  } catch {
    /* 当已载入 */
  }
  if (loaded) cache.set(key, fix)
  return fix
}

/** 浏览器把一行的基线放在行盒顶下多少 px；量不了时 null */
function browserBaselinePx(
  stack: string,
  sizePx: number,
  lineHeight: number,
  weight: string,
  slant: string,
): number | null {
  if (typeof document === 'undefined' || !document.body) return null
  if (typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent)) return null
  const probe = document.createElement('div')
  probe.setAttribute('aria-hidden', 'true')
  Object.assign(probe.style, {
    position: 'absolute',
    left: '-10000px',
    top: '0',
    visibility: 'hidden',
    pointerEvents: 'none',
    whiteSpace: 'pre',
    fontFamily: stack,
    fontSize: `${sizePx}px`,
    fontWeight: weight,
    fontStyle: slant,
    lineHeight: String(lineHeight),
  })
  const mark = document.createElement('span')
  Object.assign(mark.style, { display: 'inline-block', width: '0', height: '0' })
  probe.append(mark, document.createTextNode('Hx'))
  document.body.appendChild(probe)
  try {
    const top = probe.getBoundingClientRect().top
    const base = mark.getBoundingClientRect().bottom - top
    return Number.isFinite(base) && base > 0 ? base : null
  } finally {
    probe.remove()
  }
}
