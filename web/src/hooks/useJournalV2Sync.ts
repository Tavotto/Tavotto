import { useEffect } from 'react'
import { fetchJournalV2 } from '@/lib/journalV2'
import { journalCatalogUi } from '@/lib/journalUi'
import { resolveJournalAlias } from '@/lib/journalPick'
import { rebindJournalId, refreshJournalV2 } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalCatalogStore } from '@/store/journalCatalogStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import { hasActiveGesture, hasActivePointerGesture, subscribeGestureCoordinator } from '@/store/gestureCoordinator'

/** 此刻有没有开着的手势或文档事务：连续编辑、指针拖动 / 缩放（走另一份登记），以及它们开着的 `txn`（commit 会并进去） */
const editing = (): boolean => hasActiveGesture() || hasActivePointerGesture() || useDocumentStore.getState().txn != null

/**
 * 已按期刊检查的画布：打开项目 / 切画布时取该刊的 v2 事实，按项叠到绑定上（`refreshJournalV2`，自动、不问）。
 * 取不到什么都不做（预检照绑定里存着的判）；等的时候换了项目 / 画布 / 期刊就丢掉这次结果。
 */
export function useJournalV2Sync(): void {
  useJournalAliasRebind() // 旧 id 先换绑到现 id，下面的 v2 取用随新 id 重跑
  const journalId = useDocumentStore((s) => s.doc.profile?.journalRef?.id)
  const documentId = useDocumentStore((s) => s.documentId)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  useEffect(() => {
    if (!journalId || !journalCatalogUi()) return
    const controller = new AbortController()
    // 本次会话已经取到过（选刊 / 别的画布）就直接用，不重复请求；上次没取到的再试一次
    const known = useJournalV2Store.getState().byJournal[journalId]
    let unsubscribe: (() => void) | null = null
    let unsubscribeDoc: (() => void) | null = null
    const stopWaiting = () => {
      unsubscribe?.()
      unsubscribe = null
      unsubscribeDoc?.()
      unsubscribeDoc = null
    }
    const apply = (patch: Awaited<ReturnType<typeof fetchJournalV2>>) => {
      if (controller.signal.aborted) return
      const now = useDocumentStore.getState()
      if (now.documentId !== documentId || now.activeCanvasId !== canvasId || now.doc.profile?.journalRef?.id !== journalId) return
      // 响应可能落在一次拖动 / 连续编辑中途：这不是用户的动作，不许打断它，也不许把一条提交插进它的撤销里——
      // 等手势收尾（订阅同一份登记，不轮询）再叠
      if (editing()) {
        const retry = () => {
          if (editing()) return
          stopWaiting()
          apply(patch)
        }
        // 指针手势的登记注销可能早于 / 晚于事务收口：两处都订，哪个先放行都等到真的没有开着的事务才叠
        unsubscribe ??= subscribeGestureCoordinator(retry)
        unsubscribeDoc ??= useDocumentStore.subscribe(retry)
        return
      }
      refreshJournalV2(journalId, patch)
    }
    void (known ? Promise.resolve(known) : fetchJournalV2(journalId, controller.signal)).then(apply)
    return () => {
      controller.abort()
      stopWaiting()
    }
  }, [journalId, documentId, canvasId])
}

/**
 * 项目里存的期刊 id 在当前目录里找不到、但恰好是某一本刊记着的旧名（别名）时，静默换绑到那本刊的新 id，
 * 之后上面的 `useJournalV2Sync` 才取得到 v2。目录会在启动后由远程索引换新，所以订阅目录：同步完成后再判一次。
 * 匹配不到 / 匹配到多本 / id 本身在目录里 = 什么都不做；不弹窗、不报错。
 * 放在前端：绑定存在文档里、只有前端写文档，目录也由前端持有（`journalCatalogStore`）；后端按 id 取刊不认别名，不改。
 */
export function useJournalAliasRebind(): void {
  const journalId = useDocumentStore((s) => s.doc.profile?.journalRef?.id)
  const documentId = useDocumentStore((s) => s.documentId)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  const catalog = useJournalCatalogStore((s) => s.catalog)
  useEffect(() => {
    if (!journalId || !journalCatalogUi()) return
    const next = resolveJournalAlias(journalId, catalog)
    if (!next) return
    let unsubscribe: (() => void) | null = null
    let unsubscribeDoc: (() => void) | null = null
    const stop = () => {
      unsubscribe?.()
      unsubscribe = null
      unsubscribeDoc?.()
      unsubscribeDoc = null
    }
    const run = () => {
      const now = useDocumentStore.getState()
      if (now.documentId !== documentId || now.activeCanvasId !== canvasId || now.doc.profile?.journalRef?.id !== journalId) return
      // 与 v2 自动更新同一条纪律：不打断拖动 / 连续编辑，等手势与事务收尾再写
      if (editing()) {
        const retry = () => {
          if (editing()) return
          stop()
          run()
        }
        unsubscribe ??= subscribeGestureCoordinator(retry)
        unsubscribeDoc ??= useDocumentStore.subscribe(retry)
        return
      }
      rebindJournalId(journalId, next)
    }
    run()
    return stop
  }, [journalId, documentId, canvasId, catalog])
}
