import type { SVGProps } from 'react'

import { BRAND_MARK_BOX, BRAND_MARK_PATHS, type BrandMarkRole } from './brandMark.geometry'

/**
 * 品牌图形标（Tavotto_Master_05）：带「t」字横笔的圆角方框，右下角一片被切开、
 * 挪开的弧。
 *
 * 几何唯一出处是 scripts/build_brand_assets.py（从 .ai 原样抽出的两条路径），
 * 本文件 import 的 brandMark.geometry.ts 由它生成。这里只负责上色，色值不
 * 手写 hex，走 CSS token：框与横笔 = --color-ink，切开的那片 = --color-selected
 * （品牌里这片灰与界面选中态是同一个值；**品牌里没有蓝**）。
 *
 * 同一形从 16 px 到 1024 px 都用它，不再按尺寸分档。
 */

type Tone = 'default' | 'paper' | 'reverse' | 'mono'

const TONES: Record<Tone, Record<BrandMarkRole, string>> = {
  default: { ink: 'var(--color-ink)', piece: 'var(--color-selected)' },
  // 纸色 / 画布底：与白底同一套——#d7d7cf 在 #f2f2ef 上已经分得开
  paper: { ink: 'var(--color-ink)', piece: 'var(--color-selected)' },
  // 深底：整体反白，片换中灰
  reverse: { ink: 'var(--color-bg)', piece: 'var(--color-ink-2)' },
  // 单色：随文字颜色（印刷即纯黑）
  mono: { ink: 'currentColor', piece: 'currentColor' },
}

export interface BrandMarkProps extends Omit<SVGProps<SVGSVGElement>, 'width' | 'height'> {
  /** 渲染边长（px） */
  size: number
  tone?: Tone
  /**
   * 可访问名称。与产品名文字并排时不要传（图形是装饰，aria-hidden）；
   * 单独出现时传 'Tavotto'。
   */
  title?: string
}

export function BrandMark({ size, tone = 'default', title, ...props }: BrandMarkProps) {
  const c = TONES[tone]
  return (
    <svg
      viewBox={`0 0 ${BRAND_MARK_BOX} ${BRAND_MARK_BOX}`}
      width={size}
      height={size}
      className="shrink-0"
      {...(title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
      {...props}
    >
      {BRAND_MARK_PATHS.map((p) => (
        <path key={p.role} d={p.d} fill={c[p.role]} fillRule="evenodd" />
      ))}
    </svg>
  )
}
