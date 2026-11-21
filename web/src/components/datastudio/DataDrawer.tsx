import { useEffect, useMemo } from 'react'
import { hasUsableAi } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { requestConnectAi } from '@/store/connectAiStore'
import { useCapsWatch } from '@/components/settings/connectAi/useCapsWatch'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Sparkles } from '@/components/ui/icons'
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
import { DataBatchActions } from './DataBatchActions'
import { DataList } from './DataList'

/**
 * 左栏「数据」抽屉（`LeftTab` 'data'，和「素材」「大纲」一样走 `railClick`）：这个文件夹里的数据文件树。
 * 点一个文件 = 在画布标签栏开（或复用）一个临时标签（`DataDraft`）并开始出候选，抽屉与画布文档都不动。抽屉里不加说明文字。
 */
function DataDrawerBody({ floatInset }: { floatInset: boolean }) {
  const { t } = useTranslation('ai')
  const { t: tc } = useTranslation('common')
  const files = useDataFigureStore((s) => s.files)
  const selected = useDataFigureStore((s) => s.selected)
  const group = useDataFigureStore((s) => s.group)
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
      running,
      onToggle: (ref: TableRef) => useDataBatchStore.getState().toggle(ref),
      onRange: (ref: TableRef, visible: TableRef[]) => useDataBatchStore.getState().selectRange(ref, visible),
      onClear: () => useDataBatchStore.getState().clearSelection(),
    }),
    [picked, pickMode, rowStates, running],
  )

  // 清单变化后能画的表少于 2 份（按钮随之消失）：退出多选态并清选择，别让状态挂着；进行中不动（store 里 running 时不处理），清单没读好时不判
  useEffect(() => {
    if (files.status === 'ready' && tableCount < 2 && pickMode) useDataBatchStore.getState().setPickMode(false)
  }, [files.status, tableCount, pickMode])

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
    <div data-studio-data className="relative flex min-h-0 flex-1 flex-col">
      {tableCount >= 2 && (
        <DrawerHeaderActions>
          {/* 与右侧图钉同高（28px）的次按钮：进入多选态后文字换成「取消」（点它退出并清掉选择，Esc 同） */}
          <Button
            variant="secondary"
            size="sm"
            disabled={running}
            data-studio-pick-mode
            onKeyDown={(e) => {
              if (e.key === 'Escape' && pickMode && !running) {
                e.preventDefault()
                e.stopPropagation()
                useDataBatchStore.getState().setPickMode(false)
              }
            }}
            onClick={() => useDataBatchStore.getState().setPickMode(!pickMode)}
          >
            {pickMode ? tc('actions.cancel') : t('dataFigureV2.batch.pick')}
          </Button>
        </DrawerHeaderActions>
      )}
      <DataBatchActions open={pickMode} />
      <DataList
        floatInset={floatInset}
        rows={rows}
        // 多选态下清单里只有「已选」这一种高亮；合画的临时标签开着时也不单独亮第一份（也让漫游 tabindex 回到第一行）
        selected={pickMode || group ? null : selected}
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
    </div>
  )
}

/**
 * 「数据」抽屉：清单 + 底部居中一枚悬浮的「连接 AI」胶囊（没有可用的 AI 连接时一直显示，没有关闭按钮；抽屉里没有数据文件时也显示）。
 * 连接状态来源与助手面板空态同一个（`aiStore.caps` + `hasUsableAi`）：还没探测完（`caps` 为 null）不显示，宁可晚出现。
 * 胶囊绝对定位浮在抽屉之上、不占布局高度；清单底部留出等于它的内边距（`floatInset`），滚到底最后一行不会被盖住。
 * 连上之后对当前数据重跑一次候选的事在 `DataPanel`（它认「没连 → 连上」那一下）。
 */
export function DataDrawer() {
  const { t } = useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const unconnected = caps !== null && !hasUsableAi(caps)
  useEffect(() => {
    if (caps === null) void useAiStore.getState().loadCaps().catch(() => undefined)
  }, [caps])
  // 用户可能在终端里登录了订阅：回到前台刷新一次（连接弹窗开着时它自己 3 秒一刷）
  useCapsWatch(unconnected, { poll: false })
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <DataDrawerBody floatInset={unconnected} />
      {unconnected && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center px-3">
          <button
            type="button"
            data-data-connect
            title={t('dataFigureV2.suggest.connectHint')}
            onClick={() => requestConnectAi()}
            className="pointer-events-auto inline-flex h-9 max-w-full items-center gap-1.5 rounded-full bg-surface pl-3.5 pr-4 text-base font-medium text-ink shadow-pop outline-none hover:bg-surface-hover focus-visible:focus-ring"
          >
            <Sparkles size={ICON_SIZE.md} aria-hidden className="shrink-0 text-ink-2" />
            <span className="truncate">{t('dataFigureV2.connect.pill')}</span>
          </button>
        </div>
      )}
    </div>
  )
}
