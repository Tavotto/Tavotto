import {
  Braces,
  Circle,
  Diamond,
  Hexagon,
  Minus,
  MoveUpRight,
  Square,
  Triangle,
} from 'lucide-react'
import type { ComponentType } from 'react'
import type { SwitchKind } from '@/lib/shapeSwitch'

/**
 * 可切换类型的图标。**一份表两个入口**：属性栏对象标题的下拉与右键菜单的
 * 「更改为 ›」共用它——两份表迟早会各画各的（`arrangeButtons.ts` 是同一条
 * 理由下的先例）。
 *
 * 写成 `Record<SwitchKind, …>`：给 `ShapeKind` 加一种形状而忘了给图标，这里
 * 当场编译不过，而不是在界面上留一个空格。
 *
 * 图标之外**必须有文字名**（`switchKindLabel`），两个入口都照办：一排轮廓
 * 图形在 12px 上分不出三角与菱形，色觉与视力都不该是读懂它的前提。
 */
export const KIND_SWITCH_ICON: Record<SwitchKind, ComponentType<{ size?: number; className?: string }>> = {
  rect: Square,
  ellipse: Circle,
  triangle: Triangle,
  diamond: Diamond,
  polygon: Hexagon,
  brace: Braces,
  line: Minus,
  arrow: MoveUpRight,
}
