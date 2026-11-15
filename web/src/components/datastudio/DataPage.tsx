import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Notice } from '@/components/ui/Notice'
import { ProgressBar } from '@/components/ui/ProgressBar'
import type { AiDataOverview } from '@/lib/api'
import { fileRows, sameTable } from '@/lib/dataFigure'
import { useFormatMessage } from '@/i18n/react'
import '@/lib/dataFigureHost' // 注册宿主（刷新 / 打开并运行 / 项目可写性），见该文件头
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { AskScene } from './AskScene'
import { DataList } from './DataList'
import { MixedCellsDialog } from './MixedCellsDialog'
import { PickScene } from './PickScene'
import { DrawnScene, NothingScene, ProblemScene, WorkingScene } from './ResultScenes'
import { TablePreview } from './TablePreview'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)

/**
 * 数据页：项目里的一个视图（和画布并列，不是第二个应用——外壳的顶栏 / 左轨 / 工作面板都是现成的，这里只填工作面板的内容）。
 * 左侧清单 = 这个文件夹里的数据文件；右边舞台按这一轮走到哪里换一屏：看预览 → 提问 / 混了文字 → 选画法 → 画 → 进编辑器。
 * 状态全在 `dataFigureStore`（按项目换代、请求序号挡旧）；这里只翻译，不判断。
 */
export function DataPage() {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const files = useDataFigureStore((s) => s.files)
  const selected = useDataFigureStore((s) => s.selected)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const scene = useDataFigureStore((s) => s.scene)
  const st = useDataFigureStore.getState

  // 进页时 `enter()` 已经发了清单请求；这里只在「还没开始」时兜一次（热重载 / 直接挂载）
  useEffect(() => {
    if (useDataFigureStore.getState().files.status === 'idle') void useDataFigureStore.getState().loadFiles()
  }, [])

  const entries = files.status === 'ready' || files.status === 'error' || files.status === 'loading' ? files.entries : null
  const rows = useMemo(() => (entries ? fileRows(entries) : []), [entries])
  // 用户改过表头行的那几个文件：清单里这一行说重读后的行列数
  const listOverviews = useMemo(() => {
    const out: Record<string, AiDataOverview | undefined> = {}
    for (const [path] of Object.entries(headerRows)) {
      const sheet = rows.find((r) => r.path === path)?.workbook ? selected?.sheet ?? null : null
      const ov = overviews[overviewKeyOf({ path, sheet }, headerRows)]
      if (ov?.data) out[path] = ov.data
    }
    return out
  }, [headerRows, overviews, rows, selected])

  const ovState = selected ? overviews[overviewKeyOf(selected, headerRows)] : undefined
  const ov = ovState?.data ?? null
  const loadingOv = ovState?.status === 'loading'
  const usable = !!ov && ov.status === 'ok' && !loadingOv
  const busy = scene.kind === 'proposing' || scene.kind === 'drawing'

  const leaveToFolderPicker = () => useProjectStore.getState().showPicker()

  const stage = (() => {
    switch (scene.kind) {
      case 'proposing':
      case 'drawing':
        return <WorkingScene kind={scene.kind} />
      case 'ask':
        return <AskScene code={scene.code} options={scene.options} unsure={scene.unsure} onAnswer={(o) => void st().answer(o)} onBack={st().back} />
      case 'pick':
        return (
          <PickScene
            items={scene.items}
            selectedId={scene.selectedId}
            source={baseName(scene.sourcePath)}
            onChoose={st().choose}
            onDraw={() => void st().draw()}
            onBack={st().back}
          />
        )
      case 'nothing':
        return <NothingScene reasons={scene.reasons} onBack={st().back} />
      case 'drawn':
        return <DrawnScene notes={scene.notes} onOpen={() => void st().openDrawn()} />
      case 'problem':
        return <ProblemScene problem={scene.problem} onRetry={() => void st().retry()} onBack={st().back} onChooseFolder={leaveToFolderPicker} />
      default:
        return selected ? (
          <div className="flex w-full max-w-[720px] flex-col gap-5" data-studio-browse>
            <h2 className="type-heading min-w-0 truncate" title={selected.path}>
              {baseName(selected.path)}
              {selected.sheet ? <span className="text-ink-3"> · {selected.sheet}</span> : null}
              {ov?.status === 'ok' && ov.rows != null && ov.columns != null ? (
                <span className="text-base font-normal text-ink-3" data-studio-dims>
                  {' · '}
                  {t('dataFigureV2.list.fileMeta', { rows: ov.rows, columns: ov.columns })}
                </span>
              ) : null}
            </h2>
            {ovState?.status === 'error' && !ov ? (
              <Notice tone="neutral" title={ovState.error ? fm(ovState.error) : t('dataFigureV2.preview.failed')} action={<Button variant="ghost" size="sm" onClick={() => st().select(selected)}>{t('dataFigureV2.problem.retry')}</Button>} />
            ) : !ov ? (
              <ProgressBar pct={null} label={t('dataFigureV2.working.reading')} className="w-56" />
            ) : (
              <TablePreview ov={ov} loading={loadingOv} onHeaderRow={(line) => st().setHeaderRow(selected.path, selected.sheet, line)} />
            )}
            {/* 只有认准了、读得了的表才有「开始」：没认准的先点哪一行是每列的名字，读不了的没有下一步 */}
            {ov?.status === 'ok' && (
              <div>
                <Button variant="primary" size="lg" data-studio-start disabled={!usable || busy} onClick={() => void st().start()}>
                  {t('dataFigureV2.start')}
                </Button>
              </div>
            )}
          </div>
        ) : (
          <p className="m-auto text-base text-ink-3" data-studio-empty-stage>
            {t('dataFigureV2.stage.pickOne')}
          </p>
        )
    }
  })()

  const listBody = (() => {
    if (files.status === 'error' && !entries)
      return (
        <div className="p-4">
          <Notice
            tone="neutral"
            title={t('dataFigureV2.list.loadFailed')}
            action={
              <Button variant="ghost" size="sm" onClick={() => void st().loadFiles()}>
                {t('dataFigureV2.problem.retry')}
              </Button>
            }
          />
        </div>
      )
    if (files.status === 'ready' && rows.length === 0)
      return (
        <div className="flex flex-col items-start gap-3 p-4" data-studio-no-files>
          <p className="text-base text-ink-2">{t('dataFigureV2.list.empty')}</p>
          <Button variant="primary" size="md" onClick={leaveToFolderPicker}>
            {t('dataFigureV2.problem.otherFolder')}
          </Button>
        </div>
      )
    return null
  })()

  return (
    <div data-studio className="flex min-h-0 min-w-0 flex-1">
      {listBody ? (
        <aside data-studio-data aria-label={t('dataFigureV2.list.heading')} className="flex h-full w-75 shrink-0 flex-col border-r border-border bg-surface">
          <div className="flex h-11 shrink-0 items-center px-4">
            <h2 className="type-section text-ink-3">{t('dataFigureV2.list.heading')}</h2>
          </div>
          {listBody}
        </aside>
      ) : (
        <DataList
          rows={rows}
          selected={selected}
          overviews={listOverviews}
          onSelect={(ref) => {
            if (!sameTable(selected, ref) || scene.kind !== 'browse') st().select(ref)
          }}
        />
      )}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-canvas px-8 py-6" data-studio-stage>
        {files.status === 'loading' && !entries && <ProgressBar pct={null} label={t('dataFigureV2.working.reading')} className="w-56" />}
        {stage}
      </div>
      {scene.kind === 'mixed' && (
        <MixedCellsDialog columns={scene.columns} onCancel={st().back} onContinue={(picks) => void st().answerCells(picks)} />
      )}
    </div>
  )
}
