import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, Trash2 } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { formatMm } from '@/lib/units'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { cn, MOD } from '@/lib/utils'
import { clearGuides, removeGuide, setPageSetup, setPageSize } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { Disclosure, Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
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

/**
 * 本页所有设置行共用的标签列宽：数值行走 `Row`（内联宽度），开关行走 `ToggleRow`
 * （同宽的类名 `w-18` = 72px）。默认的 44px 装不下「网格间距」「页边距」这类
 * 四字标签，320px 属性栏下会折成两行（审计 T31）。两种行的控件从同一条竖线起排
 * ——折叠区里「透明背景 / 背景色」上下两行的控件才对得齐（Session 2）。
 */
const LABEL_W = 72
const LABEL_COL = 'w-18'

/** 本页文案 inspector:canvas.*，历史标签 inspector:history.* */
const cv = (key: string, values?: Record<string, unknown>) =>
  translate(`canvas.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

/**
 * 预设缩略图：**四档共用同一个 mm→px 比例**，所以「单栏比双栏窄一半」
 * 这件事在图形上是真的。各自撑满格子的话四个方块一样大，形状还在、
 * 比例没了，用户照样得读文字——那就白画了。
 */
const PREVIEW_BOX = 40
const PREVIEW_SCALE = PREVIEW_BOX / Math.max(...PRESETS.map((p) => Math.max(p.w, p.h)))

/**
 * 画布页：页面尺寸是排版的第一约束，默认展开；
 * 背景、查看辅助、吸附、参考线、安全区域按需展开，折叠行给现状摘要。
 */
export function CanvasPage() {
  useTranslation('inspector')
  const page = useDocumentStore((s) => s.doc.page)
  const guides = useDocumentStore((s) => s.doc.guides)
  const ui = useUiStore()
  const active = PRESETS.find((p) => p.w === page.w && p.h === page.h)
  // 一次只展开一组（审计 T31：多组同时展开显得冗长）；再点同一组就收起
  const [openKey, setOpenKey] = useState<string | null>(null)
  const open = (k: string) => openKey === k
  const toggle = (k: string) => setOpenKey((cur) => (cur === k ? null : k))

  // 收起时也得看得出网格状态（审计 T31 验收）：开着就把间距一起报出来，
  // 只报「网格」的话用户还得展开才知道它多密
  const aidsSummary =
    [ui.showRulers && cv('rulers'), ui.showGrid && cv('gridSummary', { size: ui.gridSize })]
      .filter(Boolean)
      .join(' · ') || cv('allOff')
  const snapSummary = ui.snapEnabled
    ? [ui.snapToGrid && cv('grid'), ui.snapToGuides && cv('guides'), ui.snapToObjects && cv('objects')]
        .filter(Boolean)
        .join(' · ') || cv('snapPageOnly')
    : cv('snapOff')

  return (
    <>
      <Section
        title={cv('pageSize')}
        action={
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {/* 与下面的 W / H 输入框同一个单位：摘要说 cm、框里写 mm 是两套尺子 */}
            {translate('measure.mmSizeSpaced', { w: formatMm(page.w), h: formatMm(page.h) })}
          </span>
        }
      >
        <div className="mb-2 grid grid-cols-4 gap-1" role="radiogroup" aria-label={cv('presetGroup')}>
          {PRESETS.map((p) => {
            const on = active?.id === p.id
            const label = cv(`presets.${p.id}.label`)
            return (
              <Tip key={p.id} label={cv(`presets.${p.id}.hint`)}>
                <button
                  onClick={() => setPageSize(p.w, p.h)}
                  role="radio"
                  aria-checked={on}
                  aria-label={cv('presetAria', { label, w: p.w, h: p.h })}
                  className={cn(
                    'flex flex-col items-center gap-1 rounded-sm border py-1.5 outline-none transition-colors duration-fast focus-visible:focus-ring',
                    // 选中：轻 tint + 稍强的边 + 稍强的预览线 + 字重，不用大灰块
                    on
                      ? 'border-border-strong bg-selected text-ink'
                      : 'border-border bg-surface text-ink-2 hover:border-border-strong hover:text-ink',
                  )}
                >
                  {/* 缩略图按真实比例摆，底边对齐——横竖两类放一排才看得出高矮 */}
                  <span
                    className="flex items-end justify-center"
                    style={{ height: PREVIEW_BOX }}
                    aria-hidden
                  >
                    <span
                      className={cn(
                        'block border',
                        // 选中不只换颜色：空心变实心，色觉障碍下也分得出
                        on ? 'border-ink bg-ink/15' : 'border-ink-faint bg-surface',
                      )}
                      style={{
                        width: p.w * PREVIEW_SCALE,
                        height: p.h * PREVIEW_SCALE,
                      }}
                    />
                  </span>
                  <span className={cn('text-xs', on && 'font-medium')}>{label}</span>
                </button>
              </Tip>
            )
          })}
        </div>
        {/* 宽、高与横竖交换同一行：两个字段与检查器的 X/Y/W/H 同一个 primitive
            （单位在框里），交换是个小 ghost 图标钮 */}
        <div data-page-size-row className="flex items-center gap-1.5">
          <MmField
            label="W"
            historyLabel={hist('setPageW')}
            min={10}
            value={page.w}
            onChange={(v) => setPageSize(v, page.h)}
          />
          <MmField
            label="H"
            historyLabel={hist('setPageH')}
            min={10}
            value={page.h}
            onChange={(v) => setPageSize(page.w, v)}
          />
          <IconButton
            label={cv('swap')}
            iconSize="sm"
            side="left"
            onClick={() => setPageSize(page.h, page.w)}
          >
            <ArrowLeftRight size={ICON_SIZE.sm} />
          </IconButton>
        </div>
      </Section>

      <Disclosure
        title={cv('background')}
        open={open('bg')}
        onToggle={() => toggle('bg')}
        summary={page.transparent ? cv('transparent') : (page.bg ?? '#FFFFFF').toUpperCase()}
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
            <ColorField
              ariaLabel={cv('bgColor')}
              value={page.bg ?? '#FFFFFF'}
              onChange={(v) => setPageSetup({ bg: v }, hist('setPageBgColor'))}
              className={page.transparent ? 'pointer-events-none opacity-40' : undefined}
            />
          </Row>
        </div>
      </Disclosure>

      <Disclosure
        title={cv('viewAids')}
        open={open('aids')}
        onToggle={() => toggle('aids')}
        summary={aidsSummary}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('rulers')}>
            <Toggle aria-label={cv('rulers')} checked={ui.showRulers} onChange={ui.setShowRulers} />
          </ToggleRow>
          <ToggleRow label={cv('grid')}>
            <Toggle aria-label={cv('grid')} checked={ui.showGrid} onChange={ui.setShowGrid} />
          </ToggleRow>
          {ui.showGrid && (
            <Row label={cv('gridSize')} labelWidth={LABEL_W}>
              <NumberField
                ariaLabel={cv('gridSize')}
                value={ui.gridSize}
                min={1}
                max={50}
                step={1}
                unit="mm"
                onChange={(v) => ui.setCanvasPref({ gridSize: v })}
              />
            </Row>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title={cv('snap')}
        open={open('snap')}
        onToggle={() => toggle('snap')}
        summary={snapSummary}
      >
        <div className="flex flex-col gap-1.5">
          <ToggleRow label={cv('snapEnable')}>
            <Toggle
              aria-label={cv('snapEnable')}
              checked={ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapEnabled: v })}
            />
          </ToggleRow>
          {ui.snapEnabled && (
            <>
              <ToggleRow label={cv('snapGrid')}>
                <Toggle
                  aria-label={cv('snapGrid')}
                  checked={ui.snapToGrid}
                  onChange={(v) => ui.setCanvasPref({ snapToGrid: v })}
                />
              </ToggleRow>
              <ToggleRow label={cv('snapGuides')}>
                <Toggle
                  aria-label={cv('snapGuides')}
                  checked={ui.snapToGuides}
                  onChange={(v) => ui.setCanvasPref({ snapToGuides: v })}
                />
              </ToggleRow>
              <ToggleRow label={cv('snapObjects')}>
                <Tip label={cv('snapObjectsTip', { mod: MOD })} side="left">
                  <span className="flex">
                    <Toggle
                      aria-label={cv('snapObjects')}
                      checked={ui.snapToObjects}
                      onChange={(v) => ui.setCanvasPref({ snapToObjects: v })}
                    />
                  </span>
                </Tip>
              </ToggleRow>
            </>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title={cv('guides')}
        open={open('guides')}
        onToggle={() => toggle('guides')}
        summary={
          guides.length
            ? cv('guideCount', { count: guides.length }) +
              (ui.guidesLocked ? cv('guidesLockedSuffix') : '')
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
              {guides.map((g, i) => (
                <li key={`${g.axis}-${i}`} className="flex h-7 items-center gap-2">
                  <span className={cn(LABEL_COL, 'shrink-0 truncate text-xs text-ink-2')}>
                    {cv(g.axis === 'x' ? 'guideVertical' : 'guideHorizontal')}
                  </span>
                  <span className="min-w-0 flex-1 font-mono text-xs tabular-nums text-ink">
                    {translate('measure.mm', { value: formatMm(g.pos) })}
                  </span>
                  <IconButton
                    label={cv('deleteGuide', {
                      axis: cv(g.axis === 'x' ? 'guideVertical' : 'guideHorizontal'),
                      pos: formatMm(g.pos),
                    })}
                    tip={false}
                    iconSize="sm"
                    className="text-ink-3 hover:text-danger"
                    disabled={ui.guidesLocked}
                    onClick={() => removeGuide(i)}
                  >
                    <Trash2 size={ICON_SIZE.sm} />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
          {/* 「全部清除」对齐到控件列，不与开关抢同一行 */}
          <div className="flex items-center gap-2">
            <span className={cn(LABEL_COL, 'shrink-0')} aria-hidden />
            <Button variant="ghost" size="sm" className="-ml-2" disabled={!guides.length} onClick={clearGuides}>
              {cv('clearAll')}
            </Button>
          </div>
        </div>
      </Disclosure>

      <Disclosure
        title={cv('safeArea')}
        open={open('safe')}
        onToggle={() => toggle('safe')}
        summary={
          ui.showSafeArea ? cv('marginSummary', { margin: page.margin ?? 0 }) : cv('safeAreaOff')
        }
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
      </Disclosure>
    </>
  )
}

/**
 * 开关行：标签列与本页的数值行同宽（`LABEL_COL` = `LABEL_W`），开关从同一条控件列
 * 起排——不再把开关推到侧栏最右边让它漂着（Session 2；审计 T31 的「不折行」靠的
 * 是 72px 的列宽本身，「对齐参考线」放得下）。整行是一个 `<label>`：点文字也能切换。
 */
function ToggleRow({
  label,
  children,
  className,
}: {
  label: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <label data-toggle-row className={cn('flex min-h-7 items-center gap-2', className)}>
      <span className={cn(LABEL_COL, 'min-w-0 shrink-0 truncate text-xs text-ink-2')}>{label}</span>
      <span className="flex shrink-0 items-center">{children}</span>
    </label>
  )
}
