import { useLayoutEffect, useState, type RefObject } from 'react'

/**
 * 教程里「图上某个元素此刻在哪」的量法（`tutorialLayout` 的环与提示、第一步的箭头都用它）。
 * **不写固定像素坐标**：每次按目标元素此刻的 `getBoundingClientRect` 现量，窗口缩放、字号变化、
 * 图例挪动之后跟着重量（ResizeObserver + 依赖变化），不会与目标错位。
 */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** 这次聚焦是不是键盘带来的（`:focus-visible`）；引擎不认这个选择器时按「是」处理——宁可多画一圈环 */
export function isFocusVisible(el: Element): boolean {
  try {
    return el.matches(':focus-visible')
  } catch {
    return true
  }
}

/** 等目标挂上最多等几帧（约半秒） */
const ATTACH_FRAMES = 30

const sameRect = (a: Rect | null, b: Rect | null) =>
  a === b ||
  (!!a && !!b && Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5 && Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5)

/**
 * `target` 在 `frame` 里的框（frame 自己的 CSS 像素；frame 被 transform 缩放时也换算对）。
 * 依赖变了、frame 尺寸变了、窗口缩放了都重量；量不到（还没挂上 / 隐藏）回 null。
 */
export function useTrackedRect(
  target: RefObject<Element | null>,
  frame: RefObject<HTMLElement | null>,
  deps: readonly unknown[],
): Rect | null {
  const [rect, setRect] = useState<Rect | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const t = target.current
      const f = frame.current
      if (!t || !f || !f.offsetWidth || !f.offsetHeight) {
        setRect((r) => (r === null ? r : null))
        return
      }
      const tb = t.getBoundingClientRect()
      const fb = f.getBoundingClientRect()
      const sx = fb.width / f.offsetWidth || 1
      const sy = fb.height / f.offsetHeight || 1
      const next = { x: (tb.left - fb.left) / sx, y: (tb.top - fb.top) / sy, w: tb.width / sx, h: tb.height / sy }
      setRect((r) => (sameRect(r, next) ? r : next))
    }
    // 目标与舞台可能晚一帧才挂上（舞台先量好自己的尺寸才渲染图）：等它们出现再开始量、再挂观察者，
    // 最多等 ATTACH_FRAMES 帧——等不到就是真的不在（这一步没画图），回 null
    let ro: ResizeObserver | null = null
    let raf = 0
    let tries = 0
    const attach = () => {
      if (!target.current || !frame.current) {
        if (tries++ < ATTACH_FRAMES) raf = requestAnimationFrame(attach)
        return
      }
      measure()
      if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(measure)
        ro.observe(frame.current)
      }
    }
    measure()
    attach()
    window.addEventListener('resize', measure)
    return () => {
      cancelAnimationFrame(raf)
      ro?.disconnect()
      window.removeEventListener('resize', measure)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return rect
}

/* ------------------------------ 欢迎页标注气泡 ------------------------------ */

/**
 * 欢迎页演示图旁边的三枚标注气泡（「修改文字」「调整图例」「拖动位置」）的落点。
 *
 * 做法：气泡一律排在**图右侧的专用槽**里（卡片内、图框之外），一条细线从被指元素的**右边缘外侧**
 * 水平拉过去——气泡永远不压在图里的文字上、不探出卡片、不挤到卡片上方的标签行或下方的按钮。
 * 位置全按被指元素的 bbox（图幅 pt）× 舞台此刻的缩放现算，再做竖向碰撞避让（互不重叠、不出舞台上下沿），
 * 不写固定像素。纯函数，`tutorialGeometry.test.ts` 钉住「气泡不与标题 bbox / 彼此 / 卡片外沿相交」。
 */

/** 被指元素：图幅 pt 里的框（左上原点） */
export interface CalloutTarget {
  key: string
  box: readonly [x: number, y: number, w: number, h: number]
}

export interface CalloutPlacement {
  key: string
  /** 引线在被指元素一侧的端点（舞台 px）：落在元素右边缘外侧，不遮挡它 */
  anchor: { x: number; y: number }
  /** 气泡矩形（舞台 px，左上原点；x 在图框右边之外） */
  bubble: Rect
}

/** 引线端点离被指元素右边缘的空隙（px） */
export const CALLOUT_ANCHOR_GAP = 4
/** 图框右边到气泡左边的距离（引线的水平跨度，px） */
export const CALLOUT_LEAD = 20
/** 气泡的固定高度 / 彼此之间的最小竖向间隙（px） */
export const CALLOUT_BUBBLE_H = 32
export const CALLOUT_GAP = 8

/** 量不到标题文字外框（jsdom / 字体未就绪）时按字数估一个：宽取偏大的 0.55 em，端点宁可离得远一点 */
export function estimateTextBox(
  text: string,
  sizePt: number,
  anchor: readonly [number, number],
): [number, number, number, number] {
  const w = text.length * sizePt * 0.55
  return [anchor[0] - w / 2, anchor[1] - sizePt * 0.8, w, sizePt]
}

export function placeCallouts(opts: {
  figure: { width: number; height: number }
  /** 图框（舞台里那张纸）此刻的像素尺寸 */
  stage: { w: number; h: number }
  targets: readonly CalloutTarget[]
  bubbleW: number
  bubbleH?: number
  gap?: number
}): CalloutPlacement[] {
  const { figure, stage, targets, bubbleW } = opts
  const h = opts.bubbleH ?? CALLOUT_BUBBLE_H
  const gap = opts.gap ?? CALLOUT_GAP
  const s = stage.w / figure.width
  const anchors = targets.map((t) => ({
    key: t.key,
    x: (t.box[0] + t.box[2]) * s + CALLOUT_ANCHOR_GAP,
    y: (t.box[1] + t.box[3] / 2) * s,
  }))
  // 竖向：理想位置 = 与被指元素同高；自上而下互相推开，再自下而上收回舞台里（上下沿都不出）
  const order = anchors.map((_, i) => i).sort((a, b) => anchors[a].y - anchors[b].y)
  const ys = new Map<number, number>()
  let prev = -Infinity
  for (const i of order) {
    const y = Math.max(anchors[i].y - h / 2, prev + h + gap, 0)
    ys.set(i, y)
    prev = y
  }
  let limit = Math.max(stage.h - h, 0)
  for (const i of [...order].reverse()) {
    const y = Math.min(ys.get(i)!, limit)
    ys.set(i, y)
    limit = y - h - gap
  }
  return anchors.map((a, i) => ({
    key: a.key,
    anchor: { x: a.x, y: a.y },
    bubble: { x: stage.w + CALLOUT_LEAD, y: ys.get(i)!, w: bubbleW, h },
  }))
}

export const rectsOverlap = (a: Rect, b: Rect) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
