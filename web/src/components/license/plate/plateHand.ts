// 铭牌的「手」：往哪指、怎么拖、怎么抛、怎么停（移植自官网 `foundingPlate.ts` 的 `hand`，行为一致）。
// 拖一下带惯性翻面、停在正面或背面；指针移过去牌会微微朝它倾；方向键也能翻；手机倾斜时跟着倾。
// 与官网的差别只有一处：返回 `dispose`，因为应用里铭牌会随设置页开合（官网的牌与页面同生共死）。

export type Apply = (spin: number, tiltX: number, tiltY: number) => void

export const reducedMotion = (): boolean =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export interface Hand {
  /** 画面尺寸变了 / 外部要重画一帧 */
  wake: () => void
  dispose: () => void
}

export function attachHand(root: HTMLElement, apply: { current: Apply }): Hand {
  const ac = new AbortController()
  const { signal } = ac
  let spin = 0
  let vel = 0
  let tiltX = 0
  let tiltY = 0
  let goalX = 0
  let goalY = 0
  let target = 0
  let drag = false
  let lastX = 0
  let busy = false
  let raf = 0
  const frame = () => {
    raf = 0
    if (!drag) {
      vel = (vel + (target - spin) * 0.02) * 0.86
      spin += vel
    }
    tiltX += (goalX - tiltX) * 0.12
    tiltY += (goalY - tiltY) * 0.12
    apply.current(spin, tiltX, tiltY)
    busy =
      drag ||
      Math.abs(vel) > 0.0004 ||
      Math.abs(target - spin) > 0.002 ||
      Math.abs(goalX - tiltX) > 0.002 ||
      Math.abs(goalY - tiltY) > 0.002
    if (busy) raf = requestAnimationFrame(frame)
  }
  const wake = () => {
    if (!busy) {
      busy = true
      raf = requestAnimationFrame(frame)
    }
  }
  const lean = (nx: number, ny: number) => {
    if (reducedMotion()) return
    goalY = (nx - 0.5) * 0.36
    goalX = (ny - 0.5) * 0.26
    wake()
  }
  root.addEventListener(
    'pointerdown',
    (e) => {
      drag = true
      lastX = e.clientX
      vel = 0
      root.setPointerCapture(e.pointerId)
      wake()
    },
    { signal },
  )
  root.addEventListener(
    'pointermove',
    (e) => {
      const r = root.getBoundingClientRect()
      const nx = (e.clientX - r.left) / r.width
      const ny = (e.clientY - r.top) / r.height
      if (drag) {
        const step = (e.clientX - lastX) * 0.012
        lastX = e.clientX
        spin += step
        vel = step
        target = spin
      }
      if (!reducedMotion()) {
        root.style.setProperty('--mx', `${(nx * 100).toFixed(1)}%`)
        root.style.setProperty('--my', `${(ny * 100).toFixed(1)}%`)
      }
      lean(nx, ny)
    },
    { signal },
  )
  const release = () => {
    if (!drag) return
    drag = false
    target = Math.round((spin + vel * 9) / Math.PI) * Math.PI
    if (reducedMotion()) {
      spin = target
      vel = 0
    }
    wake()
  }
  root.addEventListener('pointerup', release, { signal })
  root.addEventListener('pointercancel', release, { signal })
  root.addEventListener('pointerleave', () => !drag && lean(0.5, 0.5), {
    signal,
  })
  root.addEventListener(
    'keydown',
    (e) => {
      const k =
        e.key === 'ArrowRight' || e.key === 'ArrowDown'
          ? 1
          : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
            ? -1
            : 0
      if (!k) return
      e.preventDefault()
      target = Math.round(target / Math.PI) * Math.PI + k * Math.PI
      if (reducedMotion()) {
        spin = target
        vel = 0
      }
      wake()
    },
    { signal },
  )
  const orient = (e: DeviceOrientationEvent) => {
    if (e.gamma == null || e.beta == null) return
    lean(
      0.5 + Math.max(-30, Math.min(30, e.gamma)) / 80,
      0.5 + Math.max(-30, Math.min(30, e.beta - 40)) / 80,
    )
  }
  addEventListener('deviceorientation', orient, { signal })
  root.addEventListener(
    'pointerdown',
    () => {
      const ask = (
        globalThis as {
          DeviceOrientationEvent?: {
            requestPermission?: () => Promise<string>
          }
        }
      ).DeviceOrientationEvent?.requestPermission
      if (typeof ask === 'function')
        void ask().then(
          (r) => r === 'granted' && addEventListener('deviceorientation', orient, { signal }),
        )
    },
    { once: true, signal },
  )
  return {
    wake,
    dispose: () => {
      ac.abort()
      if (raf) cancelAnimationFrame(raf)
      busy = false
    },
  }
}
