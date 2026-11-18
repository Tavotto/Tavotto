import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Notice } from '@/components/ui/Notice'
import { ProgressBar } from '@/components/ui/ProgressBar'
import type { AiDataOverview } from '@/lib/api'
import { fileRows, sameTable, tableKey, type TableRef } from '@/lib/dataFigure'
import '@/lib/dataFigureHost' // 注册宿主（开抽屉 / 刷新 / 打开并运行 / 项目可写性），见该文件头
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { useDataBatchStore } from '@/store/dataBatchStore'
import { DrawerHeaderActions } from '../left/DrawerHeader'
import { withOverview } from '@/lib/dataFigure'
import { DataBatchBar } from './DataBatchBar'
import { DataList } from './DataList'

/**
 * 左栏「数据」抽屉（`LeftTab` 'data'，和「素材」「大纲」一样走 `railClick`）：这个文件夹里的数据文件树。
 * 点一个文件 = 在画布标签栏开（或复用）一个临时标签（`DataDraft`）并开始出候选，抽屉与画布文档都不动。抽屉里不加说明文字。
 */
export function DataDrawer() {
  const { t } = useTranslation('ai')
  const files = useDataFigureStore((s) => s.files)
  const selected = useDataFigureStore((s) => s.selected)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)

  // 抽屉开着时清单没读过就读一次（`LeftRail` 为了决定「数据」项出不出现已经读过的话这里什么都不做）
  useEffect(() => {
    if (useDataFigureStore.getState().files.status === 'idle') void useDataFigureStore.getState().loadFiles()
  }, [])

  const entries = files.status === 'ready' || files.status === 'error' || files.status === 'loading' ? files.entries : null
  const rows = useMemo(() => (entries ? fileRows(entries) : []), [entries])
  // 用户改过表头行的那几个文件：清单里这一行说重读后的行数
  const listOverviews = useMemo(() => {
    const out: Record<string, AiDataOverview | undefined> = {}
    for (const [path] of Object.entries(headerRows)) {
      const sheet = rows.find((r) => r.path === path)?.workbook ? selected?.sheet ?? null : null
      const ov = overviews[overviewKeyOf({ path, sheet }, headerRows)]
      if (ov?.data) out[path] = ov.data
    }
    return out
  }, [headerRows, overviews, rows, selected])

  // 多选（一次选多份、各画一张）：选了哪几份 / 每份的进度，状态按 tableKey 存在 `dataBatchStore`
  const picked = useDataBatchStore((s) => s.selection)
  const rowStates = useDataBatchStore((s) => s.rows)
  const pickMode = useDataBatchStore((s) => s.pickMode)
  const running = useDataBatchStore((s) => s.running)
  // 「选多份」按钮：至少有 2 份能画的表（状态是 table）才出现
  const tableCount = useMemo(() => rows.reduce((n, f) => n + f.rows.filter((r) => withOverview(r, listOverviews[r.ref.path]).kind === 'table').length, 0), [rows, listOverviews])
  const multi = useMemo(
    () => ({
      selected: new Set(picked.map(tableKey)),
      pickMode,
      rowStates,
      onToggle: (ref: TableRef) => useDataBatchStore.getState().toggle(ref),
      onRange: (ref: TableRef, visible: TableRef[]) => useDataBatchStore.getState().selectRange(ref, visible),
      onClear: () => useDataBatchStore.getState().clearSelection(),
    }),
    [picked, pickMode, rowStates],
  )

  if (files.status === 'error' && !entries)
    return (
      <div className="p-3" data-studio-data>
        <Notice
          tone="neutral"
          title={t('dataFigureV2.list.loadFailed')}
          action={
            <Button variant="ghost" size="sm" onClick={() => void useDataFigureStore.getState().loadFiles()}>
              {t('dataFigureV2.problem.retry')}
            </Button>
          }
        />
      </div>
    )
  if (files.status === 'ready' && rows.length === 0)
    return (
      <div className="flex flex-col items-start gap-3 p-3" data-studio-data data-studio-no-files>
        <p className="text-base text-ink-2">{t('dataFigureV2.list.empty')}</p>
        <Button variant="primary" size="md" onClick={() => useProjectStore.getState().showPicker()}>
          {t('dataFigureV2.problem.otherFolder')}
        </Button>
      </div>
    )
  if (!entries) return <ProgressBar pct={null} label={t('dataFigureV2.working.reading')} className="m-3 w-40" />
  return (
    <div data-studio-data className="flex min-h-0 flex-1 flex-col">
      {tableCount >= 2 && (
        <DrawerHeaderActions>
          <Button
            variant="ghost"
            size="sm"
            active={pickMode}
            aria-pressed={pickMode}
            disabled={running}
            data-studio-pick-mode
            onClick={() => useDataBatchStore.getState().setPickMode(!pickMode)}
          >
            {t('dataFigureV2.batch.pick')}
          </Button>
        </DrawerHeaderActions>
      )}
      <DataList
        rows={rows}
        selected={selected}
        overviews={listOverviews}
        multi={multi}
        onSelect={(ref) => {
          // 多选态下不清选择；否则开临时标签，并清掉多选（进行中不清），它也是 Shift+点选一段的起点
          const b = useDataBatchStore.getState()
          if (b.pickMode) {
            // 多选态下只有读不了的行会走到这里（表行由 DataList 当加选）：看一眼它的说明，不动选择
            useDataFigureStore.getState().select(ref)
            return
          }
          b.clearSelection()
          b.setAnchor(ref)
          useDataFigureStore.getState().select(ref)
        }}
        onFocusRow={(ref) => {
          // 临时标签激活着：方向键走到哪个文件，标签就换成哪个（像访达的快速查看）；出图中不动
          const st = useDataFigureStore.getState()
          if (st.draftActive && !st.drawingId && !sameTable(st.selected, ref)) st.select(ref)
        }}
        onConfirm={() => {
          // Enter：焦点交给候选网格（方向键移动、Enter 选）；还没有候选就不处理
          const first = document.querySelector<HTMLElement>('[data-data-draft] [data-candidate]')
          if (!first || first.hasAttribute('disabled')) return false
          first.focus()
          return true
        }}
      />
      <DataBatchBar />
    </div>
  )
}
