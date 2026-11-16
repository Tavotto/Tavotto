import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { useDocumentStore } from '@/store/documentStore'
import { useWorkspaceStore } from '@/store/workspace'

/**
 * 画布底部工具条上方的一枚胶囊：「用 growth.csv 画的 · 换个画法」，点了回到这份数据的临时标签。
 * 只在刚用「数据」画出这张图的这一次会话里出现（`fresh` 不持久化，换项目即清），且只在那张画布上、临时标签没激活时。
 */
export function RedrawPill() {
  const { t } = useTranslation('ai')
  const fresh = useDataFigureStore((s) => s.fresh)
  const draftActive = useDataFigureStore((s) => s.draftActive)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  // 新图放上去之后激活的画布换了：那张就是新图所在的画布
  useEffect(() => {
    if (fresh && fresh.canvasId == null && canvasId) useDataFigureStore.getState().adoptFresh(canvasId)
  }, [fresh, canvasId])
  // 「撤销」= 撤销「编辑原图」把这张图加进文档的那一步（与顶栏撤销同一条栈）；撤销栈还停在加入那一刻才给，之后再改了别的，撤销撤的就是别的了
  const justAdded = useWorkspaceStore((s) => s.addedForEdit !== null && s.addedForEdit === s.activePanelId)
  const addedDepth = useWorkspaceStore((s) => s.addedForEditDepth)
  const historyDepth = useDocumentStore((s) => s.past.length)
  const canUndo = justAdded && historyDepth === addedDepth
  if (!fresh || draftActive || fresh.canvasId == null || fresh.canvasId !== canvasId) return null
  const file = fresh.file.path.slice(fresh.file.path.lastIndexOf('/') + 1)
  return (
    <div data-redraw-pill className="pointer-events-auto absolute bottom-[6.5rem] left-1/2 z-canvas-chrome flex h-8 -translate-x-1/2 animate-fade-in items-center gap-2 whitespace-nowrap rounded-full bg-surface pl-3.5 pr-1.5 text-sm text-ink-2 shadow-pop">
      <span>{t('dataFigureV2.redraw.from', { file })}</span>
      <button
        type="button"
        data-redraw-again
        onClick={() => useDataFigureStore.getState().redraw()}
        className="h-6 rounded-full px-2.5 font-medium text-ink outline-none hover:bg-surface-hover focus-visible:focus-ring"
      >
        {t('dataFigureV2.redraw.again')}
      </button>
      {canUndo && (
        <button
          type="button"
          data-redraw-undo
          onClick={() => runUndoRedo(false)}
          className="h-6 rounded-full px-2.5 text-ink-2 outline-none hover:bg-surface-hover hover:text-ink focus-visible:focus-ring"
        >
          {t('dataFigureV2.redraw.undo')}
        </button>
      )}
    </div>
  )
}
