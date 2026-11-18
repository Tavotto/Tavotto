import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Segmented } from '@/components/ui/Segmented'
import { canRevealInFileManager, revealProjectFile } from '@/lib/desktop'
import { feedbackAction } from '@/lib/canvasFileIntake'
import { KNOWN_NOTHING_REASONS } from '@/lib/dataFigure'
import { useFormatMessage } from '@/i18n/react'
import { useDataBatchStore } from '@/store/dataBatchStore'
import '@/lib/dataFigureHost'
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { CandidateGrid, useDrawingLine } from './CandidateGrid'
import { DataStrip } from './DataStrip'

/** 正文字号、左对齐、没有底色块和图标的一句话（失败 / 说明都用它） */
function Line({ children, anchor }: { children: React.ReactNode; anchor?: string }) {
  return (
    <p {...(anchor ? { [anchor]: '' } : {})} className="text-base text-ink">
      {children}
    </p>
  )
}

/**
 * 临时标签的内容区（画布标签栏里的临时标签激活时，盖在画布位置上；画布与文档原样不动）：
 * 上面是候选画法网格（或一句话的说明），下面是数据条。出图中被点的那张带扫光、其余淡掉。
 * Esc 关临时标签；切到别的画布标签时临时标签留着但不激活。
 */
export function DataDraft() {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const open = useDataFigureStore((s) => s.draftOpen)
  const active = useDataFigureStore((s) => s.draftActive)
  const selected = useDataFigureStore((s) => s.selected)
  const group = useDataFigureStore((s) => s.group)
  const sources = useDataFigureStore((s) => s.sources)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  // 合画的数据条此刻看第几个文件（默认第一个）；换了一组就回到第一个
  const [stripIdx, setStripIdx] = useState(0)
  const groupKey = group ? group.map((r) => `${r.path}|${r.sheet}`).join('\n') : null
  useEffect(() => setStripIdx(0), [groupKey])
  const stripRef = group ? (group[Math.min(stripIdx, group.length - 1)] ?? selected) : selected
  const stripKey = stripRef ? `${stripRef.path}|${stripRef.sheet}|${headerRows[stripRef.path] ?? ''}` : null
  // 切到某个文件才读它的概览（读过的不重读；该文件的表头行换了键就变，会重读）
  useEffect(() => {
    if (group && stripRef) useDataFigureStore.getState().ensureOverview(stripRef)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stripKey, groupKey])
  const phase = useDataFigureStore((s) => s.phase)
  const allCandidates = useDataFigureStore((s) => s.candidates)
  const thumbs = useDataFigureStore((s) => s.thumbs)
  const session = useDataFigureStore((s) => s.session)
  const headerRowUsed = useDataFigureStore((s) => s.headerRowUsed)
  const unitsRow = useDataFigureStore((s) => s.unitsRow)
  const reasons = useDataFigureStore((s) => s.nothingReasons)
  const error = useDataFigureStore((s) => s.error)
  const drawingId = useDataFigureStore((s) => s.drawingId)
  const problem = useDataFigureStore((s) => s.problem)
  const root = useRef<HTMLDivElement>(null)
  const st = useDataFigureStore.getState
  const drawingLine = useDrawingLine(drawingId != null)
  const feedback = feedbackAction()
  const projectRoot = useProjectStore((s) => s.project?.figures_dir)

  // 切到别的画布（点真标签、新建、别处打开）：临时标签留在标签栏里，但不再激活
  const activeCanvasId = useDocumentStore((s) => s.activeCanvasId)
  const lastCanvas = useRef(activeCanvasId)
  useEffect(() => {
    if (lastCanvas.current !== activeCanvasId) {
      lastCanvas.current = activeCanvasId
      useDataFigureStore.getState().deactivateDraft()
    }
  }, [activeCanvasId])

  // 窄屏（左抽屉是覆盖式的）：点了文件、临时标签激活，抽屉就收起，免得盖住候选
  const draftKey = active && selected ? `${selected.path}|${selected.sheet}` : null
  useEffect(() => {
    if (!draftKey) return
    const ui = useUiStore.getState()
    if (ui.layout === 'narrow' && ui.leftOpen) ui.toggleLeft()
  }, [draftKey])

  // Esc 关临时标签（激活时；对话框 / 菜单自己收的 Esc 不算）
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (document.querySelector('[role="dialog"][data-state="open"], [role="menu"]')) return
      e.preventDefault()
      useDataFigureStore.getState().closeDraft()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active])

  if (!open || !active || !selected) return null
  // 取不到图的（422）静默去掉；推荐标记在剩下的第一张上
  const candidates = allCandidates.filter((c) => thumbs[c.id] !== 'failed')
  const settled = candidates.length > 0 && candidates.every((c) => thumbs[c.id] === 'ready')
  const shown = stripRef ?? selected
  const ovState = overviews[overviewKeyOf(shown, headerRows)]
  const ov = ovState?.data ?? null
  const setRow = (line: number) => st().setHeaderRow(shown.path, shown.sheet, line)
  // 合画：这个文件各自的表头行 / 单位行（响应的 `sources`；没有就退回概览猜的）
  const src = group ? sources?.find((s) => s.path === shown.path && s.sheet === shown.sheet) : undefined
  const labelOf = (r: { path: string; sheet: string | null }): string => {
    const hit = sources?.find((s) => s.path === r.path && s.sheet === r.sheet)
    if (hit) return hit.label
    const base = r.path.slice(r.path.lastIndexOf('/') + 1)
    const name = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base
    return r.sheet ? `${name} · ${r.sheet}` : name
  }
  const stripTabs = group ? (
    <Segmented
      ariaLabel={t('dataFigureV2.list.heading')}
      value={String(Math.min(stripIdx, group.length - 1))}
      onChange={(v) => setStripIdx(Number(v))}
      className="w-auto"
      items={group.map((r, i) => ({ value: String(i), label: <span className="whitespace-nowrap px-1">{labelOf(r)}</span> }))}
    />
  ) : undefined

  let top: React.ReactNode
  if (problem?.kind === 'mismatch') {
    const reveal = projectRoot && canRevealInFileManager() ? () => void revealProjectFile(projectRoot, problem.script) : null
    top = (
      <div data-studio-problem className="flex flex-col items-start gap-2">
        <Line>{t('dataFigureV2.result.mismatch')}</Line>
        <p className="text-sm text-ink-2">{t('dataFigureV2.problem.mismatchNext')}</p>
        <p className="max-w-full truncate font-mono text-xs text-ink-3" data-studio-script-path title={problem.script}>
          {problem.script}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="lg" data-studio-primary onClick={() => st().dismissProblem()}>
            {t('dataFigureV2.problem.otherWay')}
          </Button>
          {reveal && (
            <Button variant="secondary" size="lg" onClick={reveal}>
              {t('dataFigureV2.problem.reveal')}
            </Button>
          )}
          {feedback && (
            <Button variant="ghost" size="lg" onClick={feedback.run}>
              {fm(feedback.label)}
            </Button>
          )}
        </div>
      </div>
    )
  } else if (problem?.kind === 'unwritable') {
    top = (
      <div data-studio-problem className="flex flex-col items-start gap-2">
        <Line>{t('dataFigureV2.problem.unwritable')}</Line>
        <Button variant="primary" size="lg" className="mt-1" data-studio-primary onClick={() => useProjectStore.getState().showPicker()}>
          {t('dataFigureV2.problem.otherFolder')}
        </Button>
      </div>
    )
  } else if (problem?.kind === 'commit' || phase === 'failed') {
    const err = problem?.kind === 'commit' ? problem.error : error
    const canRetry = problem?.kind !== 'commit' || problem.retryable
    top = (
      <div data-studio-problem className="flex flex-col items-start gap-2">
        <Line>{t('dataFigureV2.draft.failed')}</Line>
        {err && <p className="text-sm text-ink-2">{fm(err)}</p>}
        {canRetry && (
          <Button variant="primary" size="lg" className="mt-1" data-studio-primary data-studio-retry onClick={() => void st().retry()}>
            {t('dataFigureV2.problem.retry')}
          </Button>
        )}
      </div>
    )
  } else if (group && (phase === 'columnsDiffer' || phase === 'limitExceeded' || (phase === 'nothing' && reasons.includes('table_not_found')))) {
    // 合画的出路（两个稳定码，以及某份表不在了的 `nothing`）：一句话 + 回到「各画一张」（它的出路）——恢复刚才的选择、回数据抽屉、直接开始
    top = (
      <div data-studio-problem data-studio-group-problem={phase} className="flex flex-col items-start gap-2">
        <Line>{phase === 'columnsDiffer' ? t('dataFigureV2.batch.columnsDiffer') : phase === 'limitExceeded' ? (error ? fm(error) : t('dataFigureV2.draft.failed')) : t('dataFigureV2.reason.table_not_found')}</Line>
        <Button variant="primary" size="lg" className="mt-1" data-studio-primary data-studio-group-each onClick={() => useDataBatchStore.getState().drawEach(group)}>
          {t('dataFigureV2.batch.draw')}
        </Button>
      </div>
    )
  } else if (phase === 'unreadable') {
    top = <Line anchor="data-studio-unreadable">{t('dataFigureV2.draft.unreadable')}</Line>
  } else if (phase === 'text') {
    top = <Line anchor="data-studio-text-only">{t('dataFigureV2.draft.allText')}</Line>
  } else if (phase === 'pickrow') {
    top = <Line anchor="data-studio-pickrow">{t('dataFigureV2.preview.pickHint')}</Line>
  } else if (phase === 'nothing') {
    const first = reasons.find((r) => KNOWN_NOTHING_REASONS.includes(r))
    top = <Line anchor="data-studio-nothing">{first ? t(`dataFigureV2.reason.${first}`) : t('dataFigureV2.nothing.generic')}</Line>
  } else if (phase === 'reading') {
    top = <p className="text-base text-ink-2" role="status">{t('dataFigureV2.working.reading')}</p>
  } else {
    const pending = phase === 'loading' || phase === 'idle'
    top = (
      <>
        <p data-draft-lead className="mb-3.5 h-5 text-base text-ink-2" role="status">
          {drawingId ? drawingLine : settled ? t('dataFigureV2.draft.pickLead') : t('dataFigureV2.working.drawing')}
        </p>
        <CandidateGrid items={candidates} thumbs={thumbs} pending={pending} drawingId={drawingId} onPick={(id) => void st().draw(id)} onThumb={(id, ok) => session && st().thumbSettled(session, id, ok)} />
      </>
    )
  }

  const noStrip = phase === 'unreadable' && !ov?.raw_lines?.length
  return (
    <div
      ref={root}
      data-data-draft
      role="tabpanel"
      aria-label={group ? t('dataFigureV2.batch.groupTitle', { count: group.length }) : selected.path.slice(selected.path.lastIndexOf('/') + 1)}
      // z-sticky：盖住画布，但压在通知轨（z-canvas-chrome）与准备卡（z-drawer）下面，出图期间的通知不会被盖住
      className="absolute inset-0 z-sticky flex flex-col bg-surface"
    >
      <div key={`top|${selected.path}|${selected.sheet}`} className="min-h-0 flex-1 animate-fade-in overflow-auto px-7 pb-6 pt-5" style={{ containerType: 'size' }}>
        {top}
      </div>
      {!noStrip && (
        <DataStrip
          key={`strip|${selected.path}|${selected.sheet}`}
          ov={ov}
          // 要用户点一行（没出候选）时不显示概览里猜的那一行：0 = 明确没有
          guessRow={phase === 'pickrow' ? 0 : group ? (src?.headerRow ?? null) : headerRowUsed}
          unitsRow={group ? (src?.unitsRow ?? false) : unitsRow}
          tabs={stripTabs}
          loading={ovState?.status === 'loading'}
          forceOpen={phase === 'text' || phase === 'pickrow' || (ov != null && (ov.status === 'unsure' || ov.status === 'unreadable') && ov.raw_lines.length > 0)}
          onHeaderRow={setRow}
        />
      )}
    </div>
  )
}
