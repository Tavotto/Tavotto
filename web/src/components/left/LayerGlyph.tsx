import type { ReactNode } from 'react'
import type { CanvasObject } from '@/types/document'
import { DEFAULT_STAR_POINTS, DEFAULT_STAR_RATIO } from '@/types/document'
import { polygonPoints, starPoints } from '@/lib/shapeGeometry'
import { layerIconPaint } from '@/lib/layerIconPaint'
import { useEffectiveTheme } from '@/lib/theme'

/**
 * 图层树行首的对象图标：每种对象一枚线条图标（24 网格、描边 1.5、圆头圆角，与 `components/ui/icons` 同一套渲染，16px 下 1px 线，
 * 不加粗），再按对象自己的颜色上色（配色规则与理由见 `lib/layerIconPaint`）：
 * 实心图形 = 原色填充 + 同色系细外框；只有描边 / 线 / 文字 = 描边色（对比度夹进可读区间）；隐藏 = 再去饱和。
 * 没有颜色的（子图）与解析不了颜色的走默认的 ink-3。
 */
type Paint = { stroke: string; fill: string }

const pts = (a: [number, number][]) => a.map((p) => p.map((v) => +v.toFixed(2)).join(' ')).join(' ')

const GLYPHS: Record<string, (p: Paint) => ReactNode> = {
  rect: ({ fill }) => <rect x="3" y="6" width="18" height="12" rx="2.5" fill={fill} />,
  ellipse: ({ fill }) => <ellipse cx="12" cy="12" rx="9" ry="6.5" fill={fill} />,
  triangle: ({ fill }) => <path d="M12 4.5L21 19H3z" fill={fill} />,
  diamond: ({ fill }) => <path d="M12 3.5L20.5 12 12 20.5 3.5 12z" fill={fill} />,
  polygon: ({ fill }) => (
    <polygon points={pts(polygonPoints(6, 18, 18, 0).map(([x, y]) => [x + 3, y + 3]))} fill={fill} />
  ),
  star: ({ fill }) => (
    <polygon
      points={pts(starPoints(DEFAULT_STAR_POINTS, 18, 18, 0, DEFAULT_STAR_RATIO + 0.08).map(([x, y]) => [x + 3, y + 3.2]))}
      fill={fill}
    />
  ),
  line: ({ stroke }) => (
    <>
      <path d="M6.5 17.5L17.5 6.5" />
      <circle cx="5" cy="19" r="1.7" fill={stroke} stroke="none" />
      <circle cx="19" cy="5" r="1.7" fill={stroke} stroke="none" />
    </>
  ),
  arrow: ({ stroke }) => (
    <>
      <path d="M6.5 17.5L18 6" />
      <path d="M10.5 6H18v7.5" />
      <circle cx="5" cy="19" r="1.7" fill={stroke} stroke="none" />
    </>
  ),
  brace: () => <path d="M14.5 4.5c-2.5 0-3 1-3 3v2c0 1.5-1 2.5-2.5 2.5 1.5 0 2.5 1 2.5 2.5v2c0 2 .5 3 3 3" />,
  arc: ({ stroke }) => (
    <>
      <path d="M5 19C5 11 11 5 19 5" />
      <circle cx="5" cy="19" r="1.5" fill={stroke} stroke="none" />
      <circle cx="19" cy="5" r="1.5" fill={stroke} stroke="none" />
    </>
  ),
  // 开放曲线：一道 S 形，两端各一个锚点
  path: ({ stroke }) => (
    <>
      <path d="M5 17.5C6 7 12.5 6.5 12.5 12S18.5 17 19.5 7" />
      <circle cx="5" cy="17.5" r="1.6" fill={stroke} stroke="none" />
      <circle cx="19.5" cy="7" r="1.6" fill={stroke} stroke="none" />
    </>
  ),
  // 闭合路径（色块）：一个规整的柔边闭合轮廓，不复用 S 形
  pathClosed: ({ fill }) => <path d="M4.5 12.5C4.5 7.5 8.5 4.5 13 5.5s7 3.5 6.5 7.5-4 6-8 5.5-7-2.5-7-6z" fill={fill} />,
  pathText: () => (
    <>
      <path d="M3.5 19.5C7.5 13 16.5 13 20.5 19.5" />
      <path d="M8 4.5h8M12 4.5V12" />
    </>
  ),
  text: () => <path d="M5.5 6h13M12 6v13M9.5 19h5" />,
  panel: () => (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
      <path d="M7.5 15l3.2-4 2.8 2.6 3-4.4" />
    </>
  ),
}

/** 画得出「里面」的字形：有填充才画实心 */
const CLOSED = new Set(['rect', 'ellipse', 'triangle', 'diamond', 'polygon', 'star', 'pathClosed'])

/** 这个对象用哪一枚字形；没有（更新的构建写进来的类型、子图以外的不认识对象）= null，调用方退回通用图标 */
export function glyphKeyOf(o: CanvasObject): string | null {
  const k =
    o.type === 'shape'
      ? o.shape
      : o.type === 'path' && o.subpaths.some((s) => s.closed)
        ? 'pathClosed'
        : o.type
  return k in GLYPHS ? k : null
}

export function LayerGlyph({ obj, hidden }: { obj: CanvasObject; hidden: boolean }) {
  const theme = useEffectiveTheme()
  const key = glyphKeyOf(obj)
  if (!key) return null
  const src = obj as { color?: unknown; fill?: unknown; stroke?: unknown }
  const solid = typeof src.fill === 'string' && CLOSED.has(key)
  const paint =
    obj.type === 'panel'
      ? null
      : layerIconPaint(
          {
            color: typeof src.color === 'string' ? src.color : null,
            fill: solid ? (src.fill as string) : null,
          },
          theme,
          hidden,
        )
  return (
    <svg
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke={paint?.stroke ?? 'currentColor'}
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={paint ? 'shrink-0' : 'shrink-0 text-ink-3'}
      aria-hidden
    >
      {GLYPHS[key]({ stroke: paint?.stroke ?? 'currentColor', fill: paint?.fill ?? 'none' })}
    </svg>
  )
}
