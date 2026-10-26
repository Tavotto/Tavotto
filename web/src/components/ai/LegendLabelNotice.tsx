import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { panelStemOfFileId } from '@/lib/panelStem'
import { setOverride } from '@/store/actions'
import type { AiTaskSuggestion } from '@/lib/api'
import { sameScript, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Notice'

/**
 * 「图例里也写着同一段文字」的跟进卡（改图任务收尾时后端给的 `task.suggestions`，kind = `legend_label`）。
 *
 * 点「一起改」= 经画布的 `setOverride(panelId, gid, 'label', to, true)` 写 override（进撤销历史、不经 AI、不写脚本），
 * 作用于**显示这份脚本这张图**的面板。卡片的显示是派生的：对应面板已经有这条 override（点过 / 重新载入）就不显示，
 * 没有可作用的面板也不显示——不另存「点过没有」的状态。
 */
export function LegendLabelNotice({ session, suggestion }: { session: AiSession; suggestion: AiTaskSuggestion }) {
  const { t } = useTranslation('ai')
  const objects = useDocumentStore((s) => s.doc.objects)
  const { stem, gid, from, to } = suggestion
  // 还没把图例文字改成 `to` 的、显示这份脚本这张图的面板
  const pending = useMemo(
    () =>
      objects.filter(
        (o): o is PanelObject =>
          o.type === 'panel' &&
          !!o.script &&
          sameScript(o.script, session.script) &&
          panelStemOfFileId(o.fileId) === stem &&
          effectiveOverride(o.overrides, gid, 'label')?.value !== to,
      ),
    [objects, session.script, stem, gid, to],
  )
  if (pending.length === 0) return null
  return (
    <Notice
      data-ai-suggestion={suggestion.kind}
      data-ai-suggestion-gid={gid}
      action={
        <Button
          data-ai-suggestion-apply
          size="sm"
          onClick={() => {
            for (const p of pending) setOverride(p.id, gid, 'label', to, true)
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
