import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { IconButton } from '@/components/ui/Button'
import { ChevronDown, ChevronRight, File, FileExclamationPoint, Folder, LayoutGrid, Layers2 } from '@/components/ui/icons'
import { ICON_SIZE, IconSlot } from '@/components/ui/Icon'
import { listRowClass, rowMetaClass } from '@/components/ui/listRow'
import { MiddleEllipsis } from '@/components/ui/MiddleEllipsis'
import { TreeBranch } from '@/components/ui/TreeBranch'
import { cn } from '@/lib/utils'
import { sameTable, withOverview, type DataFileRow, type DataRow, type RowStatus, type TableRef } from '@/lib/dataFigure'
import type { AiDataOverview } from '@/lib/api'

/** 侧栏宽度：w-75 = 300px */
export const DATA_LIST_WIDTH_CLASS = 'w-75'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
const dirName = (p: string): string => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')

/**
 * 左列「数据」：这个文件夹里的数据文件，按所在目录分组。每行 = 一张表：文件名 + 一行灰字（行数 · 列数 / 没认准 / 读不了 / 没读）。
 * XLSX 一行一个文件，可展开看工作表（每张表自己一行，点它看预览）。单击一行 = 舞台里看预览。
 * 行状态全由后端的清单给：这里不判断、不编造读取进度。选择器认 `data-studio-*`，**不把文件名放进 data-*（诊断轨迹会记 data 属性）**。
 */
export function DataList({
  rows,
  rootName,
  selected,
  overviews,
  onSelect,
}: {
  rows: DataFileRow[]
  /** 根目录那一组的名字（项目文件夹名） */
  rootName: string
  selected: TableRef | null
  /** 用户改过表头行后，这一行说重读后的行列数（键 = 路径） */
  overviews: Record<string, AiDataOverview | undefined>
  onSelect: (ref: TableRef) => void
}) {
  const { t } = useTranslation('ai')
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const dirs = [...new Set(rows.map((r) => dirName(r.path)))]
  const statusText = (s: RowStatus): string => {
    switch (s.kind) {
      case 'table':
        return t('dataFigureV2.list.fileMeta', { rows: s.rows, columns: s.columns })
      case 'unsure':
        return t('dataFigureV2.list.unsure')
      case 'unreadable':
        return t('dataFigureV2.list.unreadable')
      default:
        return t('dataFigureV2.list.unread')
    }
  }
  const rowStatus = (r: DataRow): RowStatus => withOverview(r, overviews[r.ref.path])
  let index = -1
  return (
    <aside
      data-studio-data
      aria-label={t('dataFigureV2.list.heading')}
      className={cn('flex h-full shrink-0 flex-col border-r border-border bg-surface', DATA_LIST_WIDTH_CLASS)}
    >
      <div className="flex h-11 shrink-0 items-center px-4">
        <h2 className="type-section text-ink-3">{t('dataFigureV2.list.heading')}</h2>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pb-3">
        {dirs.map((dir) => (
          <section key={dir} data-studio-folder aria-label={dir || rootName}>
            <div className="flex h-6 items-center gap-2 px-4 text-ink-3">
              <IconSlot className="w-4">
                <Folder size={ICON_SIZE.sm} aria-hidden />
              </IconSlot>
              <span className="type-section min-w-0 flex-1 truncate" title={dir || rootName}>
                {dir || rootName}
              </span>
            </div>
            <ul className="flex flex-col gap-0.5">
              {rows
                .filter((r) => dirName(r.path) === dir)
                .map((f) => {
                  index += 1
                  const open = expanded.has(f.path)
                  const only = f.rows[0]
                  const st = !f.workbook && only ? rowStatus(only) : null
                  const isSel = !f.workbook && only ? sameTable(selected, only.ref) : false
                  const Icon = f.workbook ? Layers2 : st?.kind === 'unreadable' ? FileExclamationPoint : st?.kind === 'table' ? LayoutGrid : File
                  const lead = (
                    <span className="mt-0.5 flex h-5 w-4 shrink-0 items-center justify-center self-start">
                      <IconSlot className="text-ink-3">
                        <Icon size={ICON_SIZE.sm} aria-hidden />
                      </IconSlot>
                    </span>
                  )
                  const nameBox = (
                    <>
                      <MiddleEllipsis text={baseName(f.path)} tail={8} className="min-w-0 leading-5" />
                      <span className={cn(rowMetaClass(isSel), 'flex h-4 items-center gap-1')}>
                        {f.workbook
                          ? f.unread
                            ? t('dataFigureV2.list.unread')
                            : t('dataFigureV2.list.sheets', { count: f.rows.length })
                          : st
                            ? statusText(st)
                            : ''}
                      </span>
                    </>
                  )
                  return (
                    <Fragment key={f.path}>
                    <li data-studio-file data-studio-index={index} data-kind={f.workbook ? 'workbook' : (st?.kind ?? 'unread')}>
                      <div className={cn(listRowClass({ size: 'md', selected: isSel }), 'mx-2 gap-2 px-2', !isSel && 'hover:bg-surface-hover')}>
                        {lead}
                        <button
                          type="button"
                          data-studio-file-open
                          aria-pressed={f.workbook ? undefined : isSel}
                          aria-expanded={f.workbook ? open : undefined}
                          onClick={() => {
                            if (f.workbook && f.rows.length > 0)
                              setExpanded((s) => {
                                const n = new Set(s)
                                if (n.has(f.path)) n.delete(f.path)
                                else n.add(f.path)
                                return n
                              })
                            else onSelect((only ?? { ref: { path: f.path, sheet: null } }).ref)
                          }}
                          className="flex min-w-0 flex-1 flex-col rounded-md py-0.5 text-left outline-none focus-visible:focus-ring-inset"
                        >
                          {nameBox}
                        </button>
                        {f.workbook && f.rows.length > 0 && (
                          <span className="flex min-w-6 shrink-0 items-center justify-end">
                            <IconButton
                              label={t(open ? 'dataFigureV2.list.collapse' : 'dataFigureV2.list.expand')}
                              iconSize="xs"
                              tabIndex={-1}
                              aria-expanded={open}
                              onClick={() =>
                                setExpanded((s) => {
                                  const n = new Set(s)
                                  if (n.has(f.path)) n.delete(f.path)
                                  else n.add(f.path)
                                  return n
                                })
                              }
                            >
                              {open ? <ChevronDown size={ICON_SIZE.xs} /> : <ChevronRight size={ICON_SIZE.xs} />}
                            </IconButton>
                          </span>
                        )}
                      </div>
                    </li>
                    {f.workbook && f.rows.length > 0 && (
                      <TreeBranch open={open}>
                        {f.rows.map((sh) => {
                          const s = rowStatus(sh)
                          const on = sameTable(selected, sh.ref)
                          return (
                            <li key={sh.ref.sheet} data-studio-sheet data-kind={s.kind}>
                              <button
                                type="button"
                                data-studio-file-open
                                aria-pressed={on}
                                onClick={() => onSelect(sh.ref)}
                                className={cn(listRowClass({ size: 'sm', selected: on }), 'ml-10 mr-2 w-[calc(100%-3rem)] gap-2 px-2 text-left text-sm')}
                              >
                                <span className="min-w-0 flex-1 truncate">{sh.ref.sheet}</span>
                                <span className={cn(rowMetaClass(on), 'shrink-0')}>{statusText(s)}</span>
                              </button>
                            </li>
                          )
                        })}
                      </TreeBranch>
                    )}
                    </Fragment>
                  )
                })}
            </ul>
          </section>
        ))}
      </div>
    </aside>
  )
}
