import type { PointerEvent as ReactPointerEvent } from 'react'
import { parseCssColor, type ColorSample } from '@/lib/appearance'
import { pushRecentColor } from '@/components/ui/colorPalette'
import { applyEyedropper } from '@/store/appearanceActions'

/**
 * 吸管（ADR 0185 第二批，Illustrator 的 I）：点画布上看得见的东西——画布标注**和图内元素**（面板的矢量预览）都行——
 * 读它**画出来的**填充色与描边色（计算后的样式，不是文档字段：图内元素没有文档字段可读），交给 `applyEyedropper`
 * 给选区上色（一次 commit）。读的是浏览器渲染的那一层，所以吸到的就是用户看到的颜色。
 *
 * 绘制工具下世界层整体 `pointer-events: none`（画的时候不能点中已有对象），命中测试也就看不见它们：采样那一瞬间给根节点挂
 * `data-eyedropper-sampling`，样式表把画布里的东西临时都放开，`elementsFromPoint` 取完立刻摘掉——同一个同步调用里，
 * 页面不会重绘，指针事件也不会落到谁身上。覆盖层（选框、手柄、透明命中层）一律跳过。
 */

const SAMPLING_ATTR = 'data-eyedropper-sampling'
const SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'polygon', 'polyline', 'line', 'text', 'tspan'])

function ensureStyle(): void {
  if (typeof document === 'undefined' || document.getElementById('tavotto-eyedropper-style')) return
  const el = document.createElement('style')
  el.id = 'tavotto-eyedropper-style'
  el.textContent = `[${SAMPLING_ATTR}] [data-canvas-stage] * { pointer-events: auto !important; }`
  document.head.appendChild(el)
}

/** 这个元素是覆盖层 / 命中层（不是画出来的内容） */
const isChrome = (el: Element) =>
  !!el.closest('[data-overlay-svg]') ||
  el.hasAttribute('data-hit-path') ||
  el.hasAttribute('data-hit-shape') ||
  el.hasAttribute('data-hit-line')

/** 屏幕上这一点画出来的颜色（最上面那个有颜色的图元）；什么都没有 = 两样都是 null */
export function sampleAt(clientX: number, clientY: number): ColorSample {
  if (typeof document === 'undefined' || typeof document.elementsFromPoint !== 'function') return { fill: null, stroke: null }
  ensureStyle()
  const root = document.documentElement
  root.setAttribute(SAMPLING_ATTR, '')
  let hits: Element[]
  try {
    hits = document.elementsFromPoint(clientX, clientY)
  } finally {
    root.removeAttribute(SAMPLING_ATTR)
  }
  for (const el of hits) {
    if (!SHAPES.has(el.localName) || isChrome(el)) continue
    const cs = getComputedStyle(el)
    if (cs.visibility === 'hidden' || cs.display === 'none') continue
    const fill = Number(cs.fillOpacity || '1') > 0 ? parseCssColor(cs.fill) : null
    const stroke =
      Number(cs.strokeOpacity || '1') > 0 && parseFloat(cs.strokeWidth || '0') > 0 ? parseCssColor(cs.stroke) : null
    if (fill || stroke) return { fill, stroke }
  }
  return { fill: null, stroke: null }
}

/** 吸管工具下在画布上按下：采样、上色，吸到的颜色记进「最近用过」 */
export function eyedropperPointerDown(e: ReactPointerEvent): void {
  e.preventDefault()
  const s = sampleAt(e.clientX, e.clientY)
  if (applyEyedropper(s)) {
    if (s.stroke) pushRecentColor(s.stroke)
    if (s.fill) pushRecentColor(s.fill)
  }
}
