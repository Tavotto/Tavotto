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
 * 厂商的真实彩色标志，坐在**浅色圆角底**上（像 App 图标）。
 *
 * 底色用 `bg-paper`（纸：两套主题同值的白）：彩色标志是为浅底设计的，深色主题里直接画在深色表面上
 * 会糊掉（Kimi 的黑、OpenAI 的黑），所以底永远是浅的；没有彩色版的厂商（OpenAI / Anthropic 等）
 * 用单色图形画成纸墨色（`text-paper-ink`）。图形全部本地内联，没有远程图片。
 *
 * `logo` 来自后端厂商目录的 `logo` 键；表外的键（Synthetic、自定义接口）落到通用图标，不是空白。
 * 名字永远在旁边，所以这里不带可访问文本。
 */
export function BrandLogo({
  logo,
  size = 32,
  className,
}: {
  logo?: string | null
  /** 底的边长（px） */
  size?: number
  className?: string
}) {
  const key = logo ? (ALIAS[logo] ?? logo) : ''
  const color = BRAND_LOGOS[key]
  const mono = VENDOR_GLYPHS[key]
  const glyph = Math.round(size * 0.62)
  return (
    <span
      aria-hidden
      data-brand-logo={color || mono ? key : 'generic'}
      style={{ width: size, height: size }}
      className={cn(
        'flex shrink-0 items-center justify-center rounded-lg border border-paper-ink/10 bg-paper text-paper-ink',
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
