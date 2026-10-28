import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { perfCount } from '@/perf/core'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'

/**
 * 画布网格（2026-10-28 打磨第三批 D；维护者看过两级点阵后改回**线网格，但更细更轻**）。
 *
 * - 每 `gridSize` 一条线，**1 个设备像素**粗（高清屏上 0.5 CSS px），paper-ink 的固定低比例，静止与拖动时同一个浅色，不变深；
 *   格子小到不足 8 屏幕像素时按倍数抽稀；
 * - **唯一的强调**：拖动吸上网格线时，那一条线短暂换成选中蓝（`--color-sel`），离开后 0.45 秒淡出。
 *
 * **屏幕空间**：一块 `<canvas>`，尺寸 = 舞台视口 × dpr，线落在整设备像素上，任何缩放下都锐利；它住在世界层里（纸之上、
 * 内容之下），用「`left/top = −pan / zoom` + `scale(1 / zoom)`」与世界层的 zoom 相抵——**世界层的变换不动**；纸面原点按
 * 实际位置量出来。线的墨是 `--color-paper-ink`（纸上的墨，两套主题同值，暗色里纸仍是白的）。
 */

/** 网格线的墨：paper-ink 的比例，常量（不随缩放、拖动变深）。高清屏上线只有 0.5 CSS px，墨略高一点才与 1x 观感相当 */
const LINE_INK = 0.05
const LINE_INK_HIDPI = 0.08
/** 强调淡出时长（秒） */
const FADE_MS = 450
/** 网格线最小屏幕间距（CSS px）：再密就按倍数抽稀，不糊成一片灰 */
const MIN_MAJOR_PX = 8


interface Lit {
  axis: 'x' | 'y'
  /** 页面 mm */
  v: number
  /** 离开（不再吸着）的时刻；null = 仍吸着 */
  left: number | null
}

const isOnGrid = (v: number, step: number) => {
  const k = v / step
  return Math.abs(k - Math.round(k)) < 1e-6
}

/** 把一个 CSS 变量解析成浏览器算出来的颜色串：借一个临时节点（canvas 的 fillStyle 不认 var()） */
function resolveColor(host: HTMLElement, cssVar: string): string {
  const probe = document.createElement('span')
  probe.style.color = `var(${cssVar})`
  probe.style.display = 'none'
  host.appendChild(probe)
  const c = getComputedStyle(probe).color
  probe.remove()
  return c
}

export function GridDots({ w, h, gridSize }: { w: number; h: number; gridSize: number }) {
  perfCount('render.GridDots')
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  const snapXs = useInteractionStore((s) => s.snapXs)
  const snapYs = useInteractionStore((s) => s.snapYs)
  const snapToGrid = useUiStore((s) => s.snapToGrid)
  const snapEnabled = useUiStore((s) => s.snapEnabled)
  const ref = useRef<HTMLCanvasElement>(null)
  const [dprTick, setDprTick] = useState(0)
  const lit = useRef<Lit[]>([])
  const raf = useRef(0)
  const colors = useRef<{ ink: string; sel: string } | null>(null)
  const step = Math.max(gridSize, 0.5)

  // 浏览器缩放 / 换屏会改 dpr
  useEffect(() => {
    const on = () => setDprTick((n) => n + 1)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])

  // 吸附状态 → 「亮着的网格线」表：吸上的（且正好在网格上的）线亮着；不再吸着的记下离开时刻，用来淡出。
  // 用 layout effect：要排在下面那次绘制之前，同一次提交里画出来的就是最新的亮线
  useLayoutEffect(() => {
    const now = performance.now()
    const want: Lit[] = []
    if (snapEnabled && snapToGrid) {
      for (const v of snapXs) if (isOnGrid(v, step) && v >= 0 && v <= w + 1e-6) want.push({ axis: 'x', v, left: null })
      for (const v of snapYs) if (isOnGrid(v, step) && v >= 0 && v <= h + 1e-6) want.push({ axis: 'y', v, left: null })
    }
    const next: Lit[] = []
    for (const old of lit.current) {
      const still = want.find((q) => q.axis === old.axis && Math.abs(q.v - old.v) < 1e-6)
      if (still) continue
      // 没在 want 里了：开始淡出（已经在淡的保持原来的离开时刻）
      next.push(old.left === null ? { ...old, left: now } : old)
    }
    for (const q of want) next.push(q)
    lit.current = next.filter((q) => q.left === null || now - q.left < FADE_MS)
  }, [snapXs, snapYs, snapToGrid, snapEnabled, step, w, h])

  useLayoutEffect(() => {
    const canvas = ref.current
    if (!canvas || viewW <= 0 || viewH <= 0) return
    const dpr = window.devicePixelRatio || 1
    const bw = Math.round(viewW * dpr)
    const bh = Math.round(viewH * dpr)
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw
      canvas.height = bh
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    if (!colors.current) colors.current = { ink: resolveColor(canvas, '--color-paper-ink'), sel: resolveColor(canvas, '--color-sel') }
    const { ink, sel } = colors.current

    const draw = () => {
      ctx.globalAlpha = 1
      ctx.clearRect(0, 0, bw, bh)
      // 每个点：CSS px 单位 × dpr = 设备像素；位置先落到整设备像素
      const pxPerMm = mmToWorld(1) * zoom
      const cellPx = step * pxPerMm
      const stride = Math.max(1, Math.ceil(MIN_MAJOR_PX / cellPx))
      const t = Math.max(1, Math.round(dpr)) // 强调线粗细（设备像素）= 1 CSS px
      // 纸面原点在 canvas 里的位置**量出来**，不从 pan 推：舞台四周还有标尺等偏移，从 pan 推会让点阵整体错开纸边
      const page = canvas.parentElement?.getBoundingClientRect()
      const own = canvas.getBoundingClientRect()
      const ox = page ? page.left - own.left : panX
      const oy = page ? page.top - own.top : panY
      const sx = (mm: number) => Math.round((ox + mmToWorld(mm) * zoom) * dpr)
      const sy = (mm: number) => Math.round((oy + mmToWorld(mm) * zoom) * dpr)
      const nx = Math.floor(w / step + 1e-6)
      const ny = Math.floor(h / step + 1e-6)
      // 可见的格子下标范围
      const i0 = Math.max(0, Math.floor((-ox / (mmToWorld(1) * zoom)) / step) - 1)
      const i1 = Math.min(nx, Math.ceil(((viewW - ox) / (mmToWorld(1) * zoom)) / step) + 1)
      const j0 = Math.max(0, Math.floor((-oy / (mmToWorld(1) * zoom)) / step) - 1)
      const j1 = Math.min(ny, Math.ceil(((viewH - oy) / (mmToWorld(1) * zoom)) / step) + 1)
      // 颜色串来自 token 的解析值，不透明度走 globalAlpha（不手写 rgba 字面量）
      const paint = (c: string, a: number) => {
        ctx.fillStyle = c
        ctx.globalAlpha = a
      }

      // 网格线：每 gridSize 一条，**1 个设备像素**粗（高清屏上是 0.5 CSS px），只铺在纸面范围内
      const line = 1
      const x0 = sx(0), x1 = sx(w), y0 = sy(0), y1 = sy(h)
      paint(ink, dpr >= 2 ? LINE_INK_HIDPI : LINE_INK)
      ctx.beginPath()
      for (let i = i0 - (i0 % stride); i <= i1; i += stride) {
        if (i < 0) continue
        ctx.rect(sx(i * step), y0, line, y1 - y0)
      }
      for (let j = j0 - (j0 % stride); j <= j1; j += stride) {
        if (j < 0) continue
        ctx.rect(x0, sy(j * step), x1 - x0, line)
      }
      ctx.fill()

      // 强调：吸上的那一条网格线短暂换成选中蓝；离开后淡出
      const now = performance.now()
      let fading = false
      for (const q of lit.current) {
        const a = q.left === null ? 1 : 1 - (now - q.left) / FADE_MS
        if (a <= 0) continue
        if (q.left !== null) fading = true
        paint(sel, 0.7 * a)
        if (q.axis === 'y') ctx.fillRect(x0, sy(q.v), x1 - x0, t)
        else ctx.fillRect(sx(q.v), y0, t, y1 - y0)
      }
      lit.current = lit.current.filter((q) => q.left === null || now - q.left < FADE_MS)
      raf.current = fading ? requestAnimationFrame(draw) : 0
    }
    cancelAnimationFrame(raf.current)
    draw()
    return () => cancelAnimationFrame(raf.current)
  }, [zoom, panX, panY, viewW, viewH, w, h, step, snapXs, snapYs, snapToGrid, snapEnabled, dprTick])

  if (viewW <= 0 || viewH <= 0) return null
  return (
    <canvas
      ref={ref}
      aria-hidden
      data-grid-dots=""
      className="pointer-events-none absolute"
      style={{
        // 净变换 = 恒等：canvas 的屏幕原点恰在舞台 (0,0)，一个 canvas 像素 = 一个设备像素
        left: -panX / zoom,
        top: -panY / zoom,
        // 世界层自己还会再乘一次 zoom：CSS 尺寸写 viewW，`scale(1/zoom)` 与世界层的 zoom 相抵，屏幕上正好 viewW
        width: viewW,
        height: viewH,
        transform: `scale(${1 / zoom})`,
        transformOrigin: '0 0',
      }}
    />
  )
}
