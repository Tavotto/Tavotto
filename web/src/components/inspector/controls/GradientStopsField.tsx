/**
 * 渐变色带的两端颜色（引擎字段 `gradient`，`overrides.gradient_detect` 认出来的 PolyCollection）。
 *
 * 一行：起端色块 · 用这两端画的渐变条 · 止端色块。起端 / 止端按**屏幕**方向说（竖向渐变是上 / 下，横向是左 / 右），
 * 与引擎 `gradient_stops` 同一个口径。写回的值是 `{stops: [起端, 止端]}`；引擎保留原渐变的深浅节奏换两端颜色。
 * 两个色块都是普通 `ColorField`（宿主挂了取色面板就开面板，否则系统取色盘），取色是连续动作，事务由调用方的
 * `onGestureEnd` 收口——与「填充颜色」同一套。
 */
import { useTranslation } from 'react-i18next'
import { ColorField } from '../../ui/Input'
import { Segmented } from '../../ui/Segmented'

/** 一块候选图例色块（引擎 `manifest._gradient_legend_value`）：`ref` = [ax.collections 下标, 色块序号] */
export interface LegendCandidate {
  ref: [number, number]
  gid: string
  /** 此刻两端颜色（屏幕上 / 下） */
  stops: [string, string]
  /** 紧挨在它右边的图例文字（如 TiO₂）；找不到就没有 */
  caption?: string
}

/** 「图例色块跟着改」的引擎事实（ADR 0167 修订）：候选、此刻跟着改的那块、自动配对的那块 */
export interface LegendLink {
  candidates: LegendCandidate[]
  linked: [number, number] | null
  auto: [number, number] | null
}

export interface GradientValue {
  stops: [string, string]
  direction?: 'vertical' | 'horizontal'
  /** 屏幕起端对应数据坐标的哪一头（引擎事实；写回时原样带上，让重放不依赖当时的坐标变换） */
  screenStart?: 'high' | 'low'
  /** 同一个子图里颜色对得上的图例色块（引擎事实；没有候选就缺席） */
  legend?: LegendLink
}

function readRef(v: unknown): [number, number] | null {
  return Array.isArray(v) && v.length === 2 && v.every((n) => Number.isInteger(n)) ? [v[0], v[1]] : null
}

function readLegend(v: unknown): LegendLink | undefined {
  if (!v || typeof v !== 'object') return undefined
  const o = v as { candidates?: unknown; linked?: unknown; auto?: unknown }
  if (!Array.isArray(o.candidates)) return undefined
  const candidates: LegendCandidate[] = []
  for (const c of o.candidates) {
    const ref = readRef(c?.ref)
    const stops = c?.stops
    if (!ref || typeof c?.gid !== 'string' || !Array.isArray(stops) || stops.length !== 2) continue
    if (typeof stops[0] !== 'string' || typeof stops[1] !== 'string') continue
    candidates.push({
      ref,
      gid: c.gid,
      stops: [stops[0], stops[1]],
      ...(typeof c.caption === 'string' && c.caption ? { caption: c.caption } : {}),
    })
  }
  if (!candidates.length) return undefined
  return { candidates, linked: readRef(o.linked), auto: readRef(o.auto) }
}

const refKey = (r: [number, number]) => `${r[0]}:${r[1]}`

/** 引擎发来的值 → 两端颜色；形状不对时回 null（调用方退回原样渲染） */
export function readGradient(value: unknown): GradientValue | null {
  if (!value || typeof value !== 'object') return null
  const v = value as {
    stops?: unknown
    direction?: unknown
    screen_start?: unknown
  }
  if (!Array.isArray(v.stops) || v.stops.length !== 2) return null
  const [a, b] = v.stops
  if (typeof a !== 'string' || typeof b !== 'string') return null
  const screenStart = v.screen_start === 'high' || v.screen_start === 'low' ? v.screen_start : undefined
  const legend = readLegend((value as { legend?: unknown }).legend)
  return {
    stops: [a, b],
    direction: v.direction === 'horizontal' ? 'horizontal' : 'vertical',
    ...(screenStart ? { screenStart } : {}),
    ...(legend ? { legend } : {}),
  }
}

/**
 * 当前值（override 或引擎值）里取两端颜色；方向与 `screen_start` 是引擎事实，取字段自己的 `value`——
 * override 值只有 `{stops, screen_start}`，没有 direction，直接读会把横向渐变的「左 / 右」读成「上 / 下」。
 */
export function readGradientWithFact(current: unknown, fact: unknown): GradientValue | null {
  const cur = readGradient(current)
  if (!cur) return null
  const f = readGradient(fact)
  return {
    stops: cur.stops,
    direction: f?.direction ?? cur.direction,
    ...((f?.screenStart ?? cur.screenStart) ? { screenStart: f?.screenStart ?? cur.screenStart } : {}),
    // 图例配对只认引擎事实（override 值里的 legend 只是一个 ref，候选与此刻生效的那块在 manifest 里）
    ...(f?.legend ? { legend: f.legend } : {}),
  }
}

/**
 * 写回的 override 值：两端颜色 + 引擎事实 `screen_start`（没有就不带，引擎走旧形状兜底）。
 * 有图例候选时**把此刻的配对写死**（`legend: ref | null`，`link` 给了就用它）：重放按记下的那块来，不再重新猜。
 */
export function gradientPayload(stops: [string, string], g: GradientValue, link?: [number, number] | null) {
  const out: {
    stops: [string, string]
    screen_start?: 'high' | 'low'
    legend?: [number, number] | null
  } = { stops }
  if (g.screenStart) out.screen_start = g.screenStart
  if (link !== undefined) out.legend = link
  else if (g.legend) out.legend = g.legend.linked
  return out
}

export function GradientStopsField({
  value,
  label,
  mixed,
  onChange,
  onLink,
  onGestureEnd,
}: {
  value: GradientValue
  /** 这一行的名字（读屏用：「渐变 · 上端颜色」） */
  label: string
  /** 多选且各成员两端颜色不一致：两个色块都说「多个值」，渐变条画成中性底 */
  mixed?: boolean
  onChange: (stops: [string, string]) => void
  /** 换跟着改的图例色块（null = 不跟）；不给就不画那一行（多选时成员各有各的候选） */
  onLink?: (ref: [number, number] | null) => void
  onGestureEnd?: () => void
}) {
  const { t } = useTranslation('inspector')
  const horizontal = value.direction === 'horizontal'
  const [start, end] = value.stops
  const startName = t(horizontal ? 'gradientStops.left' : 'gradientStops.top')
  const endName = t(horizontal ? 'gradientStops.right' : 'gradientStops.bottom')
  // 唯一对得上、已经配上：静默跟着改，不出这一行（用户 2026-11-03：默认直接跟，不要多余的说明）。
  // 只有要用户挑的时候（几块同色 / 这一块也对得上别的峰）才出来
  const legendAll = onLink && !mixed ? value.legend : undefined
  const legend = legendAll && !(legendAll.linked && legendAll.candidates.length === 1) ? legendAll : undefined
  // 没配上（候选不唯一）：等用户点一块
  return (
    <div className="flex w-full min-w-0 flex-col gap-1.5">
      <div className="flex w-full min-w-0 items-center gap-1.5" data-gradient-stops>
        <ColorField
          swatchOnly
          mixed={mixed}
          ariaLabel={t('gradientStops.stopLabel', {
            field: label,
            end: startName,
          })}
          value={start}
          onChange={(v) => onChange([v, end])}
          onGestureEnd={onGestureEnd}
        />
        <div
          aria-hidden
          title={`${startName} ${start.toUpperCase()} → ${endName} ${end.toUpperCase()}`}
          className="h-4 min-w-0 flex-1 rounded-xs border border-border"
          style={{
            background: mixed ? undefined : `linear-gradient(to right, ${start}, ${end})`,
          }}
          data-gradient-bar
        />
        <ColorField
          swatchOnly
          // 止端色块贴着属性栏右边：面板向左展开，不被裁
          popoverAlign="end"
          mixed={mixed}
          ariaLabel={t('gradientStops.stopLabel', {
            field: label,
            end: endName,
          })}
          value={end}
          onChange={(v) => onChange([start, v])}
          onGestureEnd={onGestureEnd}
        />
      </div>
      {legend && (
        <div className="flex min-w-0 flex-col gap-1" data-gradient-legend>
          <span className="text-xs text-ink-2">{t('gradientStops.legend')}</span>
          {/* 几块颜色都对得上（或这一块也对得上别的峰）：列出来让用户点；没点之前改色，方块都不动 */}
          <div className="min-w-0 overflow-x-auto">
            <Segmented<string>
              ariaLabel={t('gradientStops.legendAria', { field: label })}
              value={legend.linked ? refKey(legend.linked) : null}
              onChange={(k) => onLink!(legend.candidates.find((c) => refKey(c.ref) === k)!.ref)}
              items={legend.candidates.map((c, i) => {
                const name = c.caption ?? t('gradientStops.swatchN', { n: i + 1 })
                return {
                  value: refKey(c.ref),
                  label: name,
                  ariaLabel: t('gradientStops.followSwatch', { name }),
                  icon: <SwatchChip stops={c.stops} />,
                }
              })}
            />
          </div>
        </div>
      )}
    </div>
  )
}

/** 图例方块的小样（竖向渐变，上端在上） */
function SwatchChip({ stops }: { stops: [string, string] }) {
  return (
    <span
      aria-hidden
      data-legend-swatch
      className="inline-block h-3 w-2 shrink-0 border border-border"
      style={{
        background: `linear-gradient(to bottom, ${stops[0]}, ${stops[1]})`,
      }}
    />
  )
}
