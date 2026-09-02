import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalDistributeCenter,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalDistributeCenter,
  MoveHorizontal,
  MoveVertical,
} from 'lucide-react'
import type { AlignMode } from '@/lib/geometry'
import type { AlignRef } from '@/store/actions'

/**
 * 排列按钮表：图标、顺序、最少对象数、tooltip 用哪句。
 * 属性页的 `ArrangeSection` 与画布上的多选浮动栏
 * （`canvas/context-bar/MultiSelectionBar`）读的是同一份——两个入口的按钮不许各抄一份。
 */
export interface ArrangeButton {
  mode: AlignMode
  icon: typeof AlignStartVertical
  /** 带条件的长提示（inspector:arrange.*）；缺省用 alignMode.* 的通用短名 */
  tipKey?: string
  /** 少于这么多对象时不可用 */
  min: number
}

export const ALIGN_BUTTONS: readonly ArrangeButton[] = [
  { mode: 'left', icon: AlignStartVertical, min: 1 },
  { mode: 'hcenter', icon: AlignCenterVertical, min: 1 },
  { mode: 'right', icon: AlignEndVertical, min: 1 },
  { mode: 'top', icon: AlignStartHorizontal, min: 1 },
  { mode: 'vcenter', icon: AlignCenterHorizontal, min: 1 },
  { mode: 'bottom', icon: AlignEndHorizontal, min: 1 },
]

/** 分布：≥3 个对象才有意义，提示句里说明 */
export const DISTRIBUTE_BUTTONS: readonly ArrangeButton[] = [
  { mode: 'hdist', icon: AlignHorizontalDistributeCenter, tipKey: 'hdist', min: 3 },
  { mode: 'vdist', icon: AlignVerticalDistributeCenter, tipKey: 'vdist', min: 3 },
]

/** 统一尺寸：两个就行 */
export const SIZE_BUTTONS: readonly ArrangeButton[] = [
  { mode: 'samew', icon: MoveHorizontal, min: 2 },
  { mode: 'sameh', icon: MoveVertical, min: 2 },
]

/** 对齐参照的三档（顺序即界面顺序） */
export const ALIGN_REFS: readonly AlignRef[] = ['selection', 'page', 'primary']
