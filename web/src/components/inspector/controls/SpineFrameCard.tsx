import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import { msg, t as translate } from '@/i18n'
import type { EditableField, ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { clearOverride, clearOverrides } from '@/store/actions'
import type { PanelObject } from '@/types/document'
import { ColorField, NumberField } from '../../ui/Input'
import { useElementWriter } from '../elementWrite'
import { propLabel } from '../roles/registry'
import { ResetChip, labeledWithState } from './textRows'

/**
 * 子图边框：**默认四边联动，需要差异时再展开逐边**（审计 T12）。
 *
 * 引擎本来就有两档（`overrides.apply_spine_model`）：`spine_color` /
 * `spine_linewidth` 是「全部」，`spine_<side>_color` / `_linewidth` 是逐边覆盖，
 * 优先级 逐边 > 全部 > 脚本原样。修改前界面把十条字段平铺在「更多」里，
 * 用户要改一下边框粗细得先弄懂这十条的关系。
 *
 * 这里的两条纪律：
 *   * 联动那一行写「全部」；**若某边此刻有逐边覆盖，先把它清掉**——否则
 *     引擎按优先级仍画逐边那条，用户改了颜色那一边纹丝不动，而界面又什么都
 *     不说。清掉是一次独立的历史（「统一各边」），随后的取色是另一次；
 *   * 四边现值不一致（脚本本来就画得不一样、或用户逐边改过）时联动行显示
 *     「多个值」，逐边区自动展开且不许收起——一个宣称「作用于四边」的控件
 *     不能在四边并不相同时装作它们相同。
 *
 * 能力仍由 manifest 说了算：没有 `spine_<side>_color` 的边整行不画。
 */

const SIDES = ['top', 'right', 'bottom', 'left'] as const
type Side = (typeof SIDES)[number]
type Kind = 'color' | 'linewidth'

/** 卡承接的字段——子图页的通用列表要把它们让出来 */
export const SPINE_FRAME_PROPS = [
  'spine_color',
  'spine_linewidth',
  ...SIDES.flatMap((s) => [`spine_${s}_color`, `spine_${s}_linewidth`]),
] as const

const linked = (kind: Kind) => (kind === 'color' ? 'spine_color' : 'spine_linewidth')
const perSide = (side: Side, kind: Kind) => `spine_${side}_${kind}`

const ctl = (key: string, values?: Record<string, unknown>) =>
  translate(`control.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function SpineFrameCard({
  panel,
  element,
  labelWidth = 72,
}: {
  panel: PanelObject
  /** 宿主子图（字段都在它身上） */
  element: ManifestElement
  labelWidth?: number
}) {
  const w = useElementWriter(panel, element)
  const sides = SIDES.filter((s) => w.has(perSide(s, 'color')) || w.has(perSide(s, 'linewidth')))
  const hasLinked = w.has('spine_color') || w.has('spine_linewidth')
  const overridden = (prop: string) =>
    panel.overrides.some((o) => o.gid === element.gid && o.prop === prop)

  /** 一种属性在四边上的现值；逐边覆盖优先、其次 manifest 的逐边值 */
  const sideValues = (kind: Kind) => sides.map((s) => w.read(perSide(s, kind)))
  const sidesDiffer = (kind: Kind) => {
    const vs = sideValues(kind).map((v) => String(v))
    return vs.some((v) => v !== vs[0])
  }
  const anySideOverridden = (kind: Kind) => sides.some((s) => overridden(perSide(s, kind)))

  /**
   * 联动行显示什么：用户改过「全部」就是那个值（逐边覆盖会在写入时被清掉，
   * 渲染回来四边必然相同）；否则看四边是否一致——不一致就是「多个值」。
   */
  const linkedState = (kind: Kind): { value: unknown; mixed: boolean } => {
    if (overridden(linked(kind))) return { value: w.read(linked(kind)), mixed: false }
    const vs = sideValues(kind)
    if (vs.length && !sidesDiffer(kind)) return { value: vs[0], mixed: false }
    return { value: vs[0] ?? w.read(linked(kind)), mixed: vs.length > 0 }
  }

  const differ = (['color', 'linewidth'] as Kind[]).some(
    (k) => sidesDiffer(k) || anySideOverridden(k),
  )
  const [openPref, setOpenPref] = useState(false)
  const open = openPref || differ

  /** 联动写入前先把逐边覆盖清掉（一次历史）；没有就什么都不做 */
  const unifySides = (kind: Kind) => {
    const targets = sides
      .filter((s) => overridden(perSide(s, kind)))
      .map((s) => ({ gid: element.gid, prop: perSide(s, kind) }))
    if (!targets.length) return
    clearOverrides(
      panel.id,
      msg('element.editProp', { label: propLabel(linked(kind), element.role) }, 'inspector'),
      targets,
    )
  }

  if (!hasLinked && !sides.length) return null

  const colorField = w.fieldOf('spine_color')
  const widthField = w.fieldOf('spine_linewidth')
  const color = linkedState('color')
  const width = linkedState('linewidth')
  const mixedText = translate('element.mixedValues', { ns: 'inspector' })
  const head = (key: 'color' | 'linewidth') => translate(`prop.${key}`, { ns: 'inspector' })
  const frameLabel = translate('element.groupFrame', { ns: 'inspector' })
  const linkedModified = overridden('spine_color') || overridden('spine_linewidth')

  return (
    <div
      className="grid items-center gap-x-1.5 gap-y-1.5"
      // 边 / 颜色 / 线宽 / 还原：联动行与四边共用同一套列宽。
      // 颜色列封顶 88px（取色块 + 6 位色号刚好够，不让十六进制输入框的固有
      // 宽度撑开这一列）；剩余宽度全给线宽列——下限 120px，扣掉单位后缀与
      // 内边距后数字输入区稳定 ≥ 40px。
      style={{
        gridTemplateColumns: `${labelWidth}px minmax(0, 88px) minmax(120px, 1fr) auto`,
      }}
      data-spine-frame
    >
      <span aria-hidden />
      {/* 列头相对下方字段居中：列头与字段共用同一套网格列，text-center 即以该列为基准 */}
      <span aria-hidden className="text-center text-xs text-ink-3">
        {colorField ? head('color') : null}
      </span>
      <span aria-hidden className="text-center text-xs text-ink-3">
        {widthField ? head('linewidth') : null}
      </span>
      <span aria-hidden />

      {(colorField || widthField) && (
        <FrameRow
          side="all"
          primary
          label={labeledWithState(frameLabel, linkedModified)}
          color={
            colorField && (
              <div
                data-prop="spine_color"
                data-gid={element.gid}
                className="flex min-w-0 items-center gap-1.5"
              >
                <ColorField
                  ariaLabel={propLabel('spine_color', element.role)}
                  value={String(color.value ?? '#000000')}
                  onChange={(v) => {
                    unifySides('color')
                    w.write('spine_color', v, true)
                  }}
                  onGestureEnd={w.endGesture}
                />
                {color.mixed && (
                  <span className="min-w-0 truncate text-xs text-ink-3" data-spine-mixed="color">
                    {mixedText}
                  </span>
                )}
              </div>
            )
          }
          width={
            widthField && (
              <div
                data-prop="spine_linewidth"
                data-gid={element.gid}
                data-spine-mixed={width.mixed ? 'linewidth' : undefined}
                className="flex min-w-0 items-center"
              >
                <NumberField
                  className="w-full min-w-0"
                  dataProp="spine_linewidth"
                  ariaLabel={propLabel('spine_linewidth', element.role)}
                  value={Number(width.value ?? 0)}
                  mixed={width.mixed}
                  min={widthField.min}
                  max={widthField.max}
                  step={widthField.step ?? 0.1}
                  precision={2}
                  unit={widthField.unit}
                  onChange={(v) => {
                    unifySides('linewidth')
                    w.write('spine_linewidth', v)
                  }}
                  onScrubStart={() => w.beginGesture()}
                  onScrubEnd={w.endGesture}
                />
              </div>
            )
          }
          reset={
            linkedModified && (
              <>
                {overridden('spine_color') && (
                  <ResetChip
                    label={propLabel('spine_color', element.role)}
                    onReset={() => clearOverride(panel.id, element.gid, 'spine_color')}
                  />
                )}
                {overridden('spine_linewidth') && (
                  <ResetChip
                    label={propLabel('spine_linewidth', element.role)}
                    onReset={() => clearOverride(panel.id, element.gid, 'spine_linewidth')}
                  />
                )}
              </>
            )
          }
        />
      )}

      {sides.length > 0 && (
        <button
          type="button"
          onClick={() => setOpenPref(!open)}
          aria-expanded={open}
          // 四边不一致时不许收起：收起就是把「各边不同」藏起来
          disabled={differ}
          data-spine-per-side
          className="col-span-full mt-1 flex h-7 w-full items-center gap-1.5 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring disabled:hover:text-ink-2"
        >
          <ChevronRight
            size={ICON_SIZE.xs}
            aria-hidden
            className={cn('shrink-0 transition-transform', open && 'rotate-90')}
          />
          <span>{ctl('spinePerSide')}</span>
          {differ && (
            <span className="ml-auto min-w-0 shrink-0 truncate pl-2 text-xs text-ink-3">
              {ctl('spineSidesDiffer')}
            </span>
          )}
        </button>
      )}
      {sides.length > 0 &&
        open &&
        sides.map((side) => (
          <SideRow
            key={side}
            side={side}
            colorField={w.fieldOf(perSide(side, 'color'))}
            widthField={w.fieldOf(perSide(side, 'linewidth'))}
            read={w.read}
            write={w.write}
            beginGesture={() => w.beginGesture()}
            endGesture={w.endGesture}
            overridden={overridden}
            reset={(p) => clearOverride(panel.id, element.gid, p)}
            gid={element.gid}
            role={element.role}
          />
        ))}
    </div>
  )
}

/** 边位示意：四边联动画整个框，逐边只点亮那一条边 */
function SideGlyph({ side }: { side: Side | 'all' }) {
  const edge: Record<Side, string> = {
    top: 'M3 3h12',
    right: 'M15 3v12',
    bottom: 'M3 15h12',
    left: 'M3 3v12',
  }
  const all = side === 'all'
  return (
    <svg
      viewBox="0 0 18 18"
      width={ICON_SIZE.xs}
      height={ICON_SIZE.xs}
      fill="none"
      aria-hidden
      className="shrink-0"
    >
      <rect
        x="3"
        y="3"
        width="12"
        height="12"
        rx="0.6"
        stroke={all ? 'currentColor' : 'var(--color-border-strong)'}
        strokeWidth={all ? 1.2 : 1}
      />
      {!all && <path d={edge[side]} stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
    </svg>
  )
}

/** 网格里的一行：四个格子（边名 / 颜色 / 线宽 / 还原），空格子也占位以保持列对齐 */
function FrameRow({
  side,
  label,
  primary,
  color,
  width,
  reset,
}: {
  side: Side | 'all'
  label: ReactNode
  primary?: boolean
  color?: ReactNode
  width?: ReactNode
  reset?: ReactNode
}) {
  return (
    <>
      <div
        className={cn(
          'flex min-w-0 items-center gap-1.5 text-xs',
          primary ? 'font-medium text-ink' : 'text-ink-2',
        )}
      >
        <SideGlyph side={side} />
        <span className="min-w-0 truncate">{label}</span>
      </div>
      {/* 颜色格子：取色块 + 色号输入撑满这一列，列头才正对「色块 + 输入框」整体的中点 */}
      <div className="min-w-0 [&>*]:w-full [&_input]:min-w-0">{color}</div>
      {/* 线宽格子：让 NumberField 及其 input 真正撑满这一列，不受其内部默认宽度限制 */}
      <div className="min-w-0 [&>*]:w-full [&_input]:min-w-0 [&_input]:w-full">{width}</div>
      <div className="flex items-center gap-1">{reset}</div>
    </>
  )
}

function SideRow({
  side,
  colorField,
  widthField,
  read,
  write,
  beginGesture,
  endGesture,
  overridden,
  reset,
  gid,
  role,
}: {
  side: Side
  colorField?: EditableField
  widthField?: EditableField
  read: (prop: string) => unknown
  write: (prop: string, value: unknown, immediate?: boolean) => void
  beginGesture: () => void
  endGesture: () => void
  overridden: (prop: string) => boolean
  reset: (prop: string) => void
  gid: string
  role: string
}) {
  const colorProp = perSide(side, 'color')
  const widthProp = perSide(side, 'linewidth')
  const modified = overridden(colorProp) || overridden(widthProp)
  const sideName = translate(`tick.side.${side}`, { ns: 'inspector' })
  return (
    <FrameRow
      side={side}
      label={labeledWithState(sideName, modified)}
      color={
        colorField && (
          <div data-prop={colorProp} data-gid={gid} className="flex min-w-0 items-center">
            <ColorField
              ariaLabel={`${sideName} ${propLabel(colorProp, role)}`}
              value={String(read(colorProp) ?? '#000000')}
              onChange={(v) => write(colorProp, v, true)}
              onGestureEnd={endGesture}
            />
          </div>
        )
      }
      width={
        widthField && (
          <NumberField
            // 逐边线宽的定位落点与颜色一样是 data-prop（issueFocus 认它）
            fill
            dataProp={widthProp}
            ariaLabel={propLabel(widthProp, role)}
            value={Number(read(widthProp) ?? 0)}
            min={widthField.min}
            max={widthField.max}
            step={widthField.step ?? 0.1}
            precision={2}
            unit={widthField.unit}
            onChange={(v) => write(widthProp, v)}
            onScrubStart={beginGesture}
            onScrubEnd={endGesture}
          />
        )
      }
      reset={
        modified && (
          <ResetChip
            label={sideName}
            onReset={() => {
              if (overridden(colorProp)) reset(colorProp)
              if (overridden(widthProp)) reset(widthProp)
            }}
          />
        )
      }
    />
  )
}
