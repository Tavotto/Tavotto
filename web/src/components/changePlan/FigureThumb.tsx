import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * 图的缩略图（助手里的两张卡共用）：托底画框——卡面里再托一层表面色（浅色里是白、深色里是比卡面更沉的一阶，像内衬）、
 * 1px 内描边、8px 圆角、6px 衬边；里面是纸（两套主题同值，图不反相）。各状态画框一致，不跳变。
 * 叠在上面的东西（编号小圆、「修改前」胶囊、磨砂层）是纸的**兄弟**而不是子孙：纸上只许画纸墨，界面的墨坐在纸外面。
 */
export function FigureThumb({
  src,
  alt,
  aspect = 744 / 392,
  dim = false,
  cover = false,
  className,
  children,
}: {
  /** 图的地址；还没有（拿不到）就只留纸底 */
  src: string | null | undefined
  alt: string
  /** 宽 / 高；缺省是演示图的比例，真接入时由引擎给的图尺寸 */
  aspect?: number
  /** 方案已不适用 / 已放弃：整张淡一档，而不是另画一层说明 */
  dim?: boolean
  /** 收矮的预览区（已放弃）：按比例裁，不拉伸 */
  cover?: boolean
  className?: string
  children?: ReactNode
}) {
  return (
    <span data-change-plan-frame-thumb className={cn('block w-full rounded-lg bg-surface p-1.5 inset-ring inset-ring-border', className)}>
      <span className="relative block w-full" style={{ aspectRatio: aspect }}>
        <span className="absolute inset-0 rounded-md bg-paper" />
        {src && (
        <img
          src={src}
          alt={alt}
          draggable={false}
          className={cn(
            'absolute inset-0 size-full rounded-md transition-opacity duration-base',
            cover ? 'object-cover object-top' : 'object-contain',
            dim && 'opacity-50',
          )}
        />
        )}
        {children}
      </span>
    </span>
  )
}
