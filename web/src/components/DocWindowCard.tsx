import { useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/Card'
import { cn } from '@/lib/utils'
import { emptyStateAnchor } from '@/lib/emptyStateAnchor'
import { saveCopyHere, takeOverHere, useDocWindowStore } from '@/store/docWindow'
import { heldEntryCount, subscribeHeldEntries } from '@/store/documentRestore'
import { useDocumentStore } from '@/store/documentStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { Button } from './ui/Button'

/**
 * 同一份排版在两个窗口开着时，画布正中的一张卡（ADR 0181；做法同 `canvas/CanvasStage` 里的恢复卡）：
 * 一句话 + 一个主按钮 + 一个次按钮。选之前这个窗口不能改——用户动了手的操作被记住，选完接着做（有动作等着时描一圈强调边）。
 *  - 后开的窗口（`contested`）：「这份排版已在另一个窗口打开。」「在这里编辑」/「存成副本」；
 *  - 被接管的窗口（`yielded`）：「这份排版正在另一个窗口编辑。」「拿回来编辑」/「存成副本」。
 * 「在这里编辑」/「拿回来编辑」是同一个接管动作（对面存好并让出、这里载入最新版）；「存成副本」是真分叉。
 */
export function DocWindowCard() {
  const { t } = useTranslation('workspace')
  const hold = useDocumentStore((s) => s.windowHold)
  const busy = useDocWindowStore((s) => s.busy)
  const held = useSyncExternalStore(subscribeHeldEntries, heldEntryCount) > 0
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const viewW = useViewportStore((s) => s.viewW)
  const viewH = useViewportStore((s) => s.viewH)
  const page = useDocumentStore((s) => s.doc.page)
  // `yielding` 只是让出途中的一瞬（在存盘），不摆卡
  if (!hold || hold.role === 'yielding') return null
  const { x: cx, y: cy } = emptyStateAnchor({
    viewW,
    viewH,
    paper: { x: panX, y: panY, w: mmToWorld(page.w) * zoom, h: mmToWorld(page.h) * zoom },
  })
  const yielded = hold.role === 'yielded'
  return (
    <div
      data-doc-window-card
      data-doc-window-role={hold.role}
      data-doc-window-held={held ? '' : undefined}
      className="absolute z-canvas-chrome w-max max-w-80 -translate-x-1/2 -translate-y-1/2"
      style={{ left: cx, top: cy }}
      // 卡片上的按下不许漏给画布：选着画图工具时会就地起一笔（与恢复卡同理）
      onPointerDown={(e) => e.stopPropagation()}
    >
      <Card appearance="raised" padding="md" className={cn('flex flex-col gap-1.5', held && 'outline outline-2 outline-accent')}>
        <p className="type-title text-ink">{t(yielded ? 'docWindow.takenOver' : 'docWindow.openElsewhere')}</p>
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <Button variant="primary" size="sm" disabled={busy} data-doc-window-action="take" onClick={() => void takeOverHere()}>
            {t(yielded ? 'docWindow.takeBack' : 'docWindow.takeHere')}
          </Button>
          <Button size="sm" disabled={busy} data-doc-window-action="copy" onClick={() => void saveCopyHere()}>
            {t('docWindow.saveCopy')}
          </Button>
        </div>
      </Card>
    </div>
  )
}
