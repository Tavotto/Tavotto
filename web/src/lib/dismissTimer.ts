/**
 * 会让路的自动收起计时器——通知轨上两个「到时自己走」的来源共用（`uiStore.setStatus` 的状态、
 * `onboarding/hints` 的操作提示）。
 *
 * 此前两处各是一只裸 `setTimeout`：用户正伸手去点 toast 上的 ×、或者读到一半，它在指针底下消失；
 * 切去别的 app 再回来，状态早就走了，什么都看不到。改成：
 *
 *   - **有人按着就不走表**（`hold(reason)` / `release(reason)`）：指针停在 toast 上、焦点在它的
 *     按钮上，各是一个 reason；同一个 reason 按两次算一次。
 *   - **页面不可见时不走表**（`document.visibilityState === 'hidden'`）：切走的那几秒不算数，回来
 *     从剩下的时间接着数——而不是把后台过去的时间一次落下。
 *   - 剩余时间按停表那一刻结算，续表只排剩下的那截；同一只计时器 `start` 时**上一次的 hold 还算数**
 *     （指针停在 toast 上时新状态顶掉旧状态，DOM 没换、pointerleave 不会再来一次）。
 *
 * 这不是动画，所以不走 `lib/motion.tween`（那条在 reduced-motion 下直接落终态——计时器要的恰恰
 * 是「等」）；也不用 rAF：rAF 在后台标签页本来就停，但那是「不触发」不是「暂停」，回来时会一次跳到头。
 * WCAG 2.2.1「时限可调」顺手满足：hover / focus 即延长。
 */

export interface DismissTimer {
  /** 开始（或重新开始）倒计时；到时调 `fire`。已经在跑的先作废 */
  start(ms: number, fire: () => void): void
  /** 作废：不再触发。hold 的账本保留——它记的是「谁按着」，不是这一轮的状态 */
  cancel(): void
  /** 某个原因按住了：不走表。同一个 reason 重复 hold 只算一次 */
  hold(reason: string): void
  release(reason: string): void
  /** 诊断 / 测试：当前是否在走表（有活动的倒计时、没人按着、页面可见） */
  readonly running: boolean
}

const now = () => Date.now()

function pageHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

export function createDismissTimer(): DismissTimer {
  const holds = new Set<string>()
  let fire: (() => void) | null = null
  let remaining = 0
  let deadline = 0
  let handle: ReturnType<typeof setTimeout> | undefined
  let listening = false

  const shouldRun = () => fire !== null && holds.size === 0 && !pageHidden()

  const stopClock = () => {
    if (handle === undefined) return
    clearTimeout(handle)
    handle = undefined
    remaining = Math.max(0, deadline - now())
  }

  const sync = () => {
    if (shouldRun()) {
      if (handle !== undefined) return
      deadline = now() + remaining
      handle = setTimeout(() => {
        handle = undefined
        const f = fire
        fire = null
        f?.()
      }, remaining)
    } else {
      stopClock()
    }
  }

  const listen = () => {
    if (listening || typeof document === 'undefined') return
    listening = true
    document.addEventListener('visibilitychange', sync)
  }

  return {
    start(ms, f) {
      stopClock()
      fire = f
      remaining = ms
      listen()
      sync()
    },
    cancel() {
      stopClock()
      fire = null
    },
    hold(reason) {
      holds.add(reason)
      sync()
    },
    release(reason) {
      holds.delete(reason)
      sync()
    },
    get running() {
      return handle !== undefined
    },
  }
}
