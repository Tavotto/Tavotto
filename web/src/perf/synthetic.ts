/**
 * 标准拖动测试（ADR 0075）：在用户点过的那个位置，用合成的指针事件把
 * 「按下 → 连续移动 → 取消」完整走一遍。
 *
 * 为什么是合成事件而不是直接调 `startMoveDrag`：命中测试、PanelView 的
 * 边框命中区、ElementHitLayer 的分流都在 pointerdown 的处理链上，绕过它们
 * 量出来的就不是用户的那条路。事件从按下点命中的元素派发、冒泡到 window，
 * `trackPointer` 在 window 上收——与真实拖动同一条链。
 *
 * **收尾一律是 `pointercancel`**：取消语义保证不写 override、不进历史、
 * 不渲染、不留临时 transform（`TrackEnd.cancelled`，见 fake-realtime-preview
 * 细则）。测试在用户真实的文档上跑，绝不许改动它。
 *
 * 两轮，同一条轨迹：`m1` 每帧 1 个 pointermove（60Hz 鼠标），`m3` 每帧 3 个
 * （120Hz 触控板 / 高回报率鼠标在 60Hz 刷新下的样子）。两轮差得多 = 每个
 * pointermove 的处理成本在主导，合并到每帧一次就能省下来。
 */
import { useInteractionStore } from '@/store/interactionStore'
import { perfNow, perfSetSource } from './core'

export interface SyntheticPass {
  label: string
  movesPerFrame: number
  durationMs: number
}

export const STANDARD_PASSES: readonly SyntheticPass[] = [
  { label: 'm1', movesPerFrame: 1, durationMs: 3000 },
  { label: 'm3', movesPerFrame: 3, durationMs: 3000 },
]

/** 轨迹半径（屏幕 px）：足够跨过吸附容差、又不至于把对象拖出视口 */
const RADIUS = 48
/** 8 字形一圈的周期 */
const PERIOD_MS = 1500

export type SyntheticResult = 'ok' | 'no_target' | 'not_draggable' | 'aborted'

const nextFrame = () => new Promise<number>((r) => requestAnimationFrame(r))
/** 渲染结束之后的第一个任务：真实输入就落在两帧之间，而不是 rAF 里 */
const afterRender = () =>
  new Promise<void>((resolve) => {
    const ch = new MessageChannel()
    ch.port1.onmessage = () => {
      ch.port1.close()
      resolve()
    }
    ch.port2.postMessage(0)
  })
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function pointer(type: string, x: number, y: number, buttons: number): PointerEvent {
  const init: PointerEventInit = {
    bubbles: true,
    cancelable: true,
    composed: true,
    clientX: x,
    clientY: y,
    screenX: x,
    screenY: y,
    pointerId: 1,
    pointerType: 'mouse',
    isPrimary: true,
    button: type === 'pointermove' ? -1 : 0,
    buttons,
    width: 1,
    height: 1,
    pressure: buttons ? 0.5 : 0,
  }
  if (typeof PointerEvent === 'function') return new PointerEvent(type, init)
  // 没有 PointerEvent 构造器的环境（jsdom）：同名 MouseEvent，监听器按事件名派发
  const ev = new MouseEvent(type, init)
  for (const k of ['pointerId', 'pointerType', 'isPrimary'] as const) {
    Object.defineProperty(ev, k, { value: init[k] })
  }
  return ev as PointerEvent
}

/** 8 字形轨迹：先用 150ms 把半径从 0 拉满，越过 trackPointer 的起拖阈值时不跳 */
function pathAt(t: number): [number, number] {
  const ramp = Math.min(1, t / 150)
  const a = (2 * Math.PI * t) / PERIOD_MS
  return [RADIUS * ramp * Math.sin(a), (RADIUS / 2) * ramp * Math.sin(2 * a)]
}

let abortFlag = false

export function abortSynthetic(): void {
  abortFlag = true
}

/** 跑一轮。调用方负责先 `startProbe()`；这里只负责「像用户一样拖」 */
export async function runSyntheticPass(x: number, y: number, pass: SyntheticPass): Promise<SyntheticResult> {
  const target = document.elementFromPoint(x, y)
  if (!target) return 'no_target'
  abortFlag = false
  perfSetSource('synthetic', pass.label)
  try {
    target.dispatchEvent(pointer('pointerdown', x, y, 1))
    // 起拖是同步的（startXDrag 里 interaction().begin），但第一个片段要等越过
    // 阈值才有意义——这里只看按下有没有被任何拖动接住
    const t0 = perfNow()
    let lastX = x
    let lastY = y
    while (perfNow() - t0 < pass.durationMs) {
      if (abortFlag) break
      await nextFrame()
      await afterRender()
      const t = perfNow() - t0
      for (let k = 1; k <= pass.movesPerFrame; k++) {
        // 一帧里的几个事件沿轨迹均匀插值，像真实的高回报率输入
        const tk = t - ((pass.movesPerFrame - k) * 16.7) / pass.movesPerFrame
        const [dx, dy] = pathAt(Math.max(0, tk))
        lastX = x + dx
        lastY = y + dy
        target.dispatchEvent(pointer('pointermove', lastX, lastY, 1))
      }
      if (t < 200 && useInteractionStore.getState().kind === 'none') {
        // 按下 + 200ms 的移动都没有接住任何拖动：这个点上没有可拖的东西
        target.dispatchEvent(pointer('pointercancel', lastX, lastY, 0))
        return 'not_draggable'
      }
    }
    target.dispatchEvent(pointer('pointercancel', lastX, lastY, 0))
    return abortFlag ? 'aborted' : 'ok'
  } finally {
    perfSetSource('user')
  }
}

export async function runStandardTest(
  x: number,
  y: number,
  onPass?: (pass: SyntheticPass, index: number) => void,
): Promise<SyntheticResult> {
  for (const [i, pass] of STANDARD_PASSES.entries()) {
    onPass?.(pass, i)
    const r = await runSyntheticPass(x, y, pass)
    if (r !== 'ok') return r
    // 让上一轮的收尾（取消 + 尾巴采样）落完，再开下一轮
    await sleep(800)
  }
  return 'ok'
}
