import { useTranslation } from 'react-i18next'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import { overviewKeyOf, useDataFigureStore, type RoleKey } from '@/store/dataFigureStore'
import type { CellMode } from '@/lib/dataFigure'
import { diffSuggestion } from '@/lib/dataUnderstanding'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
/** 「不要横轴 / 不分组」的取值：不会和任何列名撞（列名里不会有控制字符） */
const NONE = '\u0000none'

/**
 * 右侧面板在临时标签激活时显示的这份数据：文件名、行列数；横轴 / 纵轴 / 分组三个下拉（值与可选的列来自响应的 `roles`，
 * 某个角色没有可选的列就不显示那一行；横轴多一项「不要横轴」，分组多一项「不分组」）；有非数字格时一行「N 格不是数字」+ 下拉
 * （不画这 N 行 / 只用里面的数字，多列时对每一列都带上同一个处理方式）；底部一行「原文件不会改。」。
 * 改任何一项都带着 `answers` 重新出候选；这些下拉始终挂在这里（重新出候选期间 `roles` / `mixed` 不清），不会闪。
 */
export function DataPanel() {
  const { t } = useTranslation('ai')
  const selected = useDataFigureStore((s) => s.selected)
  const group = useDataFigureStore((s) => s.group)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const mixed = useDataFigureStore((s) => s.mixed)
  const roles = useDataFigureStore((s) => s.roles)
  const busy = useDataFigureStore((s) => s.drawingId != null)
  const understanding = useDataFigureStore((s) => s.understanding)
  const suggestion = useDataFigureStore((s) => s.suggestion)
  if (!selected) return null
  const ov = overviews[overviewKeyOf(selected, headerRows)]?.data ?? null
  // 合画：概览只有第一份的，行列数不代表整组，不显示
  const rows = !group && ov?.status === 'ok' ? ov.rows : null
  const cols = !group && ov?.status === 'ok' ? ov.columns : null
  const count = mixed ? mixed.columns.reduce((n, c) => n + c.nonNumeric, 0) : 0
  const pick = (key: RoleKey) => (v: string) => useDataFigureStore.getState().setRole(key, v === NONE ? 'none' : v)
  // 模型的建议和此刻不同、但用户已经动过下拉：标题行右侧一枚胶囊，点了才套用（建议与此刻相同时 diff 为空，什么都不显示）
  const diff = suggestion ? diffSuggestion(suggestion, roles, useDataFigureStore.getState().headerRowUsed, headerRows[selected.path] != null, useDataFigureStore.getState().answers) : null
  const diffTitle = diff
    ? (['x', 'y', 'group'] as const)
        .filter((k) => diff.picks[k] !== undefined)
        .map((k) => {
          const v = diff.picks[k]!
          return `${t(`dataFigureV2.panel.${k}`)} ${Array.isArray(v) ? v.join('、') : v === 'none' ? t(k === 'x' ? 'dataFigureV2.panel.noX' : 'dataFigureV2.panel.noGroup') : v}`
        })
        .join(' · ')
    : ''
  const asOptions = (list: string[], none?: string) => [...(none ? [{ value: NONE, label: none }] : []), ...list.map((c) => ({ value: c, label: c }))]
  return (
    <div data-data-panel className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3 pt-2">
      <header className="flex flex-col">
        <p className="flex h-6 items-center justify-between gap-2 type-caption">
          <span>{t('dataFigureV2.list.heading')}</span>
          {diff && !busy ? (
            <button
              type="button"
              data-data-suggest
              title={diffTitle || undefined}
              onClick={() => useDataFigureStore.getState().applySuggestion()}
              className="h-6 shrink-0 rounded-full border border-border bg-surface-hover px-2.5 text-xs font-medium text-ink outline-none hover:bg-border focus-visible:focus-ring"
            >
              {t('dataFigureV2.suggest.apply')}
            </button>
          ) : understanding ? (
            <span data-data-reading className="text-xs font-normal text-ink-3">
              {t('dataFigureV2.suggest.reading')}
            </span>
          ) : null}
        </p>
        <h2 className="type-heading min-w-0 break-words py-1" title={group ? undefined : selected.path}>
          {group ? t('dataFigureV2.batch.groupTitle', { count: group.length }) : baseName(selected.path)}
          {!group && selected.sheet ? <span className="font-normal text-ink-3"> · {selected.sheet}</span> : null}
        </h2>
        {rows != null && cols != null && (
          <p data-data-panel-meta className="mb-3 text-sm text-ink-2 tabular-nums">
            {t('dataFigureV2.list.fileMeta', { rows, columns: cols })}
          </p>
        )}
      </header>
      {roles && (
        <div data-data-roles className="flex flex-col gap-1">
          {roles.options.x.length > 0 && (
            <div data-data-role="x" className="contents">
              <Row label={t('dataFigureV2.panel.x')} labelWidth={LABEL_W}>
                <Select
                  value={roles.x ?? NONE}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.x')}
                  onChange={pick('x')}
                  options={asOptions(roles.options.x, t('dataFigureV2.panel.noX'))}
                />
              </Row>
            </div>
          )}
          {roles.options.y.length > 0 && (
            <div data-data-role="y" className="contents">
              <Row label={t('dataFigureV2.panel.y')} labelWidth={LABEL_W}>
                <Select
                  value={roles.y ?? ''}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.y')}
                  onChange={pick('y')}
                  options={asOptions(roles.options.y)}
                  // 纵轴多列（每列一条线 / 把列当分组）：收起时把几列并列写出来；下拉里仍是单选，选了就收回一列
                  triggerLabel={roles.yAll.length > 1 ? roles.yAll.join('、') : undefined}
                />
              </Row>
            </div>
          )}
          {roles.options.group.length > 0 && (
            <div data-data-role="group" className="contents">
              <Row label={t('dataFigureV2.panel.group')} labelWidth={LABEL_W}>
                <Select
                  value={roles.group ?? NONE}
                  disabled={busy}
                  ariaLabel={t('dataFigureV2.panel.group')}
                  onChange={pick('group')}
                  options={asOptions(roles.options.group, t('dataFigureV2.panel.noGroup'))}
                />
              </Row>
            </div>
          )}
        </div>
      )}
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

const LABEL_W = 44
