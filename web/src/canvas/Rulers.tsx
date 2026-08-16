import { useEffect, useRef } from 'react'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore, type ViewTransform } from '@/store/viewportStore'
import { startGuideDrag } from './interactions'

export const RULER_SIZE = 20

const STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500]

/** 选一个让刻度间距不小于 7px 的整齐步长 */
function pickStep(t: ViewTransform) {
  for (const s of STEPS) if (mmToPx(s, t) >= 7) return s
  return STEPS[STEPS.length - 1]
}

function draw(
  canvas: HTMLCanvasElement,
  axis: 'x' | 'y',
  t: ViewTransform,
  lengthPx: number,
  pageMm: number,
  cursorMm: number | null,
) {
  const dpr = window.devicePixelRatio || 1
  const w = axis === 'x' ? lengthPx : RULER_SIZE
  const h = axis === 'x' ? RULER_SIZE : lengthPx
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const css = getComputedStyle(document.documentElement)
  const bg = css.getPropertyValue('--color-bg').trim() || '#F2F2EF'
  const line = css.getPropertyValue('--color-ink-3').trim() || '#A3A39A'
  const text = css.getPropertyValue('--color-ink-2').trim() || '#6E6E67'
  const accent = css.getPropertyValue('--color-sel').trim() || '#2F6FED'

  ctx.fillStyle = bg
  ctx.fillRect(0, 0, w, h)

  // 页面范围底纹，一眼看出纸面在哪
  const p0 = axis === 'x' ? mmToViewX(0, t) : mmToViewY(0, t)
  const p1 = axis === 'x' ? mmToViewX(pageMm, t) : mmToViewY(pageMm, t)
  ctx.fillStyle = '#FFFFFF'
  if (axis === 'x') ctx.fillRect(p0, 0, p1 - p0, h)
  else ctx.fillRect(0, p0, w, p1 - p0)

  const step = pickStep(t)
  const major = step * 5
  const startMm = Math.floor(
    (axis === 'x' ? -t.panX : -t.panY) / mmToPx(1, t) / step,
  ) * step
  const endMm = startMm + (lengthPx / mmToPx(1, t)) + step * 2

  ctx.font = '10px ui-monospace, "SF Mono", Menlo, monospace'
  ctx.textBaseline = 'top'
  ctx.lineWidth = 1

  for (let mm = startMm; mm <= endMm; mm += step) {
    const v = Math.round((axis === 'x' ? mmToViewX(mm, t) : mmToViewY(mm, t))) + 0.5
    const isMajor = Math.abs(mm % major) < 1e-6
    const len = isMajor ? 7 : 3.5
    ctx.strokeStyle = line
    ctx.globalAlpha = isMajor ? 0.85 : 0.5
    ctx.beginPath()
    if (axis === 'x') {
      ctx.moveTo(v, RULER_SIZE - len)
      ctx.lineTo(v, RULER_SIZE)
    } else {
      ctx.moveTo(RULER_SIZE - len, v)
      ctx.lineTo(RULER_SIZE, v)
    }
    ctx.stroke()
    ctx.globalAlpha = 1

    if (isMajor) {
      ctx.fillStyle = text
      const label = String(Math.round(mm))
      if (axis === 'x') {
        ctx.fillText(label, v + 2, 2)
      } else {
        ctx.save()
        ctx.translate(2, v - 2)
        ctx.rotate(-Math.PI / 2)
        ctx.fillText(label, 0, 0)
        ctx.restore()
      }
    }
  }

  if (cursorMm != null) {
    const v = Math.round(axis === 'x' ? mmToViewX(cursorMm, t) : mmToViewY(cursorMm, t)) + 0.5
    ctx.strokeStyle = accent
    ctx.beginPath()
    if (axis === 'x') {
      ctx.moveTo(v, 0)
      ctx.lineTo(v, RULER_SIZE)
    } else {
      ctx.moveTo(0, v)
      ctx.lineTo(RULER_SIZE, v)
    }
    ctx.stroke()
  }

  // 与画布之间的 1px 分隔
  ctx.strokeStyle = css.getPropertyValue('--color-border').trim() || '#E3E3DD'
  ctx.beginPath()
  if (axis === 'x') {
    ctx.moveTo(0, RULER_SIZE - 0.5)
    ctx.lineTo(w, RULER_SIZE - 0.5)
  } else {
    ctx.moveTo(RULER_SIZE - 0.5, 0)
    ctx.lineTo(RULER_SIZE - 0.5, h)
  }
  ctx.stroke()
}

function useRuler(axis: 'x' | 'y', lengthPx: number, pageMm: number) {
  const ref = useRef<HTMLCanvasElement>(null)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const cursor = useInteractionStore((s) => s.cursor)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || !lengthPx) return
    const t: ViewTransform = { zoom, panX, panY, originX: 0, originY: 0 }
    draw(canvas, axis, t, lengthPx, pageMm, cursor ? (axis === 'x' ? cursor.x : cursor.y) : null)
  }, [axis, lengthPx, pageMm, zoom, panX, panY, cursor])

  return ref
}

/** 顶部 + 左侧 mm 刻度；从标尺往画布里拖可拉出参考线 */
export function Rulers({ viewW, viewH }: { viewW: number; viewH: number }) {
  const page = useDocumentStore((s) => s.doc.page)
  const topRef = useRuler('x', viewW, page.w)
  const leftRef = useRuler('y', viewH, page.h)

  return (
    <>
      <div
        className="absolute left-0 top-0 z-10 border-b border-r border-border bg-bg"
        style={{ width: RULER_SIZE, height: RULER_SIZE }}
      />
      <canvas
        ref={topRef}
        className="absolute top-0 z-10 cursor-ns-resize"
        style={{ left: RULER_SIZE, width: viewW, height: RULER_SIZE }}
        onPointerDown={(e) => startGuideDrag(e, 'y', null)}
      />
      <canvas
        ref={leftRef}
        className="absolute left-0 z-10 cursor-ew-resize"
        style={{ top: RULER_SIZE, width: RULER_SIZE, height: viewH }}
        onPointerDown={(e) => startGuideDrag(e, 'x', null)}
      />
    </>
  )
}
