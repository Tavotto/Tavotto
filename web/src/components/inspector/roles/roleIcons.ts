import {
  Blend,
  Box,
  ChartArea,
  ChartColumn,
  ChartLine,
  ChartScatter,
  Frame,
  GitCommitVertical,
  Image,
  LayoutList,
  Minus,
  MoveUpRight,
  Ruler,
  Shapes,
  Square,
  Type,
  WavesHorizontal,
  type LucideIcon,
} from 'lucide-react'

/**
 * 图内元素的角色 → 图标，**全产品只有这一张表**（ICONOGRAPHY.md 第四节：同一语义
 * 只用一个图标）。左栏元素树与右栏属性栏的身份头都从这里查——此前身份头对所有
 * 图内元素都画同一个图片图标，树里明明按角色分了（2026-09-12 critique P3）。
 *
 * 文字类的角色都是 `Type`——标题 / 轴标题 / 刻度文字的区别由标签说，图标只回答
 * 「这是什么类的东西」。认不出的角色回落到 `Shapes`。
 */
export const ROLE_ICONS: Record<string, LucideIcon> = {
  figure: Frame,
  axes: Square,
  axes3d: Box,
  text: Type,
  title: Type,
  axis_label: Type,
  ticklabel: Type,
  legend_text: Type,
  image: Image,
  line: ChartLine,
  scatter: ChartScatter,
  bar: ChartColumn,
  bar_series: ChartColumn,
  errorbar: GitCommitVertical,
  fill: ChartArea,
  linecoll: WavesHorizontal,
  ticks: Ruler,
  spine: Minus,
  grid: Ruler,
  legend: LayoutList,
  colorbar: Blend,
  patch: Shapes,
  arrow_patch: MoveUpRight,
}

export const roleIcon = (role: string): LucideIcon => ROLE_ICONS[role] ?? Shapes
