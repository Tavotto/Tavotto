import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { perfCount } from '@/perf/core'
import { boundsOf } from '@/lib/geometry'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { mmToPx, mmToViewX, mmToViewY, useViewportStore, type ViewTransform } from '@/store/viewportStore'
import { startGuideDrag } from './interactions'

export const RULER_SIZE = 20

const STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500]

/**
 * 选一个让刻度间距不小于 8px 的整齐步长。
 *
 * 门槛跟着字号走：刻度数字从 10 抬到 11 之后（2026-09-15 打磨 C4），7px 的间距会让
 * 相邻两个主刻度的标签贴上。
 */
function pickStep(t: ViewTransform) {
  for (const s of STEPS) if (mmToPx(s, t) >= 8) return s
  return STEPS[STEPS.length - 1]
}

/**
 * 标尺用到的颜色与字体：只来自 token（index.css 的 @theme），**按挂载量一次**（2026-10-07 设计审计 §10.1）——
 * 此前每画一帧都 `getComputedStyle` 一遍，拖动 / 缩放时每帧两次强制样式计算。主题换了（暗色那一期）再按需重量。
 */
interface RulerInk {
  bg: string
  page: string
  line: string
  text: string
  accent: string
  border: string
  sans: string
}

function readInk(): RulerInk {
  // 不在这里留第二份字面量当兜底——那几份兜底色早已过期（2026-10-07 设计审计 §8）；
  // 样式表没加载时 canvas 忽略空串，什么都不画也比画错色好
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    bg: v('--color-bg'),
    page: v('--color-surface'),
    line: v('--color-ink-3'),
    text: v('--color-ink-2'),
    accent: v('--color-sel'),
    border: v('--color-border'),
    sans: v('--font-sans') || 'system-ui, sans-serif',
  }
}

function draw(
  canvas: HTMLCanvasElement,
  axis: 'x' | 'y',
  t: ViewTransform,
  lengthPx: number,
  pageMm: number,
  cursorMm: number | null,
  ink: RulerInk,
  /** 选区在这条轴上的范围（mm），标尺上画一条淡 sel 带 */
  span: [number, number] | null,
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

  const { bg, line, text, accent } = ink

  ctx.fillStyle = bg
  ctx.fillRect(0, 0, w, h)

  // 页面范围底纹，一眼看出纸面在哪
  const at = (mm: number) => (axis === 'x' ? mmToViewX(mm, t) : mmToViewY(mm, t))
  const p0 = at(0)
  const p1 = at(pageMm)
  ctx.fillStyle = ink.page
  if (axis === 'x') ctx.fillRect(p0, 0, p1 - p0, h)
  else ctx.fillRect(0, p0, w, p1 - p0)

  // 选区带（2026-10-07 设计审计 §10.1）：选中对象在这条轴上占的那一段，淡 sel 底 + 两端各一根 sel 线——
  // 读刻度时不用把选区边缘目测平移到标尺上
  if (span) {
    const s0 = Math.round(at(span[0])) + 0.5
    const s1 = Math.round(at(span[1])) + 0.5
    ctx.globalAlpha = 0.14
    ctx.fillStyle = accent
    if (axis === 'x') ctx.fillRect(s0, 0, s1 - s0, h)
    else ctx.fillRect(0, s0, w, s1 - s0)
    ctx.globalAlpha = 1
    ctx.strokeStyle = accent
    ctx.lineWidth = 1
    ctx.beginPath()
    for (const s of [s0, s1]) {
      if (axis === 'x') {
        ctx.moveTo(s, 0)
        ctx.lineTo(s, RULER_SIZE)
      } else {
        ctx.moveTo(0, s)
        ctx.lineTo(RULER_SIZE, s)
      }
    }
    ctx.stroke()
  }

  const step = pickStep(t)
  const major = step * 5
  const startMm = Math.floor(
    (axis === 'x' ? -t.panX : -t.panY) / mmToPx(1, t) / step,
  ) * step
  const endMm = startMm + (lengthPx / mmToPx(1, t)) + step * 2

  // 刻度数字用界面的系统字体 + 11px（宪法第六节：字号阶梯 xs 11 起；数值 = 系统字体 +
  // tabular-nums）。此前是 10px 的等宽——比下限还小一档，而等宽只留给代码 / 路径 / 脚本名。
  // 字体栈取自 --font-sans 这一个出处，canvas 不支持 font-variant-numeric，系统字体在
  // 这个字号上数字本来就是等宽的（2026-09-15 打磨 C4）
  ctx.font = `11px ${ink.sans}`
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
      // 刻度数字一律整数（步长最小 1 mm），也不写出「-0」
      const label = String(Math.round(mm) || 0)
      if (axis === 'x') {
        // 起点从 +2 挪到 +3：11px 的字比 10px 宽，贴着刻度线读起来像连在一起
        ctx.fillText(label, v + 3, 2)
      } else {
        ctx.save()
        ctx.translate(2, v - 3)
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
  ctx.strokeStyle = ink.border
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

function useRuler(
  axis: 'x' | 'y',
  lengthPx: number,
  pageMm: number,
  span: [number, number] | null,
) {
  const ref = useRef<HTMLCanvasElement>(null)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const cursor = useInteractionStore((s) => s.cursor)
  // 颜色按挂载量一次（见 readInk）
  const ink = useMemo(readInk, [])
  const s0 = span?.[0]
  const s1 = span?.[1]

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || !lengthPx) return
    const t: ViewTransform = { zoom, panX, panY, originX: 0, originY: 0 }
    const band: [number, number] | null = s0 != null && s1 != null ? [s0, s1] : null
    draw(canvas, axis, t, lengthPx, pageMm, cursor ? (axis === 'x' ? cursor.x : cursor.y) : null, ink, band)
  }, [axis, lengthPx, pageMm, zoom, panX, panY, cursor, ink, s0, s1])

  return ref
}

/** 选区（可见对象）的包围盒；没有选中就是 null。按值比较，拖动中每一帧都是新对象也不会多画 */
function useSelectionSpan(): { x: [number, number]; y: [number, number] } | null {
  const ids = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)
  const sel = objects.filter((o) => ids.includes(o.id) && !o.hidden)
  if (!sel.length) return null
  const b = boundsOf(sel)
  if (!b) return null
  return { x: [b.x, b.x + b.w], y: [b.y, b.y + b.h] }
}

/** 顶部 + 左侧 mm 刻度；从标尺往画布里拖可拉出参考线 */
export function Rulers({ viewW, viewH }: { viewW: number; viewH: number }) {
  perfCount('render.Rulers')
  const { t } = useTranslation('workspace')
  const page = useDocumentStore((s) => s.doc.page)
  const span = useSelectionSpan()
  const topRef = useRuler('x', viewW, page.w, span?.x ?? null)
  const leftRef = useRuler('y', viewH, page.h, span?.y ?? null)

  return (
    <>
      {/* 单位角（2026-10-07 设计审计 §10.1）：两条标尺交汇的那一格写出单位，读数不用猜是 mm 还是 pt */}
      <div
        data-ruler-unit
        className="absolute left-0 top-0 z-sticky flex items-center justify-center border-b border-r border-border bg-bg text-xs text-ink-3"
        style={{ width: RULER_SIZE, height: RULER_SIZE }}
        title={t('stage.rulerUnitTitle')}
      >
        {t('stage.rulerUnit')}
      </div>
      <canvas
        ref={topRef}
        className="absolute top-0 z-sticky cursor-ns-resize"
        style={{ left: RULER_SIZE, width: viewW, height: RULER_SIZE }}
        onPointerDown={(e) => startGuideDrag(e, 'y', null)}
      />
      <canvas
        ref={leftRef}
        className="absolute left-0 z-sticky cursor-ew-resize"
        style={{ top: RULER_SIZE, width: RULER_SIZE, height: viewH }}
        onPointerDown={(e) => startGuideDrag(e, 'x', null)}
      />
    </>
  )
}
