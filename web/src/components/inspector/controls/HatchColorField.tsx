import { Checkbox } from '@/components/ui/Checkbox'
import { ColorField, NO_COLOR } from '@/components/ui/Input'
import { t as translate } from '@/i18n'
import type { EditableField } from '@/lib/api'

/** 引擎 `hatchcolor` 的「跟随边框」取值（matplotlib 的哨兵；严格同源：`engine/overrides._set_hatchcolor`） */
export const FOLLOW_EDGE = 'edge'

/** 有 `follows_edge` 的颜色字段才带「跟随边框」选项（引擎按 matplotlib 版本发，旧版本整条不发） */
export const hasFollowEdge = (field: EditableField): boolean =>
  field.type === 'color' && typeof field.follows_edge === 'boolean'

/**
 * 这条颜色此刻画出来的色号与是否跟随边框。
 *
 * `raw` 是 `currentValue`（有 override 取 override，否则取 manifest 的 `value`）：override 是
 * `'edge'` 时 manifest 还没回来也算跟随，色号取 manifest 上一版（跟随时它就是边色实况）；
 * 没有 override 时听 manifest 的 `follows_edge`。有 override 且是色号 = 明确不跟随。
 */
export function hatchColorReading(
  raw: unknown,
  field: EditableField,
  overridden: boolean,
): { hex: string; follows: boolean } {
  if (raw === FOLLOW_EDGE) return { hex: String(field.value ?? NO_COLOR), follows: true }
  return { hex: String(raw ?? NO_COLOR), follows: !overridden && field.follows_edge === true }
}

/**
 * 纹理颜色：色块 + 一格「跟随边框」。
 *
 * 勾上 = 花纹随边框变色（写 `'edge'`），色块显示的是此刻边框的颜色；去掉勾 = 固定成当前显示的这个色，
 * 之后再取色。直接取色也会离开「跟随」（写的是色号）。多选且不一致时勾选框不勾，色块说「多个值」。
 */
export function HatchColorField({
  hex,
  follows,
  mixed,
  ariaLabel,
  onColor,
  onFollow,
  onGestureEnd,
}: {
  hex: string
  follows: boolean
  mixed?: boolean
  ariaLabel: string
  onColor: (hex: string) => void
  /** `true` = 改成跟随边框；`false` = 固定成 `hex` */
  onFollow: (follow: boolean) => void
  onGestureEnd?: () => void
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <ColorField
        ariaLabel={ariaLabel}
        mixed={mixed}
        value={hex}
        onChange={onColor}
        onGestureEnd={onGestureEnd}
      />
      <label className="flex shrink-0 items-center gap-1.5 text-ink-2">
        <Checkbox
          checked={follows && !mixed}
          data-hatch-follow-edge
          onChange={(e) => onFollow(e.target.checked)}
        />
        <span className="type-control">{translate('element.hatchFollowEdge', { ns: 'inspector' })}</span>
      </label>
    </div>
  )
}
