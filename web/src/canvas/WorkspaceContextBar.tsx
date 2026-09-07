import { useTranslation } from 'react-i18next'
import { ArrowLeft, Info, Plus } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { Button } from '@/components/ui/Button'
import { engineLabel } from '@/components/inspector/roles/registry'
import { formatMm } from '@/lib/units'
import { useOriginalSpec } from '@/hooks/useOriginalSpec'
import type { OriginalOutputSpec } from '@/lib/originalSpec'
import { reasonText, statusLabel } from '@/lib/readinessText'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { addFigureToLayout, returnToLayout, useWorkspaceStore } from '@/store/workspace'
import type { PanelObject } from '@/types/document'

/**
 * 工作区上下文栏（审计 T01）：**「我在改哪一层」只说一遍**。
 *
 * ```text
 * ← 返回画布   Fig2_correlation  /  Y 轴刻度                    画布排版 + 图内编辑
 * ← 返回画布   Fig1_kinetics [快速编辑] · 原图 80 × 57.6 mm │ + 添加到画布    快速编辑
 * ← 返回画布   Fig1_kinetics [快速编辑] / 标题 “…” · 原图 … │ + 添加到画布    快速编辑 + 图内编辑
 * ```
 *
 * 此前这条信息散在三处：顶栏有一个「快速编辑」标签兼返回按钮，画布上方有
 * 「图内编辑：Fig2 / 返回画布 Esc」一条，快速编辑另有一条带「画布排版」出口——
 * 三个返回入口、两种模式词、用户分不清点哪个回到哪。现在**返回只有这一个
 * 主位置**（Esc 照旧逐层退），面包屑从大到小：图 / 当前对象。
 *
 * **说什么话不在这里判**：状态标签与那句原因取自 `lib/readinessText.ts`，尺寸取
 * 自 `lib/originalSpec.ts`（与导出对话框同一个 hook），对象名过 `engineLabel`。
 * 这里只排版。
 */
export function WorkspaceContextBar() {
  const { t } = useTranslation('workspace')
  const fastEdit = useWorkspaceStore((s) => s.mode === 'fast_edit')
  const activePanelId = useWorkspaceStore((s) => s.activePanelId)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedGids = useUiStore((s) => s.selectedGids)
  // 「编辑原图」这一次把图加进了文档（此前不在）：常驻一行说明，回排版即消失
  const justAdded = useWorkspaceStore(
    (s) => s.addedForEdit !== null && s.addedForEdit === s.activePanelId,
  )
  const panelId = fastEdit ? activePanelId : elementPanelId
  const panel = useDocumentStore((s) => {
    const o = s.doc.objects.find((x) => x.id === panelId)
    return o?.type === 'panel' ? (o as PanelObject) : null
  })
  // capability 缺席 = 这一轮还不知道，什么都不说（不补默认值）
  const capability = useAssetStore((s) =>
    panel && fastEdit ? s.byId[panel.fileId]?.capability : undefined,
  )
  // 与导出对话框同一个 hook：渲染回来 / 图幅同步时两处一起重算，数不可能对不上
  const spec = useOriginalSpec(fastEdit && panel ? panel.fileId : null)
  const editingHere = !!panel && elementPanelId === panel.id
  const manifest = usePanelDisplayManifest(editingHere ? panel : null)
  if (!panel) return null

  const name = panel.name ?? panel.fileId
  const editable = !!panel.script
  const gid = editingHere ? selectedGids.at(-1) : undefined
  const element = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
  const objectCrumb = !editingHere
    ? null
    : selectedGids.length > 1
      ? translate('elementsSelected', { ns: 'inspector', count: selectedGids.length })
      : element
        ? engineLabel(element.label)
        : null

  /**
   * 唯一的返回入口。快速编辑里 = 回到画布排版（顺带退出图内编辑）；画布排版
   * 里 = 退出图内编辑并选中该面板——属性页落在面板上，「写回原始文件」就在手边。
   * 与 `useKeyboard` 里 Esc 的最后一层同一件事。
   */
  const back = () => {
    if (fastEdit) {
      returnToLayout()
      return
    }
    useUiStore.getState().setElementPanel(null)
    useSelectionStore.getState().set([panel.id])
  }

  return (
    <div className="pointer-events-none absolute inset-x-0 top-2 z-30 flex justify-center px-2">
      <nav
        aria-label={t('stage.contextBarLabel')}
        data-workspace-context-bar
        data-workspace-mode={fastEdit ? 'fast_edit' : 'layout'}
        className="pointer-events-auto flex max-w-full flex-col gap-1 rounded-md border border-border bg-surface px-1.5 py-1 shadow-pop"
      >
        <div className="flex items-center gap-1.5">
          {/* `data-onboarding-anchor="to-layout"`：新手教程 Step 6 的 coachmark 挂这颗 */}
          <Button
            size="sm"
            variant="ghost"
            data-context-back
            data-onboarding-anchor={fastEdit ? 'to-layout' : undefined}
            title={fastEdit ? t('fastEdit.crumbTitle') : t('stage.exitTitle')}
            onClick={back}
          >
            <ArrowLeft size={ICON_SIZE.sm} />
            {t('stage.backToCanvas')}
            {editingHere && (
              <span className="font-mono text-xs text-ink-3">{translate('keycap.esc')}</span>
            )}
          </Button>
          <span aria-hidden className="h-3.5 w-px shrink-0 bg-border" />
          <ol aria-label={t('stage.crumbsLabel')} className="flex min-w-0 items-center gap-1 text-xs">
            <li className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-0 truncate font-medium text-ink" title={name}>
                {name}
              </span>
              {fastEdit && (
                <span className="shrink-0 rounded-sm bg-ink/[.055] px-1.5 py-0.5 text-ink-2">
                  {t('fastEdit.mode')}
                </span>
              )}
            </li>
            {objectCrumb && (
              <>
                <li aria-hidden className="shrink-0 text-ink-faint">
                  /
                </li>
                <li className="min-w-0 truncate text-ink-2" data-context-object title={objectCrumb}>
                  {objectCrumb}
                </li>
              </>
            )}
          </ol>
          {spec && <OriginalSize spec={spec} />}
          {fastEdit && (
            <>
              <span aria-hidden className="h-3.5 w-px shrink-0 bg-border" />
              {/* `data-onboarding-anchor`：新手教程 Step 6 的 coachmark 挂这颗按钮 */}
              <Button
                size="sm"
                variant="outline"
                data-onboarding-anchor="add-to-layout"
                onClick={() => void addFigureToLayout(panel.fileId)}
              >
                <Plus size={ICON_SIZE.sm} />
                {t('fastEdit.addToCanvas')}
              </Button>
            </>
          )}
        </div>

        {/* 这张图是为了编辑才刚加进文档的：说出口，并说明怎么撤（UI 审计 T06）。
            不是 toast——进快速编辑紧接着的「渲染完成」会把单槽位的状态盖掉 */}
        {justAdded && (
          <div
            data-fast-edit-added-note
            className="flex items-center gap-1.5 border-t border-border pt-1 text-xs text-ink-2"
          >
            <Info size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
            <span className="min-w-0 truncate">{t('fastEdit.addedForEdit')}</span>
          </div>
        )}

        {/* 进不了图内编辑时诚实说明，并给出下一步——**不画成错误** */}
        {fastEdit && !editable && (
          <div className="flex items-center gap-2 border-t border-border pt-1 text-xs text-ink-2">
            <span className="min-w-0 truncate">
              {capability
                ? `${statusLabel(capability.status)} · ${reasonText(capability)}`
                : t('fastEdit.layoutOnly')}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => useProjectReadinessStore.getState().focusPanel(panel.fileId, 'quickedit')}
            >
              {t('fastEdit.connectSource')}
            </Button>
          </div>
        )}
      </nav>
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
