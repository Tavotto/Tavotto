import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, IconButton } from '@/components/ui/Button'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { DURATION, usePresence } from '@/lib/motion'
import { chromeFits, columnSample, hasNumericColumn, overlayCardPx, overlayTargetWidth, overlayWidthCss, tableKey } from '@/lib/dataFigure'
import { cn } from '@/lib/utils'
import { useFormatMessage } from '@/i18n/react'
import '@/lib/dataFigureHost'
import { overviewKeyOf, useDataFigureStore } from '@/store/dataFigureStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { AskScene } from './AskScene'
import { OverlayFooter, OverlayFooterContext } from './OverlayFooter'
import { MixedCellsDialog } from './MixedCellsDialog'
import { PickScene } from './PickScene'
import { DrawnScene, NothingScene, ProblemScene, WorkingScene } from './ResultScenes'
import { TablePreview } from './TablePreview'

const baseName = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea,select,[tabindex]:not([tabindex="-1"])'

/**
 * 数据预览浮层：抽屉里点一个文件，从抽屉边缘滑出一张**浮起的卡片**（离画布栏上 / 下 / 左各 8px、面板圆角、hairline + 弹窗级阴影，
 * 不压暗画布、不加遮罩，标签行不被盖住），宽度跟内容走（360–640，且不超过画布栏的 60%），右侧仍露出画布；
 * 三段结构：顶栏（文件名 · 行列数 · ×，高 44）/ 可滚动内容（上下边缘渐隐）/ 底栏（左次要信息，右主按钮）；
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

  // Enter = 卡片的主按钮（落在卡片自己或内容区上；按钮 / 链接 / 输入框自己处理 Enter）
  const confirmKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.defaultPrevented || e.nativeEvent.isComposing) return
    const el = e.target as HTMLElement
    if (el.closest('button,a,textarea,input[type="text"],[role="dialog"]:not([data-data-overlay])')) return
    const primary = root.current?.querySelector<HTMLButtonElement>('[data-studio-primary],[data-studio-start]')
    if (!primary || primary.disabled) return
    e.preventDefault()
    primary.click()
  }

  const [footerLeft, setFooterLeft] = useState<HTMLElement | null>(null)
  const [footerRight, setFooterRight] = useState<HTMLElement | null>(null)
  const [edges, setEdges] = useState({ top: false, bottom: false, left: false, right: false })
  const scroller = useRef<HTMLDivElement>(null)
  const measureEdges = () => {
    const el = scroller.current
    if (!el) return
    const top = el.scrollTop > 2
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2
    const left = el.scrollLeft > 2
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
    setEdges((e) => (e.top === top && e.bottom === bottom && e.left === left && e.right === right ? e : { top, bottom, left, right }))
  }
  useEffect(measureEdges)

  // 高度走「量出来再过渡」：内层按内容自然撑高（上限 = 画布栏高 − 16），外层卡片取它量到的高度并过渡。
  // 不用 `interpolate-size`：桌面壳（WebKit）不认，换屏时高度会直接跳
  const inner = useRef<HTMLDivElement>(null)
  const [avail, setAvail] = useState<number | null>(null)
  const [height, setHeight] = useState<number | null>(null)
  const [settled, setSettled] = useState(false)
  useLayoutEffect(() => {
    const host = root.current?.offsetParent
    if (!mounted || !(host instanceof HTMLElement) || typeof ResizeObserver === 'undefined') return
    const read = () => setAvail(Math.max(0, host.clientHeight - 16))
    read()
    const ro = new ResizeObserver(read)
    ro.observe(host)
    return () => ro.disconnect()
  }, [mounted])
  useLayoutEffect(() => {
    const el = inner.current
    if (!mounted || !el || typeof ResizeObserver === 'undefined') return
    const read = () => setHeight(Math.ceil(el.getBoundingClientRect().height))
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    // 第一次量完之后才开高度过渡：打开那一下卡片从无到有靠进场动画，不是长高
    const id = window.requestAnimationFrame(() => setSettled(true))
    return () => {
      ro.disconnect()
      window.cancelAnimationFrame(id)
    }
  }, [mounted])
  useEffect(() => {
    if (!mounted) setSettled(false)
  }, [mounted])

  // 换屏 / 关卡片后焦点没地方放（按下的按钮被换掉了）：收回卡片上，方向键 / Enter 接着能用；选画法屏落在选中的那一张上
  const sceneKind = scene.kind === 'drawing' && scene.pick ? 'pick' : scene.kind
  useEffect(() => {
    const el = root.current
    if (!open || !el) return
    const a = document.activeElement
    if (a && a !== document.body && a !== el && (el.contains(a) || a.closest('[data-left-drawer]'))) return
    const id = window.setTimeout(() => {
      const r = root.current
      if (!r) return
      const checked = r.querySelector<HTMLElement>('[data-studio-pick] input[type="radio"]:checked')
      ;(checked ?? r).focus()
    }, 0)
    return () => window.clearTimeout(id)
  }, [open, sceneKind])

  if (!mounted) return null
  const ovState = selected ? overviews[overviewKeyOf(selected, headerRows)] : undefined
  const ov = ovState?.data ?? null
  const loadingOv = ovState?.status === 'loading'
  const busy = scene.kind === 'proposing' || scene.kind === 'drawing'
  const toFolderPicker = () => useProjectStore.getState().showPicker()
  // 卡片目标宽度（要走 CSS 过渡，必须渲染前就知道）：预览按内容估，其余各屏固定
  const target = overlayTargetWidth(scene.kind, ov)
  // 高度：各屏都随内容，上限是画布栏全高（上下各留 8px），超出才在卡片里滚动
  const width = narrow ? undefined : overlayWidthCss(target)
  // 渐隐只出现在「还能滚」的那一侧：贴顶 / 贴左时那一侧不渐隐；列太多横向滚动时右缘渐隐就是「后面还有」的提示
  const fadeV = `linear-gradient(to bottom, ${edges.top ? 'transparent 0, black 20px' : 'black 0'}, ${edges.bottom ? 'black calc(100% - 24px), transparent 100%' : 'black 100%'})`
  const fadeH = `linear-gradient(to right, ${edges.left ? 'transparent 0, black 24px' : 'black 0'}, ${edges.right ? 'black calc(100% - 32px), transparent 100%' : 'black 100%'})`
  const maskImage = `${fadeV}, ${fadeH}`

  const body = (() => {
    switch (scene.kind) {
      case 'proposing':
      case 'drawing':
        if (scene.kind === 'drawing' && scene.pick) {
          // 出图中沿用选画法屏的版面：点「画这张」到进编辑器之间卡片尺寸不动
          return <PickScene items={scene.pick.items} selectedId={scene.pick.selectedId} source={baseName(scene.pick.sourcePath)} drawing onChoose={() => {}} onDraw={() => {}} onBack={() => {}} />
        }
        return <WorkingScene kind={scene.kind} thumb={scene.kind === 'drawing' ? scene.thumb : null} />
      case 'ask':
        return <AskScene code={scene.code} options={scene.options} unsure={scene.unsure} sampleOf={(c) => columnSample(ov, c)} onAnswer={(o) => void st().answer(o)} onBack={st().back} />
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
          <div className="flex flex-col gap-4" data-studio-browse>
            {/* 换文件时这一块按「哪张表 + 读到了没有」换键：新内容淡入，卡片的宽高同时过渡（卡片本身不关不开） */}
            <div key={`${tableKey(selected)}:${ov ? 'ready' : ovState?.status ?? 'loading'}`} className="animate-fade-in">
              {ovState?.status === 'error' && !ov ? (
                <div className="flex flex-col items-start gap-2">
                  <p className="text-base text-ink-2">{ovState.error ? fm(ovState.error) : t('dataFigureV2.preview.failed')}</p>
                  <Button variant="ghost" size="sm" onClick={() => st().select(selected)}>
                    {t('dataFigureV2.problem.retry')}
                  </Button>
                </div>
              ) : !ov ? (
                <TableSkeleton label={t('dataFigureV2.working.reading')} />
              ) : (
                <TablePreview ov={ov} loading={loadingOv} picked={headerRows[selected.path] != null} onHeaderRow={(line) => st().setHeaderRow(selected.path, selected.sheet, line)} />
              )}
            </div>
            {/* 只有认准了、读得了、有数值列的表才有主按钮：没认准的先点哪一行是每列的名字，读不了的没有下一步，没数值列的画不了（说清，不让人点了才失败） */}
            <OverlayFooter
              left={
                ov?.status === 'ok' && !hasNumericColumn(ov) ? (
                  <span className="text-xs text-ink-3" data-studio-no-numeric>
                    {t('dataFigureV2.preview.noNumeric')}
                  </span>
                ) : ov?.status === 'ok' && ov.truncated && ov.rows != null ? (
                  <span className="text-xs text-ink-3">{t('dataFigureV2.preview.shown', { shown: ov.preview_rows.length, total: ov.rows })}</span>
                ) : undefined
              }
              right={
                ov?.status === 'ok' && hasNumericColumn(ov) ? (
                  <Button variant="primary" size="lg" data-studio-start disabled={loadingOv || busy} onClick={() => void st().start()}>
                    {t('dataFigureV2.start')}
                  </Button>
                ) : undefined
              }
            />
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
      data-state={state}
      inert={state === 'closed'}
      onKeyDown={(e) => {
        trap(e)
        confirmKey(e)
      }}
      style={{
        ...(narrow ? {} : { width }),
        // 量到之前（首帧 / 没有 ResizeObserver 的环境）按内容自然高
        height: height ?? 'auto',
        maxHeight: 'calc(100% - 1rem)',
        // 高度只在量到第一次之后才过渡
        transitionProperty: settled ? 'width, height' : 'width',
      }}
      className={cn(
        'absolute left-2 top-2 z-overlay min-w-0 overflow-hidden rounded-panel bg-surface shadow-dialog-inset outline-none',
        'transition-[width,height] duration-base ease-standard',
        narrow && 'right-2',
        'data-[state=open]:animate-sheet-in data-[state=closed]:animate-sheet-out',
      )}
    >
      <div ref={inner} className="flex flex-col" style={{ maxHeight: avail ?? undefined }}>
      <div className="flex h-11 shrink-0 items-center gap-2 pl-5 pr-2">
        <h2 className="min-w-0 flex-1 truncate text-base font-medium" title={selected?.path}>
          {selected ? baseName(selected.path) : ''}
          {selected?.sheet ? <span className="font-normal text-ink-3"> · {selected.sheet}</span> : null}
          {ov?.status === 'ok' && ov.rows != null && ov.columns != null ? (
            <span className="ml-2 text-sm font-normal text-ink-3" data-studio-dims>
              {t('dataFigureV2.list.fileMeta', { rows: ov.rows, columns: ov.columns })}
            </span>
          ) : null}
        </h2>
        <IconButton label={t('dataFigureV2.overlay.close')} data-studio-close onClick={() => st().closeOverlay()}>
          <X size={ICON_SIZE.md} />
        </IconButton>
      </div>
      <OverlayFooterContext.Provider value={{ left: footerLeft, right: footerRight }}>
        <div
          ref={scroller}
          key={scene.kind === 'drawing' && scene.pick ? 'pick' : scene.kind}
          onScroll={measureEdges}
          data-studio-stage
          style={{ maskImage, WebkitMaskImage: maskImage, maskComposite: 'intersect', WebkitMaskComposite: 'source-in' }}
          className={'relative flex min-h-0 flex-1 animate-fade-in flex-col overflow-auto px-5 pb-2 pt-1'}
        >
          {body}
        </div>
        <div data-studio-footer className="flex min-h-14 shrink-0 items-center justify-between gap-3 px-5 pb-3 pt-2">
          <span ref={setFooterLeft} className="flex min-w-0 items-center gap-2" />
          <span ref={setFooterRight} className="flex shrink-0 items-center gap-2" />
        </div>
      </OverlayFooterContext.Provider>
      </div>
      {scene.kind === 'mixed' && <MixedCellsDialog columns={scene.columns} onCancel={st().back} onContinue={(picks) => void st().answerCells(picks)} />}
    </div>
  )
}

/** 读表时的占位：几行灰条，和最终的表差不多高，卡片先站稳再换成真内容（静态骨架，不闪） */
function TableSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} data-studio-skeleton className="flex flex-col gap-3 pt-2">
      {[62, 48, 55, 40, 58, 44, 52, 36].map((w, i) => (
        <span key={i} aria-hidden className="block h-4 rounded-sm bg-surface-active" style={{ width: `${w}%` }} />
      ))}
    </div>
  )
}

/**
 * 画布上居中的浮条（工具条 / 通知条 / 提示）在卡片开着时的左内缩：等于卡片占掉的宽度（8px 边距 + 卡片宽 + 8px 间隙），
 * 让它们在没被盖住的那部分画布里重新居中。宽度表达式与卡片同一份（`overlayWidthCss`），所以过渡起点 / 终点与卡片对得上。
 * 没开卡片 / 窄屏（卡片占满宽、浮条本来就被盖）时是 0。
 *
 * `fit`：让位之后剩下的画布放不放得下底部工具条（量出来的：画布栏宽、卡片目标宽、工具条内容的自然宽度，见 `chromeFits`）。
 * 放不下时调用方把工具条淡出，卡片关掉或变窄再回来，不硬塞到属性栏底下。
 */
export function useDataOverlayChrome(narrow: boolean, layer: React.RefObject<HTMLElement | null>): { inset: string; fit: boolean } {
  const open = useDataFigureStore((s) => s.overlay)
  const kind = useDataFigureStore((s) => s.scene.kind)
  const selected = useDataFigureStore((s) => s.selected)
  const overviews = useDataFigureStore((s) => s.overviews)
  const headerRows = useDataFigureStore((s) => s.headerRows)
  const [parentW, setParentW] = useState<number | null>(null)
  const [toolbarW, setToolbarW] = useState<number | null>(null)
  useLayoutEffect(() => {
    const host = layer.current?.parentElement
    if (!host || typeof ResizeObserver === 'undefined') return
    const read = () => {
      setParentW(host.clientWidth)
      const tb = layer.current?.querySelector<HTMLElement>('[data-canvas-toolbar]')
      if (tb) setToolbarW(tb.scrollWidth)
    }
    read()
    const ro = new ResizeObserver(read)
    ro.observe(host)
    const tb = layer.current?.querySelector<HTMLElement>('[data-canvas-toolbar]')
    if (tb) ro.observe(tb)
    return () => ro.disconnect()
  }, [layer, open])
  if (!open || narrow) return { inset: '0px', fit: true }
  const ov = selected ? (overviews[overviewKeyOf(selected, headerRows)]?.data ?? null) : null
  const target = overlayTargetWidth(kind, ov)
  const fit = parentW == null || toolbarW == null ? true : chromeFits(parentW, 16 + overlayCardPx(target, parentW), toolbarW)
  return { inset: `calc(1rem + ${overlayWidthCss(target)})`, fit }
}
