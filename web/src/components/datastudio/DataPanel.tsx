import { useTranslation } from 'react-i18next'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import type { CellMode } from '@/lib/dataFigure'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)

/**
 * 右侧面板在临时标签激活时显示的这份数据：文件名、行列数；有非数字格时一行「N 格不是数字」+ 下拉（不画这 N 行 / 只用里面的数字）；
 * 底部一行「原文件不会改。」。改任何一项都重新出候选。横轴 / 纵轴 / 分组三个下拉等后端把列角色放进响应（`dataFigureAdapter.rolesFromResponse`）再接。
 */
export function DataPanel() {
  const { t } = useTranslation('ai')
  const selected = useDataFigureStore((s) => s.selected)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const mixed = useDataFigureStore((s) => s.mixed)
  const busy = useDataFigureStore((s) => s.drawingId != null)
  if (!selected) return null
  const ov = overviews[overviewKeyOf(selected, headerRows)]?.data ?? null
  const rows = ov?.status === 'ok' ? ov.rows : null
  const cols = ov?.status === 'ok' ? ov.columns : null
  const count = mixed ? mixed.columns.reduce((n, c) => n + c.non_numeric, 0) : 0
  return (
    <div data-data-panel className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3 pt-2">
      <header className="flex flex-col">
        <p className="flex h-6 items-center type-caption">{t('dataFigureV2.list.heading')}</p>
        <h2 className="type-heading min-w-0 break-words py-1" title={selected.path}>
          {baseName(selected.path)}
          {selected.sheet ? <span className="font-normal text-ink-3"> · {selected.sheet}</span> : null}
        </h2>
        {rows != null && cols != null && (
          <p data-data-panel-meta className="mb-3 text-sm text-ink-2 tabular-nums">
            {t('dataFigureV2.list.fileMeta', { rows, columns: cols })}
          </p>
        )}
      </header>
      {mixed && count > 0 && (
        <>
          <div className="my-2 h-px bg-border" />
          <Row label={t('dataFigureV2.panel.mixed', { count })} labelWidth="auto">
            <Select<CellMode>
              value={mixed.mode}
              disabled={busy}
              ariaLabel={t('dataFigureV2.panel.mixed', { count })}
              onChange={(m) => useDataFigureStore.getState().setMixedMode(m)}
              options={[
                { value: 'skip', label: t('dataFigureV2.option.skip', { count }) },
                { value: 'extract', label: t('dataFigureV2.option.extract') },
              ]}
            />
          </Row>
        </>
      )}
      <p className="mt-auto pt-4 text-xs text-ink-3">{t('dataFigureV2.panel.note')}</p>
    </div>
  )
}
