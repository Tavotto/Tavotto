import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ManifestElement } from '@/lib/api'
import type { Rect4 } from '@/lib/axesLayout'
import { panelFullRect } from '@/lib/elementGeom'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { patchBoundsOf, patchMinSize } from '@/lib/patchResize'
import type { PanelObject } from '@/types/document'
import { beginElementPreview, cancelElementPreview, commitElementPreview } from '@/canvas/elementPreview'
import { clearOverride } from '@/store/actions'
import { finishActiveGesture, registerGesture } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { resizePatch } from '@/store/patchResizeAction'
import { useDisplayedExactManifest } from '@/store/mountedSvgStore'
import { Grid2 } from '../ui/Field'
import { NumberField } from '../ui/Input'
import { GroupHead } from './GroupHead'
import { ResetChip } from './controls/textRows'

/**
 * 图框的 W/H 是页面上的 mm，转成 figure 分数后与手柄写同一份 size_frac + pos_frac。
 * 输入与拖数字都固定左上角；连续拖数字只改线框预览，结束后一次提交。
 */
export function PatchSizeControls({ panel, element }: { panel: PanelObject; element: ManifestElement }) {
  const { t } = useTranslation('inspector')
  const manifest = useDisplayedExactManifest(panel)
  // 列表只提供 gid：位图的配对清单可能是另一个实例，尺寸与锚点必须来自实际显示的那份。
  const current = manifest?.elements.find((e) => e.gid === element.gid)
  if (!current) {
    // 换图时卸载输入器，同时取消尚未提交的数字拖动；样式编辑仍由父级保留。
    return <p data-patch-size-syncing className="text-xs leading-relaxed text-ink-3">{t('element.alignSyncing')}</p>
  }
  return <ExactPatchSizeControls panel={panel} element={current} />
}

function ExactPatchSizeControls({ panel, element }: { panel: PanelObject; element: ManifestElement }) {
  const { t } = useTranslation('inspector')
  const [draft, setDraft] = useState<Rect4 | null>(null)
  const scrub = useRef<{
    panel: PanelObject
    element: ManifestElement
    start: Rect4
    last: Rect4
    unregister: () => void
  } | null>(null)
  const bounds = patchBoundsOf(element)
  const full = panelFullRect(panel)
  const min = patchMinSize(element)

  const finish = useCallback((cancelled: boolean) => {
    const s = scrub.current
    if (!s) return
    scrub.current = null
    s.unregister()
    setDraft(null)
    useInteractionStore.getState().end()
    // 提交仍校验起手时的 panel 和 element，避免新清单到达后写入旧草稿。
    if (cancelled || !resizePatch(s.panel, s.element, s.last)) cancelElementPreview()
    else commitElementPreview(s.panel.id)
  }, [])
  useEffect(() => () => finish(true), [finish])

  if (!bounds) return null
  const shown = draft ?? bounds
  const begin = () => {
    finishActiveGesture()
    if (scrub.current) return
    beginElementPreview(panel)
    useInteractionStore.getState().begin('element')
    scrub.current = { panel, element, start: bounds, last: bounds, unregister: registerGesture(() => finish(false)) }
  }
  const write = (index: 2 | 3, valueMm: number) => {
    const s = scrub.current
    const next = [...(s?.start ?? bounds)] as Rect4
    next[index] = Math.max(min[index - 2], valueMm / (index === 2 ? full.w : full.h))
    if (s) {
      s.last = next
      setDraft(next)
      useInteractionStore.getState().setElementPreview({ boxes: { [element.gid]: next }, resizingGid: element.gid })
    } else resizePatch(panel, element, next)
  }
  const modified = !!effectiveOverride(panel.overrides, element.gid, 'size_frac')

  return (
    <div data-patch-size-block className="flex flex-col gap-1.5">
      <GroupHead meta={modified ? (
        <span data-patch-size-reset>
          <ResetChip label={t('element.patchSizeHead')} onReset={() => clearOverride(panel.id, element.gid, 'size_frac')} />
        </span>
      ) : undefined}>
        {t('element.patchSizeHead')}
      </GroupHead>
      <Grid2>
        <NumberField
          fill prefix="W" prefixInside unit="mm" precision={2}
          value={shown[2] * full.w} step={0.1} min={min[0] * full.w}
          dataProp="patch_width_mm"
          onChange={(v) => write(2, v)}
          onScrubStart={begin} onScrubEnd={() => finish(false)} onScrubCancel={() => finish(true)}
        />
        <NumberField
          fill prefix="H" prefixInside unit="mm" precision={2}
          value={shown[3] * full.h} step={0.1} min={min[1] * full.h}
          dataProp="patch_height_mm"
          onChange={(v) => write(3, v)}
          onScrubStart={begin} onScrubEnd={() => finish(false)} onScrubCancel={() => finish(true)}
        />
      </Grid2>
      <p className="text-xs leading-relaxed text-ink-3">{t('element.patchSizeHint')}</p>
    </div>
  )
}
