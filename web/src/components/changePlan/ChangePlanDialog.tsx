import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useChangePlanStore } from '@/store/changePlanStore'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { Segmented } from '../ui/Segmented'
import { ChangeList } from './ChangeList'
import { cp, effectiveStatus, groupViews, unhandledViews } from './changePlanModel'
import { CompareStage, type CompareMode } from './CompareStage'
import { useImageAspect, usePlanPanel, usePlanSource, useSelectionPreview } from './usePlanFigure'

/**
 * 修改方案弹窗（`ui/Dialog` 的 shell 外壳，按窗口取大：宽 min(1280, 92vw)、高 min(860, 90vh)）：左侧对比舞台、右侧改动清单。
 * 顶栏只有「修改方案」+ ×（Esc / × = 关闭，方案留着、卡片仍待确认）；页脚「放弃」+「应用」，**主按钮是「应用」**。
 * 「应用」= 一次 commit 进排版时间线，⌘Z 一步撤回（`changePlanStore.apply`）；全部取消勾选时禁用。
 * 批注、版本切换是高级版（ADR 0206 §五），不在这一期。
 */
export function ChangePlanDialog() {
  useTranslation('ai')
  const key = useChangePlanStore((s) => s.dialogKey)
  const entry = useChangePlanStore((s) => (s.dialogKey ? s.entries[s.dialogKey] : undefined))
  const [mode, setMode] = useState<CompareMode>('slide')
  const [activeId, setActiveId] = useState<string | null>(null)
  const panel = usePlanPanel(entry ?? { panelId: '', documentId: '', loadSeq: 0, canvasId: '' })
  const aspect = useImageAspect(entry?.afterSrc)
  // 换一份方案 / 重新打开：高亮不带过去
  useEffect(() => setActiveId(null), [key])
  const store = useChangePlanStore.getState
  const plan = entry?.plan
  // 弹窗开着时图被改了：基线对不上，不让点「应用」（卡片也已转「图已改过」）
  const source = usePlanSource(panel)
  const status = entry ? effectiveStatus(entry, panel?.overrides ?? null, source) : 'stale'
  const open = !!entry && !!plan && !!entry.beforeSrc && !!entry.afterSrc && status === 'ready'
  // 「修改后」跟着勾选的组合走：与「应用」写入的是同一份
  const preview = useSelectionPreview(entry, open)
  if (!entry || !plan || !entry.beforeSrc || !entry.afterSrc) return null
  const groups = groupViews(entry)
  const chosen = groups.filter((g) => !entry.unchecked[g.key])
  const canApply = status === 'ready' && chosen.length > 0
  const partial = chosen.length < groups.length
  // 一组都没勾 = 不改，「修改后」就是改前；部分勾选 = 按勾选出的那张（还没出来 / 失败时先用全量图，失败另有一句灰字）
  const after = chosen.length === 0 ? entry.beforeSrc : partial && preview.url ? preview.url : entry.afterSrc
  const previewBusy = partial && preview.loading
  // 只有「出好的、且正是此刻勾选组合」的那张才交给卡片当实际应用的图
  const keysSig = chosen.map((g) => g.key).join('\u0000')
  const appliedSrc = partial && !preview.loading && !preview.failed && preview.sig === keysSig ? (preview.url ?? undefined) : undefined

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !v && store().closeDialog()}
      title={cp('dialog.title')}
      chrome="shell"
      width="min(1280px, 92vw)"
      height="min(860px, 90vh)"
      anchor="change-plan"
      footer={{
        start: (
          <Button variant="ghost" size="lg" className="-ml-3.5" onClick={() => store().discard(entry.key)} data-change-plan-discard>
            {cp('dialog.discard')}
          </Button>
        ),
        primary: (
          <Button variant="primary" size="lg" onClick={() => void store().apply(entry.key, { previewSrc: appliedSrc })} disabled={!canApply} data-change-plan-confirm>
            {cp('dialog.apply')}
          </Button>
        ),
      }}
    >
      <div className="flex min-h-0 flex-1" data-change-plan-dialog>
        <div className="flex min-w-0 flex-1 flex-col bg-canvas">
          <div className="flex h-11 shrink-0 items-end justify-center px-5">
            <Segmented<CompareMode>
              value={mode}
              onChange={setMode}
              ariaLabel={cp('dialog.compareLabel')}
              compact
              className="w-60"
              items={[
                { value: 'slide', label: cp('dialog.slide') },
                { value: 'side', label: cp('dialog.side') },
                { value: 'toggle', label: cp('dialog.toggle') },
              ]}
            />
          </div>
          <CompareStage before={entry.beforeSrc} after={after} afterBusy={previewBusy} aspect={aspect} mode={mode} groups={chosen} activeId={activeId} />
        </div>
        <div className="flex w-[19rem] shrink-0 flex-col border-l border-border">
          {partial && preview.failed && (
            <p className="shrink-0 px-4 pt-3 type-meta" data-change-plan-preview-failed>
              {cp('dialog.previewStale')}
            </p>
          )}
          <ChangeList
            groups={groups}
            unhandled={unhandledViews(entry)}
            unchecked={entry.unchecked}
            onToggle={(g) => store().toggleGroup(entry.key, g)}
            activeId={activeId}
            onActive={setActiveId}
          />
        </div>
      </div>
    </Dialog>
  )
}
