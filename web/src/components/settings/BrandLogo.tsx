import { Link2 } from '@/components/ui/icons'

/** 通用图标（取别名：尺寸来自调用方的 size，而不是在这里写死的档位；与 `VendorIcon` 同一做法） */
const Generic = Link2
import { cn } from '@/lib/utils'
import { BRAND_LOGOS } from './brandLogoData'
import { VENDOR_GLYPHS } from './vendorIcons'

/** 目录里的 logo 键 → 图形键（ChatGPT 用 OpenAI 的标志、Codex 同源） */
const ALIAS: Record<string, string> = {
  chatgpt: 'openai',
  codex: 'openai',
  google: 'gemini',
  tongyi: 'qwen',
  chatglm: 'zhipu',
}

/**
 * 厂商的真实标志：直接画，没有底、没有边框（与 `VendorIcon` 同一姿态）。
 *
 * 有彩色版的厂商画彩色，深浅主题下原样；单色 / 深色的标志（OpenAI、Anthropic、Kimi 的 K 等）一律
 * `currentColor` 并跟随文字色 `text-ink`——深色主题下 ink 翻成浅色，标志自然反色，不写字面色。
 * 没有彩色版的厂商用单色图形；表外的键（Synthetic、自定义接口）落到通用图标，不是空白。
 * 图形全部本地内联，没有远程图片。名字永远在旁边，所以这里不带可访问文本。
 */
export function BrandLogo({
  logo,
  size = 20,
  className,
}: {
  logo?: string | null
  /** 图形边长（px） */
  size?: number
  className?: string
}) {
  const key = logo ? (ALIAS[logo] ?? logo) : ''
  const color = BRAND_LOGOS[key]
  const mono = VENDOR_GLYPHS[key]
  const glyph = size
  return (
    <span
      aria-hidden
      data-brand-logo={color || mono ? key : 'generic'}
      style={{ width: size, height: size }}
      className={cn(
        'flex shrink-0 items-center justify-center text-ink',
        className,
      )}
    >
      {color ? (
        <svg width={glyph} height={glyph} viewBox="0 0 24 24">
          {color.grads && (
            <defs>
              {color.grads.map((g) => (
                <linearGradient
                  key={g.id}
                  id={g.id}
                  x1={g.x1}
                  x2={g.x2}
                  y1={g.y1}
                  y2={g.y2}
                  gradientUnits={g.user ? 'userSpaceOnUse' : undefined}
                >
                  {g.stops.map((s, i) => (
                    <stop key={i} offset={s.o} stopColor={s.c} stopOpacity={s.a} />
                  ))}
                </linearGradient>
              ))}
            </defs>
          )}
          {color.paths.map((p, i) => (
            <path key={i} d={p.d} fill={p.fill} fillRule={p.rule as 'nonzero' | undefined} />
          ))}
        </svg>
      ) : mono ? (
        <svg width={glyph} height={glyph} viewBox="0 0 24 24" fill="currentColor" fillRule="evenodd">
          {mono.map((p, i) => (
            <path key={i} d={p.d} fillOpacity={p.o} />
          ))}
        </svg>
      ) : (
        <Generic size={glyph} aria-hidden />
      )}
    </span>
  )
}
