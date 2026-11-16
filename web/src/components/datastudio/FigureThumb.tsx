import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { ThumbState } from '@/lib/dataFigureAdapter'

/**
 * 候选的缩略图：一张真图放在纸上（`bg-paper`，两套主题同值、图不反相）；还没到 / 没地址时是同尺寸的静态骨架（`bg-surface-active`），
 * 取不到时是 `.hatch-box` 占位。盒子尺寸固定（宽高比由调用方给，默认 4:3），占位与成图同一个盒子，换上去时不位移。
 * `state` 是适配层给的这一张的就绪状态（`pending` 骨架 / `ready` 取图 / `failed` 占位）；图本身加载失败也落到占位。
 */
export function FigureThumb({ src, alt, state = 'ready', className }: { src: string | null; alt: string; state?: ThumbState; className?: string }) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const broken = failed || state === 'failed'
  const waiting = !broken && (src == null || state === 'pending')
  return (
    <span
      data-figure-thumb={broken ? 'failed' : loaded ? 'ready' : 'pending'}
      className={cn('relative block aspect-4/3 w-full overflow-hidden rounded-md', broken ? 'hatch-box' : loaded ? 'bg-paper' : 'bg-surface-active', className)}
    >
      {!broken && !waiting && src && (
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
