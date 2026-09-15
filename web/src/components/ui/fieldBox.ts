import { cn } from '@/lib/utils'

/**
 * 可编辑框的「框」——全站只有这一份（2026-09-14 审计 S8，用户拍板「甲」）。
 *
 * 此前有两套语法并存：文字框白底 + hairline；数字框 / 下拉 / 搜索框 / 各种样张
 * 选择器是 surface-2 灰底、无边、hover 才浮出边框——同一列里「名称」白框、「线宽」
 * 灰框、「线型」又是白框。灰底对白面板只有 1.07:1，静态时分不出谁能改、谁只是只读值。
 * 现在：**有框 = 能改**；只读摘要继续无框。
 *
 * 「框」是一块比面板深一级的底（`field`，对白 1.14:1），静态无边线（2026-09-15 参考 Codex 设置页的
 * 输入框，用户拍板「普通输入框稍微换一个颜色就可以了」）；hover 底再深一档；**聚焦 / 打开才是不透明
 * accent 边**——3:1 由它承担。边线常驻透明 1px，聚焦不改盒子尺寸。同一天先试过再加一层内阴影的
 * 「凹面」，用户看过对比后取消，只留颜色。TextInput / TextArea / NumberField / Select / SearchInput /
 * PickerTrigger 都从这里取，不各写一遍；改图助手的输入框是浮在对话流上的玻璃，另一副（AiPanel）。
 */
export const FIELD_BOX = cn(
  // 框里的值是「要读的字」：12px（正文档），标签 / caption / meta 留 11px（2026-09-14 审计分歧 1，用户拍板试 12）
  'rounded-sm border border-transparent bg-field text-sm text-ink transition-colors duration-fast',
  'hover:bg-field-hover',
)
/** 框本身就是 `<input>` 时的聚焦态 */
export const FIELD_FOCUS = 'focus:border-accent'
/** 框是外壳、里面才是 `<input>` 时的聚焦态 */
export const FIELD_FOCUS_WITHIN = 'focus-within:border-accent'
/** 弹层触发器（Select / picker）打开时与聚焦同一副样子 */
export const FIELD_OPEN = 'data-[state=open]:border-accent'
export const FIELD_INVALID = 'border-danger hover:border-danger'
/** 禁用态与 Button / Checkbox 同一档；hover 不再变色 */
export const FIELD_DISABLED = 'cursor-not-allowed opacity-40 hover:bg-field'
