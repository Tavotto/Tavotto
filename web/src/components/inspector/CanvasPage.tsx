import { useEffect, useReducer, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, ExternalLink, LayoutGrid, Trash2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatMm } from '@/lib/units'
import { onExportDefaultsHydrated, readExportDefaults } from '@/lib/exportDefaults'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { cn, MOD } from '@/lib/utils'
import { clearGuides, removeGuide, setPageSetup, setPageSize } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { Row, Section, ROW_GRID_COLS } from '../ui/Field'
import { SummaryRow } from '../ui/SummaryRow'
import { ColorField, NumberField } from '../ui/Input'
import { Segmented } from '../ui/Segmented'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { INSPECTOR_LABEL_COL, INSPECTOR_LABEL_W } from './layout'
import { MmField } from './MmField'

/**
 * 期刊常用版心；宽度是硬约束，高度给个常见起点。
 * 文案按 id 查 `inspector:canvas.presets.<id>`，这里只留尺寸。
 */
const PRESETS = [
  { id: 'single', w: 85, h: 60 },
  { id: 'double', w: 150, h: 100 },
  { id: 'full', w: 180, h: 240 },
  { id: 'square', w: 100, h: 100 },
]

/** 下拉里「自定义」那一项的取值：尺寸对不上任何预设时它就是当前值 */
const CUSTOM = 'custom'

const LABEL_W = INSPECTOR_LABEL_W

/** 本页文案 inspector:canvas.*，历史标签 inspector:history.* */
const cv = (key: string, values?: Record<string, unknown>) =>
  translate(`canvas.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

/**
 * 预设缩略图：**各档共用同一个 mm→px 比例**，所以「单栏比双栏窄一半」这件事在图形上是真的；
 * 选项里的小图与触发器里的同一副。
 */
const THUMB_BOX = 16
const THUMB_SCALE = THUMB_BOX / Math.max(...PRESETS.map((p) => Math.max(p.w, p.h)))

function PageThumb({ w, h }: { w: number; h: number }) {
  const k = Math.min(THUMB_SCALE, THUMB_BOX / Math.max(w, h))
  return (
    <span aria-hidden className="flex h-4 w-4 shrink-0 items-end justify-center">
      <span
        className="block rounded-xs border border-ink-3 bg-surface"
        style={{ width: Math.max(3, w * k), height: Math.max(3, h * k) }}
      />
    </span>
  )
}

/**
 * 画布页：页面尺寸是排版的第一约束，默认展开；
 * 背景、查看辅助、吸附、参考线、安全区域按需展开，折叠行给现状摘要。
 *
 * 2026-10-07 设计审计 §9.3：头部与属性页同一套两行（「画布」/ 画布名 + 尺寸）；四张 88px 的预设卡
 * （占首屏约四分之一、第四种选中皮肤、尺寸对不上时没有「自定义」）换成一行带缩略图的下拉；
 * 折叠行可以同时开多个、开合跨会话记住；关掉自动对齐时子开关不卸载、只变暗；
 * 参考线列表的方向是一列 │ / ─ 字形；末尾一行只读的导出摘要，点它打开导出对话框（不另起导出管线）。
 */
export function CanvasPage() {
  useTranslation('inspector')
  const page = useDocumentStore((s) => s.doc.page)
  const name = useDocumentStore((s) => s.doc.name)
  const guides = useDocumentStore((s) => s.doc.guides)
  const ui = useUiStore()
  const active = PRESETS.find((p) => p.w === page.w && p.h === page.h)
  // 摘要行互不排斥、各记各的（键 `canvas:<组>`）；开合跨会话记在 inspectorPrefs
  const foldOpen = useInspectorPrefs((s) => s.foldOpen)
  const setFoldOpen = useInspectorPrefs((s) => s.setFoldOpen)
  const open = (k: string) => foldOpen[`canvas:${k}`] ?? false
  const toggle = (k: string) => setFoldOpen(`canvas:${k}`, !open(k))

  // 收起时也得看得出网格状态（审计 T31 验收）：开着就把间距一起报出来
  const aidsSummary =
    [ui.showRulers && cv('rulers'), ui.showGrid && cv('gridSummary', { size: ui.gridSize })]
      .filter(Boolean)
      .join(' · ') || cv('allOff')
  const snapSummary = ui.snapEnabled
    ? [ui.snapToGrid && cv('grid'), ui.snapToGuides && cv('guides'), ui.snapToObjects && cv('objects')]
        .filter(Boolean)
        .join(' · ') || cv('snapPageOnly')
    : cv('snapOff')
  const portrait = page.h >= page.w
  const square = page.w === page.h
  // 导出摘要读的是本机缓存；后端那份（#715 PR-B）取回、覆盖缓存后要重读——画布页可能在
  // 取回之前就已挂着（右栏记住的页签是画布），不订阅就整次会话停在空缓存的 600 ppi（Codex #829）
  const [, onHydrated] = useReducer((n: number) => n + 1, 0)
  useEffect(() => onExportDefaultsHydrated(onHydrated), [])
  const exportDefaults = readExportDefaults()

  return (
    <>
      {/* 两行头：与属性页的身份头同一副骨架（路径 24 + 名字 32），右端是尺寸 meta */}
      <header data-identity data-canvas-identity className="mx-3 mb-1 flex shrink-0 flex-col pt-2">
        <p className="flex h-6 min-w-0 items-center gap-1.5 text-sm text-ink-3">
          <span className="min-w-0 flex-1 truncate">{translate('tab.canvas', { ns: 'inspector' })}</span>
          <span className="type-meta shrink-0">
            {cv('sizeMeta', { w: formatMm(page.w), h: formatMm(page.h) })}
          </span>
        </p>
        <div className="flex min-h-8 items-center gap-2">
          <span
            data-identity-icon
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface-hover text-ink-2"
          >
            <LayoutGrid size={ICON_SIZE.md} aria-hidden />
          </span>
          <h2 title={name} className="type-heading line-clamp-2 min-w-0 break-words">
            {name}
          </h2>
        </div>
      </header>

      <Section title={cv('pageSize')}>
        <div className="flex flex-col gap-1.5">
          <Row label={cv('preset')} labelWidth={LABEL_W}>
            <Select
              className="min-w-0 flex-1"
              ariaLabel={cv('presetGroup')}
              value={active?.id ?? CUSTOM}
              onChange={(id) => {
                const p = PRESETS.find((x) => x.id === id)
                if (p) setPageSize(p.w, p.h)
              }}
              options={[
                ...PRESETS.map((p) => ({
                  value: p.id,
                  hint: cv(`presets.${p.id}.hint`),
                  label: (
                    <span data-page-preset={p.id} className="flex min-w-0 items-center gap-2">
                      <PageThumb w={p.w} h={p.h} />
                      <span className="truncate">{cv(`presets.${p.id}.label`)}</span>
                    </span>
                  ),
                })),
                // 「自定义」只是「当前尺寸不是任何预设」的说法，不是一个能选的尺寸：只在它就是当前
                // 值时出现。尺寸对上预设时还摆着它，选了什么都不写、受控下拉又弹回去（Codex #829 P2）
                ...(active
                  ? []
                  : [
                      {
                        value: CUSTOM,
                        label: (
                          <span data-page-preset={CUSTOM} className="flex min-w-0 items-center gap-2">
                            <PageThumb w={page.w} h={page.h} />
                            <span className="truncate">{cv('presetCustom')}</span>
                          </span>
                        ),
                      },
                    ]),
              ]}
            />
          </Row>
          {/* 宽、高两个半列 + 横竖交换坐在状态槽里（与属性页的恢复钮同一格） */}
          <div data-page-size-row>
            <Row
              label={cv('size')}
              labelWidth={LABEL_W}
              status={
                <IconButton label={cv('swap')} iconSize="xs" side="left" onClick={() => setPageSize(page.h, page.w)}>
                  <ArrowLeftRight size={ICON_SIZE.xs} />
                </IconButton>
              }
            >
              <div className="flex w-full min-w-0 items-center gap-1.5">
                <span className="w-[calc((100%-0.375rem)/2)] min-w-0 shrink-0">
                  <MmField
                    label="W"
                    historyLabel={hist('setPageW')}
                    min={10}
                    value={page.w}
                    onChange={(v) => setPageSize(v, page.h)}
                  />
                </span>
                <span className="w-[calc((100%-0.375rem)/2)] min-w-0 shrink-0">
                  <MmField
                    label="H"
                    historyLabel={hist('setPageH')}
                    min={10}
                    value={page.h}
                    onChange={(v) => setPageSize(page.w, v)}
                  />
                </span>
              </div>
            </Row>
          </div>
          <Row label={cv('orientation')} labelWidth={LABEL_W}>
            <div data-page-orientation className="contents">
              <Segmented
                ariaLabel={cv('orientation')}
                value={square ? null : portrait ? 'portrait' : 'landscape'}
                // 方向是宽高的另一种说法：换方向 = 横竖交换。正方形没有方向：两档都不选、都禁用，
                // 不摆一个看着能点却永远不变的控件（Codex #829 P2）
                onChange={(v) => {
                  if (!square && (v === 'portrait') !== portrait) setPageSize(page.h, page.w)
                }}
                items={[
                  { value: 'portrait', label: cv('portrait'), disabled: square },
                  { value: 'landscape', label: cv('landscape'), disabled: square },
                ]}
              />
            </div>
          </Row>
        </div>
      </Section>

      <SummaryRow
        label={cv('background')}
        open={open('bg')}
        onToggle={() => toggle('bg')}
        data-fold="canvas-bg"
        value={page.transparent ? cv('transparent') : <SwatchValue color={page.bg ?? '#FFFFFF'} />}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('transparentBg')}>
            <Toggle
              aria-label={cv('transparentBg')}
              checked={!!page.transparent}
              onChange={(v) => setPageSetup({ transparent: v }, hist('setPageBackground'))}
            />
          </ToggleRow>
          <Row label={cv('bgColor')} labelWidth={LABEL_W}>
            {/* 透明时真禁用（键盘也改不了），不再是 pointer-events-none 的假禁用（§9.3） */}
            <ColorField
              ariaLabel={cv('bgColor')}
              value={page.bg ?? '#FFFFFF'}
              disabled={!!page.transparent}
              onChange={(v) => setPageSetup({ bg: v }, hist('setPageBgColor'))}
            />
          </Row>
        </div>
      </SummaryRow>

      <SummaryRow
        label={cv('viewAids')}
        open={open('aids')}
        onToggle={() => toggle('aids')}
        data-fold="canvas-aids"
        value={aidsSummary}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('rulers')}>
            <Toggle aria-label={cv('rulers')} checked={ui.showRulers} onChange={ui.setShowRulers} />
          </ToggleRow>
          <ToggleRow label={cv('grid')}>
            <Toggle aria-label={cv('grid')} checked={ui.showGrid} onChange={ui.setShowGrid} />
          </ToggleRow>
          <Row label={cv('gridSize')} labelWidth={LABEL_W}>
            <NumberField
              half
              ariaLabel={cv('gridSize')}
              value={ui.gridSize}
              min={1}
              max={50}
              step={1}
              unit="mm"
              disabled={!ui.showGrid}
              onChange={(v) => ui.setCanvasPref({ gridSize: v })}
            />
          </Row>
        </div>
      </SummaryRow>

      <SummaryRow
        label={cv('snap')}
        open={open('snap')}
        onToggle={() => toggle('snap')}
        data-fold="canvas-snap"
        value={snapSummary}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('snapEnable')}>
            <Toggle
              aria-label={cv('snapEnable')}
              checked={ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapEnabled: v })}
            />
          </ToggleRow>
          {/* 总开关关着时子开关不卸载：留在原位变暗（禁用），开回来时它们各自的状态还在、行不跳 */}
          <ToggleRow label={cv('snapGrid')} dim={!ui.snapEnabled}>
            <Toggle
              aria-label={cv('snapGrid')}
              checked={ui.snapToGrid}
              disabled={!ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapToGrid: v })}
            />
          </ToggleRow>
          <ToggleRow label={cv('snapGuides')} dim={!ui.snapEnabled}>
            <Toggle
              aria-label={cv('snapGuides')}
              checked={ui.snapToGuides}
              disabled={!ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapToGuides: v })}
            />
          </ToggleRow>
          <ToggleRow label={cv('snapObjects')} dim={!ui.snapEnabled}>
            <Tip label={cv('snapObjectsTip', { mod: MOD })} side="left">
              <span className="flex">
                <Toggle
                  aria-label={cv('snapObjects')}
                  checked={ui.snapToObjects}
                  disabled={!ui.snapEnabled}
                  onChange={(v) => ui.setCanvasPref({ snapToObjects: v })}
                />
              </span>
            </Tip>
          </ToggleRow>
        </div>
      </SummaryRow>

      <SummaryRow
        label={cv('guides')}
        open={open('guides')}
        onToggle={() => toggle('guides')}
        data-fold="canvas-guides"
        value={
          guides.length
            ? cv('guideCount', { count: guides.length }) + (ui.guidesLocked ? cv('guidesLockedSuffix') : '')
            : cv('guidesNone')
        }
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('lock')}>
            <Toggle
              aria-label={cv('lock')}
              checked={ui.guidesLocked}
              onChange={(v) => ui.setCanvasPref({ guidesLocked: v })}
            />
          </ToggleRow>
          {guides.length > 0 && (
            <ul className="flex flex-col">
              {guides.map((g, i) => {
                const axis = cv(g.axis === 'x' ? 'guideVertical' : 'guideHorizontal')
                return (
                  // 方向是一列字形（│ 垂直 / ─ 水平），不是每行重复一遍「垂直参考线」；名字在 sr-only 与删除钮里
                  <li key={`${g.axis}-${i}`} data-guide-row={g.axis} className={cn('grid h-7 items-center gap-x-2', ROW_GRID_COLS)}>
                    <span className="flex items-center text-ink-3">
                      <span aria-hidden className="w-4 text-center font-mono text-sm leading-none">
                        {g.axis === 'x' ? '│' : '─'}
                      </span>
                      <span className="sr-only">{axis}</span>
                    </span>
                    <span className="type-number min-w-0 text-ink">
                      {translate('measure.mm', { value: formatMm(g.pos) })}
                    </span>
                    <IconButton
                      label={cv('deleteGuide', { axis, pos: formatMm(g.pos) })}
                      tip={false}
                      iconSize="xs"
                      className="text-ink-2 hover:text-danger"
                      disabled={ui.guidesLocked}
                      onClick={() => removeGuide(i)}
                    >
                      <Trash2 size={ICON_SIZE.xs} />
                    </IconButton>
                  </li>
                )
              })}
            </ul>
          )}
          {/* 「全部清除」对齐到控件列 */}
          <Row labelWidth={LABEL_W}>
            <Button variant="ghost" size="sm" className="-ml-2.5" disabled={!guides.length} onClick={clearGuides}>
              {cv('clearAll')}
            </Button>
          </Row>
        </div>
      </SummaryRow>

      <SummaryRow
        label={cv('safeArea')}
        open={open('safe')}
        onToggle={() => toggle('safe')}
        data-fold="canvas-safe"
        value={ui.showSafeArea ? cv('marginSummary', { margin: page.margin ?? 0 }) : cv('safeAreaOff')}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('show')}>
            <Tip label={cv('safeAreaTip')} side="left">
              <span className="flex">
                <Toggle
                  aria-label={cv('show')}
                  checked={ui.showSafeArea}
                  onChange={(v) => ui.setCanvasPref({ showSafeArea: v })}
                />
              </span>
            </Tip>
          </ToggleRow>
          <Row label={cv('margin')} labelWidth={LABEL_W}>
            <NumberField
              half
              ariaLabel={cv('margin')}
              value={page.margin ?? 0}
              min={0}
              max={40}
              step={1}
              unit="mm"
              onChange={(v) => setPageSetup({ margin: v }, hist('setPageMargin'))}
            />
          </Row>
        </div>
      </SummaryRow>

      {/* 只读的导出摘要：说的是导出对话框此刻的默认（格式 · ppi），点它打开同一个导出对话框——
          这里不是第二个导出入口，没有第二条导出管线 */}
      <div className="mx-3" data-summary-row>
        <button
          type="button"
          data-canvas-export-summary
          onClick={() => useUiStore.getState().setExportOpen(true)}
          className="grid h-8 w-full grid-cols-[minmax(40%,1fr)_minmax(0,auto)_1.25rem] items-center gap-x-2 rounded-sm text-left text-sm text-ink outline-none focus-visible:focus-ring"
        >
          <span className="min-w-0 truncate">{cv('export')}</span>
          <span className="min-w-0 truncate text-right text-ink-3">
            <span className="sr-only">, </span>
            {cv('exportSummary', {
              formats: exportDefaults.formats.map((f) => f.toUpperCase()).join(' · '),
              dpi: exportDefaults.dpi,
            })}
          </span>
          <span aria-hidden className="flex w-5 justify-center text-ink-3">
            <ExternalLink size={ICON_SIZE.xs} />
          </span>
        </button>
      </div>
    </>
  )
}

/** 折叠行右值里的颜色：一小块色样 + 色号（摘要行「只写当前值」，色样比色号先被认出来） */
function SwatchValue({ color }: { color: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="h-3 w-3 shrink-0 rounded-xs border border-border" style={{ background: color }} />
      {color.toUpperCase()}
    </span>
  )
}

/**
 * 开关行：标签列与本页的数值行同宽（`INSPECTOR_LABEL_COL` = 行网格的 `--insp-label`），开关从同一条控件列
 * 起排。整行是一个 `<label>`：点文字也能切换。`dim` = 上级开关关着，这一行留在原位变暗。
 */
function ToggleRow({
  label,
  children,
  className,
  dim,
}: {
  label: ReactNode
  children: ReactNode
  className?: string
  dim?: boolean
}) {
  return (
    <label
      data-toggle-row
      data-dim={dim || undefined}
      className={cn('grid min-h-7 items-center gap-x-2', ROW_GRID_COLS, className)}
    >
      <span
        className={cn(
          INSPECTOR_LABEL_COL,
          'line-clamp-2 min-w-0 text-sm leading-tight text-ink-2',
          dim && 'opacity-40',
        )}
      >
        {label}
      </span>
      <span className="flex items-center">{children}</span>
      <span aria-hidden />
    </label>
  )
}
