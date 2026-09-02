import { useTranslation } from 'react-i18next'
import { LayoutGrid, Plus } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { formatMm } from '@/lib/units'
import { getOriginalOutputSpec, type OriginalOutputSpec } from '@/lib/originalSpec'
import { reasonText, statusLabel } from '@/lib/readinessText'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { addFigureToLayout, returnToLayout, useWorkspaceStore } from '@/store/workspace'
import type { PanelObject } from '@/types/document'

/**
 * 快速编辑工作区的浮动条：当前是哪张图、它的原图规格、以及两个出口
 * （加入画布 / 回到画布排版）。
 *
 * **说什么话不在这里判**：状态标签与那句原因取自 `lib/readinessText.ts`
 * （全产品唯一一份），尺寸取自 `lib/originalSpec.ts`（同）。这里只排版。
 */
export function FastEditBar() {
  const { t } = useTranslation('workspace')
  const panelId = useWorkspaceStore((s) => s.activePanelId)
  const panel = useDocumentStore((s) => {
    const o = s.doc.objects.find((x) => x.id === panelId)
    return o?.type === 'panel' ? (o as PanelObject) : null
  })
  // capability 缺席 = 这一轮还不知道，什么都不说（不补默认值）
  const capability = useAssetStore((s) => (panel ? s.byId[panel.fileId]?.capability : undefined))
  if (!panel) return null

  const spec = getOriginalOutputSpec(panel.fileId)
  const name = panel.name ?? panel.fileId
  const editable = !!panel.script

  return (
    <div className="pointer-events-none absolute inset-x-0 top-2 z-30 flex justify-center px-2">
      <div className="pointer-events-auto flex max-w-full flex-col gap-1 rounded-md border border-border bg-surface px-2 py-1.5 shadow-pop">
        <div className="flex items-center gap-2">
          <span className="shrink-0 rounded-sm bg-ink/[.055] px-1.5 py-0.5 text-xs text-ink-2">
            {t('fastEdit.mode')}
          </span>
          <span className="min-w-0 truncate text-xs font-medium text-ink" title={name}>
            {name}
          </span>
          {spec && <OriginalSize spec={spec} />}
          <span aria-hidden className="h-3.5 w-px shrink-0 bg-border" />
          {/* `data-onboarding-anchor`：新手教程 Step 6 的 coachmark 挂这两颗按钮 */}
          <Button
            size="sm"
            variant="outline"
            data-onboarding-anchor="add-to-layout"
            onClick={() => void addFigureToLayout(panel.fileId)}
          >
            <Plus size={12} />
            {t('fastEdit.addToCanvas')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            data-onboarding-anchor="to-layout"
            onClick={returnToLayout}
          >
            <LayoutGrid size={12} />
            {t('fastEdit.toLayout')}
          </Button>
        </div>

        {/* 进不了图内编辑时诚实说明，并给出下一步——**不画成错误** */}
        {!editable && (
          <div className="flex items-center gap-2 border-t border-border pt-1 text-xs text-ink-2">
            <span className="min-w-0 truncate">
              {capability
                ? `${statusLabel(capability.status)} · ${reasonText(capability)}`
                : t('fastEdit.layoutOnly')}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => useProjectReadinessStore.getState().focusPanel(panel.fileId)}
            >
              {t('fastEdit.connectSource')}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * 原图规格那一格。**规格不确定时必须说出来**（UX_CONTRACTS §6b）：
 *
 * * `fallback` —— 一个来源都没有，显示的是占位值，不是这张图的尺寸；
 * * `stale` —— 源文件此刻不可用，这是上一次已知的规格；
 * * `dpiSource === 'assumed'` —— 位图没写物理密度，毫米数是按假定密度算的。
 *
 * 三种都只加一个短标记 + 一句 `title`，不铺解释——用户此刻要做的是改图，
 * 不是读一段关于 dpi 的说明。
 */
function OriginalSize({ spec }: { spec: OriginalOutputSpec }) {
  const { t } = useTranslation('workspace')
  const size = t('fastEdit.originalSize', {
    w: formatMm(spec.widthMm),
    h: formatMm(spec.heightMm),
  })
  const mark = spec.fallback
    ? t('fastEdit.sizeUnknown')
    : spec.stale
      ? t('fastEdit.sizeStale')
      : spec.dpiSource === 'assumed'
        ? t('fastEdit.sizeAssumed')
        : null
  return (
    <span
      className="flex shrink-0 items-center gap-1 font-mono text-xs text-ink-3"
      title={
        spec.fallback
          ? t('fastEdit.sizeUnknownTitle', { size })
          : spec.stale
            ? t('fastEdit.sizeStaleTitle')
            : spec.dpiSource === 'assumed'
              ? t('fastEdit.sizeAssumedTitle', { dpi: spec.dpi ?? 0 })
              : undefined
      }
    >
      {spec.fallback ? null : size}
      {mark && (
        <span className="rounded-sm bg-ink/[.055] px-1 font-sans text-ink-2">{mark}</span>
      )}
    </span>
  )
}
