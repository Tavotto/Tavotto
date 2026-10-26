import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { AiTaskSuggestion } from '@/lib/api'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { panelStemOfFileId } from '@/lib/panelStem'
import { setOverride } from '@/store/actions'
import { sameScript, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { structuralPanelManifest, useRenderStore } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Notice'

/**
 * 「图例里也写着同一段文字」的跟进卡（改图任务收尾时后端给的 `task.suggestions`，kind = `legend_label`）。
 *
 * 点「一起改」= 对**这条曲线对应的图例项**经画布的 `setOverride(panelId, legend_gid, 'text', to, true)` 写 override
 * （与属性栏图例卡改图例项文字同一条路；写曲线 `label` 不会动图例）。进撤销历史、不经 AI、不写脚本。
 * 图例项以面板当前 manifest 为准再核一次：`role=legend_text` 且 `legend_entry.source_gid === 曲线 gid`，与后端给的
 * `legend_gid` 对不上 / 没有 manifest 就不出卡。显示是派生的：只算该图例项仍显示原文（没有 text override，或 override 恰为 `from`）的面板——
 * 已改成 `to` 的、用户在画布上另改过文字的都不算，点击也不会覆盖它们。
 */
export function LegendLabelNotice({ session, suggestion }: { session: AiSession; suggestion: AiTaskSuggestion }) {
  const { t } = useTranslation('ai')
  const objects = useDocumentStore((s) => s.doc.objects)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const { stem, gid, legend_gid: legendGid, from, to } = suggestion
  const pending = useMemo(
    () =>
      objects.filter((o): o is PanelObject => {
        if (o.type !== 'panel' || !o.script) return false
        if (!sameScript(o.script, session.script) || panelStemOfFileId(o.fileId) !== stem) return false
        // 同一条曲线可能绑着多个图例项：按后端给的 legend_gid 取那一项，再核它确实绑着这条曲线
        const bound = structuralPanelManifest({ byKey, latest }, o)?.elements.find(
          (e) => e.gid === legendGid && e.role === 'legend_text' && e.legend_entry?.source_gid === gid,
        )
        if (!bound) return false
        // 只替换仍显示原文的那一份：已改成 `to` 的不再出卡；用户在画布上另改过的文字（不是 `from`）不覆盖
        const ov = effectiveOverride(o.overrides, legendGid, 'text')
        return ov === undefined || ov === null || ov.value === from
      }),
    [objects, byKey, latest, session.script, stem, gid, legendGid, from],
  )
  if (pending.length === 0) return null
  return (
    <Notice
      data-ai-suggestion={suggestion.kind}
      data-ai-suggestion-gid={legendGid}
      action={
        <Button
          data-ai-suggestion-apply
          size="sm"
          onClick={() => {
            for (const p of pending) setOverride(p.id, legendGid, 'text', to, true)
          }}
        >
          {t('notice.legendLabelApply')}
        </Button>
      }
    >
      {t('notice.legendLabel', { from, to })}
    </Notice>
  )
}
