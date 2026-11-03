import { useTranslation } from 'react-i18next'
import { Info } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Button } from '@/components/ui/Button'
import { Tip } from '@/components/ui/Tooltip'
import { frameCanvasOf, isZeroPad } from '@/lib/framePad'
import { canFitFrameToContent, clearFramePad, fitFrameToContent } from '@/store/framePadActions'
import { useDocumentStore } from '@/store/documentStore'
import { useExactPanelManifest } from '@/store/renderStore'
import { useWorkspaceStore } from '@/store/workspace'
import type { PanelObject } from '@/types/document'
import { Sep } from './shared'

const mm1 = (v: number) => String(Math.round(v * 10) / 10)

/**
 * 单图编辑选中这张图时，胶囊里的画布一段（ADR 0161）：画布尺寸（图幅 + 留白）、「贴合内容」「去掉留白」。
 * 只在单图编辑、且选中的就是那张图时出现；多图排版里不挂（排版的边 = 裁剪，另有一套）。
 */
export function FrameQuick({ obj }: { obj: PanelObject }) {
  const { t } = useTranslation('workspace')
  const single = useWorkspaceStore((s) => s.mode === 'fast_edit' && s.activePanelId === obj.id)
  const framePad = useDocumentStore((s) => s.doc.framePad)
  const framePadTight = useDocumentStore((s) => s.doc.framePadTight)
  const manifest = useExactPanelManifest(single ? obj : null)
  if (!single) return null
  const c = frameCanvasOf({ framePad, framePadTight }, obj, manifest)
  const padded = !isZeroPad(c.pad) || !!framePadTight?.length
  const fittable = c.exact && c.inset != null && canFitFrameToContent(obj.id)
  return (
    <div className="flex items-center gap-1" data-frame-quick="">
      <span className="type-number px-1.5 text-ink-2 whitespace-nowrap" data-frame-size="">
        {t('frameDrag.canvasSize', { w: mm1(c.sizeMm[0] + c.pad.l + c.pad.r), h: mm1(c.sizeMm[1] + c.pad.t + c.pad.b) })}
      </span>
      <Tip label={t('frameDrag.notWrittenBack')} side="bottom">
        <span className="inline-flex text-ink-3" aria-label={t('frameDrag.notWrittenBack')}>
          <Info size={ICON_SIZE.sm} />
        </span>
      </Tip>
      <Button size="md" disabled={!fittable} data-frame-action="fit" onClick={() => fitFrameToContent(obj.id)}>
        {t('frameDrag.fitContent')}
      </Button>
      <Button size="md" disabled={!padded} data-frame-action="clear" onClick={() => clearFramePad()}>
        {t('frameDrag.clearPad')}
      </Button>
      <Sep />
    </div>
  )
}
