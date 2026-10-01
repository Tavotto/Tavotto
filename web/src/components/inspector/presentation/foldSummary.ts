import { t as translate } from '@/i18n'
import type { EditableField } from '@/lib/api'
import { optionLabel } from '../roles/registry'
import type { PresentedFold } from './types'

/**
 * 摘要行右边那一小段：**只写当前值或项数**，不写里面有什么（设计稿 A 章规则）。
 *
 * 值怎么写按折叠行 id 点名——这里是「当前值怎么说」的唯一出处，行本身（`SummaryRow`）
 * 不认识任何属性。读值走调用方给的 `read`（override 优先），与条件显示、控件读的是
 * 同一份，不另取一份。没有可说的当前值就返回 undefined（行右边留空）。
 */

const el = (key: string, values?: Record<string, unknown>) =>
  translate(`element.${key}`, { ns: 'inspector', ...(values ?? {}) })

export interface FoldReader {
  read: (prop: string) => unknown
  fieldOf: (prop: string) => EditableField | undefined
}

/** 一个字段此刻的值说成人话：枚举取显示名、布尔说显示 / 不显示、颜色大写十六进制 */
export function valueText(field: EditableField | undefined, value: unknown): string | undefined {
  if (!field || value === undefined || value === null) return undefined
  if (typeof value === 'boolean') return el(value ? 'foldOn' : 'foldOff')
  if (field.type === 'enum') return field.option_labels?.[String(value)] ?? optionLabel(field.prop, String(value))
  if (field.type === 'color') return String(value).toUpperCase()
  if (typeof value === 'number') return field.unit ? `${value} ${field.unit}` : String(value)
  return String(value)
}

const lead = (r: FoldReader, prop: string) => valueText(r.fieldOf(prop), r.read(prop))

/** 子图的网格：两个方向都没开 = 不显示，否则写开着的方向 */
function gridValue(r: FoldReader): string {
  const on = (['grid_x', 'grid_y'] as const).filter((p) => r.read(p) === true)
  if (!on.length) return el('foldOff')
  return on.map((p) => el(p === 'grid_x' ? 'axis.x' : 'axis.y')).join(' · ')
}

/**
 * 通用「更多」与角色模板里点名的摘要行的右值。
 * `modified` 是这一行里被用户改过的字段数——收起时 override 不能看不见，
 * 「更多」这种没有单一当前值的行就用它（与旧版「n 项已修改」同一句话）。
 */
export function foldValue(fold: PresentedFold, r: FoldReader): string | undefined {
  switch (fold.spec.id) {
    case 'grid':
      return gridValue(r)
    case 'background':
      return lead(r, 'facecolor')
    case 'placement':
      return lead(r, 'major_mode')
    case 'minor':
      return lead(r, 'minor_visible')
    case 'numformat':
      return lead(r, 'format')
    case 'fontcolor': {
      const bits = [lead(r, 'fontfamily'), lead(r, 'color')].filter(Boolean)
      return bits.length ? bits.join(' · ') : undefined
    }
    default:
      return undefined
  }
}

/** 通用「更多」：改过的优先报（override 不因折叠而不可发现），否则报项数 */
export function moreValue(count: number, modified: number): string {
  return modified > 0
    ? translate('element.modifiedCount', { ns: 'inspector', count: modified })
    : el('foldCount', { count })
}
