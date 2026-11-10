import { useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { Paperclip, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import type { AttachmentView } from '@/store/aiStore'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/** 卡片的倾斜角（度）：按序号取、循环，**确定性**，不随机——同一排永远是同一个样子 */
export const ATTACH_TILTS = [-3, 2.5, -1.5, 3, -2, 1.5] as const
const tiltOf = (i: number) => ATTACH_TILTS[i % ATTACH_TILTS.length]

/**
 * 一张附件卡的脸：图片 = 缩略图；其它文件 = 白底卡（扩展名角标 + 截断的文件名）。
 * 倾斜、阴影、悬停由外层给（输入框预览行与对话里的附件气泡各自带自己的动势）。
 */
export function AttachFace({ view, className }: { view: AttachmentView; className?: string }) {
  if (view.kind === 'image' && view.thumb) {
    return <img src={view.thumb} alt="" draggable={false} className={cn('block size-12 rounded-md bg-surface object-cover inset-ring inset-ring-border', className)} />
  }
  return (
    <span
      className={cn('flex h-12 w-[4.5rem] flex-col justify-between rounded-md bg-surface px-1.5 py-1 text-left inset-ring inset-ring-border', className)}
    >
      <span className="w-fit rounded-xs bg-accent-subtle px-1 text-xs font-medium uppercase leading-4 text-accent">{view.ext || '·'}</span>
      <span className="block truncate text-xs leading-4 text-ink-2">{view.name}</span>
    </span>
  )
}

/** 一张可去掉的预览卡：静止时略微倾斜 + 轻影；指针到了 / 键盘聚焦时摆正、抬起并出现 ×。`prefers-reduced-motion` 下不做位移动画 */
export function AttachPreviewCard({
  view,
  index,
  uploading,
  onRemove,
}: {
  view: AttachmentView
  index: number
  uploading?: boolean
  onRemove: () => void
}) {
  useTranslation('ai')
  return (
    <span
      data-ai-attach-chip={uploading ? 'uploading' : 'ready'}
      title={view.name}
      style={{ '--tilt': `${tiltOf(index)}deg`, zIndex: index } as CSSProperties}
      className={cn(
        // 相邻卡片微微遮挡（后一张压住前一张约 20% 卡宽）；悬停 / 聚焦的那张提到最上层，× 才够得着
        'group/card relative block shrink-0 rotate-[var(--tilt)] rounded-md shadow-thumb hover:z-sticky focus-within:z-sticky',
        index > 0 && (view.kind === 'image' ? '-ml-2.5' : '-ml-3.5'),
        'transition-[transform,box-shadow] duration-base ease-out motion-reduce:transition-none',
        'hover:-translate-y-0.5 hover:rotate-0 hover:shadow-lift focus-within:-translate-y-0.5 focus-within:rotate-0 focus-within:shadow-lift',
        'motion-reduce:hover:translate-y-0 motion-reduce:focus-within:translate-y-0',
        uploading && 'opacity-60',
      )}
    >
      <AttachFace view={view} />
      <button
        type="button"
        data-ai-attach-remove
        aria-label={ai('panel.attachRemove')}
        title={ai('panel.attachRemove')}
        onClick={onRemove}
        className={cn(
          'absolute -right-1.5 -top-1.5 grid size-4 place-items-center rounded-full bg-ink text-surface opacity-0 outline-none',
          'transition-opacity duration-fast group-hover/card:opacity-100 focus-visible:opacity-100 focus-visible:focus-ring',
        )}
      >
        <X size={ICON_SIZE.xs} aria-hidden />
      </button>
    </span>
  )
}

/**
 * 已发送消息里的附件气泡（第二个气泡）：本页发起的显示成一叠卡牌，点一下展开成平铺、再点收回；
 * 刷新 / 历史里没有明细，只剩「附了 N 个文件」。
 */
export function AttachmentBubble({ views, count }: { views?: AttachmentView[]; count?: number }) {
  useTranslation('ai')
  const [open, setOpen] = useState(false)
  if (views?.length) {
    return (
      <button
        type="button"
        data-ai-user-attachments="stack"
        aria-expanded={open}
        aria-label={ai('message.attachmentCount', { count: views.length })}
        onClick={() => setOpen((v) => !v)}
        className="flex max-w-[85%] flex-wrap items-end gap-y-2 rounded-lg bg-surface-hover px-3 py-2.5 outline-none focus-visible:focus-ring"
      >
        {views.map((v, i) => (
          <span
            key={i}
            style={
              {
                '--tilt': `${open ? 0 : tiltOf(i)}deg`,
                marginLeft: i === 0 ? 0 : open ? 8 : -26,
                zIndex: i,
              } as CSSProperties
            }
            className={cn(
              'relative block rotate-[var(--tilt)] rounded-md shadow-thumb transition-[transform,margin] duration-base ease-out motion-reduce:transition-none',
              !open && 'hover:-translate-y-0.5 motion-reduce:hover:translate-y-0',
            )}
          >
            <AttachFace view={v} />
          </span>
        ))}
      </button>
    )
  }
  return (
    <span data-ai-user-attachments="count" className="type-meta inline-flex max-w-[85%] items-center gap-1.5 rounded-lg bg-surface-hover px-3 py-2">
      <Paperclip size={ICON_SIZE.sm} aria-hidden />
      {ai('message.attachmentCount', { count: count ?? 0 })}
    </span>
  )
}
