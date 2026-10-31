import { createContext, useContext, type HTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * 图标体系的唯一出处（`docs/ux/ICONOGRAPHY.md` 是它的说明书，决策在 ADR 0052）。
 *
 * 全产品只有一套图标：`components/ui/icons/` 里自己画的那 140 个（24 网格、描边 2、
 * 实心孪生做选中态）。这里定的是**怎么用**——尺寸阶梯、描边粗细、以及「不写尺寸
 * 就拿到默认档」的机制。手写内联 svg、emoji、拿 Unicode 字符当图标都不算图标
 * （门禁在 `iconography.test.tsx`）。
 *
 * 尺寸阶梯只有四档，按用途选，不按「旁边那个多大」凑：
 *   - xs 12：装饰性折叠箭头、徽标 / 角标里的小记号、11px 说明文字旁；
 *   - sm 16（默认）：与 12–13px 界面文字并排——菜单项、按钮里的图标、
 *     检查器行首、树行状态标记、横幅提示。2026-10-31 起由 14 改为 16（ADR 0157，用户选定）：
 *     几何按 16px 对齐像素，14px 上对不齐、看着糊。档名保留，与 md 同值；
 *   - md 16：**只有图标**的按钮（28px 点击区）、顶栏工具、左侧图标轨道、
 *     对话框标题栏的关闭钮；
 *   - lg 20：空状态、引导卡片、对话框级别的强调图。
 * 更大的「展示级」尺寸不是图标：品牌标 `BrandMark` 与 Agent 头像框
 * `AgentIcon` 各管各的。
 *
 * 描边 1.5，**按比例缩放**：16px 上正好 1px（2 倍屏 2 个物理像素），20px 上 1.25px、12px 上
 * 0.75px。2026-10-31 由 2 改为 1.5（ADR 0157）：2 在 16px 上是 1.33px，小图标里的空隙被挤没、
 * 读着「糊」；同时几何上每个落在线上的点都吸到 0.75 网格（`defs.ts` 的 `snapToPixelGrid`），
 * 16px 上边缘落在整像素上。
 *
 * 加粗档只留给一种场景：填色小方块里的对勾（复选框选中态），1px 的勾在
 * 14px 的蓝底上看不见。
 */
export const ICON_SIZE = {
  xs: 12,
  sm: 16,
  md: 16,
  lg: 20,
} as const

export type IconSizeStep = keyof typeof ICON_SIZE

export const ICON_STROKE = {
  regular: 1.5,
  emphasis: 2,
} as const

export interface IconDefaults {
  size: number
  strokeWidth: number
  className: string
}

const DEFAULTS: IconDefaults = {
  size: ICON_SIZE.sm,
  strokeWidth: ICON_STROKE.regular,
  // 在 flex 行里别被挤扁——之前它在三百多处被手写，漏一处就是一个窄行里被压成椭圆的图标
  className: 'shrink-0',
}

const IconDefaultsContext = createContext<IconDefaults>(DEFAULTS)

/** 图标组件读默认档用；只有 `icons/createIcon.tsx` 该调用它 */
export const useIconDefaults = () => useContext(IconDefaultsContext)

/**
 * 根上改默认档的地方。图标集自己的默认就是上面的阶梯（不套也一样），所以三个
 * React 根（工作台 `main.tsx`、playground、Codex 内嵌画布）套它只是为了让
 * 「默认档在哪改」只有一个答案；单测里渲染的组件如果关心尺寸，也用它包一下。
 */
export function IconProvider({
  children,
  ...overrides
}: { children: ReactNode } & Partial<IconDefaults>) {
  const value = { ...DEFAULTS, ...overrides }
  return <IconDefaultsContext.Provider value={value}>{children}</IconDefaultsContext.Provider>
}

/**
 * 图标槽：「图标 + 文字」行里图标的唯一对齐手段。槽高 = 这一行文字的行高（`h-lh`，
 * 即 CSS `1lh`，取槽所在行容器继承到的 line-height），图标在槽里居中，所以图标中心 =
 * 第一行文字的中心——单行、折成两行、换字号 / 图标档都不用调；行容器写 `items-start`，
 * 不许再给图标手写 `mt-px / mt-0.5 / mt-[3px]`（`iconography.test.tsx`「图标不靠
 * margin-top 凑对齐」守着）。
 *
 * 槽的行高是**行容器**的，不是兄弟文字的：文字自己带 `type-title` 之类的行高时（图标在
 * 容器上、文字在兄弟元素上），给槽写同一个行高 `className="h-5"`，不要回到 margin。
 * 桌面最低 macOS 14（Safari 17）/ WebView2，`lh` 单位可用。
 */
export function IconSlot({ className, children, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span data-icon-slot className={cn('inline-flex h-lh shrink-0 items-center justify-center', className)} {...rest}>
      {children}
    </span>
  )
}
