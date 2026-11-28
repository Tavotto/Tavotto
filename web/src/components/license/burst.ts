import { tween } from '@/lib/motion'

/**
 * 激活成功的一小捧礼花（维护者 2026-11-28 定，宪法第七节登记豁免）：从 `origin` 的中心往上撒一次，约 1 秒落完。
 *
 * - 手写 canvas，不加动效依赖；动画只经 `lib/motion.tween`——减弱动效时它同步落到终态，这里就一颗粒子都不画。
 * - 画布铺满窗口（`fixed inset-0`，不被对话框边裁）、`z-toast`、不接事件、`aria-hidden`，播完自己移除。
 * - 颜色只读 token（Pro 的钛色三阶 + ink-faint），在调用那一刻从 `<html>` 的计算样式取，换主题天然跟上。
 * - 只在「这一次 activate() 返回成功」时由调用方叫；载入已激活、刷新、续费都不叫。
 */
export const BURST_MS = 950
const COUNT = 34
const COLORS = ['--color-pro-ti-light', '--color-pro-ti-dark', '--color-pro-ti-edge', '--color-ink-faint']

export function burstFrom(origin: Element): () => void {
  if (typeof document === 'undefined') return () => {}
  const canvas = document.createElement('canvas')
  canvas.className = 'pointer-events-none fixed inset-0 z-toast'
  canvas.setAttribute('aria-hidden', 'true')
  canvas.dataset.licenseBurst = ''
  const w = window.innerWidth
  const h = window.innerHeight
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  const g = canvas.getContext('2d')
  if (!g) return () => {}
  document.body.appendChild(canvas)
  g.scale(dpr, dpr)

  const css = getComputedStyle(document.documentElement)
  const palette = COLORS.map((n) => css.getPropertyValue(n).trim()).filter(Boolean)
  const box = origin.getBoundingClientRect()
  const ox = box.left + box.width / 2
  const oy = box.top + box.height / 2
  const parts = Array.from({ length: COUNT }, (_, i) => {
    const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.5
    const sp = 2.4 + Math.random() * 2.8
    return {
      x: ox + (Math.random() - 0.5) * box.width * 0.6,
      y: oy + (Math.random() - 0.5) * 10,
      vx: Math.cos(a) * sp * 1.15,
      vy: Math.sin(a) * sp * 1.1,
      w: 1.8 + Math.random() * 2,
      h: Math.random() < 0.5 ? 2 + Math.random() * 1.5 : 4 + Math.random() * 2.5,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.35,
      color: palette[i % palette.length] ?? 'currentColor',
      round: Math.random() < 0.35,
    }
  })

  // 按帧积分（每帧一步），时间轴只决定淡出与何时收场
  let last = 0
  const frame = (k: number) => {
    const steps = Math.max(1, Math.round((k - last) * (BURST_MS / 16.7)))
    last = k
    g.clearRect(0, 0, w, h)
    g.globalAlpha = Math.max(0, 1 - k ** 2.2)
    for (const p of parts) {
      for (let s = 0; s < steps; s++) {
        p.vx *= 0.955
        p.vy = p.vy * 0.955 + 0.13
        p.x += p.vx
        p.y += p.vy
        p.rot += p.vr
      }
      g.fillStyle = p.color
      g.save()
      g.translate(p.x, p.y)
      g.rotate(p.rot)
      if (p.round) {
        g.beginPath()
        g.arc(0, 0, p.w / 2 + 0.5, 0, Math.PI * 2)
        g.fill()
      } else {
        g.scale(1, Math.cos(p.rot * 2))
        g.fillRect(-p.w / 2, -p.h / 2, p.w, p.h)
      }
      g.restore()
    }
  }
  let started = false
  const cancel = tween({
    duration: BURST_MS,
    ease: (t) => t,
    onUpdate: (k) => {
      // 减弱动效时 tween 同步给 1：不画任何一帧
      if (!started && k >= 1) return
      started = true
      frame(k)
    },
    onDone: () => canvas.remove(),
  })
  return () => {
    cancel()
    canvas.remove()
  }
}
