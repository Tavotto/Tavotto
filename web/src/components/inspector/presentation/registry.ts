import type { EditableField } from '@/lib/api'
import { isTextEffectSwitch } from '@/lib/textEffects'
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

/**
 * 0–1 的透明度类字段：界面按百分比显示与输入，写回仍是 0–1（审计 T16 / T20）。
 * **按 prop 名点名**，不按「min 0 max 1 就是百分比」猜——`framealpha` 与 `alpha`
 * 是透明度，而一个恰好落在 0–1 的比例（如 `handlelength` 的某些取值）不是。
 * 换算只在 `controls/PercentField` 一处。
 */
const PERCENT_PROPS = new Set(['alpha', 'grid_alpha', 'framealpha', 'bbox_alpha'])

/** 这个数值字段是不是按百分比显示的透明度（批量行与单元素行共用一条判据） */
export const isPercentField = (field: EditableField): boolean =>
  field.type === 'number' && PERCENT_PROPS.has(field.prop)

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
  // 色条的方向 / 两端延伸：用当前色图画的小色条预览，不是两个文字下拉（审计 T23）
  if (role === 'colorbar' && field.type === 'enum') {
    if (field.prop === 'orientation') return 'colorbar-orientation'
    if (field.prop === 'extend') return 'colorbar-extend'
  }
  // 三维子图的投影方式：透视 / 正交各一个小立方体（审计 T24）
  if (field.prop === 'proj_type' && role === 'axes3d' && field.type === 'enum') {
    return 'projection'
  }
  // 图例项的绑定：一行状态 + 动作（跟随 / 自定义），不是一个下拉
  if (field.prop === 'binding' && role === 'legend_text' && field.type === 'enum') {
    return 'legend-binding'
  }
  // 子图纵横比：引擎按 text 发（'auto' | 'equal' | 数字串），但它不是一段文字
  // ——落进带上下标 / 换行 / 大小写转换的富文本编辑器是审计 T12 点名的错配。
  // 按 prop + 角色认，不按「值长得像什么」猜：给它一个明确的控件形态
  if (field.prop === 'aspect' && role === 'axes' && field.type === 'text') {
    return 'aspect'
  }
  const byProp = CONTROL_BY_PROP[field.prop]
  if (byProp && field.type === 'enum') return byProp
  if (isPercentField(field)) return 'percent'
  // 背景 / 描边的开关：关着的时候不是一个开关，是一条「＋添加背景」入口
  // （与画布文字 `TextSection` 同一种操作模式；表在 `lib/textEffects`）
  if (field.type === 'bool' && isTextEffectSwitch(field.prop)) return 'effect'
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

/**
 * 单个字段此刻该不该显示：模板的 `visibleWhen`（「开关 → 从属字段」那张表）
 * + 「用户改过的必须能看到」。**只有这一条判据**：`presentFields` 分桶用它，
 * 不走桶的复合控件（刻度卡的次刻度长宽）也用它——次刻度关着时该收起哪些行，
 * 通用列表与刻度卡说的必须是同一句话。
 */
export function fieldVisible(role: string, prop: string, opts: PresentOptions): boolean {
  const cond = ROLE_PROFILES[role]?.visibleWhen?.[prop]
  return !cond || cond(opts.read) || opts.isOverridden(prop)
}

/**
 * 这个角色里与 `prop` 并排成一行的另一条字段（模板 `pairRows`）；没有就 null。
 * 只回答「谁和谁一对」，画不画在一行由列表按「两条都在同一桶里」决定。
 */
export function pairedProp(role: string, prop: string): string | null {
  for (const [a, b] of ROLE_PROFILES[role]?.pairRows ?? []) {
    if (a === prop) return b
    if (b === prop) return a
  }
  return null
}

export function presentFields(
  role: string,
  fields: EditableField[],
  opts: PresentOptions,
): PresentedBuckets {
  const profile = ROLE_PROFILES[role]
  const out: PresentedBuckets = { primary: [], more: [], advanced: [] }

  fields.forEach((field, engineIndex) => {
    // 条件显示：模式从属字段只在对应模式下渲染；用户改过的必须能看到
    if (!fieldVisible(role, field.prop, opts)) return

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
