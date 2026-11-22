import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { styleOverrideTargets, styleRoleLabel } from '@/lib/stylePresets'
import { adjustStyleToJournal, journalStylePlan } from '@/store/journalStyle'
import type { JournalStyleEdit } from '@/lib/journalStyle'
import {
  alignCanvasToStyle,
  restoreCanvasStyle,
  restoreReady,
  styleMismatchCount,
} from '@/store/styleBinding'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { panelRender, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'
import { useFigureFamilies } from '../settings/StyleProfileFields'
import { sp } from './styleRowParts'

/**
 * 样式面板底部的**画布级动作**（两层共用）：与样式不一致 → 对齐、按期刊调整、恢复原样、管理样式。
 *
 * 「跟随哪一套」的选择器 2026-11-20 起只在画布层顶部（`CanvasStyleLayer`），这里不再有第二个
 * （ADR 0081 §十四）；节也不再折叠——这几行只在有事可做时出现，平时只剩一个「管理样式…」。
 *
 * 脚本重跑之后样式不自动对齐（用户 2026-09-25 裁决：脚本赢）：与样式不一致的处数与「对齐」只在
 * 有不一致时出现（`styleMismatchCount` / `alignCanvasToStyle`，一次 commit）。
 * 「恢复原样」清掉这张画布上所有图里样式管得到的 override，并解绑（理由见
 * `styleBinding.restoreCanvasStyle`）；没有可恢复的、也没有绑定时不摆这颗钮。
 */
export function StyleCanvasActions() {
  useTranslation('workspace')
  const records = useProfileStore((s) => s.styles)
  const loaded = useProfileStore((s) => s.loaded)
  const binding = useDocumentStore((s) => s.doc.style ?? null)
  const objects = useDocumentStore((s) => s.doc.objects)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const specBinding = useDocumentStore((s) => s.doc.profile)
  const families = useFigureFamilies()
  const renderable = useMemo(
    () => families.options.filter((o) => !families.unavailable.includes(o)),
    [families],
  )
  /** 项目按期刊检查、画布上有不合期刊的字号 / 字体 / 线宽：给一颗「按期刊调整」（用户点了才改，ADR 0029） */
  const journalPlan = useMemo(
    () => journalStylePlan(renderable),
    [specBinding, binding, objects, byKey, latest, records, renderable], // eslint-disable-line react-hooks/exhaustive-deps
  )

  useEffect(() => {
    if (!loaded) void useProfileStore.getState().load()
  }, [loaded])

  const ready = useMemo(() => restoreReady(), [objects, byKey, latest]) // eslint-disable-line react-hooks/exhaustive-deps
  const restoreCount = useMemo(() => {
    let n = 0
    for (const o of objects) {
      if (o.type !== 'panel') continue
      const m = panelRender({ byKey, latest }, o)?.manifest
      if (m) n += styleOverrideTargets(o, m).length
    }
    return n
  }, [objects, byKey, latest])

  const detached = !!binding?.detached
  const mismatches = useMemo(
    () => (binding && !detached ? styleMismatchCount() : 0),
    [binding, detached, objects, byKey, latest, records], // eslint-disable-line react-hooks/exhaustive-deps
  )

  return (
    <section data-style-actions className="px-3 pb-3 pt-1">
      {mismatches > 0 && (
        <div data-style-mismatch className="flex h-7 items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate text-xs text-ink-2">{sp('mismatch', { count: mismatches })}</span>
          <Button data-style-align variant="ghost" onClick={() => void alignCanvasToStyle()}>
            {sp('align')}
          </Button>
        </div>
      )}
      {journalPlan && journalPlan.edits.length > 0 && (
        <div data-style-journal className="mt-1.5 flex h-7 items-center gap-1.5">
          <Tip label={journalPlan.edits.map(journalEditText).join('\n')} side="top" tone="light">
            <span tabIndex={0} className="min-w-0 flex-1 truncate text-xs text-ink-2">
              {sp('journalMismatch', { count: journalPlan.edits.length, journal: journalPlan.journal })}
            </span>
          </Tip>
          <Button data-style-journal-adjust variant="ghost" onClick={() => void adjustStyleToJournal(renderable)}>
            {sp('journalAdjust')}
          </Button>
        </div>
      )}
      <div className="mt-1.5 flex items-center gap-1.5">
        {(restoreCount > 0 || binding) && (
          <Button
            data-style-restore
            variant="ghost"
            // 有图还没渲染出来时认不出它身上哪些 override 是样式写的：先等，并说为什么
            disabled={!ready}
            title={ready ? undefined : sp('restoreWaiting')}
            onClick={() => void restoreCanvasStyle()}
          >
            <RotateCcw size={ICON_SIZE.sm} aria-hidden />
            {sp('restore', { count: restoreCount })}
          </Button>
        )}
        <button
          type="button"
          data-style-manage
          onClick={() => useUiStore.getState().setSettingsOpen(true, 'style')}
          className="ml-auto rounded-sm text-xs text-ink-3 underline-offset-2 outline-none hover:text-ink-2 hover:underline focus-visible:focus-ring"
        >
          {sp('manage')}
        </button>
      </div>
    </section>
  )
}

/** 「按期刊调整」的一项改动说成一行：「刻度 字号 6 → 7 pt」「轴标签 字体 DejaVu Sans → Arial」 */
function journalEditText(e: JournalStyleEdit): string {
  const who = e.kind === 'annotation' ? sp('journalAnnotation') : styleRoleLabel(e.role)
  const what = sp(`journalProp.${e.kind === 'annotation' ? 'fontsize' : e.prop === 'fontfamily' ? 'fontfamily' : e.prop.includes('font') ? 'fontsize' : 'linewidth'}`)
  const unit = e.kind === 'element' && e.prop === 'fontfamily' ? '' : ' pt'
  return `${who} ${what} ${String(e.from)} → ${String(e.value)}${unit}`
}
