import { cn } from '@/lib/utils'

/**
 * 可编辑框的「框」——全站只有这一份（2026-09-14 审计 S8，用户拍板「甲」）。
 *
 * 此前有两套语法并存：文字框白底 + hairline；数字框 / 下拉 / 搜索框 / 各种样张
 * 选择器是 surface-2 灰底、无边、hover 才浮出边框——同一列里「名称」白框、「线宽」
 * 灰框、「线型」又是白框。灰底对白面板只有 1.07:1，静态时分不出谁能改、谁只是只读值。
 * 现在：**有框 = 能改**；只读摘要继续无框。
 *
 * 边框色 `border-input`：白 3.48:1，识别控件边界的非文字对比过 3:1（WCAG 1.4.11）；
 * hover 再深一档到 ink-3，聚焦 accent。TextInput / TextArea / NumberField / Select /
 * SearchInput / PickerTrigger 都从这里取，不各写一遍。
 */
export const FIELD_BOX = cn(
  // 框里的值是「要读的字」：12px（正文档），标签 / caption / meta 留 11px（2026-09-14 审计分歧 1，用户拍板试 12）
  'rounded-sm border border-border-input bg-surface text-sm text-ink transition-colors duration-fast',
  'hover:border-border-input-hover',
)
/** 框本身就是 `<input>` 时的聚焦态 */
export const FIELD_FOCUS = 'focus:border-accent'
/** 框是外壳、里面才是 `<input>` 时的聚焦态 */
export const FIELD_FOCUS_WITHIN = 'focus-within:border-accent'
/** 弹层触发器（Select / picker）打开时与聚焦同一副样子 */
export const FIELD_OPEN = 'data-[state=open]:border-accent'
export const FIELD_INVALID = 'border-danger hover:border-danger'
/** 禁用态与 Button / Checkbox 同一档；hover 不再变色 */
export const FIELD_DISABLED = 'cursor-not-allowed bg-surface-2 opacity-40 hover:border-border-input'
