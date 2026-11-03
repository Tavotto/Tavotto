import { useEffect, useLayoutEffect, useMemo, useRef, useState, type HTMLAttributes } from 'react'
import { dotLayout, dotsFor, filledCells, sweepCells } from '@/lib/dotProgress'
import { prefersReducedMotion } from '@/lib/motion'
import { dotForPpi, useScreenPpi } from '@/lib/screenPpi'
import { useEffectiveTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

/**
 * 点阵进度条（ADR 0168）：包管理作业、准备卡、更新下载、修复下载、导出进度共用这一份。
 * 未完成画稀点、已完成画密点——**点一样大、同一个颜色，只用疏密表现填充**；几何全在 `lib/dotProgress.ts`。
 *
 * - 点径：桌面壳报了屏幕物理 ppi 就按它选（`lib/screenPpi.dotForPpi`，目标约 0.2 mm）；否则取最接近缩放倍数的整数。
 * - canvas 按**物理像素**画（ResizeObserver 的 `device-pixel-content-box`；不支持的内核按 dpr 估），点是整数个物理
 *   像素，Windows 125% / 175% 也不发灰；换主题当场重画（墨色读 canvas 自己的 `color`，即 `.progress-dots` 的 ink-2）。
 * - `pct` 是数就是确定态：变化时一格一格补齐或退回，`aria-valuenow` 照报。
 * - `pct === null` 是不确定态：一小段密点来回扫。**不做假百分比**——拿不到真数就不报 `aria-valuenow`。
 * - 减少动态效果时两种动画都关：确定态直接到位，不确定态那段密点停在正中。
 * - 百分比文字由调用方自己写，这里不画。
 * - 画布冷启动那条（PanelView 内的 2px 扫条，叠在图上）不是这个组件，仍用 `animate-sweep`。
 */
const STEP_MS = 18
const SWEEP_MS = 80

interface Box {
  devW: number
  devH: number
  cssW: number
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof matchMedia === 'undefined') return
    const mq = matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(mq.matches)
    mq.addEventListener?.('change', update)
    return () => mq.removeEventListener?.('change', update)
  }, [])
  return reduced
}

/** canvas 在屏幕上实际占的物理像素：优先 `devicePixelContentBoxSize`（Chromium / WebView2），否则 CSS 尺寸 × dpr */
function useDeviceBox(canvas: React.RefObject<HTMLCanvasElement | null>): Box | null {
  const [box, setBox] = useState<Box | null>(null)
  useLayoutEffect(() => {
    const el = canvas.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const set = (devW: number, devH: number, cssW: number) =>
      setBox((b) => (b && b.devW === devW && b.devH === devH && b.cssW === cssW ? b : { devW, devH, cssW }))
    const estimate = () => {
      const r = el.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      set(Math.round(r.width * dpr), Math.round(r.height * dpr), r.width)
    }
    let exact = false // 内核报过 devicePixelContentBoxSize：之后只信它
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const d = e.devicePixelContentBoxSize?.[0]
        if (d) {
          exact = true
          set(d.inlineSize, d.blockSize, e.contentRect.width)
        } else estimate()
      }
    })
    try {
      ro.observe(el, { box: 'device-pixel-content-box' })
    } catch {
      ro.observe(el)
    }
    // 不支持 device-pixel-content-box 的内核：窗口拖到另一块屏幕（dpr 变了）时 ResizeObserver 不报，自己听
    let mq: MediaQueryList | null = null
    const listen = () => {
      mq?.removeEventListener?.('change', onDpr)
      mq = typeof matchMedia === 'undefined' ? null : matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
      mq?.addEventListener?.('change', onDpr)
    }
    function onDpr() {
      if (!exact) estimate()
      listen()
    }
    listen()
    return () => {
      ro.disconnect()
      mq?.removeEventListener?.('change', onDpr)
    }
  }, [canvas])
  return box
}

export function ProgressBar({
  pct,
  label,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLDivElement>, 'role' | 'children'> & {
  pct: number | null
  label: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const box = useDeviceBox(canvasRef)
  const reduced = useReducedMotion()
  const theme = useEffectiveTheme()
  // 桌面壳读得到屏幕物理 ppi 时按它选点径（各屏幕上点的实际大小接近 Mac Retina）；读不到按缩放倍数取整
  const dot = dotForPpi(useScreenPpi())
  const indeterminate = pct === null
  const clamped = pct === null ? null : Number.isFinite(pct) ? Math.min(100, Math.max(0, pct)) : 0
  const layout = useMemo(
    () => (box && box.devW > 0 && box.devH > 0 ? dotLayout(box.devW, box.devH, box.cssW, dot) : null),
    [box, dot],
  )
  const target = layout && clamped !== null ? filledCells(layout, clamped) : 0

  // 确定态：一格一格走到目标（第一次量到尺寸时直接到位，不从 0 补起）
  const [shown, setShown] = useState<number | null>(null)
  const shownRef = useRef(shown)
  shownRef.current = shown
  useEffect(() => {
    if (indeterminate || !layout) return
    if (reduced || shownRef.current === null) {
      setShown(target)
      return
    }
    if (shownRef.current === target) return
    const id = window.setInterval(() => {
      // 函数式更新：两拍之间还没重渲染也照样一格一格走
      setShown((cur) => (cur === null || cur === target ? target : cur + (target > cur ? 1 : -1)))
      if (shownRef.current === target) window.clearInterval(id)
    }, STEP_MS)
    return () => window.clearInterval(id)
  }, [target, indeterminate, reduced, layout])

  // 不确定态：一段密点来回扫
  const span = layout ? sweepCells(layout) : 1
  const [pos, setPos] = useState(0)
  const dirRef = useRef(1)
  useEffect(() => {
    if (!indeterminate || reduced || !layout) return
    const max = Math.max(0, layout.cells - span)
    const id = window.setInterval(() => {
      setPos((p) => {
        let next = p + dirRef.current
        if (next > max || next < 0) {
          dirRef.current *= -1
          next = Math.min(max, Math.max(0, p + dirRef.current))
        }
        return next
      })
    }, SWEEP_MS)
    return () => window.clearInterval(id)
  }, [indeterminate, reduced, layout, span])

  // 画：位图尺寸 = 物理像素，一个点对一个整像素方块
  useLayoutEffect(() => {
    const c = canvasRef.current
    if (!c || !box || !layout) return
    if (c.width !== box.devW) c.width = box.devW
    if (c.height !== box.devH) c.height = box.devH
    const ctx = c.getContext('2d')
    if (!ctx) return
    let from = 0
    let to = 0
    if (indeterminate) {
      from = reduced ? Math.floor((layout.cells - span) / 2) : Math.min(pos, Math.max(0, layout.cells - span))
      to = from + span
    } else {
      to = Math.min(layout.cells, shown ?? target)
    }
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.fillStyle = getComputedStyle(c).color
    for (const [x, y] of dotsFor(layout, from, to)) ctx.fillRect(x, y, layout.dot, layout.dot)
  }, [box, layout, indeterminate, reduced, pos, span, shown, target, theme])

  return (
    <div
      {...props}
      role="progressbar"
      aria-label={label}
      aria-valuenow={clamped ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
      data-progress-indeterminate={indeterminate || undefined}
      className={cn('progress-dots', className)}
    >
      <canvas ref={canvasRef} aria-hidden="true" />
    </div>
  )
}
