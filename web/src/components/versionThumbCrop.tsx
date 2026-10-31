import { cn } from '@/lib/utils'

type Box = { x: number; y: number; w: number; h: number }
type Page = { w: number; h: number }

/**
 * 页面上有东西的那块（mm，夹在页面内、四周留一点白）。
 *
 * 时间线里的缩略图（存好的位图、对比卡片的快照）拍的都是整页。单图编辑时根本没有
 * 「页面」这回事（beta 0.19.3b3 反馈），图却常常只占页面一角，缩成一小块偏在边上；
 * 排版里图只占一角时同理。时间线要回答的是「那一版长什么样」，所以一律对准内容。
 * 内容本来就差不多占满页面、或者没有可见对象时不裁（null = 整页）。
 */
export function contentBox(page: Page, objects: readonly (Box & { hidden?: boolean })[]): Box | null {
  const shown = objects.filter((o) => !o.hidden && o.w > 0 && o.h > 0)
  if (!shown.length || !(page.w > 0) || !(page.h > 0)) return null
  const x0 = Math.max(0, Math.min(...shown.map((o) => o.x)))
  const y0 = Math.max(0, Math.min(...shown.map((o) => o.y)))
  const x1 = Math.min(page.w, Math.max(...shown.map((o) => o.x + o.w)))
  const y1 = Math.min(page.h, Math.max(...shown.map((o) => o.y + o.h)))
  if (x1 <= x0 || y1 <= y0) return null
  const pad = Math.min(page.w, page.h) * 0.03
  const bx = Math.max(0, x0 - pad)
  const by = Math.max(0, y0 - pad)
  const box = { x: bx, y: by, w: Math.min(page.w, x1 + pad) - bx, h: Math.min(page.h, y1 + pad) - by }
  return box.w * box.h > page.w * page.h * 0.8 ? null : box
}

/** 两块的并集；任一边不裁（null）就都不裁——对比的两格必须用同一个框，位置变化才看得出来 */
export function unionBox(a: Box | null, b: Box | null): Box | null {
  if (!a || !b) return null
  const x = Math.min(a.x, b.x)
  const y = Math.min(a.y, b.y)
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y }
}

/**
 * 按 `box` 裁一张整页的东西：外框是 `width × height`（px），里面按页面比例摆一块页面大小的
 * 内层，内容中心对准外框中心。`children` 铺满内层（位图用 `size-full`，快照本来就按页面比例）。
 * `box` 为 null 时就是整页，外框按页面比例。
 */
export function CropFrame({
  page,
  box,
  width,
  height,
  className,
  children,
  ...rest
}: {
  page: Page
  box: Box | null
  width: number
  /** 不给 = 按裁出来那块的比例 */
  height?: number
  className?: string
  children: (pageWidthPx: number) => React.ReactNode
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'>) {
  const b = box ?? { x: 0, y: 0, w: page.w, h: page.h }
  const h = height ?? (width * b.h) / b.w
  const k = Math.min(width / b.w, h / b.h)
  return (
    <span
      {...rest}
      className={cn('relative block shrink-0 overflow-hidden bg-paper', className)}
      style={{ width, height: h }}
    >
      <span
        className="absolute block"
        style={{
          width: page.w * k,
          height: page.h * k,
          left: width / 2 - (b.x + b.w / 2) * k,
          top: h / 2 - (b.y + b.h / 2) * k,
        }}
      >
        {children(page.w * k)}
      </span>
    </span>
  )
}
