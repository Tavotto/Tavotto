import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { GradientLinear, GradientRadial, Plus, Square, X } from '@/components/ui/icons'
import { IconButton, Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Row } from '@/components/ui/Field'
import { Select } from '@/components/ui/Select'
import {
  addStop,
  defaultGradient,
  linearAngle,
  readGradient,
  removeStop,
  switchKind,
  updateStop,
  withLinearAngle,
} from '@/lib/gradient'
import { updateObjects } from '@/store/actions'
import type { Gradient, PathObject, ShapeObject } from '@/types/document'
import { INSPECTOR_LABEL_W } from '../layout'

/**
 * 渐变的属性栏控件（ADR 0186 §四，第一期：只在属性栏调，画布控制柄第二期）。
 *
 * - 「填充」「描边」那一行颜色后面一个紧凑的类型下拉（纯色 / 线性 / 径向）——不新增行；
 * - 色标（增 / 删 / 改颜色与位置）、线性的角度、径向的中心与半径收进「更多」。
 *
 * 多选时以第一个对象的渐变为准、写给选区里这一侧生效的全部对象（与颜色下拉「多个值时取色写给全部」同一条）。
 * 字段读写只经 `lib/gradient`；`fill` / `color` 纯色一直留着（旧版本的降级色，切回纯色时就是它）。
 */

type Mark = ShapeObject | PathObject
export type GradientTarget = 'fill' | 'stroke'
type Kind = 'solid' | 'linear' | 'radial'

const gk = (key: string, values?: Record<string, unknown>) => translate(`gradient.${key}`, { ns: 'inspector', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')
const field = (t: GradientTarget) => (t === 'fill' ? 'fillGradient' : 'strokeGradient')

/** 这一侧的渐变（读得成才算；读不成的字段当作没有） */
export function gradientOf(o: Mark, t: GradientTarget): Gradient | null {
  const g = o[field(t)]
  return readGradient(g) ? (g as Gradient) : null
}

export function kindOf(objs: readonly Mark[], t: GradientTarget): Kind | undefined {
  const kinds = new Set(objs.map((o) => gradientOf(o, t)?.kind ?? 'solid'))
  return kinds.size === 1 ? ([...kinds][0] as Kind) : undefined
}

function write(objs: readonly Mark[], t: GradientTarget, label: UiMessage, next: (o: Mark) => Gradient | null) {
  updateObjects(
    objs.map((o) => o.id),
    label,
    (o) => {
      if (o.type !== 'shape' && o.type !== 'path') return
      const g = next(o)
      if (g) o[field(t)] = g
      else delete o[field(t)]
    },
  )
}

const KIND_ICON = { solid: Square, linear: GradientLinear, radial: GradientRadial } as const
function KindIcon({ kind }: { kind: Kind }) {
  const Icon = KIND_ICON[kind]
  return <Icon size={ICON_SIZE.sm} data-gradient-kind={kind} aria-hidden />
}

/** 类型下拉：放在「填充」/「描边」那一行颜色的后面 */
export function GradientKindSelect({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const kind = kindOf(objs, target)
  return (
    <Select<Kind>
      value={kind ?? ('' as Kind)}
      placeholder={gk('mixed')}
      ariaLabel={gk(target === 'fill' ? 'fillKind' : 'strokeKind')}
      title={gk('kindTip')}
      // 触发器只画一枚图标（纯色 / 线性 / 径向），不挤颜色的十六进制框；下拉里是完整的字
      className="w-auto min-w-0 shrink-0 gap-0.5 px-1"
      triggerLabel={kind ? <KindIcon kind={kind} /> : undefined}
      options={(['solid', 'linear', 'radial'] as const).map((k) => ({ value: k, label: gk(`kind.${k}`) }))}
      onChange={(k) =>
        write(objs, target, hist('setGradientKind'), (o) => {
          if (k === 'solid') return null
          const cur = gradientOf(o, target)
          return cur ? switchKind(cur, k) : defaultGradient(k, (target === 'fill' ? o.fill : null) ?? o.color)
        })
      }
    />
  )
}

/** 「更多」里的渐变细节：色标、角度（线性）/ 中心与半径（径向）。这一侧没有渐变就什么都不画 */
export function GradientRows({ objs, target }: { objs: readonly Mark[]; target: GradientTarget }) {
  useTranslation('inspector')
  const live = objs.filter((o) => gradientOf(o, target))
  const first = live[0]
  const g = first ? gradientOf(first, target) : null
  if (!first || !g) return null
  const set = (label: UiMessage, next: Gradient) => write(live, target, label, () => next)
  const pct = (v: number) => Math.round(v * 1000) / 10
  return (
    <div data-gradient-rows={target} className="flex flex-col gap-1.5">
      <Row label={gk(target === 'fill' ? 'fillStops' : 'strokeStops')} labelWidth={INSPECTOR_LABEL_W}>
        <div className="flex min-w-0 flex-col gap-1">
          {g.stops.map((st, i) => (
            <div key={i} data-gradient-stop={i} className="flex min-w-0 items-center gap-1">
              <ColorField
                swatchOnly
                ariaLabel={gk('stopColor', { n: i + 1 })}
                value={st.color}
                onChange={(v) => set(hist('setGradient'), updateStop(g, i, { color: v }))}
              />
              <NumberField
                fill
                ariaLabel={gk('stopOffset', { n: i + 1 })}
                value={pct(st.offset)}
                step={5}
                min={0}
                max={100}
                precision={0}
                unit="%"
                onChange={(v) => set(hist('setGradient'), updateStop(g, i, { offset: v / 100 }))}
              />
              <NumberField
                fill
                ariaLabel={gk('stopOpacity', { n: i + 1 })}
                title={gk('stopOpacityTip')}
                value={pct(st.opacity ?? 1)}
                step={5}
                min={0}
                max={100}
                precision={0}
                unit="%"
                onChange={(v) => set(hist('setGradient'), updateStop(g, i, { opacity: v / 100 }))}
              />
              <IconButton
                label={gk('removeStop', { n: i + 1 })}
                side="left"
                disabled={g.stops.length <= 2}
                onClick={() => set(hist('setGradient'), removeStop(g, i))}
              >
                <X size={ICON_SIZE.sm} />
              </IconButton>
            </div>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="self-start text-ink-2 hover:text-ink"
            data-gradient-add-stop={target}
            onClick={() => set(hist('setGradient'), addStop(g))}
          >
            <Plus size={ICON_SIZE.xs} aria-hidden />
            {gk('addStop')}
          </Button>
        </div>
      </Row>
      {g.kind === 'linear' ? (
        <Row label={gk('angle')} labelWidth={INSPECTOR_LABEL_W}>
          <NumberField
            half
            ariaLabel={gk('angle')}
            title={gk('angleTip')}
            value={linearAngle(g, first.w, first.h)}
            step={15}
            min={-360}
            max={360}
            precision={1}
            unit="°"
            onChange={(v) => set(hist('setGradient'), withLinearAngle(g, v, first.w, first.h))}
          />
        </Row>
      ) : (
        <>
          <Row label={gk('center')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              prefix="X"
              ariaLabel={gk('centerX')}
              value={pct(g.center[0])}
              step={5}
              min={-100}
              max={200}
              precision={0}
              unit="%"
              onChange={(v) => set(hist('setGradient'), { ...g, center: [v / 100, g.center[1]] })}
            />
            <NumberField
              half
              prefix="Y"
              ariaLabel={gk('centerY')}
              value={pct(g.center[1])}
              step={5}
              min={-100}
              max={200}
              precision={0}
              unit="%"
              onChange={(v) => set(hist('setGradient'), { ...g, center: [g.center[0], v / 100] })}
            />
          </Row>
          <Row label={gk('radius')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={gk('radius')}
              title={gk('radiusTip')}
              value={pct(g.radius)}
              step={5}
              min={1}
              max={300}
              precision={0}
              unit="%"
              onChange={(v) => set(hist('setGradient'), { ...g, radius: Math.max(v, 1) / 100 })}
            />
          </Row>
        </>
      )}
    </div>
  )
}
