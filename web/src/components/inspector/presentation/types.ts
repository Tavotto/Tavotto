import type { EditableField } from '@/lib/api'

/**
 * 展示层的三级层级。与 manifest 的 group 不同，这是**任务视角**的分层：
 * primary 永远展开（当前对象最常改的 4–8 个属性）；more 是唯一的中频折叠区；
 * advanced 收纳低频与诊断（层级 zorder、裸 rect 位置等）。
 * 源文件/写回/历史不走这套——它们在 SourceAdvancedSection 里另有一层。
 */
export type InspectorPriority = 'primary' | 'more' | 'advanced'

/**
 * 控件形态。基础的几种沿用字段类型；带连字符的是视觉选择器——
 * enum 不再无条件落成文字 Select（docs/ux/INSPECTOR_REDESIGN.md P5）。
 */
export type ControlKind =
  | 'text'
  | 'number'
  | 'color'
  | 'toggle'
  | 'select'
  | 'font'
  | 'line-style'
  | 'marker'
  | 'hatch'
  | 'colormap'
  | 'legend-position'
  | 'legend-binding'
  | 'arrow-style'
  | 'pair'
  | 'rect'
  | 'order'
  | 'number-list'

/** 一条被摆好位置的字段：manifest 的字段本体 + 展示决策 */
export interface PresentedField {
  field: EditableField
  priority: InspectorPriority
  control: ControlKind
  /** 桶内排序键（越小越靠前） */
  order: number
}

/** presentFields 的产出：三个桶，各自已按 order 排好 */
export interface PresentedBuckets {
  primary: PresentedField[]
  more: PresentedField[]
  advanced: PresentedField[]
}

/**
 * 角色模板。只列**顺序与归属**，不列能力——字段 manifest 里没有就自动跳过，
 * 模板里没点名的字段按兜底规则进 more/advanced，绝不丢失。
 */
export interface RoleProfile {
  /** 首屏属性，按此顺序渲染 */
  primary: string[]
  /** 显式排进「更多」前部的属性（其余按引擎顺序跟在后面） */
  more?: string[]
  /** 显式压进「高级」的属性（在通用规则之外补充） */
  advanced?: string[]
  /**
   * 条件显示：仅当返回 true（或该属性已被用户改过）才渲染。
   * 用于「主刻度间距只在 step 模式下有意义」这类模式从属字段——
   * 摆一个此刻写了也不生效的控件，比藏起来更不诚实。
   */
  visibleWhen?: Record<string, (read: (prop: string) => unknown) => boolean>
}
