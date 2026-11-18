import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { useProjectStore } from '@/store/projectStore'

/**
 * 数据抽屉底部的一条：多选 ≥ 2 份时出现，主按钮「各画一张」+ 灰字份数；进行中按钮禁用、写「正在画 2 / 3」；
 * 有没画成的（它们少于 2 份时这一条也在）按钮是「再试一次」，只重做没成的；项目文件夹写不进、整批停下时按钮是「换个文件夹」。
 * 少于 2 份又没有要说的，这一条不存在（单份走临时标签）。
 */
export function DataBatchBar() {
  const { t } = useTranslation('ai')
  const { t: tw } = useTranslation('workspace')
  const count = useDataBatchStore((s) => s.selection.length)
  const running = useDataBatchStore((s) => s.running)
  const progress = useDataBatchStore((s) => s.progress)
  const stop = useDataBatchStore((s) => s.stop)
  const failed = useDataBatchStore((s) => Object.values(s.rows).some((r) => r.state === 'failed'))
  const visible = running || count >= 2 || failed || stop != null
  if (!visible) return null
  const label = running
    ? t('dataFigureV2.batch.progress', { done: Math.min(progress.done + 1, progress.total), total: progress.total })
    : stop === 'unwritable'
      ? t('dataFigureV2.problem.otherFolder')
      : failed
        ? t('dataFigureV2.problem.retry')
        : t('dataFigureV2.batch.draw')
  return (
    <div data-studio-batch-bar className="flex shrink-0 items-center gap-2 border-t border-border px-3 py-2">
      <Button
        variant="primary"
        size="md"
        data-studio-batch-run
        disabled={running}
        onClick={() => (stop === 'unwritable' ? useProjectStore.getState().showPicker() : void useDataBatchStore.getState().run())}
      >
        {label}
      </Button>
      {count > 0 && <span className="min-w-0 truncate text-xs text-ink-3 tabular-nums">{tw('dataDrawer.count', { count })}</span>}
    </div>
  )
}
