import { useEffect } from 'react'
import { useDocumentStore } from '@/store/documentStore'
import { resolveReviewTarget } from '@/lib/reviewTarget'
import { useReviewStore } from '@/store/reviewStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { TooltipProvider } from '../ui/Tooltip'
import { ReviewDialog } from './ReviewDialog'

/**
 * 「分享审阅」对话框的唯一挂载点（工作台那一支），也是 `reviewStore.target` 的同步点：激活画布 / 单图编辑的那张图一变，
 * 就重新问一次引擎「这个 target 有没有分享记录」（决定右栏「审阅」页签出不出现）。功能关着时什么都不渲染。
 */
export function ReviewHost() {
  const enabled = useReviewStore((s) => s.enabled)
  const mode = useWorkspaceStore((s) => s.mode)
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  // 只取「是哪张图」的几个字符串：编辑图内元素 / 拖动时 objects 天天变，不该每次都重问引擎
  const key = useDocumentStore((s) => {
    if (mode === 'fast_edit' && activePanelId) {
      const p = s.doc.objects.find((o) => o.id === activePanelId)
      return p?.type === 'panel' ? JSON.stringify(['p', p.fileId, p.source?.stem ?? '']) : ''
    }
    return s.doc.objects.length > 0 ? '["l"]' : ''
  })
  useEffect(() => {
    if (!enabled) return
    let live = true
    const [kind, fileId, stem] = (key ? JSON.parse(key) : ['']) as string[]
    const panel = kind === 'p' ? { fileId, ...(stem ? { stem } : {}) } : null
    void resolveReviewTarget({ panel, hasObjects: kind === 'l', canvasId }).then((t) => {
      if (live) void useReviewStore.getState().setTarget(t)
    })
    return () => {
      live = false
    }
  }, [enabled, key, canvasId])
  if (!enabled) return null
  return (
    <TooltipProvider>
      <ReviewDialog />
    </TooltipProvider>
  )
}
