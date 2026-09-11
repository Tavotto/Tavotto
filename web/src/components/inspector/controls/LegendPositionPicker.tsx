import { useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import {
  LEGEND_OUTSIDE_PRESETS,
  outsidePresetOf,
  type LegendAnchor,
  type LegendPlacement,
} from '@/lib/legendModel'
import { cn } from '@/lib/utils'
import { optionLabel } from '../roles/registry'
import { NumberField } from '../../ui/Input'
import { Tip } from '../../ui/Tooltip'

/**
 * 图例位置：**一张可点的方位示意图**（审计 T17 + 2026-09-07 的外侧锚点，
 * 2026-09-10 重排成空间选择器）。
 *
 *   框内  九个位置点 = 现有 `GRID` 的九个 `loc`——写 `loc`，锚框清掉；
 *   框外  六个外侧预设贴在框的相应方位——写 `loc` + `loc_anchor`
 *         （父容器分数坐标里的一个点，`1.02` = 子图右边缘往外 2%）；
 *   自动  `best` 是同一组单选里的一档，不是独立开关。
 *
 * 全部档位在**一个** radiogroup 里：Tab 只进一次（roving tabindex），方向键按
 * 单选组约定移动焦点并选中。示意图只表达「预设方位」，不是按真实比例渲染的
 * 缩略图——按钮的百分比坐标绝不会反向变成 `bbox_to_anchor`。
 *
 * 选中状态完全由 `value` / `anchor` / `outsidePresetOf` 推导，本地 state 只有
 * 「锚点」那块是否展开。图外右上用的是 `loc='upper left'`（图例贴锚点的那个
 * 角），所以九宫格是否选中必须看 `anchor === null`，是否属于外侧预设必须问
 * `outsidePresetOf`，不能只比 loc。锚点不属于任何预设时既不勉强选中最近的
 * 预设，也不画一个猜的落点——状态行写出锚点数字，真实位置看画布。
 *
 * 容器认不出来（脚本自己造的图例、fig.legend）时不写名字——宁可不写，也不写
 * 一个猜的。引擎不发 `loc_anchor`（脚本用了 4 元组锚框 / 非父容器变换）时整个
 * 外侧带不出现、留白一并收回，理由由 `UnsupportedProps` 那条说出口。没给
 * `onPlace` 时凡是要写锚点的动作都禁用，而不是看似成功地写一半。
 */

const GRID: string[][] = [
  ['upper left', 'upper center', 'upper right'],
  ['center left', 'center', 'center right'],
  ['lower left', 'lower center', 'lower right'],
]

type Point = { x: number; y: number }

/**
 * 示意图的布局坐标（viewBox 单位；示意图最宽 260px，那时 1 单位 = 1px）。
 * 只管按钮与图形摆在哪，**不是**引擎坐标——外侧位按 preset id 明确映射到
 * 展示方位，不按数组下标猜业务含义。
 */
interface Geometry {
  view: { w: number; h: number }
  /** 代表宿主子图的那个框 */
  box: { x: number; y: number; w: number; h: number }
  /** 框内三列 / 三行位置点的中心，与 GRID 的列 / 行一一对应 */
  cols: [number, number, number]
  rows: [number, number, number]
  /** 外侧预设 id → 展示方位 */
  outside: Record<string, Point>
}

/** 有外侧带：四周留白给六个外侧位 */
const WITH_OUTSIDE: Geometry = {
  view: { w: 260, h: 160 },
  box: { x: 34, y: 28, w: 192, h: 104 },
  cols: [53, 130, 207],
  rows: [46, 80, 114],
  outside: {
    rightTop: { x: 246, y: 46 },
    rightCenter: { x: 246, y: 80 },
    rightBottom: { x: 246, y: 114 },
    topCenter: { x: 130, y: 14 },
    bottomCenter: { x: 130, y: 146 },
    leftCenter: { x: 14, y: 80 },
  },
}

/** 没有外侧带：留白收回，框撑满 */
const INSIDE_ONLY: Geometry = {
  view: { w: 260, h: 124 },
  box: { x: 20, y: 7, w: 220, h: 110 },
  cols: [42, 130, 218],
  rows: [24, 62, 100],
  outside: {},
}

/** 图例小标记的尺寸（示意用，不按真实比例——真实比例这么小的图上看不出来） */
const MARK = { w: 16, h: 11 }
/** 外侧档位在 radio 组里的 id 前缀：与九宫格的 loc 名分开，避免 `upper left` 撞车 */
const OUTSIDE_PREFIX = 'outside:'

const ins = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'inspector', ...(values ?? {}) })

const pct = (v: number, total: number) => `${(v / total) * 100}%`

/**
 * 外侧预设在示意图里的展示方位。认得的 id 走明确映射；将来新增的预设按锚点
 * 落在容器上的位置摆，夹在画布内——只影响它画在哪，不影响写入什么。
 */
function outsideSlot(geo: Geometry, id: string, anchor: LegendAnchor): Point {
  const known = geo.outside[id]
  if (known) return known
  const clamp = (v: number, max: number) => Math.min(Math.max(v, 14), max - 14)
  return {
    x: clamp(geo.box.x + anchor[0] * geo.box.w, geo.view.w),
    y: clamp(geo.box.y + (1 - anchor[1]) * geo.box.h, geo.view.h),
  }
}

interface Slot {
  /** radio 组里的 id：框内是 loc 名，框外是 `outside:<preset id>` */
  id: string
  band: 'inside' | 'outside'
  label: string
  x: number
  y: number
  enabled: boolean
  active: boolean
}

/**
 * 一个档位的图形。选中 = 带两条短线的实心小图例（形状 + 颜色一起说「选中」）；
 * 未选中框内是空心圆点、框外是空心小图例。画在按钮之上的一层 SVG 里，所以
 * 随示意图一起缩放，按钮只负责命中区与底色。
 */
function SlotGlyph({ slot }: { slot: Slot }) {
  const { x, y } = slot
  const l = x - MARK.w / 2
  const t = y - MARK.h / 2
  if (slot.active) {
    return (
      <g>
        <rect x={l} y={t} width={MARK.w} height={MARK.h} rx={2} className="fill-ink" />
        <g strokeWidth={1.2} strokeLinecap="round" className="stroke-surface">
          <line x1={l + 3} x2={l + 11} y1={y - 1.5} y2={y - 1.5} />
          <line x1={l + 3} x2={l + 9} y1={y + 1.5} y2={y + 1.5} />
        </g>
      </g>
    )
  }
  const dim = slot.enabled ? undefined : 'opacity-40'
  if (slot.band === 'inside') {
    return (
      <circle cx={x} cy={y} r={3} fill="none" strokeWidth={1} className={cn('stroke-ink-faint', dim)} />
    )
  }
  return (
    <g
      fill="none"
      strokeWidth={1}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('stroke-ink-faint', dim)}
    >
      <path
        d={`M${l + 2} ${t} H${l + MARK.w - 2} a2 2 0 0 1 2 2 V${t + MARK.h - 2} a2 2 0 0 1 -2 2 H${l + 2} a2 2 0 0 1 -2 -2 V${t + 2} a2 2 0 0 1 2 -2 Z`}
      />
      <line x1={l + 3} x2={l + 11} y1={y - 1.5} y2={y - 1.5} />
      <line x1={l + 3} x2={l + 9} y1={y + 1.5} y2={y + 1.5} />
    </g>
  )
}

/** 展开 / 收起的记号：与全产品的下拉记号同一枚 chevron-down（第四节），展开时转到朝上 */
function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronDown
      size={ICON_SIZE.xs}
      aria-hidden
      className={cn('shrink-0 transition-transform duration-fast', open && 'rotate-180')}
    />
  )
}

const NAV_KEYS = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End']

export function LegendPositionPicker({
  value,
  options,
  onChange,
  ariaLabel,
  containerLabel,
  anchor = null,
  anchorSupported = false,
  anchorRange,
  onPlace,
}: {
  /**
   * 当前 `loc`。**多选取值不一致时传 null**——那时一个格子都不该被标成选中，
   * 也不该把「空」当成一个自定义值塞进选项表。
   */
  value: string | null
  options: string[]
  /** 只改 `loc` 的写入（锚点由 `onPlace` 管；没有外侧带时它就是全部） */
  onChange: (v: string) => void
  ariaLabel: string
  /** 九个档位参照的容器名（宿主子图）；认不出来时不显示 */
  containerLabel?: string
  /** 当前锚点；`null` = 没有锚框（图例在容器内侧）。多选不一致时也传 null */
  anchor?: LegendAnchor | null
  /** 引擎宣称了 `loc_anchor` 这条能力——没有它整个外侧带不出现 */
  anchorSupported?: boolean
  /**
   * 锚点两个数字的取值范围。**取自 manifest 那条字段**（`min` / `max`），
   * 不在这里另写一份——范围是引擎说了算的，抄第二份就会漂。
   */
  anchorRange?: { min?: number; max?: number }
  /**
   * 一次写下整个摆法（内 / 外都走它）。没给时只有「没有锚点的图内切换」还能
   * 走 `onChange(loc)`；要写或清锚点的动作一律禁用。
   */
  onPlace?: (next: LegendPlacement) => void
}) {
  const hintId = useId()
  const panelId = useId()
  const groupRef = useRef<HTMLDivElement>(null)
  /** 「锚点」数字是否展开：纯 UI 状态，不写文档，写回也不会把它折回去 */
  const [refined, setRefined] = useState(false)

  const has = (v: string) => options.includes(v)
  const canPlace = onPlace !== undefined
  const within = containerLabel
    ? translate('control.legendPositionWithin', { ns: 'inspector', label: containerLabel })
    : null

  const placement: LegendPlacement | null = value === null ? null : { loc: value, anchor }
  const activePreset = placement ? outsidePresetOf(placement) : null
  const outside = anchor !== null
  /** 图内的一次点击要把锚点一并清掉；没有 onPlace 又有锚点时做不到，整带禁用 */
  const insideEnabled = canPlace || anchor === null
  const inRange = (v: number) =>
    (anchorRange?.min === undefined || v >= anchorRange.min) &&
    (anchorRange?.max === undefined || v <= anchorRange.max)

  const place = (next: LegendPlacement) => {
    if (onPlace) onPlace(next)
    else if (next.anchor === null) onChange(next.loc)
  }
  /** 图内的一次点击：回到容器内侧（有锚点就一并清掉） */
  const pickInside = (loc: string) => place({ loc, anchor: null })

  const geo = anchorSupported ? WITH_OUTSIDE : INSIDE_ONLY
  const slots: Slot[] = []
  GRID.forEach((row, r) =>
    row.forEach((loc, c) => {
      if (!has(loc)) return
      slots.push({
        id: loc,
        band: 'inside',
        label: optionLabel('loc', loc),
        x: geo.cols[c],
        y: geo.rows[r],
        enabled: insideEnabled,
        // 外侧摆着的时候九宫格里一个都不标选中：那个 loc 此刻说的是
        // 「贴锚点的哪个角」，不是「在容器里的哪一格」
        active: !outside && value === loc,
      })
    }),
  )
  if (anchorSupported) {
    for (const p of LEGEND_OUTSIDE_PRESETS) {
      const { x, y } = outsideSlot(geo, p.id, p.anchor)
      slots.push({
        id: OUTSIDE_PREFIX + p.id,
        band: 'outside',
        label: ins(`control.legendOutside.${p.id}`),
        x,
        y,
        // 预设的锚点超出 manifest 范围时禁用，不静默裁剪
        enabled: canPlace && has(p.loc) && p.anchor.every(inRange),
        active: activePreset === p.id,
      })
    }
  }

  const showBest = has('best')
  const bestActive = !outside && value === 'best'
  // "right" 是 matplotlib 的历史别名（≈ center right），只有当前值恰好是它时才显示
  const showLegacy = value === 'right' && has('right')
  const legacyActive = !outside && value === 'right'

  const select = (id: string) => {
    if (id.startsWith(OUTSIDE_PREFIX)) {
      const preset = LEGEND_OUTSIDE_PRESETS.find((p) => OUTSIDE_PREFIX + p.id === id)
      const slot = slots.find((s) => s.id === id)
      if (preset && slot?.enabled) place({ loc: preset.loc, anchor: [...preset.anchor] })
    } else if (insideEnabled) {
      pickInside(id)
    }
  }

  // roving tabindex：Tab 只进当前档；没有当前档（多选混合 / custom）进第一可用项但不选中
  const selectedId =
    value === null ? null : outside ? (activePreset ? OUTSIDE_PREFIX + activePreset : null) : value
  const enabledIds = [
    ...(showBest && insideEnabled ? ['best'] : []),
    ...slots.filter((s) => s.enabled).map((s) => s.id),
    ...(showLegacy && insideEnabled ? ['right'] : []),
  ]
  const entryId =
    selectedId !== null && enabledIds.includes(selectedId) ? selectedId : (enabledIds[0] ?? null)
  const tabIndexOf = (id: string) => (id === entryId ? 0 : -1)

  // 单选组的键盘约定（APG radio）：方向键移动焦点并选中，跳过禁用项、可循环。
  // 只认焦点落在本组 radio 上的按键——锚点输入框不在这个容器里，不会被劫走。
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!NAV_KEYS.includes(e.key)) return
    const radios = Array.from(
      groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]:not(:disabled)') ?? [],
    )
    const index = radios.indexOf(e.target as HTMLButtonElement)
    if (index < 0) return
    e.preventDefault()
    const forward = e.key === 'ArrowRight' || e.key === 'ArrowDown'
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? radios.length - 1
          : (index + (forward ? 1 : -1) + radios.length) % radios.length
    const target = radios[next]
    if (target.dataset.option) select(target.dataset.option)
    target.focus()
  }

  // 九宫格下面原有一行「此刻选的是什么」的状态文字，按 2026-09-11 设计包去掉：
  // 选中格子已经高亮，radio 的 aria-checked 也在说同一件事。锚点展开钮留下。

  const showRefine = anchorSupported && canPlace && value !== null && anchor !== null
  // 只在锚点确实探到容器外时提一句「可能超出」；是否真溢出由预检说了算
  const mayOverflow = anchorSupported && anchor !== null && anchor.some((v) => v < 0 || v > 1)

  return (
    <div className="flex w-full min-w-0 flex-col gap-1.5">
      <div
        ref={groupRef}
        role="radiogroup"
        aria-label={ariaLabel}
        aria-describedby={within ? hintId : undefined}
        onKeyDown={onKeyDown}
        className="flex w-full min-w-0 flex-col gap-1"
      >
        {(within || showBest) && (
          <div className="flex min-h-6 items-center justify-between gap-2">
            {within ? (
              <span id={hintId} className="min-w-0 truncate text-xs text-ink-3" title={within}>
                {within}
              </span>
            ) : (
              <span />
            )}
            {showBest && (
              <button
                type="button"
                role="radio"
                aria-checked={bestActive}
                data-option="best"
                tabIndex={tabIndexOf('best')}
                disabled={!insideEnabled}
                onClick={() => pickInside('best')}
                className={cn(
                  'relative flex h-6 shrink-0 items-center justify-center rounded-sm border px-5 text-xs outline-none transition-colors',
                  'focus-visible:focus-ring disabled:opacity-40',
                  bestActive
                    ? 'border-transparent bg-selected font-medium text-ink'
                    : 'border-border text-ink-2 hover:border-border-strong hover:text-ink',
                )}
              >
                <Check
                  size={ICON_SIZE.xs}
                  aria-hidden
                  className={cn('absolute left-1.5', !bestActive && 'invisible')}
                />
                {optionLabel('loc', 'best')}
              </button>
            )}
          </div>
        )}

        {/* 方位示意图：框 = 宿主子图；按钮是命中区，图形画在上面那层 SVG 里。
            只有外侧带存在时它才作为一张「容器边界 + 图例落点」的图对读屏说话，
            纯图内时九个 radio 已经把话说完了。 */}
        <div
          className="relative mx-auto w-full max-w-[260px]"
          style={{ aspectRatio: `${geo.view.w} / ${geo.view.h}` }}
        >
          <svg
            viewBox={`0 0 ${geo.view.w} ${geo.view.h}`}
            role={anchorSupported ? 'img' : undefined}
            aria-label={anchorSupported ? ins('control.legendPreviewAria') : undefined}
            aria-hidden={anchorSupported ? undefined : true}
            className="pointer-events-none absolute inset-0 z-10 h-full w-full"
          >
            <rect
              x={geo.box.x}
              y={geo.box.y}
              width={geo.box.w}
              height={geo.box.h}
              rx={2}
              fill="none"
              strokeWidth={1}
              className="stroke-border-strong"
            />
            {slots.map((slot) => (
              <SlotGlyph key={slot.id} slot={slot} />
            ))}
          </svg>
          {slots.map((slot) => (
            <Tip key={slot.id} label={slot.label}>
              <button
                type="button"
                role="radio"
                aria-checked={slot.active}
                aria-label={slot.label}
                data-option={slot.id}
                tabIndex={tabIndexOf(slot.id)}
                disabled={!slot.enabled}
                onClick={() => select(slot.id)}
                style={{ left: pct(slot.x, geo.view.w), top: pct(slot.y, geo.view.h) }}
                className={cn(
                  'absolute h-7 w-7 -translate-x-1/2 -translate-y-1/2 rounded-sm outline-none transition-colors',
                  'focus-visible:focus-ring disabled:opacity-40',
                  slot.active ? 'bg-selected' : 'enabled:hover:bg-surface-hover',
                )}
              />
            </Tip>
          ))}
        </div>

        {showLegacy && (
          <button
            type="button"
            role="radio"
            aria-checked={legacyActive}
            data-option="right"
            tabIndex={tabIndexOf('right')}
            disabled={!insideEnabled}
            onClick={() => pickInside('right')}
            className={cn(
              'flex h-6 items-center self-start rounded-sm border px-1.5 text-xs outline-none transition-colors',
              'focus-visible:focus-ring disabled:opacity-40',
              legacyActive
                ? 'border-transparent bg-selected text-ink'
                : 'border-border text-ink-2 hover:border-border-strong hover:text-ink',
            )}
          >
            {optionLabel('loc', 'right')}
          </button>
        )}
      </div>

      {showRefine && (
        <div className="flex min-h-6 items-center justify-end gap-2">
          <button
            type="button"
            aria-expanded={refined}
            aria-controls={panelId}
            onClick={() => setRefined((r) => !r)}
            className="flex h-6 shrink-0 items-center gap-0.5 rounded-sm px-1 text-xs text-ink-2 outline-none transition-colors hover:text-ink focus-visible:focus-ring"
          >
            {ins('control.legendAnchorLabel')}
            <Chevron open={refined} />
          </button>
        </div>
      )}

      {showRefine && anchor !== null && value !== null && (
        // 收起时留在 DOM 里（hidden），展开与否不影响受控值
        <div
          id={panelId}
          hidden={!refined}
          className="grid grid-cols-2 gap-1.5 border-t border-border pt-1.5"
        >
          <div className="flex min-w-0 items-center gap-1">
            <span aria-hidden className="shrink-0 text-xs text-ink-3">
              X
            </span>
            <NumberField
              className="min-w-0 flex-1"
              ariaLabel={ins('control.legendAnchorX')}
              value={anchor[0]}
              min={anchorRange?.min}
              max={anchorRange?.max}
              step={0.01}
              precision={2}
              onChange={(v) => place({ loc: value, anchor: [v, anchor[1]] })}
            />
          </div>
          <div className="flex min-w-0 items-center gap-1">
            <span aria-hidden className="shrink-0 text-xs text-ink-3">
              Y
            </span>
            <NumberField
              className="min-w-0 flex-1"
              ariaLabel={ins('control.legendAnchorY')}
              value={anchor[1]}
              min={anchorRange?.min}
              max={anchorRange?.max}
              step={0.01}
              precision={2}
              onChange={(v) => place({ loc: value, anchor: [anchor[0], v] })}
            />
          </div>
        </div>
      )}

      {mayOverflow && (
        // 外侧图例很容易探出图幅，导出时那一块会被静默裁掉——预检
        // `element-outside-figure` 会把它报出来，这里只先说一句「可能」
        <p className="text-xs leading-snug text-ink-3">
          {ins('control.legendOutsideOverflowHint')}
        </p>
      )}

      {value === 'custom' && (
        <p className="text-xs leading-snug text-ink-3">
          {translate('control.legendCustomHint', { ns: 'inspector' })}
        </p>
      )}
    </div>
  )
}
