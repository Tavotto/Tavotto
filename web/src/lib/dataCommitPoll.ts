import type { AiDataCommitResult } from '@/lib/api'

/**
 * 同一个请求号重发 commit 时，如果后端原任务**还在处理中**，去重分支回的是当前快照：HTTP 200、`task.deduped === true`、`task.final === false`、
 * `task.outcome === null`，没有 `script`。这不是失败也不是画成：请求号必须留着，按有界的间隔重问**同一个**请求号，直到后端说 `final`。
 * 单份流程（`dataFigureStore.commit`）与批量（`dataBatchStore.drawOne` 的重试）共用这一套。
 */
export const commitPollConfig = {
  /** 两次重问之间隔多久 */
  intervalMs: 1500,
  /** 最多等多久（超时 = 仍未完成：调用方标失败，但请求号仍保留，下次重试继续问同一个） */
  maxMs: 60_000,
}

export function isCommitInProgress(res: AiDataCommitResult): boolean {
  return res.task?.deduped === true && res.task?.final === false
}

/**
 * 重问同一个请求号直到 `final`：返回最终结果；超时返回 null；`still()` 为假（换了项目 / 清了 store）返回 null、不再发请求。
 * `resend` 抛错就原样抛给调用方（调用方按 `isDefiniteRunFailure` 决定要不要保留请求号）。
 */
export async function pollCommit(resend: () => Promise<AiDataCommitResult>, still: () => boolean): Promise<AiDataCommitResult | null> {
  const t0 = Date.now()
  for (;;) {
    await new Promise((r) => setTimeout(r, commitPollConfig.intervalMs))
    if (!still()) return null
    const res = await resend()
    if (!still()) return null
    if (!isCommitInProgress(res)) return res
    if (Date.now() - t0 >= commitPollConfig.maxMs) return null
  }
}
