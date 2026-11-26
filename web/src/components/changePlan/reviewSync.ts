/**
 * 方案应用之后的审阅收尾（ADR 0206 §六）：
 * - 有生效改动的那几组对应的意见自动标「已处理」（走 `reviewStore.setResolved`，即 `/api/review/.../resolve`），撤销应用时**只还原这次标的那几条**；
 * - 每条被处理的意见下有一份回复草稿（前端确定性生成，`lib/changePlanReplyDraft`），用户改过、点「发送」才发。
 *
 * 失败不自动重试（免得反复打后端）：条目上记一个 `resolveFailed`，审阅页签给一句灰字 + 「重试」。
 */
import { useEffect, useMemo } from 'react'
import { resolveReviewComment, type ChangePlanChange, type ReviewShareDetail } from '@/lib/api'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { draftReply } from '@/lib/changePlanReplyDraft'
import { effectiveStatus } from '@/lib/changePlanApply'
import { engineLabel } from '../inspector/roles/registry'
import type { PlanSource } from '@/lib/changePlanApply'
import { panelOf, setChangePlanLabeler, sourceOf, useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useReviewStore } from '@/store/reviewStore'

setChangePlanLabeler(engineLabel)

/** 这份方案里「有生效改动、被勾选」的组对应的意见 id（去重） */
export function resolvableIds(e: Pick<PlanEntry, 'plan' | 'unchecked' | 'request'>): string[] {
  // 审阅转出来的脚本方案卡（ADR 0206 修订 2026-11-26）：没有图内分组，转给脚本的那几条意见整体算这张卡处理的（应用成功才标，撤销还原）
  if (e.plan?.kind === 'script') return [...new Set(e.request.scriptFrom?.comment_ids ?? [])]
  const ids = (e.plan?.groups ?? []).filter((g) => !e.unchecked[g.key] && g.comment_id && g.changes.length > 0).map((g) => g.comment_id!)
  return [...new Set(ids)]
}

/** 这条意见在这份方案里被改了什么（勾选的组） */
export function changesOf(e: Pick<PlanEntry, 'plan' | 'unchecked'>, commentId: string): ChangePlanChange[] {
  return (e.plan?.groups ?? []).filter((g) => g.comment_id === commentId && !e.unchecked[g.key]).flatMap((g) => g.changes)
}

export type SyncTodo = { kind: 'mark' | 'restore'; ids: string[] }

/**
 * 这张卡此刻还欠审阅页签什么：已应用 → 标那些还没处理的意见；撤销回「待确认」→ 还原这张卡标过的。
 * 只在同一份分享记录已读到详情时动手；上次没成（`resolveFailed`）等用户点「重试」。
 */
export function pendingSync(e: PlanEntry, current: readonly unknown[] | null, detail: ReviewShareDetail | null, source?: PlanSource | null): SyncTodo | null {
  if (!e.plan || e.resolveFailed || !detail || !e.request.share_id || detail.share_id !== e.request.share_id) return null
  const st = effectiveStatus(e, current, source)
  if (st === 'applied') {
    const ids = resolvableIds(e).filter((id) => !e.resolvedIds?.includes(id) && detail.comments.some((c) => c.id === id && !c.resolved))
    return ids.length ? { kind: 'mark', ids } : null
  }
  // 撤销回到「待确认」才还原；应用之后图又被改成别的样子（stale）时，意见处理过这件事不变
  if (st === 'ready') {
    const ids = (e.resolvedIds ?? []).filter((id) => detail.comments.some((c) => c.id === id && c.resolved))
    return ids.length ? { kind: 'restore', ids } : null
  }
  return null
}

const inflight = new Set<string>()

async function runSync(key: string, todo: SyncTodo): Promise<void> {
  if (inflight.has(key)) return
  const pinned = useChangePlanStore.getState().entries[key]?.request.share_id
  if (!pinned) return
  inflight.add(key)
  // 发起时钉住：项目代际、分享记录 id、要处理的意见 id。等待中切图 / 切项目，请求仍发往原分享，结果按这张卡记回；项目换了就停（不写别的项目）
  const guard = captureProjectEpoch()
  try {
    const want = todo.kind === 'mark'
    const done: string[] = []
    let failed = false
    for (const id of todo.ids) {
      if (!guard.still()) return
      try {
        const r = await resolveReviewComment(pinned, id, want)
        if (!guard.still()) return
        if (r.ok) {
          done.push(id)
          // 页签此刻显示的还是这份分享才同步界面；别的分享的详情不碰
          const rs = useReviewStore.getState()
          if (rs.share?.share_id === pinned) {
            useReviewStore.setState((s) => (s.detail?.share_id === pinned ? { detail: { ...s.detail, comments: s.detail.comments.map((x) => (x.id === id ? r.comment : x)) } } : {}))
            void rs.reloadShare()
          }
        } else failed = true
      } catch {
        if (!guard.still()) return
        failed = true
      }
    }
    const st = useChangePlanStore.getState()
    const e = st.entries[key]
    if (!e) return
    const prev = e.resolvedIds ?? []
    st.patchEntry(key, {
      resolvedIds: want ? [...prev, ...done] : prev.filter((id) => !done.includes(id)),
      resolveFailed: failed || undefined,
      // 还原了的意见，改到一半的草稿一并作废（再应用会重新起草）；已发送的记号留着，不再重复起草
      ...(want ? {} : { drafts: Object.fromEntries(Object.entries(e.drafts).filter(([id, v]) => v === null || !done.includes(id))) }),
    })
  } finally {
    inflight.delete(key)
  }
}

/** 页签上「重试」：清掉失败标记，下一轮同步会再做一遍 */
export function retryResolveSync(key: string): void {
  useChangePlanStore.getState().patchEntry(key, { resolveFailed: false })
}

/** 挂在工作台一处（`ReviewHost`）：不管此刻开着哪个右栏页签，应用 / 撤销（含 ⌘Z）都同步到审阅 */
export function useReviewResolveSync(): void {
  const entries = useChangePlanStore((s) => s.entries)
  const detail = useReviewStore((s) => s.detail)
  const objects = useDocumentStore((s) => s.doc.objects)
  useEffect(() => {
    for (const e of Object.values(entries)) {
      const panel = panelOf(e)
      const todo = pendingSync(e, panel?.overrides ?? null, detail, panel ? sourceOf(panel) : null)
      if (todo) void runSync(e.key, todo)
    }
    // objects：⌘Z / 重做改的是文档，条目本身不变
  }, [entries, detail, objects])
}

/** 有没有一张卡的收尾没成（审阅页签那句灰字）；返回它的键 */
export function useResolveFailedKey(): string | null {
  const shareId = useReviewStore((s) => s.share?.share_id)
  return useChangePlanStore((s) => Object.values(s.entries).find((e) => e.resolveFailed && e.request.share_id === shareId)?.key ?? null)
}

/** 元素 gid → 审阅者看到的名字（取该分享各版本的元素表，新版本优先） */
export function elementLabelOf(detail: ReviewShareDetail | null): (gid: string) => string | undefined {
  return (gid) => {
    for (const v of [...(detail?.versions ?? [])].sort((a, b) => b.v - a.v)) {
      const hit = v.elements.find((x) => x.gid === gid)
      // 与左侧元素树同一个显示名出口（`engineLabel`：mathtext 读作 min⁻¹、按界面语言重组），不另写
      const name = hit?.label ? engineLabel(hit.label).trim() : ''
      if (name) return name
    }
    return undefined
  }
}

export interface ReplyDraft {
  key: string
  text: string
}

/** 这条意见此刻有没有待发的回复草稿（被某张已应用的卡标过「已处理」、还没发）；用户改过的文字优先于现算的 */
export function useReplyDraft(commentId: string): ReplyDraft | null {
  const entry = useChangePlanStore((s) => Object.values(s.entries).find((e) => e.resolvedIds?.includes(commentId) && e.drafts[commentId] !== null))
  const detail = useReviewStore((s) => s.detail)
  return useMemo(() => {
    if (!entry) return null
    return { key: entry.key, text: entry.drafts[commentId] ?? draftReply(changesOf(entry, commentId), elementLabelOf(detail)) }
  }, [entry, commentId, detail])
}
