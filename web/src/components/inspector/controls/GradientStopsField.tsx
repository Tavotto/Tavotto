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

export interface GradientValue {
  stops: [string, string]
  direction?: 'vertical' | 'horizontal'
  /** 屏幕起端对应数据坐标的哪一头（引擎事实；写回时原样带上，让重放不依赖当时的坐标变换） */
  screenStart?: 'high' | 'low'
}

/** 引擎发来的值 → 两端颜色；形状不对时回 null（调用方退回原样渲染） */
export function readGradient(value: unknown): GradientValue | null {
  if (!value || typeof value !== 'object') return null
  const v = value as { stops?: unknown; direction?: unknown; screen_start?: unknown }
  if (!Array.isArray(v.stops) || v.stops.length !== 2) return null
  const [a, b] = v.stops
  if (typeof a !== 'string' || typeof b !== 'string') return null
  const screenStart = v.screen_start === 'high' || v.screen_start === 'low' ? v.screen_start : undefined
  return {
    stops: [a, b],
    direction: v.direction === 'horizontal' ? 'horizontal' : 'vertical',
    ...(screenStart ? { screenStart } : {}),
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
  }
}

/** 写回的 override 值：两端颜色 + 引擎事实 `screen_start`（没有就不带，引擎走旧形状兜底） */
export function gradientPayload(stops: [string, string], g: GradientValue) {
  return g.screenStart ? { stops, screen_start: g.screenStart } : { stops }
}

export function GradientStopsField({
  value,
  label,
  mixed,
  onChange,
  onGestureEnd,
}: {
  value: GradientValue
  /** 这一行的名字（读屏用：「渐变 · 上端颜色」） */
  label: string
  /** 多选且各成员两端颜色不一致：两个色块都说「多个值」，渐变条画成中性底 */
  mixed?: boolean
  onChange: (stops: [string, string]) => void
  onGestureEnd?: () => void
}) {
  const { t } = useTranslation('inspector')
  const horizontal = value.direction === 'horizontal'
  const [start, end] = value.stops
  const startName = t(horizontal ? 'gradientStops.left' : 'gradientStops.top')
  const endName = t(horizontal ? 'gradientStops.right' : 'gradientStops.bottom')
  return (
    <div className="flex w-full min-w-0 items-center gap-1.5" data-gradient-stops>
      <ColorField
        swatchOnly
        mixed={mixed}
        ariaLabel={t('gradientStops.stopLabel', { field: label, end: startName })}
        value={start}
        onChange={(v) => onChange([v, end])}
        onGestureEnd={onGestureEnd}
      />
      <div
        aria-hidden
        title={`${startName} ${start.toUpperCase()} → ${endName} ${end.toUpperCase()}`}
        className="h-4 min-w-0 flex-1 rounded-xs border border-border"
        style={{ background: mixed ? undefined : `linear-gradient(to right, ${start}, ${end})` }}
        data-gradient-bar
      />
      <ColorField
        swatchOnly
        // 止端色块贴着属性栏右边：面板向左展开，不被裁
        popoverAlign="end"
        mixed={mixed}
        ariaLabel={t('gradientStops.stopLabel', { field: label, end: endName })}
        value={end}
        onChange={(v) => onChange([start, v])}
        onGestureEnd={onGestureEnd}
      />
    </div>
  )
}
