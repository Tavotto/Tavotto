import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Reveal } from '@/components/ui/Field'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { MAX_MERGE_TABLES } from '@/lib/dataFigure'
import { backendCodeMsg } from '@/lib/api'
import { useFormatMessage } from '@/i18n/react'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 多选态下「数据」抽屉标题行正下方的动作区（取代旧的底部浮条）：固定高度、透明底、底部一条细分割线，
 * 从「已选 0 份」到进行中、没画成、画完高度全程不变——点选过程中清单不跳；进入 / 退出多选态时整块用现有的高度过渡（`Reveal`，
 * 减少动态效果时全局兜底直接切换）。两行：一句灰字状态 + 两个等宽并排的按钮。
 * 左右边缘与标题行对齐：左 12px（标题文字）、右 6px（图钉按钮右沿）。文件名 / 列名不进 `data-*`。
 */
export function DataBatchActions({ open }: { open: boolean }) {
  return (
    <Reveal open={open}>
      <Zone />
    </Reveal>
  )
}

function Zone() {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const count = useDataBatchStore((s) => s.selection.length)
  const running = useDataBatchStore((s) => s.running)
  const stopRequested = useDataBatchStore((s) => s.stopRequested)
  const progress = useDataBatchStore((s) => s.progress)
  const stop = useDataBatchStore((s) => s.stop)
  const done = useDataBatchStore((s) => Object.values(s.rows).filter((r) => r.state === 'done').length)
  const failed = useDataBatchStore((s) => Object.values(s.rows).filter((r) => r.state === 'failed').length)
  const b = useDataBatchStore.getState

  type Phase = 'empty' | 'picked' | 'running' | 'partial'
  const phase: Phase = running ? 'running' : failed > 0 ? 'partial' : count > 0 ? 'picked' : 'empty'
  const line =
    phase === 'running'
      ? t('dataFigureV2.batch.progress', { done: Math.min(progress.done + 1, progress.total), total: progress.total })
      : phase === 'partial'
        ? t('dataFigureV2.batch.partial', { done, failed })
        : phase === 'picked'
          ? t('dataFigureV2.batch.selected', { count })
          : t('dataFigureV2.batch.idle')

  let buttons: React.ReactNode
  if (phase === 'running') {
    buttons = (
      <Button variant="secondary" size="md" className="col-span-2" data-studio-batch-stop disabled={stopRequested} onClick={() => b().stopRun()}>
        {t('dataFigureV2.batch.stop')}
      </Button>
    )
  } else if (phase === 'partial') {
    // 写不进文件夹、整批停下：主按钮是「换个文件夹」；再试一次 = 接着画（含没轮到的）
    buttons = (
      <>
        {stop === 'unwritable' ? (
          <Button variant="primary" size="md" data-studio-batch-folder onClick={() => useProjectStore.getState().showPicker()}>
            {t('dataFigureV2.problem.otherFolder')}
          </Button>
        ) : (
          <Button variant="primary" size="md" data-studio-batch-assets disabled={done === 0} onClick={() => useUiStore.getState().setLeftTab('assets')}>
            {t('dataFigureV2.batch.showDone')}
          </Button>
        )}
        <Button variant="secondary" size="md" data-studio-batch-retry onClick={() => void b().run(stop === 'unwritable' ? 'all' : 'failed')}>
          {t('dataFigureV2.problem.retry')}
        </Button>
      </>
    )
  } else {
    buttons = (
      <>
        <Button variant="primary" size="md" data-studio-batch-run disabled={count < 2} onClick={() => void b().run('all')}>
          {t('dataFigureV2.batch.draw')}
        </Button>
        <Button
          variant="secondary"
          size="md"
          data-studio-batch-merge
          disabled={count < 2 || count > MAX_MERGE_TABLES}
          // 超过上限：禁用，title 用后端那一句（errors 里 `tables_limit_exceeded`，带 max）
          title={count > MAX_MERGE_TABLES ? fm(backendCodeMsg('tables_limit_exceeded', { max: MAX_MERGE_TABLES }, '')) : undefined}
          onClick={() => b().merge()}>
          {t('dataFigureV2.batch.merge')}
        </Button>
      </>
    )
  }
  return (
    <div data-studio-batch-zone data-phase={phase} className="flex h-[4.75rem] flex-col justify-center gap-2 border-b border-border pb-px pl-3 pr-1.5">
      <p className="h-4 truncate text-sm leading-4 text-ink-3 tabular-nums" role="status">
        {line}
      </p>
      <div className="grid grid-cols-2 gap-2">{buttons}</div>
    </div>
  )
}
