import { useState } from 'react'
import { ArrowLeftRight, Trash2 } from 'lucide-react'
import { formatCm, formatMm } from '@/lib/units'
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

/** 期刊常用版心；宽度是硬约束，高度给个常见起点 */
const PRESETS = [
  { label: '单栏', w: 85, h: 60, hint: '85 mm，多数期刊单栏宽' },
  { label: '双栏', w: 150, h: 100, hint: '150 mm，通栏' },
  { label: '整页', w: 180, h: 240, hint: '180×240 mm，整页版心' },
  { label: '方形', w: 100, h: 100, hint: '100×100 mm' },
]

/**
 * 画布页：页面尺寸是排版的第一约束，默认展开；
 * 背景、查看辅助、吸附、参考线、安全区域按需展开，折叠行给现状摘要。
 */
export function CanvasPage() {
  const page = useDocumentStore((s) => s.doc.page)
  const guides = useDocumentStore((s) => s.doc.guides)
  const ui = useUiStore()
  const active = PRESETS.find((p) => p.w === page.w && p.h === page.h)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const toggle = (k: string) => setOpen((s) => ({ ...s, [k]: !s[k] }))

  const aidsSummary =
    [ui.showRulers && '标尺', ui.showGrid && '网格'].filter(Boolean).join(' · ') || '全部关闭'
  const snapSummary = ui.snapEnabled
    ? [ui.snapToGrid && '网格', ui.snapToGuides && '参考线', ui.snapToObjects && '对象']
        .filter(Boolean)
        .join(' · ') || '仅页面边线'
    : '已关闭'

  return (
    <>
      <Section title="页面尺寸">
        <div className="mb-2 grid grid-cols-4 gap-0.5" role="radiogroup" aria-label="页面预设">
          {PRESETS.map((p) => (
            <Tip key={p.label} label={p.hint}>
              <button
                onClick={() => setPageSize(p.w, p.h)}
                role="radio"
                aria-checked={active?.label === p.label}
                className={cn(
                  'flex h-7 items-center justify-center rounded-sm border text-xs outline-none transition-colors focus-visible:focus-ring',
                  active?.label === p.label
                    ? 'border-accent bg-accent-subtle font-medium text-accent'
                    : 'border-border bg-surface text-ink-2 hover:border-border-strong hover:text-ink',
                )}
              >
                {p.label}
              </button>
            </Tip>
          ))}
        </div>
        <Grid2>
          <MmField
            label="W"
            historyLabel="修改画布宽度"
            min={10}
            value={page.w}
            onChange={(v) => setPageSize(v, page.h)}
          />
          <MmField
            label="H"
            historyLabel="修改画布高度"
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
            横竖交换
          </Button>
          <span className="shrink-0 font-mono text-xs text-ink-3">
            {formatCm(page.w)}×{formatCm(page.h)} cm
          </span>
        </div>
      </Section>

      <Disclosure
        title="背景"
        open={!!open.bg}
        onToggle={() => toggle('bg')}
        summary={page.transparent ? '透明' : (page.bg ?? '#FFFFFF').toUpperCase()}
      >
        <div className="flex flex-col gap-1.5">
          <Row label="透明背景">
            <Toggle
              checked={!!page.transparent}
              onChange={(v) => setPageSetup({ transparent: v }, '修改页面背景')}
            />
          </Row>
          <Row label="背景色">
            <ColorField
              value={page.bg ?? '#FFFFFF'}
              onChange={(v) => setPageSetup({ bg: v }, '修改页面背景色')}
              className={page.transparent ? 'pointer-events-none opacity-40' : undefined}
            />
          </Row>
          {page.transparent && (
            <p className="text-xs leading-relaxed text-ink-3">
              导出 PNG 不铺底色，PDF 本身即无背景；画布上的棋盘格只是示意。
            </p>
          )}
        </div>
      </Disclosure>

      <Disclosure
        title="查看辅助"
        open={!!open.aids}
        onToggle={() => toggle('aids')}
        summary={aidsSummary}
      >
        <div className="flex flex-col gap-1.5">
          <Row label="标尺">
            <Toggle checked={ui.showRulers} onChange={ui.setShowRulers} />
          </Row>
          <Row label="网格">
            <Toggle checked={ui.showGrid} onChange={ui.setShowGrid} />
          </Row>
          {ui.showGrid && (
            <Row label="网格间距">
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
        title="吸附"
        open={!!open.snap}
        onToggle={() => toggle('snap')}
        summary={snapSummary}
      >
        <div className="flex flex-col gap-1.5">
          <Row label="启用吸附">
            <Toggle
              checked={ui.snapEnabled}
              onChange={(v) => ui.setCanvasPref({ snapEnabled: v })}
            />
          </Row>
          {ui.snapEnabled && (
            <>
              <Row label="对齐网格">
                <Toggle
                  checked={ui.snapToGrid}
                  onChange={(v) => ui.setCanvasPref({ snapToGrid: v })}
                />
              </Row>
              <Row label="对齐参考线">
                <Toggle
                  checked={ui.snapToGuides}
                  onChange={(v) => ui.setCanvasPref({ snapToGuides: v })}
                />
              </Row>
              <Row label="对齐对象">
                <Tip label={`页面边与中线始终参与吸附；拖动时按住 ${MOD} 可临时关掉`} side="left">
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
        title={`参考线`}
        open={!!open.guides}
        onToggle={() => toggle('guides')}
        summary={guides.length ? `${guides.length} 条${ui.guidesLocked ? ' · 已锁定' : ''}` : '无'}
      >
        <div className="flex items-center gap-2">
          <Row label="锁定" className="min-w-0 flex-1">
            <Toggle
              checked={ui.guidesLocked}
              onChange={(v) => ui.setCanvasPref({ guidesLocked: v })}
            />
          </Row>
          <Button size="sm" disabled={!guides.length} onClick={clearGuides}>
            全部清除
          </Button>
        </div>
        {guides.length === 0 ? (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
            从标尺往画布里拖即可拉出参考线。
          </p>
        ) : (
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {guides.map((g, i) => (
              <li key={`${g.axis}-${i}`} className="flex items-center gap-1.5">
                <span className="w-8 shrink-0 text-xs text-ink-2">
                  {g.axis === 'x' ? '垂直' : '水平'}
                </span>
                <span className="flex-1 font-mono text-xs text-ink">{formatMm(g.pos)} mm</span>
                <Button
                  size="icon-sm"
                  className="text-ink-3 hover:text-danger"
                  disabled={ui.guidesLocked}
                  onClick={() => removeGuide(i)}
                  aria-label={`删除${g.axis === 'x' ? '垂直' : '水平'}参考线 ${formatMm(g.pos)}mm`}
                >
                  <Trash2 size={12} />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Disclosure>

      <Disclosure
        title="安全区域"
        open={!!open.safe}
        onToggle={() => toggle('safe')}
        summary={ui.showSafeArea ? `页边距 ${page.margin ?? 0} mm` : '关闭'}
      >
        <div className="flex flex-col gap-1.5">
          <Row label="显示">
            <Tip label="只是画布上的参考框，不裁剪也不影响导出" side="left">
              <span className="flex">
                <Toggle
                  checked={ui.showSafeArea}
                  onChange={(v) => ui.setCanvasPref({ showSafeArea: v })}
                />
              </span>
            </Tip>
          </Row>
          <Row label="页边距">
            <NumberField
              value={page.margin ?? 0}
              min={0}
              max={40}
              step={1}
              suffix="mm"
              onChange={(v) => setPageSetup({ margin: v }, '修改页边距')}
            />
          </Row>
        </div>
      </Disclosure>
    </>
  )
}
