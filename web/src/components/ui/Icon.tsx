import type { ReactNode } from 'react'
import { LucideProvider } from 'lucide-react'

/**
 * 图标体系的唯一出处（`docs/ux/ICONOGRAPHY.md` 是它的说明书）。
 *
 * 全产品只有一套图标：lucide-react 的描边图标。这里定的是**怎么用**——
 * 尺寸阶梯、描边粗细、以及「不写尺寸就拿到默认档」的机制。手写内联 svg、
 * emoji、拿 Unicode 字符当图标都不算图标（门禁在 `iconography.test.tsx`）。
 *
 * 尺寸阶梯只有四档，按用途选，不按「旁边那个多大」凑：
 *   - xs 12：装饰性折叠箭头、徽标 / 角标里的小记号、11px 说明文字旁；
 *   - sm 14（默认）：与 12–13px 界面文字并排——菜单项、按钮里的图标、
 *     检查器行首、树行状态标记、横幅提示；
 *   - md 16：**只有图标**的按钮（28px 点击区）、顶栏工具、左侧图标轨道、
 *     对话框标题栏的关闭钮；
 *   - lg 20：空状态、引导卡片、对话框级别的强调图。
 * 更大的「展示级」尺寸不是图标：品牌标 `BrandMark` 与 Agent 头像框
 * `AgentIcon` 各管各的。
 *
 * 描边 1.75，**按比例缩放**（不用 absoluteStrokeWidth）：默认档 14px 上正好
 * 画成 1px 的细线，与界面其它 1px 分隔线同一重量；16px ≈ 1.17px、
 * 20px ≈ 1.46px，随尺寸自然加重而不是所有档一样粗。选比例缩放还有一个
 * 硬理由：lucide 的图形在 24 网格上留的最小间隙是 2 单位，12px 档上只剩
 * 1px，一旦描边固定 1.5px 以上细节就糊成一团。1.75 落在 morphicons 那类
 * 变形库的 1.5–2.5 区间内，日后要接变形过渡时不必再调重量。
 *
 * 加粗档只留给一种场景：填色小方块里的对勾（复选框选中态），1px 的勾在
 * 14px 的蓝底上看不见。
 */
export const ICON_SIZE = {
  xs: 12,
  sm: 14,
  md: 16,
  lg: 20,
} as const

export type IconSizeStep = keyof typeof ICON_SIZE

export const ICON_STROKE = {
  regular: 1.75,
  emphasis: 2.5,
} as const

/**
 * 每个 React 根都要套一层：它让「不写 size 的 lucide 图标」拿到默认档，
 * 而不是 lucide 自己的 24px / 描边 2。三个根（工作台 `main.tsx`、
 * playground、Codex 内嵌画布）各套一次；单测里渲染的组件如果关心尺寸，
 * 也用它包一下。
 *
 * 顺带把「在 flex 行里别被挤扁」的那个类给到每一个图标——之前它在
 * 三百多处被手写，漏一处就是一个窄行里被压成椭圆的图标。
 */
export function IconProvider({ children }: { children: ReactNode }) {
  return (
    <LucideProvider
      size={ICON_SIZE.sm}
      strokeWidth={ICON_STROKE.regular}
      className="shrink-0"
    >
      {children}
    </LucideProvider>
  )
}
