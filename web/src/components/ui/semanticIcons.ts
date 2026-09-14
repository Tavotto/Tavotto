import { SquareMousePointer } from './icons'

/**
 * 跨区域复用的**语义**图标（ICONOGRAPHY.md 第四节：同一含义只用一个图标）。
 * 角色图标在 `inspector/roles/roleIcons.ts`，这里只放不属于任何一个角色、却在
 * 好几个面板上都要认得出的那几个。
 *
 * **可编辑的图**（由脚本生成、能改图内对象）：图框 + 定位符。此前是 `{ }`
 * （2026-09-13 审计 B04 / B05 / B07：大括号说的是「参数化」这个实现词，用户
 * 得猜它的含义）。左轨「图内元素」、图层树与素材卡的角标、元素树空态、素材库
 * 「只看可编辑」筛选全部从这里取。
 */
export const EditableFigureIcon = SquareMousePointer
