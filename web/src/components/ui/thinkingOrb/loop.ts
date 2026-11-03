/**
 * 思考小球的共享循环（ADR 0165）：所有小球一个时钟、一个 rAF；滚出视口的不画；宿主停发 rAF 时定时器兜底；
 * 减少动态效果时画一帧静止的、不进循环。
 */
import { prefersReducedMotion } from '@/lib/motion'
import { MODE_FRAMES } from './engine/registry'
import { paintOrbFrame } from './paint'
import { BASE_SPEED, REST_PHASE, resolvePreset, SETTLED_SPEED, type OrbForm, type OrbSize } from './presets'


export interface Orb {
  canvas: HTMLCanvasElement
  ctx: CanvasRenderingContext2D
  form: OrbForm
  size: OrbSize
  dpr: number
  speed: number
  phase: number
  ink: string
  visible: boolean
}

const live = new Set<Orb>()
let raf = 0
let timer: ReturnType<typeof setInterval> | null = null
let lastFrame = 0
let observer: IntersectionObserver | null = null

export const now = () => performance.now() / 1000

/** 墨色 = canvas 自己的计算后 `color`（`text-ink` / `text-ink-3` 的 token 值） */
export function readInk(canvas: HTMLCanvasElement): string {
  return getComputedStyle(canvas).color || 'currentColor'
}

export function createOrb(canvas: HTMLCanvasElement, form: OrbForm, size: OrbSize, tone: 'live' | 'settled'): Orb | null {
  const dpr = Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1)
  canvas.width = Math.round(size * dpr)
  canvas.height = Math.round(size * dpr)
  let ctx: CanvasRenderingContext2D | null = null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    ctx = null
  }
  if (!ctx) return null
  const o: Orb = {
    canvas,
    ctx,
    form,
    size,
    dpr,
    speed: BASE_SPEED * (tone === 'settled' ? SETTLED_SPEED : 1),
    phase: REST_PHASE[form],
    ink: readInk(canvas),
    visible: true,
  }
  if (prefersReducedMotion()) {
    // 减少动态效果：一帧静止的、有代表性的姿态，不进循环
    drawOrb(o, null)
    return o
  }
  live.add(o)
  observe(o)
  drawOrb(o, now())
  ensureLoop()
  return o
}

export function disposeOrb(o: Orb) {
  live.delete(o)
  observer?.unobserve(o.canvas)
  if (live.size === 0) stopLoop()
}

/** clock = null：静止帧（停在 REST_PHASE） */
export function drawOrb(o: Orb, clock: number | null) {
  const p = resolvePreset(o.form, o.size)
  const t = o.phase + (clock == null || !live.has(o) ? 0 : clock * p.speed * o.speed)
  const frame = MODE_FRAMES[p.mode](o.size, t, p.opts)
  o.ctx.setTransform(o.dpr, 0, 0, o.dpr, 0, 0)
  o.ctx.clearRect(0, 0, o.size, o.size)
  paintOrbFrame(o.ctx, frame, o.ink, o.size, o.dpr)
}

function step() {
  lastFrame = performance.now()
  const clock = now()
  for (const o of live) if (o.visible) drawOrb(o, clock)
}

function ensureLoop() {
  if (raf || typeof requestAnimationFrame === 'undefined') return
  const tick = () => {
    step()
    raf = live.size ? requestAnimationFrame(tick) : 0
  }
  raf = requestAnimationFrame(tick)
  // 宿主停发 rAF（标签页没隐藏、却不给帧）时由定时器接着画；标签页隐藏时两者都不画
  timer ??= setInterval(() => {
    if (document.visibilityState === 'visible' && performance.now() - lastFrame > 250) step()
  }, 100)
}

function stopLoop() {
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  if (timer) clearInterval(timer)
  timer = null
}

function observe(o: Orb) {
  if (typeof IntersectionObserver === 'undefined') return
  observer ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      for (const x of live) if (x.canvas === e.target) x.visible = e.isIntersecting
    }
  })
  observer.observe(o.canvas)
}

/** 测试用：停掉共享循环与观察器（`test/setup.ts` 的全局 afterEach 调，L-087） */
export function __stopThinkingOrbsForTests() {
  live.clear()
  stopLoop()
  observer?.disconnect()
  observer = null
}
