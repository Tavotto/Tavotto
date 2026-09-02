import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, MoveDown, MoveUp } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { legendEntryViews, type LegendEntryView } from '@/lib/legendModel'
import { cn } from '@/lib/utils'
import { setOverride, setOverrides, unhideElement } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { msg } from '@/i18n'
import { Button } from '../ui/Button'
import { Tip } from '../ui/Tooltip'
import { TypographyControls } from './controls/TypographyControls'
import { FIGURE_TEXT_BATCH_PROPS, useFigureTypography } from './typographyAdapter'

/**
 * 图例卡（ADR 0034）：选中图例时常驻在属性区首屏的两块——
 *
 *   * **文字**：一份 Typography 控件，批量作用于全部图例项（字体 / 字号 /
 *     粗斜 / 颜色），走 `useFigureTypography` 的批量适配器；
 *   * **条目**：按显示顺序列出每一项——示意线预览、文字、「跟随 / 自定义」
 *     徽标、显隐、上下移动。点文字即选中那一项（属性页切到它，示意线样式
 *     与绑定在那里改）。
 *
 * 卡片承接掉的图例字段：`fontsize`（由 Typography 接管）与 `entry_order`
 * （由条目列表接管），通用列表要把它们让出来——同一属性不出两套控件。
 *
 * 行里没有嵌套的可交互元素：文字是一个按钮，显隐 / 上移 / 下移是各自独立
 * 的按钮，并排在同一行。
 */

/** 卡片承接掉的图例字段 */
export const LEGEND_CARD_PROPS = ['fontsize', 'entry_order'] as const

const lg = (key: string, values?: Record<string, unknown>) =>
  translate(`legend.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function LegendCard({
  panel,
  manifest,
  legend,
  labelWidth = 72,
}: {
  panel: PanelObject
  manifest: Manifest
  legend: ManifestElement
  labelWidth?: number
}) {
  useTranslation('inspector')
  const views = legendEntryViews(panel, manifest, legend)
  const entryElements = views.map((v) => v.element)
  const typography = useFigureTypography(panel, entryElements, FIGURE_TEXT_BATCH_PROPS)
  const hasTypography = FIGURE_TEXT_BATCH_PROPS.some((p) => typography.fieldOf(p))

  const order = views.map((v) => v.info.index)
  const move = (i: number, delta: -1 | 1) => {
    const j = i + delta
    if (j < 0 || j >= order.length) return
    const next = [...order]
    ;[next[i], next[j]] = [next[j], next[i]]
    setOverride(panel.id, legend.gid, 'entry_order', next, true)
  }
  const toggleHidden = (v: LegendEntryView) => {
    if (v.hidden) unhideElement(panel.id, v.element.gid)
    else {
      setOverrides(
        panel.id,
        msg('history.hideLegendEntry', { label: v.text }, 'workspace'),
        [{ gid: v.element.gid, prop: 'visible', value: false }],
      )
    }
  }

  if (!views.length) return null

  return (
    <div className="flex flex-col gap-2">
      {hasTypography && (
        <div>
          <p className="mb-1 text-xs uppercase tracking-[.06em] text-ink-3">{lg('typography')}</p>
          <TypographyControls adapter={typography} labelWidth={labelWidth} />
        </div>
      )}
      <div>
        <p className="mb-1 text-xs uppercase tracking-[.06em] text-ink-3">
          {lg('entries', { count: views.length })}
        </p>
        <ul className="rounded-sm border border-border" aria-label={lg('entriesAria')}>
          {views.map((v, i) => (
            <li
              key={v.element.gid}
              className={cn(
                'flex h-7 items-center gap-1 px-1.5',
                i > 0 && 'border-t border-border',
                v.hidden && 'text-ink-3',
              )}
            >
              <HandleSwatch panel={panel} entry={v} />
              <button
                type="button"
                className="flex h-6 min-w-0 flex-1 items-center gap-1 rounded-sm text-left text-xs outline-none hover:text-ink focus-visible:focus-ring"
                onClick={() => useUiStore.getState().setSelectedGid(v.element.gid)}
                aria-label={lg('selectEntry', { label: v.text })}
              >
                <span className={cn('min-w-0 truncate', v.hidden ? 'line-through' : 'text-ink')}>
                  {v.text}
                </span>
                <BindingBadge binding={v.binding} />
              </button>
              <Tip label={v.hidden ? lg('show') : lg('hide')}>
                <Button
                  size="icon-sm"
                  className="h-5 w-5"
                  aria-label={v.hidden ? lg('showEntry', { label: v.text }) : lg('hideEntry', { label: v.text })}
                  aria-pressed={v.hidden}
                  onClick={() => toggleHidden(v)}
                >
                  {v.hidden ? <EyeOff size={11} /> : <Eye size={11} />}
                </Button>
              </Tip>
              <Button
                size="icon-sm"
                className="h-5 w-5"
                disabled={i === 0}
                onClick={() => move(i, -1)}
                aria-label={lg('moveUp', { label: v.text })}
              >
                <MoveUp size={11} />
              </Button>
              <Button
                size="icon-sm"
                className="h-5 w-5"
                disabled={i === views.length - 1}
                onClick={() => move(i, 1)}
                aria-label={lg('moveDown', { label: v.text })}
              >
                <MoveDown size={11} />
              </Button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/** 「跟随图中对象 / 自定义 / 未关联」——只说状态，不露 gid。 */
export function BindingBadge({ binding }: { binding: LegendEntryView['binding'] }) {
  const key = binding === 'follow_source' ? 'follow' : binding === 'custom' ? 'custom' : 'unbound'
  return (
    <span
      className={cn(
        'shrink-0 rounded-[3px] border px-1 text-[10px] leading-4',
        binding === 'follow_source'
          ? 'border-border text-ink-3'
          : binding === 'custom'
            ? 'border-accent/40 text-accent'
            : 'border-dashed border-border text-ink-3',
      )}
    >
      {lg(`badge.${key}`)}
    </span>
  )
}

const DASH: Record<string, string | undefined> = {
  '-': undefined,
  '--': '6 3',
  ':': '1.5 2.5',
  '-.': '6 2.5 1.5 2.5',
}

/**
 * 示意线的小预览：读 manifest 的 `handle_*` 字段（override 优先），画一条
 * 24×12 的线 + 一个标记。它是**读 manifest 的投影**，不是第二份样式判断——
 * 引擎给什么就画什么，没有 handle_linestyle 的项（柱 / 散点）只画一个色块。
 */
function HandleSwatch({ panel, entry }: { panel: PanelObject; entry: LegendEntryView }) {
  const el = entry.element
  const read = (prop: string): unknown => {
    const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === prop)
    if (ov) return ov.value
    return el.editable.find((f) => f.prop === prop)?.value
  }
  const color = String(read('handle_color') ?? '#000000')
  const ls = read('handle_linestyle')
  const lw = Number(read('handle_linewidth') ?? 1.5)
  const marker = String(read('handle_marker') ?? 'None')
  const hasLine = el.editable.some((f) => f.prop === 'handle_linestyle')
  return (
    <svg
      width={24}
      height={12}
      viewBox="0 0 24 12"
      aria-hidden
      className={cn('shrink-0', entry.hidden && 'opacity-40')}
    >
      {hasLine ? (
        <>
          <line
            x1={1}
            y1={6}
            x2={23}
            y2={6}
            stroke={color}
            strokeWidth={Math.max(0.75, Math.min(4, lw))}
            strokeDasharray={DASH[String(ls)]}
          />
          {marker !== 'None' && marker !== '' && (
            <circle cx={12} cy={6} r={2.4} fill={color} stroke="none" />
          )}
        </>
      ) : (
        <rect x={2} y={2} width={20} height={8} fill={color} stroke="none" />
      )}
    </svg>
  )
}
