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
  /** 子图纵横比：自动 / 等比例 / 自定义比例——绝不落进文字编辑器 */
  | 'aspect'
  /** 图内文字效果的开关（背景 / 描边）：关着画成「＋添加」，开了才是开关 */
  | 'effect'
  /**
   * 0–1 的透明度：界面按百分比显示与输入（75%），写回仍是 0–1（审计 T16 / T20 / T22）。
   * 换算只在这一种控件里做，按 prop 名在展示注册表指定——不在各角色的组件里各抄一份
   */
  | 'percent'
  /** 色条方向：竖直 / 水平，用当前色图画的小色条预览 */
  | 'colorbar-orientation'
  /** 色条两端的延伸三角：无 / 下端 / 上端 / 两端，小色条预览 */
  | 'colorbar-extend'
  /** 三维投影方式：透视 / 正交，两个小立方体预览 */
  | 'projection'

/** 一条被摆好位置的字段：manifest 的字段本体 + 展示决策 */
export interface PresentedField {
  field: EditableField
  priority: InspectorPriority
  control: ControlKind
  /** 桶内排序键（越小越靠前） */
  order: number
}

/**
 * 摘要行（2026-10-01 属性栏重设计）：一组低频字段收成「名字 + 当前值 + ›」一行，
 * 点开在原地展开成原来的控件。角色模板里点名的字段**从 primary / more / advanced
 * 里被它认领走**——同一个字段只在一处出现，字段进来多少出去多少。
 */
export interface FoldSpec {
  /** 折叠行 id：展开状态按「角色:id」记在 `inspectorPrefs.foldOpen`，也是 e2e 的稳定锚点 */
  id: string
  /** 行名：`inspector:element.<labelKey>` */
  labelKey: string
  /** 认领的字段（可以只有一部分在场；一条都不在场则这一行不出现） */
  props: string[]
}

/** 一条被认领、已按模板 `props` 顺序排好的摘要行 */
export interface PresentedFold {
  spec: FoldSpec
  fields: PresentedField[]
}

/** presentFields 的产出：三个桶 + 摘要行，各自已按 order 排好 */
export interface PresentedBuckets {
  primary: PresentedField[]
  more: PresentedField[]
  advanced: PresentedField[]
  /** 模板点名的摘要行；一条字段都没有的不在这里（计数为 0 的节不显示） */
  folds: PresentedFold[]
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
  /**
   * 并排成一行的字段对（「色阶下限 / 上限」）。两条仍是各自的 manifest 字段、
   * 各写各的 override；只在两条都在同一个桶里时并排，缺一条就各画各的。
   */
  pairRows?: [string, string][]
  /**
   * 首屏字段的分组小标题（2026-09-13 审计 B48：曲线 = 名称 → 线条 → 标记）。
   * 一组从它点名的第一个**在场**字段起，到下一组的第一个在场字段止；第一组之前
   * 的字段（系列名）不带标题。只管首屏——「更多」里按引擎分组，「高级」不分。
   * 组名是 `inspector:element.<labelKey>`。
   */
  primaryGroups?: { labelKey: string; props: string[] }[]
  /**
   * 摘要行：模板点名的字段不进 primary / more / advanced，改收成一行摘要
   * （设计稿 A 章 map 里标「收进折叠」的那些）。没点名的低频字段仍走通用的
   * 「更多」摘要行。`visibleWhen` 照旧先判：被条件收起的字段不进摘要行。
   */
  folds?: FoldSpec[]
}
