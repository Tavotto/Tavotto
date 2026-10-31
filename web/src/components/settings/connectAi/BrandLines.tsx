import { useEffect, useRef } from 'react'
import { BRAND_MARK_BOX, BRAND_MARK_PATHS } from '@/components/ui/brandMark.geometry'
import {
  TIER_COUNT,
  brightestRegions,
  flowNoise,
  lineCount,
  logoBox,
  lumaAt,
  lumaFromRgba,
  repelTarget,
  smoothstep,
  snapToGrid,
  springStep,
  widthTier,
  type Region,
} from './brandLinesMath'

/** 亮度表分辨率（格） */
const GRID = 48
/** 每节长度 / 节间缝（CSS px）：缝要明显，才有竹节感 */
const SEGMENT = 10
const GAP = 4
/** 各档线宽（CSS px）：只有四档，整体偏细 */
const TIER_WIDTH = [0.6, 1.2, 2, 3] as const
/** 各档不透明度：暗处几乎不画，亮处才实 */
const TIER_ALPHA = [0.1, 0.4, 0.75, 1] as const
/** 鼠标「拨线」：作用半径 / 最大位移（px），竖向也按同一曲线衰减 */
const PUSH_RADIUS = 44
const PUSH_MAX = 12
const PUSH_VRADIUS = 90
/** 顶部给品牌名留的渐隐高度 */
const TOP_FADE = 64
/** 检测框吸附网格（CSS px） */
const BOX_SNAP = 8
const MAX_BOXES = 3
/** 检测框只在足够亮的区域出现 */
const BOX_MIN_SCORE = 0.45

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
    const regions: Region[] = brightestRegions(luma, GRID, GRID, MAX_BOXES, 8, 10, BOX_MIN_SCORE)

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
      lines = lineCount(w)
      pos = new Float32Array(lines)
      vel = new Float32Array(lines)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (reduced) draw(0, 0)
    }

    const draw = (t: number, dt: number) => {
      const started = performance.now()
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = colors.bg
      ctx.fillRect(0, 0, w, h)
      const box = logoBox(w, h)
      const spacing = w / lines
      const buckets: Path2D[] = Array.from({ length: TIER_COUNT }, () => new Path2D())
      const segs = Math.ceil(h / SEGMENT)
      for (let i = 0; i < lines; i++) {
        const x0 = (i + 0.5) * spacing
        if (!reduced) {
          const target = mouse.inside ? repelTarget(x0, mouse.x, PUSH_RADIUS, PUSH_MAX) : 0
          const [p, v] = springStep(pos[i], vel[i], target, dt)
          pos[i] = p
          vel[i] = v
        }
        // 没有鼠标时线也轻轻浮动
        const sway = reduced ? 0 : Math.sin(t * 0.8 + i * 0.7) * 0.8
        const u = (x0 - box.x) / box.s
        for (let j = 0; j < segs; j++) {
          const y = j * SEGMENT
          const yc = y + SEGMENT / 2
          const v = (yc - box.y) / box.s
          let b = lumaAt(luma, GRID, GRID, u, v)
          if (!reduced) {
            // 缓慢向下流动的噪声叠在亮度表上：标志处略有起伏，暗处只有极淡的流光
            b = Math.min(1, Math.max(0, b * 0.9 + (flowNoise(i * 0.35, j * 0.28 - t * 1.1, t) - 0.5) * 0.3))
          }
          const tier = widthTier(b)
          // 位移竖向也平滑衰减：线被拨开一个弧，而不是整条带推空
          const x = x0 + sway + pos[i] * smoothstep(1 - Math.abs(yc - mouse.y) / PUSH_VRADIUS)
          buckets[tier].moveTo(x, y)
          buckets[tier].lineTo(x, y + SEGMENT - GAP)
        }
      }
      ctx.lineCap = 'butt'
      for (let k = 0; k < TIER_COUNT; k++) {
        ctx.lineWidth = TIER_WIDTH[k]
        ctx.globalAlpha = TIER_ALPHA[k]
        ctx.strokeStyle = colors.line
        ctx.stroke(buckets[k])
        // 亮处带一点品牌蓝的调子
        if (k >= 2) {
          ctx.globalAlpha = k === 3 ? 0.3 : 0.2
          ctx.strokeStyle = colors.accent
          ctx.stroke(buckets[k])
        }
      }
      ctx.globalAlpha = 1
      // 顶部渐隐：左上角的品牌名要读得清
      const fade = ctx.createLinearGradient(0, 0, 0, TOP_FADE)
      fade.addColorStop(0, colors.bg)
      fade.addColorStop(1, 'transparent')
      ctx.fillStyle = fade
      ctx.fillRect(0, 0, w, TOP_FADE)
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
      ctx.strokeStyle = colors.accent
      ctx.fillStyle = colors.accent
      ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace'
      const cell = box.s / GRID
      const rects = regions.map((reg) => ({
        x: snapToGrid(box.x + reg.c * cell, BOX_SNAP),
        y: snapToGrid(box.y + reg.r * cell, BOX_SNAP),
        w: snapToGrid(reg.w * cell, BOX_SNAP) || BOX_SNAP,
        h: snapToGrid(reg.h * cell, BOX_SNAP) || BOX_SNAP,
      }))
      // 框之间的细线：细而半透明
      ctx.globalAlpha = boxAlpha * 0.35
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
      ctx.lineWidth = 1
      for (const r of rects) {
        ctx.strokeRect(r.x, r.y, r.w, r.h)
        const k = 5
        // 四角的短折角（在框外沿，不压框线）
        ctx.fillRect(r.x - 2, r.y - 2, k, 1.5)
        ctx.fillRect(r.x - 2, r.y - 2, 1.5, k)
        ctx.fillRect(r.x + r.w - k + 2, r.y + r.h + 0.5, k, 1.5)
        ctx.fillRect(r.x + r.w + 0.5, r.y + r.h - k + 2, 1.5, k)
        // 坐标标签放在框外上方（顶上放不下就放下方），带底色保证读得清
        const label = `${Math.round(r.x)},${Math.round(r.y)}`
        const tw = ctx.measureText(label).width
        const ly = r.y - 16 >= 0 ? r.y - 5 : r.y + r.h + 13
        ctx.fillStyle = colors.bg
        ctx.globalAlpha = boxAlpha * 0.85
        ctx.fillRect(r.x, ly - 9, tw + 6, 12)
        ctx.fillStyle = colors.accent
        ctx.globalAlpha = boxAlpha
        ctx.fillText(label, r.x + 3, ly)
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
