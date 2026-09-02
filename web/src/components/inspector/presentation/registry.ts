import type { EditableField } from '@/lib/api'
import { groupRank } from '../roles/registry'
import { ROLE_PROFILES } from './roleProfiles'
import type {
  ControlKind,
  InspectorPriority,
  PresentedBuckets,
} from './types'

/**
 * 展示注册表：把 manifest 字段分桶（primary / more / advanced）并决定控件形态。
 *
 * 三条纪律：
 *   1. manifest 是能力权威——这里**只排版不裁能力**，字段进来多少出去多少；
 *   2. 未知角色 / 未知字段有兜底：无 group 的进 primary、有 group 的进 more、
 *      「高级」「排列」组进 advanced，原文显示也比隐藏好；
 *   3. 已被用户改过的字段永远显示（条件显示与折叠都要给它让路）。
 */

/** 引擎的这两个 group 天然是低频层（层级 zorder、诊断类） */
const ADVANCED_GROUPS = new Set(['高级', '排列'])
/** 与角色无关的低频属性：层级、figure 分数坐标的裸 rect */
const ADVANCED_PROPS = new Set(['zorder', 'position'])

/**
 * enum 字段的视觉控件按 prop 名认，不按选项内容猜。
 * 未列出的 enum 落回文字 Select（带明确标签的 fallback）。
 */
const CONTROL_BY_PROP: Record<string, ControlKind> = {
  linestyle: 'line-style',
  grid_linestyle: 'line-style',
  handle_linestyle: 'line-style',
  marker: 'marker',
  handle_marker: 'marker',
  hatch: 'hatch',
  cmap: 'colormap',
  fontfamily: 'font',
  arrowstyle: 'arrow-style',
}

const CONTROL_BY_TYPE: Record<EditableField['type'], ControlKind> = {
  text: 'text',
  number: 'number',
  color: 'color',
  bool: 'toggle',
  enum: 'select',
  pair: 'pair',
  rect: 'rect',
  order: 'order',
  number_list: 'number-list',
}

export function controlKindOf(role: string, field: EditableField): ControlKind {
  // 图例位置是角色专属语义（3×3 网格）；别的角色如果哪天也发 loc，回落 Select
  if (field.prop === 'loc' && role === 'legend' && field.type === 'enum') {
    return 'legend-position'
  }
  // 图例项的绑定：一行状态 + 动作（跟随 / 自定义），不是一个下拉
  if (field.prop === 'binding' && role === 'legend_text' && field.type === 'enum') {
    return 'legend-binding'
  }
  const byProp = CONTROL_BY_PROP[field.prop]
  if (byProp && field.type === 'enum') return byProp
  return CONTROL_BY_TYPE[field.type] ?? 'text'
}

export interface PresentOptions {
  /** 该属性是否已被用户改过（override 存在） */
  isOverridden: (prop: string) => boolean
  /** 当前值读取（override 优先），供条件显示判断 */
  read: (prop: string) => unknown
}

/** 桶内排序的大偏移：显式点名的排前（0..n），兜底的按引擎组序 + 出现序跟在后面 */
const FALLBACK_BASE = 1000

export function presentFields(
  role: string,
  fields: EditableField[],
  opts: PresentOptions,
): PresentedBuckets {
  const profile = ROLE_PROFILES[role]
  const out: PresentedBuckets = { primary: [], more: [], advanced: [] }

  fields.forEach((field, engineIndex) => {
    const overridden = opts.isOverridden(field.prop)
    // 条件显示：模式从属字段只在对应模式下渲染；用户改过的必须能看到
    const cond = profile?.visibleWhen?.[field.prop]
    if (cond && !cond(opts.read) && !overridden) return

    let priority: InspectorPriority
    let order: number

    const pi = profile?.primary.indexOf(field.prop) ?? -1
    const mi = profile?.more?.indexOf(field.prop) ?? -1
    const ai = profile?.advanced?.indexOf(field.prop) ?? -1
    // 兜底顺序：无 group 的排在有 group 的前面，其余按引擎组序 + 出现序
    const fallbackOrder =
      FALLBACK_BASE + (field.group ? groupRank(field.group) : -1) * 100 + engineIndex

    if (pi >= 0) {
      priority = 'primary'
      order = pi
    } else if (ai >= 0) {
      priority = 'advanced'
      order = ai
    } else if (ADVANCED_PROPS.has(field.prop) || ADVANCED_GROUPS.has(field.group ?? '')) {
      priority = 'advanced'
      order = fallbackOrder
    } else if (mi >= 0) {
      priority = 'more'
      order = mi
    } else if (profile) {
      // 建过档的角色：没点名的字段一律进「更多」，不丢
      priority = 'more'
      order = fallbackOrder
    } else {
      // 未建档角色：沿用「无 group 平铺在前」的老约定
      priority = field.group ? 'more' : 'primary'
      order = fallbackOrder
    }

    out[priority].push({
      field,
      priority,
      control: controlKindOf(role, field),
      order,
    })
  })

  for (const bucket of [out.primary, out.more, out.advanced]) {
    bucket.sort((a, b) => a.order - b.order)
  }
  return out
}
