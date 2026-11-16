import { useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * 备选的缩略图：一张真图放在纸上（`bg-paper`，两套主题同值、图不反相）；加载中是同尺寸的静态骨架（`bg-surface-active`），取不到时是 `.hatch-box` 占位。
 * 图幅固定 4:3，占位与成图同一个盒子，换上去时不位移。地址由调用方给（已带项目标识）。
 */
export function FigureThumb({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  return (
    <span
      data-figure-thumb={failed ? 'failed' : 'ready'}
      className={cn('relative block aspect-4/3 w-full overflow-hidden rounded-md', failed ? 'hatch-box' : loaded ? 'bg-paper' : 'bg-surface-active', className)}
    >
      {!failed && (
        <img
          src={src}
          alt={alt}
          draggable={false}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className={cn('absolute inset-0 block size-full object-contain', loaded ? 'animate-fade-in' : 'opacity-0')}
        />
      )}
    </span>
  )
}
