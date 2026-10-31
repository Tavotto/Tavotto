import { cn } from '@/lib/utils'

import { contentBox, type ThumbBox, type ThumbPageSize } from '@/lib/versionThumbBox'
import { versionThumbUrl, type LayoutVersionMeta } from '@/lib/api'

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
  page: ThumbPageSize
  box: ThumbBox | null
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

/**
 * 显示一个节点的存图时对准哪一块：新图拍的就是内容区域（`thumbBox`，拍图与这里同一个
 * `contentBox` 判据）；旧节点的图是整页，按草图现算。null = 整页。
 */
export function thumbDisplayBox(meta: LayoutVersionMeta, page: ThumbPageSize): ThumbBox | null {
  if (meta.thumbBox) return meta.thumbBox
  return meta.sketch ? contentBox(page, meta.sketch.objects) : null
}

/**
 * 存好的那张缩略图，摆在**页面坐标**里——放进 `CropFrame` 的内层（页面大小）。
 * 只拍了内容区域的图（`thumbBox`）摆在那一块上；整页的图（旧节点）铺满。
 */
export function VersionThumbImage({
  docId,
  meta,
  page,
  ...rest
}: {
  docId: string
  meta: LayoutVersionMeta
  page: ThumbPageSize
} & Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt' | 'style' | 'className'>) {
  const b = meta.thumbBox
  if (!b) return <img {...rest} src={versionThumbUrl(docId, meta)} alt="" className="block size-full max-w-none" />
  return (
    <img
      {...rest}
      src={versionThumbUrl(docId, meta)}
      alt=""
      className="absolute block max-w-none"
      style={{
        left: `${(b.x / page.w) * 100}%`,
        top: `${(b.y / page.h) * 100}%`,
        width: `${(b.w / page.w) * 100}%`,
        height: `${(b.h / page.h) * 100}%`,
      }}
    />
  )
}
