import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, IconButton } from '@/components/ui/Button'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { Notice } from '@/components/ui/Notice'
import { ProgressBar } from '@/components/ui/ProgressBar'
import { DURATION, drawerMotion, usePresence } from '@/lib/motion'
import { useFormatMessage } from '@/i18n/react'
import '@/lib/dataFigureHost'
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { AskScene } from './AskScene'
import { MixedCellsDialog } from './MixedCellsDialog'
import { PickScene } from './PickScene'
import { DrawnScene, NothingScene, ProblemScene, WorkingScene } from './ResultScenes'
import { TablePreview } from './TablePreview'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea,select,[tabindex]:not([tabindex="-1"])'

/**
 * 数据预览浮层：抽屉里点一个文件，从工作面板左缘（贴着抽屉）滑出一块盖在画布上的面板，右侧仍露出画布；
 * 依次承载预览（含点一行改每列的名字）→ 选择题 → 选画法 → 出图中 → 失败结局，混了文字的弹窗照旧是弹窗。
 * Esc、点浮层外（抽屉不算）、右上角 × 都能关；打开时焦点进浮层并陷在里面，关闭时还给打开它的那一行。
 * 窄屏（抽屉本身是覆盖式）：浮层占满工作面板宽度、抽屉自动收起，关浮层后抽屉回来。
 * 出图成功：浮层关闭，新图照旧在真编辑器里打开（`dataFigureStore.goEditor`）。
 */
export function DataOverlay({ narrow }: { narrow: boolean }) {
  const { t } = useTranslation('ai')
  const fm = useFormatMessage()
  const open = useDataFigureStore((s) => s.overlay)
  const selected = useDataFigureStore((s) => s.selected)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const scene = useDataFigureStore((s) => s.scene)
  const st = useDataFigureStore.getState
  const { mounted, state } = usePresence(open, DURATION.base)
  const root = useRef<HTMLDivElement>(null)
  const returnTo = useRef<HTMLElement | null>(null)
  const autoClosedDrawer = useRef(false)

  // 焦点：打开时记下此刻的焦点（抽屉里那一行）并移进浮层，关闭时还回去（行没了就回轨道上的「数据」钮）
  useEffect(() => {
    if (!open) return
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const id = window.setTimeout(() => root.current?.focus(), 0)
    return () => {
      window.clearTimeout(id)
      const back = returnTo.current
      const target = back && back.isConnected ? back : document.querySelector<HTMLElement>('[data-rail="data"]')
      target?.focus()
    }
  }, [open])

  // 窄屏：抽屉自动收起，关了浮层再回来
  useEffect(() => {
    if (!narrow) return
    const ui = useUiStore.getState()
    if (open) {
      if (ui.leftOpen) {
        autoClosedDrawer.current = true
        ui.toggleLeft()
      }
    } else if (autoClosedDrawer.current) {
      autoClosedDrawer.current = false
      if (!useUiStore.getState().leftOpen) useUiStore.getState().toggleLeft()
    }
  }, [open, narrow])

  // Esc / 点浮层外
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (st().scene.kind === 'mixed' || document.querySelector('[role="dialog"][data-state="open"]:not([data-data-overlay])')) return // 弹窗自己收
      e.preventDefault()
      st().closeOverlay()
    }
    const onDown = (e: PointerEvent) => {
      const el = e.target as Element | null
      if (!el || root.current?.contains(el)) return
      if (el.closest('[data-left-drawer],[role="dialog"]:not([data-data-overlay]),[data-radix-popper-content-wrapper],[data-rail="data"]')) return
      st().closeOverlay()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown, true)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown, true)
    }
  }, [open, st])

  // 焦点陷阱：Tab 在浮层里转圈
  const trap = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || !root.current) return
    const items = [...root.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.closest('[inert]'))
    if (items.length === 0) {
      e.preventDefault()
      return
    }
    const first = items[0]!
    const last = items[items.length - 1]!
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === root.current)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }

  if (!mounted) return null
  const width = narrow ? '100%' : 'min(720px, calc(100% - 96px))'
  const motion = drawerMotion({ state, overlay: true, width: 720, side: 'left' })
  const ovState = selected ? overviews[overviewKeyOf(selected, headerRows)] : undefined
  const ov = ovState?.data ?? null
  const loadingOv = ovState?.status === 'loading'
  const busy = scene.kind === 'proposing' || scene.kind === 'drawing'
  const toFolderPicker = () => useProjectStore.getState().showPicker()

  const body = (() => {
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
        return <ProblemScene problem={scene.problem} onRetry={() => void st().retry()} onBack={st().back} onChooseFolder={toFolderPicker} />
      default:
        return selected ? (
          <div className="flex w-full max-w-[720px] flex-col gap-5" data-studio-browse>
            {ovState?.status === 'error' && !ov ? (
              <Notice tone="neutral" title={ovState.error ? fm(ovState.error) : t('dataFigureV2.preview.failed')} action={<Button variant="ghost" size="sm" onClick={() => st().select(selected)}>{t('dataFigureV2.problem.retry')}</Button>} />
            ) : !ov ? (
              <ProgressBar pct={null} label={t('dataFigureV2.working.reading')} className="w-56" />
            ) : (
              <TablePreview ov={ov} loading={loadingOv} picked={headerRows[selected.path] != null} onHeaderRow={(line) => st().setHeaderRow(selected.path, selected.sheet, line)} />
            )}
            {/* 只有认准了、读得了的表才有「开始」：没认准的先点哪一行是每列的名字，读不了的没有下一步 */}
            {ov?.status === 'ok' && (
              <div>
                <Button variant="primary" size="lg" data-studio-start disabled={loadingOv || busy} onClick={() => void st().start()}>
                  {t('dataFigureV2.start')}
                </Button>
              </div>
            )}
          </div>
        ) : null
    }
  })()

  return (
    <div
      ref={root}
      role="dialog"
      aria-modal="false"
      aria-label={selected ? baseName(selected.path) : t('dataFigureV2.list.heading')}
      tabIndex={-1}
      data-studio
      data-data-overlay
      onKeyDown={trap}
      {...motion}
      style={{ ...motion.style, width, left: 0 }}
      className={`absolute inset-y-0 z-overlay flex min-w-0 flex-col bg-canvas shadow-pop outline-none ${motion.className}`}
    >
      <div className="flex h-11 shrink-0 items-center gap-2 pl-6 pr-2">
        <h2 className="type-heading min-w-0 flex-1 truncate" title={selected?.path}>
          {selected ? baseName(selected.path) : ''}
          {selected?.sheet ? <span className="text-ink-3"> · {selected.sheet}</span> : null}
          {ov?.status === 'ok' && ov.rows != null && ov.columns != null ? (
            <span className="text-base font-normal text-ink-3" data-studio-dims>
              {' · '}
              {t('dataFigureV2.list.fileMeta', { rows: ov.rows, columns: ov.columns })}
            </span>
          ) : null}
        </h2>
        <IconButton label={t('dataFigureV2.overlay.close')} data-studio-close onClick={() => st().closeOverlay()}>
          <X size={ICON_SIZE.md} />
        </IconButton>
      </div>
      <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto px-6 pb-6 pt-2" data-studio-stage>
        {body}
      </div>
      {scene.kind === 'mixed' && <MixedCellsDialog columns={scene.columns} onCancel={st().back} onContinue={(picks) => void st().answerCells(picks)} />}
    </div>
  )
}
