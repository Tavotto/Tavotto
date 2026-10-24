import { useEffect, useRef } from 'react'
import { BRAND_MARK_BOX, BRAND_MARK_PATHS } from '@/components/ui/brandMark.geometry'
import {
  TIER_COUNT,
  brightestRegions,
  flowNoise,
  lumaAt,
  lumaFromRgba,
  repelTarget,
  snapToGrid,
  springStep,
  widthTier,
  type Region,
} from './brandLinesMath'

/** 亮度表分辨率（格） */
const GRID = 48
/** 竖线间距 / 每节长度 / 节间缝（CSS px） */
const PITCH = 7
const SEGMENT = 8
const GAP = 2
/** 各档线宽（CSS px）：只有四档 */
const TIER_WIDTH = [0.7, 1.3, 2.1, 3.2] as const
/** 检测框吸附网格（CSS px） */
const BOX_SNAP = 8
const MAX_BOXES = 3

/** 画布用到的颜色全部来自 CSS token（不写字面色）；挂载时读一次 */
function readColors(el: HTMLElement) {
  const cs = getComputedStyle(el)
  const get = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  return { bg: get('--color-paper-ink', 'currentColor'), line: get('--color-paper', 'currentColor'), accent: get('--color-accent', 'currentColor') }
}

/** 把品牌标志画到离屏 canvas 再按格子采样成亮度表；拿不到 2D 上下文（jsdom 等）返回 null */
function buildLuma(): Float32Array | null {
  const size = GRID * 2
  const off = document.createElement('canvas')
  off.width = size
  off.height = size
  const ctx = off.getContext('2d', { willReadFrequently: true })
  if (!ctx || typeof Path2D === 'undefined') return null
  ctx.scale(size / BRAND_MARK_BOX, size / BRAND_MARK_BOX)
  for (const p of BRAND_MARK_PATHS) {
    // 框与横笔最亮；切开的那一片稍暗，保留标志的层次
    ctx.fillStyle = 'white'
    ctx.globalAlpha = p.role === 'ink' ? 1 : 0.55
    ctx.fill(new Path2D(p.d), 'evenodd')
  }
  return lumaFromRgba(ctx.getImageData(0, 0, size, size).data, size, size, GRID, GRID)
}

/**
 * 品牌竖线画：纯 canvas，没有任何图片。做法与各个纯函数见 `brandLinesMath.ts`。
 *
 * rAF 每帧清空重画，canvas 尺寸乘 devicePixelRatio；卸载 / 标签页不可见时停掉 rAF 并移除监听；
 * `prefers-reduced-motion` 时只画静态一帧（无噪声流动、无散开、无检测框）。
 * 最近 60 帧的平均绘制耗时写在 `data-frame-ms`（诊断 / 性能测量用）。
 */
export function BrandLines({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const luma = buildLuma()
    if (!ctx || !luma) return
    const colors = readColors(canvas)
    const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
    const regions: Region[] = brightestRegions(luma, GRID, GRID, MAX_BOXES)

    let w = 0
    let h = 0
    let lines = 0
    let pos = new Float32Array(0)
    let vel = new Float32Array(0)
    const mouse = { x: -1, y: -1, inside: false }
    let boxAlpha = 0
    let raf = 0
    let last = 0
    let t0 = 0
    let frames = 0
    let spent = 0

    const resize = () => {
      const r = canvas.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      w = Math.max(1, Math.round(r.width))
      h = Math.max(1, Math.round(r.height))
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      lines = Math.max(1, Math.floor(w / PITCH))
      pos = new Float32Array(lines)
      vel = new Float32Array(lines)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (reduced) draw(0, 0)
    }

    /** 标志在画布上的方框（居中、略小于短边） */
    const logoBox = () => {
      const s = Math.min(w, h) * 0.8
      return { x: (w - s) / 2, y: (h - s) / 2, s }
    }

    const draw = (t: number, dt: number) => {
      const started = performance.now()
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = colors.bg
      ctx.fillRect(0, 0, w, h)
      const box = logoBox()
      const spacing = w / lines
      const buckets: Path2D[] = Array.from({ length: TIER_COUNT }, () => new Path2D())
      const segs = Math.ceil(h / SEGMENT)
      for (let i = 0; i < lines; i++) {
        const x0 = (i + 0.5) * spacing
        if (!reduced) {
          const target = mouse.inside ? repelTarget(x0, mouse.x, 70, 26) : 0
          const [p, v] = springStep(pos[i], vel[i], target, dt)
          pos[i] = p
          vel[i] = v
        }
        // 没有鼠标时线也轻轻浮动
        const x = x0 + pos[i] + (reduced ? 0 : Math.sin(t * 0.8 + i * 0.7) * 0.9)
        const u = (x0 - box.x) / box.s
        for (let j = 0; j < segs; j++) {
          const y = j * SEGMENT
          const v = (y + SEGMENT / 2 - box.y) / box.s
          let b = lumaAt(luma, GRID, GRID, u, v)
          if (!reduced) {
            // 缓慢向下流动的噪声叠在亮度表上：标志处略有起伏，暗处有淡淡的流光
            b = Math.min(1, Math.max(0, b * 0.85 + (flowNoise(i * 0.35, j * 0.28 - t * 1.1, t) - 0.5) * 0.5 + 0.06))
          }
          const tier = widthTier(b)
          buckets[tier].moveTo(x, y)
          buckets[tier].lineTo(x, y + SEGMENT - GAP)
        }
      }
      ctx.strokeStyle = colors.line
      ctx.globalAlpha = 0.9
      ctx.lineCap = 'butt'
      for (let k = 0; k < TIER_COUNT; k++) {
        ctx.lineWidth = TIER_WIDTH[k]
        ctx.stroke(buckets[k])
      }
      ctx.globalAlpha = 1
      if (!reduced) {
        boxAlpha += ((mouse.inside ? 1 : 0) - boxAlpha) * Math.min(1, dt * 8)
        if (boxAlpha > 0.02) drawBoxes(box)
      }
      spent += performance.now() - started
      if (++frames === 60) {
        canvas.dataset.frameMs = (spent / frames).toFixed(2)
        frames = 0
        spent = 0
      }
    }

    const drawBoxes = (box: { x: number; y: number; s: number }) => {
      ctx.save()
      ctx.globalAlpha = boxAlpha
      ctx.strokeStyle = colors.accent
      ctx.fillStyle = colors.accent
      ctx.lineWidth = 1.2
      ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace'
      const cell = box.s / GRID
      const rects = regions.map((reg) => {
        const x = snapToGrid(box.x + reg.c * cell, BOX_SNAP)
        const y = snapToGrid(box.y + reg.r * cell, BOX_SNAP)
        const bw = snapToGrid(reg.w * cell, BOX_SNAP) || BOX_SNAP
        const bh = snapToGrid(reg.h * cell, BOX_SNAP) || BOX_SNAP
        return { x, y, w: bw, h: bh }
      })
      // 框之间的细线（中心相连）
      ctx.globalAlpha = boxAlpha * 0.5
      ctx.lineWidth = 0.6
      ctx.beginPath()
      rects.forEach((r, i) => {
        const cx = r.x + r.w / 2
        const cy = r.y + r.h / 2
        if (i === 0) ctx.moveTo(cx, cy)
        else ctx.lineTo(cx, cy)
      })
      ctx.stroke()
      ctx.globalAlpha = boxAlpha
      ctx.lineWidth = 1.2
      for (const r of rects) {
        ctx.strokeRect(r.x, r.y, r.w, r.h)
        // 角标
        const k = 4
        ctx.fillRect(r.x - 1.5, r.y - 1.5, k, 1.5)
        ctx.fillRect(r.x - 1.5, r.y - 1.5, 1.5, k)
        ctx.fillRect(r.x + r.w - k + 1.5, r.y + r.h, k, 1.5)
        ctx.fillRect(r.x + r.w, r.y + r.h - k + 1.5, 1.5, k)
        ctx.fillText(`${Math.round(r.x)},${Math.round(r.y)}`, r.x, Math.max(9, r.y - 4))
      }
      ctx.restore()
    }

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame)
      if (!t0) t0 = now
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016)
      last = now
      draw((now - t0) / 1000, dt)
    }
    const start = () => {
      if (reduced || raf) return
      last = 0
      raf = requestAnimationFrame(frame)
    }
    const stop = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    }
    const onVisibility = () => (document.hidden ? stop() : start())
    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      mouse.x = e.clientX - r.left
      mouse.y = e.clientY - r.top
      mouse.inside = true
    }
    const onLeave = () => {
      mouse.inside = false
    }

    resize()
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null
    ro?.observe(canvas)
    if (reduced) {
      draw(0, 0)
    } else {
      canvas.addEventListener('pointermove', onMove)
      canvas.addEventListener('pointerleave', onLeave)
      document.addEventListener('visibilitychange', onVisibility)
      start()
    }
    return () => {
      stop()
      ro?.disconnect()
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerleave', onLeave)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return <canvas ref={ref} aria-hidden data-brand-lines className={className} />
}
