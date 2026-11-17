import { useState } from 'react'
import { t as translate } from '@/i18n'
import { Dialog } from '../ui/Dialog'

/**
 * 原文高亮截图的缩略图（宽 240px），点开在对话框里看大图（走公共 Dialog：放在设置窗里也不会被当成点到窗外）。
 * 查看器与出处卡共用；加载失败就整个不画（不显示任何错误态），没有截图的调用方不渲染它。
 */
export function SnapshotThumb({ src, alt, className }: { src: string; alt?: string; className?: string }) {
  const [zoom, setZoom] = useState(false)
  const [failed, setFailed] = useState(false)
  if (failed) return null
  const label = alt || translate('journal.snapshotAlt', { ns: 'dialogs' })
  return (
    <div data-journal-thumb className={className}>
      <button
        type="button"
        title={translate('journal.snapshotZoom', { ns: 'dialogs' })}
        onClick={() => setZoom(true)}
        className="block w-60 max-w-full cursor-zoom-in overflow-hidden rounded-md border border-border"
      >
        <img src={src} alt={label} onError={() => setFailed(true)} className="block h-auto w-full bg-paper" />
      </button>
      <Dialog open={zoom} onOpenChange={setZoom} title={translate('journal.snapshotAlt', { ns: 'dialogs' })} size="lg">
        <img data-journal-thumb-zoom src={src} alt={label} className="block h-auto w-full rounded-md border border-border bg-paper" />
      </Dialog>
    </div>
  )
}
