/**
 * 推荐配色目录（ADR 0210 §一）。色值的唯一权威是 Python 包里的两份 JSON，这里经别名整份 import：
 *
 *   @palettes          src/tavotto/profiles/palettes.json            许可宽松
 *   @palettesCopyleft  src/tavotto/profiles/palettes_copyleft.json   GPL 的期刊风格色板
 *
 * 构建时设 `TAVOTTO_NO_COPYLEFT_PALETTES=1`，第二份文件加载时换成 `copyleftEmpty.json`（`web/scripts/palette-copyleft-path.mjs` 的插件），
 * 产物里就没有那些色值；引擎侧同名开关在 `engine/palettes.py`。
 */
import permissiveDoc from '@palettes'
import copyleftDoc from '@palettesCopyleft'

export interface PaletteSource {
  id: string
  name: string
  license: string
  url: string
  copyleft: boolean
  cite?: string
  doi?: string
  attribution?: string
  note?: string
}

export interface CatalogPalette {
  /** 稳定 id，文档里只存它（`crameri.batlowS`） */
  id: string
  name: string
  /** 期刊风格色板：仿的是哪本期刊的图（显示成「风格」，不是官方） */
  styleOf?: string
  source: string
  kind: 'categorical'
  /** `#RRGGBB`，大写，按作者给的顺序 */
  colors: readonly string[]
  /** 方案作者自带的中性灰（Tol Bright / Vibrant 的 #BBBBBB）；没有就用目录顶层的 */
  neutral?: string
  license: string
  copyleft: boolean
}

/** 首批默认推荐（ADR 0210 §五、§八：维护者可以改） */
export const DEFAULT_SCHEME = 'crameri.batlowS'

interface RawPalette {
  id: string
  name: string
  style_of?: string
  source: string
  kind: string
  colors: string[]
  neutral?: string
}
interface RawSource {
  name: string
  license: string
  url: string
  cite?: string
  doi?: string
  attribution?: string
  note?: string
}
interface RawDoc {
  neutral?: string
  sources: Record<string, RawSource>
  palettes: RawPalette[]
}

function build(docs: readonly [RawDoc, boolean][]) {
  const sources = new Map<string, PaletteSource>()
  const palettes: CatalogPalette[] = []
  for (const [doc, copyleft] of docs) {
    for (const [id, s] of Object.entries(doc.sources)) sources.set(id, { id, ...s, copyleft })
    for (const p of doc.palettes) {
      if (p.kind !== 'categorical') continue
      palettes.push({
        id: p.id,
        name: p.name,
        ...(p.style_of ? { styleOf: p.style_of } : {}),
        source: p.source,
        kind: 'categorical',
        colors: p.colors,
        ...(p.neutral ? { neutral: p.neutral } : {}),
        license: doc.sources[p.source]?.license ?? '',
        copyleft,
      })
    }
  }
  return { sources, palettes }
}

const BUILT = build([
  [permissiveDoc as RawDoc, false],
  [copyleftDoc as RawDoc, true],
])

/** 没有自带灰的方案用的中性对照色（目录顶层 `neutral`） */
export const CATALOG_NEUTRAL: string = (permissiveDoc as RawDoc).neutral ?? '#A6A6A6'

/** 这次构建里所有方案（期刊风格那份被构建开关排除时就没有） */
export const PALETTES: readonly CatalogPalette[] = BUILT.palettes

export function paletteById(id: string | undefined | null): CatalogPalette | null {
  if (!id) return null
  return BUILT.palettes.find((p) => p.id === id) ?? null
}

export function paletteSource(id: string): PaletteSource | null {
  return BUILT.sources.get(id) ?? null
}

/** 一套方案给「中性」槽用的颜色 */
export const neutralOf = (p: CatalogPalette): string => p.neutral ?? CATALOG_NEUTRAL
