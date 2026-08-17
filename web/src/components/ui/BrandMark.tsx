import type { SVGProps } from 'react'

/**
 * 品牌图形标「一页正在排版的 Figure」。
 *
 * 几何唯一出处是 scripts/build_brand_assets.py（Brand System Rev 2.0），
 * 这里是它在界面内的绘制——色值不手写 hex，走 CSS token：
 * 框与线 = --color-ink，灰块 = --color-border（纸色底上换 -strong），
 * 蓝块 = --color-accent（品牌蓝；选中蓝 --color-sel 永不进标志）。
 *
 * 三档不是等比缩放而是三个形：≥40px full（两级线宽 20/10）、
 * 22–39px compact（去分栏线、结构线加粗 32）、≤21px mini（实心 Ink 底）。
 * 不传 variant 时按 size 自动选档；TopBar 的 20px compact 是规范里的显式例外。
 */

type Variant = 'full' | 'compact' | 'mini'
type Tone = 'default' | 'paper' | 'reverse' | 'mono'

/** 深底提亮蓝：规范允许的唯一例外色，不进入令牌 */
const REVERSE_BLUE = '#4a8ede'
/** 单色（印刷）版的灰：同样只在此处出现 */
const MONO_GREY = '#d8d8d0'

const TONES: Record<Tone, { ink: string; grey: string; blue: string }> = {
  default: {
    ink: 'var(--color-ink)',
    grey: 'var(--color-border)',
    blue: 'var(--color-accent)',
  },
  // 纸色 / 画布底（#f2f2ef / #eaeae6）：灰块换深一档，与背景分开
  paper: {
    ink: 'var(--color-ink)',
    grey: 'var(--color-border-strong)',
    blue: 'var(--color-accent)',
  },
  // 深底：整体反白，蓝提亮（品牌蓝在 Ink 底上对比只有 ~3.1:1）
  reverse: {
    ink: 'var(--color-bg)',
    grey: 'var(--color-ink-2)',
    blue: REVERSE_BLUE,
  },
  // 单色：随文字颜色（印刷即纯黑），蓝并入 ink
  mono: { ink: 'currentColor', grey: MONO_GREY, blue: 'currentColor' },
}

function pickVariant(size: number): Variant {
  if (size >= 40) return 'full'
  if (size >= 22) return 'compact'
  return 'mini'
}

export interface BrandMarkProps extends Omit<SVGProps<SVGSVGElement>, 'width' | 'height'> {
  /** 渲染边长（px） */
  size: number
  /** 缺省按 size 自动选档；显式传入以覆盖（如 TopBar 的 20px compact） */
  variant?: Variant
  tone?: Tone
  /**
   * 可访问名称。与产品名文字并排时不要传（图形是装饰，aria-hidden）；
   * 单独出现时传 'Magplot'。
   */
  title?: string
}

export function BrandMark({ size, variant, tone = 'default', title, ...props }: BrandMarkProps) {
  const v = variant ?? pickVariant(size)
  const c = TONES[tone]
  return (
    <svg
      viewBox="0 0 1024 1024"
      width={size}
      height={size}
      className="shrink-0"
      {...(title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
      {...props}
    >
      {v === 'full' && (
        <>
          <rect x={98} y={98} width={492} height={340} fill={c.grey} />
          <rect x={652} y={652} width={284} height={284} fill={c.grey} />
          <rect x={88} y={88} width={848} height={848} fill="none" stroke={c.ink} strokeWidth={20} />
          <rect x={652} y={652} width={284} height={284} fill="none" stroke={c.ink} strokeWidth={20} />
          <rect x={88} y={433} width={848} height={10} fill={c.ink} />
          <rect x={596} y={596} width={112} height={112} fill={c.blue} />
        </>
      )}
      {v === 'compact' && (
        <>
          <rect x={110} y={110} width={480} height={328} fill={c.grey} />
          <rect x={656} y={656} width={272} height={272} fill={c.grey} />
          <rect x={96} y={96} width={832} height={832} fill="none" stroke={c.ink} strokeWidth={32} />
          <rect x={656} y={656} width={272} height={272} fill="none" stroke={c.ink} strokeWidth={32} />
          <rect x={592} y={592} width={128} height={128} fill={c.blue} />
        </>
      )}
      {v === 'mini' && (
        <>
          <rect width={1024} height={1024} fill={c.ink} />
          <rect x={96} y={96} width={528} height={368} fill={c.grey} />
          <rect x={592} y={592} width={336} height={336} fill={c.grey} />
          <rect x={512} y={512} width={160} height={160} fill={c.blue} />
        </>
      )}
    </svg>
  )
}
