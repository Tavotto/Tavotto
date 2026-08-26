import type { EditableField, ManifestElement } from '@/lib/api'
import type { PanelObject } from '@/types/document'

/**
 * 一个控件当前该显示什么。**三态必须分开**：
 *
 *   uniform     —— 所有目标的值一致，控件显示真实值；
 *   mixed       —— 目标之间值不同，控件显示「多个值」占位；
 *   unavailable —— 至少一个目标不支持这条能力，整个控件不渲染。
 *
 * 用空串 / 0 / 默认值冒充 mixed 是本轮要修掉的一类缺陷：字号 mixed 被画成
 * 9 pt，用户看一眼以为「它们本来就一样」，一敲回车就把两个不同的字号
 * 抹平成同一个——数据损坏级的误导。
 */
export type ControlValue<T = unknown> =
  | { kind: 'uniform'; value: T }
  | { kind: 'mixed' }
  | { kind: 'unavailable' }

/**
 * 「文字语义家族」：这些图内角色在 matplotlib 里都是 `Text`，字体 / 字号 /
 * 字重 / 字形 / 颜色是同一套语义，因此可以放在一起批量改。
 *
 * **这张表只用来判断「值不值得尝试」，不用来判断「能改什么」**——真正
 * 可编辑的字段仍由 manifest 的交集说了算（`commonTextFields`）。角色名
 * 在表里但 manifest 没发 `fontsize`，控件照样不出现。
 */
export const TEXT_LIKE_ROLES: ReadonlySet<string> = new Set([
  'title',
  'axis_label',
  'legend_text',
  'text',
])

/**
 * 跨元素批量样式认的属性，按渲染顺序。
 *
 * 刻意**不含**：`text`（各自的内容，批量改等于把三个标题写成同一句话）、
 * `labelpad` / `ha` / `va`（与角色强耦合，同一个值在标题与轴标题上语义不同，
 * 批量写会让元素意外移动）、裸坐标。
 */
export const TEXT_STYLE_PROPS = [
  'fontfamily',
  'fontsize',
  'weight',
  'style',
  'color',
  'alpha',
  'rotation',
] as const

export type TextStyleProp = (typeof TEXT_STYLE_PROPS)[number]

/**
 * 单选文字额外认的属性。`ha`（水平对齐）**只在单选时给**：同一个 `left`
 * 在图标题与 Y 轴标题上语义不同（一个沿图宽、一个沿轴），批量写会让元素
 * 意外移动。它没进 `TEXT_STYLE_PROPS`，所以批量适配器**结构上**拿不到它。
 */
export const TEXT_SINGLE_PROPS = [...TEXT_STYLE_PROPS, 'ha'] as const

/** 这一组选择能不能走文字样式批量：至少两个、全是文字家族角色 */
export function isTextLikeSelection(elements: ManifestElement[]): boolean {
  return elements.length > 1 && elements.every((e) => TEXT_LIKE_ROLES.has(e.role))
}

const num = (v: number | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * 字段交集：只有**全部**目标都声明了这条属性、且类型一致时才留下。
 *
 * enum 的选项取**交集**而不是要求逐字相等——引擎给文字的 `fontfamily`
 * 选项表里会插进该元素当前用的那个字体（`fam_opts` 的第一项），两个用了
 * 不同字体的标题选项表就不相等。要求相等的话「字体」会整条消失，而它恰恰
 * 是用户最想统一的那个属性。交集为空才真的放弃。
 *
 * number 的范围取**最紧的那个**（min 取最大、max 取最小）：写出去的值必须
 * 在每个目标各自的合法区间内，宽的那个会让某个目标收到越界值。
 */
export function commonTextFields(
  elements: ManifestElement[],
  props: readonly string[] = TEXT_STYLE_PROPS,
): Map<string, EditableField> {
  const out = new Map<string, EditableField>()
  if (!elements.length) return out

  for (const prop of props) {
    const own = elements.map((e) => e.editable.find((f) => f.prop === prop))
    if (own.some((f) => !f)) continue
    const fields = own as EditableField[]
    const base = fields[0]
    if (fields.some((f) => f.type !== base.type)) continue

    let merged: EditableField = { ...base }

    if (base.type === 'enum') {
      const sets = fields.map((f) => new Set(f.options ?? []))
      const options = (base.options ?? []).filter((o) => sets.every((s) => s.has(o)))
      if (!options.length) continue
      merged = { ...merged, options }
    }

    if (base.type === 'number') {
      const mins = fields.map((f) => f.min).filter(num)
      const maxes = fields.map((f) => f.max).filter(num)
      merged = {
        ...merged,
        min: mins.length === fields.length ? Math.max(...mins) : undefined,
        max: maxes.length === fields.length ? Math.min(...maxes) : undefined,
      }
    }

    out.set(prop, merged)
  }
  return out
}

/** 当前值：用户改过的 override 优先于渲染时的初值（与 ElementWriter 同一口径） */
export function currentOf(panel: PanelObject, el: ManifestElement, prop: string): unknown {
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === prop)
  if (ov) return ov.value
  return el.editable.find((f) => f.prop === prop)?.value
}

/**
 * 跨目标读一条属性。字段交集里没有 = `unavailable`（控件整个不画），
 * 值不全相同 = `mixed`。比较用 JSON 序列化，与既有批量行的口径一致。
 */
export function readAcross(
  panel: PanelObject,
  elements: ManifestElement[],
  prop: string,
  fields: Map<string, EditableField>,
): ControlValue {
  if (!fields.has(prop) || !elements.length) return { kind: 'unavailable' }
  const values = elements.map((e) => currentOf(panel, e, prop))
  const first = JSON.stringify(values[0] ?? null)
  if (values.some((v) => JSON.stringify(v ?? null) !== first)) return { kind: 'mixed' }
  return { kind: 'uniform', value: values[0] }
}

/** 已修改状态：没有 / 部分 / 全部——恢复按钮要按它决定文案与作用范围 */
export type OverrideState = 'none' | 'some' | 'all'

export function overrideStateOf(
  panel: PanelObject,
  elements: ManifestElement[],
  prop: string,
): OverrideState {
  const hit = elements.filter((e) =>
    panel.overrides.some((o) => o.gid === e.gid && o.prop === prop),
  )
  if (!hit.length) return 'none'
  return hit.length === elements.length ? 'all' : 'some'
}

/**
 * 三态开关（B / I）的下一个值。
 *
 * mixed 点一次 = 全开（先把它们对齐，再想要不要关），全开点一次 = 全关，
 * 全关点一次 = 全开。**没有「点一次回到 mixed」**——mixed 不是用户能选的
 * 目标状态，它只是当前事实的描述。
 */
export function nextToggleValue(
  state: ControlValue,
  onValue: string,
  offValue: string,
): string {
  if (state.kind === 'uniform' && state.value === onValue) return offValue
  return onValue
}

/** 三态开关当前该画成什么样 */
export function toggleStateOf(state: ControlValue, onValue: string): 'on' | 'off' | 'mixed' {
  if (state.kind === 'mixed') return 'mixed'
  return state.kind === 'uniform' && state.value === onValue ? 'on' : 'off'
}
