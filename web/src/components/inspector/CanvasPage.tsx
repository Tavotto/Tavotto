import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, Trash2 } from 'lucide-react'
import { formatCm, formatMm } from '@/lib/units'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { cn, MOD } from '@/lib/utils'
import { clearGuides, removeGuide, setPageSetup, setPageSize } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Disclosure, Grid2, Row, Section } from '../ui/Field'
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
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const toggle = (k: string) => setOpen((s) => ({ ...s, [k]: !s[k] }))

  const aidsSummary =
    [ui.showRulers && cv('rulers'), ui.showGrid && cv('grid')]
      .filter(Boolean)
      .join(' · ') || cv('allOff')
  const snapSummary = ui.snapEnabled
    ? [ui.snapToGrid && cv('grid'), ui.snapToGuides && cv('guides'), ui.snapToObjects && cv('objects')]
        .filter(Boolean)
        .join(' · ') || cv('snapPageOnly')
    : cv('snapOff')

  return (
    <>
      <Section title={cv('pageSize')}>
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
                    'flex flex-col items-center gap-1 rounded-sm border py-1.5 outline-none transition-colors focus-visible:focus-ring',
                    on
                      ? 'border-accent bg-accent-subtle text-accent'
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
                        on ? 'border-accent bg-accent/25' : 'border-ink-faint bg-surface',
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
        <Grid2>
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
        </Grid2>
        <div className="mt-1.5 flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => setPageSize(page.h, page.w)}
          >
            <ArrowLeftRight size={13} />
            {cv('swap')}
          </Button>
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formatCm(page.w)}×{formatCm(page.h)} cm
          </span>
        </div>
      </Section>

      <Disclosure
        title={cv('background')}
        open={!!open.bg}
        onToggle={() => toggle('bg')}
        summary={page.transparent ? cv('transparent') : (page.bg ?? '#FFFFFF').toUpperCase()}
      >
        <div className="flex flex-col gap-1.5">
          <Row label={cv('transparentBg')}>
            <Toggle
              checked={!!page.transparent}
              onChange={(v) => setPageSetup({ transparent: v }, hist('setPageBackground'))}
            />
          </Row>
          <Row label={cv('bgColor')}>
            <ColorField
              value={page.bg ?? '#FFFFFF'}
              onChange={(v) => setPageSetup({ bg: v }, hist('setPageBgColor'))}
              className={page.transparent ? 'pointer-events-none opacity-40' : undefined}
            />
          </Row>
          {page.transparent && (
            <p className="text-xs leading-relaxed text-ink-3">{cv('transparentHint')}</p>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title={cv('viewAids')}
        open={!!open.aids}
        onToggle={() => toggle('aids')}
        summary={aidsSummary}
      >
        <div className="flex flex-col gap-1.5">
          <Row label={cv('rulers')}>
            <Toggle checked={ui.showRulers} onChange={ui.setShowRulers} />
          </Row>
          <Row label={cv('grid')}>
            <Toggle checked={ui.showGrid} onChange={ui.setShowGrid} />
          </Row>
          {ui.showGrid && (
            <Row label={cv('gridSize')}>
              <NumberField
                value={ui.gridSize}
                min={1}
                max={50}
                step={1}
                suffix="mm"
                onChange={(v) => ui.setCanvasPref({ gridSize: v })}
              />
            </Row>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title={cv('snap')}
        open={!!open.snap}
        onToggle={() => toggle('snap')}
        summary={snapSummary}
      >
        <div className="flex flex-col gap-1.5">
          <Row label={cv('snapEnable')}>
            <Toggle
              checked={ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapEnabled: v })}
            />
          </Row>
          {ui.snapEnabled && (
            <>
              <Row label={cv('snapGrid')}>
                <Toggle
                  checked={ui.snapToGrid}
                  onChange={(v) => ui.setCanvasPref({ snapToGrid: v })}
                />
              </Row>
              <Row label={cv('snapGuides')}>
                <Toggle
                  checked={ui.snapToGuides}
                  onChange={(v) => ui.setCanvasPref({ snapToGuides: v })}
                />
              </Row>
              <Row label={cv('snapObjects')}>
                <Tip label={cv('snapObjectsTip', { mod: MOD })} side="left">
                  <span className="flex">
                    <Toggle
                      checked={ui.snapToObjects}
                      onChange={(v) => ui.setCanvasPref({ snapToObjects: v })}
                    />
                  </span>
                </Tip>
              </Row>
            </>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title={cv('guides')}
        open={!!open.guides}
        onToggle={() => toggle('guides')}
        summary={
          guides.length
            ? cv('guideCount', { count: guides.length }) +
              (ui.guidesLocked ? cv('guidesLockedSuffix') : '')
            : cv('guidesNone')
        }
      >
        <div className="flex items-center gap-2">
          <Row label={cv('lock')} className="min-w-0 flex-1">
            <Toggle
              checked={ui.guidesLocked}
              onChange={(v) => ui.setCanvasPref({ guidesLocked: v })}
            />
          </Row>
          <Button size="sm" disabled={!guides.length} onClick={clearGuides}>
            {cv('clearAll')}
          </Button>
        </div>
        {guides.length === 0 ? (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-3">{cv('guidesHint')}</p>
        ) : (
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {guides.map((g, i) => (
              <li key={`${g.axis}-${i}`} className="flex items-center gap-1.5">
                <span className="w-8 shrink-0 text-xs text-ink-2">
                  {cv(g.axis === 'x' ? 'guideVertical' : 'guideHorizontal')}
                </span>
                <span className="flex-1 font-mono text-xs text-ink">{formatMm(g.pos)} mm</span>
                <Button
                  size="icon-sm"
                  className="text-ink-3 hover:text-danger"
                  disabled={ui.guidesLocked}
                  onClick={() => removeGuide(i)}
                  aria-label={cv('deleteGuide', {
                    axis: cv(g.axis === 'x' ? 'guideVertical' : 'guideHorizontal'),
                    pos: formatMm(g.pos),
                  })}
                >
                  <Trash2 size={12} />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Disclosure>

      <Disclosure
        title={cv('safeArea')}
        open={!!open.safe}
        onToggle={() => toggle('safe')}
        summary={
          ui.showSafeArea ? cv('marginSummary', { margin: page.margin ?? 0 }) : cv('safeAreaOff')
        }
      >
        <div className="flex flex-col gap-1.5">
          <Row label={cv('show')}>
            <Tip label={cv('safeAreaTip')} side="left">
              <span className="flex">
                <Toggle
                  checked={ui.showSafeArea}
                  onChange={(v) => ui.setCanvasPref({ showSafeArea: v })}
                />
              </span>
            </Tip>
          </Row>
          <Row label={cv('margin')}>
            <NumberField
              value={page.margin ?? 0}
              min={0}
              max={40}
              step={1}
              suffix="mm"
              onChange={(v) => setPageSetup({ margin: v }, hist('setPageMargin'))}
            />
          </Row>
        </div>
      </Disclosure>
    </>
  )
}
