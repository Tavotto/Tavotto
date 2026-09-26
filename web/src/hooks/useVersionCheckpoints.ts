import { useDocumentStore } from '@/store/documentStore'
import { setMomentSink, takeCheckpoint } from '@/lib/timelineCheckpoint'

/**
 * 排版时间线的自动节点：编辑停顿后落一个服务器快照；关键时刻另打点（ADR 0101）。
 *
 * 与本机自动保存（localStorage，1s 防抖）互不替代：本机保存兜「刷新不丢」，
 * 时间线兜「改乱了想回到刚才」。停顿 15s 且距上一个节点 ≥2 分钟才拍（ADR 0101
 * 从 5 分钟调密，实测数字在 ADR 里），服务器端还会去重（与最近一版相同则跳过）
 * 并滚动清理（命名节点永不清理）。
 */
export const DEBOUNCE_MS = 15_000
export const MIN_GAP_MS = 2 * 60_000

/**
 * 真浏览器 e2e 的时序注入：`page.addInitScript` 往 window 上放
 * `__TAVOTTO_TIMELINE_TIMING__ = { debounceMs, minGapMs }`，**不改产品默认值**。
 * 只认正的有限数、下限 100 ms；形状不对就当没给。
 */
function timing(): { debounceMs: number; minGapMs: number } {
  const raw =
    typeof window === 'undefined'
      ? undefined
      : (window as unknown as { __TAVOTTO_TIMELINE_TIMING__?: unknown }).__TAVOTTO_TIMELINE_TIMING__
  const pick = (v: unknown, dflt: number) =>
    typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.max(100, v) : dflt
  const o = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return { debounceMs: pick(o.debounceMs, DEBOUNCE_MS), minGapMs: pick(o.minGapMs, MIN_GAP_MS) }
}

export function startVersionCheckpoints(): () => void {
  const { debounceMs, minGapMs } = timing()
  let timer: number | undefined
  let lastSaved = 0

  const fire = () => {
    const wait = lastSaved + minGapMs - Date.now()
    if (wait > 0) {
      timer = window.setTimeout(fire, wait)
      return
    }
    if (!useDocumentStore.getState().doc.objects.length) return
    lastSaved = Date.now()
    void takeCheckpoint({ auto: true }).catch(() => {
      /* 自动节点失败不打扰编辑；下一轮改动会再试 */
    })
  }

  // 关键时刻：导出 / 写回 / 保存 / 打开 / 关闭 / 恢复前。**不去重**（服务器端也不），
  // 打完点重新计间隔——刚打过关键时刻，紧接着再拍一个自动节点只是重复
  setMomentSink(async (moment) => {
    const res = await takeCheckpoint({ auto: true, moment })
    if (res?.version) lastSaved = Date.now()
    return res
  })

  const unsub = useDocumentStore.subscribe((state, prev) => {
    if (state.doc === prev.doc) return
    window.clearTimeout(timer)
    timer = window.setTimeout(fire, debounceMs)
  })

  return () => {
    window.clearTimeout(timer)
    setMomentSink(null)
    unsub()
  }
}
