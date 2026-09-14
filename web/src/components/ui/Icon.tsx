import { createContext, useContext, type ReactNode } from 'react'

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
 *   - sm 14（默认）：与 12–13px 界面文字并排——菜单项、按钮里的图标、
 *     检查器行首、树行状态标记、横幅提示；
 *   - md 16：**只有图标**的按钮（28px 点击区）、顶栏工具、左侧图标轨道、
 *     对话框标题栏的关闭钮；
 *   - lg 20：空状态、引导卡片、对话框级别的强调图。
 * 更大的「展示级」尺寸不是图标：品牌标 `BrandMark` 与 Agent 头像框
 * `AgentIcon` 各管各的。
 *
 * 描边 2，**按比例缩放**：默认档 14px 上画成 1.17px、16px 上 1.33px、20px 上
 * 1.67px、12px 上正好 1px——比 13px 正文的笔画略重而不是更轻（1.75 时 14px 只有
 * 1.02px，比旁边的字还细，整套图标就像线框稿）。2 / 24 也是 OpenAI 图标集的比例。
 * 图形在 24 网格上留的最小净空是 2 单位，12px 档上只剩 1px，所以描边不能固定
 * 成绝对像素，只能按比例。
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
  regular: 2,
  emphasis: 2.5,
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
