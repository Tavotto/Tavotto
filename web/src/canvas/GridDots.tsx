import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { perfCount } from '@/perf/core'
import { useInteractionStore } from '@/store/interactionStore'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'

/**
 * 画布网格（2026-10-28 打磨第三批 D，方案 3「两级 + 上下文强调」，维护者补了一条铁律：**点绝不变深**）。
 *
 * - **主点**：每 `gridSize` 一个小十字（1 CSS px 粗、臂长 2 CSS px），静止与拖动时都是同一个浅色；
 * - **副点**：每半格一个 1 CSS px 的点，**只在半格 ≥ 10 屏幕像素时**才淡入（12–20 之间渐显，仍是同一个浅色，不更深）；
 *   格子小到不足 8 屏幕像素时主点按倍数抽稀（只抽稀，不变密也不变深）；
 * - **唯一的强调**：拖动吸上网格线时，那一整行 / 一整列的网格点短暂换成选中蓝（`--color-sel`），松开 / 离开后 0.45 秒淡出。
 *
 * **屏幕空间**：一块 `<canvas>`，尺寸 = 舞台视口 × dpr，每个点落在整设备像素上，任何缩放下都锐利；它住在世界层里（纸之上、
 * 内容之下，所以对象仍盖住网格），用「`left/top = −pan / zoom` + `scale(1 / zoom)`」把自己的净变换抵成恒等——**世界层的
 * 变换不动**。点的位置每帧按当前 pan / zoom 重算（几百到两万个小矩形，一次 fill）。
 * 点的墨是 `--color-paper-ink`（纸上的墨，两套主题同值，暗色里纸仍是白的）。
 */

/** 主点 / 副点的墨：paper-ink 的百分比；两档都是**常量**，不随缩放、拖动变化 */
const MAJOR_INK = 0.3
const MINOR_INK = 0.24
/** 强调淡出时长（秒） */
const FADE_MS = 450
/** 主点最小屏幕间距（CSS px）：再密就按倍数抽稀 */
const MIN_MAJOR_PX = 8
/** 半格屏幕间距到这个值才开始画副点，到 `MINOR_FULL_PX` 全部淡入 */
const MINOR_START_PX = 10
const MINOR_FULL_PX = 18

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
      const t = Math.max(1, Math.round(dpr)) // 十字 / 点的粗细（设备像素）= 1 CSS px
      const arm = 2 * t
      const half = Math.floor(t / 2)
      const sx = (mm: number) => Math.round((panX + mmToWorld(mm) * zoom) * dpr)
      const sy = (mm: number) => Math.round((panY + mmToWorld(mm) * zoom) * dpr)
      const nx = Math.floor(w / step + 1e-6)
      const ny = Math.floor(h / step + 1e-6)
      // 可见的格子下标范围
      const i0 = Math.max(0, Math.floor((-panX / (mmToWorld(1) * zoom)) / step) - 1)
      const i1 = Math.min(nx, Math.ceil(((viewW - panX) / (mmToWorld(1) * zoom)) / step) + 1)
      const j0 = Math.max(0, Math.floor((-panY / (mmToWorld(1) * zoom)) / step) - 1)
      const j1 = Math.min(ny, Math.ceil(((viewH - panY) / (mmToWorld(1) * zoom)) / step) + 1)
      // 颜色串来自 token 的解析值，不透明度走 globalAlpha（不手写 rgba 字面量）
      const paint = (c: string, a: number) => {
        ctx.fillStyle = c
        ctx.globalAlpha = a
      }

      // 主点：十字
      paint(ink, MAJOR_INK)
      ctx.beginPath()
      for (let j = j0 - (j0 % stride); j <= j1; j += stride) {
        if (j < 0) continue
        const y = sy(j * step)
        for (let i = i0 - (i0 % stride); i <= i1; i += stride) {
          if (i < 0) continue
          const x = sx(i * step)
          ctx.rect(x - arm, y - half, arm * 2 + t, t)
          ctx.rect(x - half, y - arm, t, arm * 2 + t)
        }
      }
      ctx.fill()

      // 副点：半格；只在半格够大时淡入，淡入完仍是同一个浅色
      const halfPx = cellPx / 2
      if (stride === 1 && halfPx >= MINOR_START_PX) {
        const k = Math.min(1, (halfPx - MINOR_START_PX) / (MINOR_FULL_PX - MINOR_START_PX))
        paint(ink, MINOR_INK * k)
        ctx.beginPath()
        for (let j = j0; j <= j1; j++) {
          for (let i = i0; i <= i1; i++) {
            const hx = (i + 0.5) * step
            const hy = (j + 0.5) * step
            const xm = hx <= w + 1e-6
            const ym = hy <= h + 1e-6
            // 半格点三种：格心、横向半格、纵向半格（与主点的并集 = 半格网格）
            if (xm && ym && i < nx && j < ny) ctx.rect(sx(hx) - half, sy(hy) - half, t, t)
            if (xm && i < nx && j >= 0 && j <= ny) ctx.rect(sx(hx) - half, sy(j * step) - half, t, t)
            if (ym && j < ny && i >= 0 && i <= nx) ctx.rect(sx(i * step) - half, sy(hy) - half, t, t)
          }
        }
        ctx.fill()
      }

      // 强调：吸上的那一整行 / 一整列的网格点换选中蓝；离开后淡出
      const now = performance.now()
      let fading = false
      for (const q of lit.current) {
        const a = q.left === null ? 1 : 1 - (now - q.left) / FADE_MS
        if (a <= 0) continue
        if (q.left !== null) fading = true
        paint(sel, 0.9 * a)
        ctx.beginPath()
        if (q.axis === 'y') {
          const y = sy(q.v)
          for (let i = Math.max(0, i0 - (i0 % stride)); i <= i1; i += stride) {
            const x = sx(i * step)
            ctx.rect(x - arm, y - half, arm * 2 + t, t)
            ctx.rect(x - half, y - arm, t, arm * 2 + t)
          }
        } else {
          const x = sx(q.v)
          for (let j = Math.max(0, j0 - (j0 % stride)); j <= j1; j += stride) {
            const y = sy(j * step)
            ctx.rect(x - arm, y - half, arm * 2 + t, t)
            ctx.rect(x - half, y - arm, t, arm * 2 + t)
          }
        }
        ctx.fill()
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
        width: viewW * zoom,
        height: viewH * zoom,
        transform: `scale(${1 / zoom})`,
        transformOrigin: '0 0',
      }}
    />
  )
}
