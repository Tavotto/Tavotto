import { useDocumentStore } from '@/store/documentStore'
import { setMomentSink, takeCheckpoint, markMoment } from '@/lib/timelineCheckpoint'
import { onLayoutSaved } from '@/lib/layoutSaved'

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

/**
 * 「打开项目」这一刻（ADR 0101 §3；Codex #679 P2）。
 *
 * 由编辑器壳（`App.tsx` 的 Workspace）在**文档恢复完之后**调：从 Project Picker 打开、
 * 启动时恢复上次的项目、从 Picker 回到当前项目，这三条路都要先挂上 Workspace——而
 * `projectStore.adoptNow` 里那一次 `markMoment('open')` 发生在 Workspace 挂上之前，
 * 时间线还没在跑，那一下是空的。`adoptNow` 那一处只负责「编辑器开着时直接切到另一个
 * 项目」（Workspace 不重挂）；两处互不重叠：Picker 上 Workspace 一定没挂着。
 */
export function markWorkspaceOpened(): Promise<unknown> {
  return markMoment('open')
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

  // 「排版写进了文件」→「保存」时刻。订阅而不是让保存侧调时间线：写文件的路不止一条、
  // 还在变（#674），见 `lib/layoutSaved.ts`
  // 点打给发起保存时的那一份排版：完成时已经换走了就不打（`markMoment` 的 ctx）
  const unsubSaved = onLayoutSaved((_via, { ctx }) => void markMoment('save', ctx))

  const unsub = useDocumentStore.subscribe((state, prev) => {
    if (state.doc === prev.doc) return
    window.clearTimeout(timer)
    timer = window.setTimeout(fire, debounceMs)
  })

  return () => {
    window.clearTimeout(timer)
    setMomentSink(null)
    unsubSaved()
    unsub()
  }
}
