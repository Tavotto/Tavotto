import { fetchPanelStem, fetchReviewShare, type ReviewShareDetail } from '@/lib/api'
import { msg } from '@/i18n/message'
import { activateCanvas } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { useReviewStore } from '@/store/reviewStore'
import { useUiStore } from '@/store/uiStore'
import { openFastEdit } from '@/store/workspace'

/**
 * 通知轨胶囊「查看」（ADR 0187 §八）：切到那条分享对应的图 / 排版，再打开右栏「审阅」页签。
 * 排版 = 按画布 id 找；单图 = 在项目的画布里按 stem 找那张图（独占一张画布就切过去，否则进单图编辑）。
 * 当前项目里找不到（分享的是别的项目的图）就在通知轨说一句，不乱切。
 */
export async function openReviewShare(shareId: string): Promise<boolean> {
  let d: ReviewShareDetail
  try {
    d = await fetchReviewShare(shareId)
  } catch {
    return false
  }
  const ui = useUiStore.getState()
  const docs = useDocumentStore.getState()
  let found = false
  if (d.kind === 'layout') {
    if (docs.canvases.some((c) => c.id === d.target_id)) {
      activateCanvas(d.target_id, { open: true })
      found = true
    }
  } else {
    for (const c of docs.canvases) {
      for (const o of c.objects) {
        if (o.type !== 'panel') continue
        const stem = o.source?.stem ?? (await fetchPanelStem(o.fileId).then((r) => r.stem).catch(() => null))
        if (stem !== d.target_id) continue
        if (c.objects.filter((x) => x.type === 'panel').length === 1) activateCanvas(c.id, { open: true })
        else void openFastEdit(o.fileId)
        found = true
        break
      }
      if (found) break
    }
  }
  if (!found) {
    ui.setStatus(msg('review.notFound', undefined, 'dialogs'), 'info')
    return false
  }
  // 目标换过去之后 ReviewHost 才会把这条分享认成「当前的」：等它就位再开页签（最多 3 秒）
  for (let i = 0; i < 30; i++) {
    if (useReviewStore.getState().share?.share_id === shareId) {
      useUiStore.getState().setRightTab('review')
      return true
    }
    await new Promise((r) => window.setTimeout(r, 100))
  }
  return false
}
