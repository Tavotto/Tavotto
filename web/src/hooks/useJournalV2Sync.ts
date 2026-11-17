import { useEffect } from 'react'
import { fetchJournalV2 } from '@/lib/journalV2'
import { journalCatalogUi } from '@/lib/journalUi'
import { refreshJournalV2 } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalV2Store } from '@/store/journalV2Store'
import { hasActiveGesture, subscribeGestureCoordinator } from '@/store/gestureCoordinator'

/**
 * 已按期刊检查的画布：打开项目 / 切画布时取该刊的 v2 事实，按项叠到绑定上（`refreshJournalV2`，自动、不问）。
 * 取不到什么都不做（预检照绑定里存着的判）；等的时候换了项目 / 画布 / 期刊就丢掉这次结果。
 */
export function useJournalV2Sync(): void {
  const journalId = useDocumentStore((s) => s.doc.profile?.journalRef?.id)
  const documentId = useDocumentStore((s) => s.documentId)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  useEffect(() => {
    if (!journalId || !journalCatalogUi()) return
    const controller = new AbortController()
    // 本次会话已经取到过（选刊 / 别的画布）就直接用，不重复请求；上次没取到的再试一次
    const known = useJournalV2Store.getState().byJournal[journalId]
    let unsubscribe: (() => void) | null = null
    const apply = (patch: Awaited<ReturnType<typeof fetchJournalV2>>) => {
      if (controller.signal.aborted) return
      const now = useDocumentStore.getState()
      if (now.documentId !== documentId || now.activeCanvasId !== canvasId || now.doc.profile?.journalRef?.id !== journalId) return
      // 响应可能落在一次拖动 / 连续编辑中途：这不是用户的动作，不许打断它，也不许把一条提交插进它的撤销里——
      // 等手势收尾（订阅同一份登记，不轮询）再叠
      if (hasActiveGesture()) {
        unsubscribe ??= subscribeGestureCoordinator(() => {
          if (hasActiveGesture()) return
          unsubscribe?.()
          unsubscribe = null
          apply(patch)
        })
        return
      }
      refreshJournalV2(journalId, patch)
    }
    void (known ? Promise.resolve(known) : fetchJournalV2(journalId, controller.signal)).then(apply)
    return () => {
      controller.abort()
      unsubscribe?.()
    }
  }, [journalId, documentId, canvasId])
}
