import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { paletteById, paletteSource, type CatalogPalette } from '@/lib/palette/catalog'
import type { ColorGroup } from '@/lib/palette/colorKeys'
import { VISIONS, type Vision } from '@/lib/palette/cvd'
import { colorForSlot, evaluate, rankPalettes, type RankedPalette } from '@/lib/palette/rank'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import {
  applyPaletteScheme,
  canvasPaletteState,
  mergePaletteGroups,
  planPaletteSchemePreview,
  setPaletteGroupNeutral,
  type PaletteApplyResult,
} from '@/store/paletteActions'
import { useRenderStore } from '@/store/renderStore'
import { askConfirm } from '@/store/uiStore'
import {
  clearPalettePreview,
  holdPalettePreview,
  showPalettePreview,
  usePalettePreviewMissed,
} from '@/store/palettePreviewStore'
import { useCvdPreviewStore } from '@/store/cvdPreviewStore'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Section } from '../ui/Field'
import { Select, type SelectOption } from '../ui/Select'
import { SearchSelect } from '../ui/SearchSelect'
import { STYLE_ROW_GRID } from './styleRowParts'

const pp = (key: string, values?: Record<string, unknown>) =>
  translate(`palettePanel.${key}`, { ns: 'workspace', ...(values ?? {}) })
const pm = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`palettePanel.${key}`, values, 'workspace')

/** 色觉名写成字面量表：模板拼出来的键死键门禁看不见 */
const VISION_LABEL: Record<Vision, () => string> = {
  normal: () => pp('visionNormal'),
  protanomaly: () => pp('visionProtanomaly'),
  deuteranomaly: () => pp('visionDeuteranomaly'),
  tritanomaly: () => pp('visionTritanomaly'),
}

/** 一套方案对这几组的结论：一句话，与弹层底部的预览说同一件事 */
function verdictText(r: RankedPalette, groups: readonly ColorGroup[]): string {
  if (r.tooFew) return pp('verdictTooFew', { colors: r.palette.colors.length, count: groups.length })
  if (!r.closest) return pp('verdictSingle')
  const de = Math.round(r.closest.deltaE)
  if (r.fits) return pp('verdictFits', { de })
  return pp('verdictClash', {
    vision: VISION_LABEL[r.closest.vision](),
    a: groups[r.closest.i]?.name || pp('unnamed'),
    b: groups[r.closest.j]?.name || pp('unnamed'),
    de,
  })
}

/** 方案的色条：一排紧挨着的色块，外面一圈细边（与样式小样张同一种「样本」语气） */
function Swatches({ colors, max = 6 }: { colors: readonly string[]; max?: number }) {
  return (
    <span
      aria-hidden
      data-palette-swatches
      className="flex h-3.5 shrink-0 overflow-hidden rounded-xs inset-ring inset-ring-border"
    >
      {colors.slice(0, max).map((c, i) => (
        <span key={i} className="w-3 shrink-0" style={{ background: c }} />
      ))}
    </span>
  )
}

/** 一组的颜色：与图例记号同形的圆点；还没有颜色（没选方案 / 超出色数）是一圈虚线 */
function GroupDot({ color }: { color: string | null }) {
  return (
    <span
      aria-hidden
      data-palette-dot
      className={cn(
        'size-2.5 shrink-0 rounded-full',
        color ? 'inset-ring inset-ring-border' : 'border border-dashed border-ink-faint',
      )}
      style={color ? { background: color } : undefined}
    />
  )
}

function SchemeLabel({ palette, withSource = false }: { palette: CatalogPalette; withSource?: boolean }) {
  const src = withSource ? paletteSource(palette.source)?.name : null
  return (
    <span data-palette-option={palette.id} className="flex min-w-0 flex-1 items-center gap-2">
      <Swatches colors={palette.colors} />
      <span className="min-w-0 truncate">{palette.name}</span>
      {palette.styleOf && <Badge data-palette-style-of>{pp('styleBadge')}</Badge>}
      {src && (
        <span data-palette-source className="ml-auto shrink-0 pl-2 text-xs font-normal text-ink-3">
          {src}
        </span>
      )}
    </span>
  )
}

/** 结论一行：前面一颗状态点（分得开 = 绿、撞色 / 色数不够 = 黄），字用说明色，不整句染色 */
function Verdict({ r, groups, where }: { r: RankedPalette; groups: readonly ColorGroup[]; where: 'current' | 'preview' }) {
  const ok = r.fits || (!r.tooFew && !r.closest)
  const attr = where === 'current' ? { 'data-palette-verdict': true } : { 'data-palette-preview-verdict': true }
  return (
    <p {...attr} className="type-caption flex items-start gap-1.5">
      <span aria-hidden className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', ok ? 'bg-ok' : 'bg-warn')} />
      <span className={cn('min-w-0', !ok && 'text-warn-content')}>{verdictText(r, groups)}</span>
    </p>
  )
}

/** 控件行：与样式面板的其它行同一副网格（标签列 `--insp-label`） */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={cn(STYLE_ROW_GRID, 'items-center py-0.5')}>
      <span className="min-w-0 truncate text-xs text-ink-2" title={label}>
        {label}
      </span>
      <div className="flex min-w-0">{children}</div>
    </div>
  )
}

/**
 * 样式面板画布档里的「配色」一节（ADR 0210 方向 A）：方案下拉 + 这张画布的组。
 *
 * - 下拉打开是带搜索的弹层：「适合这张画布」（过了硬约束、按最小色差排）在前，其余与期刊风格在「更多」；
 *   ↑↓ / 悬停只改弹层底部的预览（这几组在这套方案下的颜色与一句结论），回车选用，Esc 关上什么都不变。
 * - 选用 = 一次 commit、一步撤销（`store/paletteActions`）。手改过颜色的系列不动，结果在下面说出来。
 * - 名字像对照组的只**建议**中性灰，点了才生效；同名不同身份的组只**建议**并入，点了才合。
 *
 * 入口默认关（`lib/paletteUi.ts`），挂在 `CanvasStyleLayer` 里。
 */
export function CanvasPaletteSection() {
  useTranslation('workspace')
  const doc = useDocumentStore((s) => s.doc)
  const byKey = useRenderStore((s) => s.byKey)
  const latest = useRenderStore((s) => s.latest)
  const state = useMemo(() => canvasPaletteState(doc, { byKey, latest }), [doc, byKey, latest])
  const slots = useMemo(() => state.groups.map((g) => g.slot), [state.groups])
  const ranked = useMemo(() => rankPalettes(slots), [slots])
  const [active, setActive] = useState<string | null>(null)
  const [last, setLast] = useState<PaletteApplyResult | null>(null)
  const vision = useCvdPreviewStore((s) => s.vision)
  // 色觉预览只是「看一眼」：这一节不在了（关面板、换到单图档）就回到正常色觉，不留一张悄悄变了色的画布
  useEffect(() => () => useCvdPreviewStore.getState().setVision('normal'), [])
  // 弹层里 ↑↓ / 悬停到哪一套，画布上所有面板先换成那套颜色（只改显示，ADR 0210 §六）；关弹层 / Esc 还原
  useEffect(() => {
    if (active) showPalettePreview(planPaletteSchemePreview(active))
    else clearPalettePreview()
  }, [active])
  useEffect(() => () => clearPalettePreview(), [])
  const missed = usePalettePreviewMissed()

  if (!state.groups.length && !state.unlabeled.length) return null

  const scheme = paletteById(state.table.scheme)
  const current = scheme ? evaluate(scheme, slots) : null
  const options: SelectOption<string>[] = ranked.map((r) => {
    const fit = r.fits && !r.palette.styleOf
    const src = paletteSource(r.palette.source)
    return {
      value: r.palette.id,
      label: <SchemeLabel palette={r.palette} withSource />,
      group: fit ? pp('groupFits') : pp('groupMore'),
      searchText: [r.palette.name, r.palette.styleOf, src?.name].filter(Boolean).join(' '),
    }
  })
  // 「适合」一组排前：rankPalettes 已经是过约束的在前，期刊风格从里面挪到「更多」
  options.sort((a, b) => Number(b.group === pp('groupFits')) - Number(a.group === pp('groupFits')))

  const preview = active ? ranked.find((r) => r.palette.id === active) : null

  const pick = async (id: string) => {
    let merges: { into: string; from: string }[] = []
    // 第一次在这张画布上建色键表、又有同名不同身份的组：问一次要不要当成同一组（方向 C 的精简版）
    if (!state.stored && state.merges.length) {
      const ok = await askConfirm({
        title: pm('mergeTitle'),
        body: pm('mergeBody', {
          names: [...new Set(state.merges.map((m) => m.name))].join(pp('nameSep')),
          count: state.merges.length,
        }),
        confirmLabel: pm('mergeConfirm'),
        cancelLabel: pm('mergeKeep'),
      })
      if (ok) merges = state.merges.map(({ into, from }) => ({ into, from }))
    }
    // 画布上正预览着的就是这一套（且没有顺手并组，颜色与预览一致）：写完让预览挂着等新图，不先闪回旧色
    const keep = id === active && !merges.length
    if (!keep) clearPalettePreview()
    setLast(applyPaletteScheme(id, merges))
    if (keep) holdPalettePreview()
  }

  // 弹层底部：↑↓ 到哪一套，就先看这几组在它下面的颜色与一句结论；键位提示一直在
  const footer = (
    <div data-palette-footer className="mt-1 border-t border-border px-2 pt-1.5">
      {preview && (
        <div data-palette-preview className="mb-1.5">
          <ul className="flex flex-wrap gap-x-2.5 gap-y-1">
            {state.groups.map((g, i) => (
              <li key={g.key} className="flex min-w-0 items-center gap-1 text-xs text-ink-2">
                <GroupDot color={preview.colors[i] ?? null} />
                <span className="max-w-24 truncate">{g.name || pp('unnamed')}</span>
              </li>
            ))}
          </ul>
          <div className="mt-1">
            <Verdict r={preview} groups={state.groups} where="preview" />
          </div>
        </div>
      )}
      {preview && missed > 0 && (
        <p data-palette-preview-missed className="type-meta mb-1 text-ink-3">
          {pp('canvasPreviewMissed', { count: missed })}
        </p>
      )}
      <p className="type-meta text-ink-3">{pp('keysHint')}</p>
    </div>
  )

  const figures = new Set(state.groups.flatMap((g) => g.series.map((x) => x.panelId))).size

  return (
    <Section title={pp('title')} data-palette-section>
      <Row label={pp('scheme')}>
        <SearchSelect
          className="min-w-0 flex-1"
          ariaLabel={pp('scheme')}
          searchLabel={pp('search')}
          emptyLabel={pp('noMatch')}
          value={state.stored ? state.table.scheme : ''}
          triggerLabel={
            state.stored && scheme ? (
              <SchemeLabel palette={scheme} />
            ) : (
              <span className="text-ink-3">{pp('notChosen')}</span>
            )
          }
          options={options}
          grouped
          width={{ min: 300 }}
          onActiveChange={setActive}
          footer={footer}
          onChange={(id) => void pick(id)}
        />
      </Row>
      {!state.stored && <p className="type-caption mt-1">{pp('intro')}</p>}
      {state.groups.length > 0 && (
        <div className="mt-2">
          <div className="flex h-6 items-center justify-between gap-2">
            <span className="text-xs text-ink-2">{pp('groups')}</span>
            <span data-palette-summary className="text-xs tabular-nums text-ink-3">
              {pp('groupCount', { count: state.groups.length })} · {pp('figures', { count: figures })}
            </span>
          </div>
          <ul data-palette-groups className="flex flex-col">
            {state.groups.map((g) => (
              <GroupRow
                key={g.key}
                group={g}
                color={scheme && state.stored ? colorForSlot(g.slot, scheme) : null}
                mergeInto={state.merges.find((m) => m.from === g.key)}
                editable={state.stored}
              />
            ))}
          </ul>
        </div>
      )}
      {state.stored && current && (
        <div className="mt-1.5">
          <Verdict r={current} groups={state.groups} where="current" />
        </div>
      )}
      <PaletteNotes unlabeled={state.unlabeled.length} last={last} />
      <div className="mt-2">
        <Row label={pp('vision')}>
          <Select
            className="min-w-0 flex-1"
            ariaLabel={pp('visionPreview')}
            value={vision}
            onChange={(v) => useCvdPreviewStore.getState().setVision(v as Vision)}
            options={VISIONS.map((v) => ({ value: v, label: VISION_LABEL[v]() }))}
          />
        </Row>
      </div>
      {vision !== 'normal' && (
        <p data-palette-vision-hint className="type-caption mt-1">
          {pp('visionHint')}
        </p>
      )}
    </Section>
  )
}

function GroupRow({
  group,
  color,
  mergeInto,
  editable,
}: {
  group: ColorGroup
  color: string | null
  mergeInto?: { into: string; from: string; name: string }
  editable: boolean
}) {
  const figures = new Set(group.series.map((s) => s.panelId)).size
  const neutral = group.slot === 'neutral'
  let action: { label: string; run: () => void } | null = null
  if (editable && mergeInto) {
    action = { label: pp('mergeInto', { name: mergeInto.name }), run: () => mergePaletteGroups(mergeInto.into, mergeInto.from) }
  } else if (editable && group.suggestNeutral) {
    action = { label: pp('useNeutral'), run: () => setPaletteGroupNeutral(group.key, true) }
  } else if (editable && neutral) {
    action = { label: pp('dropNeutral'), run: () => setPaletteGroupNeutral(group.key, false) }
  }
  return (
    <li data-palette-group={group.key} className="group/row flex h-7 min-w-0 items-center gap-2">
      <GroupDot color={color} />
      <span className="min-w-0 truncate text-sm" title={group.name}>
        {group.name || <span className="text-ink-3">{pp('unnamed')}</span>}
      </span>
      {neutral && (
        <span data-palette-neutral className="shrink-0 text-xs text-ink-3">
          {pp('neutral')}
        </span>
      )}
      <span className="ml-auto flex shrink-0 items-center">
        {action ? (
          <Button
            size="sm"
            variant="ghost"
            data-palette-action={group.key}
            className="-mr-2 px-2 text-xs text-ink-2 hover:text-ink"
            title={pp('figures', { count: figures })}
            onClick={action.run}
          >
            {action.label}
          </Button>
        ) : (
          <span className="text-xs tabular-nums text-ink-3">{pp('figures', { count: figures })}</span>
        )}
      </span>
    </li>
  )
}

function PaletteNotes({ unlabeled, last }: { unlabeled: number; last: PaletteApplyResult | null }) {
  const lines = [
    unlabeled > 0 ? pp('unlabeled', { count: unlabeled }) : null,
    last && last.skipped > 0 ? pp('skipped', { count: last.skipped }) : null,
    last && last.uncolored > 0 ? pp('uncolored', { count: last.uncolored }) : null,
  ].filter((x): x is string => !!x)
  if (!lines.length) return null
  return (
    <div data-palette-notes className="mt-1.5 flex flex-col gap-0.5">
      {lines.map((l) => (
        <p key={l} className="type-caption">
          {l}
        </p>
      ))}
    </div>
  )
}
