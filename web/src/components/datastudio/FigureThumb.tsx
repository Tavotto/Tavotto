import { useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * 候选的缩略图：一张真图放在纸上（`bg-paper`，两套主题同值、图不反相）；还没到（后端懒渲染，`<img>` 一取就当场画）/ 没地址时是同尺寸的静态骨架（`bg-surface-active`），
 * 取不到时是 `.hatch-box` 占位。盒子尺寸固定（宽高比由调用方给，默认 4:3），占位与成图同一个盒子，换上去时不位移。
 */
export function FigureThumb({ src, alt, onSettled, className }: { src: string | null; alt: string; /** 这张图取到了（true）/ 取不到（false，后端画不出来是 422） */ onSettled?: (ok: boolean) => void; className?: string }) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const broken = failed
  const waiting = !broken && src == null
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
          onLoad={() => {
            setLoaded(true)
            onSettled?.(true)
          }}
          onError={() => {
            setFailed(true)
            onSettled?.(false)
          }}
          className={cn('absolute inset-0 block size-full object-contain', loaded ? 'animate-fade-in' : 'opacity-0')}
        />
      )}
    </span>
  )
}
