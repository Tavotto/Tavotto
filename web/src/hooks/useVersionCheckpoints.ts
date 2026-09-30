import { useDocumentStore } from '@/store/documentStore'
import { setMomentSink, takeCheckpoint, markMoment } from '@/lib/timelineCheckpoint'
import { onLayoutSaved } from '@/lib/layoutSaved'
import { currentTimelineCtx } from '@/lib/timelineContext'
import { currentProjectId } from '@/lib/session'
import { useTimelineStore } from '@/store/timelineStore'

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

/**
 * 等 `ready`（启动时素材清单与文档恢复都到齐）之后再打「打开项目」点——**只打给启动时那个
 * 项目**（Codex #679）：`ready` 回来之前用户就切到了 B 的话，`adoptNow` 已经给 B 打过自己的
 * 「打开」，这里再打就是 B 里多出来的一个。发起时记下项目代际与 pj，回来一核，对不上就丢；
 * 返回的函数（Workspace 卸载时调）直接作废这一次。
 */
export function markWorkspaceOpenedAfter(ready: Promise<unknown>): () => void {
  const gen = useTimelineStore.getState().gen
  const pj = currentProjectId()
  let cancelled = false
  void ready
    .then(() => {
      if (cancelled || useTimelineStore.getState().gen !== gen || currentProjectId() !== pj) return
      return markWorkspaceOpened()
    })
    .catch(() => undefined)
  return () => {
    cancelled = true
  }
}

export function startVersionCheckpoints(): () => void {
  const { debounceMs, minGapMs } = timing()
  let timer: number | undefined
  // 上一个节点的时刻**按时间线上下文记**（项目代际 + 排版 id，Codex #679）：间隔说的是
  // 「这份排版的时间线上别挤得太密」。共用一个时间戳的话，在 A 里保存完切到 B，B 第一次
  // 停顿满 15 s 也要等 A 的 2 分钟走完，B 的时间线平白缺一段
  const lastSaved = new Map<string, number>()
  /** 自动节点在路上的上下文：回来之前不发第二个（间隔只在写成之后才记） */
  const inFlight = new Set<string>()

  const fire = () => {
    const ctx = currentTimelineCtx()
    const wait = (lastSaved.get(ctx) ?? 0) + minGapMs - Date.now()
    if (wait > 0) {
      timer = window.setTimeout(fire, wait)
      return
    }
    if (!useDocumentStore.getState().doc.objects.length) return
    if (inFlight.has(ctx)) return // 这份排版上一个自动节点还在路上：它回来之前不再发第二个
    inFlight.add(ctx)
    void takeCheckpoint({ auto: true })
      .then((res) => {
        // **写成了才重新计间隔**（Codex #679）：请求没成（网络 / 磁盘的一时错误）就不动，
        // 下一次停顿满 15 s 照常重试——先记上的话，一次失败换来 2 分钟的空白。
        // 服务端判重（内容与上一个节点相同，回 `skipped` 并带回那个节点）也算：这一刻的内容
        // 已经在时间线上了
        if (res?.version) lastSaved.set(ctx, Date.now())
      })
      .catch(() => {
        /* 自动节点失败不打扰编辑；下一轮改动会再试 */
      })
      .finally(() => inFlight.delete(ctx))
  }

  // 关键时刻：导出 / 写回 / 保存 / 打开 / 关闭 / 恢复前。**不去重**（服务器端也不），
  // 打完点重新计间隔——刚打过关键时刻，紧接着再拍一个自动节点只是重复
  // 关键时刻**空画布也打**（Codex #679）：「清空之后保存」「打开 / 离开一个空项目」都是发生过的
  // 事，与命名节点、恢复前一样不因为画布空着就静默缺席；只有普通自动节点在空画布上不拍。
  // 「空」按节点实际拍的那份判——带快照时是快照里那份（`takeCheckpoint` 里同一个 `id.doc`）
  setMomentSink(async (moment, snapshot) => {
    // 点打在哪份排版的时间线上，就重新计哪份的间隔（发起那一刻取，await 回来可能已经换了）
    const ctx = snapshot?.ctx ?? currentTimelineCtx()
    const res = await takeCheckpoint({ auto: true, moment, allowEmpty: true }, snapshot)
    if (res?.version) lastSaved.set(ctx, Date.now())
    return res
  })

  // 「排版写进了文件」→「保存」时刻。订阅而不是让保存侧调时间线：写文件的路不止一条、
  // 还在变（#674），见 `lib/layoutSaved.ts`
  // 点打给发起保存时的那一份排版：完成时已经换走了就不打（`markMoment` 的 ctx）
  const unsubSaved = onLayoutSaved((_via, { moment }) => void markMoment('save', moment))

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
